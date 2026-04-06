"""
app/db/repositories/fee_agreement.py - Repository for the FeeAgreement model.
"""
from typing import Optional

from db.models.fee_agreement import FeeAgreementInDB, FeeAgreementStatus
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class FeeAgreementRepository(BaseRepository[FeeAgreementInDB]):
    """CRUD repository for the ``fee_agreements`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "fee_agreements", FeeAgreementInDB)

    def get_by_matter(self, matter_id: int) -> list[FeeAgreementInDB]:
        """
        Return all fee agreements for a matter, most recently created first.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of fee agreement records.
        :rtype: list[FeeAgreementInDB]
        """
        LOGGER.debug("FeeAgreementRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="created_at",
            sort_direction="desc",
        )[0]

    def get_executed(self, matter_id: int) -> Optional[FeeAgreementInDB]:
        """
        Return the executed fee agreement for a matter, or ``None`` if none has been signed.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: Executed fee agreement, or ``None``.
        :rtype: Optional[FeeAgreementInDB]
        """
        LOGGER.debug("FeeAgreementRepository.get_executed: matter_id=%s", matter_id)
        return self.select_one(
            condition={"matter_id": matter_id, "status": FeeAgreementStatus.executed.value},
        )

    def get_pending_signature(self, matter_id: int) -> Optional[FeeAgreementInDB]:
        """
        Return the fee agreement currently awaiting client signature, or ``None``.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: Pending fee agreement, or ``None``.
        :rtype: Optional[FeeAgreementInDB]
        """
        LOGGER.debug("FeeAgreementRepository.get_pending_signature: matter_id=%s", matter_id)
        return self.select_one(
            condition={"matter_id": matter_id, "status": FeeAgreementStatus.sent_to_client.value},
        )
