"""
tests/test_unreconciled_accounts.py - An account says when it holds a statement that does not tie.

THE FAILURE THIS GUARDS AGAINST is a Type 2 error made in a hurry. An import of
46 combined statements put ~200 statements that reconciled to the cent into the
exceptions queue, and one that was really off by $0.20 sat among them. Clicking
Accept down that list clears the queue; it does not make the arithmetic true,
and nothing outside the account's own statement rows said so afterwards.

So the matter's account list carries, per account, how many of its statements
do not tie, accepted or not. What is checked:

1. `out_of_balance` counts only statements whose lines disagree with the close.
   A statement with no printed balances (delta NULL) could not be checked, which
   is a different fact; a rejected read is not evidence of anything.
2. The list attaches the count to the right account, and 0 to the others.
3. A single-account response leaves it None, which means "not computed here".
   A 0 there would erase the warning whenever somebody edited the account.

Run:  venv/Scripts/python.exe tests/test_unreconciled_accounts.py
"""
import os
import sys
from datetime import datetime
from decimal import Decimal

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import routers.financial as router  # noqa: E402
from db.models.financial import StatementReviewStatus  # noqa: E402
from db.repositories.financial import FinancialAccountStatementRepository  # noqa: E402
from schemas.financial import FinancialAccountResponse  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def statement(id_, account, delta, status=StatementReviewStatus.accepted):
    return Row(id=id_, financial_account_id=account,
               reconciliation_delta=None if delta is None else Decimal(delta),
               review_status=status, reconciled=False)


# ── 1. Which statements count ────────────────────────────────────────────
print("1. out_of_balance")
rows = [
    statement(1, 10, "0.20"),                                       # accepted, off: the case
    statement(2, 10, "-5.00", StatementReviewStatus.needs_review),  # still queued, off
    statement(3, 11, None),                                         # no balances printed
    statement(4, 12, "1.00", StatementReviewStatus.rejected),       # a discarded read
]
asked: list[dict] = []


class FakeStatementRepo(FinancialAccountStatementRepository):
    def __init__(self):  # no manager: select_many is replaced below
        pass

    def select_many(self, condition=None, **kwargs):
        asked.append(condition)
        return rows, len(rows)


off = FakeStatementRepo().out_of_balance(7)
check("asks for the matter's unreconciled statements",
      asked[-1], {"matter_id": 7, "reconciled": False})
check("an accepted statement that is off counts, and so does a queued one",
      sorted(s.id for s in off), [1, 2])

# ── 2. The list attaches the count per account ───────────────────────────
print("2. list_accounts")
NOW = datetime(2026, 11, 20)


def account(id_):
    return Row(id=id_, model_dump=lambda: dict(
        id=id_, matter_id=7, institution="Ally Bank", account_type="savings",
        account_number_last4=str(7000 + id_), account_number_masked=None,
        name_on_account=None, opposing_party_id=None, production_responsibility="client",
        ownership="client_sole", property_character=None, purpose=None, notes=None,
        antecedent_account_id=None, is_closed=False, created_at=NOW, updated_at=None))


class FakeAccountRepo:
    def __init__(self, manager): pass
    def get_by_matter(self, matter_id): return [account(10), account(11), account(12)]


router.FinancialAccountRepository = FakeAccountRepo
router.FinancialAccountStatementRepository = lambda manager: FakeStatementRepo()
listed = {a.id: a.unreconciled_statements for a in router.list_accounts(7, manager=None, _=None)}
check("two statements off on the first account", listed[10], 2)
check("a statement with no printed balances is not called out of balance", listed[11], 0)
check("a rejected read is not counted", listed[12], 0)

# ── 3. Elsewhere the field means "not computed", not "none" ──────────────
print("3. single-account responses")
single = FinancialAccountResponse(**account(10).model_dump())
check("left None, so the client keeps the count it had", single.unreconciled_statements, None)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
