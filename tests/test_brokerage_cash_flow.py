"""
tests/test_brokerage_cash_flow.py - Brokerage statements, cash flow only.

This project traces sources and uses of cash. A stock purchase is neither: it
exchanges $8,500 of cash for $8,500 of stock inside one account, and the account
is worth what it was a second earlier. Nothing entered the marital estate and
nothing left it. So trades are not extracted at all -- what is extracted is
money that crossed the account boundary, plus dividends, interest, fees, and the
change in market value the statement prints.

The worked example throughout is Tom's: open at zero, $10,000 transferred in,
fifty purchases totalling $8,500, $90 interest, $110 dividends, $75 margin
interest, $1,000 wired out.

Run:  venv/Scripts/python.exe tests/test_brokerage_cash_flow.py
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.statement_service import (  # noqa: E402
    _STATEMENT_SYSTEM, _TRANSACTIONS_SYSTEM, _is_market_change,
    _portfolio_findings, statement_service,
)

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# ── The prompts must carry the rule ──────────────────────────────────────────

print("What the extractor is told")

for name, prompt in (("single-pass", _STATEMENT_SYSTEM), ("chunked", _TRANSACTIONS_SYSTEM)):
    lowered = prompt.lower()
    check_true("the %s prompt names the account types it applies to" % name,
               "brokerage" in lowered and "retirement" in lowered)
    check_true("  and says trades are not extracted" % (),
               "do not extract" in lowered)
    check_true("  and forbids DERIVING the market change",
               "never comput" in lowered or "never arithmetic" in lowered)
    check_true("  and says the balance is total account value, not cash",
               "total account value" in lowered)
    check_true("  and asks for the count of what it skipped",
               "securities_transactions_omitted" in prompt)

# THE ONE INSTRUCTION THAT MUST NOT BE SOFTENED. A change in market value
# computed as closing minus opening minus everything else always makes the
# statement balance -- so it would conceal a wire nobody extracted rather than
# reveal it. Both prompts say so explicitly.
for name, prompt in (("single-pass", _STATEMENT_SYSTEM), ("chunked", _TRANSACTIONS_SYSTEM)):
    check_true("the %s prompt explains WHY a derived figure is worse than none" % name,
               "balance" in prompt.lower() and "hide" in prompt.lower()
               or "conceal" in prompt.lower())

print("\nRecognising the change-in-value line")

check("the printed figure is recognised",
      _is_market_change({"description": "Change in Market Value"}), True)
check("case and padding do not matter",
      _is_market_change({"description": "  CHANGE IN MARKET VALUE  "}), True)
check("a wire is not it", _is_market_change({"description": "WIRE TO FIDELITY"}), False)
check("nor is a missing description", _is_market_change({}), False)


# ── The statement's own summary is the real check ────────────────────────────

print("\nChecking the extraction against the statement's own summary")

# Tom's example. Additions 10,000; withdrawals 1,000.
SUMMARY = {
    "opening_value": 0,
    "additions": 10000.00,
    "withdrawals": -1000.00,
    "income": 200.00,
    "change_in_market_value": 425.00,
    "closing_value": 9550.00,
}


def flows(*amounts):
    return [{"description": "Line %d" % i, "amount": a}
            for i, a in enumerate(amounts, start=1)]


complete = flows(10000, 90, 110, -75, -1000)
check("a complete extraction reports nothing", _portfolio_findings(complete, SUMMARY), [])

# THE FAILURE THIS EXISTS FOR. The ordinary check -- beginning + sum == ending --
# closes as soon as a change-in-value line is present, whatever that line says.
# A model that computed the figure rather than reading it would produce a
# statement that reconciles perfectly and hides a missing wire. Only the
# statement's own summary catches it.
missing_wire = flows(10000, 90, 110, -75)
found = _portfolio_findings(missing_wire, SUMMARY)
check("a missing withdrawal is caught", len(found), 1)
check_true("and named by side and amount",
           "withdrawals" in found[0] and "1000" in found[0])

missing_in = flows(90, 110, -75, -1000)
check_true("a missing addition is caught too",
           any("additions" in f for f in _portfolio_findings(missing_in, SUMMARY)))

# The market-change line is not a flow and must not be counted as one, or it
# would paper over the very shortfall this is looking for.
with_change = complete[:-1] + [{"description": "Change in Market Value", "amount": 1000}]
check_true("the change-in-value line does not stand in for a withdrawal",
           any("withdrawals" in f for f in _portfolio_findings(with_change, SUMMARY)))

# Reported only where the extraction falls SHORT. A statement may split its
# additions across named buckets, so extracting all of them can read as too many
# against one; a shortfall has no innocent explanation.
check("extracting MORE than the summary prints is not reported",
      _portfolio_findings(flows(15000, -1000), SUMMARY), [])

check("no summary, no finding", _portfolio_findings(complete, {}), [])


# ── End to end through commit ────────────────────────────────────────────────

print("\nCommitting a brokerage statement")


class Row(dict):
    __getattr__ = dict.get


class FakeAccounts:
    def __init__(self): self.rows = []
    def get_by_matter(self, m): return self.rows
    def find_match(self, m, inst, last4): return None
    def others_with_last4(self, m, inst, last4): return []
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeStatements:
    def __init__(self): self.rows = []
    def get_by_account(self, a): return []
    def find_period(self, a, s, e): return None
    def find_overlapping(self, a, s, e): return []
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


def commit(extracted, raw_text="<<<PAGE 1>>>\nSCHWAB BROKERAGE\n"):
    accounts, statements, transactions = FakeAccounts(), FakeStatements(), FakeTransactions()
    import services.statement_service as mod
    original = (mod.FinancialAccountRepository,
                mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository)
    mod.FinancialAccountRepository = lambda m: accounts
    mod.FinancialAccountStatementRepository = lambda m: statements
    mod.FinancialAccountTransactionRepository = lambda m: transactions
    try:
        statement_service.commit_document(
            manager=None, matter_id=1, staff_id=1,
            extracted=extracted, raw_text=raw_text,
        )
    finally:
        (mod.FinancialAccountRepository,
         mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository) = original
    return statements.rows[0], transactions.rows


def brokerage(lines, summary=None, omitted=0, ending=9550.00):
    body = {
        "account": {"institution": "Charles Schwab", "account_type": "brokerage",
                    "account_number_last4": "4448"},
        "period": {"start_date": "2025-03-01", "end_date": "2025-03-31"},
        "balances": {"beginning_balance": 0, "ending_balance": ending},
        "transactions": lines,
    }
    if summary is not None:
        body["portfolio_summary"] = summary
    if omitted:
        body["securities_transactions_omitted"] = omitted
    return {"statements": [body]}


def codes(row):
    return {f["code"] for f in row["flags"]}


# Tom's example, with the printed change in value present. Five lines, not
# fifty-five: the purchases are absent by design.
LINES = [
    {"line_no": 1, "transaction_date": "2025-03-02", "description": "TRANSFER IN FROM CHECKING",
     "amount": 10000, "physical_page_number": 1},
    {"line_no": 2, "transaction_date": "2025-03-10", "description": "BANK INTEREST",
     "amount": 90, "physical_page_number": 1},
    {"line_no": 3, "transaction_date": "2025-03-15", "description": "QUALIFIED DIVIDEND",
     "amount": 110, "physical_page_number": 1},
    {"line_no": 4, "transaction_date": "2025-03-20", "description": "MARGIN INTEREST",
     "amount": -75, "physical_page_number": 1},
    {"line_no": 5, "transaction_date": "2025-03-25", "description": "WIRE OUT TO FIRST NATIONAL",
     "amount": -1000, "physical_page_number": 1},
    {"line_no": 6, "transaction_date": "2025-03-31", "description": "Change in Market Value",
     "amount": 425, "physical_page_number": 1},
]

statement_row, written = commit(brokerage(LINES, SUMMARY, omitted=50))

check("five cash-flow lines plus the change in value", len(written), 6)
check("and the fifty purchases are nowhere in it",
      [t for t in written if "PURCHASE" in (t["description"] or "").upper()], [])

# 0 + 10000 + 90 + 110 - 75 - 1000 + 425 = 9550. The identity is unchanged: the
# market-change line is simply one of the amounts.
check("the balances tie against TOTAL ACCOUNT VALUE", statement_row["reconciled"], True)
check("with nothing left over", statement_row["reconciliation_delta"], Decimal("0.00"))

check_true("the omission is recorded, not silent",
           "SECURITIES_NOT_EXTRACTED" in codes(statement_row))
note = next(f for f in statement_row["flags"] if f["code"] == "SECURITIES_NOT_EXTRACTED")
check("info, because nothing is wrong", note["severity"], "info")
check_true("and it says how many, so a short register reads as deliberate",
           "50 securities transaction(s)" in note["note"])

check_true("the statement's own summary is kept as the second source",
           statement_row["portfolio_summary"] == SUMMARY)
check("nothing claims the extraction is short",
      "INCOMPLETE_EXTRACTION" in codes(statement_row), False)

print("\nWhen the statement prints no change-in-value figure")

# No printed figure means no line, which means the balances do not tie -- and
# they should not. What must NOT happen is a figure derived by subtraction: it
# would make every statement balance and hide a withdrawal nobody extracted.
no_change, _ = commit(brokerage(LINES[:-1], SUMMARY))
check("the statement does not tie", no_change["reconciled"], False)
check("and the gap is the market movement, reported not filled",
      no_change["reconciliation_delta"], Decimal("425.00"))
check_true("with a note saying which figure is missing and why it is not computed",
           "MARKET_CHANGE_NOT_PRINTED" in codes(no_change))
missing = next(f for f in no_change["flags"] if f["code"] == "MARKET_CHANGE_NOT_PRINTED")
check_true("that names the failure it is avoiding",
           "hide a withdrawal" in missing["note"])

print("\nThe bank-shaped completeness check must not be applied here")

# Its counts describe every printed row INCLUDING the fifty trades, so it would
# report a shortfall on every brokerage statement -- and a warning that is
# always on is a warning nobody reads.
bank_shaped = brokerage(LINES, SUMMARY, omitted=50)
bank_shaped["statements"][0]["printed_counts"] = {"deposits_credits": 3, "checks_debits": 53}
bank_shaped["statements"][0]["balances"]["printed_totals"] = {
    "deposits_credits": 10200.00, "checks_debits": -9575.00,
}
row, _ = commit(bank_shaped)
check("the printed row counts are not held against a cash-flow extraction",
      "INCOMPLETE_EXTRACTION" in codes(row), False)

print("\nA checking account is untouched by any of this")

CHECKING = {"statements": [{
    "account": {"institution": "First National Bank", "account_type": "checking",
                "account_number_last4": "4357"},
    "period": {"start_date": "2025-03-01", "end_date": "2025-03-31"},
    "balances": {"beginning_balance": 1000, "ending_balance": 900,
                 "printed_totals": {"checks_debits": -100.00}},
    "printed_counts": {"checks_debits": 1},
    "transactions": [
        {"line_no": 1, "transaction_date": "2025-03-04", "description": "COFFEE SHOP",
         "amount": -100, "physical_page_number": 1},
    ],
}]}
plain, _ = commit(CHECKING, raw_text="<<<PAGE 1>>>\nFIRST NATIONAL BANK\n")
check("no securities note on a bank statement",
      "SECURITIES_NOT_EXTRACTED" in codes(plain), False)
check("no market-change note either",
      "MARKET_CHANGE_NOT_PRINTED" in codes(plain), False)
check("and it still reconciles the way it always did", plain["reconciled"], True)
check("its summary column stays empty", plain["portfolio_summary"], None)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all brokerage cash-flow checks passed")
