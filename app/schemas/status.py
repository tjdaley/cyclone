"""
app/schemas/status.py - The public service-status response.

Deliberately coarse: it is served without authentication. States and sentences
only — see services/status_service.py for what is left out and why.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

ComponentState = Literal["operational", "degraded", "down", "idle", "unknown"]


class StatusComponent(BaseModel):
    key: str = Field(..., description="Stable identifier: api, database, storage, processor, ai")
    name: str = Field(..., description="What a user calls this part of Cyclone")
    state: ComponentState = Field(..., description="idle = healthy with nothing to report; "
                                                   "unknown = could not be checked")
    summary: str = Field(..., description="One sentence saying what this means for the user")
    latency_ms: Optional[int] = Field(default=None, description="Database round trip, when measured")


class ServiceStatusResponse(BaseModel):
    overall: Literal["operational", "degraded", "down", "unknown"] = Field(
        ..., description="The worst component state; idle counts as operational")
    checked_at: str = Field(..., description="ISO timestamp of the check (may be up to 15s cached)")
    version: str = Field(..., description="Application version")
    server: str = Field(..., description="Which node answered — already public on /api/config")
    components: list[StatusComponent] = Field(..., description="One row per part of Cyclone")
