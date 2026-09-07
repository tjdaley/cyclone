"""
tests/test_platform_seeds.py - The seeded patterns in migration 036.

A seed is firm-wide and silent. It reaches every matter without anybody
choosing it, so a pattern that is also an ordinary English word mis-files
evidence on cases nobody is looking at -- the same failure as TARGET matching
inside STARGETTER LLC, one level up and with a wider blast radius.

This reads the patterns out of the migration rather than restating them, so a
pattern added to the SQL is tested by the next run instead of the day somebody
remembers to update a list here.

Run:  venv/Scripts/python.exe tests/test_platform_seeds.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.category_rule_service import matches, prepare  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


MIGRATION = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "db", "migrations",
    "036_value_platforms.sql",
)

# (null, 'PATTERN', 'classification', ...
_SEED = re.compile(r"^\s*\(null,\s*'([^']+)',\s*'(creditor|custodian|not_creditor)'",
                   re.M)


def seeds() -> list[tuple[str, str]]:
    with open(MIGRATION, encoding="utf-8") as handle:
        return _SEED.findall(handle.read())


print("What 036 seeds")

rows = seeds()
patterns = [p for p, _ in rows]
check_true("the migration is readable and seeds something", len(rows) > 30)
check("nothing is seeded as a suppression -- that is always somebody's decision",
      [p for p, kind in rows if kind == "not_creditor"], [])
check("no pattern is seeded twice", len(patterns), len(set(patterns)))
check("every pattern clears the three-character floor",
      [p for p in patterns if len(p.strip()) < 3], [])

print("\nOrdinary spending must not match any of them")

# Real shapes off produced statements: groceries, fuel, restaurants, utilities,
# insurance, retail. None of these names a platform or a lender, and every one
# of them appears hundreds of times in a single production -- so a pattern that
# matched one would not produce a wrong row, it would produce a wrong row on
# every matter in the firm.
INNOCENT = [
    "KROGER #4521 PLANO TX",
    "WAL-MART SUPERCENTER #1234",
    "SHELL OIL 57445267403 FRISCO TX",
    "CHICK-FIL-A #02388 ALLEN TX",
    "ATMOS ENERGY 8009884466 TX",
    "CITY OF LEWISVILLE UTILITY PMT",
    "STATE FARM INSURANCE AUTOPAY",
    "AMAZON.COM*2H4KL9OI3 AMZN.COM/BILL",
    "TARGET 00012345 PLANO TX",
    "STARGETTER LLC CONSULTING",
    "CROSSROADS MARKET DALLAS TX",
    "USPS SHIPPING ZIPCODE 75024",
    "NETFLIX.COM 8667797538 CA",
    "COSTCO WHSE #0678 PLANO TX",
    "TOM THUMB #3712 FRISCO TX",
    "SPECTRUM 8556435262 MO",
    "CURRENT ELECTRIC SERVICE CO",
    "DAVE AND BUSTERS #24 DALLAS",
    "EMPOWER RETIREMENT LOAN REPAY",
    "MARCUS THEATRES 0142",
    "ALLY MOVING AND STORAGE LLC",
    "GEMINI SUSHI RESTAURANT PLANO",
    "PUBLIC STORAGE 25516",
    "STASH HOUSE BBQ MCKINNEY TX",
    "WISE GUYS PIZZA CARROLLTON",
    "ACORNS PRESCHOOL TUITION PMT",
    "MERRILL GARDENS SENIOR LIVING",
    "CHIMEY SWEEP SERVICES LLC",
    "REVOLUTION BREWING CHICAGO",
    "M1 FINANCIAL ADVISORS INC",
    "PUBLIC HOUSE RESTAURANT",
]

for description in INNOCENT:
    prepared = prepare(description)
    hit = [p for p in patterns if matches(prepared, p)]
    check("no seed matches %r" % description, hit, [])

print("\nReal platform descriptions must match")

# Descriptor shapes as they print on a statement. If one of these fails, the
# seed is decorative: it exists in the table and finds nothing.
EXPECTED = [
    ("VENMO PAYMENT 1042956789", "VENMO"),
    ("VENMO CASHOUT 1042956789", "VENMO"),
    ("PAYPAL INST XFER 1029384756", "PAYPAL"),
    ("PAYPAL *STEAMGAMES 4029357733", "PAYPAL"),
    ("CASH APP*JOHN SMITH", "CASH APP"),
    ("SQUARE CASH SENDING", "SQUARE CASH"),
    ("APPLE CASH SENT MONEY", "APPLE CASH"),
    ("COINBASE.COM 8887 8889 CA", "COINBASE"),
    ("COINBASE INC 8888888888", "COINBASE"),
    ("CRYPTO COM 8556744966", "CRYPTO COM"),
    ("ROBINHOOD 8006676380 CA", "ROBINHOOD"),
    ("ACH DEBIT WEBULL FINANCIAL", "WEBULL"),
    ("E TRADE ACH TRANSFER", "ETRADE"),
    ("ETRADE ACH TRANSFER", "ETRADE"),
    ("FIDELITY INVESTMENTS FID BKG SVC", "FIDELITY"),
    ("VANGUARD BUY INVESTMENT", "VANGUARD"),
    ("SCHWAB BROKERAGE TRANSFER", "SCHWAB"),
    ("TD AMERITRADE CLEARING", "TD AMERITRADE"),
    ("SOFI BANK TRANSFER", "SOFI"),
    ("CHIME TRANSFER FROM CHECKING", "CHIME"),
    ("ALLY BANK TRANSFER DDA", "ALLY BANK"),
    ("MARCUS BY GOLDMAN SACHS TRANSFER", "MARCUS BY GOLDMAN"),
    ("GOOGLE PAY BALANCE ADD", "GOOGLE PAY"),
    ("AFFIRM PAY MZWQ4XKLPQ", "AFFIRM"),
    ("KLARNA*PAYMENT 4402956", "KLARNA"),
    ("AFTERPAY US INC 8555675111", "AFTERPAY"),
    ("ZIP CO US INC PAYMENT", "ZIP CO"),
]

for description, wanted in EXPECTED:
    check_true("%r matches %s" % (description, wanted),
               matches(prepare(description), wanted))
    check_true("  and %s is actually seeded" % wanted, wanted in patterns)

print("\nOne description must not light up two platforms")

# Two rows for one payee is not fatal, but it doubles the money the report
# shows moving and reads as two findings. The scan keeps the LAST match, so a
# collision here would silently pick by seed order rather than by correctness.
for description, wanted in EXPECTED:
    prepared = prepare(description)
    hit = [p for p in patterns if matches(prepared, p)]
    if len(hit) > 1:
        check("%r matches only one seed" % description, hit, [wanted])

# Cash App genuinely has two descriptors and they share no words, so two rows
# are right. E*TRADE does NOT: matching flattens spaces away, so "E TRADE" and
# "ETRADE" are one pattern, and seeding both would double every figure.
check("Cash App has two spellings and they do not overlap",
      matches(prepare("SQUARE CASH SENDING"), "CASH APP"), False)
check("a spaced alias of a seeded pattern is a duplicate, not an alias",
      matches(prepare("E TRADE ACH TRANSFER"), "ETRADE"), True)
check("so only one E*TRADE pattern is seeded",
      [p for p in patterns if p.replace(" ", "") == "ETRADE"], ["ETRADE"])

print("\nFour patterns kept bare, as a recorded trade")

# These are NOT oversights. Each can be made to collide with an invented
# business name, but none is an ordinary English word, and qualifying them
# risks the opposite failure: SoFi alone prints SOFI BANK, SOFI SECURITIES and
# SOFI LENDING, so any one qualified pattern misses two of its three products.
# A false positive costs a glance at the example line; a false negative means an
# undisclosed account never surfaces. The trade is written down here so that
# changing it is a decision rather than a surprise.
ACCEPTED = {
    "SOFI": "SOFI STADIUM PARKING INGLEWOOD",
    "KRAKEN": "KRAKEN RUM SPECS LIQUOR",
    "VARO": "VARO TEX MEX GRILL",
    "BETTERMENT": "BETTERMENT HOME REPAIR CO",
}
for pattern, collision in ACCEPTED.items():
    check_true("%s is still seeded bare" % pattern, pattern in patterns)
    check("and it does collide with %r, knowingly" % collision,
          matches(prepare(collision), pattern), True)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all platform-seed checks passed")
