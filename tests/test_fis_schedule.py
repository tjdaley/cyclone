"""
tests/test_fis_schedule.py - The detail behind the statement.

Two jobs, and the second is the harder one. Online it is a review pass that
finds the line filed under the wrong heading. In court it is the answer to
"what exactly is in Miscellaneous?" -- the question that, unanswered, costs a
witness their credibility.

Which means the schedule must **tie to the statement**, and the trap it exists
to avoid is a quiet one: the FIS says $300/month for property taxes because the
line is annual, while the report window holds a single payment of $3,600 -- or
none at all, if it was paid the November before. A schedule showing only the
window would appear, on its face, to contradict the summary it backs.

Run:  venv/Scripts/python.exe tests/test_fis_schedule.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.financial import AccountType, FisRecurrence  # noqa: E402
from db.models.matter import ClientAlignment  # noqa: E402
from db.repositories.financial import TransactionCategoryRepository  # noqa: E402
from services.exhibit_service import to_csv, to_markdown  # noqa: E402
from services.fis_service import FisService  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# -- Fakes -------------------------------------------------------------------

class FakeMatter:
    id = 1
    matter_number = "416-56988-2024"
    court_name = "416th Judicial District Court"
    county = "Collin"
    state = "Texas"
    matter_name = "Doe divorce"
    case_style = "In the Matter of the Marriage of John Doe and Jane Doe"
    client_alignment = ClientAlignment.petitioner


class FakeAccount:
    def __init__(self, id, institution="First Financial Bank", last4="9260", account_type=AccountType.checking):
        self.id = id
        self.account_type = account_type
        self.institution = institution
        self.account_number_last4 = last4


class FakeCategory:
    def __init__(self, id, description, parent_id=None, display_order=0, include_in_fis=True):
        self.id = id
        self.description = description
        self.parent_id = parent_id
        self.display_order = display_order
        self.include_in_fis = include_in_fis
        self.is_active = True


class FakeSetting:
    def __init__(self, category_id, recurrence=None, stated_annual_amount=None, note=None):
        self.category_id = category_id
        self.recurrence = recurrence
        self.stated_annual_amount = stated_annual_amount
        self.note = note


class FakeTransaction:
    def __init__(self, id, account_id, when, amount, category_id=None,
                 bates=None, page=None, check=None, statement_id=1,
                 description="Mr. Cooper Mortgage Payment"):
        self.id = id
        self.financial_account_id = account_id
        self.transaction_date = when
        self.amount = Decimal(amount)
        self.category_id = category_id
        self.bates_number = bates
        self.physical_page_number = page
        self.check_number = check
        self.statement_id = statement_id
        self.description = description


class FakeStatementRecord:
    def __init__(self, id, account_id, start, end, filename=None):
        self.id = id
        self.financial_account_id = account_id
        self.period_start = start
        self.period_end = end
        self.extraction = {"source_filename": filename} if filename else {}


class FakeAccountRepo:
    def __init__(self, accounts):
        self._accounts = accounts

    def get_by_matter(self, matter_id):
        return self._accounts


class FakeStatementRepo:
    def __init__(self, statements):
        self._statements = statements

    def rejected_ids(self, matter_id):
        return []

    def get_by_matter(self, matter_id):
        return self._statements


class FakeTransactionRepo:
    def __init__(self, rows):
        self._rows = rows

    def search(self, account_ids, exclude_statement_ids=None,
               date_from=None, date_to=None, limit=200, offset=0, **kwargs):
        matches = [
            r for r in self._rows
            if r.financial_account_id in account_ids
            and (r.transaction_date is None
                 or ((date_from is None or r.transaction_date >= date_from)
                     and (date_to is None or r.transaction_date <= date_to)))
        ]
        return matches[offset:offset + limit], len(matches)


class FakeCategoryRepo:
    """Ordering delegates to the real repository, so the fake cannot drift."""

    def __init__(self, categories):
        self._categories = categories

    def get_all(self, include_inactive=False):
        return self._categories

    def get_ordered(self, include_inactive=False):
        real = TransactionCategoryRepository.__new__(TransactionCategoryRepository)
        real.get_all = self.get_all
        return real.get_ordered(include_inactive=include_inactive)


class FakeSettingsRepo:
    def __init__(self, settings):
        self._settings = settings

    def resolve(self, client_id=None, opposing_party_id=None):
        return {s.category_id: s for s in self._settings}


HOUSING = FakeCategory(1, "Housing", None, 100)
MORTGAGE = FakeCategory(2, "Mortgage Payment", 1, 105)
TAXES = FakeCategory(3, "Property Taxes", 1, 110)
MISC = FakeCategory(4, "Miscellaneous", 1, 120)
CHART = [HOUSING, MORTGAGE, TAXES, MISC]

ACCOUNTS = [FakeAccount(1)]
STATEMENTS = [FakeStatementRecord(1, 1, date(2024, 1, 1), date(2026, 12, 31),
                                  "Chase x9260 2026.03.pdf")]


def run(rows, settings=None, categories=None, category_ids=None,
        start=(2026, 1), end=(2026, 8), exhibit=False):
    import services.fis_service as mod

    original = (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository, mod.TransactionCategoryRepository,
                mod.FisCategorySettingRepository)
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo(ACCOUNTS)
    mod.FinancialAccountStatementRepository = lambda m: FakeStatementRepo(STATEMENTS)
    mod.FinancialAccountTransactionRepository = lambda m: FakeTransactionRepo(rows)
    mod.TransactionCategoryRepository = lambda m: FakeCategoryRepo(categories or CHART)
    mod.FisCategorySettingRepository = lambda m: FakeSettingsRepo(settings or [])
    service = FisService()
    try:
        args = dict(start_year=start[0], start_month=start[1],
                    end_year=end[0], end_month=end[1], category_ids=category_ids)
        if exhibit:
            return service.build_schedule_exhibit(object(), FakeMatter(), **args)
        return service.build_schedule(object(), FakeMatter(), **args)
    finally:
        (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository, mod.TransactionCategoryRepository,
         mod.FisCategorySettingRepository) = original


def group(schedule, label):
    return next(g for g in schedule["groups"] if g["label"] == label)


MORTGAGE_ROWS = [
    FakeTransaction(i, 1, date(2026, m, 1), "-2504.87", MORTGAGE.id,
                    bates="KH%06d" % (800 + m), page=m)
    for i, m in enumerate(range(1, 9), start=1)
]


# -- It ties to the statement -------------------------------------------------

print("\nThe schedule agrees with the statement it backs")

schedule = run(MORTGAGE_ROWS)
mortgage = group(schedule, "Mortgage Payment")
check("the monthly figure is the statement's", mortgage["monthly"], "-2504.87")
check("eight transactions", len(mortgage["transactions"]), 8)
check("totalled", mortgage["total"], "-20038.96")
check_true("and the arithmetic is spelled out",
           "8 transaction(s) totalling -$20,038.96 over 8 month(s) is -$2,504.87 per month"
           in mortgage["derivation"])

print("\nAn annual line shows the transactions its figure came from")

# The trap: the FIS says $300/month, the window holds one $3,600 payment.
TAX_IN = [FakeTransaction(50, 1, date(2026, 1, 15), "-3600.00", TAXES.id, bates="KH000801")]
ANNUAL = [FakeSetting(TAXES.id, FisRecurrence.annual)]

schedule = run(MORTGAGE_ROWS + TAX_IN, settings=ANNUAL, start=(2026, 1), end=(2026, 2))
taxes = group(schedule, "Property Taxes")
check("the figure is the statement's", taxes["monthly"], "-300.00")
check("the span is the trailing year, not the window", taxes["span"], "trailing_year")
check("which reaches back before the window", taxes["span_start"], "2025-03-01")
check("the payment is shown", len(taxes["transactions"]), 1)
check_true("and the derivation explains why it is not simply divided by two",
           "over the twelve months to 2026-02-28" in taxes["derivation"])
check_true("naming the schedule", "paid annually" in taxes["derivation"])

print("\nEven when the payment falls outside the window entirely")

# Paid last November; a window-only schedule would show an empty group under a
# non-zero figure, which reads as a contradiction.
TAX_BEFORE = [FakeTransaction(51, 1, date(2025, 11, 14), "-3600.00", TAXES.id)]
schedule = run(MORTGAGE_ROWS + TAX_BEFORE, settings=ANNUAL, start=(2026, 1), end=(2026, 2))
taxes = group(schedule, "Property Taxes")
check("the figure still stands", taxes["monthly"], "-300.00")
check("and the transaction behind it is produced", len(taxes["transactions"]), 1)
check("dated before the window", taxes["transactions"][0]["date"], "2025-11-14")

print("\nA stated figure says it was not derived")

STATED = [FakeSetting(TAXES.id, FisRecurrence.annual,
                      stated_annual_amount=Decimal("-4800.00"))]
schedule = run(MORTGAGE_ROWS + TAX_IN, settings=STATED)
taxes = group(schedule, "Property Taxes")
check("the stated figure wins", taxes["monthly"], "-400.00")
check_true("and the schedule says the transactions were not used",
           "were not used to compute it" in taxes["derivation"])


# -- Provenance ---------------------------------------------------------------

print("\nEvery line carries enough to find it in the production")

line = group(run(MORTGAGE_ROWS), "Mortgage Payment")["transactions"][0]
check("date", line["date"], "2026-01-01")
check("account", line["account"], "First Financial Bank x9260")
check("bates", line["bates_number"], "KH000801")
check("page", line["page"], 1)
check("document name, which storage otherwise renames to a job id",
      line["document"], "Chase x9260 2026.03.pdf")
check("amount", line["amount"], "-2504.87")


# -- Unfiled money ------------------------------------------------------------

print("\nUnfiled money is a group of its own, last")

mixed = MORTGAGE_ROWS + [FakeTransaction(90, 1, date(2026, 3, 4), "-750.00", None)]
schedule = run(mixed)
check("it is there", schedule["groups"][-1]["label"], "Not yet filed under a category")
check("with its transaction", len(schedule["groups"][-1]["transactions"]), 1)
check_true("and says it is in no line of the statement",
           "in no line of the statement" in schedule["groups"][-1]["derivation"])

print("\nEmpty categories are left out")
check("only the group that holds transactions appears",
      [g["label"] for g in run(MORTGAGE_ROWS)["groups"]],
      ["Mortgage Payment"])

print("\nOne category can be singled out when only it is in dispute")
schedule = run(MORTGAGE_ROWS + TAX_IN, settings=ANNUAL, category_ids=[TAXES.id])
check("just the one group", [g["label"] for g in schedule["groups"]], ["Property Taxes"])


# -- The exhibit --------------------------------------------------------------

print("\nThe exhibit")

exhibit = run(MORTGAGE_ROWS + TAX_IN, settings=ANNUAL, exhibit=True)
md = to_markdown(exhibit).decode("utf-8")
check_true("titled from the alignment",
           "**Petitioner's Schedule of Transactions by Category**" in md)
check_true("a heading per group", "Mortgage Payment**" in md)
check_true("provenance columns",
           "| Category / Date | Account | Description | Bates | Amount |" in md)
check_true("bates on the row", "KH000801" in md)
check_true("the derivation is on the ruled total row", "per month." in md)
check_true("and the promise the whole document rests on",
           "the same figure that appears on the Financial Information Statement" in md)

csv_text = to_csv(exhibit).decode("utf-8-sig")
check("csv keeps its header",
      csv_text.splitlines()[0],
      "Category / Date,Account,Description,Bates,Amount")

# One column does two jobs: the category on a heading row, the date on the lines
# beneath it. The category is no longer repeated on every data row — it cost
# width the description needed, and the screen reads the same way.
check("a heading row carries the category, flush left",
      csv_text.splitlines()[1].startswith("Mortgage Payment,"), True)
data_row = next(r for r in csv_text.splitlines() if r.startswith("2026-01-01,"))
check("a data row starts with the date", data_row.startswith("2026-01-01,"), True)
check("the amount column stays raw for the spreadsheet",
      data_row.rstrip().endswith(",-2504.87"), True)

# The check number was a mostly-empty column costing width the description
# needed. It is folded in only when the bank did not already print it.
check("a check number the bank already printed is not repeated",
      FisService()._describe({"description": "CHECK 2495 TO ACME", "check_number": "2495"}),
      "CHECK 2495 TO ACME")
check("one the bank omitted is appended",
      FisService()._describe({"description": "Paid to order", "check_number": "2495"}),
      "Paid to order (check 2495)")
check("a bare check number still says what it is",
      FisService()._describe({"description": "", "check_number": "2495"}), "Check 2495")
check("no check number, no change",
      FisService()._describe({"description": "Groceries", "check_number": None}), "Groceries")

print("")
print("Headings sit flush left whatever their depth in the chart")
heading_row = next(r for r in exhibit.rows if r.heading)
check("no indent", heading_row.depth, 0)
check("the lines beneath are indented once",
      next(r for r in exhibit.rows if not r.heading and not r.rule).depth, 1)
check("as is the total that closes the group",
      next(r for r in exhibit.rows if r.rule).depth, 0)

print("")
print("The schedule is a wide document")
check("landscape", exhibit.landscape, True)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all FIS schedule checks passed")
