"""
app/db/repositories/lead_agent_run.py - Repository for the lead_agent_runs table.
"""
from uuid import UUID

from db_handler import BaseRepository, DatabaseManager

from db.models.lead_agent_run import LeadAgentRunInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class LeadAgentRunRepository(BaseRepository[LeadAgentRunInDB]):
    """CRUD repository for the ``lead_agent_runs`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "lead_agent_runs", LeadAgentRunInDB)

    def list_pending_explanations(self, limit: int = 5) -> list[LeadAgentRunInDB]:
        """Return runs where the staff edited the draft but no explanation has
        been generated yet. Oldest-first so the backlog drains in order.
        """
        records, _ = self.select_many(
            condition={
                "human_edited": True,
                "edit_explanation": None,
                "final_action": "sent",
            },
            sort_by="updated_at",
            sort_direction="asc",
            start=0,
            end=max(0, limit - 1),
        )
        return records

    def list_recent_edited(self, limit: int = 20) -> list[LeadAgentRunInDB]:
        """Return recently-edited runs (regardless of whether explanation is filled)
        so the admin UI can review tuning signals."""
        records, _ = self.select_many(
            condition={"human_edited": True, "final_action": "sent"},
            sort_by="updated_at",
            sort_direction="desc",
            start=0,
            end=max(0, min(limit, 100) - 1),
        )
        return records

    def welcome_exists(self, session_uuid: UUID) -> bool:
        """Return True if a welcome run has already been recorded for this lead.

        This is the welcome dedup — keyed on the run ledger, not on workflow-row
        existence, so a lead a human opened before the poller ran still gets its
        welcome exactly once.
        """
        return self.select_one(
            condition={"foreign_session_uuid": str(session_uuid), "trigger": "welcome"},
        ) is not None
