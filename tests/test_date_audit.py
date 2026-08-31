"""
One missing date condemns every date in the batch.

Ground truth on First Financial x9260 (27 pages, seven batches) showed the
pattern this is built on:

  * Batches 1-2 and 6: every date correct.
  * Batches 3, 4, 5: nulls — and, mixed in with them, dates that were simply
    wrong. Batch 4 came back with all four of its pages undated.
  * The wrong dates never appeared in a batch that was otherwise clean, and
    they were not detectable on their own: inside the statement period, not
    reliably out of order.
  * Every AMOUNT in those same batches was correct.

So a null is not merely a missing value — it marks a call that stopped reading
the date column, and the dates that call did emit are no more trustworthy than
the ones it dropped. One null triggers a re-read of the whole batch. Only the
date column is re-read: the amounts were right, and re-running the batch to fix
one column would re-roll the dice on data that already ties.
"""
import sys
from datetime import date

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from services.statement_service import (  # noqa: E402
    _apply_date_audit, _date_audit_reason,
)

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


PERIOD_START, PERIOD_END = date(2023, 11, 28), date(2023, 12, 26)


def line(day=None, description="Transfer from DDA (Sweep)", amount=-5000.00):
    return {"transaction_date": day, "description": description, "amount": amount, "flags": []}


# ── 1. What triggers a re-read ───────────────────────────────────────────
print("1. triggers")
clean = [line("2023-11-28"), line("2023-12-04"), line("2023-12-26")]
check("a clean batch is left alone",
      _date_audit_reason(clean, PERIOD_START, PERIOD_END), None)

one_null = [line("2023-11-28"), line(None), line("2023-12-26")]
check("one null triggers", _date_audit_reason(one_null, PERIOD_START, PERIOD_END),
      "1 of 3 line(s) came back with no date")

all_null = [line(None), line(None), line(None), line(None)]
check("batch 4's shape triggers", _date_audit_reason(all_null, PERIOD_START, PERIOD_END),
      "4 of 4 line(s) came back with no date")

check("an empty batch is not a failure", _date_audit_reason([], PERIOD_START, PERIOD_END), None)

# ── 2. Wrong dates with no nulls — the case a null cannot catch ──────────
print("2. dates outside the period")
off_by_a_month = [line("2023-11-28"), line("2023-10-04"), line("2023-12-26")]
check("a month adrift triggers",
      _date_audit_reason(off_by_a_month, PERIOD_START, PERIOD_END),
      "1 line(s) dated outside 2023-11-28 to 2023-12-26")

# These statements post interest after the period closes — 6/02 on a statement
# running 5/01 to 5/31 — so the bound has to have give in it.
posted_late = [line("2023-11-28"), line("2023-12-26"), line("2023-12-30")]
check("a few days past the close is ordinary",
      _date_audit_reason(posted_late, PERIOD_START, PERIOD_END), None)

check("no period, no bound to check against",
      _date_audit_reason(off_by_a_month, None, None), None)

# ── 3. No ordering check, deliberately ───────────────────────────────────
# Credits run to the end of the period, then debits start again at the
# beginning. Dates jump backwards mid-batch on the first batch of every
# statement of this shape, so an ordering check would fire on almost all of
# them and catch nothing we have seen.
print("3. a legitimate backwards jump")
sections = [line("2023-12-26"), line("2023-11-28"), line("2023-11-29")]
check("credits then debits is not a fault",
      _date_audit_reason(sections, PERIOD_START, PERIOD_END), None)

# ── 4. Taking the second reading ─────────────────────────────────────────
print("4. applying the re-read")
lines = [line("2023-12-05"), line(None), line("2023-12-04")]
changed = _apply_date_audit(lines, [
    {"index": 1, "transaction_date": "2023-12-04", "date_provenance": "printed"},
    {"index": 2, "transaction_date": "2023-12-04", "date_provenance": "printed"},
    {"index": 3, "transaction_date": "2023-12-04", "date_provenance": "printed"},
])
check("two moved, one confirmed", changed, 2)
check("the wrong one corrected", lines[0]["transaction_date"], "2023-12-04")
check("the missing one filled", lines[1]["transaction_date"], "2023-12-04")
check("the correct one untouched by a flag", lines[2]["flags"], [])
check("changes are recorded", [f["code"] for f in lines[0]["flags"]], ["DATE_REREAD"])
check("the record names both values",
      (lines[0]["flags"][0]["from"], lines[0]["flags"][0]["to"]), ("2023-12-05", "2023-12-04"))
check("provenance carried over", lines[1]["date_provenance"], "printed")

# ── 5. Matched by position, because descriptions repeat ──────────────────
# Three identical "Transfer from DDA (Sweep)" of $5,000.00 on one page is
# ordinary on this statement. Matching on description or amount would date them
# confidently and wrongly.
print("5. identical lines, different dates")
lines = [line(None), line(None), line(None)]
_apply_date_audit(lines, [
    {"index": 1, "transaction_date": "2023-12-04"},
    {"index": 2, "transaction_date": "2023-12-11"},
    {"index": 3, "transaction_date": "2023-12-18"},
])
check("each got its own date",
      [line_["transaction_date"] for line_ in lines],
      ["2023-12-04", "2023-12-11", "2023-12-18"])

# ── 6. The second read may also find nothing ─────────────────────────────
print("6. a line the statement really does not date")
lines = [line("2023-12-05")]
changed = _apply_date_audit(lines, [{"index": 1, "transaction_date": None}])
check("the second reading wins even when it is empty", lines[0]["transaction_date"], None)
check("and says so", lines[0]["flags"][0]["note"].endswith("from 2023-12-05 to (none)."), True)
check("counted as a change", changed, 1)

print("7. an answer that skips a line leaves it alone")
lines = [line("2023-12-05"), line("2023-12-06")]
_apply_date_audit(lines, [{"index": 2, "transaction_date": "2023-12-07"}])
check("line 1 untouched", lines[0]["transaction_date"], "2023-12-05")
check("line 2 taken", lines[1]["transaction_date"], "2023-12-07")

print("8. a malformed index is ignored, not fatal")
lines = [line("2023-12-05")]
changed = _apply_date_audit(lines, [{"index": "x", "transaction_date": "2023-12-09"},
                                    {"transaction_date": "2023-12-09"}])
check("nothing applied", changed, 0)
check("original kept", lines[0]["transaction_date"], "2023-12-05")

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
