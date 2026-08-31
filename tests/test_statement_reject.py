"""
Rejecting a statement discards it — the row, its lines, and often the account.

Rejection used to flip a status and stop, leaving the statement, its
transactions, and the empty account a bad import had created sitting in the
database, filtered out of every view. Invisible but present is the worst of both
worlds. These cover what now goes, and what is deliberately kept.
"""
import sys

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


def account(id=1, matter=1, ownership=AccountOwnership.unknown, character=None,
            purpose=None, notes=None, antecedent=None):
    return Row(id=id, matter_id=matter, institution="First Financial Bank",
               account_number_last4="4527", ownership=ownership,
               property_character=character, purpose=purpose, notes=notes,
               antecedent_account_id=antecedent)


def statement(id=10, account_id=1):
    return Row(id=id, financial_account_id=account_id, matter_id=1)


def transaction(id, statement_id=10):
    return Row(id=id, statement_id=statement_id, financial_account_id=1)


class FakeAccounts:
    def __init__(self, rows): self.rows = {r["id"]: r for r in rows}; self.deleted = []
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_matter(self, m): return [r for r in self.rows.values() if r["matter_id"] == m]
    def delete(self, id): self.deleted.append(id); self.rows.pop(id, None); return True
    def others_with_last4(self, matter_id, institution, last4):
        if not last4:
            return []
        wanted = institution.strip().lower()
        return [a for a in self.get_by_matter(matter_id)
                if a['account_number_last4'] == last4
                and (a['institution'] or '').strip().lower() != wanted]

class FakeStatements:
    def __init__(self, rows): self.rows = {r["id"]: r for r in rows}; self.deleted = []
    def select_one(self, condition): return self.rows.get(condition["id"])
    def get_by_account(self, a): return [r for r in self.rows.values() if r["financial_account_id"] == a]
    def delete(self, id): self.deleted.append(id); self.rows.pop(id, None); return True
    def find_overlapping(self, a, s, e):
        return [r for r in self.get_by_account(a)
                if r.get('review_status') != 'rejected'
                and not (r['period_start'] == s and r['period_end'] == e)
                and r['period_start'] <= e and s <= r['period_end']]

class FakeTransactions:
    def __init__(self, rows): self.rows = rows
    def get_by_statement(self, sid, include_deleted=False):
        return [r for r in self.rows if r["statement_id"] == sid]


def wire(accounts, statements, transactions):
    a, s, t = FakeAccounts(accounts), FakeStatements(statements), FakeTransactions(transactions)
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    return a, s, t


# ── 1. The everyday case: bad import, nothing worth keeping ──────────────
print("1. bad import discarded whole")
a, s, t = wire([account()], [statement()], [transaction(1), transaction(2), transaction(3)])
result = statement_service.reject_statement(None, 10)
check("statement deleted", s.deleted, [10])
check("lines counted before the delete", result["transactions_deleted"], 3)
check("empty account removed", result["account_deleted"], True)
check("account actually deleted", a.deleted, [1])
check("no reason to report", result["account_kept_reason"], None)

# ── 2. The account has other statements ─────────────────────────────────
print("2. account still holds other statements")
a, s, t = wire([account()], [statement(10), statement(11)], [transaction(1)])
result = statement_service.reject_statement(None, 10)
check("statement gone", s.deleted, [10])
check("account kept", result["account_deleted"], False)
check("reason given", result["account_kept_reason"], "it still has other statements")
check("account untouched", a.deleted, [])

# ── 3. Attorney judgment outlives the import ────────────────────────────
print("3. characterized account survives")
for field, value, reason in [
    ("ownership", AccountOwnership.joint, "someone has recorded who holds it"),
    ("property_character", PropertyCharacter.community, "it has been characterized"),
    ("purpose", "Household operating account", "it carries notes someone wrote"),
    ("notes", "Client says this was closed in March", "it carries notes someone wrote"),
]:
    kwargs = {"ownership": AccountOwnership.unknown}
    if field == "ownership":
        kwargs["ownership"] = value
    elif field == "property_character":
        kwargs["character"] = value
    else:
        kwargs[field] = value
    a, s, t = wire([account(**kwargs)], [statement()], [transaction(1)])
    result = statement_service.reject_statement(None, 10)
    check("kept for %s" % field, result["account_deleted"], False)
    check("reason for %s" % field, result["account_kept_reason"], reason)

# ── 4. A link in an account history is not collateral damage ────────────
print("4. succession chain protected")
a, s, t = wire([account(1), account(2, antecedent=1)], [statement(10, 1)], [])
result = statement_service.reject_statement(None, 10)
check("predecessor kept", result["account_deleted"], False)
check("reason", result["account_kept_reason"], "another account is recorded as succeeding it")

a, s, t = wire([account(1, antecedent=2), account(2)], [statement(10, 1)], [])
result = statement_service.reject_statement(None, 10)
check("successor kept", result["account_deleted"], False)
check("reason", result["account_kept_reason"], "it is part of an account history")

# ── 5. A statement that is not there ────────────────────────────────────
print("5. missing statement")
wire([account()], [], [])
try:
    statement_service.reject_statement(None, 999)
    check("refused", False, True)
except ValueError as e:
    check("refused", str(e), "Statement not found")

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
