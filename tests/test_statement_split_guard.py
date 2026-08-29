"""
One statement read as two.

The failure this reproduces, from a real First Financial Bank statement: page 1
carries the transaction register, page 3 carries a DAILY ENDING BALANCE table
under a repeated account header. The model read the balance table as a second
register and, because the institution lives only in the letterhead graphic and
could not be read either, filed it under a second invented account.

That second account is what defeats the duplicate guard — `find_period` is
scoped to an account, so two statements for one period never meet. The guard
here is document-scoped instead: two statements cannot be printed on the same
page.
"""
import sys

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

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
    def __init__(self): self.rows = []
    def get_by_matter(self, m): return self.rows
    def find_match(self, m, inst, last4):
        for a in self.rows:
            if a["account_number_last4"] == last4 and a["institution"].lower() == inst.lower():
                return a
        return None
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r
    def others_with_last4(self, matter_id, institution, last4):
        if not last4:
            return []
        wanted = institution.strip().lower()
        return [a for a in self.get_by_matter(matter_id)
                if a['account_number_last4'] == last4
                and (a['institution'] or '').strip().lower() != wanted]

class FakeStatements:
    def __init__(self): self.rows = []
    def get_by_account(self, a): return [r for r in self.rows if r["financial_account_id"] == a]
    def find_period(self, a, s, e):
        for r in self.get_by_account(a):
            if r["period_start"] == s and r["period_end"] == e:
                return r
        return None
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r
    def find_overlapping(self, a, s, e):
        return [r for r in self.get_by_account(a)
                if r.get('review_status') != 'rejected'
                and not (r['period_start'] == s and r['period_end'] == e)
                and r['period_start'] <= e and s <= r['period_end']]

class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


def run(extracted, raw_text=""):
    a, s, t = FakeAccounts(), FakeStatements(), FakeTransactions()
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    summary = statement_service.commit_document(
        manager=None, matter_id=1, staff_id=1, extracted=extracted, raw_text=raw_text,
    )
    return summary, a, s, t


def block(institution, last4, pages, amounts, start="2026-01-01", end="2026-01-30",
          beginning=300.03, ending=None):
    # Close the statement out properly unless a test wants it broken, so an
    # unrelated UNRECONCILED flag never masks what is being checked.
    if ending is None:
        ending = round(beginning + sum(amounts), 2)
    return {
        "account": {"institution": institution, "account_type": "savings",
                    "account_number_last4": last4},
        "period": {"start_date": start, "end_date": end},
        "balances": {"beginning_balance": beginning, "ending_balance": ending},
        "transactions": [
            {"line_no": i + 1, "transaction_date": "2026-01-06", "description": "LINE %d" % (i + 1),
             "amount": amt, "physical_page_number": pages[i % len(pages)]}
            for i, amt in enumerate(amounts)
        ],
    }


def codes(statement):
    return sorted(f["code"] for f in statement["flags"])


# ── 1. The real failure: register on p1, balance table on p3 ─────────────
# Two invented accounts, so the account-scoped duplicate guard never fires.
print("1. one statement read as two")
summary, a, s, t = run({"statements": [
    block("Unknown institution", "4527", [1], [100.00, 100.00, 0.03, -500.03]),
    block("Frst Fnancial", "8617", [1, 3], [300.03, 400.03, 500.03, 0.00, 0.03]),
]})
check("both committed", summary["statements_found"], 2)
check("two accounts created — the duplicate guard could not see across them", len(a.rows), 2)
check("first is clean", codes(s.rows[0]), [])
check("second flagged as a split", "SUSPECT_SPLIT" in codes(s.rows[1]), True)
check("held for review", summary["results"][1]["status"], "needs_review")
note = next(f["note"] for f in s.rows[1]["flags"] if f["code"] == "SUSPECT_SPLIT")
check("names the other statement", "#1" in note, True)
check("says what to do", "reject whichever is not the real register" in note, True)

# ── 2. A genuine combined package is left alone ──────────────────────────
print("2. two real statements on separate pages")
summary, a, s, t = run({"statements": [
    block("First Financial Bank", "4527", [1, 2], [100.00, -99.97]),
    block("First Financial Bank", "9260", [3, 4], [50.00, -49.97]),
]})
check("both clean", [codes(r) for r in s.rows], [[], []])
check("both auto-accepted", [r["status"] for r in summary["results"]],
      ["auto_accepted", "auto_accepted"])

# ── 3. Same account, different periods, different pages ─────────────────
print("3. two months in one file")
summary, a, s, t = run({"statements": [
    block("First Financial Bank", "4527", [1, 2], [100.00, -99.97],
          start="2026-01-01", end="2026-01-30"),
    block("First Financial Bank", "4527", [3, 4], [100.00, -99.97],
          start="2026-02-01", end="2026-02-28"),
]})
check("one account", len(a.rows), 1)
check("no split flags", [codes(r) for r in s.rows], [[], []])

# ── 4. A statement with no page numbers cannot be checked ───────────────
# Nothing to compare, so nothing is claimed — silence beats a false alarm.
print("4. no page numbers reported")
summary, a, s, t = run({"statements": [
    block("First Financial Bank", "4527", [1], [100.00, -99.97]),
    {"account": {"institution": "First Financial Bank", "account_type": "savings",
                 "account_number_last4": "9260"},
     "period": {"start_date": "2026-01-01", "end_date": "2026-01-30"},
     "balances": {"beginning_balance": 0, "ending_balance": 0},
     "transactions": [{"line_no": 1, "description": "X", "amount": 0}]},
]})
check("no split flag raised", "SUSPECT_SPLIT" in codes(s.rows[1]), False)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
