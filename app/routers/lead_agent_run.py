"""
app/routers/lead_agent_run.py - HITL approval endpoints for AI-composed drafts.

POST /{run_id}/send    Approve + send a draft to the PNC. Threads on the
                       parent message (welcome or prior inbound) so the PNC's
                       mail client sees the conversation flow correctly.
                       Sets sent_body, human_edited, status='done', final_action='sent'.

POST /{run_id}/reject  Reject a draft without sending. Logs an internal note
                       with the reason. Sets status='done', final_action='rejected'.

Access: caller must be admin OR have slug access for the lead's attorney_slug
OR be a responder for the lead's assigned attorney.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.foreign_lead import ForeignLead
from db.models.lead_action import LeadActionDirection, LeadActionType, LeadActorType
from db.repositories.attorney_lead_responder import AttorneyLeadResponderRepository
from db.repositories.foreign_lead import ForeignLeadRepository
from db.repositories.lead_action import LeadActionRepository
from db.repositories.lead_agent_run import LeadAgentRunRepository
from db.repositories.lead_workflow import LeadWorkflowRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, get_landing_pages_db, require_role
from schemas.lead_agent_run import LeadAgentRunResponse, RejectDraftRequest, SendDraftRequest
from services.email_service import email_service
from services.lead_service import LeadAccessDeniedError, lead_service
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/lead-agent-runs", tags=["agent-runs"])


def _resolve_caller(request: Request, cyclone_db) -> tuple[Optional[int], str]:
    """Resolve the calling staff member's id + role from request.state."""
    uid = getattr(request.state, "supabase_uid", None)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    staff = StaffRepository(cyclone_db).select_one(condition={"supabase_uid": uid})
    role = getattr(request.state, "role", None) or ""
    return (staff.id if staff else None, role)


def _check_lead_access(
    cyclone_db,
    foreign_db,
    foreign_session_uuid,
    caller_staff_id: Optional[int],
    caller_role: str,
) -> ForeignLead:
    """
    Two-axis access check matching the leads visibility model:
      1. Admin role bypasses everything.
      2. Slug access (staff_slug_access) for the lead's attorney_slug.
      3. Responder mapping — caller is a responder for the lead's assigned attorney.
    """
    foreign_lead = ForeignLeadRepository(foreign_db).get_by_session_uuid(foreign_session_uuid)
    if foreign_lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    if caller_role == "admin":
        return foreign_lead
    if caller_staff_id is None:
        raise HTTPException(status_code=403, detail="No staff record correlated to this account")

    # Slug-axis
    try:
        lead_service.assert_can_access_slug(
            cyclone_db, caller_staff_id, caller_role, foreign_lead.attorney_slug,
        )
        return foreign_lead
    except LeadAccessDeniedError:
        pass

    # Responder-axis: is the caller a responder for the lead's assigned attorney?
    wf = LeadWorkflowRepository(cyclone_db).get_by_session_uuid(foreign_session_uuid)
    if wf and wf.assigned_staff_id:
        responder_rows = AttorneyLeadResponderRepository(cyclone_db).get_by_responder(caller_staff_id)
        if any(r.attorney_staff_id == wf.assigned_staff_id for r in responder_rows):
            return foreign_lead

    raise HTTPException(status_code=403, detail="No access to this lead")


def _find_parent_message_id(cyclone_db, foreign_session_uuid, run_id: int) -> Optional[str]:
    """Look up the draft_pending action for this run and read parent_message_id from its metadata.
    Used for In-Reply-To threading on the outbound send."""
    actions = LeadActionRepository(cyclone_db).get_for_lead(foreign_session_uuid)
    for a in actions:
        if (a.action_type == LeadActionType.draft_pending
                and isinstance(a.metadata, dict)
                and a.metadata.get("run_id") == run_id):
            parent = a.metadata.get("parent_message_id")
            return parent if isinstance(parent, str) else None
    return None


# ── Send ──────────────────────────────────────────────────────────────────

