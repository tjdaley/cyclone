"""
app/services/intake_service.py - Create a client and matter from a filed pleading.

The ordinary pleading pipeline (services/pleading_service.py) needs to know who
our client is before it can say which attorney is "opposing". At intake we do
not know that yet — the pleading is the first thing we have. So extraction here
asks a neutral question: who are ALL the parties, and which attorney appears for
each? The attorney then names our side once, in the review step, and everything
adverse is derived from that single answer.

Commit is deliberately a thin orchestration: create the client (or reuse a
matched one), create the matter, then hand off to
``pleading_service.commit_ingest`` so parties, counsel, children, and claims are
written by the same code that handles every other pleading.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Optional

from db.models.client import Client
from db.models.matter import Matter
from db.repositories.client import ClientRepository
from db.repositories.matter import MatterRepository
from db_handler import DatabaseManager
from schemas.intake import (
    IntakeAttorneyPreview,
    IntakeCasePreview,
    IntakeClientMatch,
    IntakeLeadMatch,
    IntakePartyPreview,
    MatterIntakeCommitRequest,
    MatterIntakeCommitResponse,
    MatterIntakePreviewResponse,
)
from schemas.pleading import (
    ChildPreview,
    ClaimPreview,
    OCCommitEntry,
    OpposingPartyCommitEntry,
    PleadingCommitRequest,
)
from services.llm_service import llm_service
from services.pleading_service import clip_for_metadata, pleading_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# How many recent leads to scan when matching a party to a lead. Matching is
# done in Python because the pleading gives a name written the court's way, not
# a value any index can be queried by.
_LEAD_MATCH_SCAN = 500


_CASE_STYLE_SYSTEM = """\
You are a legal expert reading a pleading filed in a Texas family law case.

This document is being used to OPEN A NEW FILE, so you do not know which side
the reader represents. Do not guess, and never label anyone "opposing".
Report every party and every attorney neutrally.

Return ONLY a valid JSON object with these fields:

