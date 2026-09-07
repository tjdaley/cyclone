"""
tests/test_compliance_matrix.py - What a production holds, and what it does not.

Every period below is real: Liberty x8157, a rural bank whose reprints carry no
letterhead, twelve months of 2020. Its cycles run 4 March to 5 April — which is
the whole reason the coverage summary exists separately from the grid. A month
can hold a statement and still be missing days at either end.

The other half is the boundary marker. Without it every account reports holes
back to the start of the year range, and somebody either chases statements that
never existed or spends an afternoon proving they do not.

Run:  venv/Scripts/python.exe tests/test_compliance_matrix.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.financial import (  # noqa: E402
    AccountType, ProductionResponsibility, StatementBoundary, StatementReviewStatus,
)
import services.compliance_service as mod  # noqa: E402
from services.compliance_service import compliance_service  # noqa: E402

FAILURES: list[str] = []


def check_true(label, got):
    check(label, bool(got), True)


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


class FakeAccount:
    def __init__(self, id, institution, last4, account_type=AccountType.checking, closed=False,
                 responsibility=ProductionResponsibility.unknown):
        self.id = id
        self.institution = institution
        self.account_number_last4 = last4
        self.account_type = account_type
        self.is_closed = closed
        self.production_responsibility = responsibility


class FakeStatement:
    def __init__(self, id, account_id, start, end, bates=None,
                 boundary=StatementBoundary.intermediate,
                 review_status=StatementReviewStatus.auto_accepted):
        self.id = id
        self.financial_account_id = account_id
        self.period_start = start
        self.period_end = end
        self.boundary = boundary
        self.review_status = review_status
        self.reconciled = True
        self.storage_path = "intake/%d.pdf" % id
        self.extraction = {"bates_first": bates, "bates_last": bates,
                           "source_filename": "Liberty x8157.pdf"}


class FakeAccountRepo:
    def __init__(self, accounts): self._a = accounts
    def get_by_matter(self, matter_id): return self._a


class FakeStatementRepo:
    def __init__(self, statements): self._s = statements
    def get_by_matter(self, matter_id): return self._s


class FakeMatter:
    def __init__(self, client_since=None, opposing_since=None):
        self.id = 1
        self.client_produces_since = client_since
        self.opposing_produces_since = opposing_since


class FakeMatterRepo:
    def __init__(self, matter): self._m = matter
    def select_one(self, condition=None): return self._m


def run(accounts, statements, side=None, matter=None, today=None):
    original = (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
                mod.MatterRepository)
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo(accounts)
    mod.FinancialAccountStatementRepository = lambda m: FakeStatementRepo(statements)
    mod.MatterRepository = lambda m: FakeMatterRepo(matter or FakeMatter())
    try:
        return compliance_service.matrix(object(), 1, side=side, today=today)
    finally:
        (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
         mod.MatterRepository) = original


def guard_fakes() -> None:
    """A fake may not invent a field the real model does not have."""
    from db.models.financial import FinancialAccount, FinancialAccountStatement
    pairs = (
        ("FakeAccount", FakeAccount(1, "B", "1", closed=False), FinancialAccount, {"id"}),
        ("FakeStatement", FakeStatement(1, 1, date(2020, 1, 1), date(2020, 1, 31)),
         FinancialAccountStatement, {"id"}),
    )
    for label, fake, model, exempt in pairs:
        for attribute in vars(fake):
            if attribute in exempt or attribute in model.model_fields:
                continue
            check("%s.%s is a real field" % (label, attribute), attribute, "on the model")


# Liberty's real 2020 cycles, in order.
LIBERTY = [
    (date(2019, 12, 4), date(2020, 1, 5)),
    (date(2020, 1, 6), date(2020, 2, 3)),
    (date(2020, 2, 4), date(2020, 3, 3)),
    (date(2020, 3, 4), date(2020, 4, 5)),
    (date(2020, 4, 6), date(2020, 5, 3)),
    (date(2020, 5, 4), date(2020, 6, 3)),
]

guard_fakes()

print("\nThe grid")

account = FakeAccount(60, "Liberty", "8157")
statements = [FakeStatement(200 + n, 60, s, e, bates="LIB%04d" % (100 + n * 8))
              for n, (s, e) in enumerate(LIBERTY)]
report = run([account], statements)
row = report["accounts"][0]

# Keyed on the month each statement CLOSES in. Keying on the start would file
# the 4 March – 5 April statement under March and make consecutive statements
# look like they skip a month.
check("a cell per closing month", sorted(row["cells"]),
      ["2020-01", "2020-02", "2020-03", "2020-04", "2020-05", "2020-06"])
check("December 2019 holds nothing, though a statement starts in it",
      "2019-12" in row["cells"], False)
check("the cell carries the Bates stamp", row["cells"]["2020-01"][0]["bates"], "LIB0100")
check("and the statement it links to", row["cells"]["2020-01"][0]["statement_id"], 200)

# The year range spans the matter, so a blank year is visible rather than the
# grid simply ending.
check("years span the data", report["years"], [2020])

print("\nA filled month is not a covered month")

# Consecutive Liberty statements abut — one ends 5 April, the next starts the
# 6th — so a clean run has no gaps at all, despite no period matching a
# calendar month.
check("abutting periods leave no gap", row["gaps"], [])

# Drop the March statement. The hole is 4 February to 3 March in DAYS, which is
# what a motion has to name; the grid alone would only say "no March cell".
missing_march = [s for s in statements if s.period_end != date(2020, 3, 3)]
row = run([account], missing_march)["accounts"][0]
check("one gap", len(row["gaps"]), 1)
check("named in days, not months", row["gaps"][0],
      {"start": "2020-02-04", "end": "2020-03-03", "days": 29})

print("\nWhat the boundary markers suppress")

# Unmarked: everything before the earliest and after the latest is unaccounted
# for, because nothing says the account did not exist then.
row = run([account], statements)["accounts"][0]
check("nothing produced before the earliest", row["nothing_before"], "2019-12-04")
check("nor after the latest", row["nothing_after"], "2020-06-03")

marked = [
    FakeStatement(200, 60, LIBERTY[0][0], LIBERTY[0][1], boundary=StatementBoundary.opening),
    *[FakeStatement(201 + n, 60, s, e) for n, (s, e) in enumerate(LIBERTY[1:-1])],
    FakeStatement(299, 60, LIBERTY[-1][0], LIBERTY[-1][1], boundary=StatementBoundary.closing),
]
row = run([account], marked)["accounts"][0]
check("an opening statement means nothing is missing before it",
      row["nothing_before"], None)
check("a closing statement means nothing is missing after it",
      row["nothing_after"], None)
# THE ACCOUNT'S LIFE IS NOT THE CELL ITS STATEMENT SITS IN. The first Liberty
# statement runs 4 Dec 2019 to 5 Jan 2020, so it is placed in the January cell —
# but the account was already alive through December. Marking the life from the
# cell would grey December out as "before the account existed", which is an
# assertion and a false one. A blank is a question; a wrong question is
# recoverable and a wrong assertion is not.
check("the account's life starts when the opening statement's period does",
      row["opening_month"], "2019-12")
# The closing end has no such problem: the last statement's period ends in the
# month its cell sits in, by construction.
check("and ends where the closing statement's does", row["closing_month"], "2020-06")

# The markers suppress the OUTER holes only. A gap between two statements is a
# gap whatever the edges say — the account demonstrably existed on both sides.
inner = [marked[0], marked[-1]]
row = run([account], inner)["accounts"][0]
check("a hole between two statements survives the markers", len(row["gaps"]), 1)
check("even with both edges marked",
      (row["nothing_before"], row["nothing_after"]), (None, None))

print("\nOrder, and what is left out")

accounts = [
    FakeAccount(3, "Zions", "1111", AccountType.checking),
    FakeAccount(1, "Amex", "2222", AccountType.credit_card),
    FakeAccount(2, "Ally", "3333", AccountType.checking),
    FakeAccount(4, "Vanguard", "4444", AccountType.retirement),
]
report = run(accounts, [FakeStatement(1, 3, date(2020, 1, 1), date(2020, 1, 31))])
check("grouped by kind, then by name",
      [a["label"] for a in report["accounts"]],
      ["Ally ····3333", "Zions ····1111", "Amex ····2222", "Vanguard ····4444"])

# A rejected extraction is not evidence that a month was produced.
rejected = [FakeStatement(1, 60, LIBERTY[0][0], LIBERTY[0][1],
                          review_status=StatementReviewStatus.rejected)]
report = run([account], rejected)
check("a rejected statement fills no cell", report["accounts"][0]["cells"], {})
check("and is not counted", report["totals"]["statements"], 0)

print("\nEdges")

check("a matter with no accounts", run([], [])["accounts"], [])
empty = run([account], [])["accounts"][0]
check("an account with no statements has no gaps", empty["gaps"], [])
check("and claims nothing about its edges",
      (empty["nothing_before"], empty["nothing_after"]), (None, None))

# Two statements closing in one month — a re-import, or an odd cycle. Both are
# shown rather than one silently winning.
twice = [
    FakeStatement(1, 60, date(2020, 1, 1), date(2020, 1, 20), bates="A1"),
    FakeStatement(2, 60, date(2020, 1, 21), date(2020, 1, 31), bates="A2"),
]
row = run([account], twice)["accounts"][0]
check("both statements appear in the month", len(row["cells"]["2020-01"]), 2)

# ── Two reports, two obligations ─────────────────────────────────────────────
#
# There is no single "compliance report". Opposing counsel propounds on us and
# sets how far back WE must go; we propound on them and set how far back THEY
# must go. Same grid, different accounts, different look-back.

print("\nWho has to produce what")

OURS = FakeAccount(1, "Liberty", "8157", responsibility=ProductionResponsibility.client)
THEIRS = FakeAccount(2, "Chase", "9547", responsibility=ProductionResponsibility.opposing)
JOINT = FakeAccount(3, "Wells", "1234", responsibility=ProductionResponsibility.both)
UNMARKED = FakeAccount(4, "Ally", "5555")
ALL = [OURS, THEIRS, JOINT, UNMARKED]
SOME = [FakeStatement(n, a.id, date(2023, 1, 1), date(2023, 1, 31))
        for n, a in enumerate(ALL, start=1)]

in_ours = sorted(a["label"] for a in run(ALL, SOME, side="client")["accounts"])
report = run(ALL, SOME, side="opposing")
in_theirs = sorted(a["label"] for a in report["accounts"])

check("our report holds the accounts we must produce",
      in_ours, ["Liberty ····8157", "Wells ····1234"])
check("theirs holds the ones they must",
      in_theirs, ["Chase ····9547", "Wells ····1234"])
# An account both sides were ordered to produce appears on both. That is not a
# duplicate: it is two obligations over the same documents, and each is bounded
# by its own request.
check("a 'both' account is on each report",
      ("Wells ····1234" in in_ours, "Wells ····1234" in in_theirs), (True, True))
check("and an account of one side never reaches the other",
      "Chase ····9547" in in_ours, False)

# Nobody has said who produces the Ally account. It is counted, not shown —
# dropped silently it would escape both reports and never be chased.
check("unassigned accounts are counted", report["totals"]["unassigned"], 1)
check("and kept off the scoped report",
      any(a["label"].startswith("Ally") for a in report["accounts"]), False)
check("but the unscoped view still shows everything",
      len(run(ALL, SOME)["accounts"]), 4)

print("\nEach side gets its own look-back")

# The real asymmetry: OC asked us for everything since 2002; we asked them for
# everything since 2023.
matter = FakeMatter(client_since=date(2002, 1, 1), opposing_since=date(2023, 1, 1))
TODAY = date(2023, 6, 30)

ours = run(ALL, SOME, side="client", matter=matter, today=TODAY)
theirs = run(ALL, SOME, side="opposing", matter=matter, today=TODAY)

check("our grid runs from 2002", ours["years"][0], 2002)
check("through today", ours["years"][-1], 2023)
check("theirs runs only from 2023", theirs["years"], [2023])
check("and the scope says which date bounds it", theirs["scope"]["since"], "2023-01-01")
check("our scope carries the other one", ours["scope"]["since"], "2002-01-01")

# THE POINT OF THE LOOK-BACK: an unbounded report can only say "anything before
# 1 Jan 2023" — an open-ended sentence a motion cannot use. A bounded one turns
# both outer holes into ordinary gaps with two ends.
row = next(a for a in theirs["accounts"] if a["label"].startswith("Chase"))
check("no open-ended leading claim once bounded", row["nothing_before"], None)
check("nor an open-ended trailing one", row["nothing_after"], None)
check("the trailing hole is a dated range instead",
      row["gaps"], [{"start": "2023-02-01", "end": "2023-06-30", "days": 150}])

# The same account, unbounded: the identical facts can only be stated as two
# open-ended dates, and neither can go in a request for production.
loose = next(a for a in run(ALL, SOME, side="opposing", today=TODAY)["accounts"]
             if a["label"].startswith("Chase"))
check("unbounded, it can only name a date", loose["nothing_after"], "2023-01-31")
check("with no gap to point at", loose["gaps"], [])

# A statement starting after the look-back leaves a real leading gap — the days
# between what was asked for and the earliest thing produced.
late = [FakeStatement(9, 2, date(2023, 4, 10), date(2023, 5, 9))]
row = run([THEIRS], late, side="opposing", matter=matter, today=TODAY)["accounts"][0]
check("the leading gap runs from the look-back to the first statement",
      row["gaps"][0], {"start": "2023-01-01", "end": "2023-04-09", "days": 99})

print("\nWhat the look-back excludes")

# A statement older than the request is not a gap and not an obligation. Only
# the window somebody actually asked for is reported.
old_and_new = [
    FakeStatement(1, 2, date(2019, 1, 1), date(2019, 1, 31)),
    FakeStatement(2, 2, date(2023, 3, 1), date(2023, 3, 31)),
]
report = run([THEIRS], old_and_new, side="opposing", matter=matter, today=TODAY)
row = report["accounts"][0]
check("no gap is reported before the request reaches",
      all(g["start"] >= "2023-01-01" for g in row["gaps"]), True)
check("the hole starts at the look-back, not at the older statement",
      row["gaps"][0]["start"], "2023-01-01")

# An account with nothing produced at all, inside a bounded window, is the
# strongest finding the report can make — and an unbounded one cannot make it.
report = run([THEIRS], [], side="opposing", matter=matter, today=TODAY)
row = report["accounts"][0]
check("the whole window is the gap", row["gaps"],
      [{"start": "2023-01-01", "end": "2023-06-30", "days": 181}])
check("where unbounded it can say nothing at all",
      run([THEIRS], [], side="opposing", today=TODAY)["accounts"][0]["gaps"], [])

# A closing marker still wins: nothing is missing after the account ended, even
# though the request runs to today.
closed = [FakeStatement(1, 2, date(2023, 1, 1), date(2023, 1, 31),
                        boundary=StatementBoundary.closing)]
row = run([THEIRS], closed, side="opposing", matter=matter, today=TODAY)["accounts"][0]
check("a closed account is not missing the rest of the window",
      row["gaps"], [])

# ── The exhibit ──────────────────────────────────────────────────────────────

print("\nThe matrix as a document")

from db.models.matter import ClientAlignment  # noqa: E402
from services.exhibit_service import (  # noqa: E402
    _visible as visible_columns, to_csv, to_markdown, to_pdf,
)

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ExhibitMatter(FakeMatter):
    matter_number = "DF-24-01234"
    court_name = "401st Judicial District Court"
    county = "Parker"
    state = "Texas"
    matter_name = "Salmons divorce"
    case_style = "IN THE MATTER OF THE MARRIAGE OF GABRIEL SALMONS AND ANA SALMONS"
    client_alignment = ClientAlignment.petitioner


def build(accounts, statements, side=None, matter=None, today=None,
          name="Statements Produced and Not Produced"):
    original = (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
                mod.MatterRepository)
    matter = matter or ExhibitMatter()
    mod.FinancialAccountRepository = lambda m: FakeAccountRepo(accounts)
    mod.FinancialAccountStatementRepository = lambda m: FakeStatementRepo(statements)
    mod.MatterRepository = lambda m: FakeMatterRepo(matter)
    try:
        return compliance_service.build_exhibit(
            object(), matter, side=side, exhibit_name=name, today=today)
    finally:
        (mod.FinancialAccountRepository, mod.FinancialAccountStatementRepository,
         mod.MatterRepository) = original


THEM = FakeAccount(60, "Liberty", "8157", responsibility=ProductionResponsibility.opposing)
RUN = [
    FakeStatement(200, 60, date(2023, 1, 4), date(2023, 2, 3), bates="LIB0100",
                  boundary=StatementBoundary.opening),
    FakeStatement(201, 60, date(2023, 2, 4), date(2023, 3, 3), bates="LIB0108"),
    # March deliberately absent.
    FakeStatement(203, 60, date(2023, 4, 4), date(2023, 5, 3), bates="LIB0124"),
]
BOUND = ExhibitMatter(opposing_since=date(2023, 1, 1))
exhibit = build([THEM], RUN, side="opposing", matter=BOUND, today=date(2023, 6, 30))

# Fourteen columns will not fit a portrait page without wrapping the Bates
# numbers, and a grid nobody can scan is no grid.
check("landscape", exhibit.landscape, True)
check("account, type, year and twelve months", len(exhibit.columns), 15)

# THE ACCOUNT IS A TITLE, NOT A COLUMN. Repeated down every year row the name
# wraps and takes width from twelve months that need it more. It still rides in
# the row's cells so the CSV can sort on it — hidden from the printed exhibit,
# not thrown away.
check("account and type are carried for the CSV alone",
      [(c.heading, c.csv_only) for c in exhibit.columns[:3]],
      [("Account", True), ("Type", True), ("Year", False)])
check("so the printed grid is a year and twelve months",
      [c.heading for _, c in visible_columns(exhibit)],
      ["Year"] + list(MONTH_NAMES))

titles = [row for row in exhibit.rows if row.full_width]
check("one title per account", len(titles), 1)
check("naming the account and its kind", titles[0].cells[0], "Liberty ····8157 — Checking")

flat = [list(row.cells) for row in exhibit.rows if not row.full_width]
year_row = next(r for r in flat if r[2] == "2023")
check("the account still rides in the row, for the CSV", year_row[0], "Liberty ····8157")
check("the Bates number is the cell", year_row[4], "LIB0100 †")
check("a month with no statement is blank", year_row[5], "LIB0108")
check("and one that was never produced stays empty", year_row[6], "")

# THE COLOUR CANNOT SURVIVE INTO A .docx CELL, so the daggers carry it — and the
# legend has to travel with them, or the reader sees a mark they cannot read.
check("the dagger legend travels",
      any("Opening statement" in note for note in exhibit.footnotes), True)
check("both of them", any("Closing statement" in note for note in exhibit.footnotes), True)
check("and the em dash is explained",
      any("em dash marks a month outside" in note for note in exhibit.footnotes), True)

# The gaps are the point of the document, in days rather than months.
check("one finding per account", len(exhibit.findings), 1)
check("headed as what it is", exhibit.findings_title, "Not accounted for")
label, sentence = exhibit.findings[0]
check("named by account", label, "Liberty ····8157")
check_true("naming days, not months", "2023-03-04 to 2023-04-03 (31 days)" in sentence)
check_true("and running to the end of the request", "2023-06-30" in sentence)

# Which of the two obligations this measures, on the face of the document.
selection = dict(exhibit.selection)
check("says whose report it is", selection["Report"],
      "Statements the other side is responsible for producing")
check("and the period it measures", selection["Period requested"],
      "2023-01-01 through 2023-06-30")

print("\nWhat an unbounded export says instead")

loose = build([THEM], RUN, side="opposing", today=date(2023, 6, 30))
check_true("it admits it has no period",
           "Not recorded" in dict(loose.selection)["Period requested"])

print("\nAn account nobody assigned is named on the document")

mixed = [THEM, FakeAccount(61, "Ally", "5555")]
exhibit = build(mixed, RUN, side="opposing", matter=BOUND, today=date(2023, 6, 30))
check_true("said on the exhibit, not just the screen",
           "appear on neither report" in dict(exhibit.selection)["Not included"])

print("\nIt renders")

md = to_markdown(exhibit).decode("utf-8")
check_true("markdown carries the grid", "| Year | Jan | Feb |" in md)
check_true("with the account as a title above it",
           "**Liberty ····8157 — Checking**" in md)
check_true("and the findings", "## Not accounted for" in md)
check_true("and the Rule 1006 notice", "offered in court" in md)

# The CSV is the flat grid — the workbook this replaces, with no preamble.
csv_text = to_csv(exhibit).decode("utf-8-sig")
check("CSV starts at the header row", csv_text.splitlines()[0],
      "Account,Type,Year,Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec")
# The title is a printed device. In the CSV the same fact is a column, and a
# heading dropped into the middle of the data would break every filter.
check("no title rows in the CSV", "Liberty ····8157 — Checking" in csv_text, False)
check("but every row says which account it is",
      csv_text.splitlines()[1].startswith("Liberty ····8157,Checking,"), True)
check("no caption in it", "Cause No" in csv_text, False)
check("nor the findings", "Not accounted for" in csv_text, False)

pdf = to_pdf(exhibit)
check("pdf magic", pdf[:5], b"%PDF-")

# ── One grid per page ────────────────────────────────────────────────────────
#
# These are read one account at a time across a counsel table. An account whose
# years straddle a fold is the one thing the grid must not do — the reader loses
# the column headings and has to count months to find August.

print("\nEach grid starts its own page in the PDF")

many_accounts, one_each = [], []
for n, (bank, last4, kind) in enumerate([
        ("Liberty", "8157", AccountType.checking),
        ("Ally", "5555", AccountType.checking),
        ("Amex", "1122", AccountType.credit_card),
        ("Vanguard", "9090", AccountType.retirement)], start=1):
    many_accounts.append(FakeAccount(n, bank, last4, kind,
                                     responsibility=ProductionResponsibility.opposing))
    one_each.append(FakeStatement(n * 10, n, date(2021, 1, 1), date(2021, 1, 31),
                                  bates="%s%04d" % (bank[:3].upper(), n)))

# Four small grids that would otherwise share a page — which is the point.
wide = build(many_accounts, one_each, side="opposing",
             matter=ExhibitMatter(opposing_since=date(2021, 1, 1)),
             today=date(2023, 12, 31))

marked = [row.cells[0] for row in wide.rows if row.page_break]
check("every account but the first asks for a page", len(marked), 3)
# The break sits on the title row, so the account's name never strands at the
# foot of the page before its grid.
check("and it is the title that carries it",
      marked, ["Liberty ····8157 — Checking", "Amex ····1122 — Credit cards",
               "Vanguard ····9090 — Retirement"])

import pymupdf  # noqa: E402

with pymupdf.open(stream=to_pdf(wide), filetype="pdf") as document:
    pages = [page.get_text() for page in document]

first_seen = {}
for bank in ("Liberty", "Ally", "Amex", "Vanguard"):
    first_seen[bank] = next((n for n, text in enumerate(pages, start=1) if bank in text), None)
check("no two grids begin on the same page",
      len(set(first_seen.values())), len(first_seen))
check("and none of them went missing", None in first_seen.values(), False)


# ── Twelve months have to fit on the page ────────────────────────────────────
#
# Measured, not guessed: twelve nine-character Bates numbers plus a year column
# need 648pt, and landscape letter at one-inch margins leaves exactly 648pt. The
# first real export lost December off the right edge — the header was not on the
# page at all.

print("\nDecember stays on the page")

wide_account = FakeAccount(1, "Liberty National Bank", "8157", AccountType.checking,
                           responsibility=ProductionResponsibility.opposing)
every_month = []
for count, (year, month) in enumerate(
        ((y, m) for y in range(2019, 2024) for m in range(1, 13)), start=1):
    every_month.append(FakeStatement(count, 1, date(year, month, 1), date(year, month, 28),
                                     bates="RWP%06d" % (200 + count)))

full = build([wide_account], every_month, side="opposing",
             matter=ExhibitMatter(opposing_since=date(2019, 1, 1)),
             today=date(2023, 12, 31))
check("the exhibit asks for half-inch margins", full.margin_inches, 0.5)

with pymupdf.open(stream=to_pdf(full), filetype="pdf") as document:
    words = document[0].get_text("words")
    page_width = document[0].rect.width
rightmost = max((word[2] for word in words), default=0.0)
check("the December column is drawn",
      any(word[4] == "Dec" for word in words), True)
check_true("and the ink stays inside the page", rightmost < page_width - 8)

# THE TITLE READS ABOVE THE COLUMN HEADERS. The header is re-emitted on every
# page, so a title left in row order sits under the month names and the account
# it names reads as part of the data rather than as the caption of the grid.
with pymupdf.open(stream=to_pdf(full), filetype="pdf") as document:
    lines = [line.strip() for line in document[0].get_text().splitlines() if line.strip()]
title_at = next(i for i, line in enumerate(lines) if "Liberty National Bank" in line)
year_at = next(i for i, line in enumerate(lines) if line == "Year")
check_true("the account title comes before the Year header", title_at < year_at)

# The same exhibit at the old margin, to show the fix is the margin and not
# something incidental that happens to work today.
full.margin_inches = 1.0
with pymupdf.open(stream=to_pdf(full), filetype="pdf") as document:
    cramped = document[0].get_text("words")
check("at one inch it does not fit — which is what was seen",
      any(word[4] == "Dec" for word in cramped), False)


# ── The DOCX is a document, not a screenshot of the PDF ──────────────────────

print("\nThe Word export")

import io as _io  # noqa: E402

import docx  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402

from services.exhibit_service import to_docx  # noqa: E402
import fitz  # noqa: E402

pair = build(many_accounts[:2], one_each[:2], side="opposing",
             matter=ExhibitMatter(opposing_since=date(2021, 1, 1)),
             today=date(2021, 12, 31))
document = docx.Document(_io.BytesIO(to_docx(pair)))

# ONE TABLE PER ACCOUNT. Word repeats a header row across a *natural* page break
# and not across a manual one, so an author who splits a long table where they
# want it loses the month names on every page after — which is the whole reason
# they split it there.
check("a table per account, not one for the lot", len(document.tables), 2)
check("each carrying its own header",
      [table.rows[0].cells[0].text for table in document.tables], ["Year", "Year"])
check("and its own data",
      document.tables[0].rows[1].cells[0].text, "2021")

# The account name is a heading paragraph above its table, not a row inside it.
titles = [p.text for p in document.paragraphs if "—" in p.text and "····" in p.text]
check("a title above each grid", len(titles), 2)

section = document.sections[0]
check("narrowed left and right", (section.left_margin.inches, section.right_margin.inches),
      (0.5, 0.5))
# Width is what a wide grid runs out of. The top and bottom stay where a filing
# expects them.
check("top left alone", section.top_margin.inches, 1.0)

# The year rows carried depth=1 from when they sat under a type heading. Left
# in, the DOCX indented "2019" far enough to wrap it onto four lines inside a
# column wide enough to hold it twice over.
check("no indent on the year", any(row.depth for row in pair.rows), False)
check("and none reaches the cell",
      document.tables[0].rows[1].cells[0].paragraphs[0].paragraph_format.left_indent, None)

# The table face. `cell.text = ""` leaves an empty run ahead of the real one, so
# the drawn run is the last.
from docx.oxml.ns import qn  # noqa: E402

body = document.tables[0].rows[1].cells[0].paragraphs[0].runs[-1]
head = document.tables[0].rows[0].cells[0].paragraphs[0].runs[-1]
check("cells are set in the table face", (body.font.name, body.font.size.pt), ("Aptos", 9.0))
check("headers too, in bold", (head.font.name, head.font.size.pt, head.bold),
      ("Aptos", 9.0, True))

# SET ON ALL FOUR SCRIPT CLASSES, not just what python-docx writes. A character
# Word classes as complex-script would otherwise render in the document default,
# and one stray glyph in another face is invisible until it is printed.
fonts = body._element.rPr.rFonts
check("on every script class",
      {name: fonts.get(qn("w:" + name))
       for name in ("ascii", "hAnsi", "cs", "eastAsia")},
      {"ascii": "Aptos", "hAnsi": "Aptos", "cs": "Aptos", "eastAsia": "Aptos"})

# OOXML HAS NO FONT STACK. `w:altName` is the whole of the fallback mechanism —
# Word uses it when the face is missing and LibreOffice reads it.
table_part = next(part for part in document.part.package.iter_parts()
                  if "fontTable" in str(part.partname))
declared = table_part.blob.decode("utf-8", "ignore")
check_true("the face is declared", '<w:font w:name="Aptos">' in declared)
check_true("with something every reader has as its fallback",
           '<w:altName w:val="Times New Roman"/>' in declared)

print("\nA statement produced without a Bates stamp")

# PRODUCTIONS ARRIVE UNSTAMPED MORE OFTEN THAN THEY SHOULD, and the grid still
# has to say the month is accounted for. The screen shows a tick; the exports
# used to print the word "held", which reads as a value and invites somebody to
# go looking for it in the production. An X is a mark, not a datum.
UNSTAMPED = [
    FakeStatement(300, 60, date(2023, 1, 1), date(2023, 1, 31), bates="LIB0100",
                  boundary=StatementBoundary.opening),
    FakeStatement(301, 60, date(2023, 2, 1), date(2023, 2, 28), bates=None),
]
bare = build([THEM], UNSTAMPED, side="opposing", matter=BOUND, today=date(2023, 3, 31))
bare_row = next(list(row.cells) for row in bare.rows if not row.full_width)
check("a stamped month prints its number", bare_row[3], "LIB0100 †")
check("an unstamped one prints a mark", bare_row[4], "X")

# CENTRED, and centring is per COLUMN in this model, so the Bates numbers centre
# with it. That is the trade: a grid of numbers reads better centred than
# ragged-left, and a lone X hanging off the left edge of a wide cell reads as an
# accident rather than an entry.
check("the months are centred, the year is not",
      [c.center for c in bare.columns[2:6]], [False, True, True, True])

# EVERY FORMAT, because the word was hard-coded once and rendered four ways.
# Scoped to the grid's own data row: "Statements held" is a line in the
# Selection block and is prose about the report, not a mark in a cell.
def data_row(rendered, fmt):
    """The 2023 row of the grid, as the reader of that format sees it."""
    if fmt == "csv":
        line = next(l for l in rendered.decode("utf-8-sig").splitlines()
                    if l.startswith("Liberty"))
        return line.split(",")[2:]
    if fmt == "markdown":
        line = next(l for l in rendered.decode("utf-8").splitlines()
                    if l.startswith("| 2023"))
        return [c.strip() for c in line.strip("|").split("|")]
    if fmt == "docx":
        table = docx.Document(_io.BytesIO(rendered)).tables[0]
        return [c.text for r in table.rows for c in r.cells if r.cells[0].text == "2023"]
    page = fitz.open(stream=rendered, filetype="pdf")[0]
    top = min(w[1] for w in page.get_text("words") if w[4] == "2023")
    return [w[4] for w in page.get_text("words") if abs(w[1] - top) < 2]


for fmt, render in (("csv", to_csv), ("markdown", to_markdown),
                    ("docx", to_docx), ("pdf", to_pdf)):
    cells = data_row(render(bare), fmt)
    check_true("the %s grid marks the unstamped month" % fmt, "X" in cells)
    check_true("and never says 'held'", "held" not in cells)

# THE HEADER ROW TOO. A centred X under a left-hugging "Feb" reads as the
# month before it, and a grid whose entire content is position cannot afford
# that. Read off the XML, because python-docx reports an unset alignment as
# None and "None means left" is exactly the assumption that hid this.
grid = docx.Document(_io.BytesIO(to_docx(bare))).tables[0]
check("the month headings are centred, the Year heading is not",
      [grid.rows[0].cells[i].paragraphs[0].alignment for i in range(0, 4)],
      [None, WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.CENTER,
       WD_ALIGN_PARAGRAPH.CENTER])
head_xml = grid.rows[0].cells[2]._element.xml
check_true("which is a real w:jc in the part", '<w:jc w:val="center"/>' in head_xml)

# The mark sits under its own heading rather than beside it. Measured off the
# drawn page, because alignment is the one thing a renderer can silently drop.
words = fitz.open(stream=to_pdf(bare), filetype="pdf")[0].get_text("words")
centre = lambda text: [round((w[0] + w[2]) / 2, 1) for w in words if w[4] == text]
check("the X is centred under its month", centre("X"), centre("Feb"))

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all compliance-matrix checks passed")
