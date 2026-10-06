"""
app/services/cost_service.py - Prices and spend.

The arithmetic lives in SQL (migration 041): pricing a call against the rate in
force on its date, and grouping by month. This service chooses the window,
keeps money exact on the way out, and audits every price change -- a price is a
number that moves every cost figure built on it, so who changed it and from what
has to be answerable.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from db.repositories.service_rate import LlmCostRepository, ServiceRateRepository
from db.repositories.staff import StaffRepository
from db_handler import DatabaseManager
from schemas.costs import (
    CorrectServiceRateRequest,
    CostGroup,
    CostReportResponse,
    CostReportRow,
    ServiceRateResponse,
    SetServiceRateRequest,
    UnpricedMeterResponse,
)
from services.audit_logger import AuditLogger
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

_ZERO = Decimal("0")


def month_window(from_month: date, to_month: date) -> tuple[datetime, datetime]:
    """
    Whole UTC months, ``from_month`` through ``to_month`` inclusive.

    UTC because vendor invoices are UTC. A window that rolled over at local
    midnight would disagree with the invoice by a few hours at every month edge,
    and "why does your September not match Anthropic's September" is the first
    question a cost report gets.

    :raises ValueError: When the range runs backwards.
    """
    if (to_month.year, to_month.month) < (from_month.year, from_month.month):
        raise ValueError("The report range ends before it starts")
    start = datetime(from_month.year, from_month.month, 1, tzinfo=timezone.utc)
    year, month = (to_month.year + 1, 1) if to_month.month == 12 else (to_month.year, to_month.month + 1)
    return start, datetime(year, month, 1, tzinfo=timezone.utc)


def _money(value: Any) -> str:
    """A numeric from PostgREST as an exact string. Through str, never float."""
    return str(Decimal(str(value if value is not None else 0)))


def _rate_response(row: Any, now: Optional[datetime] = None) -> ServiceRateResponse:
    now = now or datetime.now(timezone.utc)
    in_force = row.effective_from <= now and (row.effective_to is None or now < row.effective_to)
    return ServiceRateResponse(
        id=row.id, provider=row.provider, model=row.model, meter=row.meter,
        unit_count=row.unit_count, price=f"{row.price:.6f}", currency=row.currency,
        effective_from=row.effective_from, effective_to=row.effective_to,
        in_force=in_force, source_url=row.source_url, note=row.note,
        created_at=row.created_at,
    )


class CostService:
    """Prices, and spend priced against them."""

    def list_rates(self, manager: DatabaseManager) -> list[ServiceRateResponse]:
        """Every price, with the ones in force now marked."""
        now = datetime.now(timezone.utc)
        return [_rate_response(r, now) for r in ServiceRateRepository(manager).list_all()]

    def set_rate(
        self, manager: DatabaseManager, request: SetServiceRateRequest, supabase_uid: Optional[str],
    ) -> ServiceRateResponse:
        """A new price from a date, closing the one in force. Audited."""
        staff = StaffRepository(manager).get_by_supabase_uid(supabase_uid) if supabase_uid else None
        effective_from = request.effective_from
        if effective_from.tzinfo is None:
            # A date picked in a browser arrives naive. Read it as UTC, the zone
            # every figure in the ledger and every vendor invoice uses.
            effective_from = effective_from.replace(tzinfo=timezone.utc)

        row = ServiceRateRepository(manager).set_from(
            provider=request.provider, model=request.model, meter=request.meter,
            price=request.price, unit_count=request.unit_count,
            effective_from=effective_from, source_url=request.source_url,
            note=request.note, staff_id=staff.id if staff else None,
        )
        AuditLogger(manager).log(
            action="service_rate.set", entity_type="service_rate", entity_id=str(row.id),
            supabase_uid=supabase_uid, after_json=row.model_dump(mode="json"),
        )
        LOGGER.info("cost_service: price set id=%s provider=%s model=%s meter=%s",
                    row.id, row.provider, row.model, row.meter.value)
        return _rate_response(row)

    def correct_rate(
        self, manager: DatabaseManager, rate_id: int, request: CorrectServiceRateRequest,
        supabase_uid: Optional[str],
    ) -> Optional[ServiceRateResponse]:
        """
        Fix the price, source or note of a price already entered. Audited.

        Fields left out of the request are untouched; a field sent as null is
        cleared. Hence ``exclude_unset``, not ``exclude_none``.

        :return: The corrected price, or None when no such id exists.
        """
        repo = ServiceRateRepository(manager)
        before = repo.select_one(condition={"id": rate_id})
        if before is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        if not changes:
            return _rate_response(before)
        if "price" in changes and changes["price"] is None:
            raise ValueError("A price cannot be cleared; enter the corrected figure")
        # Only the changed columns are sent: update() is a PostgREST PATCH, so
        # the effective dates are never re-sent and cannot be moved by accident.
        row = repo.update(rate_id, changes)
        AuditLogger(manager).log(
            action="service_rate.corrected", entity_type="service_rate", entity_id=str(rate_id),
            supabase_uid=supabase_uid, before_json=before.model_dump(mode="json"),
            after_json=row.model_dump(mode="json"),
        )
        return _rate_response(row)

    def report(
        self, manager: DatabaseManager, from_month: date, to_month: date, group: CostGroup,
    ) -> CostReportResponse:
        """Spend by month and ``group``, with the gaps in it counted."""
        date_from, date_to = month_window(from_month, to_month)
        raw = LlmCostRepository(manager).report(date_from, date_to, group.value)

        rows: list[CostReportRow] = []
        precise_total = _ZERO
        calls = unpriced = no_usage = 0
        for r in raw:
            row = CostReportRow(
                month=r["month"], group_key=r["group_key"] or "", group_label=r["group_label"] or "",
                calls=r["calls"], failed_calls=r["failed_calls"],
                no_usage_calls=r["no_usage_calls"], unpriced_calls=r["unpriced_calls"],
                input_uncached_tokens=r.get("input_uncached_tokens"),
                input_cache_read_tokens=r.get("input_cache_read_tokens"),
                input_cache_write_tokens=r.get("input_cache_write_tokens"),
                output_tokens=r.get("output_tokens"),
                priced_cost=_money(r["priced_cost"]),
                priced_cost_precise=_money(r["priced_cost_precise"]),
            )
            rows.append(row)
            # Summed from the six-place figure and rounded ONCE, so the total is
            # not a sum of rounded cents that drifts from the true figure.
            precise_total += Decimal(row.priced_cost_precise)
            calls += row.calls
            unpriced += row.unpriced_calls
            no_usage += row.no_usage_calls

        return CostReportResponse(
            date_from=date_from, date_to=date_to, group=group, rows=rows,
            total_calls=calls, total_unpriced_calls=unpriced, total_no_usage_calls=no_usage,
            total_priced_cost=str(precise_total.quantize(Decimal("0.01"))),
            total_priced_cost_precise=str(precise_total.quantize(Decimal("0.000001"))),
            complete=unpriced == 0,
        )

    def unpriced(self, manager: DatabaseManager) -> list[UnpricedMeterResponse]:
        """The pricing to-do list."""
        return [UnpricedMeterResponse(**r) for r in LlmCostRepository(manager).unpriced()]


cost_service = CostService()
