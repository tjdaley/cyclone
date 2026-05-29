"""
app/db/models/kb_article.py - Editable knowledge-base content for the CRM compose agent.

One row per fact the agent may need to cite when answering a PNC's logistical
question (hours, locations, meeting types, fees, practice areas, geography…).
Phase 1 loads all active rows directly into the retrieval agent's system prompt.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class KbArticle(BaseModel):
    """Domain model for a knowledge-base article."""
    topic: str = Field(..., description="Top-level grouping, e.g. 'Office', 'Fees', 'Practice areas'")
    subtopic: Optional[str] = Field(
        default=None,
        description="Optional sub-grouping under a topic, e.g. 'Hours', 'Locations'",
    )
    body_md: str = Field(..., description="Article content in Markdown")
    active: bool = Field(default=True, description="If false, excluded from the agent's KB prompt")
    sort_order: int = Field(default=0, description="Ascending sort within topic in the editor + prompt")


class KbArticleInDB(KbArticle):
    """Database model — adds DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by the database on update")
    model_config = ConfigDict(from_attributes=True)
