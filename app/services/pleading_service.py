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
from typing import Any

from db.models.matter import MatterInDB
from db.models.pleading import (
    ChildSex,
    ClaimKind,
    CounselRole,  # type: ignore
    MatterChild,
    MatterClaim,
    MatterOpposingCounsel,
    MatterPleading,
    OpposingCounsel,
)
from db.repositories.matter import MatterRepository
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
    PleadingCommitRequest,
    PleadingIngestPreviewResponse,
    PleadingPreview,
)
from services.llm_service import llm_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


def _strip_markdown_fences(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


# ── LLM Prompts ───────────────────────────────────────────────────────────────

_METADATA_SYSTEM = """\
You are a legal expert analyzing a pleading filed in a Texas family law case.

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
- opposing_counsel: array of objects (from the signature block, empty if not present):
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

    def classify_and_extract(self, raw_text: str) -> dict[str, Any]:
        """First LLM call: extract pleading metadata, case info, children, OC."""
        # Send first ~12000 chars — metadata + signature block live at top and bottom
        # so we also append the tail
        head = raw_text[:10000]
        tail = raw_text[-4000:] if len(raw_text) > 10000 else ""
        prompt_text = f"{head}\n\n[END OF DOCUMENT OR TAIL]\n{tail}" if tail else head

        response = llm_service.complete(_METADATA_SYSTEM, prompt_text)
        try:
            return json.loads(_strip_markdown_fences(response))
        except json.JSONDecodeError as e:
            LOGGER.warning("pleading_service.classify_and_extract: parse failure: %s", str(e))
            raise ValueError("Could not classify pleading — LLM response was not valid JSON") from e

    def extract_claims(self, raw_text: str) -> list[dict[str, Any]]:
        """Second LLM call: extract claims, defenses, counterclaims."""
        response = llm_service.complete(_CLAIMS_SYSTEM, raw_text)
        try:
            items: list[dict[str, Any]] = json.loads(_strip_markdown_fences(response))
            if not isinstance(items, list):  # type: ignore - Extra sanity check since this is free-form LLM output
                raise ValueError("Expected a JSON array")
            return items
        except (json.JSONDecodeError, ValueError) as e:
            LOGGER.warning("pleading_service.extract_claims: parse failure: %s", str(e))
            return []  # Claims extraction failure is non-fatal; attorney can add them manually

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

        # Step 1: Metadata + case info + children + OC
        try:
            meta = self.classify_and_extract(raw_text)
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

        # Children previews
        new_children: list[ChildPreview] = []
        children_raw: list[dict[str, Any]] = meta.get("children") or []
        for child_data in children_raw:
            try:
                # Coerce LLM date/enum strings that might be malformed — bad values
                # become None rather than failing validation and dropping the child.
                new_children.append(ChildPreview.model_validate({
                    **child_data,
                    "date_of_birth": self._parse_date(child_data.get("date_of_birth")),
                    "sex": self._parse_sex(child_data.get("sex")),
                }))
            except Exception as e:
                warnings.append("Could not parse a child entry: %s" % str(e))

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
    ) -> tuple[Any, int, int, int]:
        """
        Commit the attorney-reviewed preview.

        Writes: pleading row, matter field updates, children, OC (new + updated),
        matter_opposing_counsel links, and claims.

        Returns (pleading_record, children_count, oc_count, claims_count).
        """
        matter_id = request.matter_id

        # 1. Apply matter field updates
        if request.matter_field_updates:
            matter_repo = MatterRepository(manager)
            matter_repo.update(matter_id, request.matter_field_updates)
            LOGGER.info("pleading_service.commit: applied matter field updates for matter %s: %s",
                        matter_id, list(request.matter_field_updates))

        # 2. Create the pleading row
        pleading_repo = MatterPleadingRepository(manager)
        pleading = MatterPleading(
            matter_id=matter_id,
            opposing_party_id=request.opposing_party_id,
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

        # 3. Upload PDF to storage (if provided) and update the row
        if pdf_bytes:
            storage = StorageService(manager)
            try:
                storage_path = storage.upload_pleading(matter_id, pleading_record.id, pdf_bytes)
                pleading_record = pleading_repo.update(pleading_record.id, {"storage_path": storage_path})
            except Exception as e:
                LOGGER.error("pleading_service.commit: PDF upload failed: %s", str(e))
                # Non-fatal — the row exists, just without the stored PDF

        # 4. Create children
        child_repo = MatterChildRepository(manager)
        for child_entry in request.children:
            child = MatterChild(
                matter_id=matter_id,
                name=child_entry.name,
                date_of_birth=child_entry.date_of_birth,
                sex=child_entry.sex,
                needs_support_after_majority=child_entry.needs_support_after_majority,
            )
            child_repo.insert(child.model_dump())
        LOGGER.info("pleading_service.commit: created %s children", len(request.children))

        # 5. Create/update opposing counsel and link to matter
        oc_repo = OpposingCounselRepository(manager)
        m_oc_repo = MatterOpposingCounselRepository(manager)
        oc_count = 0
        for oc_entry in request.opposing_counsel:
            if oc_entry.existing_id:
                # Update existing OC row with any changed fields
                update_fields = oc_entry.model_dump(
                    exclude={"existing_id", "opposing_party_id", "role", "bar_state", "bar_number"},
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

            # Link to matter if not already linked
            if not m_oc_repo.exists_for_matter(matter_id, oc_id):
                link = MatterOpposingCounsel(
                    matter_id=matter_id,
                    opposing_counsel_id=oc_id,
                    opposing_party_id=oc_entry.opposing_party_id,
                    role=oc_entry.role,
                )
                m_oc_repo.insert(link.model_dump())
            oc_count += 1

        # 6. Create claims
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

        return pleading_record, len(request.children), oc_count, len(request.claims)

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
