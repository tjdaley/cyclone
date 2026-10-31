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

# ── 10. Excluded from the totals (050) ───────────────────────────────────
# Listed, so the reader sees the account was considered; never summed, so a
# stale balance does not move the estate. Marked in every format.
print("10. an excluded account is listed and left out of the totals")
from services.exhibit_service import to_csv, to_pdf  # noqa: E402

ACCOUNTS[1]["excluded_from_inventory"] = True            # BofA savings, 10485.99
ACCOUNTS[1]["inventory_exclusion_reason"] = "stale"
ACCOUNTS[4]["excluded_from_inventory"] = True            # Synchrony loan, -1485.99
ACCOUNTS[4]["inventory_exclusion_reason"] = "other"
ACCOUNTS[4]["inventory_exclusion_note"] = "Paid off at mediation"
ACCOUNTS[5]["excluded_from_inventory"] = True            # no usable statement

ex = inventory_service.inventory(None, 1, matter=MATTER)
savings = next(r for r in ex["assets"] if r["account_number_last4"] == "4331")
check("still listed, with its balance", savings["balance"], "10485.99")
check("flagged", savings["excluded"], True)
check("with the reason in words", savings["exclusion_label"], "Stale")
check("'other' shows the note", next(r for r in ex["liabilities"]
      if r["account_number_last4"] == "9331")["exclusion_label"], "Paid off at mediation")
check("assets total leaves it out", ex["totals"]["assets"], "1252891.99")
check("liabilities total leaves it out", ex["totals"]["liabilities"], "-1045.45")
check("so does the net", ex["totals"]["net"], "1251846.54")
check("and the count is reported", ex["totals"]["excluded"], 3)
check("an excluded account is not also 'without a balance'",
      ex["totals"]["without_balance"], 0)

doc = inventory_service.build_exhibit(None, MATTER)
md = to_markdown(doc).decode("utf-8")
check_true("the row carries the dagger", "Bank of America †" in md)
check_true("the footnote explains it, with the reason",
           "† Bank of America ····4331: listed, not included in the totals (Stale)"
           in md)
check_true("the selection says how many", "3 account(s), marked †" in md)
check_true("who excluded it stays off the exhibit", "excluded by" not in md.lower())
csv_text = to_csv(doc).decode("utf-8-sig")
check_true("the CSV has a column to filter on", "Excluded from totals" in csv_text)
check_true("carrying the reason", "yes: Stale" in csv_text)
check_true("the PDF still renders", to_pdf(doc)[:4] == b"%PDF")

for i in (1, 4, 5):
    for key in ("excluded_from_inventory", "inventory_exclusion_reason", "inventory_exclusion_note"):
        ACCOUNTS[i].pop(key, None)

print("11. setting the exclusion stamps who and when, and 'other' needs a note")
from db.models.financial import InventoryExclusionReason  # noqa: E402

writes = []


class WritableAccounts(FakeAccounts):
    def select_one(self, condition):
        return next((a for a in ACCOUNTS if a["id"] == condition["id"]), None)

    def update(self, id, data):
        writes.append((id, data))
        return Row(**data)


mod.FinancialAccountRepository = lambda m: WritableAccounts()
inventory_service.set_exclusion(None, 2, True, 7, "Thomas J. Daley")
check("excluded", writes[-1][1]["excluded_from_inventory"], True)
check("by name", writes[-1][1]["inventory_excluded_by"], "Thomas J. Daley")
check_true("and when", writes[-1][1]["inventory_excluded_at"] is not None)
check("no reason is fine", writes[-1][1]["inventory_exclusion_reason"], None)

try:
    inventory_service.set_exclusion(None, 2, True, 7, "T", reason=InventoryExclusionReason.other)
    check("'other' with no note is refused", "accepted", "refused")
except ValueError:
    check("'other' with no note is refused", "refused", "refused")

inventory_service.set_exclusion(None, 2, True, 7, "T", reason=InventoryExclusionReason.stale,
                                note="ignored")
