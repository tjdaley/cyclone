"""
app/db/repositories/discovery.py - Repositories for discovery documents, items, and responses.
"""
from typing import Optional

from db.models.discovery import (
    DiscoveryDocumentInDB,
    DiscoveryRequestItemInDB,
    DiscoveryRequestStatus,
    DiscoveryResponseInDB,
    StandardObjection,
    StandardPrivilege,
)
from db.repositories.base_repo import BaseRepository
from db.supabasemanager import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class DiscoveryDocumentRepository(BaseRepository[DiscoveryDocumentInDB]):
    """CRUD repository for the ``discovery_requests`` table (parent documents)."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "discovery_requests", DiscoveryDocumentInDB)

    def get_by_matter(self, matter_id: int) -> list[DiscoveryDocumentInDB]:
        """Return all discovery documents for a matter, newest first."""
        LOGGER.debug("DiscoveryDocumentRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="propounded_date",
        )[0]


class DiscoveryRequestItemRepository(BaseRepository[DiscoveryRequestItemInDB]):
    """CRUD repository for the ``discovery_request_items`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "discovery_request_items", DiscoveryRequestItemInDB)

    def get_by_document(self, discovery_request_id: int) -> list[DiscoveryRequestItemInDB]:
        """Return all items within a discovery document, ordered by request number."""
        LOGGER.debug("DiscoveryRequestItemRepository.get_by_document: doc_id=%s", discovery_request_id)
        return self.select_many(
            condition={"discovery_request_id": discovery_request_id},
            sort_by="request_number",
        )[0]

    def get_by_matter(self, matter_id: int) -> list[DiscoveryRequestItemInDB]:
        """Return all items for a matter across all documents."""
        LOGGER.debug("DiscoveryRequestItemRepository.get_by_matter: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id},
            sort_by="request_number",
        )[0]

    def get_pending_client(self, matter_id: int) -> list[DiscoveryRequestItemInDB]:
        """Return items awaiting client input for a matter."""
        LOGGER.debug("DiscoveryRequestItemRepository.get_pending_client: matter_id=%s", matter_id)
        return self.select_many(
            condition={"matter_id": matter_id, "status": DiscoveryRequestStatus.pending_client.value},
        )[0]


class DiscoveryResponseRepository(BaseRepository[DiscoveryResponseInDB]):
    """CRUD repository for the ``discovery_responses`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "discovery_responses", DiscoveryResponseInDB)

    def get_by_request(self, discovery_request_id: int) -> Optional[DiscoveryResponseInDB]:
        """Return the response for a discovery request item, or None."""
        LOGGER.debug("DiscoveryResponseRepository.get_by_request: request_id=%s", discovery_request_id)
        return self.select_one(condition={"discovery_request_id": discovery_request_id})


class StandardPrivilegeRepository(BaseRepository[StandardPrivilege]):
    """Read-only repository for the standard_privileges lookup table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "standard_privileges", StandardPrivilege)

    def get_all(self) -> list[StandardPrivilege]:
        """Return all standard privileges, sorted by slug."""
        return self.select_many(condition={}, sort_by="slug")[0]


class StandardObjectionRepository:
    """
    Repository for the standard_objections lookup table.

    Uses a direct Supabase query for the ``ov`` (array overlap) operator,
    which is not supported by BaseRepository.select_many.
    """

    def __init__(self, manager: DatabaseManager):
        self._manager = manager

    def get_by_request_type(self, request_type: str) -> list[StandardObjection]:
        """
        Return objections whose ``applies_to`` array contains the given
        request_type or the wildcard ``'*'``.
        """
        LOGGER.debug("StandardObjectionRepository.get_by_request_type: %s", request_type)
        result = (
            self._manager.client
            .table("standard_objections")
            .select("*")
            .ov("applies_to", [request_type, "*"])
            .order("slug")
            .execute()
        )
        return [StandardObjection(**row) for row in (result.data or [])]
