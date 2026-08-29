"""
A page with no transactions on it is still a page.

Built from four real First Financial Bank statements for account x4527. Every
one is three pages with a consecutive Bates run and nothing missing:

    2024.01.31  GS6385-6387   transactions on pages 1 and 3
    2024.02.29  GS6382-6384   transactions on pages 1 and 3
    2024.05.31  GS6397-6399   transactions on page 1 only
    2024.09.30  GS6406-6408   transactions on page 1 only

Page 2 of each is the checkbook reconciliation worksheet — a blank form, no
entries. The gap scan used to run over the pages that carried transactions, so
for the first two it saw pages [1, 3], values {6385, 6387}, and reported the
statement's own page 2 as missing from the production. The last two came
through clean only because their page set collapsed to [1], and a single value
has no interior to find a hole in.

The scan now runs over the page SPAN and consults the series for every page in
it, so an unremarkable worksheet page stops reading as a phantom.
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
    def find_match(self, m, i, l4):
        return next((a for a in self.rows if a["account_number_last4"] == l4), None)
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r
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
    def find_period(self, a, s, e): return None
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r
    def find_overlapping(self, a, s, e):
        return [r for r in self.get_by_account(a)
                if r.get('review_status') != 'rejected'
                and not (r['period_start'] == s and r['period_end'] == e)
                and r['period_start'] <= e and s <= r['period_end']]

class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


def wire():
    a, s, t = FakeAccounts(), FakeStatements(), FakeTransactions()
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    return a, s, t


def document(first_bates, line_pages, page_count=3, stamped=None):
    """
    A three-page First Financial statement.

    :param line_pages: which physical page each transaction sits on.
    :param stamped: pages that carry a readable stamp; all of them by default.
    """
    stamped = set(range(1, page_count + 1)) if stamped is None else set(stamped)
    pages = []
    for page in range(1, page_count + 1):
        pages.append("<<<PAGE %d>>>" % page)
        pages.append("ACCOUNT NUMBER 81120014527")
        pages.append("STATEMENT DATES 1/01/24-1/31/24")
        pages.append("PAGE %d of %d" % (page, page_count))
        if page == 2:
            pages.append("Checkbook Reconciliation")
            pages.append("CHECKS OUTSTANDING")
        if page in stamped:
            pages.append("GS%d" % (first_bates + page - 1))
    raw_text = "\n".join(pages)

    amounts = [100.00] * len(line_pages)
    extracted = {"statements": [{
        "account": {"institution": "First Financial Bank", "account_type": "savings",
                    "account_number_last4": "4527"},
        "period": {"start_date": "2024-01-01", "end_date": "2024-01-31"},
        "balances": {"beginning_balance": 0.00, "ending_balance": round(sum(amounts), 2)},
        "transactions": [
            {"line_no": i + 1, "transaction_date": "2024-01-%02d" % (i + 2),
             "description": "Transfer from DDA (Sweep)", "amount": amounts[i],
             "physical_page_number": p}
            for i, p in enumerate(line_pages)
        ],
    }]}
    return extracted, raw_text


def run(extracted, raw_text):
    a, s, t = wire()
    summary = statement_service.commit_document(
        manager=None, matter_id=1, staff_id=1, extracted=extracted, raw_text=raw_text,
    )
    return summary, s


def codes(statement):
    return sorted(f["code"] for f in statement["flags"])


# ── 1. The reported failure, both files that showed it ───────────────────
print("1. transactions on pages 1 and 3, worksheet on page 2")
for label, first in [("2024.01.31", 6385), ("2024.02.29", 6382)]:
    summary, s = run(*document(first, line_pages=[1, 1, 1, 3, 3, 3]))
    check("%s: no phantom gap" % label, "BATES_GAP" in codes(s.rows[0]), False)
    check("%s: nothing reported missing" % label, summary["results"][0]["bates_gaps"], [])
    check("%s: auto-accepted" % label, summary["results"][0]["status"], "auto_accepted")
    check("%s: cites its full range" % label,
          (summary["results"][0]["bates_first"], summary["results"][0]["bates_last"]),
          ("GS%d" % first, "GS%d" % (first + 2)))

# ── 2. The two that were clean before, still clean ───────────────────────
print("2. transactions on page 1 only")
for label, first in [("2024.05.31", 6397), ("2024.09.30", 6406)]:
    summary, s = run(*document(first, line_pages=[1, 1, 1, 1]))
    check("%s: no gap" % label, "BATES_GAP" in codes(s.rows[0]), False)
    check("%s: auto-accepted" % label, summary["results"][0]["status"], "auto_accepted")

# ── 3. A page genuinely missing is still caught ──────────────────────────
# Four pages of statement, but the production skipped page 3 entirely: the
# stamps run GS6385, GS6386, GS6388.
print("3. a page really absent from the production")
extracted, raw_text = document(6385, line_pages=[1, 1, 4], page_count=3)
raw_text = raw_text.replace("GS6387", "GS6388")
extracted["statements"][0]["transactions"][-1]["physical_page_number"] = 3
summary, s = run(extracted, raw_text)
check("gap flagged", "BATES_GAP" in codes(s.rows[0]), True)
check("names the missing number", summary["results"][0]["bates_gaps"], ["GS6387"])
check("held for review", summary["results"][0]["status"], "needs_review")

# ── 4. An unstamped page inside the span is not a gap ────────────────────
print("4. worksheet page carries no stamp")
summary, s = run(*document(6385, line_pages=[1, 1, 3], stamped=[1, 3]))
check("no gap", "BATES_GAP" in codes(s.rows[0]), False)
check("gaps empty", summary["results"][0]["bates_gaps"], [])

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
