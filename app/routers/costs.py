"""
app/routers/costs.py - Admin > Costs: prices and what running Cyclone spends.

Thin. Pricing arithmetic is SQL (migration 041); decisions are in
``cost_service``. Admin only -- a firm's spend is the firm's business.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from dependencies import get_db_manager, require_role
from schemas.costs import (
    CorrectServiceRateRequest,
    CostGroup,
    CostReportResponse,
    ServiceRateResponse,
    SetServiceRateRequest,
    UnpricedMeterResponse,
)
from services.cost_service import cost_service

router = APIRouter(prefix="/api/v1/costs", tags=["costs"])


@router.get("/rates", response_model=list[ServiceRateResponse])
def list_rates(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> list[ServiceRateResponse]:
    """Every price entered, with the ones in force now marked."""
    return cost_service.list_rates(manager)


@router.post("/rates", response_model=ServiceRateResponse, status_code=201)
def set_rate(
    body: SetServiceRateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> ServiceRateResponse:
    """
    A new price from a date. Closes the price in force at that date.

    409 when a price for the same meter already starts on or after that date --
    answered by ``main._db_rejection_handler`` with the database's own sentence.
    """
    return cost_service.set_rate(manager, body, getattr(request.state, "supabase_uid", None))


@router.patch("/rates/{rate_id}", response_model=ServiceRateResponse)
def correct_rate(
    rate_id: int,
    body: CorrectServiceRateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> ServiceRateResponse:
    """Correct a typo in a price's figure, source or note. Dates are not editable here."""
    try:
        result = cost_service.correct_rate(
            manager, rate_id, body, getattr(request.state, "supabase_uid", None))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if result is None:
        raise HTTPException(status_code=404, detail="No such price")
    return result


@router.get("/report", response_model=CostReportResponse)
def report(
    from_month: date = Query(..., description="Any day in the first month"),
    to_month: date = Query(..., description="Any day in the last month (inclusive)"),
    group: CostGroup = Query(CostGroup.vendor_model),
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> CostReportResponse:
    """Spend by UTC month and one breakdown."""
    try:
        return cost_service.report(manager, from_month, to_month, group)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/unpriced", response_model=list[UnpricedMeterResponse])
def unpriced(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> list[UnpricedMeterResponse]:
    """Model meters that carried tokens with no price in force. The to-do list."""
    return cost_service.unpriced(manager)
