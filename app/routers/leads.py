"""
app/routers/leads.py - CRM endpoints for leads from the landing-pages DB.

All routes are thin: validate input, call lead_service, return a response.
Access control is two-layered:
  1. require_role(...) blocks non-staff at the route level.
  2. lead_service.assert_can_access_slug(...) blocks staff who don't have
     slug access to a particular lead (admin bypasses).
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, get_landing_pages_db, require_role
from schemas.lead import (
    AddActionRequest,
    AddNoteRequest,
    AgentToggleRequest,
    AssignRequest,
    FollowUpRequest,
    LeadActionResponse,
    LeadDetail,
    LeadListItem,
    PriorityUpdateRequest,
    StatusUpdateRequest,
)
from services.lead_service import (
    LeadAccessDeniedError,
    LeadNotFoundError,
    lead_service,
)
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

_STAFF_ROLES = ["attorney", "paralegal", "admin"]


def _resolve_staff_id(request: Request, cyclone_db) -> int:
    """
    Resolve the authenticated user's cyclone staff.id from their supabase_uid.

    Required because the lead service is keyed by staff_id, but the JWT
    only carries the auth UID. Raises 401 if no matching staff row exists.
    """
    uid = getattr(request.state, "supabase_uid", None)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    staff = StaffRepository(cyclone_db).select_one(condition={"supabase_uid": uid})
    if staff is None:
        raise HTTPException(status_code=403, detail="No staff record correlated to this account")
    return staff.id


def _role(request: Request) -> str:
    role = getattr(request.state, "role", None)
    if role is None:
        raise HTTPException(status_code=403, detail="Role not resolved")
    return role


# ── List ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[LeadListItem])
def list_leads(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[LeadListItem]:
    """List leads accessible to the caller, newest first."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return lead_service.list_leads(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        limit=min(limit, 500),
        offset=max(offset, 0),
    )


# ── Detail ────────────────────────────────────────────────────────────────

@router.get("/{session_uuid}", response_model=LeadDetail)
def get_lead(
    session_uuid: UUID,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadDetail:
    """Return the full detail for one lead, after access check."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    try:
        return lead_service.get_detail(
            cyclone_db=cyclone_db,
            foreign_db=foreign_db,
            staff_id=staff_id,
            role=_role(request),
            session_uuid=session_uuid,
        )
    except LeadNotFoundError:
        raise HTTPException(status_code=404, detail="Lead not found")
    except LeadAccessDeniedError:
        raise HTTPException(status_code=403, detail="No access to this lead")


# ── Activity log ──────────────────────────────────────────────────────────

@router.get("/{session_uuid}/actions", response_model=list[LeadActionResponse])
def list_lead_actions(
    session_uuid: UUID,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[LeadActionResponse]:
    """Return the activity timeline for a lead."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    try:
        return lead_service.list_actions(
            cyclone_db=cyclone_db,
            foreign_db=foreign_db,
            staff_id=staff_id,
            role=_role(request),
            session_uuid=session_uuid,
        )
    except LeadNotFoundError:
        raise HTTPException(status_code=404, detail="Lead not found")
    except LeadAccessDeniedError:
        raise HTTPException(status_code=403, detail="No access to this lead")


# ── Mutations ─────────────────────────────────────────────────────────────

def _mutation_wrapper(handler):
    """Translate service exceptions to HTTP errors. Used inline below."""

    def wrapped(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except LeadNotFoundError:
            raise HTTPException(status_code=404, detail="Lead not found")
        except LeadAccessDeniedError:
            raise HTTPException(status_code=403, detail="No access to this lead")

    return wrapped


@router.patch("/{session_uuid}/status", response_model=LeadDetail)
def update_status(
    session_uuid: UUID,
    body: StatusUpdateRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadDetail:
    """Update the pipeline status of a lead."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.update_status)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        new_status=body.status,
        dismissal_reason=body.dismissal_reason,
        dismissal_note=body.dismissal_note,
    )


@router.patch("/{session_uuid}/assign", response_model=LeadDetail)
def assign_lead(
    session_uuid: UUID,
    body: AssignRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadDetail:
    """Assign or unassign the lead to a staff member."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.assign)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        assignee_staff_id=body.staff_id,
    )


@router.patch("/{session_uuid}/priority", response_model=LeadDetail)
def update_priority(
    session_uuid: UUID,
    body: PriorityUpdateRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadDetail:
    """Update the priority of a lead."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.update_priority)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        priority=body.priority,
    )


@router.patch("/{session_uuid}/follow-up", response_model=LeadDetail)
def set_follow_up(
    session_uuid: UUID,
    body: FollowUpRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadDetail:
    """Set or clear the next-action reminder."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.set_follow_up)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        next_action_at=body.next_action_at,
        next_action_note=body.next_action_note,
    )


@router.patch("/{session_uuid}/agent", response_model=LeadDetail)
def toggle_agent(
    session_uuid: UUID,
    body: AgentToggleRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadDetail:
    """Enable or disable the AI agent for this lead."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.toggle_agent)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        enabled=body.agent_enabled,
    )


@router.post("/{session_uuid}/notes", response_model=LeadActionResponse, status_code=201)
def add_note(
    session_uuid: UUID,
    body: AddNoteRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadActionResponse:
    """Add a free-form note to the lead's activity log."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.add_note)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        body=body.body,
    )


@router.post("/{session_uuid}/actions", response_model=LeadActionResponse, status_code=201)
def add_action(
    session_uuid: UUID,
    body: AddActionRequest,
    request: Request,
    cyclone_db=Depends(get_db_manager),
    foreign_db=Depends(get_landing_pages_db),
    _=Depends(require_role(_STAFF_ROLES)),
) -> LeadActionResponse:
    """Log a manual action like 'call_attempted' or 'voicemail_left'."""
    staff_id = _resolve_staff_id(request, cyclone_db)
    return _mutation_wrapper(lead_service.log_manual_action)(
        cyclone_db=cyclone_db,
        foreign_db=foreign_db,
        staff_id=staff_id,
        role=_role(request),
        session_uuid=session_uuid,
        action_type=body.action_type,
        direction=body.direction,
        body=body.body,
        notes=body.notes,
        metadata=body.metadata,
    )
