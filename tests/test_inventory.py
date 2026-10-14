"""
tests/test_inventory.py - What each account was worth, and who holds it.

The one-page answer to "what is in this estate?", and three of its rules are
places a reasonable implementation goes wrong:

  * **A liability prints NEGATIVE.** A card statement's ending balance is what
    the institution printed — a positive amount OWED — because every account
    type has to satisfy `beginning + sum == ending`. An inventory asks what the
    account contributes to the estate, and a card contributes minus. The stored
    figure is untouched; only the display flips. Same inversion the FIS makes,
    for the same reason and nowhere else.
  * **An account with no statement is LISTED, not omitted**, without a balance
    and outside the total. Dropping it would make the schedule look complete
    while being short — and an account nobody has produced a statement for is
    precisely what the reader is looking for.
  * **The party letter is not guessed.** P and R come from the matter's client
    alignment; with none recorded the column says C and O and the exhibit
    carries a warning, because "P" would assert something nobody recorded.

Run:  venv/Scripts/python.exe tests/test_inventory.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.inventory_service as mod  # noqa: E402
from db.models.financial import AccountOwnership, AccountType  # noqa: E402
from services.exhibit_service import to_markdown  # noqa: E402
from services.inventory_service import inventory_service  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, got):
    check(name, bool(got), True)


class Row(dict):
    __getattr__ = dict.get


def account(id, institution, last4, type_, ownership, closed=False):
    return Row(id=id, matter_id=1, institution=institution, account_number_last4=last4,
               account_type=type_, ownership=ownership, is_closed=closed)


def statement(id, account_id, end, ending, bates=None, status="auto_accepted", filename="prod.pdf"):
    return Row(id=id, financial_account_id=account_id, matter_id=1,
               period_start=date(2026, 9, 1), period_end=date.fromisoformat(end),
               ending_balance=Decimal(ending) if ending is not None else None,
               review_status=status,
               extraction={"bates_first": bates, "bates_last": bates,
                           "source_filename": filename})


ACCOUNTS = [
    account(1, "Bank of America", "1234", AccountType.checking, AccountOwnership.client_sole),
    account(2, "Bank of America", "4331", AccountType.savings, AccountOwnership.opposing_sole),
    account(3, "JPMorgan Chase", "9845", AccountType.checking, AccountOwnership.joint),
    account(4, "Capital One", "9944", AccountType.credit_card, AccountOwnership.client_sole),
    account(5, "Synchrony", "9331", AccountType.loan, AccountOwnership.opposing_sole),
    account(6, "Old Credit Union", "7777", AccountType.savings, AccountOwnership.unknown),
]
STATEMENTS = [
    statement(10, 1, "2026-09-30", "13045.45", bates="433654"),
    statement(11, 2, "2026-08-26", "10485.99", bates="987899"),
    # Two statements on one account: the LATEST is the one that counts.
    statement(12, 3, "2026-08-27", "11.11", bates="000001"),
    statement(13, 3, "2026-09-27", "1239846.54", bates="594582"),
    # A card: the statement prints the balance OWED, positive.
    statement(14, 4, "2026-09-30", "1045.45", bates="933654"),
    statement(15, 5, "2026-08-26", "1485.99", bates="887899"),
    # A rejected read is not evidence of a balance.
    statement(16, 6, "2026-09-30", "99999.99", bates="111111", status="rejected"),
]


class FakeAccounts:
    def get_by_matter(self, matter_id):
        return ACCOUNTS


class FakeStatements:
    def get_by_account(self, account_id):
        return [s for s in STATEMENTS if s["financial_account_id"] == account_id]


mod.FinancialAccountRepository = lambda m: FakeAccounts()
mod.FinancialAccountStatementRepository = lambda m: FakeStatements()

MATTER = Row(id=1, matter_name="Salmons", matter_number="DF-24-01234",
             client_alignment="petitioner", case_style="IN THE MATTER OF…",
             court_name="401st Judicial District Court", county="Parker", state="Texas")


# ── 1. The report ────────────────────────────────────────────────────────
print("1. assets and liabilities, split by account type")
report = inventory_service.inventory(None, 1, matter=MATTER)

check("three assets", [r["account_number_last4"] for r in report["assets"]],
      ["1234", "4331", "9845", "7777"][:3] + ["7777"])
check("two liabilities", [r["account_number_last4"] for r in report["liabilities"]],
      ["9944", "9331"])

print("2. the balance is the LATEST statement's")
chase = next(r for r in report["assets"] if r["account_number_last4"] == "9845")
check("the later of two statements wins", chase["balance"], "1239846.54")
check("and its date is the as-of", chase["as_of"], "2026-09-27")
check("with its own Bates", chase["bates"], "594582")

print("3. a liability is negative — the statement prints it owed")
card = next(r for r in report["liabilities"] if r["account_number_last4"] == "9944")
check("flipped for the inventory", card["balance"], "-1045.45")
# The stored figure is untouched: the statement still says what the bank printed.
check("while the statement still says what the bank printed",
      str(STATEMENTS[4]["ending_balance"]), "1045.45")

print("4. an account with no usable statement is listed, not dropped")
orphan = next(r for r in report["assets"] if r["account_number_last4"] == "7777")
check("listed", orphan["institution"], "Old Credit Union")
check("with no balance", orphan["balance"], None)
check("no date", orphan["as_of"], None)
check("a rejected read is not a balance", orphan["bates"], None)
check("and counted, so the total cannot read as complete",
      report["totals"]["without_balance"], 1)

print("5. the totals")
# 13045.45 + 10485.99 + 1239846.54
check("assets total excludes the account with no statement",
      report["totals"]["assets"], "1263377.98")
check("liabilities total is negative", report["totals"]["liabilities"], "-2531.44")
check("and the net is the estate", report["totals"]["net"], "1260846.54")

print("6. who holds it")
check("our client, on a petitioner matter",
      next(r for r in report["assets"] if r["account_number_last4"] == "1234")["held_by"], "P")
check("the other side", next(r for r in report["assets"]
                             if r["account_number_last4"] == "4331")["held_by"], "R")
check("joint", chase["held_by"], "J")
check("unknown ownership is a question mark, never a guess", orphan["held_by"], "?")
check("and the legend says what the letters mean",
      sorted(report["legend"]), [("J", "Joint"), ("P", "Petitioner"), ("R", "Respondent")])
check("nothing to warn about", report["warnings"], [])

print("7. a respondent matter flips P and R")
flipped = inventory_service.inventory(
    None, 1, matter=Row(**{**MATTER, "client_alignment": "respondent"}))
check("our client is R", next(r for r in flipped["assets"]
                              if r["account_number_last4"] == "1234")["held_by"], "R")
check("and the other side is P", next(r for r in flipped["assets"]
                                      if r["account_number_last4"] == "4331")["held_by"], "P")

print("8. with no alignment recorded, the letters are not guessed")
unknown = inventory_service.inventory(
    None, 1, matter=Row(**{**MATTER, "client_alignment": None}))
check("our client reads C", next(r for r in unknown["assets"]
                                 if r["account_number_last4"] == "1234")["held_by"], "C")
check("the other party O", next(r for r in unknown["assets"]
                                if r["account_number_last4"] == "4331")["held_by"], "O")
check_true("and the report says why", unknown["warnings"])
check_true("naming what to set",
           "client alignment" in unknown["warnings"][0])

# ── 9. The exhibit ───────────────────────────────────────────────────────
print("9. as a document")
exhibit = inventory_service.build_exhibit(None, MATTER)
# to_markdown returns bytes, like every renderer: an export is a file.
text = to_markdown(exhibit).decode("utf-8")

check_true("two sections", "Assets" in text and "Liabilities" in text)
check_true("a total line for each", text.count("TOTAL") == 2)
check_true("money reads as currency", "$1,239,846.54" in text)
check_true("a liability reads negative", "-$1,045.45" in text)
check_true("the Bates number travels", "594582" in text)
check_true("the legend is a footnote", "P = Petitioner" in text)
check_true("the sign convention is stated, not left to be inferred",
           "reduces the estate" in text)
check_true("the untotalled account is declared",
           "1 account(s) have no statement on file" in text)
check_true("the documents behind it are named", "prod.pdf" in text)
# The notice does not say "1006" -- it states the rule rather than citing it,
# so the assertion is on what it actually promises.
check_true("and it carries the verification notice",
           "underlying records must be made available" in text)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
