"""
app/services/discovery_service.py - Orchestrates discovery document ingestion.

Calls pdf_service for text extraction, llm_service for parsing, and writes
discovery_requests + discovery_request_items records.
"""
import json
import re
from datetime import date, timedelta
from typing import Optional

from db.models.discovery import (
    DiscoveryDocument,
    DiscoveryDocumentInDB,
    DiscoveryRequestItem,
    DiscoveryRequestItemInDB,
    DocumentRequestType,
)
from db.repositories.discovery import DiscoveryDocumentRepository, DiscoveryRequestItemRepository
from db.repositories.matter import MatterRepository
from db.repositories.client import ClientRepository
from db.supabasemanager import DatabaseManager
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ```) from LLM responses."""
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


# ── LLM Prompts ───────────────────────────────────────────────────────────────

_DOC_CLASSIFY_SYSTEM = """\
You are a legal discovery expert. Analyze the full text of a discovery request \
document and extract metadata about it.

Return ONLY a valid JSON object with these fields:
- request_type: one of "interrogatories" | "production" | "disclosures" | "admissions"
- propounded_by: "opposing_counsel" | "our_client" \
  (who is SENDING these requests — if the document is addressed to our client, \
  then propounded_by is "opposing_counsel")
- service_date: "YYYY-MM-DD" or null (the date in the certificate of service)
- response_days: integer or null (number of days for response, if stated in \
  the document — e.g. "within 30 days" → 30)
- look_back_date: "YYYY-MM-DD" or null (if the preamble says "documents \
  created since <date>" or "in the past X years", compute that date)

If you cannot determine a field, set it to null. \
Respond ONLY with the JSON object. No markdown, no explanation.\
"""

_ITEM_EXTRACT_SYSTEM = """\
You are a legal discovery expert parsing individual numbered requests from a \
discovery document.

Extract each numbered request and return a JSON array. Each element must have:
- request_number: integer (the sequential number, e.g. 1 for "Interrogatory No. 1")
- source_text: the VERBATIM text of that single request, formatted as \
  markdown. Preserve all indentation, numbered sub-items, and emphasis. \
  Do NOT abbreviate, paraphrase, or correct typos.

Include ALL requests in the document, in order. The preamble, definitions, \
and instructions sections are NOT individual requests — skip them.

Respond ONLY with a valid JSON array. No markdown fences, no explanation.\
"""


class DiscoveryService:
    """Orchestrates discovery document ingestion and item extraction."""

    def classify_document(self, client_name: str, raw_text: str) -> dict[str, Optional[str]]:
        """
        Call the LLM to classify the document type and extract metadata.

        :param client_name: Name of the client. Used to determine if the document is propounded by or against our client.
        :param raw_text: Full extracted text from the PDF.
        :return: Dict with request_type, propounded_by, service_date, response_days, look_back_date.
        :raises ValueError: If the LLM response is not valid JSON.
        """
        # Send first ~8000 chars to avoid token overrun — metadata is in the header/preamble
        response = llm_service.complete(_DOC_CLASSIFY_SYSTEM, f"client_name: {client_name}\nraw_text: {raw_text[:8000]}")
        try:
            return json.loads(_strip_markdown_fences(response))
        except json.JSONDecodeError as e:
            LOGGER.warning("discovery_service.classify_document: LLM parse failure: %s", str(e))
            raise ValueError("Could not classify the discovery document — LLM response was not valid JSON") from e

    def extract_items(self, raw_text: str) -> list[dict[str, str]]:
        """
        Call the LLM to extract individual numbered request items.

        :param raw_text: Full extracted text from the PDF.
        :return: List of dicts with request_number and source_text.
        :raises ValueError: If the LLM response cannot be parsed.
        """
        response = llm_service.complete(_ITEM_EXTRACT_SYSTEM, raw_text)
        try:
            items: list[dict[str, str]] = json.loads(_strip_markdown_fences(response))
            if not isinstance(items, list):  # type: ignore
                raise ValueError("Expected a JSON array")
            return items
        except (json.JSONDecodeError, ValueError) as e:
            LOGGER.warning("discovery_service.extract_items: LLM parse failure: %s", str(e))
            raise ValueError("Could not parse discovery items — please review and retry") from e

    def compute_due_date(self, service_date: date, response_days: Optional[int]) -> date:
        """
        Compute the response due date.

        Uses the number of days specified in the document. If not specified,
        defaults to 30 days. Rolls weekends to Monday.

        :param service_date: Date the discovery was served.
        :param response_days: Number of days for response (from the document), or None for default 30.
        :return: Due date, adjusted for weekends.
        """
        days = response_days if response_days and response_days > 0 else 30
        due = service_date + timedelta(days=days)
        if due.weekday() == 5:  # Saturday
            due += timedelta(days=2)
        elif due.weekday() == 6:  # Sunday
            due += timedelta(days=1)
        return due

    def ingest(
        self,
        manager: DatabaseManager,
        matter_id: int,
        staff_id: int,
        raw_text: str,
        propounded_date_override: Optional[date] = None,
    ) -> tuple[DiscoveryDocumentInDB, list[DiscoveryRequestItemInDB], list[str]]:
        """
        Full ingestion pipeline: classify → validate → extract items → persist.

        :param manager: Database manager instance.
        :param matter_id: FK to the matter.
        :param staff_id: FK to the staff member performing the ingestion.
        :param raw_text: Full text extracted from the PDF.
        :param propounded_date_override: Manual override for the service date.
        :return: Tuple of (document record, list of item records, list of warnings).
        :raises ValueError: If the document appears to be propounded by our client.
        """
        warnings: list[str] = []

        # Step 0: Get our client's name
        matter_repo = MatterRepository(manager)
        client_repo = ClientRepository(manager)
        matter = matter_repo.select_one({"id": matter_id})
        assert matter is not None, "Matter not found for id %s" % matter_id
        client = client_repo.select_one({"id": matter.client_id})
        assert client is not None, "Client not found for id %s" % matter.client_id
        client_name = str(client.name)

        # Step 1: Classify the document
        meta = self.classify_document(client_name, raw_text)
        LOGGER.info(
            "discovery_service.ingest: matter_id=%s type=%s propounded_by=%s",
            matter_id, meta.get("request_type"), meta.get("propounded_by"),
        )

        if meta.get("propounded_by") == "our_client":
            raise ValueError(
                "This document appears to be discovery propounded BY our client, "
                "not served ON our client. Only incoming requests can be ingested here."
            )

        # Step 2: Resolve request type
        request_type_str = meta.get("request_type", "interrogatories")
        try:
            request_type = DocumentRequestType(request_type_str)
        except ValueError:
            request_type = DocumentRequestType.interrogatories
            warnings.append("Could not determine request type — defaulting to interrogatories")

        # Step 3: Resolve dates
        service_date_str = meta.get("service_date")
        if propounded_date_override:
            propounded_date = propounded_date_override
        elif service_date_str:
            try:
                propounded_date = date.fromisoformat(service_date_str)
            except ValueError:
                propounded_date = date.today()
                warnings.append("Could not parse service date from document — using today")
        else:
            propounded_date = date.today()
            warnings.append("No service date found in document — using today")

        response_days = meta.get("response_days")
        due_date = self.compute_due_date(propounded_date, response_days)

        look_back_str = meta.get("look_back_date")
        look_back_date = None
        if look_back_str:
            try:
                look_back_date = date.fromisoformat(look_back_str)
            except ValueError:
                warnings.append("Could not parse look-back date")

        # Step 4: Create the parent document record
        doc_repo = DiscoveryDocumentRepository(manager)
        doc = DiscoveryDocument(
            matter_id=matter_id,
            ingested_by_staff_id=staff_id,
            propounded_date=propounded_date,
            due_date=due_date,
            request_type=request_type,
            look_back_date=look_back_date,
        )
        doc_record = doc_repo.insert(doc.model_dump())
        LOGGER.info("discovery_service.ingest: created document id=%s", doc_record.id)

        # Step 5: Extract and persist individual items
        items_raw = self.extract_items(raw_text)
        item_repo = DiscoveryRequestItemRepository(manager)
        created_items: list[DiscoveryRequestItemInDB] = []

        for raw_item in items_raw:
            try:
                item = DiscoveryRequestItem(
                    discovery_request_id=doc_record.id,
                    matter_id=matter_id,
                    request_number=int(raw_item.get("request_number", 0)),
                    source_text=raw_item.get("source_text", ""),
                    ingested_by_staff_id=staff_id,
                )
                item_record = item_repo.insert(item.model_dump())
                created_items.append(item_record)
            except Exception as e:
                LOGGER.warning(
                    "discovery_service.ingest: skipping malformed item #%s: %s",
                    raw_item.get("request_number", "?"), str(e),
                )
                warnings.append("Skipped malformed item #%s" % raw_item.get("request_number", "?"))

        LOGGER.info(
            "discovery_service.ingest: created %s items for document %s",
            len(created_items), doc_record.id,
        )

        return doc_record, created_items, warnings


discovery_service = DiscoveryService()
