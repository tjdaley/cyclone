"""
app/db/models/document.py - The as-produced record, the working documents, and their extents.

Three tables from migration 047, and the distinction between the first two is
the point of the whole design: `source_documents` is the file as it was served
on us, never OCR'd, never split, never altered; `documents` are the working
copies the pipeline reads and the exhibits cite.

`document_source_pages` is a document's extent — a LIST of page ranges, not a
first and a last, because a scanned pile contains documents interrupted mid-way
by unrelated pages and resumed later. Where a person groups those pages on the
splitting bench, the assertion that they are one document has been made and
recorded; where only the automatic splitter has looked, the fragments stay
separate and are related by ``continues_document_id``.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class BatesSource(str, Enum):
    """
    Where a document's Bates numbers came from. Values match the
    ``documents.bates_source`` CHECK constraint (047).

    An exhibit has to be able to say which: a detected stamp was read off the
    page by pattern, a manual one is somebody's reading of it, and an applied
    one is a series Cyclone printed.
    """
    detected = "detected"
    manual = "manual"
    applied = "applied"


class TextLayer(str, Enum):
    """
    How the text being classified was obtained. Matches the
    ``documents.text_layer`` CHECK constraint (047).

    ``unreadable`` is a TERMINAL verdict and it has to exist. A 396-page pile
    measured at 0% usable text contains scans that will never yield one, and
    without a terminal state such a page is indistinguishable from one not yet
    processed: it would be re-OCR'd on every run, re-reported as a gap, and
    re-queued forever. A page recorded unreadable is a page somebody reads by
    eye, which is the correct outcome and not a failure.
    """
    native = "native"
    ocr = "ocr"
    mixed = "mixed"
    none = "none"
    unreadable = "unreadable"


class PhiBasis(str, Enum):
    """
    Why a document is marked as containing protected health information.
    Matches the ``documents.phi_basis`` CHECK constraint (047).

    The two are recorded separately because they fail differently: a label can
    be wrong about a whole document, while an extracted value makes a document
    PHI whatever it was labelled — a diagnosis code quoted in an email.
    """
    label = "label"
    extraction = "extraction"
    manual = "manual"


# ── The as-produced record ────────────────────────────────────────────────────

class SourceDocument(BaseModel):
    """A file exactly as it was served on us, received from a client, or
    returned by a subpoena. Append-only: never OCR'd, split, or re-saved."""

    matter_id: int = Field(..., description="FK to matters — the case this was produced in")
    production_id: Optional[int] = Field(
        default=None, description="FK to document_productions, when it arrived as part of one")
    storage_path: str = Field(..., description="Supabase Storage path of the untouched original")
    original_filename: str = Field(
        ..., description="The name as served, however mangled; never shown in an exhibit")
    media_type: Optional[str] = Field(default=None, description="MIME type as received")
    content_sha256: str = Field(
        ..., description="Hex digest of the bytes; the dedup key, since filenames are unreliable")
    byte_count: int = Field(
        ..., description="Size in bytes, recorded here rather than derived from storage later")
    page_count: Optional[int] = Field(
        default=None, description="Pages, or null for a non-paginated original")
    page_features: Optional[list] = Field(
        default=None,
        description="Per-page facts the splitting bench shows as badges, computed once when "
                    "a session opens. Derived and cached here because the alternative is "
                    "re-downloading the original on every grid request")
    received_on: date = Field(..., description="Date the file reached the firm")
    received_by_staff_id: Optional[int] = Field(
        default=None, description="FK to staff — who received it")


class SourceDocumentInDB(SourceDocument):
    """Database model. No ``updated_at``: the as-produced record is immutable."""

    id: int = Field(..., description="Primary key, set by database")
    created_at: datetime = Field(..., description="Set by database")
    model_config = ConfigDict(from_attributes=True)


# ── The working copy ──────────────────────────────────────────────────────────

class Document(BaseModel):
    """One document split out of a source file: what the pipeline classifies and
    what an exhibit cites."""

    matter_id: int = Field(..., description="FK to matters")
    source_document_id: int = Field(
        ..., description="FK to source_documents — the as-produced file this came from")
    storage_path: Optional[str] = Field(
        default=None, description="Working PDF in Storage; null while it is being built")

    doc_type: Optional[str] = Field(
        default=None, description="Taxonomy label, and the routing key to a processor")
    doc_type_confidence: Optional[float] = Field(
        default=None, description="Classifier probability for doc_type")
    classified_at: Optional[datetime] = Field(
        default=None, description="When the label was assigned")
    needs_review: bool = Field(
        default=True, description="Awaiting a person; true until somebody has looked")

    description: Optional[str] = Field(
        default=None, description="Exhibit-facing description, never the filename")
    doc_date: Optional[date] = Field(default=None, description="The document's own date, if known")
    period_begin: Optional[date] = Field(
        default=None, description="Statements: period start, drives coverage analysis")
    period_end: Optional[date] = Field(default=None, description="Statements: period end")

    bates_first: Optional[str] = Field(
        default=None, description="First Bates stamp; text, so leading zeros survive")
    bates_last: Optional[str] = Field(default=None, description="Last Bates stamp")
    bates_source: Optional[BatesSource] = Field(
        default=None, description="Detected by pattern, entered by hand, or applied by us")

    statement_id: Optional[int] = Field(
        default=None,
        description="FK to financial_account_statements where this document IS one, so the "
                    "index reads periods and Bates from one record instead of a second copy")
    continues_document_id: Optional[int] = Field(
        default=None,
        description="FK to documents — the fragment this one continues, where an interrupted "
                    "document was split automatically and nobody asserted they are one")

    text_layer: Optional[TextLayer] = Field(
        default=None, description="How the classifiable text was obtained")
    text_quality: Optional[int] = Field(
        default=None, description="0-100 quality score for that text")

    contains_phi: bool = Field(
        default=False, description="Holds protected health information; gates export and the portal")
    phi_basis: Optional[PhiBasis] = Field(
        default=None, description="Why it is marked PHI: the label, an extracted value, or a person")
    phi_subject_client: bool = Field(
        default=False, description="The PHI is our own client's, so it may go to them")
    phi_subject_minor_child: bool = Field(
        default=False,
        description="The PHI is a minor child's; releasable to the client only where "
                    "matters.may_receive_childrens_phi is true")

    flags: list = Field(
        default_factory=list, description="Provenance and exception flags, as for a statement")
    withheld: bool = Field(
        default=False, description="Withheld under a privilege claim; never reaches the index")
    byte_count: Optional[int] = Field(default=None, description="Size of the working PDF")


class DocumentInDB(Document):
    """Database model."""

    id: int = Field(..., description="Primary key, set by database")
    created_at: datetime = Field(..., description="Set by database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


# ── A document's extent ───────────────────────────────────────────────────────

class DocumentSourcePage(BaseModel):
    """One contiguous page range of the source file that a document covers.

    1-based and inclusive, matching ``physical_page_number`` and every other
    page number in this codebase.
    """

    document_id: int = Field(..., description="FK to documents")
    seq: int = Field(
        ...,
        description="Reading order of this range within the document; on the splitting bench "
                    "it is the order the operator selected, so nothing sorts it")
    page_first: int = Field(..., description="First page of the range in the source, 1-based")
    page_last: int = Field(..., description="Last page of the range in the source, inclusive")


class DocumentSourcePageInDB(DocumentSourcePage):
    """Database model. No ``updated_at``: a range is replaced, never edited."""

    id: int = Field(..., description="Primary key, set by database")
    created_at: datetime = Field(..., description="Set by database")
    model_config = ConfigDict(from_attributes=True)
