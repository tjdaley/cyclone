"""
app/routers/billing.py - Billing entry, cycle, NL parse, and balance endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.billing_entry import BillingEntry
from db.repositories.billing_cycle import BillingCycleRepository
from db.repositories.billing_entry import BillingEntryRepository
from dependencies import get_db_manager, require_role
from schemas.billing import (
    BillingCycleCreateRequest,
    BillingCycleResponse,
    BillingEntryCreateRequest,
    BillingEntryResponse,
    BillingEntryUpdateRequest,
    ClientBalanceResponse,
    CloseCycleRequest,
    NLBillingParseRequest,
    NLBillingParseResponse,
)
from schemas.common import DeletedResponse
from services.billing_service import BillingService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


# ── Billing Entries ────────────────────────────────────────────────────────

@router.get("/entries", response_model=list[BillingEntryResponse])
def list_entries_by_matter(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[BillingEntryResponse]:
    """Return all billing entries for a matter."""
    repo = BillingEntryRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [BillingEntryResponse(**r.model_dump()) for r in records]


@router.post("/entries", response_model=BillingEntryResponse, status_code=201)
def create_entry(
    body: BillingEntryCreateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> BillingEntryResponse:
    """
    Create a billing entry.

    ``staff_id`` is resolved from the authenticated user if not provided.
    ``entry_date`` is always set to today. ``invoice_date`` defaults to today
    if not provided. For pro-bono matters, TIME entry amounts are zeroed.
    """
    from datetime import date as date_type
    from db.repositories.staff import StaffRepository

    today = date_type.today()

    # Resolve staff_id from JWT if not provided
    staff_id = body.staff_id
    if staff_id is None:
        staff_repo = StaffRepository(manager)
        staff = staff_repo.get_by_supabase_uid(request.state.supabase_uid)
        if staff is None:
            raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")
        staff_id = staff.id

    entry_data = body.model_dump()
    entry_data["staff_id"] = staff_id
    entry_data["entry_date"] = today
    entry_data["invoice_date"] = body.invoice_date or today

    entry = BillingEntry(**entry_data)
    svc = BillingService(manager)
    created = svc.create_entry(entry, supabase_uid=request.state.supabase_uid)
    return BillingEntryResponse(**created.model_dump())


@router.patch("/entries/{entry_id}", response_model=BillingEntryResponse)
def update_entry(
    entry_id: int,
    body: BillingEntryUpdateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> BillingEntryResponse:
    """Partially update a billing entry. Cannot edit a billed entry."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    svc = BillingService(manager)
    try:
        updated = svc.update_entry(entry_id, updates, supabase_uid=request.state.supabase_uid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return BillingEntryResponse(**updated.model_dump())


@router.delete("/entries/{entry_id}", response_model=DeletedResponse)
def delete_entry(
    entry_id: int,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> DeletedResponse:
    """Delete an unbilled billing entry."""
    svc = BillingService(manager)
    try:
        svc.delete_entry(entry_id, supabase_uid=request.state.supabase_uid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return DeletedResponse(id=entry_id)


# ── Natural Language Parse ─────────────────────────────────────────────────

@router.post("/parse", response_model=NLBillingParseResponse)
def parse_natural_language(
    body: NLBillingParseRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> NLBillingParseResponse:
    """
    Parse a natural-language billing description into structured fields.

    Returns a preview card — not committed until the attorney POSTs to
    /api/v1/billing/entries. If ``matter_id`` is provided, the rate is
    resolved from the matter/staff rate chain and the amount is computed.
    """
    svc = BillingService(manager)
    try:
        parsed = svc.parse_natural_language(body.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Resolve rate if we know the matter and the logged-in staff member
    rate = None
    amount = None
    if body.matter_id and parsed.entry_type == "time" and parsed.hours:
        try:
            from db.repositories.staff import StaffRepository
            staff_repo = StaffRepository(manager)
            staff = staff_repo.get_by_supabase_uid(request.state.supabase_uid)
            if staff:
                rate = svc.resolve_rate(body.matter_id, staff.id)
                amount = round(parsed.hours * rate, 2)
        except Exception:
            pass  # Rate resolution failed — preview without rate

    confidence = "high" if parsed.hours and parsed.description else "low"
    return NLBillingParseResponse(
        entry_type=parsed.entry_type,
        description=parsed.description or "",
        hours=parsed.hours,
        rate=rate,
        amount=amount,
        invoice_date=parsed.invoice_date,
        billable=parsed.billable,
        confidence=confidence,
    )


# ── Client Balance ─────────────────────────────────────────────────────────

@router.get("/balance/{matter_id}", response_model=ClientBalanceResponse)
def get_client_balance(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> ClientBalanceResponse:
    """
    Return the client financial balance for a matter.

    Formula: trust_balance − unbilled_time − unbilled_expenses.
    Status: green / yellow / red based on refresh trigger threshold.
    """
    svc = BillingService(manager)
    try:
        balance = svc.get_client_balance(matter_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return ClientBalanceResponse(**balance)


# ── Billing Cycles ─────────────────────────────────────────────────────────

@router.get("/cycles", response_model=list[BillingCycleResponse])
def list_cycles_by_matter(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> list[BillingCycleResponse]:
    """Return all billing cycles for a matter."""
    repo = BillingCycleRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [BillingCycleResponse(**r.model_dump()) for r in records]


@router.post("/cycles", response_model=BillingCycleResponse, status_code=201)
def create_cycle(
    body: BillingCycleCreateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> BillingCycleResponse:
    """Open a new billing cycle for a matter."""
    repo = BillingCycleRepository(manager)
    LOGGER.info("billing.create_cycle: matter_id=%s", body.matter_id)
    record = repo.insert(body.model_dump())
    return BillingCycleResponse(**record.model_dump())


@router.post("/cycles/{cycle_id}/close", response_model=BillingCycleResponse)
def close_cycle(
    cycle_id: int,
    body: CloseCycleRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> BillingCycleResponse:
    """
    Close a billing cycle.

    Marks all entries in the cycle as billed, sets status to closed,
    and writes an audit log entry.
    """
    svc = BillingService(manager)
    try:
        svc.close_billing_cycle(
            cycle_id=cycle_id,
            staff_id=body.staff_id,
            supabase_uid=request.state.supabase_uid,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    repo = BillingCycleRepository(manager)
    record = repo.select_one(condition={"id": cycle_id})
    return BillingCycleResponse(**record.model_dump())
