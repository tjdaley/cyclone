"""
app/db/repositories/service_rate.py - Prices, and the cost reports built on them.

Two SQL functions do the work that PostgREST's filter syntax cannot: setting a
new price atomically (closing the old one in the same transaction), and
grouping the priced ledger by month. Both are called through
``manager.client.rpc`` -- the same raw-client route ``financial.py`` takes for
queries the condition dict cannot express -- and both are closed to the anon
role in the migration.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from db.models.service_rate import RateMeter, ServiceRateInDB
from db_handler import BaseRepository, DatabaseManager


class ServiceRateRepository(BaseRepository[ServiceRateInDB]):
    """``service_rates``: read, set from a date, and correct."""

    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "service_rates", ServiceRateInDB)

    def list_all(self) -> list[ServiceRateInDB]:
        """Every price ever entered, grouped for reading: model, meter, newest first."""
        rows = (
            self.manager.client.table(self.table_name)
            .select("*")
            .order("provider").order("model").order("meter")
            .order("effective_from", desc=True)
            .execute()
        ).data or []
        return [ServiceRateInDB(**row) for row in rows]

    def set_from(
        self,
        *,
        provider: str,
        model: str,
        meter: RateMeter,
        price: Decimal,
        unit_count: int,
        effective_from: datetime,
        source_url: Optional[str],
        note: Optional[str],
        staff_id: Optional[int],
    ) -> ServiceRateInDB:
        """
        A new price from ``effective_from``, closing whatever was in force.

        One function call, one transaction: two PostgREST calls could leave an
        overlap (refused) or a gap (calls in it silently unpriced).

        :raises postgrest.exceptions.APIError: 23P01 when a price already starts
            on or after ``effective_from``; the message says which. Answered as a
            409 by ``main._db_rejection_handler``.
        """
        data = self.manager.client.rpc("set_service_rate", {
            "p_provider": provider,
            "p_model": model,
            "p_meter": meter.value,
            # As a string: a JSON float would carry binary rounding error into
            # a numeric(14,6) column.
            "p_price": str(price),
            "p_unit_count": unit_count,
            "p_effective_from": effective_from.isoformat(),
            "p_source_url": source_url,
            "p_note": note,
            "p_staff_id": staff_id,
        }).execute().data
        row = data[0] if isinstance(data, list) else data
        return ServiceRateInDB(**row)


class LlmCostRepository:
    """
    Read-only access to the priced ledger.

    Not a ``BaseRepository``: it reads a view and a function, not a table with a
    model of its own, and ``schema_check`` diffs only BaseRepository tables.
    """

    def __init__(self, manager: DatabaseManager):
        self.manager = manager

    def report(self, date_from: datetime, date_to: datetime, group: str) -> list[dict[str, Any]]:
        """Spend by month and one dimension, from ``llm_cost_report``."""
        return self.manager.client.rpc("llm_cost_report", {
            "p_from": date_from.isoformat(),
            "p_to": date_to.isoformat(),
            "p_group": group,
        }).execute().data or []

    def unpriced(self) -> list[dict[str, Any]]:
        """Every model meter that carried tokens with no price in force."""
        return (
            self.manager.client.table("llm_unpriced_meters")
            .select("*")
            .order("last_seen", desc=True)
            .execute()
        ).data or []
