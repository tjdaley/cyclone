"""
Three deletes, and why they are not the same delete.

Nothing in this database is the original record — the Bates-stamped PDF in
Storage is. So statements and accounts are removed outright: a mistake costs a
re-import, and a half-deleted account sitting in an inventory is worse than one
that is gone.

A single line is different. Dropping one asserts something about the document —
"this is not printed there" — and changes whether the statement reconciles. So a
line is flagged and hidden rather than destroyed, with the person's name on it,
and swept when the matter closes.

The tell that a deletion was legitimate: extraction invented the line, so the
statement reconciles BETTER without it.
"""
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from db.models.financial import AccountOwnership, PropertyCharacter  # noqa: E402
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


class FakeAccounts:
    def __init__(self, rows=()): self.rows = {r["id"]: Row(r) for r in rows}; self.deleted = []
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_matter(self, m): return [r for r in self.rows.values() if r["matter_id"] == m]
    def update(self, i, p): self.rows[i].update(p); return self.rows[i]
    def delete(self, i, id_column="id"): self.deleted.append(i); self.rows.pop(i, None); return True


class FakeStatements:
    def __init__(self, rows=()): self.rows = {r["id"]: Row(r) for r in rows}; self.deleted = []
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_account(self, a): return [r for r in self.rows.values() if r["financial_account_id"] == a]
    def update(self, i, p): self.rows[i].update(p); return self.rows[i]
    def delete(self, i, id_column="id"): self.deleted.append(i); self.rows.pop(i, None); return True


class FakeTransactions:
    def __init__(self, rows=()): self.rows = {r["id"]: Row(r) for r in rows}
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_statement(self, sid, include_deleted=False):
        rows = [r for r in self.rows.values() if r["statement_id"] == sid]
        return rows if include_deleted else [r for r in rows if r["deleted_at"] is None]
    def get_by_account(self, aid, include_deleted=False):
        rows = [r for r in self.rows.values() if r["financial_account_id"] == aid]
        return rows if include_deleted else [r for r in rows if r["deleted_at"] is None]
    def update(self, i, p): self.rows[i].update(p); return self.rows[i]


def wire(accounts=(), statements=(), transactions=()):
    a, s, t = FakeAccounts(accounts), FakeStatements(statements), FakeTransactions(transactions)
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    return a, s, t


def account(id=1, matter=1, ownership=AccountOwnership.unknown, character=None,
            purpose=None, notes=None, antecedent=None):
    return {"id": id, "matter_id": matter, "institution": "First Financial Bank",
            "account_number_last4": "4527", "account_type": "savings",
            "ownership": ownership, "property_character": character,
            "purpose": purpose, "notes": notes, "antecedent_account_id": antecedent}


def statement(id=10, account_id=1, beginning="0.00", ending="300.00"):
    return {"id": id, "financial_account_id": account_id, "matter_id": 1,
            "period_start": date(2024, 1, 1), "period_end": date(2024, 1, 31),
            "beginning_balance": Decimal(beginning), "ending_balance": Decimal(ending),
            "computed_ending_balance": None, "reconciled": False,
            "reconciliation_delta": None, "flags": [], "review_status": "auto_accepted"}


def line(id, amount, statement_id=10, account_id=1, deleted=None):
    return {"id": id, "statement_id": statement_id, "financial_account_id": account_id,
            "amount": Decimal(amount), "description": "LINE %d" % id, "flags": [],
            "bates_number": None, "deleted_at": deleted}


# ── 1. Extraction read a line twice; dropping it fixes the balance ───────
print("1. dropping an invented line")
# Printed close is 300.00, but extraction produced 100+100+100+100 = 400.
a, s, t = wire([account()], [statement()],
               [line(1, "100.00"), line(2, "100.00"), line(3, "100.00"), line(4, "100.00")])
tx, st = statement_service.delete_transaction(
    None, 4, staff_id=3, staff_name="Tom Daley", reason="duplicate of line 3")
