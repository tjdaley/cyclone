"""
Safety checks on merging two account rows that are really one account.

The scenario this exists for: a statement whose institution lives only in the
letterhead graphic gets filed under "Unknown institution". Correcting the name
afterwards does not retroactively match the next upload — institution plus last
four is the dedup key — so the second statement opens a second row.
"""
import sys
from datetime import date

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from db.models.financial import AccountType, StatementReviewStatus  # noqa: E402
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


def account(id, matter=1, institution="First Financial Bank", last4="4527",
            acct_type=AccountType.savings, antecedent=None):
    return Row(id=id, matter_id=matter, institution=institution,
               account_number_last4=last4, account_type=acct_type,
               antecedent_account_id=antecedent)


def statement(id, account_id, start, end, status=StatementReviewStatus.auto_accepted):
    return Row(id=id, financial_account_id=account_id,
               period_start=date.fromisoformat(start), period_end=date.fromisoformat(end),
               review_status=status)


def transaction(id, account_id, bates=None):
    return Row(id=id, financial_account_id=account_id, bates_number=bates)


class FakeAccounts:
    def __init__(self, rows): self.rows = {r["id"]: r for r in rows}; self.deleted = []
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_matter(self, m): return [r for r in self.rows.values() if r["matter_id"] == m]
    def update(self, id, patch): self.rows[id].update(patch); return self.rows[id]
    def delete(self, id): self.deleted.append(id); self.rows.pop(id, None); return True
    def others_with_last4(self, matter_id, institution, last4):
        if not last4:
            return []
        wanted = institution.strip().lower()
        return [a for a in self.get_by_matter(matter_id)
                if a['account_number_last4'] == last4
                and (a['institution'] or '').strip().lower() != wanted]

class FakeStatements:
    def __init__(self, rows): self.rows = rows
    def get_by_account(self, a): return [r for r in self.rows if r["financial_account_id"] == a]
    def update(self, id, patch):
        for r in self.rows:
            if r["id"] == id:
                r.update(patch); return r
        raise KeyError(id)
    def find_overlapping(self, a, s, e):
        return [r for r in self.get_by_account(a)
                if r.get('review_status') != 'rejected'
                and not (r['period_start'] == s and r['period_end'] == e)
                and r['period_start'] <= e and s <= r['period_end']]

class FakeTransactions:
    def __init__(self, rows): self.rows = rows
    def get_by_account(self, a, include_deleted=False):
        return [r for r in self.rows if r["financial_account_id"] == a]


def wire(accounts, statements, transactions):
    a, s, t = FakeAccounts(accounts), FakeStatements(statements), FakeTransactions(transactions)
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    return a, s, t


def codes(preview):
    return sorted(c["code"] for c in preview["conflicts"])


# ── 1. The real case: "Unknown institution" folded into the named row ────
print("1. clean merge")
a, s, t = wire(
    [account(1, institution="Unknown institution"), account(2)],
    [statement(10, 1, "2026-02-01", "2026-02-28"), statement(11, 2, "2026-01-01", "2026-01-30")],
    [transaction(100, 1, "GS2779"), transaction(101, 1, "GS2780"), transaction(102, 2, "GS2775")],
)
preview = statement_service.preview_merge(None, 1, 2)
check("no conflicts", codes(preview), [])
check("can merge", preview["can_merge"], True)
check("statements counted", preview["statements_to_move"], 1)
check("transactions counted", preview["transactions_to_move"], 2)

result = statement_service.merge(None, 1, 2)
check("statement repointed", s.rows[0]["financial_account_id"], 2)
check("source deleted", a.deleted, [1])
check("moved count", result["statements_moved"], 1)

# ── 2. Overlapping periods are blocking ─────────────────────────────────
print("2. overlapping statement periods")
wire(
    [account(1, institution="Unknown institution"), account(2)],
    [statement(10, 1, "2026-01-01", "2026-01-30"), statement(11, 2, "2026-01-01", "2026-01-30")],
    [],
)
preview = statement_service.preview_merge(None, 1, 2)
check("PERIOD_OVERLAP raised", "PERIOD_OVERLAP" in codes(preview), True)
check("blocking", preview["can_merge"], False)
try:
    statement_service.merge(None, 1, 2, force=True)
    check("force cannot override", False, True)
except ValueError:
    check("force cannot override", True, True)

# ── 3. A rejected statement does not block ──────────────────────────────
print("3. rejected duplicate ignored")
wire(
    [account(1, institution="Unknown institution"), account(2)],
    [statement(10, 1, "2026-01-01", "2026-01-30"),
     statement(11, 2, "2026-01-01", "2026-01-30", StatementReviewStatus.rejected)],
    [],
)
preview = statement_service.preview_merge(None, 1, 2)
check("no overlap conflict", "PERIOD_OVERLAP" in codes(preview), False)
check("can merge", preview["can_merge"], True)

# ── 4. Shared Bates: warn, forceable ────────────────────────────────────
print("4. same pages ingested twice")
wire(
    [account(1, institution="Unknown institution"), account(2)],
    [statement(10, 1, "2026-02-01", "2026-02-28"), statement(11, 2, "2026-01-01", "2026-01-30")],
    [transaction(100, 1, "GS2775"), transaction(101, 2, "GS2775")],
)
preview = statement_service.preview_merge(None, 1, 2)
check("BATES_OVERLAP raised", "BATES_OVERLAP" in codes(preview), True)
check("not blocking", preview["can_merge"], True)
check("needs force", preview["needs_force"], True)
try:
    statement_service.merge(None, 1, 2)
    check("refused without force", False, True)
except ValueError:
    check("refused without force", True, True)

wire(
    [account(1, institution="Unknown institution"), account(2)],
    [statement(10, 1, "2026-02-01", "2026-02-28"), statement(11, 2, "2026-01-01", "2026-01-30")],
    [transaction(100, 1, "GS2775"), transaction(101, 2, "GS2775")],
)
statement_service.merge(None, 1, 2, force=True)
check("force proceeds", True, True)

# ── 5. Different matters can never merge ────────────────────────────────
print("5. different matters")
wire([account(1, matter=1), account(2, matter=7)], [], [])
preview = statement_service.preview_merge(None, 1, 2)
check("DIFFERENT_MATTER raised", "DIFFERENT_MATTER" in codes(preview), True)
check("blocking", preview["can_merge"], False)

# ── 6. Mismatched last four warns but does not block ────────────────────
print("6. different account numbers")
wire([account(1, last4="4527"), account(2, last4="9999", acct_type=AccountType.checking)], [], [])
preview = statement_service.preview_merge(None, 1, 2)
check("LAST4_MISMATCH raised", "LAST4_MISMATCH" in codes(preview), True)
check("TYPE_MISMATCH raised", "TYPE_MISMATCH" in codes(preview), True)
check("not blocking", preview["can_merge"], True)
check("needs force", preview["needs_force"], True)

# ── 7. An account cannot swallow itself ─────────────────────────────────
print("7. self merge")
wire([account(1)], [], [])
preview = statement_service.preview_merge(None, 1, 1)
check("SAME_ACCOUNT raised", "SAME_ACCOUNT" in codes(preview), True)
check("blocking", preview["can_merge"], False)

# ── 8. Succession chains survive the merge ──────────────────────────────
print("8. antecedent repointed")
a, s, t = wire(
    [account(1, institution="Unknown institution"), account(2), account(3, antecedent=1)],
    [statement(10, 1, "2026-02-01", "2026-02-28")],
    [],
)
statement_service.merge(None, 1, 2)
check("successor repointed at survivor", a.rows[3]["antecedent_account_id"], 2)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
