"""
app/db/repositories/trust_ledger.py - Repository for the TrustLedgerEntry model.
"""
from db.models.trust_ledger import TrustLedgerEntryInDB, TrustTransactionType
from db_handler import BaseRepository, DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class TrustLedgerRepository(BaseRepository[TrustLedgerEntryInDB]):
    """
    Read/insert repository for the ``trust_ledger`` table.

    Trust ledger entries are immutable — never update or delete.
    Use ``insert()`` to post a transaction and the running balance is
    derived by summing all entries for a matter at query time.
    """

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "trust_ledger", TrustLedgerEntryInDB)

    def get_by_matter(self, matter_id: int) -> list[TrustLedgerEntryInDB]:
        """
        Return all trust ledger entries for a matter, oldest first.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of trust ledger entry records.
        :rtype: list[TrustLedgerEntryInDB]
        """
        LOGGER.debug("TrustLedgerRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="transaction_date",
            sort_direction="asc",
        )[0]

    def get_deposits(self, matter_id: int) -> list[TrustLedgerEntryInDB]:
        """
        Return deposit entries for a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of deposit records.
        :rtype: list[TrustLedgerEntryInDB]
        """
        LOGGER.debug("TrustLedgerRepository.get_deposits: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id, "transaction_type": TrustTransactionType.deposit.value},
            sort_by="transaction_date",
        )[0]
