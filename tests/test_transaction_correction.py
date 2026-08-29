"""
Correcting an ingested line, and what it does to the statement's balance check.

The rule under test: nothing is quietly overwritten. Every change appends a
MANUAL_CORRECTION flag naming the field, both values, and the person, so the
original stays recoverable from the record that goes into an exhibit.
"""
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from db.models.financial import DateProvenance  # noqa: E402
import services.statement_service as mod  # noqa: E402
from services.statement_service import statement_service  # noqa: E402

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Row(dict):
    __getattr__ = dict.get


def transaction(id, statement_id=10, amount="-6.49", description="COFEE SHOP", flags=None):
    return Row(id=id, statement_id=statement_id, financial_account_id=1,
               amount=Decimal(amount), description=description,
               transaction_date=date(2026, 1, 6), posted_date=None,
               counterparty=None, location=None, running_balance=None,
               bates_number="GS2775", physical_page_number=1,
               date_provenance=DateProvenance.printed, flags=flags or [])


def statement(id=10, beginning="300.03", ending="0.03", flags=None):
    return Row(id=id, financial_account_id=1, matter_id=1,
               beginning_balance=Decimal(beginning), ending_balance=Decimal(ending),
               computed_ending_balance=None, reconciled=False,
               reconciliation_delta=None, flags=flags or [])


class FakeTransactions:
    def __init__(self, rows): self.rows = {r["id"]: r for r in rows}
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_statement(self, sid): return [r for r in self.rows.values() if r["statement_id"] == sid]
    def update(self, id, patch):
        self.rows[id].update(patch)
        return self.rows[id]


class FakeStatements:
    def __init__(self, rows): self.rows = {r["id"]: r for r in rows}
    def select_one(self, condition): return self.rows.get(condition["id"])
    def update(self, id, patch): self.rows[id].update(patch); return self.rows[id]
    def find_overlapping(self, a, s, e):
        return [r for r in self.get_by_account(a)
                if r.get('review_status') != 'rejected'
                and not (r['period_start'] == s and r['period_end'] == e)
                and r['period_start'] <= e and s <= r['period_end']]

def wire(transactions, statements):
    t, s = FakeTransactions(transactions), FakeStatements(statements)
    mod.FinancialAccountTransactionRepository = lambda m: t
    mod.FinancialAccountStatementRepository = lambda m: s
    return t, s


# ── 1. A description fix leaves a readable trail ─────────────────────────
print("1. description corrected")
t, s = wire([transaction(1)], [statement()])
tx, st = statement_service.correct_transaction(
    None, 1, {"description": "COFFEE SHOP"}, staff_id=3, staff_name="Tom Daley",
    reason="corrected against page 1",
)
check("value applied", tx["description"], "COFFEE SHOP")
check("one flag added", len(tx["flags"]), 1)
flag = tx["flags"][0]
check("code", flag["code"], "MANUAL_CORRECTION")
check("severity is info", flag["severity"], "info")
check("field named", flag["field_path"], "description")
check("reads as a sentence", flag["note"],
      'Tom Daley changed the description from "COFEE SHOP" to "COFFEE SHOP".')
check("original recoverable", flag["from"], "COFEE SHOP")
check("attributed", flag["by_staff_id"], 3)
check("reason kept", flag["reason"], "corrected against page 1")
check("statement untouched", st, None)

# ── 2. An amount fix re-reconciles the statement ─────────────────────────
print("2. amount corrected into balance")
# 300.03 + 100 + 100 + 0.03 - 500.03 = 0.03. Break one line, then fix it.
lines = [
    transaction(1, amount="100.00"), transaction(2, amount="100.00"),
    transaction(3, amount="0.03"), transaction(4, amount="-500.00"),
]
t, s = wire(lines, [statement()])
tx, st = statement_service.correct_transaction(
    None, 4, {"amount": "-500.03"}, staff_id=3, staff_name="Tom Daley",
)
check("amount stored as Decimal", tx["amount"], Decimal("-500.03"))
check("statement returned", st is not None, True)
check("now reconciles", st["reconciled"], True)
check("delta cleared", st["reconciliation_delta"], Decimal("0.00"))
check("computed close", st["computed_ending_balance"], Decimal("0.03"))
check("money reads naturally", tx["flags"][0]["note"],
      "Tom Daley changed the amount from -$500.00 to -$500.03.")

# ── 3. A stale UNRECONCILED flag is removed, not stacked ─────────────────
print("3. stale flag replaced")
stale = [{"code": "UNRECONCILED", "severity": "warn", "field_path": "balances.ending_balance",
          "note": "old text"}]
t, s = wire(
    [transaction(1, amount="100.00"), transaction(2, amount="100.00"),
     transaction(3, amount="0.03"), transaction(4, amount="-500.00")],
    [statement(flags=stale)],
)
_, st = statement_service.correct_transaction(
    None, 4, {"amount": "-500.03"}, staff_id=3, staff_name="Tom Daley",
)
check("no UNRECONCILED left", [f["code"] for f in st["flags"]], [])

# ── 4. Still out of balance: the flag is rewritten, not dropped ──────────
print("4. still unreconciled after the edit")
t, s = wire(
    [transaction(1, amount="100.00"), transaction(2, amount="100.00"),
     transaction(3, amount="0.03"), transaction(4, amount="-500.00")],
    [statement(flags=stale)],
)
_, st = statement_service.correct_transaction(
    None, 4, {"amount": "-499.00"}, staff_id=3, staff_name="Tom Daley",
)
check("still flagged", [f["code"] for f in st["flags"]], ["UNRECONCILED"])
check("not reconciled", st["reconciled"], False)
check("note refreshed", "old text" not in st["flags"][0]["note"], True)

# ── 5. A no-op edit is refused ───────────────────────────────────────────
print("5. nothing actually changed")
t, s = wire([transaction(1)], [statement()])
try:
    statement_service.correct_transaction(
        None, 1, {"description": "COFEE SHOP"}, staff_id=3, staff_name="Tom Daley")
    check("refused", False, True)
except ValueError as e:
    check("refused", str(e), "Nothing changed")

# ── 6. Corrections accumulate rather than overwrite ──────────────────────
print("6. second correction appends")
t, s = wire([transaction(1)], [statement()])
statement_service.correct_transaction(
    None, 1, {"description": "COFFEE SHOP"}, staff_id=3, staff_name="Tom Daley")
tx, _ = statement_service.correct_transaction(
    None, 1, {"counterparty": "Coffee Shop"}, staff_id=3, staff_name="Tom Daley")
check("two flags", len(tx["flags"]), 2)
check("first still there", tx["flags"][0]["field_path"], "description")
check("blank reads as blank", tx["flags"][1]["note"],
      'Tom Daley changed the counterparty from (blank) to "Coffee Shop".')

# ── 7. A field nobody may edit is ignored ────────────────────────────────
print("7. non-correctable field")
t, s = wire([transaction(1)], [statement()])
try:
    statement_service.correct_transaction(
        None, 1, {"statement_id": 99}, staff_id=3, staff_name="Tom Daley")
    check("refused", False, True)
except ValueError as e:
    check("refused", str(e), "Nothing changed")
check("statement_id untouched", t.rows[1]["statement_id"], 10)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
