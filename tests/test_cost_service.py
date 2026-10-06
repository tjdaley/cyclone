"""
tests/test_cost_service.py - Prices and spend, the Python half.

The pricing arithmetic is SQL and is exercised against real Postgres by
tests/test_cost_sql.py. This file covers what the service decides: the report
window, keeping money exact on the way out, saying when a total is only a
floor, and auditing every price change.

Run:  venv/Scripts/python.exe tests/test_cost_service.py
"""
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from pydantic import ValidationError  # noqa: E402

import services.cost_service as mod  # noqa: E402
from db.models.service_rate import RateMeter, ServiceRateInDB  # noqa: E402
from schemas.costs import CorrectServiceRateRequest, CostGroup, SetServiceRateRequest  # noqa: E402

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    return False


UTC = timezone.utc

# ── The window ───────────────────────────────────────────────────────────────

print("Whole UTC months, inclusive of the last")
check("Sep through Dec runs to 1 January",
      mod.month_window(date(2026, 9, 17), date(2026, 12, 3)),
      (datetime(2026, 9, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)))
check("one month is one month",
      mod.month_window(date(2026, 9, 30), date(2026, 9, 1)),
      (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)))
check_true("a backwards range is refused",
           raises(lambda: mod.month_window(date(2026, 10, 1), date(2026, 9, 1)), ValueError))

# ── The report ───────────────────────────────────────────────────────────────

print("")
print("The report keeps money exact and says when the total is a floor")

# PostgREST sends numeric as JSON NUMBERS, so these arrive as floats.
RAW = [
    {"month": "2026-10-01", "group_key": "anthropic / claude-opus-5",
     "group_label": "anthropic / claude-opus-5", "calls": 1, "failed_calls": 0,
     "no_usage_calls": 0, "unpriced_calls": 0, "input_uncached_tokens": 1000000,
     "input_cache_read_tokens": None, "input_cache_write_tokens": None,
     "output_tokens": 100000, "priced_cost": 17.5, "priced_cost_precise": 17.5},
    {"month": "2026-09-01", "group_key": "gemini / flash", "group_label": "gemini / flash",
     "calls": 3, "failed_calls": 1, "no_usage_calls": 1, "unpriced_calls": 1,
     "input_uncached_tokens": 241, "input_cache_read_tokens": None,
     "input_cache_write_tokens": None, "output_tokens": 68,
     # Three calls at a fraction of a cent: rounded to cents EACH they would sum
     # to 0.00; summed precisely first, they are still under a cent but not zero.
     "priced_cost": 0.0, "priced_cost_precise": 0.004713},
]


class FakeCostRepo:
    def __init__(self, manager):
        pass

    def report(self, date_from, date_to, group):
        FakeCostRepo.asked = (date_from, date_to, group)
        return RAW

    def unpriced(self):
        return [{"vendor": "gemini", "model": "flash", "meter": "output", "calls": 1,
                 "tokens": 68, "first_seen": "2026-09-17T03:34:00+00:00",
                 "last_seen": "2026-09-17T03:34:00+00:00"}]


mod.LlmCostRepository = FakeCostRepo
result = mod.cost_service.report(None, date(2026, 9, 1), date(2026, 10, 1), CostGroup.vendor_model)
check("the window reached the database as whole UTC months",
      FakeCostRepo.asked,
      (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC), "vendor_model"))
check("a float from PostgREST becomes an exact string", result.rows[0].priced_cost, "17.5")
check("the precise total is summed before rounding", result.total_priced_cost_precise, "17.504713")
check("and rounded once, to the cent", result.total_priced_cost, "17.50")
check("unpriced calls are counted", result.total_unpriced_calls, 1)
check("so the report is NOT complete -- the total is a floor", result.complete, False)
check("no-usage calls are counted separately", result.total_no_usage_calls, 1)

RAW[1]["unpriced_calls"] = 0
check("with nothing unpriced it is complete",
      mod.cost_service.report(None, date(2026, 9, 1), date(2026, 10, 1), CostGroup.vendor_model).complete,
      True)
check("the pricing to-do list parses", mod.cost_service.unpriced(None)[0].meter, RateMeter.output)

# ── Setting a price ──────────────────────────────────────────────────────────

print("")
print("Setting a price: exact, trimmed, UTC, and audited")

audited: list = []


class FakeAudit:
    def __init__(self, manager):
        pass

    def log(self, **kwargs):
        audited.append(kwargs)


class FakeStaffRepo:
    def __init__(self, manager):
        pass

    def get_by_supabase_uid(self, uid):
        return NS(id=8) if uid == "uid-atty" else None


