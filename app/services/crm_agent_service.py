"""
app/services/crm_agent_service.py - CRM email agent orchestration.

  - send_welcome():   template acknowledgment on a new lead, threaded so replies come back.
  - ingest_inbound(): match an inbound reply to a lead → triage → dispatch.
  - triage():         classify an inbound message as spam | escalate | continue.
  - escalate():       notify lead responders by email + telegram.

Phase B dispatches triage outcomes:
  - spam     → move_to_spam + log
  - escalate → escalate (email + telegram to attorney_lead_responders)
  - continue → also escalate for now (Phase C adds the issue-extract /
               KB-retrieval / compose / guardrail loop with HITL approval).

Everything is driven by the crm_worker poller; nothing here runs in the request path.
"""
import json
import re
from datetime import datetime, timezone
from typing import Optional

from db_handler import DatabaseManager

from db.models.foreign_lead import ForeignLead
from db.models.lead_action import LeadActionDirection, LeadActionType, LeadActorType
from db.models.lead_agent_run import LeadAgentRun, LeadAgentRunInDB, LeadAgentTrigger
from db.models.lead_workflow import DismissalReason, LeadStatus
from db.models.processed_inbound_email import ProcessedInboundEmail
from db.repositories.attorney_lead_responder import AttorneyLeadResponderRepository
from db.repositories.foreign_lead import ForeignLeadRepository
from db.repositories.lead_agent_run import LeadAgentRunRepository
from db.repositories.lead_workflow import LeadWorkflowRepository
from db.repositories.processed_inbound_email import ProcessedInboundEmailRepository
from db.repositories.staff import StaffRepository
from services.email_service import InboundEmail, email_service
from services.lead_service import lead_service
from services.llm_service import llm_service
from services.telegram_service import telegram_service
from util.loggerfactory import LoggerFactory
from util.redis_client import claim_once
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


# ── Triage prompts + parsing ────────────────────────────────────────────────

