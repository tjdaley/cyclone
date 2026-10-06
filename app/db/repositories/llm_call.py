"""
app/db/repositories/llm_call.py - Repository for the LLM cost ledger.

Append-only. Insert and read; never update. The table's trigger refuses updates
anyway, but a repository that offered one would invite the attempt.
"""
from db.models.llm_call import LlmCallInDB
from db_handler import BaseRepository, DatabaseManager


class LlmCallRepository(BaseRepository[LlmCallInDB]):
    """Append-only repository for ``llm_calls``."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "llm_calls", LlmCallInDB)