@router.post("/{run_id}/send", response_model=LeadAgentRunResponse)
def send_draft(
    run_id: int,
    body: SendDraftRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(["attorney", "paralegal", "admin"])),
) -> LeadAgentRunResponse:
    """Approve a draft and send it to the PNC. Threads via the parent message."""
    runs_repo = LeadAgentRunRepository(cyclone_db)
    run = runs_repo.select_one(condition={"id": run_id})
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail="Run is not awaiting approval (status=%s)" % run.status,
        )
    if not run.draft_body:
        raise HTTPException(status_code=409, detail="Run has no draft to send")

    caller_staff_id, caller_role = _resolve_caller(request, cyclone_db)
    foreign_lead = _check_lead_access(
        cyclone_db, foreign_db, run.foreign_session_uuid, caller_staff_id, caller_role,
    )
    if not foreign_lead.email:
        raise HTTPException(status_code=422, detail="Lead has no email address")

    parent_message_id = _find_parent_message_id(cyclone_db, run.foreign_session_uuid, run_id)

    body_text = body.body
    was_edited = body_text.strip() != (run.draft_body or "").strip()

    subject = "Re: Thank you for contacting %s" % settings.firm_name

    try:
        message_id = email_service.send(
            to_address=foreign_lead.email,
            subject=subject,
            body_text=body_text,
            in_reply_to=parent_message_id,
        )
    except Exception as e:  # noqa: BLE001
        LOGGER.error("send_draft: SMTP failed run_id=%s err=%s", run_id, str(e))
        # Log a permanent failure note on the lead's timeline so retries have history.
        # Run row stays in 'awaiting_approval' — the user can edit and try Send again.
        try:
            lead_service.record_action(  # type: ignore[call-arg]
                cyclone_db,
                session_uuid=run.foreign_session_uuid,
                action_type=LeadActionType.note,
                actor_type=LeadActorType.staff,
                direction=LeadActionDirection.internal,
                staff_id=caller_staff_id,
                notes="Send failed for AI draft: %s" % str(e),
                metadata={
                    "kind": "email_send_failed",
                    "stage": "draft_send",
                    "run_id": run_id,
                    "error": str(e),
                },
            )
        except Exception as log_err:  # noqa: BLE001 — failure logging must never crash the response
            LOGGER.error("send_draft: could not log failure note err=%s", str(log_err))
        raise HTTPException(status_code=502, detail="Failed to send email: %s" % str(e))

    lead_service.record_action(  # type: ignore[call-arg]
        cyclone_db,
        session_uuid=run.foreign_session_uuid,
        action_type=LeadActionType.email_sent,
        actor_type=LeadActorType.staff,
        direction=LeadActionDirection.outbound,
        staff_id=caller_staff_id,
        body=body_text,
        notes="Approved AI draft" + (" (edited)" if was_edited else ""),
        metadata={
            "kind": "approved_draft",
            "message_id": message_id,
            "parent_message_id": parent_message_id,
            "run_id": run_id,
            "human_edited": was_edited,
        },
    )

    updated = runs_repo.update(run.id, {
        "sent_body": body_text,
        "human_edited": was_edited,
        "status": "done",
        "final_action": "sent",
    })

    LOGGER.info(
        "send_draft: sent run_id=%s by_staff=%s human_edited=%s",
        run_id, caller_staff_id, was_edited,
    )
    return LeadAgentRunResponse(**updated.model_dump())


# ── Reject ────────────────────────────────────────────────────────────────

@router.post("/{run_id}/reject", response_model=LeadAgentRunResponse)
def reject_draft(
    run_id: int,
    body: RejectDraftRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(["attorney", "paralegal", "admin"])),
) -> LeadAgentRunResponse:
    """Reject an AI draft, marking the run done. The PNC is NOT contacted automatically —
    a human handles follow-up off-band."""
    runs_repo = LeadAgentRunRepository(cyclone_db)
    run = runs_repo.select_one(condition={"id": run_id})
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail="Run is not awaiting approval (status=%s)" % run.status,
        )

    caller_staff_id, caller_role = _resolve_caller(request, cyclone_db)
    _check_lead_access(
        cyclone_db, foreign_db, run.foreign_session_uuid, caller_staff_id, caller_role,
    )

    reason = (body.reason or "").strip() or "(no reason given)"

    lead_service.record_action(  # type: ignore[call-arg]
        cyclone_db,
        session_uuid=run.foreign_session_uuid,
        action_type=LeadActionType.note,
        actor_type=LeadActorType.staff,
        direction=LeadActionDirection.internal,
        staff_id=caller_staff_id,
        notes="Rejected AI draft. Reason: %s" % reason,
        metadata={
            "kind": "draft_rejected",
            "run_id": run_id,
            "reason": reason,
        },
    )

    updated = runs_repo.update(run.id, {
        "status": "done",
        "final_action": "rejected",
    })

    LOGGER.info("reject_draft: run_id=%s by_staff=%s reason=%s", run_id, caller_staff_id, reason)
    return LeadAgentRunResponse(**updated.model_dump())
