"""
app/db/repositories/lead_action.py - Repository for the lead_actions table.

Append-only — the DB enforces no updates and no deletes via trigger.
"""
from uuid import UUID

from db_handler import BaseRepository, DatabaseManager

from db.models.lead_action import LeadActionInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class LeadActionRepository(BaseRepository[LeadActionInDB]):
    """CRUD repository for the ``lead_actions`` table. Append-only at the DB layer."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "lead_actions", LeadActionInDB)

    def get_for_lead(self, session_uuid: UUID, limit: int = 200) -> list[LeadActionInDB]:
        """Return actions for a lead, newest first."""
        records, _ = self.select_many(
            condition={"foreign_session_uuid": str(session_uuid)},
            sort_by="created_at",
            sort_direction="desc",
            start=0,
            end=max(0, limit - 1),
        )
        return records
