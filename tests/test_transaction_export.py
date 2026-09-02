"""
tests/test_transaction_export.py - Search results becoming an exhibit.

The rule this suite exists to hold: **an export is not a page.** The screen
shows 200 rows because that is what a person reads. An exhibit that stopped
there would be a summary of the wrong set, and — this is the part that matters —
it would look complete. So the export pages through every match, and when it
cannot, it says so in the document itself rather than in a log nobody reads.

Run:  venv/Scripts/python.exe tests/test_transaction_export.py
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.matter import ClientAlignment  # noqa: E402
from services.exhibit_service import to_csv, to_markdown  # noqa: E402
from services.transaction_search_service import TransactionSearchService  # noqa: E402

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
    def __init__(self, id, institution, last4):
        self.id = id
        self.institution = institution
        self.account_number_last4 = last4


# These fakes are built from the real models on purpose. The first version of
# this suite invented `.name` on both a category and a tag — the one attribute
# neither model has. Every test passed and every export returned a 500. A fake
# that agrees with the code under test rather than with the schema tests nothing.
class FakeCategory:
    """Mirrors TransactionCategory: the display name is `description`."""

    def __init__(self, id, description):
        self.id = id
        self.description = description


class FakeCategoryRepo:
    def get_all(self, include_inactive=False):
        return [FakeCategory(7, "Housing"), FakeCategory(9, "Transfers")]


class FakeAccountRepo:
    def __init__(self, accounts):
        self._accounts = accounts

    def get_by_matter(self, matter_id):
        return self._accounts


class FakeTag:
    """Mirrors TransactionTag: the display name is `label`."""

    def __init__(self, id, label):
        self.id = id
        self.label = label


class FakeTagRepo:
    def available_for_matter(self, matter_id):
        return [FakeTag(3, "Waste")]


class StubService(TransactionSearchService):
    """Replaces the database read with a fixed result set, paged the same way."""

    def __init__(self, rows, page_size):
        self.rows = rows
        self.page_size = page_size
        self.pages = 0

    def search(self, manager, matter_id, limit=200, offset=0, **kwargs):
        self.pages += 1
        window = self.rows[offset:offset + min(limit, self.page_size)]
        return {"total": len(self.rows), "items": window, "sum_amount": "0.00"}


def row(n, amount, description="Transfer to XXX4070", date="2023-03-04", category_id=None):
    return {
        "id": n, "transaction_date": date, "bates_number": "KF%06d" % (100 + n),
        "description": description, "check_number": None, "amount": amount,
        "category_id": category_id, "institution": "First Financial Bank",
        "account_last4": "9260",
    }


def build(rows, page_size=1000, criteria=None, name="Financial Summary"):
    """Run build_exhibit against fakes."""
    import services.transaction_search_service as mod

    service = StubService(rows, page_size)
    original = (mod.FinancialAccountRepository, mod.TransactionCategoryRepository,
                mod.TransactionTagRepository)
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo(
        [FakeAccount(1, "First Financial Bank", "9260"), FakeAccount(2, "Bank of Texas", "6837")]
    )
    mod.TransactionCategoryRepository = lambda m: FakeCategoryRepo()
    mod.TransactionTagRepository = lambda m: FakeTagRepo()
    try:
        return service, service.build_exhibit(
            object(), FakeMatter(), name, criteria or {"limit": 200, "offset": 0},
        )
    finally:
        (mod.FinancialAccountRepository, mod.TransactionCategoryRepository,
         mod.TransactionTagRepository) = original


# ── The fakes must agree with the real models ────────────────────────────────
#
# This is the check that would have caught the export 500. Asserting against the
# Pydantic models means a fake cannot quietly drift from the schema it stands in
# for: rename a column and this fails here rather than in production.

print("\nFakes agree with the schema")

from db.models.financial import TransactionCategory, TransactionTag  # noqa: E402

check("a category's display name is 'description'",
      "description" in TransactionCategory.model_fields, True)
check("a category has no 'name'", "name" in TransactionCategory.model_fields, False)
check("a tag's display name is 'label'", "label" in TransactionTag.model_fields, True)
check("a tag has no 'name'", "name" in TransactionTag.model_fields, False)
check("the category fake carries the real field",
      hasattr(FakeCategory(1, "x"), "description"), True)
check("the tag fake carries the real field", hasattr(FakeTag(1, "x"), "label"), True)


# ── The whole set, not the page ──────────────────────────────────────────────

print("\nAn export covers every matching line")

rows = [row(n, "-10.00") for n in range(1, 1451)]
service, exhibit = build(rows, page_size=1000)
check("every line reached the exhibit", len(exhibit.rows), 1450)
check("paged rather than taking the first page", service.pages, 2)
check("not truncated", any("Narrow the filter" in w for w in exhibit.warnings), False)

print("\nHitting the cap is reported, not silent")

import services.transaction_search_service as mod  # noqa: E402

original_cap = mod._EXPORT_CAP
mod._EXPORT_CAP = 100
try:
    _, exhibit = build([row(n, "-10.00") for n in range(1, 351)], page_size=50)
finally:
    mod._EXPORT_CAP = original_cap

check("stopped at the cap", len(exhibit.rows), 100)
check_true("warned the caller", any("Narrow the filter" in w for w in exhibit.warnings))
selection = dict(exhibit.selection)
check("and said so inside the document", selection["Lines"], "100 of 350 matching")


# ── Totals ───────────────────────────────────────────────────────────────────

print("\nTotals")

rows = [row(1, "-2500.00"), row(2, "10000.00"), row(3, "-159.11")]
_, exhibit = build(rows)
summary = dict(exhibit.summary)
check("transactions", summary["Transactions"], "3")
check("credits", summary["Total credits"], "$10,000.00")
check("debits are stated positive", summary["Total debits"], "$2,659.11")
check("net", summary["Net"], "$7,340.89")
check("undated lines are not mentioned when there are none",
      "Lines with no date" in summary, False)

print("\nA line with no date still carries its amount")

rows = [row(1, "-2500.00"), row(2, "100.00", date=None)]
_, exhibit = build(rows)
summary = dict(exhibit.summary)
check("both lines counted", summary["Transactions"], "2")
check("the amount is in the total", summary["Net"], "-$2,400.00")
check("and the gap is stated", summary["Lines with no date"], "1")
check("the empty date is blank, not the word None", exhibit.rows[1][0], "")


# ── Selection ────────────────────────────────────────────────────────────────

print("\nSelection describes the query, not a form")

_, exhibit = build([row(1, "-10.00")], criteria={
    "account_ids": [1], "date_from": "2023-01-01", "date_to": "2023-12-31",
    "text": "transfer", "checks_only": False, "include_deleted": False,
    "category_ids": [7], "include_subcategories": True,
    "limit": 200, "offset": 0,
})
selection = dict(exhibit.selection)
check("names the account", selection["Accounts"], "First Financial Bank x9260")
check("states the period", selection["Period"], "2023-01-01 through 2023-12-31")
check("names the category", selection["Categories"], "Housing (including sub-categories)")
check("quotes the text filter", selection["Description contains"], "transfer")
check("a filter that was not applied is absent, not listed as none",
      "Tags" in selection, False)

print("\nA tag filter names its tags")

# Never exercised before, which is why the tag half of the same bug survived
# even after the category half was found.
_, exhibit = build([row(1, "-10.00")], criteria={
    "tag_ids": [3], "tag_match_all": False, "limit": 200, "offset": 0,
})
check("tag resolved to its label, not an id", dict(exhibit.selection)["Tags"], "Waste (any)")

print("\nNo account filter says so plainly")
_, exhibit = build([row(1, "-10.00")])
check("all accounts", dict(exhibit.selection)["Accounts"], "All 2 accounts on this matter")

print("\nDeleted lines are never included silently")
_, exhibit = build([row(1, "-10.00")], criteria={"include_deleted": True, "limit": 200, "offset": 0})
check("stated in the document", dict(exhibit.selection)["Includes"],
      "Lines removed from their statements")


# ── Rendering the real thing ─────────────────────────────────────────────────

print("\nThe rendered exhibit")

rows = [row(1, "-2500.00", category_id=9), row(2, "10000.00")]
_, exhibit = build(rows, name="Undisclosed Transfers")

csv_text = to_csv(exhibit).decode("utf-8-sig")
lines = csv_text.strip().split("\r\n")
check("csv header", lines[0], "Date,Bates,Account,Check No.,Description,Category,Amount")
check("csv row", lines[1],
      "2023-03-04,KF000101,First Financial Bank x9260,,Transfer to XXX4070,Transfers,-2500.00")
check("csv has no caption", "Cause No" in csv_text, False)

md = to_markdown(exhibit).decode("utf-8")
check_true("exhibit is titled from the alignment", "**Petitioner's Undisclosed Transfers**" in md)
check_true("bates column carried through", "KF000101" in md)
check_true("category name resolved, not an id", "| Transfers |" in md)
check_true("an unfiled line shows blank, not None", "|  |" in md)
check_true("notice present", "offered in court" in md)

# The same exhibit, two audiences: the spreadsheet gets a number it can add up,
# the court document gets a figure a person reads.
check_true("the exhibit formats the amount as currency", "-$2,500.00" in md)
check_true("the CSV leaves it raw", ",-2500.00" in csv_text)
check("Totals precedes Selection in the exhibit",
      md.index("## Totals") < md.index("## Selection"), True)

print("\nFilename follows the exhibit name")
check("stem", exhibit.filename_stem, "Undisclosed_Transfers")

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all transaction-export checks passed")