check("a canned reason drops a stray note", writes[-1][1]["inventory_exclusion_note"], None)

inventory_service.set_exclusion(None, 2, False, 7, "T")
check("including it again clears the attribution", writes[-1][1]["inventory_excluded_by"], None)
try:
    inventory_service.set_exclusion(None, 999, True, 7, "T")
    check("a missing account is a lookup error", "ok", "LookupError")
except LookupError:
    check("a missing account is a lookup error", "LookupError", "LookupError")

# ── 12. Foreign currency (051) ───────────────────────────────────────────
# Converted on the way out at a rate somebody entered, and said so in words.
# With no rate the balance is listed in its own currency and kept out of the
# totals -- a rupee figure in a dollar column is the bug this exists to stop.
print("12. a foreign balance is converted at the matter's rate, or left out")
mod.FinancialAccountRepository = lambda m: FakeAccounts()
ACCOUNTS.append(account(7, "Habib Bank", "5555", AccountType.savings, AccountOwnership.client_sole))
ACCOUNTS[-1]["currency"] = "PKR"
STATEMENTS.append(statement(17, 7, "2026-09-30", "10000000.00", bates="777001"))

RATES: dict = {}


class FakeRates:
    def by_matter(self, matter_id):
        return RATES


mod.MatterCurrencyRateRepository = lambda m: FakeRates()

no_rate = inventory_service.inventory(None, 1, matter=MATTER)
hbl = next(r for r in no_rate["assets"] if r["account_number_last4"] == "5555")
check("no rate: no dollar balance", hbl["balance"], None)
check("but the printed figure is kept", hbl["native_balance"], "10000000.00")
check("and counted as unconverted", no_rate["totals"]["unconverted"], 1)
check("not as 'no statement'", no_rate["totals"]["without_balance"], 1)
check("assets unchanged by it", no_rate["totals"]["assets"], "1263377.98")
check("the matter's foreign currencies are listed", no_rate["currencies"], ["PKR"])
md_no_rate = to_markdown(inventory_service.build_exhibit(None, MATTER)).decode("utf-8")
check_true("the exhibit prints it in rupees, never dollars", "PKR 10,000,000.00" in md_no_rate)
check_true("and says it is not totalled", "1 account(s) are in a currency with no rate" in md_no_rate)

RATES["PKR"] = Row(currency="PKR", usd_per_unit=Decimal("0.0036000000"), as_of=date(2026, 10, 29),
                   source="State Bank of Pakistan", entered_by="Thomas J. Daley")
rated = inventory_service.inventory(None, 1, matter=MATTER)
hbl = next(r for r in rated["assets"] if r["account_number_last4"] == "5555")
check("converted to dollars", hbl["balance"], "36000.00")
check("the rate reads as typed", hbl["usd_per_unit"], "0.0036")
check("annotated in words", hbl["conversion_note"],
      "Balance of PKR 10,000,000.00 converted to USD at 0.0036 USD/PKR "
      "(rate as of 10/29/2026; State Bank of Pakistan)")
check("and now in the total", rated["totals"]["assets"], "1299377.98")
check("nothing unconverted", rated["totals"]["unconverted"], 0)
md = to_markdown(inventory_service.build_exhibit(None, MATTER)).decode("utf-8")
check_true("the row is marked", "Habib Bank ‡" in md)
check_true("the footnote carries the conversion",
           "converted to USD at 0.0036 USD/PKR" in md)
csv_text = to_csv(inventory_service.build_exhibit(None, MATTER)).decode("utf-8-sig")
check_true("the CSV keeps the printed figure and the rate", "PKR,10000000.00,0.0036" in csv_text)

print("13. an all-dollar matter never reads the rate table")
ACCOUNTS.pop(); STATEMENTS.pop()


def _boom(m):
    raise AssertionError("rates read on an all-dollar matter")


mod.MatterCurrencyRateRepository = _boom
check("no warning, no read", inventory_service.inventory(None, 1, matter=MATTER)["warnings"], [])

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
