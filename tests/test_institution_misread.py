"""
The institution is the field that goes wrong, and it is half the dedup key.

Two real misreads from one production of First Financial Bank statements:

  * The name is only in the letterhead graphic, so extraction returns null and
    the account is filed under "Unknown institution".
  * The vision fallback, asked which institution issued the page, answered
    "CSI" — Computer Services, Inc., printed in six-point type at the foot of
    every page beside the form's revision code. It printed the form; it does
    not hold the money.

Either way the matter ends up with two accounts for one real one, because
`find_match` keys on institution *plus* last four. These cover the guard against
the vendor answer and the flag that fires when it happens anyway.
"""
import sys

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

import services.pdf_service as pdf_mod  # noqa: E402
import services.statement_service as mod  # noqa: E402
from services.statement_service import _is_form_vendor, statement_service  # noqa: E402

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
    def find_match(self, m, inst, last4):
        if not last4:
            return None
        return next((a for a in self.rows if a["account_number_last4"] == last4
                     and a["institution"].strip().lower() == inst.strip().lower()), None)
    def others_with_last4(self, m, inst, last4):
        if not last4:
            return []
        return [a for a in self.rows if a["account_number_last4"] == last4
                and a["institution"].strip().lower() != inst.strip().lower()]
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


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


def wire(existing=()):
    a, s, t = FakeAccounts(existing), FakeStatements(), FakeTransactions()
    mod.FinancialAccountRepository = lambda m: a
    mod.FinancialAccountStatementRepository = lambda m: s
    mod.FinancialAccountTransactionRepository = lambda m: t
    return a, s, t


class Vision:
    def __init__(self, answer): self.answer = answer; self.calls = 0
    def ask_page(self, pdf_bytes, page, prompt):
        self.calls += 1
        return self.answer


def statement(institution, last4="4527"):
    return {"statements": [{
        "account": {"institution": institution, "account_type": "savings",
                    "account_number_last4": last4},
        "period": {"start_date": "2023-12-01", "end_date": "2023-12-31"},
        "balances": {"beginning_balance": 0.00, "ending_balance": 100.00},
        "transactions": [{"line_no": 1, "transaction_date": "2023-12-02",
                          "description": "Transfer from DDA (Sweep)", "amount": 100.00,
                          "physical_page_number": 1}],
    }]}


# ── 1. The vendor imprint is recognised for what it is ───────────────────
print("1. form-vendor names")
for name, want in [
    ("CSI", True), ("csi", True), ("CSI REV 3/12/18", True),
    ("Computer Services, Inc.", True), ("Fiserv", True), ("Jack Henry", True),
    ("FDIC", True), ("Visa", True),
    ("First Financial Bank", False), ("Chase", False),
    ("Charles Schwab", False), ("CSI Federal Credit Union", False),
]:
    check("%-28s -> %s" % (name, want), _is_form_vendor(name), want)

# ── 2. A vendor answer is discarded, not stored ──────────────────────────
print("2. vision answers with the form printer")
pdf_mod.pdf_service = Vision("CSI")
extracted = statement(None)
statement_service.resolve_missing_institutions(extracted, b"%PDF")
check("looked at the page", pdf_mod.pdf_service.calls, 1)
check("answer rejected", extracted["statements"][0]["account"]["institution"], None)

# ── 3. A real answer is taken ────────────────────────────────────────────
print("3. vision reads the letterhead")
pdf_mod.pdf_service = Vision("First Financial Bank")
extracted = statement(None)
statement_service.resolve_missing_institutions(extracted, b"%PDF")
check("name recovered", extracted["statements"][0]["account"]["institution"],
      "First Financial Bank")

# ── 4. A second account for a number we already hold is flagged ──────────
print("4. same last four, different name")
a, s, t = wire(existing=[{"id": 3, "matter_id": 1, "institution": "First Financial Bank",
                          "account_number_last4": "4527", "account_type": "savings"}])
summary = statement_service.commit_document(
    manager=None, matter_id=1, staff_id=1, extracted=statement("CSI"),
    raw_text="<<<PAGE 1>>>\nx\n",
)
codes = sorted(f["code"] for f in s.rows[0]["flags"])
check("flagged", "SAME_LAST4_DIFFERENT_INSTITUTION" in codes, True)
check("held for review", summary["results"][0]["status"], "needs_review")
note = next(f["note"] for f in s.rows[0]["flags"] if f["code"] == "SAME_LAST4_DIFFERENT_INSTITUTION")
check("names both", '"First Financial Bank"' in note and '"CSI"' in note, True)
check("says what to do", "Merge them" in note, True)
check("still committed, not dropped", len(a.rows), 2)

# ── 5. A genuinely new account is not flagged ────────────────────────────
print("5. a different account number")
a, s, t = wire(existing=[{"id": 3, "matter_id": 1, "institution": "First Financial Bank",
                          "account_number_last4": "4527", "account_type": "savings"}])
statement_service.commit_document(
    manager=None, matter_id=1, staff_id=1,
    extracted=statement("First Financial Bank", last4="9260"),
    raw_text="<<<PAGE 1>>>\nx\n",
)
codes = sorted(f["code"] for f in s.rows[0]["flags"])
check("no flag", "SAME_LAST4_DIFFERENT_INSTITUTION" in codes, False)

# ── 6. An unreadable number cannot be compared ───────────────────────────
print("6. no last four to match on")
a, s, t = wire(existing=[{"id": 3, "matter_id": 1, "institution": "First Financial Bank",
                          "account_number_last4": "4527", "account_type": "savings"}])
statement_service.commit_document(
    manager=None, matter_id=1, staff_id=1, extracted=statement("CSI", last4=None),
    raw_text="<<<PAGE 1>>>\nx\n",
)
codes = sorted(f["code"] for f in s.rows[0]["flags"])
check("no false pairing", "SAME_LAST4_DIFFERENT_INSTITUTION" in codes, False)
check("but the unmatched account is flagged", "NO_ACCOUNT_MATCH" in codes, True)

# ── 7. The vendor name arrives from the TEXT layer, not vision ───────────
# The commoner case, and the one that got through in production: "CSI REV
# 3/12/18" is in the page text, so extraction returns "CSI" and the statement
# never reaches the vision fallback at all.
print("7. primary extraction names the vendor")
pdf_mod.pdf_service = Vision(None)          # letterhead unreadable, as it really is
extracted = statement("CSI")
statement_service.resolve_missing_institutions(extracted, b"%PDF")
check("vendor name discarded", extracted["statements"][0]["account"]["institution"], None)
check("the page was consulted", pdf_mod.pdf_service.calls, 1)

print("8. vendor in text, letterhead readable by vision")
pdf_mod.pdf_service = Vision("First Financial Bank")
extracted = statement("CSI")
statement_service.resolve_missing_institutions(extracted, b"%PDF")
check("corrected from the page", extracted["statements"][0]["account"]["institution"],
      "First Financial Bank")

print("9. a real name in the text layer is left alone")
pdf_mod.pdf_service = Vision("Should Not Be Asked")
extracted = statement("First Financial Bank")
statement_service.resolve_missing_institutions(extracted, b"%PDF")
check("kept", extracted["statements"][0]["account"]["institution"], "First Financial Bank")
check("no page lookup", pdf_mod.pdf_service.calls, 0)


print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
