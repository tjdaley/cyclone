"""
app/db/repositories/billing_cycle.py - Repository for the BillingCycle model.
"""
from typing import Optional

from db.models.billing_cycle import BillingCycleInDB, BillingCycleStatus
from db_handler import BaseRepository, DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class BillingCycleRepository(BaseRepository[BillingCycleInDB]):
    """CRUD repository for the ``billing_cycles`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "billing_cycles", BillingCycleInDB)

    def get_by_matter(self, matter_id: int) -> list[BillingCycleInDB]:
        """
        Return all billing cycles for a matter, most recent first.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of billing cycle records.
        :rtype: list[BillingCycleInDB]
        """
        LOGGER.debug("BillingCycleRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="period_start",
            sort_direction="desc",
        )[0]

    def get_open_cycle(self, matter_id: int) -> Optional[BillingCycleInDB]:
        """
        Return the open billing cycle for a matter, or ``None`` if there is none.

        At most one cycle should be open per matter at any time.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: Open billing cycle, or ``None``.
        :rtype: Optional[BillingCycleInDB]
        """
        LOGGER.debug("BillingCycleRepository.get_open_cycle: matter_id=%s", matter_id)
        return self.select_one(
            condition={"matter_id": matter_id, "status": BillingCycleStatus.open.value},
        )

    def get_closed_cycles(self, matter_id: int) -> list[BillingCycleInDB]:
        """
        Return all closed billing cycles for a matter (for client portal history).

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of closed billing cycle records, most recent first.
        :rtype: list[BillingCycleInDB]
        """
        LOGGER.debug("BillingCycleRepository.get_closed_cycles: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id, "status": BillingCycleStatus.closed.value},
            sort_by="period_start",
            sort_direction="desc",
        )[0]
