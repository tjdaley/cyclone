"""
app/util/amount_filter.py - An amount range a person typed, read the way they meant it.

**The sign the user types is the filter's most important character**, and it is
why this is a string filter rather than two numbers. A plain figure searches by
MAGNITUDE and matches both directions, which is what finds an offsetting pair: a
$5,000 transfer out of one produced account and the $5,000 arriving in another
are one movement of money, and an exhibit about it needs both lines. A typed
sign narrows to one side -- `-5000` is money out, `+5000` is money in.

ONE RULE: MAGNITUDES ALWAYS, AND THE SIGN CHOOSES THE SIDE
────────────────────────────────────────────────────────────────────────────
The alternative -- treating a signed entry as a literal numeric bound -- reads
reasonably and fails silently on the shortest query anybody will type. "Money
out of five thousand or more" is entered as `-5000` in the *from* box, and under
numeric bounds that means ``amount >= -5000``: every credit, every small debit,
the whole production, returned without complaint. A filter whose commonest
single-field use quietly matches everything is the confident-wrong failure this
codebase spends its effort avoiding.

So both bounds are magnitudes, and the sign selects which side of zero they
apply to. Consequences worth knowing:

  * ``-500`` alone is "money out, $500.00 or more".
  * ``-500`` to ``-1000`` is money out between $500 and $1,000 -- the boxes read
    as smallest-magnitude-first, which is how they read for an unsigned search
    too. There is one ordering rule rather than one per sign.
  * Mixing ``-`` and ``+`` is refused rather than guessed at. A range that
    crosses zero is what you get by leaving the signs off.

Amounts are signed by how they move the balance the institution printed
(CLAUDE.md §11), so "money out" is the negative side on a deposit account and
is a card PAYMENT on a credit card. This filter is about the printed sign and
says so in the words it produces; it deliberately does not apply the FIS's
household-sign inversion, which answers a different question.
"""
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import NamedTuple, Optional

#: Longer than any real figure. A bound is a number somebody typed, and a cap
#: keeps a pathological string out of Decimal and out of the query URI.
_MAX_CHARS = 24


class AmountSide(str, Enum):
    """Which side of zero an amount range applies to."""

    #: No sign typed: match the magnitude in both directions. Finds the pair.
    either = "either"
    #: ``-`` typed: money leaving, as the statement printed it.
    out = "out"
    #: ``+`` typed: money arriving.
    into = "in"


class AmountRange(NamedTuple):
    """A resolved amount filter: magnitudes, and the side they apply to."""

    #: Smallest magnitude, inclusive. None means unbounded below.
    low: Optional[Decimal]
    #: Largest magnitude, inclusive. None means unbounded above.
    high: Optional[Decimal]
    side: AmountSide

    @property
    def is_set(self) -> bool:
        """Whether anything was actually filtered on."""
        return self.low is not None or self.high is not None


class _Bound(NamedTuple):
    magnitude: Decimal
    side: AmountSide


def _parse_bound(raw: str, label: str) -> Optional[_Bound]:
    """
    One typed figure as a magnitude plus the side its sign asks for.

    Accepts what people actually type: ``$1,250.00``, ``-1250``, ``+ 1250``.
    Rejects anything else with a sentence naming the box, because a filter that
    silently ignored a typo would return a result set that looks authoritative
    and answers a different question.

    :param raw: What the user typed. Blank or None means no bound.
    :param label: The field's name, for the error message.
    :return: The bound, or None when nothing was typed.
    :rtype: Optional[_Bound]
    :raises ValueError: When the text is not a figure.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > _MAX_CHARS:
        raise ValueError("%s is too long to be an amount." % label)

    side = AmountSide.either
    if text[0] in "+-":
        side = AmountSide.out if text[0] == "-" else AmountSide.into
        text = text[1:].strip()

    # Currency and grouping are noise a person pastes in; the digits are the
    # value. A second sign inside the figure is not noise -- it is a mistake.
    cleaned = text.replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValueError("%s has a sign but no amount." % label)
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(
            "%s is not an amount. Type a figure such as 5000, -5000 for money "
            "out, or +5000 for money in." % label
        ) from e
    if not value.is_finite():
        raise ValueError("%s is not an amount." % label)
    if value < 0:
        # "--500", or "-" plus a negative. The sign is the side; it is not
        # arithmetic, and two of them is a typo rather than a double negative.
        raise ValueError("%s has more than one sign." % label)
    return _Bound(value, side)


def resolve(amount_min: Optional[str], amount_max: Optional[str]) -> AmountRange:
    """
    Two typed boxes as one filter.

    :param amount_min: Smallest magnitude, as typed. A leading ``-`` or ``+``
        selects a side.
    :param amount_max: Largest magnitude, as typed.
    :return: The resolved range. ``is_set`` is False when neither box was used.
    :rtype: AmountRange
    :raises ValueError: On an unreadable figure, two different signs, or a
        smallest larger than the largest.
    """
    low = _parse_bound(amount_min, "The smallest amount")
    high = _parse_bound(amount_max, "The largest amount")

    sides = {b.side for b in (low, high) if b is not None and b.side is not AmountSide.either}
    if len(sides) > 1:
        raise ValueError(
            "One direction at a time: use - on both boxes for money out, + on "
            "both for money in, or no sign at all to match either direction."
        )
    side = sides.pop() if sides else AmountSide.either

    if low is not None and high is not None and low.magnitude > high.magnitude:
        raise ValueError(
            "The smallest amount is larger than the largest. Both boxes are "
            "amounts rather than positions, so the smaller figure goes first "
            "whichever direction you are searching."
        )
    return AmountRange(
        low=low.magnitude if low is not None else None,
        high=high.magnitude if high is not None else None,
        side=side,
    )


def _money(value: Decimal) -> str:
    """A magnitude as a dollar figure. Decimal throughout -- never a float."""
    return "${:,.2f}".format(value)


def describe(amount_range: AmountRange) -> Optional[str]:
    """
    The filter in words, for an exhibit's Selection block.

    A reader who did not run the query has to be able to tell a both-directions
    search from a one-sided one, or two exhibits drawn from the same matter look
    contradictory. **Every phrase names the direction**, including the default,
    because "between $500 and $1,000" silently omits the most surprising thing
    about it.

    :return: One sentence, or None when nothing was filtered.
    :rtype: Optional[str]
    """
    low, high, side = amount_range
    if low is None and high is None:
        return None

    if low is not None and high is not None:
        span = "between %s and %s" % (_money(low), _money(high))
    elif low is not None:
        span = "%s or more" % _money(low)
    else:
        span = "up to %s" % _money(high)   # type: ignore[arg-type]

    if side is AmountSide.out:
        return "Money out, %s" % span
    if side is AmountSide.into:
        return "Money in, %s" % span
    return "%s, in either direction" % span
