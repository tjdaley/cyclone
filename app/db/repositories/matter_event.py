"""
app/db/repositories/matter_event.py - Repository for the MatterEvent model.
"""
from db.models.matter_event import MatterEventInDB
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class MatterEventRepository(BaseRepository[MatterEventInDB]):
    """CRUD repository for the ``matter_events`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_events", MatterEventInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterEventInDB]:
        """
        Return all events for a matter, ordered by date ascending (soonest first).

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of matter event records.
        :rtype: list[MatterEventInDB]
        """
        LOGGER.debug("MatterEventRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="event_date",
            sort_direction="asc",
        )[0]

    def get_by_staff(self, staff_id: int) -> list[MatterEventInDB]:
        """
        Return all events created by a staff member.

        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: List of matter event records.
        :rtype: list[MatterEventInDB]
        """
        LOGGER.debug("MatterEventRepository.get_by_staff: staff_id=%s", staff_id)
        return self.select_many(
            condition={"created_by_staff_id": staff_id},
            sort_by="event_date",
        )[0]
