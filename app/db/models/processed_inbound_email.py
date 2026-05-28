"""
app/db/models/processed_inbound_email.py - Durable idempotency record for inbound mail.

One row per inbound Message-ID the poller has committed. Redis is the fast
claim path; this table is the durable backstop so a message is never
reprocessed even after a Redis flush. Primary key is the Message-ID itself,
so this model intentionally has no integer ``id``.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProcessedInboundEmail(BaseModel):
    """Domain model for a processed inbound message."""
    message_id: str = Field(..., description="RFC 5322 Message-ID; primary key")
    foreign_session_uuid: Optional[UUID] = Field(default=None, description="Lead this message was attached to, if matched")


class ProcessedInboundEmailInDB(ProcessedInboundEmail):
    """Database model — append-only; PK is message_id, so no integer id and no updated_at."""
    processed_at: datetime = Field(..., description="Set by the database")
    model_config = ConfigDict(from_attributes=True)
