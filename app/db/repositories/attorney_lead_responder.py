"""
app/db/repositories/attorney_lead_responder.py - Repository for attorney_lead_responders.
"""
from db_handler import BaseRepository, DatabaseManager

from db.models.attorney_lead_responder import AttorneyLeadResponderInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class AttorneyLeadResponderRepository(BaseRepository[AttorneyLeadResponderInDB]):
    """CRUD repository for the ``attorney_lead_responders`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "attorney_lead_responders", AttorneyLeadResponderInDB)

    def get_by_attorney(self, attorney_staff_id: int) -> list[AttorneyLeadResponderInDB]:
        """Return all responder mappings for one attorney."""
        records, _ = self.select_many(condition={"attorney_staff_id": attorney_staff_id})
        return records

    def get_by_responder(self, responder_staff_id: int) -> list[AttorneyLeadResponderInDB]:
        """Return all attorneys this responder covers (used by the leads visibility filter)."""
        records, _ = self.select_many(condition={"responder_staff_id": responder_staff_id})
        return records
