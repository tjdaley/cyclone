"""
tests/test_migration_seeds.py - A seed must not name an id it does not own.

024 seeded the chart of accounts with explicit ids and said so. Everything after
it has to assume the firm then edited that chart, because the firm did: by the
time 037 was written, ids 18-27 were Telephone, HOA Dues, Property Insurance,
Household Expenses, Groceries, Supplies, Security Monitoring, Housekeeping,
Lawn/Landscaping and Pool.

037's first draft claimed those ids for investment categories. The category
inserts would have been skipped harmlessly by ON CONFLICT -- but its keyword
rules named the same ids as LITERALS, so every dividend would have been filed
under Telephone and every margin interest charge under Housekeeping. Firm-wide,
silently, in a chart that feeds a sworn document.

Nothing here talks to a database. It reads the migration text and asserts two
things a seed written after 024 must satisfy:

  1. A rule resolves its category by description, never by a bare integer.
  2. Every category a rule names is one the same file creates, or one that
     demonstrably already exists in the chart.

Run:  venv/Scripts/python.exe tests/test_migration_seeds.py
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MIGRATIONS = os.path.join(ROOT, "db", "migrations")

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


def read(name: str) -> str:
    with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as handle:
        return handle.read()


def files_seeding_rules() -> list[str]:
    """Every migration that inserts into transaction_category_rules."""
    found = []
    for name in sorted(os.listdir(MIGRATIONS)):
        if not name.endswith(".sql"):
            continue
        body = read(name)
        if re.search(r"insert\s+into\s+transaction_category_rules", body, re.I):
            found.append(name)
    return found


print("Which migrations seed keyword rules")

seeding = files_seeding_rules()
check_true("at least one does", seeding)
print("       %s" % ", ".join(seeding))


print("\nNo rule may name a category by a bare id")

# THE FAILURE THIS EXISTS FOR. A literal id in a rule seed is a promise about
# what that id means in a table the firm edits daily, and the promise is broken
# the first time somebody adds a category. A wrong category_id does not fail
# loudly -- it files evidence under the wrong heading, on every matter.
#
# The shape being rejected is a VALUES row whose third element is an integer,
# as in ('QUALIFIED DIVIDEND', 19, 100, 'any', '...'). A file that resolves its
# target with a subquery has a "(select" there instead.
LITERAL_TARGET = re.compile(
    r"\(\s*'[^']+'\s*,\s*(\d+)\s*,\s*\d+\s*,\s*'(?:any|credit|debit)'", re.I)

for name in seeding:
    body = read(name)
    # Only the executable text: the commentary in these files quotes SQL.
    live = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("--"))
    hits = LITERAL_TARGET.findall(live)
    check("%s resolves its categories by name, not by id" % name, hits, [])


print("\nEvery category a rule names must actually be reachable")

# A rule pointing at a description nothing creates is skipped by the WHERE
# EXISTS guard rather than inserted with a null target -- safe, but silent, and
# a seed that quietly does nothing is the other half of this bug.
CATEGORY_TARGET = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*\d+\s*,\s*'(?:any|credit|debit)'", re.I)
CREATES = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*\d+\s*,\s*(?:true|false)\s*\)", re.I)

# Categories the DEPLOYED chart holds that no migration creates -- the firm
# built most of its chart by hand in the app. Listed here rather than derived,
# because that is the only place the fact is written down: a rule pointing at a
# name nobody has is skipped silently, so a rename has to fail here or it fails
# nowhere. Verified against the chart as at 2026-09-07.
ALREADY_IN_THE_CHART = {
    # Referenced by 032.
    "interest income", "bank fees", "atm and other cash withdrawals",
    # Referenced by 037.
    "brokerage fees",
    # Top-level parents 037 attaches to.
    "income", "other", "debt payments", "non-income investment activity",
}

for name in seeding:
    body = read(name)
    live = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("--"))
    creates = {desc.strip().lower() for desc, _parent in CREATES.findall(live)}
    wanted = {cat.strip().lower() for _pattern, cat in CATEGORY_TARGET.findall(live)}
    unreachable = sorted(wanted - creates - ALREADY_IN_THE_CHART)
    check("%s: every rule target is created here or already in the chart" % name,
          unreachable, [])


print("\n037 specifically")

body = read("037_brokerage_cash_flow.sql")
live = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("--"))

check_true("it repairs the earlier draft's rules rather than leaving them",
           "delete from transaction_category_rules" in live.lower())
check_true("and is safe to run twice, clearing its own rules first",
           "seeded by 037" in live.lower())

# Parents are resolved among TOP-LEVEL categories only. "Other",
# "Transportation", "Miscellaneous" and "Insurance" all appear more than once in
# the deployed chart, so a bare description lookup for a parent is ambiguous and
# would attach a category under a pet-expense heading.
check_true("parent lookups are restricted to top-level categories",
           "parent_id is null" in live.lower())

# THE NAMES MUST MATCH THE CHART THE FIRM BUILT. It created these by hand as
# "Investment Gain/Loss" (139) and "Investment Gain/Loss (not on FIS)" (140);
# a seed spelling them "Unrealized" would satisfy its own NOT EXISTS guard and
# create a second pair, splitting the same money across two lines of a sworn
# statement -- the quiet version of the id bug above.
#
# Investment gain/loss defaults to the version that appears on the FIS: a party
# moving $5,000 a month into a brokerage account while accumulating $4,000 a
# month of unrealized losses is depleting the estate, and a report that cannot
# see that has a money sink off its radar. Child support exhibits read the
# Income subtree, so it is filed under Other and never reaches a support figure.
pairs = {desc.strip(): parent.strip() for desc, parent in CREATES.findall(live)}
check("the FIS-visible version is filed under Other",
      pairs.get("Investment Gain/Loss"), "Other")
check("and the off-FIS twin under the non-income parent",
      pairs.get("Investment Gain/Loss (not on FIS)"), "Non-Income Investment Activity")
check("dividends are income, where a support exhibit will find them",
      pairs.get("Dividend Income"), "Income")
check("so are capital gain distributions",
      pairs.get("Capital Gain Distribution"), "Income")

on_fis = dict(
    (desc.strip(), flag) for desc, flag in
    re.findall(r"\(\s*'([^']+)'\s*,\s*'[^']+'\s*,\s*\d+\s*,\s*(true|false)\s*\)", live, re.I)
)
check("the default counts", on_fis.get("Investment Gain/Loss"), "true")
check("the twin does not", on_fis.get("Investment Gain/Loss (not on FIS)"), "false")

# Interest Income and Brokerage Fees already exist in the deployed chart.
# Creating a second of either would split the same money across two lines of a
# sworn statement.
check("it does not create a second Interest Income", "Interest Income" in pairs, False)
check("nor a second Brokerage Fees", "Brokerage Fees" in pairs, False)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all migration-seed checks passed")
