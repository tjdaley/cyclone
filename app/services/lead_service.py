"""
app/services/lead_service.py - CRM orchestration: foreign leads + cyclone workflow.

Lead rows live in the landing-pages Supabase project (read-only from
cyclone's perspective). This service joins them with cyclone-side
workflow state and exposes a clean API to the leads router.

Architectural notes:
- Foreign DB access goes through a separate SupabaseManager instance
  (see dependencies.get_landing_pages_db). Cross-DB joins happen here in
  Python — never in SQL.
- ``leads_workflow`` rows are created lazily on first cyclone interaction.
  Listing endpoints return a synthesized default for leads with no row yet.
- Every state mutation writes a ``lead_actions`` row so the activity log
  reflects the full history.
"""
from typing import Optional
from uuid import UUID

from db_handler import DatabaseManager

from db.models.foreign_lead import ForeignLead
from db.models.lead_action import (
    LeadAction,
    LeadActionDirection,
    LeadActionInDB,
    LeadActionType,
    LeadActorType,
)
from db.models.lead_workflow import (
    DismissalReason,
    LeadPriority,
    LeadStatus,
    LeadWorkflow,
    LeadWorkflowInDB,
)
from db.models.user_role import UserRoleType
from db.repositories.foreign_lead import ForeignLeadRepository
from db.repositories.lead_action import LeadActionRepository
from db.repositories.lead_workflow import LeadWorkflowRepository
from db.repositories.staff import StaffRepository
from db.repositories.staff_slug_access import (
    WILDCARD_SLUG,
    StaffSlugAccessRepository,
)
from schemas.lead import LeadActionResponse, LeadDetail, LeadListItem
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class LeadAccessDeniedError(Exception):
    """Raised when a staff member tries to touch a lead outside their slug access."""


class LeadNotFoundError(Exception):
    """Raised when a session_uuid does not exist in the foreign leads table."""


# Slugs that represent the firm's general inbox rather than a specific
# attorney's referral. Leads carrying these slugs are "up for grabs" and
# never auto-assigned, even if a staff row happens to share the slug.
UNATTRIBUTED_SLUGS: frozenset[str] = frozenset({"www", "home", ""})


