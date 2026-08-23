"""
app/db/repositories/job.py - CRUD for the ``jobs`` table.
"""
from typing import Optional

from db.models.job import JobInDB, JobKind, JobStatus
from db_handler import BaseRepository, DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


class JobRepository(BaseRepository[JobInDB]):
    """CRUD for the ``jobs`` table."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "jobs", JobInDB)

    def next_queued(self, kind: JobKind, limit: int = 5) -> list[JobInDB]:
        """
        Return the oldest queued jobs of a kind.

        A batch rather than a single row because several workers poll the same
        queue: each claims what it can and skips what another already took, so
        fetching one row would mean the losers idle a whole tick.

        :param kind: Job kind to poll for.
        :type kind: JobKind
        :param limit: How many candidates to consider this tick.
        :type limit: int
        :return: Queued jobs, oldest first.
        :rtype: list[JobInDB]
        """
        records, _ = self.select_many(
            condition={"kind": kind.value, "status": JobStatus.queued.value},
            sort_by="created_at",
            sort_direction="asc",
            start=0,
            end=max(limit - 1, 0),
        )
        return records

    def get_for_staff(self, job_id: str, staff_id: int) -> Optional[JobInDB]:
        """
        Fetch a job only if it belongs to this staff member.

        Scoping the read to the requester keeps one user's extraction — which
        can contain an entire pleading — from being polled by another.

        :param job_id: Job UUID.
        :type job_id: str
        :param staff_id: Staff member doing the polling.
        :type staff_id: int
        :return: The job, or None when it does not exist or is not theirs.
        :rtype: Optional[JobInDB]
        """
        return self.select_one(condition={"id": job_id, "requested_by_staff_id": staff_id})
