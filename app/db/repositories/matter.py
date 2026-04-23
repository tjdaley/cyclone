"""
app/db/repositories/matter.py - Repository for Matter, MatterStaff, BillingSplit, MatterRateOverride,
and OpposingParty models.
"""
from typing import Optional

from db.models.matter import (
    BillingSplitInDB,
    MatterInDB,
    MatterRateOverrideInDB,
    MatterStaffInDB,
    MatterStatus,
    OpposingPartyInDB,
)
from db_handler import BaseRepository, DatabaseManager

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class MatterRepository(BaseRepository[MatterInDB]):
    """CRUD repository for the ``matters`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matters", MatterInDB)

    def get_by_client(self, client_id: int) -> list[MatterInDB]:
        """
        Return all matters for a given client, ordered by creation date descending.

        :param client_id: Primary key of the client record.
        :type client_id: int
        :return: List of matter records; empty list if none found.
        :rtype: list[MatterInDB]
        """
        LOGGER.debug("MatterRepository.get_by_client: client_id=%s", client_id)
        return self.select_many(
            condition={"client_id": client_id},
            sort_by="created_at",
            sort_direction="desc",
        )[0]

    def get_by_status(self, status: MatterStatus) -> list[MatterInDB]:
        """
        Return all matters with the given lifecycle status.

        :param status: MatterStatus value to filter by.
        :type status: MatterStatus
        :return: List of matter records; empty list if none found.
        :rtype: list[MatterInDB]
        """
        LOGGER.debug("MatterRepository.get_by_status: status=%s", status.value)
        return self.select_many(condition={"status": status.value}, sort_by="matter_name")[0]

    def get_active_for_client(self, client_id: int) -> list[MatterInDB]:
        """
        Return active matters for a client (client portal view).

        :param client_id: Primary key of the client record.
        :type client_id: int
        :return: List of active matter records.
        :rtype: list[MatterInDB]
        """
        LOGGER.debug("MatterRepository.get_active_for_client: client_id=%s", client_id)
        return self.select_many(
            condition={"client_id": client_id, "status": MatterStatus.active.value},
        )[0]


class MatterStaffRepository(BaseRepository[MatterStaffInDB]):
    """CRUD repository for the ``matter_staff`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_staff", MatterStaffInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterStaffInDB]:
        """
        Return all staff assignments for a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of matter-staff records.
        :rtype: list[MatterStaffInDB]
        """
        LOGGER.debug("MatterStaffRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(condition={"matter_id": matter_id})[0]

    def get_by_staff(self, staff_id: int) -> list[MatterStaffInDB]:
        """
        Return all matter assignments for a staff member.

        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: List of matter-staff records.
        :rtype: list[MatterStaffInDB]
        """
        LOGGER.debug("MatterStaffRepository.get_by_staff: staff_id=%s", staff_id)
        return self.select_many(condition={"staff_id": staff_id})[0]

    def get_billing_reviewer(self, matter_id: int) -> Optional[MatterStaffInDB]:
        """
        Return the billing reviewer assignment for a matter, or ``None``.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: Billing reviewer record, or ``None``.
        :rtype: Optional[MatterStaffInDB]
        """
        LOGGER.debug("MatterStaffRepository.get_billing_reviewer: matter_id=%s", matter_id)
        return self.select_one(condition={"matter_id": matter_id, "role": "billing_reviewer"})


class BillingSplitRepository(BaseRepository[BillingSplitInDB]):
    """CRUD repository for the ``billing_splits`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "billing_splits", BillingSplitInDB)

    def get_by_matter(self, matter_id: int) -> list[BillingSplitInDB]:
        """
        Return all billing splits for a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of split records; empty for single-client matters.
        :rtype: list[BillingSplitInDB]
        """
        LOGGER.debug("BillingSplitRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(condition={"matter_id": matter_id})[0]


class MatterRateOverrideRepository(BaseRepository[MatterRateOverrideInDB]):
    """CRUD repository for the ``matter_rate_overrides`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_rate_overrides", MatterRateOverrideInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterRateOverrideInDB]:
        """
        Return all rate overrides for a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of rate override records.
        :rtype: list[MatterRateOverrideInDB]
        """
        LOGGER.debug("MatterRateOverrideRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(condition={"matter_id": matter_id})[0]

    def get_for_staff(self, matter_id: int, staff_id: int) -> Optional[MatterRateOverrideInDB]:
        """
        Return the rate override for a specific staff member on a matter, or ``None``.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: Rate override record, or ``None`` if no override exists.
        :rtype: Optional[MatterRateOverrideInDB]
        """
        LOGGER.debug(
            "MatterRateOverrideRepository.get_for_staff: matter_id=%s staff_id=%s",
            matter_id,
            staff_id,
        )
        return self.select_one(condition={"matter_id": matter_id, "staff_id": staff_id})


class OpposingPartyRepository(BaseRepository[OpposingPartyInDB]):
    """CRUD repository for the ``opposing_parties`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "opposing_parties", OpposingPartyInDB)

    def get_by_matter(self, matter_id: int) -> list[OpposingPartyInDB]:
        """
        Return all opposing parties on a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of opposing party records.
        :rtype: list[OpposingPartyInDB]
        """
        LOGGER.debug("OpposingPartyRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(condition={"matter_id": matter_id})[0]
