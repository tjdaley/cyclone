"""
app/schemas/bench.py - Request and response shapes for the splitting bench.
"""
from typing import Optional

from pydantic import BaseModel, Field


class BenchPage(BaseModel):
    """One page of the grid: what it prints, and whether anybody has claimed it."""

    page: int = Field(..., description="1-based page number in the source file")
    chars: int = Field(..., description="Characters in the page's text layer")
    page_of: Optional[str] = Field(
        default=None, description="Pagination the page prints, e.g. 'Page 1 of 4'")
    date: Optional[str] = Field(
        default=None, description="A date printed in the page's header, ISO format")
    account: Optional[str] = Field(
        default=None, description="A digit group the header labels as an account number")
    snippet: Optional[str] = Field(
        default=None, description="First characters of the page, to recognise it by")
    blank: bool = Field(..., description="Nothing to read — a separator sheet or a blank back")
    unreadable: bool = Field(
        ..., description="Has text, but not language: a bad scan the text layer cannot serve")
    claimed_by_document_id: Optional[int] = Field(
        default=None, description="The document holding this page, if one already does")


class BundleSummary(BaseModel):
    """One document already split off this file, and the pages it holds."""

    document_id: int = Field(..., description="The document")
    doc_type: Optional[str] = Field(
        default=None,
        description="Normally null — the bench does not classify. Set only on a swept-up "
                    "remainder that really was all blank or unreadable")
    ranges: list[str] = Field(..., description="Page ranges in the source, e.g. ['10-12', '15']")
    pages: list[int] = Field(..., description="Every page it holds, so the grid can mark them")
    page_count: int = Field(..., description="How many pages")
    statement_id: Optional[int] = Field(
        default=None,
        description="The statement read out of this document, once one has been. A document "
                    "holding SEVERAL statements stays null and says so on its flags instead: "
                    "picking one of them would make the production index cite one account's "
                    "period for a document covering three")
    statement_label: Optional[str] = Field(
        default=None,
        description="What that statement IS -- institution, account and period. A row id "
                    "correlates to nothing a person can see; this is how a statement is "
                    "recognised on the page it was printed on")
    can_undo: bool = Field(
        ...,
        description="False once something downstream points at it — a classification is cheap "
                    "to redo, a request mapping somebody approved is not")


class BenchSessionResponse(BaseModel):
    """What an operator is in for, reported before they start.

    Stating the size of the job is what makes cancelling a real choice: an
    oversized pile is routed here rather than refused, and an offer whose cost
    is hidden is a trap rather than an option.
    """

    source_document_id: int = Field(..., description="The received file being split")
    matter_id: int = Field(..., description="The matter it was produced in")
    original_filename: str = Field(..., description="The name as served")
    surveyed: bool = Field(
        ...,
        description="The file has been read, so the grid can be shown. While this is false "
                    "every count below is zero -- and zero pages must not be read as "
                    "nothing left to do, which is exactly what an empty grid said")
    page_count: int = Field(..., description="Pages in the file")
    documents_so_far: int = Field(..., description="Documents already bundled off it")
    pages_unclaimed: int = Field(..., description="Pages no document holds yet")
    readable_pages: int = Field(
        ..., description="Pages with a usable text layer; the rest show no badges")
    held_by_other: bool = Field(
        ...,
        description="Somebody ELSE holds this pile. Re-entry by the same person is not a "
                    "conflict — getting that wrong made this warning fire on every bundle")
    bundles: list[BundleSummary] = Field(
        default_factory=list,
        description="What has already been split off, so the screen can show which pages are "
                    "in which document without remembering it across a reload")


class BenchGridResponse(BaseModel):
    """One screen of pages."""

    source_document_id: int = Field(..., description="The received file being split")
    page_count: int = Field(..., description="Total pages in the file")
    pages_shown: int = Field(
        ..., description="Pages in the list being paged over — unclaimed only, unless asked")
    offset: int = Field(..., description="Index of the first page returned")
    pages: list[BenchPage] = Field(..., description="The pages on this screen")


class BundleRequest(BaseModel):
    """The pages an operator selected.

    A page LIST, not ranges: the client holds a selection, and the single
    conversion to ranges happens server-side so the two cannot drift. Duplicates
    collapse and order is irrelevant — a selection is a set.
    """

    pages: list[int] = Field(
        ..., min_length=1, description="1-based page numbers, in any order")


class BundleResponse(BaseModel):
    """The document that was made."""

    document_id: int = Field(..., description="The new document")
    ranges: list[str] = Field(..., description="Its page ranges in the source, e.g. ['10-12']")
    page_count: int = Field(..., description="Pages it holds")
    storage_path: Optional[str] = Field(default=None, description="Its working PDF")
    pages_unclaimed: int = Field(
        ..., description="Pages still on the grid, so the client need not refetch to know")


class FinishResponse(BaseModel):
    """What the pile came to."""

    source_document_id: int = Field(..., description="The received file")
    documents: int = Field(..., description="Documents split off it, including any sweep")
    pages_swept: int = Field(
        ..., description="Unclaimed pages gathered into one blank_or_separator document")
    swept_document_id: Optional[int] = Field(
        default=None, description="That document, when there was anything to sweep")
    swept_as_separator: bool = Field(
        default=False,
        description="The swept pages were ALL blank or unreadable, so labelling them "
                    "blank_or_separator is true of them. False means they are an unsorted "
                    "remainder carrying no label, which is the honest answer when somebody "
                    "pressed Finish earlier than they meant to")
