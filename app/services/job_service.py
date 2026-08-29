"""
app/services/job_service.py - Queue and run background jobs.

Work that outlives an HTTP request lives here. Matter intake reads a PDF —
one LLM vision call per image-only page — then runs two more LLM calls; on a
scanned pleading that is minutes, which a reverse proxy will cut off long
before it finishes.

Split of responsibilities:
  * ``enqueue`` runs in the API. It stores the upload and inserts a queued row.
  * ``run_pending`` runs in the worker. It claims queued rows and does the work.
  * ``get_for_staff`` serves the poll.

Claiming is two-layered. A Redis SET NX keeps two nodes from starting the same
job in the same instant, and the status transition to ``running`` keeps it
claimed after the Redis key expires. Redis being down degrades to the status
check alone rather than stopping work.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from db.models.job import Job, JobInDB, JobKind, JobStatus
from db.repositories.job import JobRepository
from db_handler import DatabaseManager
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# Long enough that a second worker will not re-claim a job still being worked,
# short enough that a job orphaned by a crashed node is retried the same day.
_CLAIM_TTL_SECONDS = 1800


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobService:
    """Queues background work and runs it in the worker."""

    # ── API side ──────────────────────────────────────────────────────────

    def enqueue_matter_intake(
        self,
        manager: DatabaseManager,
        staff_id: int,
        pdf_bytes: bytes,
    ) -> JobInDB:
        """
        Store an uploaded pleading and queue it for extraction.

        Returns as soon as the file is stored — the caller polls from there.

        :param manager: Database manager for this request.
        :type manager: DatabaseManager
        :param staff_id: Who uploaded it.
        :type staff_id: int
        :param pdf_bytes: Raw PDF content.
        :type pdf_bytes: bytes
        :return: The queued job.
        :rtype: JobInDB
        """
        # The id is generated here, not by the database, so the PDF can be
        # stored BEFORE the row exists. Inserting first would publish a queued
        # job whose input is still uploading, and a worker polling in that
        # window would claim it and fail with "Job has no stored PDF".
        job_id = str(uuid4())
        path = StorageService(manager).upload_intake(job_id, pdf_bytes)

        job = JobRepository(manager).insert({
            "id": job_id,
            **Job(
                kind=JobKind.matter_intake,
                status=JobStatus.queued,
                storage_path=path,
                requested_by_staff_id=staff_id,
            ).model_dump(),
        })
        LOGGER.info("job_service.enqueue_matter_intake: job=%s staff=%s", job.id, staff_id)
        return job

    def enqueue_statement_ingest(
        self,
        manager: DatabaseManager,
        staff_id: int,
        matter_id: int,
        pdf_bytes: bytes,
        bates_prefix: Optional[str] = None,
        source_filename: Optional[str] = None,
    ) -> JobInDB:
        """
        Store an uploaded statement PDF and queue it for extraction.

        Same ordering rule as intake: the file is stored before the row is
        queued, so a worker can never claim a job whose input is still
        uploading.

        :param matter_id: Matter these statements belong to — known up front,
            unlike intake.
        :param source_filename: The uploaded file's own name. Storage renames it
            to the job id, so without this there is nothing tying a statement in
            the exceptions queue back to the document it came out of.
        :param bates_prefix: Optional stamp prefix to disambiguate detection on
            an odd production. Carried on the job because the user types it at
            the upload and the worker needs it minutes later, elsewhere.
        :return: The queued job.
        :rtype: JobInDB
        """
        job_id = str(uuid4())
        path = StorageService(manager).upload_intake(job_id, pdf_bytes)

        job = JobRepository(manager).insert({
            "id": job_id,
            **Job(
                kind=JobKind.statement_ingest,
                status=JobStatus.queued,
                storage_path=path,
                matter_id=matter_id,
                requested_by_staff_id=staff_id,
                params={k: v for k, v in {
                    "bates_prefix": bates_prefix,
                    "source_filename": source_filename,
                }.items() if v},
            ).model_dump(),
        })
        LOGGER.info("job_service.enqueue_statement_ingest: job=%s matter=%s staff=%s",
                    job.id, matter_id, staff_id)
        return job

    def get_for_staff(self, manager: DatabaseManager, job_id: str, staff_id: int) -> Optional[JobInDB]:
        """Fetch a job for polling, scoped to the staff member who started it."""
        return JobRepository(manager).get_for_staff(job_id, staff_id)

    # ── Worker side ───────────────────────────────────────────────────────

    def run_pending(self, manager: DatabaseManager, limit: int = 3) -> int:
        """
        Claim and run queued intake jobs.

        :param manager: Database manager for the worker.
        :type manager: DatabaseManager
        :param limit: Most jobs to run in one tick.
        :type limit: int
        :return: How many jobs this worker completed (succeeded or failed).
        :rtype: int
        """
        repo = JobRepository(manager)
        handlers = {
            JobKind.matter_intake: self._run_matter_intake,
            JobKind.statement_ingest: self._run_statement_ingest,
        }
        done = 0
        for kind, handler in handlers.items():
            for job in repo.next_queued(kind, limit=limit):
                if not self._claim(repo, job):
                    continue
                handler(manager, repo, job)
                done += 1
        return done

    def _claim(self, repo: JobRepository, job: JobInDB) -> bool:
        """
        Take ownership of a job, or leave it to whoever already has it.

        :return: True when this worker may run the job.
        :rtype: bool
        """
        # Imported here, not at module load: only the worker claims jobs, and
        # the API should not pull in a Redis client to enqueue and poll.
        from util.redis_client import claim_once  # noqa: PLC0415

        try:
            if not claim_once("job:%s" % job.id, ttl_seconds=_CLAIM_TTL_SECONDS):
                LOGGER.debug("job_service: job=%s already claimed by another node", job.id)
                return False
        except Exception as e:  # noqa: BLE001 — Redis down must not stop the queue
            LOGGER.warning("job_service: claim lock unavailable for job=%s (%s); relying on status", job.id, str(e))

        repo.update(job.id, {
            "status": JobStatus.running.value,
            "started_at": _now(),
            "attempts": job.attempts + 1,
        })
        return True

    def _run_statement_ingest(self, manager: DatabaseManager, repo: JobRepository, job: JobInDB) -> None:
        """
        Read a statement PDF and write its statements, accounts, and lines.

        Unlike matter intake this commits directly rather than parking a
        preview: statements that reconcile need no review, and the ones that do
        not are held in the exceptions queue by their ``review_status``. The
        job's result is the ingest summary the caller polls for.
        """
        from services.pdf_service import pdf_service  # noqa: PLC0415
        from services.statement_service import statement_service  # noqa: PLC0415

        LOGGER.info("job_service: running statement_ingest job=%s attempt=%s", job.id, job.attempts + 1)
        try:
            if not job.storage_path:
                raise ValueError("Job has no stored PDF")
            if job.matter_id is None:
                raise ValueError("Statement ingest requires a matter")

            pdf_bytes = StorageService(manager).download(job.storage_path)
            if not pdf_bytes:
                raise ValueError("Stored PDF could not be read back")

            # Page markers on: a Rule 1006 summary has to cite the page each
            # line came off, and the model cannot report a page it was not shown.
            raw_text = pdf_service.extract_text(pdf_bytes, page_markers=True)
            if not raw_text.strip():
                raise ValueError("No text could be extracted from the PDF")

            extracted = statement_service.extract(raw_text)
            # The institution is often only in the letterhead graphic, which the
            # text layer never carried. Look at the page before filing an
            # account under "Unknown institution" — that name becomes half the
            # dedup key, so getting it wrong splits one account into two.
            statement_service.resolve_missing_institutions(extracted, pdf_bytes)
            summary = statement_service.commit_document(
                manager=manager,
                matter_id=job.matter_id,
                staff_id=job.requested_by_staff_id,
                extracted=extracted,
                raw_text=raw_text,
                storage_path=job.storage_path,
                source_job_id=job.id,
                bates_prefix=(job.params or {}).get("bates_prefix"),
                source_filename=(job.params or {}).get("source_filename"),
            )
            repo.update(job.id, {
                "status": JobStatus.succeeded.value,
                "result": summary,
                "error": None,
                "finished_at": _now(),
            })
            LOGGER.info(
                "job_service: statement_ingest job=%s found=%s auto_accepted=%s needs_review=%s",
                job.id, summary["statements_found"], summary["auto_accepted"], summary["needs_review"],
            )
        except Exception as e:  # noqa: BLE001 — the failure belongs on the job
            LOGGER.error("job_service: statement_ingest job=%s failed: %s", job.id, str(e))
            repo.update(job.id, {
                "status": JobStatus.failed.value,
                "error": str(e),
                "finished_at": _now(),
            })

    def _requester_role(self, manager: DatabaseManager, staff_id: int) -> Optional[str]:
        """
        Resolve the requester's effective role.

        The worker has no request to read it from, but lead matching is
        access-filtered by slug, so the extraction has to run as the person who
        uploaded — not with blanket visibility.

        :return: Primary role string, or None when it cannot be resolved.
        :rtype: Optional[str]
        """
        from db.models.user_role import primary_role  # noqa: PLC0415
        from db.repositories.staff import StaffRepository  # noqa: PLC0415
        from db.repositories.user_role import UserRoleRepository  # noqa: PLC0415

        staff = StaffRepository(manager).select_one(condition={"id": staff_id})
        if staff is None or not staff.supabase_uid:
            return None
        roles = [r.role.value for r in UserRoleRepository(manager).get_by_uid(staff.supabase_uid)]
        return primary_role(roles) if roles else None

    def _run_matter_intake(self, manager: DatabaseManager, repo: JobRepository, job: JobInDB) -> None:
        """Read the stored PDF, extract the case style, and record the result."""
        from dependencies import get_landing_pages_db  # noqa: PLC0415
        from services.intake_service import intake_service  # noqa: PLC0415 — avoids an import cycle
        from services.pdf_service import pdf_service  # noqa: PLC0415

        LOGGER.info("job_service: running matter_intake job=%s attempt=%s", job.id, job.attempts + 1)
        try:
            if not job.storage_path:
                raise ValueError("Job has no stored PDF")
            pdf_bytes = StorageService(manager).download(job.storage_path)
            if not pdf_bytes:
                raise ValueError("Stored PDF could not be read back")

            raw_text = pdf_service.extract_text(pdf_bytes)
            if not raw_text.strip():
                raise ValueError("No text could be extracted from the PDF")

            # Lead matching is a convenience; if the requester's role cannot be
            # resolved we extract without it rather than failing the job.
            role = self._requester_role(manager, job.requested_by_staff_id)
            foreign_db = get_landing_pages_db() if role else None

            preview = intake_service.preview(
                manager=manager,
                raw_text=raw_text,
                foreign_db=foreign_db,
                staff_id=job.requested_by_staff_id if role else None,
                role=role,
            )
            repo.update(job.id, {
                "status": JobStatus.succeeded.value,
                "result": preview.model_dump(mode="json"),
                "error": None,
                "finished_at": _now(),
            })
            LOGGER.info("job_service: matter_intake job=%s succeeded", job.id)
        except Exception as e:  # noqa: BLE001 — the failure belongs on the job, not in the worker loop
            LOGGER.error("job_service: matter_intake job=%s failed: %s", job.id, str(e))
            repo.update(job.id, {
                "status": JobStatus.failed.value,
                "error": str(e),
                "finished_at": _now(),
            })


job_service = JobService()
