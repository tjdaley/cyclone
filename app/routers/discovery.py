"""
app/routers/discovery.py - Discovery request ingestion and response endpoints.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.discovery import DiscoveryRequest, DiscoveryRequestStatus, DiscoveryRequestType
from db.repositories.discovery import DiscoveryRequestRepository, DiscoveryResponseRepository
from dependencies import get_db_manager, require_role
from schemas.discovery import (
    DiscoveryIngestRequest,
    DiscoveryIngestResponse,
    DiscoveryRequestResponse,
    DiscoveryResponseSchema,
    DiscoveryResponseUpdateRequest,
)
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])

_INGEST_SYSTEM_PROMPT = """\
You are a legal discovery assistant. Parse the following discovery requests into a JSON array.
Each element must have these fields:
- request_type: "interrogatory" | "rfa" | "rfp" | "witness_list"
- request_number: integer (sequential number from opposing counsel)
- source_text: verbatim text of the individual request

Respond ONLY with a valid JSON array. No markdown, no explanation.
If the type cannot be determined, use "interrogatory" as default.\
"""


@router.get("/{matter_id}/requests", response_model=list[DiscoveryRequestResponse])
def list_requests(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> list[DiscoveryRequestResponse]:
    """Return all discovery requests for a matter."""
    repo = DiscoveryRequestRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [DiscoveryRequestResponse(**r.model_dump()) for r in records]


@router.post("/ingest", response_model=DiscoveryIngestResponse, status_code=201)
def ingest_discovery(
    body: DiscoveryIngestRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> DiscoveryIngestResponse:
    """
    Parse and persist a batch of discovery requests via LLM.

    The raw text is sent to the LLM for segmentation and classification.
    All parsed items are committed to the database in one batch.
    """
    LOGGER.info("discovery.ingest: matter_id=%s", body.matter_id)
    response_text = llm_service.complete(_INGEST_SYSTEM_PROMPT, body.raw_text)
    try:
        parsed_items: list[dict] = json.loads(response_text)
        if not isinstance(parsed_items, list):
            raise ValueError("LLM did not return a list")
    except (json.JSONDecodeError, ValueError) as e:
        LOGGER.warning("discovery.ingest: LLM parse failure: %s", str(e))
        raise HTTPException(status_code=422, detail="Could not parse discovery text — please review and retry") from e

    repo = DiscoveryRequestRepository(manager)
    created: list[DiscoveryRequestResponse] = []
    for item in parsed_items:
        try:
            dr = DiscoveryRequest(
                matter_id=body.matter_id,
                request_type=DiscoveryRequestType(item.get("request_type", "interrogatory")),
                request_number=int(item.get("request_number", 1)),
                source_text=item.get("source_text", ""),
                ingested_by_staff_id=body.staff_id,
            )
            record = repo.insert(dr.model_dump())
            created.append(DiscoveryRequestResponse(**record.model_dump()))
        except Exception as e:
            LOGGER.warning("discovery.ingest: skipping malformed item: %s", str(e))
            continue

    return DiscoveryIngestResponse(
        matter_id=body.matter_id,
        item_count=len(created),
        items=created,
    )


@router.get("/responses/{request_id}", response_model=DiscoveryResponseSchema)
def get_response(
    request_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> DiscoveryResponseSchema:
    """Return the response record for a discovery request."""
    repo = DiscoveryResponseRepository(manager)
    record = repo.get_by_request(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Response not found")
    return DiscoveryResponseSchema(**record.model_dump())


@router.patch("/responses/{response_id}", response_model=DiscoveryResponseSchema)
def update_response(
    response_id: int,
    body: DiscoveryResponseUpdateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> DiscoveryResponseSchema:
    """
    Update a discovery response.

    Clients submit their draft. Attorneys/paralegals add objections, notes,
    and mark final. The ``last_updated_by_uid`` field tracks the last editor.
    """
    repo = DiscoveryResponseRepository(manager)
    if repo.select_one(condition={"id": response_id}) is None:
        raise HTTPException(status_code=404, detail="Response not found")
    updates = body.model_dump(exclude_none=True)
    updates["last_updated_by_uid"] = request.state.supabase_uid
    LOGGER.info("discovery.update_response: response_id=%s", response_id)
    record = repo.update(response_id, updates)
    return DiscoveryResponseSchema(**record.model_dump())
