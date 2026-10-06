"""
tests/test_amount_filter.py - The amount range, and the sign that changes its meaning.

The filter searches by MAGNITUDE unless a sign is typed, because an offsetting
pair -- $5,000 leaving one produced account and $5,000 arriving in another -- is
one movement of money and an exhibit about it needs both lines.

The rule that needs a test rather than a comment is what a LONE signed bound
means. Read as a literal numeric bound, ``-5000`` in the "at least" box means
``amount >= -5000``: the entire production, returned without complaint, for the
shortest query anybody will type. Read as "money out, $5,000 or more" it answers
the question that was asked. This suite pins the second reading, the ordering
rule that follows from it, and the PostgREST filters each case produces --
including the ``or`` that stands in for ``abs()``, which PostgREST cannot call.

Run:  venv/Scripts/python.exe tests/test_amount_filter.py
"""
import os
import sys
from decimal import Decimal

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from db.repositories.financial import _apply_amount_range  # noqa: E402
from util import amount_filter  # noqa: E402
from util.amount_filter import AmountSide  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def refused(label: str, amount_min, amount_max, expect_words: str) -> None:
    """A bad range must raise, and the sentence must say what to type."""
    try:
        amount_filter.resolve(amount_min, amount_max)
    except ValueError as e:
        check(label, expect_words in str(e), True)
    else:
        check(label, "accepted", "refused")


class FakeQuery:
    """Records what the repository asked PostgREST for."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def gte(self, column, value):
        self.calls.append(("gte", column, value))
        return self

    def lte(self, column, value):
        self.calls.append(("lte", column, value))
        return self

    def or_(self, expression):
        self.calls.append(("or", expression))
        return self


def filters_for(amount_min, amount_max) -> list[tuple]:
    query = FakeQuery()
    _apply_amount_range(query, amount_filter.resolve(amount_min, amount_max))
    return query.calls


print("No sign means either direction, which is what finds the pair")
check("a plain figure is a magnitude on both sides",
      amount_filter.resolve("5000", None),
      (Decimal("5000"), None, AmountSide.either))
check("a floor alone becomes everything outside [-5000, 5000]",
      filters_for("5000", None), [("or", "amount.gte.5000,amount.lte.-5000")])
check("a window becomes the two mirrored windows, because PostgREST has no abs()",
      filters_for("500", "1000"),
      [("or", "and(amount.gte.500,amount.lte.1000),and(amount.gte.-1000,amount.lte.-500)")])
check("a ceiling alone needs no or -- one window straddles zero",
      filters_for(None, "100"),
      [("gte", "amount", "-100"), ("lte", "amount", "100")])

print("")
print("A SIGN PICKS THE SIDE, and a lone one must not match everything")
check("-5000 is money out, five thousand or more",
      amount_filter.resolve("-5000", None),
      (Decimal("5000"), None, AmountSide.out))
check("which is amount <= -5000, NOT amount >= -5000",
      filters_for("-5000", None), [("lte", "amount", "-5000")])
check("+5000 is money in",
      filters_for("+5000", None), [("gte", "amount", "5000")])
check("money out, between 500 and 1000, is mirrored -- larger magnitude is the smaller number",
      filters_for("-500", "-1000"),
      [("lte", "amount", "-500"), ("gte", "amount", "-1000")])
check("money in, up to 1000", filters_for(None, "+1000"), [("lte", "amount", "1000")])

print("")
print("Both boxes are magnitudes, so one ordering rule serves every direction")
refused("1000 to 500 is refused", "1000", "500", "smaller figure goes first")
refused("and so is -1000 to -500, by the same rule", "-1000", "-500",
        "smaller figure goes first")
check("a sign on one box is enough to set the side",
      amount_filter.resolve("-500", "1000").side, AmountSide.out)
refused("but two different signs are refused rather than guessed at",
        "-500", "+900", "One direction at a time")

print("")
print("What people actually type")
check("currency and grouping are noise",
      amount_filter.resolve("$1,250.00", None).low, Decimal("1250.00"))
check("a space after the sign still reads as the sign",
      amount_filter.resolve("- 1250", None), (Decimal("1250"), None, AmountSide.out))
check("blank is no bound at all",
      amount_filter.resolve("", "   ").is_set, False)
check("None is no bound at all", amount_filter.resolve(None, None).is_set, False)
refused("a typo is refused, naming what to type", "5oo0", None, "Type a figure")
refused("a sign with no figure is refused", "-", None, "sign but no amount")
refused("two signs are a typo, not a double negative", "--500", None, "more than one sign")
refused("a word is refused", None, "lots", "Type a figure")

print("")
print("No filter means no filter -- nothing is added to the query")
check("an unset range adds nothing", filters_for(None, None), [])
check("and is_set says so", amount_filter.resolve(None, None).is_set, False)

print("")
print("The exhibit says which direction it searched")
check("either direction is stated, not left to be assumed",
      amount_filter.describe(amount_filter.resolve("500", "1000")),
      "between $500.00 and $1,000.00, in either direction")
check("money out reads as money out",
      amount_filter.describe(amount_filter.resolve("-5000", None)),
      "Money out, $5,000.00 or more")
check("money in, with a ceiling",
      amount_filter.describe(amount_filter.resolve(None, "+250.5")),
      "Money in, up to $250.50")
check("a floor alone, either direction",
      amount_filter.describe(amount_filter.resolve("5000", None)),
      "$5,000.00 or more, in either direction")
check("nothing filtered describes as nothing",
      amount_filter.describe(amount_filter.resolve(None, None)), None)

print("")
print("The API refuses a bad range at the boundary too")
from schemas.financial import TransactionSearchRequest  # noqa: E402

check("a readable range is accepted",
      TransactionSearchRequest(amount_min="-5000").amount_min, "-5000")
try:
    TransactionSearchRequest(amount_min="5oo0")
    check("an unreadable one is refused by the schema", "accepted", "refused")
except Exception as e:  # pydantic wraps it in a ValidationError
    check("an unreadable one is refused by the schema", "Type a figure" in str(e), True)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all amount-filter checks passed")
