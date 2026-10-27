"""
app/services/document_processor_service.py - Sending a split document to the right processor.

Step 5 of the pipeline, in embryo: a document comes off the splitting bench and
goes to something that knows how to read that kind of document. Today there is
exactly one such processor — the statement ingest that already exists — and this
is the one route into it.

**It is deliberately small.** The registry the spec describes (a processor per
taxonomy label, selected by the classifier) needs the classifier, and the
classifier needs a taxonomy nobody has finalised. What is needed first is the
link itself: until a split document can reach the statement ingest, splitting a
statement pile strands the pieces, and that is what keeps the oversized-upload
refusal in place.

So this is the bridge, and the route is chosen by a person for now. When the
classifier arrives it chooses instead, and nothing else here changes.
"""
from typing import Optional

from db.models.document import DocumentInDB
from db.models.job import JobInDB
from db.repositories.document import (
    DocumentRepository,
    DocumentSourcePageRepository,
    SourceDocumentRepository,
)
from db_handler import DatabaseManager

from services.job_service import job_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class ProcessorError(Exception):
    """Something the caller can fix: a document with no file, or already read."""


class DocumentProcessorService:
    """Route a document to the processor for its kind."""

    def send_to_statement_ingest(
        self, manager: DatabaseManager, document_id: int, staff_id: int,
        bates_prefix: Optional[str] = None,
    ) -> JobInDB:
        """
        Read this document as a bank, card, brokerage, policy or annuity statement.

        **The job points at the document's existing object rather than a copy.**
        The statement's "show me the source" then opens the split document,
        which is the statement — not page 47 of a 396-page production, which is
        not an answer to the question. The as-produced original stays reachable
        through ``source_document_id``.

        :raises ProcessorError: If the document has no working PDF yet, or a
            statement has already been read out of it.
        """
        docs = DocumentRepository(manager)
        document = docs.select_one(condition={"id": document_id})
        if document is None:
            raise ProcessorError("That document is not on record.")
        if not document.storage_path:
            raise ProcessorError(
                "This document has no file yet, so there is nothing to read. That usually "
                "means the split did not finish — unbundle it and make it again.")
        if document.statement_id:
            raise ProcessorError(
                "A statement has already been read out of this document. Reading it again "
                "would create a duplicate; reject the existing statement first if it is "
                "wrong.")

        # A name worth seeing in the exceptions queue. Storage renames every
        # object, so without this a statement traces back to a uuid.
        source = SourceDocumentRepository(manager).select_one(
            condition={"id": document.source_document_id})
        label = self._label(manager, document, source.original_filename if source else None)

        job = job_service.enqueue_statement_ingest(
            manager, staff_id, document.matter_id,
            pdf_bytes=b"",                       # unused: the object is already stored
            bates_prefix=bates_prefix,
            source_filename=label,
            storage_path=document.storage_path,
            document_id=document.id,
        )
        LOGGER.info(
            "document_processor.send_to_statement_ingest: document=%s job=%s matter=%s",
            document.id, job.id, document.matter_id)
        return job

    @staticmethod
    def _label(manager: DatabaseManager, document: DocumentInDB,
               original_filename: Optional[str]) -> str:
        """
        A name that says where this came from, for the exceptions queue.

        The received file's own name plus the pages, because that pair is what
        somebody needs to find the document again: the filename alone is
        ambiguous once a pile has been split twenty ways, and the page range
        alone says nothing about which pile.
        """
        extents = DocumentSourcePageRepository(manager).get_by_document(document.id)
        ranges = ", ".join(
            "%d-%d" % (e.page_first, e.page_last) if e.page_last > e.page_first
            else str(e.page_first) for e in extents)
        stem = (original_filename or "received").rsplit(".", 1)[0][:80]
        return "%s pp. %s" % (stem, ranges) if ranges else stem


document_processor_service = DocumentProcessorService()
