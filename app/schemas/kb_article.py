"""
app/schemas/kb_article.py - Request and response schemas for KB endpoints.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class KbArticleCreateRequest(BaseModel):
    """Body for POST /api/v1/kb-articles."""
    topic: str = Field(..., description="Top-level grouping")
    subtopic: Optional[str] = Field(default=None)
    body_md: str = Field(..., description="Markdown content")
    active: bool = Field(default=True)
    sort_order: int = Field(default=0)


class KbArticleUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/kb-articles/{id} — all fields optional."""
    topic: Optional[str] = None
    subtopic: Optional[str] = None
    body_md: Optional[str] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None


class KbArticleResponse(BaseModel):
    """Response shape for KB endpoints."""
    id: int
    topic: str
    subtopic: Optional[str]
    body_md: str
    active: bool
    sort_order: int
    created_at: datetime
    updated_at: Optional[datetime]
