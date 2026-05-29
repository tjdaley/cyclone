"""
app/db/repositories/kb_article.py - Repository for the kb_articles table.
"""
from db_handler import BaseRepository, DatabaseManager

from db.models.kb_article import KbArticleInDB
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class KbArticleRepository(BaseRepository[KbArticleInDB]):
    """CRUD repository for the ``kb_articles`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "kb_articles", KbArticleInDB)

    def list_all(self) -> list[KbArticleInDB]:
        """Return every article (active or not) ordered for the editor."""
        records, _ = self.select_many(
            condition={},
            sort_by="sort_order",
            sort_direction="asc",
        )
        return records

    def list_active(self) -> list[KbArticleInDB]:
        """Return only active articles — the set the agent sees in its prompt."""
        records, _ = self.select_many(
            condition={"active": True},
            sort_by="sort_order",
            sort_direction="asc",
        )
        return records
