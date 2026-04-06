"""
app/db/repositories/billing_entry.py - Repository for the BillingEntry model.
"""
from db.models.billing_entry import BillingEntryInDB
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class BillingEntryRepository(BaseRepository[BillingEntryInDB]):
    """CRUD repository for the ``billing_entries`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "billing_entries", BillingEntryInDB)

    def get_by_matter(self, matter_id: int) -> list[BillingEntryInDB]:
        """
        Return all billing entries for a matter, ordered by entry date descending.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of billing entry records.
        :rtype: list[BillingEntryInDB]
        """
        LOGGER.debug("BillingEntryRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="entry_date",
            sort_direction="desc",
        )[0]

    def get_unbilled_for_matter(self, matter_id: int) -> list[BillingEntryInDB]:
        """
        Return all unbilled (not yet closed into a billing cycle) entries for a matter.

        Used by the ClientBalanceWidget to compute the outstanding unbilled total.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of unbilled billing entry records.
        :rtype: list[BillingEntryInDB]
        """
        LOGGER.debug("BillingEntryRepository.get_unbilled_for_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id, "billed": False},
            sort_by="entry_date",
        )[0]

    def get_by_cycle(self, billing_cycle_id: int) -> list[BillingEntryInDB]:
        """
        Return all entries associated with a billing cycle (for bill generation).

        :param billing_cycle_id: Primary key of the billing cycle record.
        :type billing_cycle_id: int
        :return: List of billing entry records in this cycle.
        :rtype: list[BillingEntryInDB]
        """
        LOGGER.debug("BillingEntryRepository.get_by_cycle: billing_cycle_id=%s", billing_cycle_id)
        return self.select_many(
            condition={"billing_cycle_id": billing_cycle_id},
            sort_by="entry_date",
        )[0]

    def get_by_staff(self, staff_id: int) -> list[BillingEntryInDB]:
        """
        Return all billing entries for a given timekeeper (staff member).

        :param staff_id: Primary key of the staff record.
        :type staff_id: int
        :return: List of billing entry records.
        :rtype: list[BillingEntryInDB]
        """
        LOGGER.debug("BillingEntryRepository.get_by_staff: staff_id=%s", staff_id)
        return self.select_many(
            condition={"staff_id": staff_id},
            sort_by="entry_date",
            sort_direction="desc",
        )[0]
