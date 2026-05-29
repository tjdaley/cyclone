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
from typing import Any, Optional

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
from services.kb_retrieval_service import KbRetrievalResult, kb_retrieval_service
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
to our automated welcome. Classify each into exactly ONE of three categories.

IMPORTANT: a downstream pipeline handles MIXED messages safely by answering
only what can be answered from the firm's knowledge base and escalating
substantive legal questions to a human attorney. Your job is to filter out
messages that should not be touched by an AI at all — NOT to escalate every
message that contains any legal content.

- "spam": Marketing solicitations (SEO services, lead-gen pitches, "I help \
businesses with X"), phishing, mass mail, or any message clearly not from a \
prospective client seeking legal help.

- "escalate": The ENTIRE message must go straight to a human attorney with
  no AI touch. Trigger ONLY when at least one of these applies:
  - Crisis or safety language (threats, abuse, suicidal ideation, immediate danger)
  - Anger, hostility, or threats toward the firm itself
  - The message is ONLY substantive legal questions / case-specific facts
    with no logistical content the AI could safely address
  - Anything that, even when handled per-issue, an AI shouldn't be allowed
    to engage with at all

- "continue": Any other PNC message. INCLUDES mixed messages that contain
  BOTH logistical/scheduling content (hours, fees, location, consultation,
  practice areas, scheduling) AND substantive legal questions. The downstream
  compose pipeline answers the logistical part from the KB and escalates the
  substantive legal questions to the attorney separately. Trust the pipeline.

When in doubt between "escalate" and "continue", choose "continue". The
compose pipeline is conservative (it refuses to answer anything outside the
KB) and every draft is reviewed by a human before sending. False-escalating
a mixed message means losing the opportunity to answer the safe parts
quickly.

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


# ── Compose pipeline (Phase C.2) ─────────────────────────────────────────────

_EXTRACT_SYSTEM = """\
You read a prospective client's (PNC) message to a law firm and list the
DISTINCT questions or points they're raising. Return short, declarative
items — one per question or topic. Don't paraphrase substantively; capture
what they're actually asking about.

Cap at 6 items. If the message contains no clear question (just a greeting
or "thanks"), return an empty array.

Respond ONLY with a JSON array of strings, no markdown.
"""


_COMPOSE_SYSTEM = """\
You are an AI assistant drafting a reply on behalf of a law firm's intake team
to a prospective client (PNC). The PNC has asked questions that can be at
least partially answered from the firm's knowledge base, which is provided
to you as KB FRAGMENTS.

Tone: friendly, professional, brief. Match the register of an intake
coordinator. Don't be salesy. Don't make legal claims.

Hard rules:
- Use ONLY the KB fragments provided as factual grounding. Don't invent facts.
- If the message includes substantive legal questions you weren't given KB
  for, acknowledge that those will be passed to an attorney — don't try to
  answer them yourself.
- Don't promise specific outcomes, timelines, or fee amounts unless the KB
  explicitly contains that information.
- Don't recommend a specific legal course of action; offer to schedule a
  consultation.
- If a Calendly link is provided in the context, weave it in naturally for
  scheduling. If not, just mention that staff will reach out.
- Sign off naturally — DO NOT include placeholder text like "[Your Name]"
  or "[Attorney Signature]". The system will append a standard caveat after
  your message; you don't need to add one yourself.
- Plain text, no Markdown headings or bullet syntax that won't render in email.

Respond ONLY with the body of the email reply. No subject line. No quoting.
"""


_GUARDRAIL_SYSTEM = """\
You review an AI-drafted email reply from a law firm's intake team to a
prospective client (PNC). Flag the draft if ANY of these rules are violated.

Rules:
1. NO legal advice. The draft must not analyze the PNC's specific facts or
   suggest a specific legal course of action.
2. NO outcome guarantees ("you'll win", "we can get you primary custody",
   "this is an easy case", "very likely", etc.).
3. NO specific predictions about timing, fees, or outcomes that aren't
   explicitly grounded in factual content the composer was given.
4. NO unprofessional or hostile tone.
5. NO language creating an attorney-client relationship ("As your attorney…",
   "We represent you…", "Our advice is…").
6. NO statements about specific case strategy.
7. NO insensitive handling of crisis content (abuse, threats, suicidal
   ideation) — those must escalate to a human, never receive an auto-reply.

Respond ONLY with a JSON object, no markdown:
{
  "passed": <true if ALL rules are satisfied; false if ANY is violated>,
  "reason": "<one short sentence; if failed, name the rule and quote the offending text>"
}
"""


