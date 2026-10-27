"""
app/schemas/received.py - Files as they arrived, and the routing verdict.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class UploadUrlRequest(BaseModel):
    """What the browser knows before it uploads anything."""

    content_sha256: str = Field(
        ..., min_length=64, max_length=64,
        description="Hex digest of the file, computed in the browser. It is the dedup key, "
                    "so a file this matter already holds costs no upload at all")
    filename: str = Field(..., description="The name as served, however mangled")
    media_type: Optional[str] = Field(default=None, description="MIME type")


class UploadUrlResponse(BaseModel):
    """Where to PUT the file — or what already holds it."""

    already_held: bool = Field(
        ...,
        description="This matter already holds these bytes, so nothing needs uploading. On "
                    "a large production that is the difference between instant and minutes, "
                    "and it is only possible because the key is the content, not the name")
    source_document_id: Optional[int] = Field(
        default=None, description="The existing record, when already held")
    path: str = Field(..., description="Where the file goes, or already is")
    signed_url: Optional[str] = Field(
        default=None,
        description="One-time URL to PUT the file to, DIRECTLY to storage. The bytes never "
                    "pass through this application, which is what keeps a large upload off "
                    "a request that would otherwise time out")
    token: Optional[str] = Field(default=None, description="Upload token for that URL")


class RegisterUploadRequest(BaseModel):
    """A file the browser has already put into storage."""

    path: str = Field(..., description="Where it was put — from the upload-url response")
    content_sha256: str = Field(..., min_length=64, max_length=64)
    filename: str = Field(..., description="The name as served")
    byte_count: int = Field(..., ge=0, description="Size in bytes")
    media_type: Optional[str] = Field(default=None)


class ReceivedUploadResponse(BaseModel):
    """What was recorded, and what should happen to it next.

    The numbers are returned alongside the verdict deliberately. A screen that
    says only "this goes to the bench" is an instruction; one that says 8% of
    pages carry their own pagination is a reason, and the person reading it can
    tell when the routing is wrong.
    """

    source_document_id: int = Field(..., description="The as-produced record")
    matter_id: int = Field(..., description="The matter it was produced in")
    original_filename: str = Field(..., description="The name as served")
    byte_count: int = Field(..., description="Size in bytes")
    already_held: bool = Field(
        ...,
        description="This matter already held these exact bytes, so nothing was stored "
                    "again. A supplement re-serves documents, and keying on the content "
                    "rather than the filename is what catches it")

    route: Optional[str] = Field(
        default=None,
        description="ocr_first: no usable text layer, nothing is decidable yet. "
                    "bench: a person should split it — too large, or it does not describe "
                    "its own structure. automatic: the split can be made and checked. "
                    "NULL while the survey that reads the file is still queued: deciding "
                    "means reading every page, which is a download this request must not "
                    "wait for")
    reason: str = Field(..., description="Why, in words somebody can disagree with")

    pages: int = Field(..., description="Pages in the file, once it has been read")
    text_share: float = Field(
        ..., description="Share of pages with a usable text layer, 0 to 1")
    pagination_coverage: float = Field(
        ...,
        description="Share of pages inside a complete, self-consistent 'Page i of N' run. "
                    "The predictor of whether an automatic split can work — measured at "
                    "74% on a pile that splits cleanly and 8% on one that cannot, both "
                    "with a text layer, which is why text quality is not the test")
    likely_documents: int = Field(
        ...,
        description="Documents that paginate themselves. A LOWER BOUND: a receipt prints "
                    "no pagination and is not counted")


class ReceivedDocumentResponse(BaseModel):
    """One file on record, and how far it has been split."""

    source_document_id: int = Field(..., description="The as-produced record")
    matter_id: int = Field(..., description="The matter")
    original_filename: str = Field(..., description="The name as served")
    byte_count: int = Field(..., description="Size in bytes")
    page_count: Optional[int] = Field(default=None, description="Pages, once known")
    received_on: date = Field(..., description="When it reached the firm")
    documents: int = Field(..., description="Documents split off it so far")
    surveyed: bool = Field(
        ...,
        description="The file has been read, so its routing verdict is known. False while "
                    "the survey job is still queued or running")
    # `opened` was here and is deliberately gone. It meant "page_features
    # exist", which the survey job makes true of EVERY file -- so the list said
    # "started" about a file nobody had touched. The three states that remain
    # are each true of the row they sit on: not read yet, read but unsplit, or
    # split into N documents. Telling "opened the bench and stopped" apart from
    # "never opened" needs a fact nothing records, and a wrong flag is worse
    # than a missing one.
