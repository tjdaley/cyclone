"""
app/db/repositories/foreign_lead.py - Read-only access to the landing-pages DB.

Cyclone never writes to the landing-pages project. These repositories wrap
read queries against ``leads`` and ``attorneys`` so the rest of the app can
work with typed Pydantic objects instead of raw dicts.

The repositories follow the same BaseRepository[T] pattern as cyclone's own
tables, but they are instantiated with a different DatabaseManager (one
pointed at the landing-pages credentials — see dependencies.get_landing_pages_db).
"""
from typing import Optional
from uuid import UUID

from db_handler import BaseRepository, DatabaseManager

from db.models.foreign_lead import ForeignAttorney, ForeignLead
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class ForeignLeadRepository(BaseRepository[ForeignLead]):
    """Read-only repository for landing-pages.leads."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "leads", ForeignLead)

    def list_by_slugs(
        self,
        slugs: list[str],
        limit: int = 200,
        offset: int = 0,
    ) -> list[ForeignLead]:
        """Return leads matching any of the given attorney slugs, newest first."""
        if not slugs:
            return []
        records, _ = self.select_many(
            condition={"attorney_slug": slugs},
            sort_by="created_at",
            sort_direction="desc",
            start=offset,
            end=offset + max(0, limit - 1),
        )
        return records

    def list_all(self, limit: int = 200, offset: int = 0) -> list[ForeignLead]:
        """Return all leads, newest first. Use for admins (implicit wildcard)."""
        records, _ = self.select_many(
            condition={},
            sort_by="created_at",
            sort_direction="desc",
            start=offset,
            end=offset + max(0, limit - 1),
        )
        return records

    def get_by_session_uuid(self, session_uuid: UUID) -> Optional[ForeignLead]:
        """Return one lead by its stable cross-DB key."""
        return self.select_one(condition={"session_uuid": str(session_uuid)})


class ForeignAttorneyRepository(BaseRepository[ForeignAttorney]):
    """Read-only repository for landing-pages.attorneys."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "attorneys", ForeignAttorney)

    def get_by_slug(self, slug: str) -> Optional[ForeignAttorney]:
        """Return one attorney by slug."""
        return self.select_one(condition={"slug": slug})

    def list_all(self) -> list[ForeignAttorney]:
        """Return all attorneys."""
        records, _ = self.select_many(condition={})
        return records
