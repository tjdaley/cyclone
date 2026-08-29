"""
app/db/models/job.py - Background job records.

A job is work that cannot finish inside an HTTP request: the caller uploads,
gets an id back immediately, and polls. The worker does the work.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobKind(str, Enum):
    """What the job does. Values must match the jobs.kind CHECK constraint."""
    matter_intake = "matter_intake"
    statement_ingest = "statement_ingest"


class JobStatus(str, Enum):
    """
    Lifecycle of a job. Values must match the jobs.status CHECK constraint.

    ``succeeded`` and ``failed`` are terminal — a poller stops on either.
    """
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Job(BaseModel):
    """A unit of background work."""
    kind: JobKind = Field(..., description="What this job does")
    status: JobStatus = Field(default=JobStatus.queued, description="Lifecycle state")
    storage_path: Optional[str] = Field(
        default=None,
        description="Supabase Storage path of the uploaded input, e.g. the PDF to read",
    )
    matter_id: Optional[int] = Field(
        default=None,
        description="Matter the work belongs to. Null for matter intake, which has no matter yet",
    )
    requested_by_staff_id: int = Field(..., description="FK to the staff member who started it")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Options the job was started with, e.g. a Bates prefix override. "
                    "Shape depends on kind; the counterpart to result",
    )
    result: Optional[dict[str, Any]] = Field(
        default=None,
        description="Payload the caller polls for; shape depends on kind",
    )
    error: Optional[str] = Field(default=None, description="Why the job failed, for the caller to read")
    attempts: int = Field(default=0, description="How many times a worker has picked this up")
    started_at: Optional[datetime] = Field(default=None, description="When a worker claimed it")
    finished_at: Optional[datetime] = Field(default=None, description="When it reached a terminal state")


class JobInDB(Job):
    """Database model for the jobs table."""
    id: str = Field(..., description="Primary key (UUID), set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)
