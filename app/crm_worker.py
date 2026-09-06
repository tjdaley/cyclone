"""
app/crm_worker.py - CRM email agent poller.

Runs as a dedicated worker container (one per node). All nodes share one
Redis/Valkey, so the ``crm:poller`` lock makes polling fleet-wide single-runner
with automatic failover: exactly one node polls per tick, and if it dies another
acquires the lock on the next tick.

Run from the app directory (same image as the API):

    python crm_worker.py
"""
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from dependencies import get_db_manager, get_landing_pages_db
from services.crm_agent_service import crm_agent_service
from services.diff_explainer_service import diff_explainer_service
from services.email_service import email_service
from services.job_service import job_service
from db.repositories.foreign_lead import ForeignLeadRepository
from db.repositories.lead_agent_run import LeadAgentRunRepository
from util.loggerfactory import LoggerFactory
from util.redis_client import lock
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_POLLER_LOCK = "crm:poller"


def _poll_welcomes() -> None:
    """Send the welcome template to recently-created leads not yet welcomed."""
    cutoff = crm_agent_service.welcome_cutoff()
    if cutoff is None:
        LOGGER.debug("crm_worker: WELCOME_LEADS_AFTER unset; skipping welcome scan")
        return

    cyclone_db = get_db_manager()
    foreign_db = get_landing_pages_db()
    recent = ForeignLeadRepository(foreign_db).list_recent(limit=100)
    for lead in recent:
        if lead.created_at < cutoff:
            continue
        try:
            crm_agent_service.process_new_lead(cyclone_db, lead)
        except Exception as e:  # noqa: BLE001 — one bad lead must not stop the batch
            LOGGER.error("crm_worker: process_new_lead failed session=%s err=%s", lead.session_uuid, str(e))


def _poll_inbound() -> None:
    """Fetch unseen intake mail and log each message to its lead's timeline."""
    if not settings.imap_host:
        LOGGER.debug("crm_worker: IMAP not configured; skipping inbound scan")
        return
    cyclone_db = get_db_manager()
    foreign_db = get_landing_pages_db()
    for inbound in email_service.fetch_unseen():
        try:
            crm_agent_service.ingest_inbound(cyclone_db, foreign_db, inbound)
        except Exception as e:  # noqa: BLE001
            LOGGER.error("crm_worker: inbound ingest failed message_id=%s err=%s", inbound.message_id, str(e))


def _run_diff_explainer() -> None:
    """Fill ``edit_explanation`` for runs where staff edited the AI draft.

    Capped at 5 per tick so a backlog of edited drafts can't blow up the LLM
    budget; on a typical tick this finds 0–1 rows and is a no-op.
    """
    cyclone_db = get_db_manager()
    runs_repo = LeadAgentRunRepository(cyclone_db)
    pending = runs_repo.list_pending_explanations(limit=5)
    if not pending:
        return
    LOGGER.info("diff_explainer: %s pending explanation(s)", len(pending))
    for run in pending:
        try:
            explanation = diff_explainer_service.explain_diff(
                run.draft_body or "",
                run.sent_body or "",
            )
            runs_repo.update(run.id, {"edit_explanation": explanation})
            LOGGER.info("diff_explainer: explained run_id=%s", run.id)
        except Exception as e:  # noqa: BLE001 — one bad run must not stop the batch
            LOGGER.error("diff_explainer: failed run_id=%s err=%s", run.id, str(e))


# Jobs this worker has started and not yet seen finish. Only the main loop
# touches it, so no lock: the pool's threads never look at it.
_IN_FLIGHT: set[Future] = set()


def _run_one(job: Any) -> None:
    """
    Run one claimed job on a pool thread, with its own database connection.

    A manager per job, not per worker: ``SupabaseManager`` is not thread-safe,
    and sharing one would interleave two ingests' requests down a single
    connection. It is cheap to make.
    """
    try:
        job_service.run_claimed(get_db_manager(), job)
    except Exception as e:  # noqa: BLE001 — a thread that raises kills nothing else
        LOGGER.error("crm_worker: job=%s failed err=%s", job.id, str(e), exc_info=True)


