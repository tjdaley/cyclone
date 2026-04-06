"""
app/db/repositories/user_role.py - Repository for the UserRole model.
"""
from typing import Optional

from db.models.user_role import UserRoleInDB, UserRoleType
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class UserRoleRepository(BaseRepository[UserRoleInDB]):
    """CRUD repository for the ``user_roles`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "user_roles", UserRoleInDB)

    def get_by_uid(self, supabase_uid: str) -> Optional[UserRoleInDB]:
        """
        Return the role assignment for a Supabase Auth UID, or ``None`` if not found.

        This is called by AuthMiddleware on every authenticated request to
        resolve the user's application role.

        :param supabase_uid: Supabase Auth UID from the JWT sub claim.
        :type supabase_uid: str
        :return: User role record, or ``None`` if the user has no role assigned.
        :rtype: Optional[UserRoleInDB]
        """
        LOGGER.debug("UserRoleRepository.get_by_uid")
        return self.select_one(condition={"supabase_uid": supabase_uid})

    def get_by_staff(self, staff_id: int) -> Optional[UserRoleInDB]:
        """
        Return the role assignment for a staff record, or ``None``.

        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: User role record, or ``None``.
        :rtype: Optional[UserRoleInDB]
        """
        LOGGER.debug("UserRoleRepository.get_by_staff: staff_id=%s", staff_id)
        return self.select_one(condition={"staff_id": staff_id})

    def get_by_client(self, client_id: int) -> Optional[UserRoleInDB]:
        """
        Return the role assignment for a client record, or ``None``.

        :param client_id: Primary key of the client record.
        :type client_id: int
        :return: User role record, or ``None``.
        :rtype: Optional[UserRoleInDB]
        """
        LOGGER.debug("UserRoleRepository.get_by_client: client_id=%s", client_id)
        return self.select_one(condition={"client_id": client_id})

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

    def uid_has_role(self, supabase_uid: str) -> bool:
        """
        Check whether a Supabase Auth UID has any role assigned.

        :param supabase_uid: Supabase Auth UID to check.
        :type supabase_uid: str
        :return: ``True`` if the user has a role assignment.
        :rtype: bool
        """
        return self.exists(field="supabase_uid", value=supabase_uid)
