"""
app/schemas/common.py - Shared response schemas used across multiple domains.
"""
from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    """Generic success/info response."""
    message: str = Field(..., description="Human-readable status message")


class DeletedResponse(BaseModel):
    """Confirmation that a record was deleted."""
    deleted: bool = Field(default=True, description="True if deletion succeeded")
    id: int = Field(..., description="Primary key of the deleted record")


class PaginatedMeta(BaseModel):
    """Pagination metadata included in list responses."""
    total: int = Field(..., description="Total number of matching records")
    page: int = Field(..., description="Current page (1-based)")
    page_size: int = Field(..., description="Records per page")
