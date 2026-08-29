"""End-to-end over statement_service.commit_document with fake repositories."""
import sys
sys.path.insert(0, r"d:\Local Projects\cyclone\app")

from services.statement_service import statement_service

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
    def find_match(self, m, inst, last4): return None
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
    def get_by_account(self, a): return []
    def find_period(self, a, s, e): return None
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


def run(raw_text, extracted, prefix=None):
    accounts, statements, transactions = FakeAccounts(), FakeStatements(), FakeTransactions()
    import services.statement_service as mod
    mod.FinancialAccountRepository = lambda m: accounts
    mod.FinancialAccountStatementRepository = lambda m: statements
    mod.FinancialAccountTransactionRepository = lambda m: transactions
    summary = statement_service.commit_document(
        manager=None, matter_id=1, staff_id=1, extracted=extracted,
        raw_text=raw_text, bates_prefix=prefix,
    )
    return summary, statements, transactions


def build_text(stamps):
    """stamps: {page: stamp or None}"""
    out = []
    for page in sorted(stamps):
        out.append("<<<PAGE %d>>>" % page)
        out.append("FIRST NATIONAL BANK")
        out.append("Account ending in 4357")
        out.append("Page %d of %d" % (page, len(stamps)))
        out.append("03/04  COFFEE SHOP DENTON TX   -6.49")
        if stamps[page]:
            out.append(stamps[page])
    return "\n".join(out)


def statement(lines):
    return {"statements": [{
        "account": {"institution": "First National Bank", "account_type": "checking",
                    "account_number_last4": "4357"},
        "period": {"start_date": "2025-03-01", "end_date": "2025-03-31"},
        "balances": {"beginning_balance": 1000, "ending_balance": 1000 - 6.49 * len(lines)},
        "transactions": lines,
    }]}


def line(n, page, bates=None):
    d = {"line_no": n, "transaction_date": "2025-03-04", "description": "COFFEE SHOP",
         "amount": -6.49, "physical_page_number": page}
    if bates:
        d["bates_number"] = bates
    return d


# ── 1. Stamped production: regex owns the field ──────────────────────────
print("1. stamped production")
text = build_text({1: "KF-000141", 2: "KF-000142", 3: "KF-000143"})
# The model returns WRONG bates numbers on purpose - they must be discarded.
summary, st, tx = run(text, statement([line(1, 1, "BOGUS-1"), line(2, 2, "BOGUS-2"), line(3, 3, "BOGUS-3")]))
check("series detected", summary["bates"] is not None, True)
check("prefix", summary["bates"]["prefix"], "KF")
check("model values discarded", [t["bates_number"] for t in tx.rows],
      ["KF-000141", "KF-000142", "KF-000143"])
check("statement range first", summary["results"][0]["bates_first"], "KF-000141")
check("statement range last", summary["results"][0]["bates_last"], "KF-000143")
check("no gaps", summary["results"][0]["bates_gaps"], [])
check("auto accepted", summary["results"][0]["status"], "auto_accepted")

# ── 2. Unstamped page inside the statement ───────────────────────────────
print("2. one page unstamped")
text = build_text({1: "KF-000141", 2: None, 3: "KF-000143"})
summary, st, tx = run(text, statement([line(1, 1), line(2, 2), line(3, 3)]))
check("page 2 has no citation", tx.rows[1]["bates_number"], None)
check("page 1 and 3 stamped", [tx.rows[0]["bates_number"], tx.rows[2]["bates_number"]],
      ["KF-000141", "KF-000143"])
codes = [f["code"] for f in st.rows[0]["flags"]]
check("UNSTAMPED flagged", "BATES_UNSTAMPED" in codes, True)
check("no false GAP", "BATES_GAP" in codes, False)
check("still auto accepted", summary["results"][0]["status"], "auto_accepted")

# ── 3. A page genuinely missing from the production ──────────────────────
print("3. page missing from production")
text = build_text({1: "KF-000141", 2: "KF-000142", 3: "KF-000145"})
summary, st, tx = run(text, statement([line(1, 1), line(2, 2), line(3, 3)]))
codes = [f["code"] for f in st.rows[0]["flags"]]
check("GAP flagged", "BATES_GAP" in codes, True)
check("gaps listed", summary["results"][0]["bates_gaps"], ["KF-000143", "KF-000144"])
check("held for review", summary["results"][0]["status"], "needs_review")

# ── 4. No stamps at all: model values kept but flagged ───────────────────
print("4. unstamped document")
text = build_text({1: None, 2: None, 3: None})
summary, st, tx = run(text, statement([line(1, 1, "GUESS-1"), line(2, 2), line(3, 3)]))
check("no series", summary["bates"], None)
check("model value kept", tx.rows[0]["bates_number"], "GUESS-1")
codes = [f["code"] for f in st.rows[0]["flags"]]
check("UNVERIFIED flagged", "BATES_UNVERIFIED" in codes, True)
check("info only, not blocking", summary["results"][0]["status"], "auto_accepted")

# ── 5. Text without page markers must not crash ──────────────────────────
print("5. no page markers")
summary, st, tx = run("plain text, no markers", statement([line(1, None)]))
check("no series", summary["bates"], None)
check("committed anyway", summary["statements_found"], 1)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
