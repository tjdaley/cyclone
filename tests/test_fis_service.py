"""
tests/test_fis_service.py - The averaging, and the things it must not hide.

The case this suite exists for is Tom's: $3,600 of property tax paid once in
January reads as $1,800/month over a Jan-Feb window and $1,200 over Jan-Mar.
Same facts, a different sworn figure every time the report is re-run. With a
recurrence on record the answer is $300 in both, and stays $300.

The rest of the suite guards the three ways an FIS can look complete and
understate: a window with missing statements, transactions nobody categorized,
and money that moved between the parties' own accounts.

Run:  venv/Scripts/python.exe tests/test_fis_service.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.financial import AccountType, FisRecurrence  # noqa: E402
from db.repositories.financial import TransactionCategoryRepository  # noqa: E402
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


class FakeSetting:
    def __init__(self, category_id, recurrence=None, stated_annual_amount=None, note=None):
        self.category_id = category_id
        self.recurrence = recurrence
        self.stated_annual_amount = stated_annual_amount
        self.note = note


class FakeTransaction:
    def __init__(self, id, account_id, when, amount, category_id=None):
        self.id = id
        self.financial_account_id = account_id
        self.transaction_date = when
        self.amount = Decimal(amount) if amount is not None else None
        self.category_id = category_id


class FakeStatement:
    def __init__(self, account_id, start, end):
        self.financial_account_id = account_id
        self.period_start = start
        self.period_end = end


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
    """
    Ordering is delegated to the real repository.

    A fake that reimplemented reading order would agree with itself and not with
    what ships — which is how the export shipped calling `.name` on a model that
    has no such field. Only the row source is faked.
    """

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


# A minimal chart: one heading with two leaves, plus an excluded category.
HOUSING = FakeCategory(1, "Housing", None, 100)
MORTGAGE = FakeCategory(2, "Mortgage Payment", 1, 105)
TAXES = FakeCategory(3, "Property Taxes", 1, 110)
TRANSFERS = FakeCategory(9, "Interaccount Transfers", None, 9000, include_in_fis=False)
CHART = [HOUSING, MORTGAGE, TAXES, TRANSFERS]


def build(rows, categories=None, settings=None, statements=None,
          start=(2026, 1), end=(2026, 8), accounts=None):
    """Run the service against fakes."""
    import services.fis_service as mod

    # `accounts or [...]` would turn an explicitly empty list back into the
    # default — the same None-vs-empty trap the tag filter documents.
    accounts = [FakeAccount(1)] if accounts is None else accounts
    if statements is None:
        # Full coverage of the window, so coverage warnings do not fire unless
        # a test is about coverage.
        statements = [FakeStatement(a.id, date(2024, 1, 1), date(2026, 12, 31))
                      for a in accounts]

    original = (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
                mod.FinancialAccountTransactionRepository, mod.TransactionCategoryRepository,
                mod.FisCategorySettingRepository)
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo(accounts)
    mod.FinancialAccountStatementRepository = lambda m: FakeStatementRepo(statements)
    mod.FinancialAccountTransactionRepository = lambda m: FakeTransactionRepo(rows)
    mod.TransactionCategoryRepository = lambda m: FakeCategoryRepo(categories or CHART)
    mod.FisCategorySettingRepository = lambda m: FakeSettingsRepo(settings or [])
    try:
        return FisService().build(
            object(), 1, start[0], start[1], end[0], end[1],
        )
    finally:
        (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
         mod.FinancialAccountTransactionRepository, mod.TransactionCategoryRepository,
         mod.FisCategorySettingRepository) = original


def line(result, label):
    return next(row for row in result["lines"] if row["label"] == label)


# -- The property-tax case ---------------------------------------------------

print("\nThe case this exists for: an annual bill in a short window")

TAX_ROWS = [FakeTransaction(1, 1, date(2026, 1, 15), "-3600.00", TAXES.id)]
ANNUAL = [FakeSetting(TAXES.id, FisRecurrence.annual)]

print("  without a recurrence, the figure moves with the window")
for start, end, expected in (((2026, 1), (2026, 2), "-1800.00"),
                             ((2026, 1), (2026, 3), "-1200.00"),
                             ((2026, 1), (2026, 12), "-300.00")):
    got = line(build(TAX_ROWS, start=start, end=end), "Property Taxes")
    check("    %s..%s" % (start, end), got["monthly"], expected)

print("  with it recorded as annual, the figure holds still")
for start, end in (((2026, 1), (2026, 2)), ((2026, 1), (2026, 3)), ((2026, 1), (2026, 12))):
    got = line(build(TAX_ROWS, settings=ANNUAL, start=start, end=end), "Property Taxes")
    check("    %s..%s" % (start, end), got["monthly"], "-300.00")

got = line(build(TAX_ROWS, settings=ANNUAL, start=(2026, 1), end=(2026, 2)), "Property Taxes")
check("computed from the trailing year, and says so", got["basis"], "trailing_year")
check("and carries the legend the witness needs", got["legend"], "paid annually")

print("\nA payment outside the window is still found")

# Tax paid in November 2025; the window is Jan-Feb 2026. Window-only arithmetic
# shows a blank line, which is as wrong as an inflated one.
outside = [FakeTransaction(1, 1, date(2025, 11, 14), "-3600.00", TAXES.id)]
check("without a recurrence it vanishes",
      line(build(outside, start=(2026, 1), end=(2026, 2)), "Property Taxes")["monthly"],
      "0.00")
check("with one, the trailing year reaches it",
      line(build(outside, settings=ANNUAL, start=(2026, 1), end=(2026, 2)),
           "Property Taxes")["monthly"],
      "-300.00")

print("\nTwo tax parcels sum rather than average")

# Averaging per occurrence would halve these; summing the trailing year is right.
parcels = [
    FakeTransaction(1, 1, date(2026, 1, 15), "-1800.00", TAXES.id),
    FakeTransaction(2, 1, date(2026, 1, 15), "-1800.00", TAXES.id),
]
check("both parcels counted",
      line(build(parcels, settings=ANNUAL, start=(2026, 1), end=(2026, 2)),
           "Property Taxes")["monthly"],
      "-300.00")

print("\nQuarterly reaches the same monthly figure")

quarterly = [FakeTransaction(i, 1, when, "-900.00", TAXES.id) for i, when in enumerate(
    [date(2025, 11, 1), date(2026, 2, 1), date(2026, 5, 1), date(2026, 8, 1)], start=1)]
check("four payments of 900 over the trailing year",
      line(build(quarterly, settings=[FakeSetting(TAXES.id, FisRecurrence.quarterly)],
                 start=(2026, 1), end=(2026, 8)), "Property Taxes")["monthly"],
      "-300.00")


# -- Monthly and irregular ---------------------------------------------------

print("\nMonthly lines still divide by the window")

mortgage = [FakeTransaction(i, 1, date(2026, m, 1), "-2504.87", MORTGAGE.id)
            for i, m in enumerate(range(1, 9), start=1)]
got = line(build(mortgage), "Mortgage Payment")
check("eight payments over eight months", got["monthly"], "-2504.87")
check("basis is the window", got["basis"], "window")
check("no legend on a monthly line -- it is the assumption", got["legend"], None)

got = line(build(mortgage, settings=[FakeSetting(MORTGAGE.id, FisRecurrence.irregular)]),
           "Mortgage Payment")
check("irregular computes like monthly", got["monthly"], "-2504.87")
check("but says 'as incurred'", got["legend"], "as incurred")


# -- The attorney's own figure -----------------------------------------------

print("\nA stated annual amount wins over anything derived")

stated = [FakeSetting(TAXES.id, FisRecurrence.annual, stated_annual_amount=Decimal("-4800.00"))]
got = line(build(TAX_ROWS, settings=stated), "Property Taxes")
check("uses the stated figure", got["monthly"], "-400.00")
check("and says where it came from", got["basis"], "stated")

# Signed, like every other amount here: an expense states a negative.
income_stated = [FakeSetting(MORTGAGE.id, FisRecurrence.annual,
                             stated_annual_amount=Decimal("120000.00"))]
check("a positive stated figure reads as income",
      line(build([], settings=income_stated), "Mortgage Payment")["monthly"], "10000.00")


# -- The three silent failures -----------------------------------------------

print("\nUncategorized money is reported, not swallowed")

mixed = mortgage + [FakeTransaction(99, 1, date(2026, 3, 4), "-750.00", None)]
result = build(mixed)
check("counted", result["uncategorized"]["count"], 1)
check("totalled", result["uncategorized"]["total"], "-750.00")
check("and averaged over the window", result["uncategorized"]["monthly"], "-93.75")
check_true("with a warning naming it",
           any("not been filed under a category" in w for w in result["warnings"]))
check("it is in no category line",
      line(result, "Mortgage Payment")["monthly"], "-2504.87")

print("\nInteraccount transfers are set aside, and visible")

with_transfers = mortgage + [
    FakeTransaction(50, 1, date(2026, 2, 1), "-5000.00", TRANSFERS.id),
    FakeTransaction(51, 1, date(2026, 2, 2), "5000.00", TRANSFERS.id),
]
result = build(with_transfers)
check("no line on the statement",
      [row["label"] for row in result["lines"] if row["label"] == "Interaccount Transfers"], [])
check("but reported separately", len(result["excluded"]), 1)
check("with its count", result["excluded"][0]["transaction_count"], 2)
check("net is unmoved by them", result["net_monthly"], "-2504.87")

print("\nMissing statements are declared")

# Only the first three months of an eight-month window are covered.
short = [FakeStatement(1, date(2026, 1, 1), date(2026, 3, 31))]
result = build(mortgage, statements=short)
check("coverage is not complete", result["coverage"]["complete"], False)
check("five months missing", len(result["coverage"]["accounts"][0]["missing_months"]), 5)
check("named by month", result["coverage"]["accounts"][0]["missing_months"][0], "2026-04")
check_true("and warned about",
           any("Statements are missing" in w for w in result["warnings"]))
check("the average still divides by the window, as stated",
      line(result, "Mortgage Payment")["monthly"], "-2504.87")

print("\nAn undated line is in no average, and says so")

result = build(mortgage + [FakeTransaction(77, 1, None, "-500.00", MORTGAGE.id)])
check_true("warned", any("carries no date" in w for w in result["warnings"]))
check("and excluded from the figure", line(result, "Mortgage Payment")["monthly"], "-2504.87")


# -- Shape and compression ---------------------------------------------------

print("\nThe chart's own order and depth")

result = build(mortgage)
check("in display order", [row["label"] for row in result["lines"]],
      ["Housing", "Mortgage Payment", "Property Taxes"])
check("headings at depth 0", line(result, "Housing")["depth"], 0)
check("leaves at depth 1", line(result, "Mortgage Payment")["depth"], 1)

print("\nCompression drops empty rows, and keeps what holds money")

check("a leaf with no money is empty", line(result, "Property Taxes")["empty"], True)
check("a leaf with money is not", line(result, "Mortgage Payment")["empty"], False)
check("a heading survives on its child's account", line(result, "Housing")["empty"], False)

nothing = build([])
check("with nothing anywhere, every row is empty",
      all(row["empty"] for row in nothing["lines"]), True)

# A heading can hold transactions directly; dropping it would drop the money.
direct = build([FakeTransaction(1, 1, date(2026, 2, 1), "-100.00", HOUSING.id)])
check("a heading with its own money is kept", line(direct, "Housing")["empty"], False)
check("its childless children are still dropped", line(direct, "Property Taxes")["empty"], True)


# -- Net -----------------------------------------------------------------

print("")
print("A credit card charge is money spent, not money earned")

# The stored sign is by effect on the balance the institution prints, which on a
# card makes a purchase POSITIVE. The FIS asks what the household spent, and
# those two answers are opposites for a liability account. Left alone, the same
# groceries bought on debit and on credit cancel to nothing.
CARD = FakeAccount(2, "Chase Sapphire", "4321", AccountType.credit_card)
BOTH = [FakeAccount(1), CARD]

same_groceries = [
    FakeTransaction(1, 1, date(2026, 1, 5), "-500.00", MORTGAGE.id),
    FakeTransaction(2, 2, date(2026, 2, 5), "500.00", MORTGAGE.id),
]
result = build(same_groceries, accounts=BOTH, start=(2026, 1), end=(2026, 2))
check("both read as expense, not as a wash",
      line(result, "Mortgage Payment")["window_total"], "-1000.00")
check("averaged over the window", line(result, "Mortgage Payment")["monthly"], "-500.00")
check("and the net is an outflow", result["net_monthly"], "-500.00")

print("A refund on the card reduces the expense")
check("a credit on the card reads as money coming back",
      line(build([FakeTransaction(3, 2, date(2026, 1, 9), "-75.00", MORTGAGE.id)],
                 accounts=BOTH, start=(2026, 1), end=(2026, 1)),
           "Mortgage Payment")["window_total"], "75.00")

print("A loan behaves the same way")
LOAN = FakeAccount(3, "Ally", "7788", AccountType.loan)
check("interest accruing is an expense",
      line(build([FakeTransaction(4, 3, date(2026, 1, 9), "50.00", MORTGAGE.id)],
                 accounts=[FakeAccount(1), LOAN], start=(2026, 1), end=(2026, 1)),
           "Mortgage Payment")["window_total"], "-50.00")

print("An asset account is untouched")
check("checking still signs by the printed balance",
      line(build([FakeTransaction(5, 1, date(2026, 1, 9), "-120.00", MORTGAGE.id)],
                 start=(2026, 1), end=(2026, 1)),
           "Mortgage Payment")["window_total"], "-120.00")


print("\nNet cash flow")

income = FakeCategory(20, "Salary & Wages (W-2)", None, 10)
chart = [income, HOUSING, MORTGAGE, TAXES]
rows = mortgage + [FakeTransaction(200 + i, 1, date(2026, m, 15), "13595.25", income.id)
                   for i, m in enumerate(range(1, 9))]
result = build(rows, categories=chart)
check("income line", line(result, "Salary & Wages (W-2)")["monthly"], "13595.25")
check("net is the sum of the printed lines",
      result["net_monthly"], "11090.38")
check("which is what the column adds to",
      str(sum(Decimal(row["monthly"]) for row in result["lines"])), "11090.38")


print("\nEdge cases")

check("a matter with no accounts", build([], accounts=[])["lines"], [])
check("and it says why", len(build([], accounts=[])["warnings"]), 1)

try:
    build([], start=(2026, 8), end=(2026, 1))
    check("a backwards window is refused", "no error", "ValueError")
except ValueError as e:
    check_true("a backwards window is refused", "cannot fall before" in str(e))

one_month = build([FakeTransaction(1, 1, date(2026, 3, 5), "-600.00", MORTGAGE.id)],
                  start=(2026, 3), end=(2026, 3))
check("a single-month window divides by one",
      line(one_month, "Mortgage Payment")["monthly"], "-600.00")
check("and reports one month", one_month["window"]["months"], 1)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all FIS checks passed")