class LeadService:

    # ── Access resolution ─────────────────────────────────────────────────

    def resolve_accessible_slugs(
        self,
        cyclone_db: DatabaseManager,
        staff_id: int,
        role: str,
    ) -> Optional[list[str]]:
        """
        Return the list of attorney slugs this staff member may see leads for.

        Returns ``None`` when the caller has unrestricted access (admin role
        or an explicit '*' grant). Callers should treat ``None`` as "no slug
        filter — return everything".

        :param cyclone_db: Manager bound to the cyclone DB.
        :param staff_id: Primary key of the staff member.
        :param role: Role string from the JWT (validated by ``require_role``).
        :return: List of slugs, or ``None`` for unrestricted.
        """
        if role == UserRoleType.admin.value:
            return None
        access_repo = StaffSlugAccessRepository(cyclone_db)
        slugs = access_repo.slugs_for_staff(staff_id)
        if WILDCARD_SLUG in slugs:
            return None
        return slugs

    def assert_can_access_slug(
        self,
        cyclone_db: DatabaseManager,
        staff_id: int,
        role: str,
        slug: Optional[str],
    ) -> None:
        """Raise LeadAccessDeniedError if the staff member can't see leads for ``slug``."""
        accessible = self.resolve_accessible_slugs(cyclone_db, staff_id, role)
        if accessible is None:
            return  # unrestricted
        if slug is None or slug not in accessible:
            raise LeadAccessDeniedError("No access to slug=%s" % slug)

    # ── List ──────────────────────────────────────────────────────────────

    def list_leads(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LeadListItem]:
        """
        Return the accessible-leads list for the staff member, enriched with
        workflow state. Newest leads first.
        """
        accessible = self.resolve_accessible_slugs(cyclone_db, staff_id, role)
        foreign_repo = ForeignLeadRepository(foreign_db)
        if accessible is None:
            foreign_leads = foreign_repo.list_all(limit=limit, offset=offset)
        elif not accessible:
            return []  # No access granted to any slug
        else:
            foreign_leads = foreign_repo.list_by_slugs(accessible, limit=limit, offset=offset)

        # Batch-fetch matching workflow rows
        wf_repo = LeadWorkflowRepository(cyclone_db)
        session_uuids = [fl.session_uuid for fl in foreign_leads]
        wf_rows = wf_repo.get_by_session_uuids(session_uuids)
        wf_by_uuid: dict[UUID, LeadWorkflowInDB] = {w.foreign_session_uuid: w for w in wf_rows}

        return [self._build_list_item(fl, wf_by_uuid.get(fl.session_uuid)) for fl in foreign_leads]

    @staticmethod
    def _build_list_item(
        foreign: ForeignLead,
        wf: Optional[LeadWorkflowInDB],
    ) -> LeadListItem:
        return LeadListItem(
            session_uuid=foreign.session_uuid,
            foreign_lead_id=foreign.id,
            attorney_slug=foreign.attorney_slug,
            full_name=foreign.full_name,
            email=foreign.email,
            telephone=foreign.telephone,
            audit_score=foreign.audit_score,
            lead_source=foreign.lead_source,
            state=foreign.state,
            city=foreign.city,
            captured_at=foreign.created_at,
            status=wf.status if wf else LeadStatus.new,
            assigned_staff_id=wf.assigned_staff_id if wf else None,
            priority=wf.priority if wf else LeadPriority.normal,
            next_action_at=wf.next_action_at if wf else None,
            has_workflow_row=wf is not None,
        )

    # ── Detail ────────────────────────────────────────────────────────────

    def get_detail(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
    ) -> LeadDetail:
        """Return enriched detail for one lead, after checking access."""
        foreign = ForeignLeadRepository(foreign_db).get_by_session_uuid(session_uuid)
        if foreign is None:
            raise LeadNotFoundError(str(session_uuid))

        self.assert_can_access_slug(cyclone_db, staff_id, role, foreign.attorney_slug)

        wf_repo = LeadWorkflowRepository(cyclone_db)
        wf = wf_repo.get_by_session_uuid(session_uuid)
        if wf is None:
            wf = self._create_workflow_row(cyclone_db, foreign)

        return LeadDetail(
            session_uuid=foreign.session_uuid,
            foreign_lead_id=foreign.id,
            attorney_slug=foreign.attorney_slug,
            captured_at=foreign.created_at,
            full_name=foreign.full_name,
            email=foreign.email,
            telephone=foreign.telephone,
            audit_score=foreign.audit_score,
            country=foreign.country,
            state=foreign.state,
            city=foreign.city,
            zip=foreign.zip,
            url_path=foreign.url_path,
            lead_source=foreign.lead_source,
            referrer=foreign.referrer,
            conflict_summary=foreign.conflict_summary,
            status=wf.status,
            assigned_staff_id=wf.assigned_staff_id,
            priority=wf.priority,
            next_action_at=wf.next_action_at,
            next_action_note=wf.next_action_note,
            dismissal_reason=wf.dismissal_reason,
            dismissal_note=wf.dismissal_note,
            converted_to_client_id=wf.converted_to_client_id,
            converted_to_matter_id=wf.converted_to_matter_id,
            agent_enabled=wf.agent_enabled,
            agent_summary=wf.agent_summary,
            agent_last_run_at=wf.agent_last_run_at,
            agent_handoff_reason=wf.agent_handoff_reason,
        )

    # ── Activity log ──────────────────────────────────────────────────────

    def list_actions(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
    ) -> list[LeadActionResponse]:
        """Return the activity timeline for a lead, newest first."""
        self._assert_access_for_session(cyclone_db, foreign_db, staff_id, role, session_uuid)
        actions = LeadActionRepository(cyclone_db).get_for_lead(session_uuid)
        return [self._action_to_response(a) for a in actions]

    @staticmethod
    def _action_to_response(a: LeadActionInDB) -> LeadActionResponse:
        return LeadActionResponse(
            id=a.id,
            session_uuid=a.foreign_session_uuid,
            actor_type=a.actor_type,
            staff_id=a.staff_id,
            action_type=a.action_type,
            direction=a.direction,
            body=a.body,
            notes=a.notes,
            metadata=a.metadata,
            created_at=a.created_at,
        )

    # ── Mutations (every one writes a lead_action) ────────────────────────

    def update_status(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        new_status: LeadStatus,
        dismissal_reason: Optional[DismissalReason],
        dismissal_note: Optional[str],
    ) -> LeadDetail:
        wf = self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        old_status = wf.status

        # Reason + note only apply to disqualification; clear them otherwise so
        # a lead moved out of 'disqualified' doesn't carry a stale reason.
        updates: dict[str, object] = {"status": new_status.value}
        if new_status == LeadStatus.disqualified:
            updates["dismissal_reason"] = dismissal_reason.value if dismissal_reason else None
            updates["dismissal_note"] = dismissal_note
        else:
            updates["dismissal_reason"] = None
            updates["dismissal_note"] = None

        LeadWorkflowRepository(cyclone_db).update(wf.id, updates)
        self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=LeadActionType.status_change,
            metadata={
                "from": old_status.value,
                "to": new_status.value,
                "reason": dismissal_reason.value if dismissal_reason else None,
                "note": dismissal_note,
            },
        )
        LOGGER.info("lead_service.update_status: session=%s %s -> %s", session_uuid, old_status.value, new_status.value)
        return self.get_detail(cyclone_db, foreign_db, staff_id, role, session_uuid)

    def assign(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        assignee_staff_id: Optional[int],
    ) -> LeadDetail:
        wf = self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        LeadWorkflowRepository(cyclone_db).update(wf.id, {"assigned_staff_id": assignee_staff_id})
        self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=LeadActionType.assigned,
            metadata={"from": wf.assigned_staff_id, "to": assignee_staff_id},
        )
        LOGGER.info("lead_service.assign: session=%s assignee=%s", session_uuid, assignee_staff_id)
        return self.get_detail(cyclone_db, foreign_db, staff_id, role, session_uuid)

    def update_priority(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        priority: LeadPriority,
    ) -> LeadDetail:
        wf = self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        LeadWorkflowRepository(cyclone_db).update(wf.id, {"priority": priority.value})
        self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=LeadActionType.priority_change,
            metadata={"from": wf.priority.value, "to": priority.value},
        )
        return self.get_detail(cyclone_db, foreign_db, staff_id, role, session_uuid)

    def set_follow_up(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        next_action_at,
        next_action_note: Optional[str],
    ) -> LeadDetail:
        wf = self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        LeadWorkflowRepository(cyclone_db).update(
            wf.id,
            {"next_action_at": next_action_at, "next_action_note": next_action_note},
        )
        self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=LeadActionType.follow_up_set,
            notes=next_action_note,
            metadata={"next_action_at": next_action_at.isoformat() if next_action_at else None},
        )
        return self.get_detail(cyclone_db, foreign_db, staff_id, role, session_uuid)

    def toggle_agent(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        enabled: bool,
    ) -> LeadDetail:
        wf = self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        LeadWorkflowRepository(cyclone_db).update(wf.id, {"agent_enabled": enabled})
        self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=LeadActionType.note,
            notes="Agent enabled" if enabled else "Agent disabled",
            metadata={"agent_enabled": enabled},
        )
        return self.get_detail(cyclone_db, foreign_db, staff_id, role, session_uuid)

    def add_note(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        body: str,
    ) -> LeadActionResponse:
        self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        action = self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=LeadActionType.note,
            body=body,
        )
        return self._action_to_response(action)

    def log_manual_action(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
        action_type: LeadActionType,
        direction: LeadActionDirection,
        body: Optional[str],
        notes: Optional[str],
        metadata: dict,
    ) -> LeadActionResponse:
        self._get_or_create_for_mutation(cyclone_db, foreign_db, staff_id, role, session_uuid)
        action = self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=action_type,
            direction=direction,
            body=body,
            notes=notes,
            metadata=metadata,
        )
        return self._action_to_response(action)

    # ── System-actor helpers (used by the CRM agent / worker) ──────────────

    def ensure_workflow_row(
        self,
        cyclone_db: DatabaseManager,
        foreign: ForeignLead,
    ) -> LeadWorkflowInDB:
        """
        Return the workflow row for a lead, creating it (with slug auto-assign)
        if absent. For non-request contexts like the poller, where there is no
        staff_id/role to check — access control already happened upstream.
        """
        existing = LeadWorkflowRepository(cyclone_db).get_by_session_uuid(foreign.session_uuid)
        return existing if existing is not None else self._create_workflow_row(cyclone_db, foreign)

    def record_action(
        self,
        cyclone_db: DatabaseManager,
        session_uuid: UUID,
        action_type: LeadActionType,
        actor_type: LeadActorType = LeadActorType.system,
        direction: LeadActionDirection = LeadActionDirection.internal,
        staff_id: Optional[int] = None,
        body: Optional[str] = None,
        notes: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> LeadActionInDB:
        """Public wrapper around the internal action logger for the agent/worker."""
        return self._log_action(
            cyclone_db,
            session_uuid=session_uuid,
            staff_id=staff_id,
            action_type=action_type,
            actor_type=actor_type,
            direction=direction,
            body=body,
            notes=notes,
            metadata=metadata,
        )

    # ── Internals ─────────────────────────────────────────────────────────

    def _assert_access_for_session(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
    ) -> ForeignLead:
        foreign = ForeignLeadRepository(foreign_db).get_by_session_uuid(session_uuid)
        if foreign is None:
            raise LeadNotFoundError(str(session_uuid))
        self.assert_can_access_slug(cyclone_db, staff_id, role, foreign.attorney_slug)
        return foreign

    def _get_or_create_for_mutation(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        staff_id: int,
        role: str,
        session_uuid: UUID,
    ) -> LeadWorkflowInDB:
        foreign = self._assert_access_for_session(cyclone_db, foreign_db, staff_id, role, session_uuid)
        wf_repo = LeadWorkflowRepository(cyclone_db)
        existing = wf_repo.get_by_session_uuid(session_uuid)
        if existing is not None:
            return existing
        return self._create_workflow_row(cyclone_db, foreign)

    @staticmethod
    def _create_workflow_row(cyclone_db: DatabaseManager, foreign: ForeignLead) -> LeadWorkflowInDB:
        # Auto-assign: if a staff member's slug matches the lead's attorney_slug,
        # assign the lead to them at creation time. Captures the common case
        # where each landing-page slug maps to a single attorney via subdomain
        # (e.g. tjd.txfamlaw.com → slug='tjd' → Tom).
        # Slugs in UNATTRIBUTED_SLUGS represent the firm's general inbox and
        # are never auto-assigned regardless of staff config.
        auto_assignee_id: Optional[int] = None
        if foreign.attorney_slug and foreign.attorney_slug not in UNATTRIBUTED_SLUGS:
            staff = StaffRepository(cyclone_db).get_by_slug(foreign.attorney_slug)
            if staff is not None:
                auto_assignee_id = staff.id

        wf = LeadWorkflow(
            foreign_lead_id=foreign.id,
            foreign_session_uuid=foreign.session_uuid,
            attorney_slug=foreign.attorney_slug or "",
            assigned_staff_id=auto_assignee_id,
        )
        record = LeadWorkflowRepository(cyclone_db).insert(wf.model_dump(mode="json"))
        LOGGER.info(
            "lead_service: created workflow row session=%s slug=%s auto_assigned=%s",
            foreign.session_uuid, foreign.attorney_slug, auto_assignee_id,
        )

        if auto_assignee_id is not None:
            LeadService._log_action(
                cyclone_db,
                session_uuid=foreign.session_uuid,
                staff_id=None,
                actor_type=LeadActorType.system,
                action_type=LeadActionType.assigned,
                metadata={"from": None, "to": auto_assignee_id, "auto": True, "via": "slug"},
            )
        return record

    @staticmethod
    def _log_action(
        cyclone_db: DatabaseManager,
        session_uuid: UUID,
        staff_id: Optional[int],
        action_type: LeadActionType,
        actor_type: LeadActorType = LeadActorType.staff,
        direction: LeadActionDirection = LeadActionDirection.internal,
        body: Optional[str] = None,
        notes: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> LeadActionInDB:
        action = LeadAction(
            foreign_session_uuid=session_uuid,
            actor_type=actor_type,
            staff_id=staff_id if actor_type == LeadActorType.staff else None,
            action_type=action_type,
            direction=direction,
            body=body,
            notes=notes,
            metadata=metadata or {},
        )
        return LeadActionRepository(cyclone_db).insert(action.model_dump(mode="json"))


lead_service = LeadService()
