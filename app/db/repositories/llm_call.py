"""
app/db/repositories/llm_call.py - Repository for the LLM cost ledger.

Append-only. Insert and read; never update. The table's trigger refuses updates
anyway, but a repository that offered one would invite the attempt.
"""
from datetime import datetime

from db.models.llm_call import LlmCallInDB
from db_handler import BaseRepository, DatabaseManager


class LlmCallRepository(BaseRepository[LlmCallInDB]):
    """Append-only repository for ``llm_calls``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "llm_calls", LlmCallInDB)

    def outcomes_since(self, since: datetime, limit: int = 2000) -> list[str]:
        """
        The ``outcome`` of every attempt since a moment, newest first.

        Only the outcome column: the status page needs "is AI reading working",
        and a row of this table names the matter, the staff member and the task.
        Built on the client directly, without the manager's retries, for the same
        reason as ``JobRepository.oldest_queued_at``. Raises on error.
        """
        rows = (
            self.manager.client.table(self.table_name)
            .select("outcome")
            .gte("occurred_at", since.isoformat())
            .order("occurred_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        return [str(r.get("outcome") or "") for r in rows]
