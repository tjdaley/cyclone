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


def _run_jobs() -> None:
    """
    Run queued background jobs — currently matter intake extraction.

    Deliberately outside the ``crm:poller`` lock. That lock makes CRM polling
    fleet-wide single-runner, which is right for scanning a shared mailbox but
    wrong here: jobs are claimed individually, so every node should take work.
    """
    try:
        done = job_service.run_pending(get_db_manager(), limit=3)
        if done:
            LOGGER.info("crm_worker: completed %s background job(s)", done)
    except Exception as e:  # noqa: BLE001 — a bad job must not stop the loop
        LOGGER.error("crm_worker: job run failed err=%s", str(e))


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
    """
    job_interval = max(1, settings.job_poll_interval_seconds)
    crm_interval = settings.lead_poll_interval_seconds
    LOGGER.info(
        "CRM worker started | jobs every %ss, CRM every %ss, redis=%s",
        job_interval, crm_interval, settings.redis_url,
    )
    next_crm_run = 0.0
    while True:
        _run_jobs()
        now = time.monotonic()
        if now >= next_crm_run:
            next_crm_run = now + crm_interval
            try:
                _tick()
            except Exception as e:  # noqa: BLE001 — never let the loop die
                LOGGER.error("crm_worker: tick failed err=%s", str(e))
        time.sleep(job_interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOGGER.info("CRM worker stopping")
