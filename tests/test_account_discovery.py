"""
tests/test_account_discovery.py - Reading account numbers out of transfer lines.

Every description in the parser cases below is a real line from a produced
statement in the Salmons matter (First Financial x9260, Bank of Texas), plus
the two shapes Tom described from other productions. The noise cases matter as
much as the hits: a confirmation number and a card timestamp are digit runs
sitting inside a transfer description, and reading either one as an account
invents an undisclosed account out of nothing.

Run:  .venv/Scripts/python.exe tests/test_account_discovery.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.account_discovery_service import (  # noqa: E402
    AccountDiscoveryService, _last4, _references,
)

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeAccount:
    def __init__(self, id, institution, last4, ownership="unknown"):
        self.id = id
        self.institution = institution
        self.account_number_last4 = last4
        self.ownership = ownership


class FakeTransaction:
    def __init__(self, id, account_id, description, amount, when=None):
        self.id = id
        self.financial_account_id = account_id
        self.description = description
        self.amount = Decimal(amount)
        self.transaction_date = when


class FakeAccountRepo:
    def __init__(self, accounts):
        self._accounts = accounts

    def get_by_matter(self, matter_id):
        return self._accounts


class FakeStatementRepo:
    def __init__(self, rejected=None):
        self._rejected = rejected or []

    def rejected_ids(self, matter_id):
        return self._rejected


class FakeTransactionRepo:
    """Applies the same substring filter PostgREST would, so paging is exercised."""

    def __init__(self, rows, page_cap=1000):
        self._rows = rows
        self._page_cap = page_cap
        self.calls = 0

    def search(self, account_ids, exclude_statement_ids=None, text=None,
               limit=200, offset=0, **kwargs):
        self.calls += 1
        matches = [
            r for r in self._rows
            if r.financial_account_id in account_ids
            and (text is None or text.lower() in (r.description or "").lower())
        ]
        size = min(limit, self._page_cap)
        return matches[offset:offset + size], len(matches)


def run(accounts, rows, page_cap=1000):
    """Drive the service against fakes and return (results, transaction_repo)."""
    import services.account_discovery_service as mod

    account_repo = FakeAccountRepo(accounts)
    statement_repo = FakeStatementRepo()
    transaction_repo = FakeTransactionRepo(rows, page_cap=page_cap)

    original = (mod.FinancialAccountRepository,
                mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository)
    mod.FinancialAccountRepository = lambda m: account_repo
    mod.FinancialAccountStatementRepository = lambda m: statement_repo
    mod.FinancialAccountTransactionRepository = lambda m: transaction_repo
    try:
        return AccountDiscoveryService().undisclosed(object(), 1), transaction_repo
    finally:
        (mod.FinancialAccountRepository,
         mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository) = original


# ── Parser ───────────────────────────────────────────────────────────────────

print("\nReading references out of a description")

# Real First Financial x9260 lines.
check("Acct No. — full number, no institution named",
      _references("Transfer from DDA (Sweep) Acct No. 81110044625"),
      [("81110044625", None)])

check("Acct No. with a suffix — the suffix is not part of the number",
      _references("Transfer to DDA        Acct No.  86110018909-D"),
      [("86110018909", None)])

check("masked pair — both sides come back, caller drops its own",
      _references("Transfer from XXX4070 to XXX9260: Conf #:19842192"),
      [("4070", None), ("9260", None)])

check("Savings sweep",
      _references("Transfer to Savings (Sweep) Acct No. 81120014527"),
      [("81120014527", None)])

# Bank of Texas.
check("labelled with an account TYPE — CHKG is not an institution",
      _references("INTERNET XFER FROM CHKG 8098386837"),
      [("8098386837", None)])

# The shape Tom described from other productions.
check("labelled with an institution NAME — kept",
      _references("TRANSFER FROM CHASE 4321"),
      [("4321", "CHASE")])

print("\nBanks disagree about the mask character")

# Chase masks with dots and leaves no space before the digits, so none of the
# three patterns reached it: not the XX mask, not "Acct", and not "to <word>
# <space> <digits>". x9323 sat in a produced statement, referenced by name, and
# never appeared on the undisclosed list.
check("Chase dot mask, the line that found this",
      _references("12/15 Online Transfer To Chk ...9323 Transaction#: 19012059496"),
      [("9323", None)])
check("and the other direction",
      _references("Online Transfer From Chk ...4448 Transaction#: 22385628138"),
      [("4448", None)])
check("asterisks", _references("Transfer to ****1234"), [("1234", None)])
check("hashes", _references("XFER TO ####5678"), [("5678", None)])
check("middle dots", _references("Transfer to ••••4321"), [("4321", None)])

print("\nA single mask character is not a mask")

# Two or more is the rule, and it has to be: a lone dot is the one in "Acct No."
# and in every decimal amount printed on the statement, and a lone hyphen is in
# every date.
check("a decimal amount is not an account",
      _references("Transfer of 1,234.56 to savings"), [])
check("a date is not an account",
      _references("Transfer on 2024-11-15 completed"), [])
check("the dot in 'Acct No.' still resolves through the Acct pattern",
      _references("Transfer from DDA (Sweep) Acct No. 81110044625"),
      [("81110044625", None)])

print("\nTransaction numbers are stripped, like conf and ref numbers")

check("a transaction number alone yields nothing",
      _references("Transfer completed Transaction#: 19012059496"), [])
check("trace number", _references("XFER TO ...4321 Trace# 998877665"), [("4321", None)])
check("auth number", _references("Transfer to ...4321 Auth: 55512345"), [("4321", None)])
check("'Transfer' is not mistaken for 'transaction' and stripped",
      _references("Transfer to XXX4070"), [("4070", None)])


print("\nNumbers that are not accounts")

check("confirmation number alone is not an account",
      _references("Transfer XXX9260: Conf #:19842192"),
      [("9260", None)])

check("merchant instant transfer — no account number at all",
      _references("INST XFER PAYPAL WEB LULULEMONUS TYPE S"),
      [])

check("brokerage transfer with an alphanumeric reference, not a number",
      _references("Transfer Acorns Invest WEB 9Z920C1 TYPE S"),
      [])

check("card timestamp is stripped",
      _references("XFER TO XXX5150 231212 103658"),
      [("5150", None)])

check("a description that is not a transfer is never parsed",
      _references("CHECK CARD PURCHASE 4820 HEB GROCERY 1234567"),
      [])

check("empty description",
      _references(""),
      [])

print("\nLast four")
check("full number", _last4("81110044625"), "4625")
check("already four", _last4("4070"), "4070")
check("too short to identify an account", _last4("123"), None)


# ── The workflow ─────────────────────────────────────────────────────────────

print("\nAn account the matter does not hold")

held = [FakeAccount(1, "First Financial Bank", "9260"),
        FakeAccount(2, "First Financial Bank", "4527")]
rows = [
    # Its own number and one the matter holds — neither is a discovery.
    FakeTransaction(1, 1, "Transfer to Savings (Sweep) Acct No. 81120014527", "-2500.00", date(2023, 3, 4)),
    # An outside account, twice, in both directions.
    FakeTransaction(2, 1, "Transfer from XXX4070 to XXX9260: Conf #:19842192", "10000.00", date(2023, 3, 6)),
    FakeTransaction(3, 1, "Transfer from XXX9260 to XXX4070: Conf #:19842200", "-4000.00", date(2023, 5, 9)),
]
results, repo = run(held, rows)

check("only the outside account is reported", [r["last4"] for r in results], ["4070"])
entry = results[0]
check("mentions counted", entry["mentions"], 2)
check("money in — the sign says it came from them", entry["money_in"], Decimal("10000.00"))
check("money out — the sign says it went to them", entry["money_out"], Decimal("4000.00"))
check("net", entry["net"], Decimal("6000.00"))
check("first seen", entry["first_seen"], date(2023, 3, 6))
check("last seen", entry["last_seen"], date(2023, 5, 9))
check("institution inferred from the statement it sat on",
      entry["institution"], "First Financial Bank")
check("and flagged as inferred", entry["institution_inferred"], True)
check("named the account it was seen on", entry["seen_on"], ["First Financial Bank ····9260"])
check("the search was pushed to the database, not paged over everything",
      repo.calls, 2)

print("\nAn institution the description names outright")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [FakeTransaction(1, 1, "TRANSFER FROM CHASE 4321", "7500.00", date(2023, 4, 1))]
results, _ = run(held, rows)
check("reported", [r["last4"] for r in results], ["4321"])
check("institution read off the page", results[0]["institution"], "CHASE")
check("not inferred — so no dagger", results[0]["institution_inferred"], False)

print("\nA later mention that names the bank beats an earlier inference")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(1, 1, "Transfer to XXX4321", "-100.00", date(2023, 4, 1)),
    FakeTransaction(2, 1, "TRANSFER FROM CHASE 4321", "200.00", date(2023, 4, 2)),
]
results, _ = run(held, rows)
check("one account, not two", len(results), 1)
check("upgraded to the named institution", results[0]["institution"], "CHASE")
check("no longer inferred", results[0]["institution_inferred"], False)
check("both mentions kept", results[0]["mentions"], 2)

print("\nThe same account written two ways")

# 86110018909 and XXX8909 are the same account. Deduping on last four is what
# merges them; deduping on the printed string would report two.
held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(1, 1, "Transfer to DDA        Acct No.  86110018909-D", "-1000.00", date(2023, 6, 1)),
    FakeTransaction(2, 1, "Transfer from XXX9260 to XXX8909", "-500.00", date(2023, 6, 8)),
]
results, _ = run(held, rows)
check("merged into one account", len(results), 1)
check("mentions across both spellings", results[0]["mentions"], 2)
check("quotes the longest form seen", results[0]["reference"], "86110018909")
check("money out summed", results[0]["money_out"], Decimal("1500.00"))

print("\nA line matching both search terms is counted once")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [FakeTransaction(1, 1, "TRANSFER / XFER TO XXX7777", "-300.00", date(2023, 7, 1))]
results, _ = run(held, rows)
check("one mention, not two", results[0]["mentions"], 1)
check("counted once", results[0]["money_out"], Decimal("300.00"))

print("\nPaging")

# 250 transfer lines against a 100-row cap: three pages per term. A scan that
# does not page reports only what fits in the first page and looks complete.
held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(i, 1, "Transfer to XXX4070", "-10.00", date(2023, 1, 1))
    for i in range(1, 251)
]
results, repo = run(held, rows, page_cap=100)
check("every line reached the tally", results[0]["mentions"], 250)
check("money out complete", results[0]["money_out"], Decimal("2500.00"))

print("\nEdge cases")

results, _ = run([], [FakeTransaction(1, 1, "Transfer to XXX4070", "-10.00")])
check("a matter with no accounts returns nothing", results, [])

held = [FakeAccount(1, "First Financial Bank", "9260")]
results, _ = run(held, [])
check("no transfers at all", results, [])

# A null date must not become a sort key or a comparison against None.
rows = [FakeTransaction(1, 1, "Transfer to XXX4070", "-10.00", None)]
results, _ = run(held, rows)
check("a dateless line still counts", results[0]["mentions"], 1)
check("and leaves the window empty", results[0]["first_seen"], None)

# A null amount is what an unreconciled extraction can leave behind.
rows = [FakeTransaction(1, 1, "Transfer to XXX4070", "0.00", date(2023, 1, 1))]
rows[0].amount = None
results, _ = run(held, rows)
check("a null amount does not crash the tally", results[0]["net"], Decimal("0.00"))

print("\nOrdering")

held = [FakeAccount(1, "First Financial Bank", "9260")]
rows = [
    FakeTransaction(1, 1, "Transfer to XXX1111", "-5.00", date(2023, 1, 1)),
    FakeTransaction(2, 1, "Transfer to XXX2222", "-5.00", date(2023, 1, 2)),
    FakeTransaction(3, 1, "Transfer to XXX2222", "-5.00", date(2023, 1, 3)),
]
results, _ = run(held, rows)
check("busiest account first", [r["last4"] for r in results], ["2222", "1111"])

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all account-discovery checks passed")