_AI_CAVEAT = (
    "\n\n—\n"
    "This message was generated by an AI assistant on behalf of the firm "
    "(cyclone.jdbot.us). It is an automated reply and not legal advice."
)


# Warmer welcome — Phase C.3 increment ① ────────────────────────────────────
# The welcome is the firm's first words to the PNC. A template is bland; a
# topic-aware LLM acknowledgment makes them feel heard immediately. The
# substantive answer still goes through HITL — this is just the door-opener.
# Hard-limited to acknowledgment only: no legal info, no scheduling specifics,
# no calendly link (that comes from the substantive draft).
_WELCOME_SYSTEM = """\
You write a brief, warm acknowledgment email for a law firm to send IMMEDIATELY
to a prospective client (PNC) who just submitted the intake form on the
firm's website. This is the firm's first contact — the substantive reply
will follow later.

Goal: make the PNC feel heard. Reference the gist of their topic in ONE
phrase so they know the message was read. Do NOT answer any of their
questions. Do NOT provide any legal information. Do NOT promise specific
timelines, fees, or outcomes.

Hard rules:
- Maximum 3 short paragraphs.
- Use a natural greeting: if the name has a courtesy title (Mr./Mrs./Dr.),
  use "Dear <title> <last name>"; otherwise "Dear <first name>"; if no
  usable name, "Hello".
- Acknowledge the topic of their inquiry in ONE phrase
  ("...about your divorce question", "...regarding adoption", etc.).
  Do NOT quote or paraphrase their full message. If the message is
  emotionally heavy or describes a crisis, acknowledge that they're
  reaching out without engaging with specifics.
- Reassure that a member of the team will be in touch shortly.
- Sign off with the firm name.
- Plain text only. No links. No bullet lists. No headings.

Respond with the EMAIL BODY ONLY. No subject line. No preamble. No quote marks.
"""


_WELCOME_FOOTER = (
    "\n\nThis is an automated acknowledgment. You can reply to this email "
    "with any additional details and your message will reach our intake team."
)


def _llm_compose_welcome(foreign_lead: ForeignLead, firm: str) -> Optional[str]:
    """Try to compose a warm topic-aware welcome via LLM. Returns None on failure
    so the caller can fall back to the template."""
    user_msg = (
        "PROSPECTIVE CLIENT\n"
        "Name: %s\n"
        "Topic of their inquiry (from the intake form):\n"
        "----- begin -----\n%s\n----- end -----\n\n"
        "Firm name: %s\n\n"
        "Write the acknowledgment email body now."
    ) % (
        foreign_lead.full_name or "(unknown)",
        foreign_lead.conflict_summary or "(none — the form was submitted without a message)",
        firm,
    )
    try:
        body = llm_service.complete_fast(_WELCOME_SYSTEM, user_msg).strip()
    except Exception as e:  # noqa: BLE001 — fail-safe to template
        LOGGER.warning("warm welcome LLM call failed: %s", str(e))
        return None
    # Sanity bounds: reject suspiciously short/long output.
    if len(body) < 50 or len(body) > 1500:
        LOGGER.warning("warm welcome rejected by length check: len=%s", len(body))
        return None
    return body


