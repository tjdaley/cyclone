"""
app/services/pleading_service.py - Pleading ingestion orchestrator.

Two-step LLM pipeline:
  1. classify_and_extract — pulls case metadata, children, OC, and pleading details
  2. extract_claims — pulls each distinct claim/defense/affirmative defense

The preview flow is stateless: preview_ingest() returns everything the
frontend needs to render a review form; the frontend echoes the edited
version back to commit_ingest() which performs the actual writes.
"""
import json
import re
from typing import Any, Optional

from db.models.matter import MatterInDB, OpposingParty
from db.models.pleading import (
    ChildSex,
    ClaimKind,
    CounselRole,  # type: ignore
    MatterChild,
    MatterClaim,
    MatterOpposingCounsel,
    MatterPleading,
    OpposingCounsel,
    PleadingStatus,
)
from db.repositories.client import ClientRepository
from db.repositories.matter import MatterRepository, OpposingPartyRepository
from db.repositories.pleading import (
    MatterChildRepository,
    MatterClaimRepository,
    MatterOpposingCounselRepository,
    MatterPleadingRepository,
    OpposingCounselRepository,
)
from db_handler import DatabaseManager
from schemas.pleading import (
    ChildCommitEntry,  # type: ignore
    ChildPreview,
    ClaimCommitEntry,  # type: ignore
    ClaimPreview,
    FieldDiff,
    OCCommitEntry,  # type: ignore
    OCMatchPreview,
    OCPreview,
    OpposingPartyPreview,
    PleadingCommitRequest,
    PleadingIngestPreviewResponse,
    PleadingPreview,
)
from services.llm_service import llm_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


