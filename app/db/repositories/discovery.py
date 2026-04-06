"""
app/db/repositories/discovery.py - Repositories for DiscoveryRequest and DiscoveryResponse models.
"""
from typing import Optional

from db.models.discovery import (
    DiscoveryRequestInDB,
    DiscoveryRequestStatus,
    DiscoveryRequestType,
    DiscoveryResponseInDB,
)
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class DiscoveryRequestRepository(BaseRepository[DiscoveryRequestInDB]):
    """CRUD repository for the ``discovery_requests`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "discovery_requests", DiscoveryRequestInDB)

    def get_by_matter(self, matter_id: int) -> list[DiscoveryRequestInDB]:
        """
        Return all discovery requests for a matter, ordered by type then request number.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of discovery request records.
        :rtype: list[DiscoveryRequestInDB]
        """
        LOGGER.debug("DiscoveryRequestRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="request_number",
        )[0]

    def get_by_type(self, matter_id: int, request_type: DiscoveryRequestType) -> list[DiscoveryRequestInDB]:
        """
        Return discovery requests of a specific type for a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :param request_type: Type of discovery request to filter by.
        :type request_type: DiscoveryRequestType
        :return: List of matching discovery request records.
        :rtype: list[DiscoveryRequestInDB]
        """
        LOGGER.debug(
            "DiscoveryRequestRepository.get_by_type: matter_id=%s type=%s",
            matter_id,
            request_type.value,
        )
        return self.select_many(
            condition={"matter_id": matter_id, "request_type": request_type.value},
            sort_by="request_number",
        )[0]

    def get_pending_client(self, matter_id: int) -> list[DiscoveryRequestInDB]:
        """
        Return discovery requests awaiting client input for a matter.

        :param matter_id: Primary key of the matter record.
        :type matter_id: int
        :return: List of pending discovery request records.
        :rtype: list[DiscoveryRequestInDB]
        """
        LOGGER.debug("DiscoveryRequestRepository.get_pending_client: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id, "status": DiscoveryRequestStatus.pending_client.value},
        )[0]


class DiscoveryResponseRepository(BaseRepository[DiscoveryResponseInDB]):
    """CRUD repository for the ``discovery_responses`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "discovery_responses", DiscoveryResponseInDB)

    def get_by_request(self, discovery_request_id: int) -> Optional[DiscoveryResponseInDB]:
        """
        Return the response for a discovery request, or ``None`` if not yet submitted.

        :param discovery_request_id: Primary key of the discovery request record.
        :type discovery_request_id: int
        :return: Discovery response, or ``None``.
        :rtype: Optional[DiscoveryResponseInDB]
        """
        LOGGER.debug(
            "DiscoveryResponseRepository.get_by_request: request_id=%s", discovery_request_id,
        )
        return self.select_one(condition={"discovery_request_id": discovery_request_id})

    def get_by_matter(self, matter_id: int) -> list[DiscoveryResponseInDB]:
        """
        Return all responses for a matter by joining through discovery_requests.

        Note: This performs a filtered select on ``discovery_request_id`` values
        and requires the caller to first fetch request IDs via DiscoveryRequestRepository.
        For a direct join, extend this method with a custom select_string.

        :param matter_id: Primary key of the matter record (unused in base query).
        :type matter_id: int
        :return: Empty list — callers should query by individual request ID.
        :rtype: list[DiscoveryResponseInDB]
        """
        LOGGER.warning(
            "DiscoveryResponseRepository.get_by_matter called — "
            "use get_by_request per item instead: matter_id=%s",
            matter_id,
        )
        return []
