"""
app/db/repositories/pleading.py - Repositories for pleadings, claims, children, and opposing counsel.
"""
from datetime import date
from typing import Optional

from db.models.pleading import (
    ClaimKind,
    MatterChildInDB,
    MatterClaimInDB,
    MatterOpposingCounselInDB,
    MatterPleadingInDB,
    OpposingCounselInDB,
)
from db_handler import BaseRepository, DatabaseManager

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class MatterChildRepository(BaseRepository[MatterChildInDB]):
    """CRUD for the ``matter_children`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_children", MatterChildInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterChildInDB]:
        return self.select_many(condition={"matter_id": matter_id}, sort_by="date_of_birth")[0]


class OpposingCounselRepository(BaseRepository[OpposingCounselInDB]):
    """CRUD for the ``opposing_counsel`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "opposing_counsel", OpposingCounselInDB)

    def get_by_bar_number(self, bar_state: str, bar_number: str) -> Optional[OpposingCounselInDB]:
        """Dedup lookup — the canonical way to find an existing OC row."""
        LOGGER.debug("OpposingCounselRepository.get_by_bar_number: %s:%s", bar_state, bar_number)
        return self.select_one(condition={"bar_state": bar_state, "bar_number": bar_number})


class MatterOpposingCounselRepository(BaseRepository[MatterOpposingCounselInDB]):
    """CRUD for the ``matter_opposing_counsel`` intersection table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_opposing_counsel", MatterOpposingCounselInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterOpposingCounselInDB]:
        return self.select_many(condition={"matter_id": matter_id})[0]

    def exists_for_matter(self, matter_id: int, opposing_counsel_id: int) -> bool:
        rows = self.select_many(
            condition={"matter_id": matter_id, "opposing_counsel_id": opposing_counsel_id},
        )[0]
        return len(rows) > 0


def _pleading_order(pleading: MatterPleadingInDB) -> tuple:
    """
    Chronological sort key: filed date, then served date, then id.

    A pleading missing both dates sorts last rather than first — an undated row
    is usually one still being worked out, not the oldest thing on the matter.
    The id tie-breaks so the order is stable across calls.
    """
    far_future = date.max
    return (pleading.filed_date or far_future, pleading.served_date or far_future, pleading.id)


class MatterPleadingRepository(BaseRepository[MatterPleadingInDB]):
    """CRUD for the ``matter_pleadings`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_pleadings", MatterPleadingInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterPleadingInDB]:
        """
        Return a matter's pleadings in chronological order.

        Sorted here rather than in the query because the manager applies a
        single ORDER BY column, and the order is on (filed_date, served_date).
        A matter holds a handful of pleadings, so this is not worth a
        round-trip's worth of complexity to push into PostgREST.
        """
        rows = self.select_many(condition={"matter_id": matter_id})[0]
        return sorted(rows, key=_pleading_order)


class MatterClaimRepository(BaseRepository[MatterClaimInDB]):
    """CRUD for the ``matter_claims`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "matter_claims", MatterClaimInDB)

    def get_by_matter(self, matter_id: int) -> list[MatterClaimInDB]:
        return self.select_many(condition={"matter_id": matter_id})[0]

    def get_by_pleading(self, pleading_id: int) -> list[MatterClaimInDB]:
        return self.select_many(condition={"matter_pleading_id": pleading_id})[0]

    def get_by_kind(self, matter_id: int, kind: ClaimKind) -> list[MatterClaimInDB]:
        return self.select_many(condition={"matter_id": matter_id, "kind": kind.value})[0]
