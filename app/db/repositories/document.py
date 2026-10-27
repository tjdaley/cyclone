"""
app/db/repositories/document.py - The as-produced record, working documents, and their extents.
"""
from typing import Optional

from db.models.document import (
    DocumentInDB,
    DocumentSourcePageInDB,
    SourceDocumentInDB,
)
from db_handler import BaseRepository, DatabaseManager

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class SourceDocumentRepository(BaseRepository[SourceDocumentInDB]):
    """CRUD for the ``source_documents`` table — the as-produced record."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "source_documents", SourceDocumentInDB)

    def get_by_matter(self, matter_id: int) -> list[SourceDocumentInDB]:
        return self.select_many(condition={"matter_id": matter_id}, sort_by="received_on")[0]

    def find_by_hash(self, matter_id: int, content_sha256: str) -> Optional[SourceDocumentInDB]:
        """
        The dedup lookup, keyed on the bytes rather than the filename.

        Filenames from a production are unreliable to the point of uselessness,
        and the same file arrives twice — re-served, or handed over again by a
        client who is not sure it went. One matter cannot hold the same bytes
        twice; across matters it can, because the same bank statement produced
        in two cases is two records.
        """
        return self.select_one(
            condition={"matter_id": matter_id, "content_sha256": content_sha256})


class DocumentRepository(BaseRepository[DocumentInDB]):
    """CRUD for the ``documents`` table — the working copies."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "documents", DocumentInDB)

    def get_by_source(self, source_document_id: int) -> list[DocumentInDB]:
        """Every document split out of one received file, in creation order."""
        return self.select_many(
            condition={"source_document_id": source_document_id}, sort_by="id")[0]

    def get_by_matter(self, matter_id: int) -> list[DocumentInDB]:
        return self.select_many(condition={"matter_id": matter_id}, sort_by="id")[0]


class DocumentSourcePageRepository(BaseRepository[DocumentSourcePageInDB]):
    """CRUD for ``document_source_pages`` — which pages of the source a document covers."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "document_source_pages", DocumentSourcePageInDB)

    def get_by_document(self, document_id: int) -> list[DocumentSourcePageInDB]:
        """
        A document's ranges in reading order.

        Sorted by ``seq``, never by page number: the sequence is the order the
        operator selected on the bench, which is an assertion about how the
        document reads. Sorting by page would quietly overrule it.
        """
        return self.select_many(condition={"document_id": document_id}, sort_by="seq")[0]

    def get_by_documents(self, document_ids: list[int]) -> list[DocumentSourcePageInDB]:
        """
        Every range belonging to any of these documents, in one query.

        The bench grid needs which document claims each page, for every page at
        once. One query per document would be a round trip per document on a
        pile that may hold 169 of them.
        """
        if not document_ids:
            return []
        return self.select_many(condition={"document_id": document_ids}, sort_by="seq")[0]