def _strip_markdown_fences(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def _same_child(name_a: Any, dob_a: Any, name_b: Any, dob_b: Any) -> bool:
    """
    Decide whether two child records describe the same child.

    A later pleading restates the children already pleaded, so this is what
    keeps a counterpetition from re-adding them. Date of birth alone is not
    enough (twins) and the name alone is not enough (a parent and child sharing
    a name), so both must agree. Names are compared loosely — a pleading may
    write "Selah L. Salmons" where the first one wrote "Selah Lynndon Salmons".

    This is deliberately enforced here rather than by a unique index on
    matter_children: siblings can legitimately share a first name and birthday
    (half-siblings from different relationships, reused family names), and an
    attorney can resolve that in the review form. A database constraint would
    reject the commit outright.

    :param name_a: FullName of the first child.
    :param dob_a: Date of birth of the first child, or None.
    :param name_b: FullName of the second child.
    :param dob_b: Date of birth of the second child, or None.
    :return: True when both records appear to be the same child.
    :rtype: bool
    """
    first_a = (getattr(name_a, "first_name", "") or "").strip().lower()
    last_a = (getattr(name_a, "last_name", "") or "").strip().lower()
    first_b = (getattr(name_b, "first_name", "") or "").strip().lower()
    last_b = (getattr(name_b, "last_name", "") or "").strip().lower()

    if not first_a or not last_a or first_b != first_a or last_b != last_a:
        return False
    # Both dates known: they must agree. If either is missing, the matching
    # first and last name on the same matter is taken as sufficient.
    if dob_a and dob_b:
        return dob_a == dob_b
    return True


def _same_party(a: str, b: str) -> bool:
    """
    Loose name comparison for "is this the party we represent?".

    Case- and punctuation-insensitive, and tolerant of a partial name: the
    document may say "Kaci Salmons" where the matter says
    "Kaci Lynndon Salmons". Every word of the shorter name must appear in
    the longer one.

    :param a: First party name.
    :type a: str
    :param b: Second party name.
    :type b: str
    :return: True when the names plausibly refer to the same party.
    :rtype: bool
    """
    words_a = set(re.findall(r"[a-z]+", a.lower()))
    words_b = set(re.findall(r"[a-z]+", b.lower()))
    if not words_a or not words_b:
        return False
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    return shorter <= longer


# ── LLM Prompts ───────────────────────────────────────────────────────────────

_METADATA_SYSTEM = """\
You are a legal expert analyzing a pleading filed in a Texas family law case.

The user message begins with our_client and our_firm — the party WE represent and the
firm doing the analyzing. "Opposing" always means adverse to our_client, never adverse
to whoever filed the document.

Extract case metadata and return ONLY a valid JSON object with these fields:

- title: the title of the pleading, e.g. "Original Petition for Divorce"
- filed_date: "YYYY-MM-DD" or null (from the court clerk's file stamp)
- served_date: "YYYY-MM-DD" or null (from the certificate of service)
- is_supplement: boolean (true if the title contains "Supplemental", false otherwise)
- amends_pleading_title: string or null (if the title says "Amended" or "First Amended", \
  return the title of the prior pleading being amended, e.g. "Original Petition for Divorce"; \
  otherwise null)
- case_metadata: object with these fields (all nullable):
  - state: string (e.g. "Texas")
  - county: string (e.g. "Dallas")
  - court_name: string (e.g. "401st District Court")
  - matter_number: string (cause number)
  - matter_type: one of "divorce" | "child_custody" | "modification" | "enforcement" | \
    "cps" | "probate" | "estate_planning" | "civil" | "other"
  - discovery_level: "level_1" | "level_2" | "level_3" or null
- children: array of objects (empty if no children are mentioned):
  - name: {first_name, last_name, middle_name, courtesy_title, suffix}
  - date_of_birth: "YYYY-MM-DD" or null
  - sex: "male" | "female" | "other" or null
- opposing_parties: array of objects. Every PARTY to this suit who is adverse to our_client —
  the other spouse, the other parent, a respondent, an intervenor. Never our_client, and never
  a child of the marriage (children are listed above, not here). Use the party's name exactly
  as the document writes it. Each object has:
  - full_name: string
  - relationship: string or null — relationship to our_client, e.g. "spouse", "father of the child"
- opposing_counsel: array of objects. Include EVERY attorney of record who represents a party
  ADVERSE to our_client. NEVER include our_client's own attorney, even when the document names
  them: a certificate of service, or a "service may be had by serving X" paragraph, names the
  attorney being SERVED, who is usually OUR attorney rather than opposing counsel. The attorney
  in the signature block is opposing counsel whenever the filing party is adverse to our_client.
  Return an empty array if the only attorneys named represent our_client. Each object has:
  - represents: string — the party this attorney represents, exactly as named in the document
  - name: {first_name, last_name, middle_name, courtesy_title, suffix}
  - firm_name: string or null
  - street_address: string or null
  - street_address_2: string or null
  - city: string or null
  - state: string or null (state of mailing address)
  - postal_code: string or null
  - email: string or null
  - cell_phone: string or null
  - telephone: string or null
  - fax: string or null
  - bar_state: string or null (e.g. "TX")
  - bar_number: string or null

Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""

_CLAIMS_SYSTEM = """\
You are a legal expert extracting claims, defenses, and counterclaims from a pleading.

Return ONLY a valid JSON array. Each element must have:
- kind: one of "claim" | "defense" | "affirmative_defense" | "counterclaim"
- label: short descriptive label, e.g. "Fault: adultery", "Statute of limitations"
- narrative: the full text of the claim as stated in the pleading (do not paraphrase substantively)
- statute_rule_cited: any statute or rule cited in support, or null
- party_side: "our_client" if this is our client's claim/defense, "opposing" otherwise

Extract EVERY distinct claim, defense, and counterclaim in the document. Do not skip any.
A "claim" is an affirmative request for relief (divorce grounds, custody, support, etc.).
A "defense" is a denial or response to the other side's claims.
An "affirmative defense" is a legal bar that defeats a claim even if the facts are true.
A "counterclaim" is a claim filed by the respondent against the petitioner.

Respond ONLY with a valid JSON array. No markdown fences, no explanation.\
"""


class PleadingService:

    # ── LLM calls ─────────────────────────────────────────────────────────

    def classify_and_extract(self, raw_text: str, client_name: str, firm_name: str) -> dict[str, Any]:
        """
        First LLM call: extract pleading metadata, case info, children, OC.

        ``client_name`` is required, not optional: without it the model cannot
        tell which attorney is *opposing*. A pleading filed against us names our
        own attorney in its service paragraph, and with no client context the
        model reasonably reads that attorney as the opposing one.

        :param raw_text: Full extracted text of the pleading.
        :type raw_text: str
        :param client_name: The party we represent on this matter.
        :type client_name: str
        :param firm_name: Our firm's name.
        :type firm_name: str
        :return: Parsed metadata dict.
        :rtype: dict[str, Any]
        :raises ValueError: If the LLM response is not valid JSON.
        """
        # Send first ~12000 chars — metadata + signature block live at top and bottom
        # so we also append the tail
        head = raw_text[:10000]
        tail = raw_text[-4000:] if len(raw_text) > 10000 else ""
        body = f"{head}\n\n[END OF DOCUMENT OR TAIL]\n{tail}" if tail else head
        prompt_text = f"our_client: {client_name}\nour_firm: {firm_name}\n\npleading_text:\n{body}"

        response = llm_service.complete(_METADATA_SYSTEM, prompt_text, profile="analyze_pleading")
        try:
            return json.loads(_strip_markdown_fences(response))
        except json.JSONDecodeError as e:
            LOGGER.warning("pleading_service.classify_and_extract: parse failure: %s", str(e))
            raise ValueError("Could not classify pleading — LLM response was not valid JSON") from e

    def extract_claims(self, raw_text: str) -> list[dict[str, Any]]:
        """Second LLM call: extract claims, defenses, counterclaims."""
        response = llm_service.complete(_CLAIMS_SYSTEM, raw_text, profile="extract_pleading_claims")
        try:
            items: list[dict[str, Any]] = json.loads(_strip_markdown_fences(response))
            if not isinstance(items, list):  # type: ignore - Extra sanity check since this is free-form LLM output
                raise ValueError("Expected a JSON array")
            return items
        except (json.JSONDecodeError, ValueError) as e:
            LOGGER.warning("pleading_service.extract_claims: parse failure: %s", str(e))
            return []  # Claims extraction failure is non-fatal; attorney can add them manually

    # ── Children ──────────────────────────────────────────────────────────

    def find_matching_child(
        self,
        manager: DatabaseManager,
        matter_id: int,
        name: Any,
        date_of_birth: Any,
        ignore_id: int | None = None,
    ) -> Any:
        """
        Find a child already on the matter that matches name + date of birth.

        Shared by pleading ingestion and the children CRUD endpoints so both
        apply the same duplicate rule. See ``_same_child`` for the comparison.

        :param manager: Database manager for this request.
        :type manager: DatabaseManager
        :param matter_id: Matter to search within.
        :type matter_id: int
        :param name: FullName of the child being added or edited.
        :param date_of_birth: Date of birth of that child, or None.
        :param ignore_id: Row to exclude — the child being edited, which
            must not match itself.
        :type ignore_id: int | None
        :return: The matching MatterChildInDB, or None.
        """
        repo = MatterChildRepository(manager)
        for existing in repo.get_by_matter(matter_id):
            if ignore_id is not None and existing.id == ignore_id:
                continue
            if _same_child(name, date_of_birth, existing.name, existing.date_of_birth):
                return existing
        return None

    # ── Preview ───────────────────────────────────────────────────────────

    def preview_ingest(
        self,
        manager: DatabaseManager,
        matter_id: int,
        raw_text: str,
    ) -> PleadingIngestPreviewResponse:
        """
        Run the full extraction pipeline without persisting anything.

        Returns a payload for the attorney to review and edit.
        """
        warnings: list[str] = []

        # Load the matter so we can compare against extracted fields
        matter_repo = MatterRepository(manager)
        matter: MatterInDB | None = matter_repo.select_one(condition={"id": matter_id})
        if matter is None:
            raise ValueError("Matter not found: id=%s" % matter_id)

        # Our client's name tells the LLM which side is "opposing" — see
        # classify_and_extract. Without it, our own attorney gets extracted.
        client_repo = ClientRepository(manager)
        client = client_repo.select_one(condition={"id": matter.client_id})
        if client is None:
            raise ValueError("Client not found: id=%s" % matter.client_id)
        client_name = str(client.name)

        # Step 1: Metadata + case info + children + OC
        try:
            meta = self.classify_and_extract(raw_text, client_name, settings.firm_name)
        except ValueError as e:
            raise ValueError(str(e)) from e

        pleading_preview = PleadingPreview(
            title=meta.get("title", "(untitled)"),
            filed_date=self._parse_date(meta.get("filed_date")),
            served_date=self._parse_date(meta.get("served_date")),
            is_supplement=bool(meta.get("is_supplement", False)),
            amends_pleading_title=meta.get("amends_pleading_title"),
        )

        # Matter field diffs
        case_meta: dict[str, Any] = meta.get("case_metadata", {}) or {}
        matter_field_updates: dict[str, FieldDiff] = {}
        for field in ("state", "county", "court_name", "matter_number", "matter_type", "discovery_level"):
            proposed = case_meta.get(field)
            if proposed is None:
                continue
            current = getattr(matter, field, None)
            if current:
                current_val = current.value if hasattr(current, "value") else current
                if current_val != proposed:
                    matter_field_updates[field] = FieldDiff(current=current_val, proposed=proposed)

        # Opposing parties — every counsel link, claim, and pleading points at one
        # of these, so they have to exist before any of that can be assigned.
        party_repo = OpposingPartyRepository(manager)
        existing_parties = party_repo.get_by_matter(matter_id)

        party_previews: list[OpposingPartyPreview] = []
        for party_data in (meta.get("opposing_parties") or []):
            try:
                preview_party = OpposingPartyPreview.model_validate(party_data)
            except Exception as e:
                warnings.append("Could not parse an opposing party entry: %s" % str(e))
                continue

            # The same trap as opposing counsel: from the filer's perspective our
            # client is the adverse party, so drop them if the LLM lists them.
            if _same_party(preview_party.full_name, client_name):
                warnings.append(
                    "Ignored an opposing party entry naming our own client (%s)" % preview_party.full_name
                )
                continue

            match = next(
                (p for p in existing_parties if _same_party(preview_party.full_name, p.full_name)),
                None,
            )
            if match is not None:
                preview_party.existing_id = match.id
            party_previews.append(preview_party)

        # Children previews — matched against the children already on the matter
        # so a later pleading restating them does not create duplicates.
        child_repo = MatterChildRepository(manager)
        existing_children = child_repo.get_by_matter(matter_id)

        new_children: list[ChildPreview] = []
        children_raw: list[dict[str, Any]] = meta.get("children") or []
        for child_data in children_raw:
            try:
                # Coerce LLM date/enum strings that might be malformed — bad values
                # become None rather than failing validation and dropping the child.
                preview_child = ChildPreview.model_validate({
                    **child_data,
                    "date_of_birth": self._parse_date(child_data.get("date_of_birth")),
                    "sex": self._parse_sex(child_data.get("sex")),
                })
            except Exception as e:
                warnings.append("Could not parse a child entry: %s" % str(e))
                continue

            match = next(
                (
                    existing for existing in existing_children
                    if _same_child(
                        preview_child.name, preview_child.date_of_birth,
                        existing.name, existing.date_of_birth,
                    )
                ),
                None,
            )
            if match is not None:
                preview_child.existing_id = match.id
            new_children.append(preview_child)

        # Opposing counsel — match by bar number
        oc_repo = OpposingCounselRepository(manager)
        oc_matches: list[OCMatchPreview] = []
        new_ocs: list[OCPreview] = []

        oc_raw: list[dict[str, Any]] = meta.get("opposing_counsel") or []
        for oc_data in oc_raw:
            try:
                preview = OCPreview.model_validate(oc_data)
            except Exception as e:
                warnings.append("Could not parse opposing counsel entry: %s" % str(e))
                continue

            # Deterministic backstop for the failure the prompt guards against:
            # an attorney the LLM says represents our own client is not opposing
            # counsel, whatever the document's own perspective calls them.
            if preview.represents and _same_party(preview.represents, client_name):
                warnings.append(
                    "Ignored an attorney extracted as opposing counsel who represents our client (%s)"
                    % preview.represents
                )
                continue
            bar_state = preview.bar_state
            bar_number = preview.bar_number

            existing = None
            if bar_state and bar_number:
                existing = oc_repo.get_by_bar_number(bar_state, bar_number)

            if existing is not None:
                # Compute diffs
                diffs: dict[str, FieldDiff] = {}
                for field in (
                    "firm_name", "street_address", "street_address_2", "city", "state",
                    "postal_code", "email", "cell_phone", "telephone", "fax",
                ):
                    current = getattr(existing, field, None)
                    proposed = getattr(preview, field, None)
                    if proposed and proposed != current:
                        diffs[field] = FieldDiff(current=current, proposed=proposed)

                from schemas.pleading import OpposingCounselResponse
                oc_matches.append(OCMatchPreview(
                    existing_id=existing.id,
                    existing=OpposingCounselResponse(**existing.model_dump()),
                    proposed=preview,
                    diffs=diffs,
                ))
            else:
                new_ocs.append(preview)

        # Step 2: Claims
        claim_previews: list[ClaimPreview] = []
        try:
            claims_raw = self.extract_claims(raw_text)
            for c in claims_raw:
                try:
                    claim_previews.append(ClaimPreview(
                        kind=ClaimKind(c.get("kind", "claim")),
                        label=c.get("label", "(unlabeled)"),
                        narrative=c.get("narrative", ""),
                        statute_rule_cited=c.get("statute_rule_cited"),
                        party_side=c.get("party_side", "opposing"),
                    ))
                except Exception:
                    warnings.append("Skipped malformed claim entry")
        except Exception as e:
            warnings.append("Claims extraction failed: %s" % str(e))

        return PleadingIngestPreviewResponse(
            matter_id=matter_id,
            raw_text=raw_text,
            pleading=pleading_preview,
            matter_field_updates=matter_field_updates,
            opposing_parties=party_previews,
            new_children=new_children,
            opposing_counsel_matches=oc_matches,
            new_opposing_counsel=new_ocs,
            claims=claim_previews,
            warnings=warnings,
        )

    # ── Commit ────────────────────────────────────────────────────────────

    def commit_ingest(
        self,
        manager: DatabaseManager,
        staff_id: int,
        request: PleadingCommitRequest,
        pdf_bytes: bytes | None = None,
    ) -> tuple[Any, int, int, int, int]:
        """
        Commit the attorney-reviewed preview.

        Writes: opposing parties, pleading row, matter field updates, children,
        OC (new + updated), matter_opposing_counsel links, and claims.

        Returns (pleading_record, parties_created, children_count, oc_count, claims_count).
        """
        matter_id = request.matter_id

        # 1. Apply matter field updates
        if request.matter_field_updates:
            matter_repo = MatterRepository(manager)
            matter_repo.update(matter_id, request.matter_field_updates)
            LOGGER.info("pleading_service.commit: applied matter field updates for matter %s: %s",
                        matter_id, list(request.matter_field_updates))

        # 2. Create opposing parties FIRST — the pleading row, the counsel links,
        # and the claims below all reference them by id.
        party_repo = OpposingPartyRepository(manager)
        known_parties = party_repo.get_by_matter(matter_id)
        parties_created = 0
        for party_entry in request.opposing_parties:
            match_id = party_entry.existing_id
            if match_id is None:
                match = next(
                    (p for p in known_parties if _same_party(party_entry.full_name, p.full_name)),
                    None,
                )
                match_id = match.id if match else None
            if match_id is not None:
                continue  # Already on the matter — nothing to create

            created_party = party_repo.insert(OpposingParty(
                matter_id=matter_id,
                full_name=party_entry.full_name,
                relationship=party_entry.relationship,
            ).model_dump())
            known_parties.append(created_party)
            parties_created += 1
        LOGGER.info("pleading_service.commit: created %s opposing parties for matter %s",
                    parties_created, matter_id)

        def resolve_party(name: Optional[str]) -> Optional[int]:
            """Map a party name from the review form to an id now that parties exist."""
            if not name:
                return None
            match = next((p for p in known_parties if _same_party(name, p.full_name)), None)
            return match.id if match else None

        # 3. Create the pleading row
        pleading_repo = MatterPleadingRepository(manager)
        pleading = MatterPleading(
            matter_id=matter_id,
            opposing_party_id=request.opposing_party_id or resolve_party(request.opposing_party_name),
            title=request.title,
            filed_date=request.filed_date,
            served_date=request.served_date,
            amends_pleading_id=request.amends_pleading_id,
            is_supplement=request.is_supplement,
            storage_path=None,  # filled in after upload
            raw_text=request.raw_text,
            ingested_by_staff_id=staff_id,
        )
        pleading_record = pleading_repo.insert(pleading.model_dump())
        LOGGER.info("pleading_service.commit: created pleading id=%s", pleading_record.id)

        # An amendment supersedes what it amends. A supplement does not — it adds
        # to the live pleading, so the amended-pleading pointer is what matters.
        if request.amends_pleading_id and not request.is_supplement:
            amended = pleading_repo.select_one(condition={"id": request.amends_pleading_id})
            if amended is None:
                LOGGER.warning(
                    "pleading_service.commit: amends_pleading_id=%s not found; nothing superseded",
                    request.amends_pleading_id,
                )
            elif amended.status != PleadingStatus.superseded:
                pleading_repo.update(request.amends_pleading_id, {"status": PleadingStatus.superseded.value})
                LOGGER.info("pleading_service.commit: pleading id=%s marked superseded by id=%s",
                            request.amends_pleading_id, pleading_record.id)

        # 4. Upload PDF to storage (if provided) and update the row
        if pdf_bytes:
            storage = StorageService(manager)
            try:
                storage_path = storage.upload_pleading(matter_id, pleading_record.id, pdf_bytes)
                pleading_record = pleading_repo.update(pleading_record.id, {"storage_path": storage_path})
            except Exception as e:
                LOGGER.error("pleading_service.commit: PDF upload failed: %s", str(e))
                # Non-fatal — the row exists, just without the stored PDF

        # 4. Create or update children. The matter is re-read here rather than
        # trusting the preview: a preview can be stale, and the same pleading
        # can be committed twice. Only genuinely new children are inserted.
        child_repo = MatterChildRepository(manager)
        existing_children = child_repo.get_by_matter(matter_id)
        children_created = 0
        for child_entry in request.children:
            match_id = child_entry.existing_id
            if match_id is None:
                match = next(
                    (
                        existing for existing in existing_children
                        if _same_child(
                            child_entry.name, child_entry.date_of_birth,
                            existing.name, existing.date_of_birth,
                        )
                    ),
                    None,
                )
                match_id = match.id if match else None

            fields = {
                "name": child_entry.name.model_dump(),
                "date_of_birth": child_entry.date_of_birth,
                "sex": child_entry.sex,
                "needs_support_after_majority": child_entry.needs_support_after_majority,
            }
            if match_id is not None:
                child_repo.update(match_id, fields)
                LOGGER.info(
                    "pleading_service.commit: child already on matter %s — updated id=%s instead of inserting",
                    matter_id, match_id,
                )
                continue

            child = MatterChild(
                matter_id=matter_id,
                name=child_entry.name,
                date_of_birth=child_entry.date_of_birth,
                sex=child_entry.sex,
                needs_support_after_majority=child_entry.needs_support_after_majority,
            )
            created_child = child_repo.insert(child.model_dump())
            existing_children.append(created_child)  # Guards duplicates within one payload
            children_created += 1
        LOGGER.info("pleading_service.commit: created %s children (%s submitted)",
                    children_created, len(request.children))

        # 6. Create/update opposing counsel and link to matter
        oc_repo = OpposingCounselRepository(manager)
        m_oc_repo = MatterOpposingCounselRepository(manager)
        oc_count = 0
        for oc_entry in request.opposing_counsel:
            if oc_entry.existing_id:
                # Update existing OC row with any changed fields. Everything
                # excluded here is either an identifier or belongs to the
                # matter↔counsel link rather than the counsel row itself —
                # 'represents' has no column on opposing_counsel.
                update_fields = oc_entry.model_dump(
                    exclude={
                        "existing_id", "opposing_party_id", "represents",
                        "role", "bar_state", "bar_number",
                    },
                    exclude_none=True,
                )
                if update_fields:
                    oc_repo.update(oc_entry.existing_id, update_fields)
                oc_id = oc_entry.existing_id
            else:
                new_oc = OpposingCounsel(
                    name=oc_entry.name,
                    firm_name=oc_entry.firm_name,
                    street_address=oc_entry.street_address,
                    street_address_2=oc_entry.street_address_2,
                    city=oc_entry.city,
                    state=oc_entry.state,
                    postal_code=oc_entry.postal_code,
                    email=oc_entry.email,
                    cell_phone=oc_entry.cell_phone,
                    telephone=oc_entry.telephone,
                    fax=oc_entry.fax,
                    bar_state=oc_entry.bar_state,
                    bar_number=oc_entry.bar_number,
                    email_ccs=oc_entry.email_ccs,
                )
                created = oc_repo.insert(new_oc.model_dump())
                oc_id = created.id

            # Link to matter if not already linked. The party is taken from the
            # reviewed id when present, otherwise resolved from the name the
            # extraction reported — that party may have been created moments ago
            # by this same commit, so it had no id at review time.
            if not m_oc_repo.exists_for_matter(matter_id, oc_id):
                link = MatterOpposingCounsel(
                    matter_id=matter_id,
                    opposing_counsel_id=oc_id,
                    opposing_party_id=oc_entry.opposing_party_id or resolve_party(oc_entry.represents),
                    role=oc_entry.role,
                )
                m_oc_repo.insert(link.model_dump())
            oc_count += 1

        # 7. Create claims
        claim_repo = MatterClaimRepository(manager)
        for claim_entry in request.claims:
            claim = MatterClaim(
                matter_pleading_id=pleading_record.id,
                matter_id=matter_id,
                opposing_party_id=claim_entry.opposing_party_id,
                kind=claim_entry.kind,
                label=claim_entry.label,
                narrative=claim_entry.narrative,
                statute_rule_cited=claim_entry.statute_rule_cited,
            )
            claim_repo.insert(claim.model_dump())
        LOGGER.info("pleading_service.commit: created %s claims", len(request.claims))

        return pleading_record, parties_created, children_created, oc_count, len(request.claims)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(value: Any):
        if not value:
            return None
        try:
            from datetime import date as date_type
            return date_type.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _parse_sex(value: Any):
        if not value:
            return None
        try:
            return ChildSex(str(value).lower())
        except ValueError:
            return None


pleading_service = PleadingService()
