"""
app/services/crm_agent_service.py - CRM email agent orchestration.

Phase A scope (no AI yet):
  - send_welcome():   template acknowledgment on a new lead, threaded so replies come back.
  - ingest_inbound(): match an inbound reply to a lead and log it to the activity timeline.

Later phases add the triage → extract → retrieve → compose → guardrail pipeline
on top of ingest_inbound. Everything is driven by the crm_worker poller; nothing
here runs in the request path.
"""
from datetime import datetime, timezone
from typing import Optional

from db_handler import DatabaseManager

from db.models.foreign_lead import ForeignLead
from db.models.lead_action import LeadActionDirection, LeadActionType, LeadActorType
from db.models.lead_agent_run import LeadAgentRun, LeadAgentTrigger
from db.models.processed_inbound_email import ProcessedInboundEmail
from db.repositories.foreign_lead import ForeignLeadRepository
from db.repositories.lead_agent_run import LeadAgentRunRepository
from db.repositories.processed_inbound_email import ProcessedInboundEmailRepository
from services.email_service import InboundEmail, email_service
from services.lead_service import lead_service
from util.loggerfactory import LoggerFactory
from util.redis_client import claim_once
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


# Lead-supplied full_name is free text and often includes a courtesy title
# (e.g. "Mr. Test Lead"). Strip leading titles so the greeting doesn't become
# "Dear Mr.,".
_COURTESY_TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam", "rev", "hon"}


def _greeting(full_name: Optional[str]) -> str:
    """Return a salutation: 'Dear Mr. Lead' when a title is present, else
    'Dear <first name>', else 'Hello' when no usable name was provided."""
    tokens = (full_name or "").strip().split()
    if not tokens:
        return "Hello"
    if tokens[0].lower().rstrip(".") in _COURTESY_TITLES:
        rest = tokens[1:]
        return ("Dear %s %s" % (tokens[0], rest[-1])) if rest else "Hello"
    return "Dear %s" % tokens[0]


def _welcome_message(foreign_lead: ForeignLead) -> tuple[str, str]:
    """Build the (subject, body) for the automated welcome. Plain template — no AI."""
    firm = settings.firm_name
    subject = "Thank you for contacting %s" % firm
    body = (
        "%s,\n\n"
        "Thank you for reaching out to %s. We've received your message and a member "
        "of our team will be in touch with you shortly.\n\n"
        "If your matter is urgent, please call our office.\n\n"
        "— %s\n\n"
        "This is an automated acknowledgment. You can reply to this email with any "
        "additional details and your message will reach our intake team."
    ) % (_greeting(foreign_lead.full_name), firm, firm)
    return subject, body


class CrmAgentService:

    # ── Welcome (outbound-first) ──────────────────────────────────────────

    def send_welcome(
        self,
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
    ) -> bool:
        """
        Send the one-time welcome for a lead. Idempotent via the run ledger.

        :return: True if a welcome was sent this call, False if skipped.
        """
        if not foreign_lead.email:
            LOGGER.debug("crm_agent.send_welcome: lead has no email; skipping session=%s", foreign_lead.session_uuid)
            return False

        runs = LeadAgentRunRepository(cyclone_db)
        if runs.welcome_exists(foreign_lead.session_uuid):
            return False

        # Create the workflow row (also fires slug auto-assignment).
        lead_service.ensure_workflow_row(cyclone_db, foreign_lead)

        subject, body = _welcome_message(foreign_lead)
        message_id = email_service.send(foreign_lead.email, subject, body)

        lead_service.record_action(  # type: ignore[call-arg] -- the repository expects a dict, but model_dump returns a dict, so this works out
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.email_sent,
            actor_type=LeadActorType.system,
            direction=LeadActionDirection.outbound,
            body=body,
            notes="Automated welcome",
            metadata={"message_id": message_id, "kind": "welcome"},
        )
        runs.insert(LeadAgentRun(
            foreign_session_uuid=foreign_lead.session_uuid,
            trigger=LeadAgentTrigger.welcome,
            sent_body=body,
            final_action="welcome_sent",
            status="done",
        ).model_dump(mode="json"))

        LOGGER.info("crm_agent.send_welcome: sent session=%s message_id=%s", foreign_lead.session_uuid, message_id)
        return True

    # ── Inbound ingest (Phase A: log only) ────────────────────────────────

    def ingest_inbound(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        inbound: InboundEmail,
    ) -> None:
        """
        Match an inbound message to a lead and log it. Idempotent via Redis fast
        claim + the durable processed_inbound_emails table. Marks the message
        \\Seen only after it has been committed, so a crash leaves it re-fetchable.
        """
        if not inbound.message_id:
            LOGGER.warning("crm_agent.ingest_inbound: message has no Message-ID; marking seen uid=%s", inbound.uid)
            email_service.mark_seen(inbound.uid)
            return

        processed = ProcessedInboundEmailRepository(cyclone_db)

        # Fast path: another worker already claimed it this cycle.
        if not claim_once("inbound:%s" % inbound.message_id):
            return
        # Durable backstop: committed in a prior run (survives a Redis flush).
        if processed.already_processed(inbound.message_id):
            email_service.mark_seen(inbound.uid)
            return

        foreign_lead = ForeignLeadRepository(foreign_db).get_by_email(inbound.from_address)
        if foreign_lead is None:
            LOGGER.warning("crm_agent.ingest_inbound: no lead matches sender; message_id=%s", inbound.message_id)
            processed.insert(ProcessedInboundEmail(message_id=inbound.message_id).model_dump(mode="json"))
            email_service.mark_seen(inbound.uid)
            return

        lead_service.ensure_workflow_row(cyclone_db, foreign_lead)
        lead_service.record_action(  # type: ignore[call-arg] -- the repository expects a dict, but model_dump returns a dict, so this works out
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.email_received,
            actor_type=LeadActorType.system,
            direction=LeadActionDirection.inbound,
            body=inbound.body_text,
            metadata={
                "message_id": inbound.message_id,
                "in_reply_to": inbound.in_reply_to,
                "subject": inbound.subject,
            },
        )
        processed.insert(ProcessedInboundEmail(
            message_id=inbound.message_id,
            foreign_session_uuid=foreign_lead.session_uuid,
        ).model_dump(mode="json"))
        email_service.mark_seen(inbound.uid)
        LOGGER.info("crm_agent.ingest_inbound: logged session=%s message_id=%s", foreign_lead.session_uuid, inbound.message_id)
        # Phase B+ branches here into triage → extract → compose.

    # ── Eligibility ───────────────────────────────────────────────────────

    @staticmethod
    def welcome_cutoff() -> Optional[datetime]:
        """Parse settings.welcome_leads_after into a tz-aware datetime, or None if unset."""
        raw = settings.welcome_leads_after.strip()
        if not raw:
            return None
        try:
            cutoff = datetime.fromisoformat(raw)
            return cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
        except ValueError:
            LOGGER.error("crm_agent: invalid WELCOME_LEADS_AFTER=%r; welcomes disabled", raw)
            return None


crm_agent_service = CrmAgentService()