_TRIAGE_SYSTEM = """\
You are a triage agent for a law firm's prospective-client (PNC) intake channel.
Messages arrive either as a brand-new lead's form submission OR as a reply
to our automated welcome. Classify each into exactly ONE of three categories:

- "spam": Marketing solicitations (SEO services, lead-gen pitches, "I help \
businesses with X"), phishing, mass mail, or any message clearly not from a \
prospective client seeking legal help.

- "escalate": The message demands an attorney's immediate attention. Triggers:
  - Crisis or safety language (threats, abuse, suicidal ideation, immediate danger)
  - A specific legal question or request for legal advice
  - Detailed case facts requiring substantive response
  - Anger, complaints, or threatening tone toward the firm
  - Anything that smells like it might create liability if mishandled by an AI

- "continue": A routine PNC reply with logistical or scheduling content — \
questions about hours, fees, what happens at a consultation, where the firm \
is located, available appointment times, or providing additional intake \
context. The kind of question a knowledgeable intake coordinator can answer.

When in doubt, choose "escalate" — false positives there are safe; missing a \
real legal question is not.

Respond ONLY with a JSON object of this exact shape, nothing else:
{"classification": "spam"|"escalate"|"continue", "reason": "<one short sentence>"}
"""


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json … ``` fences if the LLM wrapped its reply despite being told not to."""
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def _parse_triage(text: str) -> tuple[str, str]:
    """Parse the triage agent's JSON reply. On any parse failure, fail-safe to escalate."""
    try:
        obj = json.loads(_strip_markdown_fences(text))
    except json.JSONDecodeError:
        return ("escalate", "Triage LLM returned non-JSON; defaulted to escalate.")
    cls = (obj.get("classification") or "").strip().lower()
    reason = (obj.get("reason") or "").strip() or "(no reason given)"
    if cls not in ("spam", "escalate", "continue"):
        return ("escalate", "Triage returned unknown classification %r; defaulted to escalate." % cls)
    return cls, reason


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

    # ── New-lead processing (welcome + intake triage) ─────────────────────

    def process_new_lead(
        self,
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
    ) -> bool:
        """
        Process a freshly-arrived lead end-to-end:

        1. Ensure workflow row (slug auto-assigns to the matching attorney).
        2. ALWAYS send the welcome template — a false-positive spam
           classification shouldn't ghost a legitimate PNC, and acknowledging
           a spammer is mild.
        3. Triage the lead's ``conflict_summary`` (their actual first message).
        4. Dispatch:
             - spam     → mark the lead disqualified (status=disqualified,
                          dismissal_reason=spam, dismissal_note=triage reason),
                          log a spam note. No responder ping.
             - escalate → escalate to the lead's responders by email + telegram.
             - continue → welcome alone is enough (Phase C composes here).

        Idempotent via the run ledger: a second call for the same lead is a no-op.

        :return: True if work was done this call, False if skipped.
        """
        if not foreign_lead.email:
            LOGGER.debug("crm_agent.process_new_lead: lead has no email; skipping session=%s", foreign_lead.session_uuid)
            return False

        runs_repo = LeadAgentRunRepository(cyclone_db)
        if runs_repo.welcome_exists(foreign_lead.session_uuid):
            return False

        # Create the workflow row (also fires slug auto-assignment).
        lead_service.ensure_workflow_row(cyclone_db, foreign_lead)

        # 1. Welcome — always, regardless of triage outcome below.
        subject, body = _welcome_message(foreign_lead)
        message_id = email_service.send(foreign_lead.email, subject, body)
        lead_service.record_action(  # type: ignore[call-arg]
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.email_sent,
            actor_type=LeadActorType.system,
            direction=LeadActionDirection.outbound,
            body=body,
            notes="Automated welcome",
            metadata={"message_id": message_id, "kind": "welcome"},
        )

        # Open the run trace; we'll fill in triage_result + final_action as we go.
        run = runs_repo.insert(LeadAgentRun(
            foreign_session_uuid=foreign_lead.session_uuid,
            trigger=LeadAgentTrigger.welcome,
            sent_body=body,
            status="running",
        ).model_dump(mode="json"))

        # 2. Triage the lead-capture submission (the conflict_summary IS their
        #    first message — don't wait for an email reply that may never come).
        summary = (foreign_lead.conflict_summary or "").strip()
        if not summary:
            runs_repo.update(run.id, {
                "triage_result": "continue",
                "final_action": "welcome_sent",
                "status": "done",
            })
            LOGGER.info(
                "crm_agent.process_new_lead: welcomed session=%s (no conflict_summary to triage)",
                foreign_lead.session_uuid,
            )
            return True

        try:
            classification, reason = self.triage(foreign_lead, summary, kind="lead_capture")
        except Exception as e:  # noqa: BLE001 — fail-safe to escalate
            LOGGER.error("crm_agent.process_new_lead: triage failed err=%s", str(e))
            classification = "escalate"
            reason = "Triage error at intake: %s — defaulted to escalate." % str(e)

        LOGGER.info(
            "crm_agent.process_new_lead: triage session=%s classification=%s",
            foreign_lead.session_uuid, classification,
        )
        runs_repo.update(run.id, {"triage_result": classification})

        # 3. Dispatch — no IMAP UID involved here (lead capture, not email).
        if classification == "spam":
            self._mark_lead_spam(cyclone_db, foreign_lead, reason)
            final_action = "spam_at_intake"
        elif classification == "escalate":
            self.escalate(cyclone_db, foreign_lead, summary, "lead_capture", classification, reason)
            final_action = "welcomed_and_escalated"
        else:
            # continue: welcome alone — Phase C will compose a richer reply here.
            final_action = "welcome_sent"

        runs_repo.update(run.id, {"final_action": final_action, "status": "done"})
        LOGGER.info(
            "crm_agent.process_new_lead: complete session=%s final=%s",
            foreign_lead.session_uuid, final_action,
        )
        return True

    @staticmethod
    def _mark_lead_spam(
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
        reason: str,
    ) -> None:
        """
        Auto-disqualify a lead classified as spam at intake. Sets the workflow
        row's status + dismissal fields and logs both a status_change action
        and a note action so the timeline tells the whole story. A human can
        revert the status if it was a false positive — the run row and
        actions document exactly what the agent saw.
        """
        wf_repo = LeadWorkflowRepository(cyclone_db)
        wf = wf_repo.get_by_session_uuid(foreign_lead.session_uuid)
        if wf is None:
            LOGGER.error("crm_agent._mark_lead_spam: no workflow row session=%s", foreign_lead.session_uuid)
            return

        old_status = wf.status.value
        wf_repo.update(wf.id, {
            "status": LeadStatus.disqualified.value,
            "dismissal_reason": DismissalReason.spam.value,
            "dismissal_note": reason,
        })
        lead_service.record_action(  # type: ignore[call-arg]
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.status_change,
            actor_type=LeadActorType.ai_agent,
            notes="Auto-disqualified as spam at intake.",
            metadata={
                "from": old_status,
                "to": LeadStatus.disqualified.value,
                "reason": DismissalReason.spam.value,
                "note": reason,
            },
        )
        lead_service.record_action(  # type: ignore[call-arg]
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.note,
            actor_type=LeadActorType.ai_agent,
            notes="Classified as spam at lead capture. Reason: %s" % reason,
            metadata={"kind": "spam_at_intake", "reason": reason},
        )

    # ── Inbound ingest (Phase B: log + triage + dispatch) ─────────────────

    def ingest_inbound(
        self,
        cyclone_db: DatabaseManager,
        foreign_db: DatabaseManager,
        inbound: InboundEmail,
    ) -> None:
        """
        Process an inbound message end-to-end:
          1. Idempotency (Redis fast claim + durable processed_inbound_emails).
          2. Match the sender to a foreign lead row.
          3. Log the inbound as an ``email_received`` action.
          4. Triage (LLM): spam | escalate | continue.
          5. Dispatch:
             - spam     → move to Spam folder, log a spam-filed action.
             - escalate → notify the lead's responders by email + telegram.
             - continue → (Phase B) also escalate as a graceful fallback;
               Phase C will replace this with extract → KB-retrieve → compose.
          6. Commit dedup + mark_seen (unless moved to Spam, which removes
             the message from INBOX already).

        Failure semantics: triage errors fail-safe to "escalate." Email send
        failures to any one responder are logged but don't stop the others.
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

        # Always record the inbound on the lead's timeline before triaging,
        # so the run table + activity feed reflect what arrived even if triage fails.
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

        runs_repo = LeadAgentRunRepository(cyclone_db)
        run = runs_repo.insert(LeadAgentRun(
            foreign_session_uuid=foreign_lead.session_uuid,
            trigger=LeadAgentTrigger.inbound_reply,
            inbound_message_id=inbound.message_id,
            status="running",
        ).model_dump(mode="json"))

        # ── Triage ────────────────────────────────────────────────────────
        try:
            classification, reason = self.triage(foreign_lead, inbound.body_text or "", kind="email_reply")
        except Exception as e:  # noqa: BLE001 — any LLM/network error fails safe to escalate
            LOGGER.error("crm_agent.ingest_inbound: triage call failed err=%s", str(e))
            classification = "escalate"
            reason = "Triage error: %s — defaulted to escalate." % str(e)

        LOGGER.info(
            "crm_agent.ingest_inbound: triage session=%s classification=%s",
            foreign_lead.session_uuid, classification,
        )
        runs_repo.update(run.id, {"triage_result": classification})

        # ── Dispatch ──────────────────────────────────────────────────────
        if classification == "spam":
            self._handle_spam(cyclone_db, foreign_lead, inbound, reason)
            final_action = "spam_filed"
            # move_to_spam removed the message from INBOX — no mark_seen needed.
        else:
            # escalate OR continue → escalate (Phase B graceful fallback)
            self.escalate(cyclone_db, foreign_lead, inbound.body_text or "", "email_reply", classification, reason)
            email_service.mark_seen(inbound.uid)
            final_action = "escalated"

        runs_repo.update(run.id, {"final_action": final_action, "status": "done"})

        # Commit dedup last — if any of the above raised, the message stays
        # un-committed and (after Redis claim expires) becomes retriable.
        processed.insert(ProcessedInboundEmail(
            message_id=inbound.message_id,
            foreign_session_uuid=foreign_lead.session_uuid,
        ).model_dump(mode="json"))

        LOGGER.info(
            "crm_agent.ingest_inbound: complete session=%s message_id=%s final=%s",
            foreign_lead.session_uuid, inbound.message_id, final_action,
        )

    # ── Triage ────────────────────────────────────────────────────────────

    def triage(
        self,
        foreign_lead: ForeignLead,
        message_text: str,
        kind: str = "email_reply",
    ) -> tuple[str, str]:
        """
        Classify a PNC message. Returns (classification, reason).

        :param message_text: The content to classify (form submission body, or email reply body).
        :param kind: "lead_capture" (their first message via the marketing form)
                     or "email_reply" (their reply to our welcome).

        classification ∈ {"spam", "escalate", "continue"}. On any parse/LLM
        problem this fails safe to "escalate" — false positives are safe;
        false negatives on real legal questions are not.
        """
        user_msg = self._format_triage_input(foreign_lead, message_text, kind)
        response = llm_service.complete_fast(_TRIAGE_SYSTEM, user_msg)
        return _parse_triage(response)

    @staticmethod
    def _format_triage_input(foreign_lead: ForeignLead, message_text: str, kind: str) -> str:
        if kind == "lead_capture":
            return (
                "PROSPECTIVE CLIENT — new submission from the marketing intake form\n"
                "Name: %s\n\n"
                "Their submission:\n"
                "----- begin -----\n%s\n----- end -----"
            ) % (
                foreign_lead.full_name or "(unknown)",
                message_text or "(empty submission)",
            )
        # email_reply: lead has prior intake context + a new reply
        return (
            "PROSPECTIVE CLIENT — replying to our automated welcome\n"
            "Name: %s\n"
            "Their initial intake summary:\n%s\n\n"
            "Their reply:\n"
            "----- begin -----\n%s\n----- end -----"
        ) % (
            foreign_lead.full_name or "(unknown)",
            foreign_lead.conflict_summary or "(none)",
            message_text or "(empty body)",
        )

    # ── Dispatch: spam ────────────────────────────────────────────────────

    def _handle_spam(
        self,
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
        inbound: InboundEmail,
        reason: str,
    ) -> None:
        """Move the message to the Spam folder and record a system action on the lead."""
        try:
            email_service.move_to_spam(inbound.uid)
        except Exception as e:  # noqa: BLE001
            LOGGER.error("crm_agent._handle_spam: move_to_spam failed uid=%s err=%s", inbound.uid, str(e))
            # If we can't move to spam, fall back to mark_seen so we don't reprocess.
            try:
                email_service.mark_seen(inbound.uid)
            except Exception:  # noqa: BLE001
                pass
        lead_service.record_action(  # type: ignore[call-arg]
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.note,
            actor_type=LeadActorType.ai_agent,
            notes="Classified as spam, moved to Spam folder. Reason: %s" % reason,
            metadata={
                "kind": "spam_filed",
                "reason": reason,
                "message_id": inbound.message_id,
            },
        )

    # ── Dispatch: escalate ────────────────────────────────────────────────

    def escalate(
        self,
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
        message_text: str,
        message_kind: str,
        classification: str,
        reason: str,
    ) -> None:
        """
        Notify the responders for the lead's responding attorney by email and
        (where configured) Telegram. Logs the escalation action with metadata
        identifying which responders were notified.

        :param message_text: The PNC content that triggered escalation —
            the lead-capture submission OR the email reply body.
        :param message_kind: "lead_capture" or "email_reply"; controls the
            framing of the escalation email body.

        Routing:
          - If the workflow row has an assigned_staff_id → that's the attorney.
          - Else fall back to the staff whose slug matches lead.attorney_slug
            (which resolves to the 'www' Firm record for unattributed leads).
        """
        attorney_staff_id = self._resolve_responding_attorney(cyclone_db, foreign_lead)
        if attorney_staff_id is None:
            LOGGER.error(
                "crm_agent.escalate: no responding attorney resolved session=%s slug=%s",
                foreign_lead.session_uuid, foreign_lead.attorney_slug,
            )
            lead_service.record_action(  # type: ignore[call-arg]
                cyclone_db,
                session_uuid=foreign_lead.session_uuid,
                action_type=LeadActionType.agent_escalated,
                actor_type=LeadActorType.ai_agent,
                notes="Escalation failed: no responding attorney could be resolved for this lead.",
                metadata={"reason": reason, "classification": classification, "error": "no_attorney"},
            )
            return

        responder_rows = AttorneyLeadResponderRepository(cyclone_db).get_by_attorney(attorney_staff_id)
        if not responder_rows:
            LOGGER.error(
                "crm_agent.escalate: no responders configured for attorney_staff_id=%s session=%s",
                attorney_staff_id, foreign_lead.session_uuid,
            )
            lead_service.record_action(  # type: ignore[call-arg]
                cyclone_db,
                session_uuid=foreign_lead.session_uuid,
                action_type=LeadActionType.agent_escalated,
                actor_type=LeadActorType.ai_agent,
                notes="Escalation failed: no responders configured for the responding attorney.",
                metadata={
                    "reason": reason,
                    "classification": classification,
                    "responding_attorney_staff_id": attorney_staff_id,
                    "error": "no_responders",
                },
            )
            return

        staff_repo = StaffRepository(cyclone_db)
        responder_ids = [r.responder_staff_id for r in responder_rows]
        responders, _ = staff_repo.select_many(condition={"id": responder_ids})

        subject = "Lead escalation: %s" % (foreign_lead.full_name or "(unnamed lead)")
        email_body = self._format_escalation_email(foreign_lead, message_text, message_kind, classification, reason)
        telegram_body = (
            "Lead escalation: %s\n"
            "Why: %s\n"
            "View: %s/app/leads/%s"
        ) % (
            foreign_lead.full_name or "(unnamed lead)",
            reason,
            settings.host_url.rstrip("/"),
            foreign_lead.session_uuid,
        )

        emailed: list[int] = []
        telegrammed: list[int] = []
        for responder in responders:
            try:
                email_service.send(responder.email, subject, email_body)
                emailed.append(responder.id)
            except Exception as e:  # noqa: BLE001 — one bad responder must not stop the others
                LOGGER.warning("crm_agent.escalate: email failed responder=%s err=%s", responder.id, str(e))
            if responder.telegram_id:
                if telegram_service.send(responder.telegram_id, telegram_body):
                    telegrammed.append(responder.id)

        lead_service.record_action(  # type: ignore[call-arg]
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.agent_escalated,
            actor_type=LeadActorType.ai_agent,
            notes="Escalated to %s responder(s): %s" % (len(responders), reason),
            metadata={
                "reason": reason,
                "classification": classification,
                "responding_attorney_staff_id": attorney_staff_id,
                "responder_staff_ids": responder_ids,
                "emailed_staff_ids": emailed,
                "telegrammed_staff_ids": telegrammed,
            },
        )

    @staticmethod
    def _resolve_responding_attorney(
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
    ) -> Optional[int]:
        """assigned_staff_id if the workflow row is assigned, else staff matching the lead's slug."""
        wf = LeadWorkflowRepository(cyclone_db).get_by_session_uuid(foreign_lead.session_uuid)
        if wf is not None and wf.assigned_staff_id is not None:
            return wf.assigned_staff_id
        if foreign_lead.attorney_slug:
            staff = StaffRepository(cyclone_db).get_by_slug(foreign_lead.attorney_slug)
            if staff is not None:
                return staff.id
        return None

    @staticmethod
    def _format_escalation_email(
        foreign_lead: ForeignLead,
        message_text: str,
        message_kind: str,
        classification: str,
        reason: str,
    ) -> str:
        if message_kind == "lead_capture":
            headline = "A new lead needs your attention."
            message_label = "Their intake submission:"
            # On lead capture, the message IS the intake summary — skip the redundant
            # "Initial intake summary" footer.
            intake_block = ""
        else:
            headline = "A lead reply needs your attention."
            message_label = "Their reply:"
            intake_block = "Initial intake summary:\n%s\n\n" % (foreign_lead.conflict_summary or "(none)")

        return (
            "%s\n\n"
            "Lead:     %s\n"
            "Email:    %s\n"
            "Phone:    %s\n"
            "Slug:     %s\n\n"
            "Triage:   %s\n"
            "Reason:   %s\n\n"
            "%s\n"
            "===== begin =====\n"
            "%s\n"
            "===== end =====\n\n"
            "%s"
            "View in Cyclone: %s/app/leads/%s\n\n"
            "—\n"
            "Sent automatically by the Cyclone CRM agent."
        ) % (
            headline,
            foreign_lead.full_name or "(unnamed)",
            foreign_lead.email or "(no email)",
            foreign_lead.telephone or "(no phone)",
            foreign_lead.attorney_slug or "(none)",
            classification,
            reason,
            message_label,
            message_text or "(empty)",
            intake_block,
            settings.host_url.rstrip("/"),
            foreign_lead.session_uuid,
        )

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