def stored(**overrides):
    base = dict(id=4, provider="anthropic", model="claude-opus-5", meter="input_uncached",
                unit_count=1_000_000, price=Decimal("10"), currency="USD",
                effective_from=datetime(2026, 10, 1, tzinfo=UTC), effective_to=None,
                source_url=None, note=None, created_by_staff_id=8,
                created_at=datetime(2026, 9, 17, tzinfo=UTC))
    base.update(overrides)
    return ServiceRateInDB(**base)


class FakeRateRepo:
    last_set = None
    last_update = None
    rows = {4: stored()}

    def __init__(self, manager):
        pass

    def set_from(self, **kwargs):
        FakeRateRepo.last_set = kwargs
        return stored(price=kwargs["price"], effective_from=kwargs["effective_from"])

    def select_one(self, condition):
        return FakeRateRepo.rows.get(condition["id"])

    def update(self, rate_id, data):
        FakeRateRepo.last_update = data
        return FakeRateRepo.rows[rate_id].model_copy(update=data)

    def list_all(self):
        return [stored(effective_to=datetime(2026, 10, 1, tzinfo=UTC),
                       effective_from=datetime(2026, 9, 1, tzinfo=UTC), id=1),
                stored()]


mod.AuditLogger = FakeAudit
mod.StaffRepository = FakeStaffRepo
mod.ServiceRateRepository = FakeRateRepo

request = SetServiceRateRequest(
    provider="  anthropic ", model="claude-opus-5\t", meter="input_uncached",
    price="0.075", effective_from="2026-10-01T00:00:00")
check("provider and model are trimmed -- a pasted space would unprice every call",
      (request.provider, request.model), ("anthropic", "claude-opus-5"))

response = mod.cost_service.set_rate(None, request, "uid-atty")
check("the price reaches the repository as an exact Decimal",
      FakeRateRepo.last_set["price"], Decimal("0.075"))
check("a naive date from the browser is read as UTC",
      FakeRateRepo.last_set["effective_from"].tzinfo, UTC)
check("the staff member is recorded", FakeRateRepo.last_set["staff_id"], 8)
check("and the price goes back out with all six places", response.price, "0.075000")
check("the change is audited", audited[-1]["action"], "service_rate.set")

print("")
print("Bad prices are refused before they reach the database")
for label, payload in (
    ("a negative price", {"price": "-1"}),
    ("a zero unit count", {"unit_count": 0}),
    ("an unknown meter", {"meter": "output_tokens"}),
    ("more than six decimals", {"price": "0.0000001"}),
):
    body = {"provider": "openai", "model": "gpt-5", "meter": "output", "price": "1",
            "effective_from": "2026-09-01T00:00:00Z"}
    body.update(payload)
    check_true("refused: %s" % label, raises(lambda: SetServiceRateRequest(**body), ValidationError))

# ── Correcting a price ───────────────────────────────────────────────────────

print("")
print("Correcting a price sends only what changed")
audited.clear()
fixed = mod.cost_service.correct_rate(
    None, 4, CorrectServiceRateRequest(note="typo: was 100"), "uid-atty")
check("only the note is sent -- never the dates", FakeRateRepo.last_update, {"note": "typo: was 100"})
check("the corrected row comes back", fixed.note, "typo: was 100")
check("audited with the before and the after",
      (audited[-1]["action"], audited[-1]["before_json"]["note"], audited[-1]["after_json"]["note"]),
      ("service_rate.corrected", None, "typo: was 100"))

check_true("a price cannot be cleared",
           raises(lambda: mod.cost_service.correct_rate(
               None, 4, CorrectServiceRateRequest(price=None), None), ValueError))
check("an unknown id is None, for a 404",
      mod.cost_service.correct_rate(None, 999, CorrectServiceRateRequest(note="x"), None), None)

print("")
print("Which price is in force is decided by date -- against a pinned clock")
september, october = FakeRateRepo(None).list_all()
on_oct_5 = datetime(2026, 10, 5, tzinfo=UTC)
on_sep_17 = datetime(2026, 9, 17, tzinfo=UTC)
check("on 5 Oct the closed September price is not in force",
      mod._rate_response(september, on_oct_5).in_force, False)
check("on 5 Oct the open October price is", mod._rate_response(october, on_oct_5).in_force, True)
check("on 17 Sep the September price is in force", mod._rate_response(september, on_sep_17).in_force, True)
check("and October is only scheduled", mod._rate_response(october, on_sep_17).in_force, False)
check("the list endpoint returns every row", len(mod.cost_service.list_rates(None)), 2)

# ── The database's refusal is a 409, not a 500 ───────────────────────────────

print("")
print("An overlapping price is answered, not a server error")
import main as app_main  # noqa: E402

check("23P01 (exclusion_violation) is a 409", app_main._DB_REJECTIONS.get("23P01", (None,))[0], 409)


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all cost-service checks passed")