check("hidden, not destroyed", tx["id"] in t.rows, True)
check("marked deleted", tx["deleted_at"] is not None, True)
check("attributed", tx["deleted_by_staff_id"], 3)
check("reason kept", tx["deletion_reason"], "duplicate of line 3")
check("trail left", [f["code"] for f in tx["flags"]], ["MANUAL_DELETION"])
check("reads as a sentence", tx["flags"][0]["note"],
      "Tom Daley removed this line from the statement. duplicate of line 3")
# The point of the whole design: the balance now ties.
check("statement reconciles without it", st["reconciled"], True)
check("computed close", st["computed_ending_balance"], Decimal("300.00"))
check("excluded from reads", len(t.get_by_statement(10)), 3)
check("still there when asked for", len(t.get_by_statement(10, include_deleted=True)), 4)

# ── 2. Dropping a real line breaks the balance, and says so ──────────────
print("2. dropping a real line")
a, s, t = wire([account()], [statement()],
               [line(1, "100.00"), line(2, "100.00"), line(3, "100.00")])
tx, st = statement_service.delete_transaction(None, 3, 3, "Tom Daley")
check("no longer reconciles", st["reconciled"], False)
check("delta shows what is missing", st["reconciliation_delta"], Decimal("100.00"))
check("flagged for review", [f["code"] for f in st["flags"]], ["UNRECONCILED"])

# ── 3. Restore puts it back, and keeps both halves of the story ──────────
print("3. restoring a dropped line")
tx, st = statement_service.restore_transaction(None, 3, 3, "Tom Daley")
check("no longer deleted", tx["deleted_at"], None)
check("reason cleared", tx["deletion_reason"], None)
check("both events recorded", [f["code"] for f in tx["flags"]],
      ["MANUAL_DELETION", "MANUAL_RESTORE"])
check("reconciles again", st["reconciled"], True)

# ── 4. Dropping twice, or restoring what is not dropped ──────────────────
print("4. refused when it would mean nothing")
a, s, t = wire([account()], [statement()], [line(1, "300.00", deleted="2024-02-01T00:00:00Z")])
try:
    statement_service.delete_transaction(None, 1, 3, "Tom Daley")
    check("second delete refused", False, True)
except ValueError as e:
    check("second delete refused", str(e), "That line has already been removed")
a, s, t = wire([account()], [statement()], [line(1, "300.00")])
try:
    statement_service.restore_transaction(None, 1, 3, "Tom Daley")
    check("restore refused", False, True)
except ValueError as e:
    check("restore refused", str(e), "That line has not been removed")

# ── 5. Account delete: preview says exactly what goes ────────────────────
print("5. account delete preview")
a, s, t = wire(
    [account()],
    [statement(10), statement(11, beginning="300.00", ending="300.00")],
    [line(1, "100.00"), line(2, "100.00"), line(3, "100.00", statement_id=11),
     line(4, "0.00", deleted="2024-02-01T00:00:00Z")],
)
preview = statement_service.preview_account_delete(None, 1)
check("statements counted", preview["statements"], 2)
check("live lines counted", preview["transactions"], 3)
check("periods listed", len(preview["periods"]), 2)
check("nothing to warn about", preview["warnings"], [])

# ── 6. Warnings, but never a block — this is a deliberate act ────────────
print("6. account carrying attorney judgment")
a, s, t = wire([account(ownership=AccountOwnership.joint,
                        character=PropertyCharacter.community,
                        notes="Client says closed in March")], [], [])
preview = statement_service.preview_account_delete(None, 1)
check("three warnings", len(preview["warnings"]), 3)
check("names the characterization", any("community" in w for w in preview["warnings"]), True)
result = statement_service.delete_account(None, 1)
check("still deleted", a.deleted, [1])
check("reports what went", result["account_id"], 1)

# ── 7. A successor is unlinked deliberately, not by cascade ──────────────
print("7. deleting a predecessor")
a, s, t = wire([account(1), account(2, antecedent=1)], [], [])
preview = statement_service.preview_account_delete(None, 1)
check("warns about the chain", any("succeeding" in w for w in preview["warnings"]), True)
statement_service.delete_account(None, 1)
check("successor unlinked", a.rows[2]["antecedent_account_id"], None)
check("successor survives", 2 in a.rows, True)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
