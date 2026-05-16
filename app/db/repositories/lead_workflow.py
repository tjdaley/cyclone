"""
app/db/repositories/lead_workflow.py - Repository for the leads_workflow table.
"""
from typing import Optional
from uuid import UUID

from db_handler import BaseRepository, DatabaseManager

from db.models.lead_workflow import LeadWorkflowInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class LeadWorkflowRepository(BaseRepository[LeadWorkflowInDB]):
    """CRUD repository for the ``leads_workflow`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "leads_workflow", LeadWorkflowInDB)

    def get_by_session_uuid(self, session_uuid: UUID) -> Optional[LeadWorkflowInDB]:
        """Return the workflow row for a given foreign lead, or None if not yet created."""
        return self.select_one(condition={"foreign_session_uuid": str(session_uuid)})

    def get_by_session_uuids(self, session_uuids: list[UUID]) -> list[LeadWorkflowInDB]:
        """Return workflow rows for a batch of foreign leads."""
        if not session_uuids:
            return []
        records, _ = self.select_many(
            condition={"foreign_session_uuid": [str(u) for u in session_uuids]},
        )
        return records

    def get_assigned_to(self, staff_id: int) -> list[LeadWorkflowInDB]:
        """Return all workflow rows currently assigned to a staff member."""
        records, _ = self.select_many(
            condition={"assigned_staff_id": staff_id},
            sort_by="updated_at",
            sort_direction="desc",
        )
        return records