- title: the title of the pleading, e.g. "Original Petition for Divorce"
- filed_date: "YYYY-MM-DD" or null (from the court clerk's file stamp)
- served_date: "YYYY-MM-DD" or null (from the certificate of service)
- state: string or null (e.g. "Texas")
- county: string or null (e.g. "Parker")
- court_name: string or null (e.g. "415th Judicial District Court")
- matter_number: string or null (the cause number)
- matter_type: one of "divorce" | "child_custody" | "modification" | "enforcement" | \
  "cps" | "probate" | "estate_planning" | "civil" | "other"
- discovery_level: "level_1" | "level_2" | "level_3" or null
- parties: array of every ADULT party to the suit. Children are NOT parties; list \
  them under "children" instead. Each object has:
  - full_name: string, exactly as the document writes it
  - designation: string or null — the party's role in the caption, e.g. "petitioner", \
    "respondent", "counterpetitioner", "counterrespondent", "intervenor"
- children: array of objects (empty if none):
  - name: {first_name, last_name, middle_name, courtesy_title, suffix}
  - date_of_birth: "YYYY-MM-DD" or null
  - sex: "male" | "female" | "other" or null
- attorneys: array of every attorney of record named anywhere in the document — \
  the signature block, the certificate of service, and any "service may be had by \
  serving X" paragraph. Each object has:
  - represents: string or null — the full_name of the party this attorney appears for
  - name: {first_name, last_name, middle_name, courtesy_title, suffix}
  - firm_name, street_address, street_address_2, city, state, postal_code: string or null
  - email, cell_phone, telephone, fax: string or null
  - bar_state: string or null (e.g. "TX")
  - bar_number: string or null

Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


def _name_tokens(value: str) -> list[str]:
    """Lowercase alphabetic words of a name, in order."""
    return re.findall(r"[a-z]+", (value or "").lower())


def _match_confidence(party_name: str, other_name: str) -> Optional[str]:
    """
    Compare a name from a pleading to a client or lead we already hold.

    Both the first and last name must agree. Surname-only matching is
    deliberately not enough: in a family law caption the two adverse parties
    almost always share a surname, so it would offer the opposing spouse's
    record as a candidate for our own client — the one mistake this matching
    must never make.

    :return: 'strong' when the shorter name's words all appear in the longer
        one ("Kaci Salmons" vs "Kaci Lynndon Salmons"), 'partial' when the
        first and last agree but something in between does not
        ("Kaci Marie Salmons"), or None when they are not the same person.
    :rtype: Optional[str]
    """
    a, b = _name_tokens(party_name), _name_tokens(other_name)
    if not a or not b:
        return None
    if a[0] != b[0] or a[-1] != b[-1]:
        return None
    shorter, longer = sorted((set(a), set(b)), key=len)
    return "strong" if shorter <= longer else "partial"


class IntakeService:
    """Opens a client and matter from a filed pleading."""

    # ── Extraction ────────────────────────────────────────────────────────

    def extract_case_style(self, raw_text: str) -> dict[str, Any]:
        """
        Pull the case style, parties, children, and attorneys from a pleading.

        Unlike ``pleading_service.classify_and_extract`` this takes no client
        context — at intake there is no client yet.

        :param raw_text: Full extracted text of the pleading.
        :type raw_text: str
        :return: Parsed metadata dict.
        :rtype: dict[str, Any]
        :raises ValueError: If the LLM response is not valid JSON.
        """
        prompt_text = clip_for_metadata(raw_text)
        response = llm_service.complete(_CASE_STYLE_SYSTEM, prompt_text, profile="analyze_pleading")
        try:
            return json.loads(_strip_fences(response))
        except json.JSONDecodeError as e:
            LOGGER.warning("intake_service.extract_case_style: parse failure: %s", str(e))
            raise ValueError("Could not read the case style — the model's response was not valid JSON") from e

    # ── Preview ───────────────────────────────────────────────────────────

    def preview(
        self,
        manager: DatabaseManager,
        raw_text: str,
        foreign_db: Optional[DatabaseManager] = None,
        staff_id: Optional[int] = None,
        role: Optional[str] = None,
    ) -> MatterIntakePreviewResponse:
        """
        Extract everything needed for the review screen. Writes nothing.

        :param manager: Cyclone database manager for this request.
        :type manager: DatabaseManager
        :param raw_text: Full extracted text of the pleading.
        :type raw_text: str
        :param foreign_db: Landing-pages manager, for matching parties to leads.
            Omit to skip lead matching.
        :param staff_id: Caller, used to filter leads to those they may see.
        :param role: Caller's role, same purpose.
        :return: The review payload.
        :rtype: MatterIntakePreviewResponse
        """
        warnings: list[str] = []
        started = time.monotonic()

        # The case style and the claims are independent reads of the same text,
        # so they run together. Sequentially they roughly double the time the
        # attorney waits on the upload, which is what pushes this request past
        # a reverse proxy's timeout.
        with ThreadPoolExecutor(max_workers=2) as pool:
            meta_future = pool.submit(self.extract_case_style, raw_text)
            claims_future = pool.submit(pleading_service.extract_claims, raw_text)
            meta = meta_future.result()
            try:
                claims_raw = claims_future.result()
            except Exception as e:  # noqa: BLE001 — claims are not required to open a file
                warnings.append("Claims extraction failed: %s" % str(e))
                claims_raw = []
        llm_elapsed = time.monotonic() - started

        # Match each party against existing clients so a returning client is not
        # entered twice. This is a convenience match, NOT a conflict check.
        clients, _ = ClientRepository(manager).select_many(condition={})

        # ...and against leads, which are the preferred origin for a client:
        # a lead has cleared the conflict check and carries contact details.
        # Access-filtered by the caller's slugs, so this cannot surface leads
        # the user is not entitled to see.
        leads = []
        if foreign_db is not None and staff_id is not None and role is not None:
            try:
                from services.lead_service import lead_service  # noqa: PLC0415 — avoids an import cycle
                leads = [
                    lead for lead in lead_service.list_leads(
                        manager, foreign_db, staff_id, role, limit=_LEAD_MATCH_SCAN,
                    )
                    if lead.converted_to_client_id is None  # Already a client — the client match covers it
                ]
            except Exception as e:  # noqa: BLE001 — lead matching is a convenience, not a requirement
                LOGGER.warning("intake_service.preview: lead matching unavailable: %s", str(e))
                warnings.append("Could not check for a matching lead; client matching is unaffected")

        parties: list[IntakePartyPreview] = []
        for raw_party in (meta.get("parties") or []):
            full_name = (raw_party or {}).get("full_name")
            if not full_name:
                warnings.append("Skipped a party with no name")
                continue
            matches = []
            for client in clients:
                client_name = "%s %s" % (client.name.first_name, client.name.last_name)
                confidence = _match_confidence(full_name, client_name)
                if confidence:
                    matches.append(IntakeClientMatch(
                        client_id=client.id, full_name=client_name, confidence=confidence,
                    ))
            matches.sort(key=lambda m: 0 if m.confidence == "strong" else 1)

            lead_matches = []
            for lead in leads:
                confidence = _match_confidence(full_name, lead.full_name or "")
                if confidence:
                    lead_matches.append(IntakeLeadMatch(
                        session_uuid=lead.session_uuid,
                        full_name=lead.full_name or "(no name)",
                        email=lead.email,
                        telephone=lead.telephone,
                        status=lead.status.value if hasattr(lead.status, "value") else str(lead.status),
                        confidence=confidence,
                    ))
            lead_matches.sort(key=lambda m: 0 if m.confidence == "strong" else 1)

            parties.append(IntakePartyPreview(
                full_name=full_name,
                designation=(raw_party.get("designation") or None),
                client_matches=matches,
                lead_matches=lead_matches,
            ))

        attorneys: list[IntakeAttorneyPreview] = []
        for raw_attorney in (meta.get("attorneys") or []):
            try:
                attorneys.append(IntakeAttorneyPreview.model_validate(raw_attorney))
            except Exception as e:
                warnings.append("Could not parse an attorney entry: %s" % str(e))

        children: list[ChildPreview] = []
        for raw_child in (meta.get("children") or []):
            try:
                children.append(ChildPreview.model_validate({
                    **raw_child,
                    "date_of_birth": pleading_service._parse_date(raw_child.get("date_of_birth")),
                    "sex": pleading_service._parse_sex(raw_child.get("sex")),
                }))
            except Exception as e:
                warnings.append("Could not parse a child entry: %s" % str(e))

        claims: list[ClaimPreview] = []
        for raw_claim in claims_raw:
            try:
                claims.append(ClaimPreview.model_validate(raw_claim))
            except Exception:
                warnings.append("Skipped a malformed claim entry")

        case = IntakeCasePreview(
            title=meta.get("title") or "(untitled)",
            filed_date=pleading_service._parse_date(meta.get("filed_date")),
            served_date=pleading_service._parse_date(meta.get("served_date")),
            state=meta.get("state"),
            county=meta.get("county"),
            court_name=meta.get("court_name"),
            matter_number=meta.get("matter_number"),
            matter_type=_coerce_enum(meta.get("matter_type"), "matter_type", warnings),
            discovery_level=_coerce_enum(meta.get("discovery_level"), "discovery_level", warnings),
            suggested_matter_name=_suggest_matter_name(parties),
        )

        if not parties:
            warnings.append("No parties were found — the case style may not have been read correctly")

        LOGGER.info(
            "intake_service.preview: parties=%d attorneys=%d children=%d claims=%d "
            "(llm %.1fs, total %.1fs)",
            len(parties), len(attorneys), len(children), len(claims),
            llm_elapsed, time.monotonic() - started,
        )
        return MatterIntakePreviewResponse(
            raw_text=raw_text, case=case, parties=parties, attorneys=attorneys,
            children=children, claims=claims, warnings=warnings,
        )

    # ── Commit ────────────────────────────────────────────────────────────

    def commit(
        self,
        manager: DatabaseManager,
        staff_id: int,
        request: MatterIntakeCommitRequest,
    ) -> MatterIntakeCommitResponse:
        """
        Create the client (if needed), the matter, and then the pleading.

        :param manager: Database manager for this request.
        :type manager: DatabaseManager
        :param staff_id: Staff member performing the intake.
        :type staff_id: int
        :param request: The attorney-reviewed intake payload.
        :type request: MatterIntakeCommitRequest
        :return: Ids and counts of everything created.
        :rtype: MatterIntakeCommitResponse
        :raises ValueError: If neither an existing client nor a new one is given.
        """
        client_repo = ClientRepository(manager)

        # 1. Client — reuse the matched one, or create from the review form.
        client_created = False
        if request.existing_client_id is not None:
            client = client_repo.select_one(condition={"id": request.existing_client_id})
            if client is None:
                raise ValueError("Client not found: id=%s" % request.existing_client_id)
            client_id = client.id
        elif request.new_client is not None:
            created = client_repo.insert(Client(**request.new_client.model_dump()).model_dump())
            client_id = created.id
            client_created = True
            LOGGER.info("intake_service.commit: created client id=%s", client_id)
        else:
            raise ValueError("Select an existing client or provide details for a new one")

        # 2. Matter.
        matter = Matter(
            client_id=client_id,
            matter_name=request.matter.matter_name,
            short_name=request.matter.short_name,
            matter_type=request.matter.matter_type,
            state=request.matter.state,
            county=request.matter.county,
            court_name=request.matter.court_name,
            matter_number=request.matter.matter_number,
            discovery_level=request.matter.discovery_level,
            opened_date=request.matter.opened_date or date.today(),
            notes=request.matter.notes,
        )
        matter_record = MatterRepository(manager).insert(matter.model_dump())
        LOGGER.info("intake_service.commit: created matter id=%s for client id=%s", matter_record.id, client_id)

        # 3. No pleading — a lead promoted before any paper arrived. The client
        #    and matter are the whole job; there are no parties or counsel to
        #    derive without a document naming them.
        if request.case is None or not request.our_party_name:
            LOGGER.info("intake_service.commit: matter %s opened without a pleading", matter_record.id)
            return MatterIntakeCommitResponse(
                client_id=client_id,
                client_created=client_created,
                matter_id=matter_record.id,
                pleading_id=None,
                opposing_parties_created=0,
                children_created=0,
                opposing_counsel_linked=0,
                claims_created=0,
            )

        # 4. Everything adverse is whatever is not us. This is the only place
        #    the "who do we represent" answer is applied.
        ours = request.our_party_name
        opposing_parties = [
            OpposingPartyCommitEntry(full_name=p.full_name, relationship=p.designation)
            for p in request.parties
            if not _same_name(p.full_name, ours)
        ]
        opposing_counsel = [
            OCCommitEntry(
                name=a.name,
                firm_name=a.firm_name,
                street_address=a.street_address,
                street_address_2=a.street_address_2,
                city=a.city,
                state=a.state,
                postal_code=a.postal_code,
                email=a.email,
                cell_phone=a.cell_phone,
                telephone=a.telephone,
                fax=a.fax,
                bar_state=a.bar_state or "",
                bar_number=a.bar_number or "",
                represents=a.represents,
            )
            for a in request.attorneys
            if a.represents and not _same_name(a.represents, ours) and a.bar_state and a.bar_number
        ]
        dropped = len(request.attorneys) - len(opposing_counsel)
        if dropped:
            LOGGER.info("intake_service.commit: %d attorney(s) not stored (ours, or missing a bar number)", dropped)

        # 5. Hand the rest to the ordinary pleading commit.
        pleading_request = PleadingCommitRequest(
            matter_id=matter_record.id,
            raw_text=request.raw_text,
            title=request.case.title,
            filed_date=request.case.filed_date,
            served_date=request.case.served_date,
            opposing_party_name=opposing_parties[0].full_name if opposing_parties else None,
            opposing_parties=opposing_parties,
            children=request.children,
            opposing_counsel=opposing_counsel,
            claims=request.claims,
        )
        pleading_record, parties_created, children_created, oc_count, claims_created = (
            pleading_service.commit_ingest(manager=manager, staff_id=staff_id, request=pleading_request)
        )

        return MatterIntakeCommitResponse(
            client_id=client_id,
            client_created=client_created,
            matter_id=matter_record.id,
            pleading_id=pleading_record.id,
            opposing_parties_created=parties_created,
            children_created=children_created,
            opposing_counsel_linked=oc_count,
            claims_created=claims_created,
        )


def _strip_fences(text: str) -> str:
    """Strip ```json fences the model adds despite being told not to."""
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    return re.sub(r"\n?```\s*$", "", stripped).strip()


def _same_name(a: str, b: str) -> bool:
    """True when two party names plausibly refer to the same person."""
    return _match_confidence(a, b) == "strong"


def _coerce_enum(value: Any, field: str, warnings: list[str]) -> Any:
    """Return the value only if the schema will accept it; warn and drop otherwise."""
    if value in (None, ""):
        return None
    try:
        if field == "matter_type":
            from db.models.matter import MatterType
            return MatterType(str(value))
        from db.models.matter import DiscoveryLevel
        return DiscoveryLevel(str(value))
    except ValueError:
        warnings.append("Ignored an unrecognized %s: %r" % (field.replace("_", " "), value))
        return None


def _suggest_matter_name(parties: list[IntakePartyPreview]) -> Optional[str]:
    """Build 'Surname v. Surname' from the first two parties, for the matter name."""
    surnames = [p.full_name.split()[-1] for p in parties if p.full_name.strip()]
    if len(surnames) >= 2:
        return "%s v. %s" % (surnames[0], surnames[1])
    return surnames[0] if surnames else None


intake_service = IntakeService()
