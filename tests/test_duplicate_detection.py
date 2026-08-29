"""
Re-ingesting a document that is already filed.

Two guards, and they answer different questions:

  * The same account and the exact same period is a repeat. It is skipped —
    nothing is written and the caller is told 'duplicate'.
  * The same account and an *overlapping* period is probably the same statement
    read twice, but not certainly. These statements print two different date
    ranges — the header says "5/01/24-5/31/24" while the summary below says
    "5/01/24 thru 6/02/24" — so a re-ingest can pick the other one and slip past
    an equality check. That is flagged, never skipped: consecutive statements do
    not share days, but "nearly always" is not grounds for discarding evidence.

The guard that does NOT hold: both are scoped to an account. When the account
itself was misidentified, neither fires — which is what happened when a run of
statements landed under the form printer's name instead of the bank's.
"""
import sys
from datetime import date

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from db.models.financial import StatementReviewStatus  # noqa: E402
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
    def __init__(self, rows=()): self.rows = [Row(r) for r in rows]
    def get_by_matter(self, m): return self.rows
    def find_match(self, m, i, l4):
        if not l4:
            return None
        return next((a for a in self.rows if a["account_number_last4"] == l4
                     and a["institution"].strip().lower() == i.strip().lower()), None)
    def others_with_last4(self, m, i, l4):
        if not l4:
            return []
        return [a for a in self.rows if a["account_number_last4"] == l4
                and a["institution"].strip().lower() != i.strip().lower()]
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeStatements:
    def __init__(self, rows=()): self.rows = [Row(r) for r in rows]
    def get_by_account(self, a): return [r for r in self.rows if r["financial_account_id"] == a]
    def find_period(self, a, s, e):
        return next((r for r in self.get_by_account(a)
                     if r["review_status"] != StatementReviewStatus.rejected
                     and r["period_start"] == s and r["period_end"] == e), None)
    def find_overlapping(self, a, s, e):
        return [r for r in self.get_by_account(a)
                if r["review_status"] != StatementReviewStatus.rejected
                and not (r["period_start"] == s and r["period_end"] == e)
                and r["period_start"] <= e and s <= r["period_end"]]
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


def wire(accounts=(), statements=()):
    a, s, t = FakeAccounts(accounts), FakeStatements(statements), FakeTransactions()
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    return a, s, t


def account(id, institution="First Financial Bank", last4="4527"):
    return {"id": id, "matter_id": 1, "institution": institution,
            "account_number_last4": last4, "account_type": "savings"}


def filed(id, account_id, start, end, status=StatementReviewStatus.auto_accepted):
    return {"id": id, "financial_account_id": account_id, "matter_id": 1,
            "period_start": date.fromisoformat(start), "period_end": date.fromisoformat(end),
            "review_status": status}


def upload(institution="First Financial Bank", last4="4527",
           start="2023-12-01", end="2023-12-31"):
    return {"statements": [{
        "account": {"institution": institution, "account_type": "savings",
                    "account_number_last4": last4},
        "period": {"start_date": start, "end_date": end},
        "balances": {"beginning_balance": 0.00, "ending_balance": 100.00},
        "transactions": [{"line_no": 1, "transaction_date": "2023-12-02",
                          "description": "Transfer from DDA (Sweep)", "amount": 100.00,
                          "physical_page_number": 1}],
    }]}


def run(extracted, accounts=(), statements=()):
    a, s, t = wire(accounts, statements)
    summary = statement_service.commit_document(
        manager=None, matter_id=1, staff_id=1, extracted=extracted,
        raw_text="<<<PAGE 1>>>\nx\n",
    )
    return summary, a, s, t


def codes(statement):
    return sorted(f["code"] for f in statement["flags"])


# ── 1. The same file again: skipped outright ─────────────────────────────
print("1. exact re-ingest")
summary, a, s, t = run(
    upload(),
    accounts=[account(3)],
    statements=[filed(34, 3, "2023-12-01", "2023-12-31")],
)
check("reported as duplicate", summary["results"][0]["status"], "duplicate")
check("points at the one already filed", summary["results"][0]["statement_id"], 34)
check("nothing written", len(s.rows), 1)
check("no transactions written", len(t.rows), 0)

# ── 2. A rejected copy does not block a re-run ───────────────────────────
print("2. previously rejected, now re-uploaded")
summary, a, s, t = run(
    upload(),
    accounts=[account(3)],
    statements=[filed(34, 3, "2023-12-01", "2023-12-31", StatementReviewStatus.rejected)],
)
check("committed", summary["results"][0]["status"], "auto_accepted")
check("written alongside the rejected one", len(s.rows), 2)

# ── 3. The same statement, the other printed date range ──────────────────
# The header says 5/01-5/31; the account summary says 5/01 thru 6/02.
print("3. re-ingest picks the other printed period")
summary, a, s, t = run(
    upload(start="2024-05-01", end="2024-06-02"),
    accounts=[account(3)],
    statements=[filed(34, 3, "2024-05-01", "2024-05-31")],
)
check("not silently skipped", summary["results"][0]["status"], "needs_review")
check("overlap flagged", "OVERLAPPING_PERIOD" in codes(s.rows[-1]), True)
note = next(f["note"] for f in s.rows[-1]["flags"] if f["code"] == "OVERLAPPING_PERIOD")
check("names the other statement", "#34" in note, True)
check("says what to do", "reject whichever is less complete" in note, True)

# ── 4. Consecutive statements do not overlap ─────────────────────────────
# September ran 9/01 to 10/01 and October starts 10/02 — adjacent, not shared.
print("4. adjacent periods")
summary, a, s, t = run(
    upload(start="2023-10-02", end="2023-10-31"),
    accounts=[account(3)],
    statements=[filed(32, 3, "2023-09-01", "2023-10-01")],
)
check("no overlap flag", "OVERLAPPING_PERIOD" in codes(s.rows[-1]), False)
check("auto-accepted", summary["results"][0]["status"], "auto_accepted")

# ── 5. The guard that does not hold: a misidentified account ─────────────
# This is what happened in production. Both checks are account-scoped, so a
# statement filed under the form printer's name is invisible to them.
print("5. same period, but the account was misread")
summary, a, s, t = run(
    upload(institution="First Financial Bank"),
    accounts=[account(3), account(9, institution="CSI")],
    statements=[filed(34, 9, "2023-12-01", "2023-12-31")],
)
check("duplicate NOT caught across accounts", summary["results"][0]["status"], "auto_accepted")
check("a second copy is written", len(s.rows), 2)
# The account-level guard is what covers this, and it only fires when a *new*
# account is opened — here the upload matched account 3, which already existed.
check("no new account, so no last-four flag",
      "SAME_LAST4_DIFFERENT_INSTITUTION" in codes(s.rows[-1]), False)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
