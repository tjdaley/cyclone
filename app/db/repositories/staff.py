"""
app/db/repositories/staff.py - Repository for StaffMember model.

Replaces the illustrative attorney.py repository starter.
"""
from typing import Optional

from db.models.staff import StaffMemberInDB
from db_handler import BaseRepository
from db_handler import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class StaffRepository(BaseRepository[StaffMemberInDB]):
    """CRUD repository for the ``staff`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "staff", StaffMemberInDB)

    def get_by_supabase_uid(self, supabase_uid: str) -> Optional[StaffMemberInDB]:
        """
        Return the staff member linked to a Supabase Auth UID, or ``None`` if not found.

        :param supabase_uid: Supabase Auth UID from auth.users.
        :type supabase_uid: str
        :return: Matching staff record, or ``None``.
        :rtype: Optional[StaffMemberInDB]
        """
        LOGGER.debug("StaffRepository.get_by_supabase_uid: uid=%s", supabase_uid)
        return self.select_one(condition={"supabase_uid": supabase_uid})

    def get_by_slug(self, slug: str) -> Optional[StaffMemberInDB]:
        """
        Return the staff member with the given URL slug, or ``None`` if not found.

        :param slug: URL-safe unique identifier.
        :type slug: str
        :return: Matching staff record, or ``None``.
        :rtype: Optional[StaffMemberInDB]
        """
        LOGGER.debug("StaffRepository.get_by_slug: slug=%s", slug)
        return self.select_one(condition={"slug": slug})

    def get_by_office(self, office_id: int) -> list[StaffMemberInDB]:
        """
        Return all staff members belonging to the specified office.

        :param office_id: Primary key of the office record.
        :type office_id: int
        :return: List of staff records; empty list if none found.
        :rtype: list[StaffMemberInDB]
        """
        LOGGER.debug("StaffRepository.get_by_office: office_id=%s", office_id)
        return self.select_many(condition={"office_id": office_id})[0]

    def slug_exists(self, slug: str) -> bool:
        """
        Check whether a slug is already in use (for duplicate-guard on create/update).

        :param slug: URL-safe unique identifier to check.
        :type slug: str
        :return: ``True`` if the slug is taken, ``False`` otherwise.
        :rtype: bool
        """
        return self.exists(field="slug", value=slug)
