"""
tests/test_undisclosed_exhibit.py - The motion-to-compel attachment.

The undisclosed-account list is a different shape from a transaction exhibit:
the rows are accounts, not lines. What this suite guards is that the shape
survives into the document, and that the dagger marking an inferred institution
travels WITH its footnote. A qualification whose explanation stayed behind on
the screen is worse than none — the reader sees that something is being
hedged and cannot tell what.

Run:  venv/Scripts/python.exe tests/test_undisclosed_exhibit.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.financial import AccountType  # noqa: E402
from db.models.matter import ClientAlignment  # noqa: E402
from services.account_discovery_service import AccountDiscoveryService  # noqa: E402
from services.exhibit_service import to_csv, to_markdown, to_pdf  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


class FakeMatter:
    id = 1
    matter_number = "DF-24-01234"
    court_name = "401st Judicial District Court"
    county = "Parker"
    state = "Texas"
    matter_name = "Salmons divorce"
    case_style = "IN THE MATTER OF THE MARRIAGE OF GABRIEL SALMONS AND ANA SALMONS"
    client_alignment = ClientAlignment.petitioner


class FakeAccount:
    def __init__(self, id, institution, last4, account_type=AccountType.checking):
        self.id = id
        self.institution = institution
        self.account_number_last4 = last4
        self.account_type = account_type


class FakeTransaction:
    def __init__(self, id, account_id, description, amount, when, category_id=None):
        self.category_id = category_id
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
    def rejected_ids(self, matter_id):
        return []


class FakeTransactionRepo:
    def __init__(self, rows):
        self._rows = rows

    def search(self, account_ids, exclude_statement_ids=None, text=None,
               limit=200, offset=0, **kwargs):
        matches = [
            r for r in self._rows
            if r.financial_account_id in account_ids
            and (text is None or text.lower() in (r.description or "").lower())
        ]
        return matches[offset:offset + limit], len(matches)


ACCOUNTS = [FakeAccount(1, "First Financial Bank", "9260")]
ROWS = [
    # Inferred institution — no bank named in the description.
    FakeTransaction(1, 1, "Transfer to XXX4070", "-25000.00", date(2023, 3, 4)),
    FakeTransaction(2, 1, "Transfer from XXX4070", "10000.00", date(2023, 9, 8)),
    # Stated institution — read off the page, so no dagger.
    FakeTransaction(3, 1, "TRANSFER FROM CHASE 4321", "5000.00", date(2023, 5, 1)),
]


class FakeCategoryRepo:
    """The category side of creditor discovery, which the exhibit now includes."""

    def __init__(self, liability_ids=()):
        self._ids = list(liability_ids)

    def liability_ids(self):
        return self._ids


class FakeClassificationRepo:
    def __init__(self, rulings=()):
        self._rulings = list(rulings)

    def available_for_matter(self, matter_id, include_inactive=False):
        return self._rulings


class FakeRuling:
    def __init__(self, id, pattern, classification, name=None, kind=None):
        self.id = id
        self.pattern = pattern
        self.classification = classification
        self.creditor_name = name
        self.creditor_type = kind


def build(accounts=ACCOUNTS, rows=ROWS, name="Accounts Referenced But Not Produced",
          liability_ids=(), rulings=()):
    import services.account_discovery_service as mod

    original = (mod.FinancialAccountRepository,
                mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository,
                mod.TransactionCategoryRepository,
                mod.PayeeClassificationRepository)
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo(accounts)
    mod.FinancialAccountStatementRepository = lambda m: FakeStatementRepo()
    mod.FinancialAccountTransactionRepository = lambda m: FakeTransactionRepo(rows)
    mod.TransactionCategoryRepository = lambda m: FakeCategoryRepo(liability_ids)
    mod.PayeeClassificationRepository = lambda m: FakeClassificationRepo(rulings)
    try:
        return AccountDiscoveryService().build_exhibit(object(), FakeMatter(), name)
    finally:
        (mod.FinancialAccountRepository,
         mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository,
         mod.TransactionCategoryRepository,
         mod.PayeeClassificationRepository) = original


print("\nThe exhibit")

exhibit = build()
check("titled from the matter's alignment",
      "".join(r.text for r in exhibit.caption[-1].runs),
      "Petitioner's Accounts Referenced But Not Produced")
check("one row per account, busiest first",
      [row[1] for row in exhibit.rows], ["4070", "4321"])
check("columns are account-shaped, not transaction-shaped",
      [c.heading for c in exhibit.columns][:4],
      ["Institution", "Last 4", "As printed", "Mentions"])

first = exhibit.rows[0]
check("inferred institution carries the dagger", first[0], "First Financial Bank †")
check("last four", first[1], "4070")
check("mentions", first[3], "2")
check("received from them", first[4], "10000.00")
check("sent to them", first[5], "25000.00")
check("net", first[6], "-15000.00")
check("first seen", first[7], "2023-03-04")
check("last seen", first[8], "2023-09-08")
check("named the account it was referenced on", first[9], "First Financial Bank ····9260")

check("a stated institution carries no dagger", exhibit.rows[1][0], "CHASE")

print("\nThe dagger travels with its footnote")

check("one footnote", len(exhibit.footnotes), 1)
check_true("it explains the mark", exhibit.footnotes[0].startswith("†"))
check_true("and says it is an assumption, not evidence",
           "not evidence" in exhibit.footnotes[0])

# No inference anywhere means no dangling mark.
plain = build(rows=[FakeTransaction(1, 1, "TRANSFER FROM CHASE 4321", "5000.00", date(2023, 5, 1))])
check("no dagger, no footnote", plain.footnotes, ())

print("\nTotals and method")

summary = dict(exhibit.summary)
check("count", summary["Accounts referenced but not produced"], "2")
check("total received, as currency", summary["Total received from them"], "$15,000.00")
check("total sent, as currency", summary["Total sent to them"], "$25,000.00")

selection = dict(exhibit.selection)
check_true("states what it was compared against",
           "1 account" in selection["Compared against"])
check("states the matching rule", selection["Matched on"],
      "The last four digits of the account number")
check_true("states how direction was decided", "sign of each amount" in selection["Direction"])

print("\nRendering")

csv_text = to_csv(exhibit).decode("utf-8-sig")
lines = csv_text.strip().split("\r\n")
check("csv header", lines[0].split(",")[:3], ["Institution", "Last 4", "As printed"])
check("csv keeps money raw for the spreadsheet", "25000.00" in lines[1], True)
check("csv carries no caption", "Cause No" in csv_text, False)
check("csv carries no footnote", "†" in csv_text, True)  # the dagger is in the cell, not a note

md = to_markdown(exhibit).decode("utf-8")
check_true("markdown has the caption", "**Cause No: DF-24-01234**" in md)
check_true("money is formatted in the exhibit", "-$15,000.00" in md)
check_true("the footnote is under the table", "†" in md)
check("footnote sits between the table and the totals",
      md.index("First Financial Bank †") < md.index("*†") < md.index("## Totals"), True)
check_true("verification notice", "offered in court" in md)

pdf = to_pdf(exhibit)
check("pdf magic", pdf[:5], b"%PDF-")

import pymupdf  # noqa: E402

with pymupdf.open(stream=pdf, filetype="pdf") as document:
    text = "\n".join(page.get_text() for page in document)
check_true("account on the page", "4070" in text)
check_true("footnote on the page", "not evidence" in text)
check_true("page numbered", "Page 1 of" in text)

print("\nCreditors are a third block, and candidates are not in the document")

CREDITOR_ROWS = [
    # A finding: filed by a person under a category that names a debt.
    FakeTransaction(10, 1, "ACH PMT AMEX EPAYMENT 0005000008 ID #-M3630",
                    "-5000.00", date(2023, 4, 1), category_id=69),
    # Not a finding: nobody has said what this payee is. It belongs in the
    # triage queue on screen and NOWHERE in a document filed with a court —
    # listing it would assert of a water utility exactly what the row above
    # asserts of a card issuer.
    FakeTransaction(11, 1, "10/16 Online Payment 22398267106 To City of Lewisville",
                    "-120.00", date(2023, 4, 2)),
]
with_creditors = build(rows=CREDITOR_ROWS, liability_ids=[69])
flat = "\n".join(" | ".join(row) for row in with_creditors.rows)

check_true("the creditor block is headed", "Creditors paid, with no account produced" in flat)
check_true("the creditor is named", "AMEX" in flat)
check_true("with what it costs to service", "5000.00" in flat)
check("an unruled payee never reaches the exhibit",
      "LEWISVILLE" in flat.upper(), False)
check("and is not counted as one either",
      dict(with_creditors.summary)["Creditors paid, with no account produced"], "1")
check_true("the footnote explains what a creditor row is and is not",
           any("identify a creditor rather than an account number" in note
               for note in with_creditors.footnotes))

md = to_markdown(with_creditors).decode("utf-8")
check_true("and it survives into the document", "Creditors paid" in md)

print("\nAn empty list still produces a document")

empty = build(rows=[])
check("no rows", empty.rows, ())
check("count says zero", dict(empty.summary)["Accounts referenced but not produced"], "0")
check_true("and it still renders", to_markdown(empty).startswith(b"**Cause No"))

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all undisclosed-exhibit checks passed")