def _template_welcome_body(foreign_lead: ForeignLead, firm: str) -> str:
    """Old static template. Used as fallback when the warm welcome LLM call fails."""
    return (
        "%s,\n\n"
        "Thank you for reaching out to %s. We've received your message and a member "
        "of our team will be in touch with you shortly.\n\n"
        "If your matter is urgent, please call our office.\n\n"
        "— %s"
    ) % (_greeting(foreign_lead.full_name), firm, firm)


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
    """Build the (subject, body) for the automated welcome.

    Tries an LLM-composed topic-aware acknowledgment first; falls back to the
    static template if the LLM call fails or returns suspect content. The
    standard "this is automated, you can reply" footer is always appended.
    """
    firm = settings.firm_name
    subject = "Thank you for contacting %s" % firm
    body = _llm_compose_welcome(foreign_lead, firm) or _template_welcome_body(foreign_lead, firm)
    return subject, body + _WELCOME_FOOTER


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
        # SMTP failure here is logged as a permanent failure note on the lead
        # timeline (visible in the UI), and we continue with triage so the
        # agent's intent is still captured even if the PNC never received the
        # welcome. The substantive draft (if composed) will have no parent
        # message to thread to, but that's a cosmetic loss.
        subject, body = _welcome_message(foreign_lead)
        message_id: Optional[str] = None
        try:
            message_id = email_service.send(foreign_lead.email, subject, body)
        except Exception as e:  # noqa: BLE001
            LOGGER.error(
                "crm_agent.process_new_lead: welcome SMTP failed session=%s err=%s",
                foreign_lead.session_uuid, str(e),
            )
            self._log_email_failure(cyclone_db, foreign_lead, "welcome", e)

        if message_id is not None:
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
        # ``sent_body`` is reserved for the agent's composed reply once a human
        # approves and Send fires (Phase C.3) — NOT for the welcome template,
        # which has its own lead_action row.
        run = runs_repo.insert(LeadAgentRun(
            foreign_session_uuid=foreign_lead.session_uuid,
            trigger=LeadAgentTrigger.welcome,
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
            # continue: try to compose a draft from the KB (HITL).
            # Parent message for threading = the welcome we just sent.
            final_action = self._handle_continue(
                cyclone_db, foreign_lead, summary, "lead_capture",
                reason, run.id, parent_message_id=message_id,
            )

        # Drafted outcomes stay in 'awaiting_approval' so C.3's Send button can
        # find them. Everything else (spam, escalate, no-summary continue) is done.
        run_updates: dict[str, object] = {"final_action": final_action}
        if final_action not in ("drafted_pending_approval", "drafted_and_escalated"):
            run_updates["status"] = "done"
        runs_repo.update(run.id, run_updates)
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
        elif classification == "continue":
            # Phase C: try to compose a draft answer (HITL). Falls back to escalate.
            final_action = self._handle_continue(
                cyclone_db, foreign_lead, inbound.body_text or "", "email_reply",
                reason, run.id, parent_message_id=inbound.message_id,
            )
            email_service.mark_seen(inbound.uid)
        else:
            # escalate
            self.escalate(cyclone_db, foreign_lead, inbound.body_text or "", "email_reply", classification, reason)
            email_service.mark_seen(inbound.uid)
            final_action = "escalated"

        run_updates: dict[str, object] = {"final_action": final_action}
        if final_action not in ("drafted_pending_approval", "drafted_and_escalated"):
            run_updates["status"] = "done"
        runs_repo.update(run.id, run_updates)

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

    # ── Compose pipeline (Phase C.2) ──────────────────────────────────────

    def extract_issues(
        self,
        foreign_lead: ForeignLead,
        message_text: str,
        message_kind: str,
    ) -> list[str]:
        """
        Pull the distinct questions/points from a PNC message. Capped at 6.
        Non-fatal: returns [] on any LLM/parse failure (compose falls back to
        treating the whole message as a single issue).
        """
        label = "their intake submission" if message_kind == "lead_capture" else "their reply to our welcome"
        user_msg = (
            "PROSPECTIVE CLIENT: %s\n"
            "Message context: %s\n\n"
            "Message:\n"
            "----- begin -----\n%s\n----- end -----"
        ) % (
            foreign_lead.full_name or "(unknown)",
            label,
            message_text or "(empty)",
        )
        try:
            response = llm_service.complete_fast(_EXTRACT_SYSTEM, user_msg)
            items: Any = json.loads(_strip_markdown_fences(response))
            if not isinstance(items, list):
                return []
            return [str(i).strip() for i in items if str(i).strip()][:6]
        except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001 — non-fatal
            LOGGER.warning("crm_agent.extract_issues: failed err=%s", str(e))
            return []

    def compose_reply(
        self,
        foreign_lead: ForeignLead,
        message_text: str,
        message_kind: str,
        retrieval: KbRetrievalResult,
        calendly_url: Optional[str],
    ) -> str:
        """Draft a reply grounded in the KB fragments. Uses the primary LLM
        (not _fast) since this is the most quality-sensitive step."""
        fragments_block = (
            "\n\n".join(retrieval.fragments)
            if retrieval.fragments else "(no specific KB fragments returned)"
        )
        unanswerable_block = (
            "\n".join("- %s" % i for i in retrieval.unanswerable_issues)
            if retrieval.unanswerable_issues else "(none)"
        )
        label = "Their intake submission" if message_kind == "lead_capture" else "Their reply to our welcome"
        calendly_block = (
            "If appropriate, offer to schedule a consultation via: %s" % calendly_url
            if calendly_url else "No Calendly link available — say staff will follow up to schedule."
        )

        user_msg = (
            "PROSPECTIVE CLIENT: %s\n\n"
            "%s:\n"
            "----- begin -----\n%s\n----- end -----\n\n"
            "KB FRAGMENTS (use these as your only factual grounding):\n"
            "%s\n\n"
            "ISSUES WE CANNOT ANSWER FROM KB — acknowledge these will go to an attorney:\n"
            "%s\n\n"
            "%s"
        ) % (
            foreign_lead.full_name or "(unknown)",
            label,
            message_text or "(empty)",
            fragments_block,
            unanswerable_block,
            calendly_block,
        )
        return llm_service.complete(_COMPOSE_SYSTEM, user_msg).strip()

    def guardrail(self, draft: str, original_message: str) -> tuple[bool, str]:
        """Safety check on a drafted reply. Returns (passed, reason).
        Any parse/LLM error returns passed=False (fail-safe)."""
        user_msg = (
            "ORIGINAL MESSAGE FROM PNC:\n"
            "----- begin -----\n%s\n----- end -----\n\n"
            "DRAFTED REPLY:\n"
            "----- begin -----\n%s\n----- end -----"
        ) % (original_message or "(empty)", draft or "(empty)")

        try:
            response = llm_service.complete_fast(_GUARDRAIL_SYSTEM, user_msg)
            obj = json.loads(_strip_markdown_fences(response))
        except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            LOGGER.warning("crm_agent.guardrail: parse/LLM error err=%s", str(e))
            return False, "Guardrail check could not be evaluated: %s" % str(e)
        passed = bool(obj.get("passed"))
        reason = (str(obj.get("reason") or "").strip() or "(no reason given)")
        return passed, reason

    # ── Continue dispatch ─────────────────────────────────────────────────

    def _handle_continue(
        self,
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
        message_text: str,
        message_kind: str,
        triage_reason: str,
        run_id: int,
        parent_message_id: Optional[str] = None,
    ) -> str:
        """
        Phase C continue path: extract → retrieve → compose → guardrail →
        save draft as ``draft_pending`` action + flip the run row to
        ``awaiting_approval``. Falls back to escalation if any step fails
        (retrieval unanswerable, compose error, guardrail rejection).

        :return: final_action string — 'drafted_pending_approval' or 'escalated'.
        """
        runs_repo = LeadAgentRunRepository(cyclone_db)

        # 1. Extract issues (non-fatal — empty list is fine)
        issues = self.extract_issues(foreign_lead, message_text, message_kind)

        # 2. Retrieve KB context
        retrieval = kb_retrieval_service.retrieve_context(cyclone_db, message_text, issues)

        # 3. Unanswerable → escalate as fallback
        if not retrieval.answerable:
            LOGGER.info(
                "crm_agent._handle_continue: KB cannot answer; escalating session=%s",
                foreign_lead.session_uuid,
            )
            runs_repo.update(run_id, {
                "issues": issues,
                "dispositions": [{"issue": i, "disposition": "escalated"} for i in retrieval.unanswerable_issues],
            })
            self.escalate(
                cyclone_db, foreign_lead, message_text, message_kind,
                "continue",
                "KB has no answer for this message: %s" % (retrieval.notes or "no relevant articles found"),
            )
            return "escalated"

        # 4. Compose draft
        calendly_url = self._resolve_calendly_url(cyclone_db, foreign_lead)
        try:
            draft = self.compose_reply(foreign_lead, message_text, message_kind, retrieval, calendly_url)
        except Exception as e:  # noqa: BLE001 — never let a compose error drop the message
            LOGGER.error("crm_agent._handle_continue: compose failed err=%s", str(e))
            runs_repo.update(run_id, {"issues": issues})
            self.escalate(
                cyclone_db, foreign_lead, message_text, message_kind,
                "continue",
                "Compose error: %s" % str(e),
            )
            return "escalated"

        # 5. Guardrail
        passed, guardrail_reason = self.guardrail(draft, message_text)
        if not passed:
            LOGGER.warning(
                "crm_agent._handle_continue: GUARDRAIL FAILED session=%s reason=%s",
                foreign_lead.session_uuid, guardrail_reason,
            )
            # Save the rejected draft + reason on the run so a human can audit what was flagged.
            runs_repo.update(run_id, {
                "issues": issues,
                "draft_body": draft,
                "guardrail_passed": False,
            })
            self.escalate(
                cyclone_db, foreign_lead, message_text, message_kind,
                "continue",
                "Guardrail rejected the AI draft: %s. The rejected draft is on the run row for review." % guardrail_reason,
            )
            return "escalated"

        # 6. Caveat + save as draft_pending (HITL approval)
        final_draft = draft + _AI_CAVEAT
        lead_service.record_action(  # type: ignore[call-arg]
            cyclone_db,
            session_uuid=foreign_lead.session_uuid,
            action_type=LeadActionType.draft_pending,
            actor_type=LeadActorType.ai_agent,
            direction=LeadActionDirection.internal,
            body=final_draft,
            notes="Draft awaiting human approval. Triage: continue. Reason: %s" % triage_reason,
            metadata={
                "kind": "compose",
                "run_id": run_id,
                "parent_message_id": parent_message_id,
                "extracted_issues": issues,
                "retrieval_notes": retrieval.notes,
                "unanswerable_issues": retrieval.unanswerable_issues,
            },
        )

        # Per-issue dispositions for the run trace
        answerable_issues = [i for i in issues if i not in retrieval.unanswerable_issues]
        dispositions: list[dict[str, str]] = (
            [{"issue": i, "disposition": "answered_in_draft"} for i in answerable_issues]
            + [{"issue": i, "disposition": "escalated_to_human"} for i in retrieval.unanswerable_issues]
        )
        runs_repo.update(run_id, {
            "issues": issues,
            "draft_body": final_draft,
            "guardrail_passed": True,
            "dispositions": dispositions,
            "status": "awaiting_approval",
        })

        # 7. Per-issue dispatch: if the retrieval flagged any issues outside the
        # KB, ALSO escalate just those to the lead's responders. The PNC sees
        # the draft (which acknowledges those items will go to the team); the
        # responders get a focused notification with the specific items.
        if retrieval.unanswerable_issues:
            escalation_body = (
                "%s\n\n"
                "----- ITEMS NEEDING ATTORNEY RESPONSE -----\n%s\n\n"
                "A partial AI draft addressing the lead's logistical questions has been "
                "queued for human approval. This escalation is about the items above, "
                "which the AI declined to answer."
            ) % (
                message_text,
                "\n".join("- %s" % i for i in retrieval.unanswerable_issues),
            )
            self.escalate(
                cyclone_db, foreign_lead, escalation_body, message_kind,
                "continue",
                "Partial draft queued; attorney attention needed on: %s"
                % "; ".join(retrieval.unanswerable_issues),
            )
            LOGGER.info(
                "crm_agent._handle_continue: drafted + escalated session=%s run_id=%s items=%d",
                foreign_lead.session_uuid, run_id, len(retrieval.unanswerable_issues),
            )
            return "drafted_and_escalated"

        # Pure-answerable: no escalation will fire, so we explicitly notify
        # responders that a draft is sitting for approval. (The mixed-case
        # escalation above already mentions the draft, so we skip there to
        # avoid double-notifying.)
        self._notify_responders_of_draft(cyclone_db, foreign_lead)
        LOGGER.info(
            "crm_agent._handle_continue: draft saved session=%s run_id=%s",
            foreign_lead.session_uuid, run_id,
        )
        return "drafted_pending_approval"

    def _notify_responders_of_draft(
        self,
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
    ) -> None:
        """Email + telegram the lead's responders that an AI draft is awaiting
        their approval. Same routing as escalation (assigned attorney → their
        responders, else slug-resolved Firm record). Silently no-ops if no
        responders are configured."""
        attorney_staff_id = self._resolve_responding_attorney(cyclone_db, foreign_lead)
        if attorney_staff_id is None:
            return
        responder_rows = AttorneyLeadResponderRepository(cyclone_db).get_by_attorney(attorney_staff_id)
        if not responder_rows:
            return

        responder_ids = [r.responder_staff_id for r in responder_rows]
        responders, _ = StaffRepository(cyclone_db).select_many(condition={"id": responder_ids})
        lead_url = "%s/app/leads/%s" % (settings.host_url.rstrip("/"), foreign_lead.session_uuid)

        subject = "Draft awaiting your approval: %s" % (foreign_lead.full_name or "(unnamed lead)")
        email_body = (
            "An AI-drafted reply for %s is queued for your review.\n\n"
            "Open the lead to view, edit, or send the draft:\n%s\n\n"
            "—\n"
            "Sent automatically by the Cyclone CRM agent."
        ) % (foreign_lead.full_name or "(unnamed)", lead_url)
        telegram_body = (
            "Draft awaiting approval: %s\nView: %s"
        ) % (foreign_lead.full_name or "(unnamed)", lead_url)

        for responder in responders:
            try:
                email_service.send(responder.email, subject, email_body)
            except Exception as e:  # noqa: BLE001 — one bad responder must not stop the others
                LOGGER.warning(
                    "crm_agent._notify_responders_of_draft: email failed responder=%s err=%s",
                    responder.id, str(e),
                )
            if responder.telegram_id:
                telegram_service.send(responder.telegram_id, telegram_body)

    @staticmethod
    def _log_email_failure(
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
        stage: str,
        error: Exception,
        *,
        staff_id: Optional[int] = None,
        actor_type: LeadActorType = LeadActorType.system,
        extra_metadata: Optional[dict[str, object]] = None,
    ) -> None:
        """
        Write a permanent record of an SMTP failure to the lead's timeline.
        Surfaces email-transmission errors that would otherwise only live in
        the worker logs (welcome failures) or vanish after the 502 (C.3 send).

        ``stage`` distinguishes which channel failed: 'welcome', 'draft_send',
        'escalation', etc. The UI renders any note with
        metadata.kind='email_send_failed' as a red-tinted card.
        """
        meta: dict[str, object] = {
            "kind": "email_send_failed",
            "stage": stage,
            "error": str(error),
        }
        if extra_metadata:
            meta.update(extra_metadata)
        try:
            lead_service.record_action(  # type: ignore[call-arg]
                cyclone_db,
                session_uuid=foreign_lead.session_uuid,
                action_type=LeadActionType.note,
                actor_type=actor_type,
                direction=LeadActionDirection.internal,
                staff_id=staff_id,
                notes="Email send failed (%s): %s" % (stage, str(error)),
                metadata=meta,
            )
        except Exception as log_err:  # noqa: BLE001 — failure logging must never crash
            LOGGER.error(
                "crm_agent._log_email_failure: could not log failure note err=%s",
                str(log_err),
            )

    @staticmethod
    def _resolve_calendly_url(
        cyclone_db: DatabaseManager,
        foreign_lead: ForeignLead,
    ) -> Optional[str]:
        """Find the Calendly URL for the lead's assigned (or slug-resolved) attorney."""
        attorney_id = CrmAgentService._resolve_responding_attorney(cyclone_db, foreign_lead)
        if attorney_id is None:
            return None
        staff = StaffRepository(cyclone_db).select_one(condition={"id": attorney_id})
        if staff is None:
            return None
        return staff.calendly_url

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
