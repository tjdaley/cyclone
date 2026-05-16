"""
app/db/repositories/staff_slug_access.py - Repository for the staff_slug_access table.
"""
from db_handler import BaseRepository, DatabaseManager

from db.models.staff_slug_access import StaffSlugAccessInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

WILDCARD_SLUG = "*"


class StaffSlugAccessRepository(BaseRepository[StaffSlugAccessInDB]):
    """CRUD repository for the ``staff_slug_access`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "staff_slug_access", StaffSlugAccessInDB)

    def slugs_for_staff(self, staff_id: int) -> list[str]:
        """Return the list of slugs this staff member can see leads for."""
        records, _ = self.select_many(condition={"staff_id": staff_id})
        return [r.slug for r in records]

    def has_wildcard(self, staff_id: int) -> bool:
        """Return True if this staff member has a '*' grant."""
        return self.exists(condition={"staff_id": staff_id, "slug": WILDCARD_SLUG})
