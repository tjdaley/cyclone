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
    interval = settings.lead_poll_interval_seconds
    LOGGER.info("CRM worker started | interval=%ss redis=%s", interval, settings.redis_url)
    while True:
        try:
            _tick()
        except Exception as e:  # noqa: BLE001 — never let the loop die
            LOGGER.error("crm_worker: tick failed err=%s", str(e))
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOGGER.info("CRM worker stopping")
