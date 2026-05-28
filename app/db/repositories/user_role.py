"""
app/db/repositories/user_role.py - Repository for the UserRole model.

user_roles is the auth entry point. All authenticated lookups start here.
"""
from typing import Optional

from db.models.user_role import UserRoleInDB, UserRoleType
from db_handler import BaseRepository
from db_handler import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class UserRoleRepository(BaseRepository[UserRoleInDB]):
    """CRUD repository for the ``user_roles`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "user_roles", UserRoleInDB)

    # ── Auth lookups ──────────────────────────────────────────────────────

    def get_by_uid(self, supabase_uid: str) -> list[UserRoleInDB]:
        """
        Return ALL role assignments for a Supabase Auth UID.

        A user may hold multiple roles (e.g. attorney + admin); each is its own
        row here. Returns an empty list if the user has no roles assigned.

        :param supabase_uid: Supabase Auth UID from the JWT sub claim.
        :return: List of user role records (possibly empty, often size 1 or 2).
        :rtype: list[UserRoleInDB]
        """
        LOGGER.debug("UserRoleRepository.get_by_uid")
        records, _ = self.select_many(condition={"supabase_uid": supabase_uid})
        return records

    def get_unlinked_by_auth_email(self, auth_email: str) -> list[UserRoleInDB]:
        """
        Return ALL unlinked role rows (supabase_uid IS NULL) matching an auth email.

        Used by the first-login correlation flow. If an admin pre-created the
        user with multiple roles, all rows are linked together in one pass.

        :param auth_email: Email address to match against ``auth_email``.
        :return: List of unlinked rows (possibly empty).
        :rtype: list[UserRoleInDB]
        """
        LOGGER.debug("UserRoleRepository.get_unlinked_by_auth_email")
        records, _ = self.select_many(condition={"auth_email": auth_email, "supabase_uid": None})
        return records

    def uid_has_role(self, supabase_uid: str) -> bool:
        """
        Check whether a Supabase Auth UID has any role assigned.

        :param supabase_uid: Supabase Auth UID to check.
        :type supabase_uid: str
        :return: ``True`` if the user has a role assignment.
        :rtype: bool
        """
        return self.exists(field="supabase_uid", value=supabase_uid)

    # ── FK lookups ────────────────────────────────────────────────────────

    def get_by_staff(self, staff_id: int) -> list[UserRoleInDB]:
        """
        Return all role assignments for a staff record.

        A staff member may have multiple roles (attorney + admin, etc.), each
        as its own row. Returns an empty list if no rows match.
        """
        LOGGER.debug("UserRoleRepository.get_by_staff: staff_id=%s", staff_id)
        records, _ = self.select_many(condition={"staff_id": staff_id})
        return records

    def get_by_client(self, client_id: int) -> list[UserRoleInDB]:
        """Return all role assignments for a client record."""
        LOGGER.debug("UserRoleRepository.get_by_client: client_id=%s", client_id)
        records, _ = self.select_many(condition={"client_id": client_id})
        return records

    def get_by_role(self, role: UserRoleType) -> list[UserRoleInDB]:
        """
        Return all users with the specified role.

        :param role: UserRoleType to filter by.
        :type role: UserRoleType
        :return: List of user role records.
        :rtype: list[UserRoleInDB]
        """
        LOGGER.debug("UserRoleRepository.get_by_role: role=%s", role.value)
        return self.select_many(condition={"role": role.value})[0]

    def staff_has_role(self, staff_id: int) -> bool:
        """
        Check whether a staff member has any role assigned.

        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: ``True`` if the staff member has a role assignment.
        :rtype: bool
        """
        return self.exists(field="staff_id", value=staff_id)