def _run_jobs(pool: ThreadPoolExecutor, capacity: int) -> None:
    """
    Top the pool back up to capacity with whatever is queued.

    **This must not block**, and that is the change that matters. It used to run
    each job to completion on the main loop, so a single statement — a
    thirteen-month upload took 1,600 seconds and was still going — stopped
    everything: no other job started, and the CRM tick did not run either,
    which is why a long ingest looked like a dead worker.

    Now the loop claims only what it has room for and hands it to the pool. Five
    statements read at once, and the tick carries on around them.

    Deliberately outside the ``crm:poller`` lock. That lock makes CRM polling
    fleet-wide single-runner, which is right for scanning a shared mailbox but
    wrong here: jobs are claimed individually, so every node should take work.
    """
    global _IN_FLIGHT
    _IN_FLIGHT = {future for future in _IN_FLIGHT if not future.done()}

    free = capacity - len(_IN_FLIGHT)
    if free <= 0:
        return
    try:
        # Claim only what there is room to run. A worker at capacity takes
        # nothing, which leaves the queue for a node that has room rather than
        # holding jobs hostage in a backlog of its own.
        claimed = job_service.claim_pending(get_db_manager(), limit=free)
    except Exception as e:  # noqa: BLE001 — a bad claim must not stop the loop
        LOGGER.error("crm_worker: job claim failed err=%s", str(e))
        return

    for job in claimed:
        _IN_FLIGHT.add(pool.submit(_run_one, job))
    if claimed:
        LOGGER.info("crm_worker: started %d job(s), %d now running of %d slots",
                    len(claimed), len(_IN_FLIGHT), capacity)


def _tick() -> None:
    # TTL covers a few cycles so the holder keeps the lock across one tick's
    # work, but frees within minutes if the node dies (failover).
    with lock(_POLLER_LOCK, ttl_seconds=max(60, settings.lead_poll_interval_seconds * 3)) as got:
        if not got:
            LOGGER.debug("crm_worker: another node holds the poller lock; idling this tick")
            return
        _poll_welcomes()
        _poll_inbound()
        _run_diff_explainer()


def main() -> None:
    """
    Two cadences in one loop.

    Jobs are picked up every ``job_poll_interval_seconds`` because somebody is
    watching a spinner while they wait. The CRM polls stay on their own, much
    slower schedule — running them every few seconds would hammer the mailbox
    and the landing-pages DB for no benefit.

    Neither cadence waits on a job any more. Jobs run in a pool of
    ``job_concurrency``; the loop only hands work to it and reaps what has
    finished, so a statement that takes half an hour no longer holds up the
    other four slots or the CRM tick.
    """
    job_interval = max(1, settings.job_poll_interval_seconds)
    crm_interval = settings.lead_poll_interval_seconds
    capacity = max(1, settings.job_concurrency)
    LOGGER.info(
        "CRM worker started | jobs every %ss (%s at a time), CRM every %ss, redis=%s",
        job_interval, capacity, crm_interval, settings.redis_url,
    )
    # Never shut down: the worker runs until the container stops, and an
    # executor closed on the way out would only cancel work already claimed.
    pool = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="job")
    next_crm_run = 0.0
    while True:
        _run_jobs(pool, capacity)
        now = time.monotonic()
        if now >= next_crm_run:
            next_crm_run = now + crm_interval
            try:
                _tick()
            except Exception as e:  # noqa: BLE001 — never let the loop die
                # With the traceback, because this handler catches everything
                # the tick touches — the poller lock, the landing-pages
                # database, IMAP, SMTP, and the LLM. "Connection refused" on its
                # own names none of them, and the tick repeats once a minute
                # forever, so the one line that would identify it is worth the
                # width. Jobs are unaffected: _run_jobs runs outside this.
                LOGGER.error("crm_worker: tick failed err=%s", str(e), exc_info=True)
        time.sleep(job_interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOGGER.info("CRM worker stopping")
