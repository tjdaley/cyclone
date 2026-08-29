"""
The model returns null for fields it cannot read, and null is not absent.

`d.get("institution", "")` returns None when the key is present and set to
null — a default only applies to a missing key. That crashed the worker before
it committed anything, so a whole batch failed with nothing on screen to say so.

The field it happened to is the one most likely to be null: the institution name
lives in the letterhead graphic on many statements, so failing to read it is the
normal outcome, not the rare one.
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
        return next((a for a in self.rows if a["account_number_last4"] == l4
                     and a["institution"].lower() == i.lower()), None)
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


class NoVision:
    """A vision lookup that cannot answer — the common case for a scanned logo."""
    calls = 0

    def ask_page(self, pdf_bytes, page, prompt):
        NoVision.calls += 1
        return None


def statement_with(**account_fields):
    account = {"institution": None, "account_type": "savings",
               "account_number_last4": None, "account_number_masked": None,
               "name_on_account": None}
    account.update(account_fields)
    return {
        "account": account,
        "period": {"start_date": "2024-01-01", "end_date": "2024-01-31"},
        "balances": {"beginning_balance": 0.00, "ending_balance": 100.00},
        "transactions": [{
            "line_no": 1, "transaction_date": "2024-01-02", "posted_date": None,
            "description": "Transfer from DDA (Sweep)", "counterparty": None,
            "location": None, "amount": 100.00, "running_balance": None,
            "physical_page_number": 1, "bates_number": None, "category": None,
        }],
    }


# ── 1. institution: null must not crash the pre-commit fixup ─────────────
print("1. institution reported as null")
mod_pdf = sys.modules.get("services.pdf_service")
import services.pdf_service as pdf_mod  # noqa: E402
real = pdf_mod.pdf_service
pdf_mod.pdf_service = NoVision()
try:
    extracted = {"statements": [statement_with()]}
    statement_service.resolve_missing_institutions(extracted, b"%PDF-fake")
    check("did not raise", True, True)
    check("looked at the page", NoVision.calls, 1)
    check("left the name alone when the page could not answer",
          extracted["statements"][0]["account"]["institution"], None)

    # ── 2. and the commit still lands ────────────────────────────────────
    print("2. commits under a placeholder name")
    a, s, t = wire()
    summary = statement_service.commit_document(
        manager=None, matter_id=1, staff_id=1, extracted=extracted,
        raw_text="<<<PAGE 1>>>\nACCOUNT NUMBER 81120014527\n",
    )
    check("one statement written", summary["statements_found"], 1)
    check("account created", len(a.rows), 1)
    check("named honestly", a.rows[0]["institution"], "Unknown institution")

    # ── 3. every other string field null too ─────────────────────────────
    print("3. every optional string null")
    extracted = {"statements": [statement_with()]}
    extracted["statements"][0]["transactions"][0]["description"] = None
    a, s, t = wire()
    summary = statement_service.commit_document(
        manager=None, matter_id=1, staff_id=1, extracted=extracted,
        raw_text="<<<PAGE 1>>>\nx\n",
    )
    check("still commits", summary["statements_found"], 1)
    check("description falls back", t.rows[0]["description"], "(no description)")

    # ── 4. a mix of named and null across one upload ─────────────────────
    print("4. one named, one null")
    NoVision.calls = 0
    extracted = {"statements": [
        statement_with(institution="First Financial Bank", account_number_last4="4527"),
        statement_with(),
    ]}
    statement_service.resolve_missing_institutions(extracted, b"%PDF-fake")
    check("did not raise", True, True)
    # Only the unnamed one is worth a page lookup; the named one is not in
    # dispute, because a missing name is not a competing name.
    check("one lookup, not two", NoVision.calls, 1)
    check("named one untouched",
          extracted["statements"][0]["account"]["institution"], "First Financial Bank")
finally:
    pdf_mod.pdf_service = real

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
