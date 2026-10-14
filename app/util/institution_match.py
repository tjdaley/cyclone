"""
app/util/institution_match.py - Are these two spellings the same institution?

Asked four times by the undisclosed-accounts report — of a wire's originating
bank, a creditor's name, a value platform's name, and an account's institution —
and the answer decides whether something is reported as *not produced*. Getting
it wrong costs in both directions, and they are not symmetric:

  * **Too strict** and a bank the firm already holds statements for is reported
    as undisclosed. That is the complaint this module answers: "BofA" and "Bank
    of America, N.A." are one bank, and a report that is excited about an
    account somebody already produced teaches its reader to skim it.
  * **Too loose** and a genuinely undisclosed account is suppressed — evidence
    hidden, silently. **That is the worse failure**, so nothing here ever
    suppresses on a near match: `same()` is for exact identity after
    normalization, and `similar()` only ever DEMOTES a finding to "probably
    one of yours, go and look", which stays on the screen and off the exhibit.

WHAT WAS THERE BEFORE, AND WHY IT HAD TO GO
────────────────────────────────────────────────────────────────────────────
Substring containment on a squashed name — ``"CHASE" in "JPMORGANCHASE"``. It
is too strict for "BOFA" against "BANKOFAMERICA" (no containment either way),
and too LOOSE in a way nobody would guess: ``"CHASE" in "PURCHASE"``. A produced
Chase card therefore suppressed any creditor whose name contained "purchase",
which is the quiet direction of the same mistake. Tokens fix both: a word is
matched as a word.

THE DISTINCTIVE TOKEN IS THE WHOLE IDEA
────────────────────────────────────────────────────────────────────────────
Bank names are mostly furniture. "First National Bank of Texas" and "First
National Bank of Omaha" share four words out of six and are different banks;
what separates them is TEXAS and OMAHA. So comparison runs on the tokens left
after the furniture is removed, and two names match only when their distinctive
tokens agree. Where a name is nothing *but* furniture — "First National Bank" —
the full token set is compared instead, because that is all the evidence there
is.
"""
import re
from typing import Iterable, Optional

#: Words that appear in so many institution names that they distinguish nothing.
#: Removed before comparison, never from what is displayed.
_FURNITURE = frozenset({
    "BANK", "BANKS", "BANKING", "BANCORP", "BANCSHARES", "BK",
    "NATIONAL", "NATL", "NA", "FEDERAL", "FED", "FSB", "SSB", "FCU", "CU",
    "CREDIT", "UNION", "SAVINGS", "LOAN", "TRUST", "FINANCIAL", "FINANCE",
    "COMPANY", "CO", "CORP", "CORPORATION", "INC", "INCORPORATED", "LLC", "LP",
    # "AMERICA" is deliberately NOT here: it is the only distinctive word in
    # "Bank of America".
    "THE", "OF", "AND", "USA", "US",
    "SERVICES", "SERVICE", "CARD", "CARDS", "CARDMEMBER", "ACCOUNT", "ACCOUNTS",
    "BRANCH", "AG", "PLC", "SA", "NV", "GROUP", "HOLDINGS",
})

# ── ALIASES COME IN TWO KINDS, AND CONFLATING THEM MISREADS A REAL BANK ──────
#
# "Chase" on its own means JPMorgan Chase. The same word inside "Chevy Chase
# Trust" does not — and expanding it there turned a different institution into
# a match for the firm's Chase card. So a brand word is only an alias when it is
# the WHOLE name; abbreviations that are not words in anybody else's name expand
# wherever they appear.
#
# Both lists are deliberately short. A wrong alias suppresses evidence firm-wide
# and silently, which is the one failure this module exists to avoid.

#: Expanded only when the entire name is this and nothing else.
_WHOLE_NAME_ALIASES = {
    "CHASE": "JPMORGAN CHASE",
    "CITI": "CITIBANK",
    "CITIGROUP": "CITIBANK",
    "SCHWAB": "CHARLES SCHWAB",
    "ETRADE": "E TRADE",
    "SUNTRUST": "TRUIST",
    "BANKAMERICA": "BANK OF AMERICA",
    "WELLSFARGO": "WELLS FARGO",
    "MERRILLLYNCH": "MERRILL",
}

#: Expanded anywhere. Abbreviations only — a string nobody else's name contains.
_ALIASES = {
    "BOFA": "BANK OF AMERICA",
    "BOA": "BANK OF AMERICA",
    "BANKAMERICA": "BANK OF AMERICA",
    "JPMC": "JPMORGAN CHASE",
    "AMEX": "AMERICAN EXPRESS",
    "BBT": "TRUIST",
    "CAPONE": "CAPITAL ONE",
}

_WORD = re.compile(r"[A-Z0-9]+")


def _expand(name: str) -> str:
    """
    Replace an alias with the full name it stands for.

    Whole-name aliases first — "Chase" is JPMorgan Chase, "Chevy Chase Trust" is
    not — then the abbreviations, which are safe anywhere.
    """
    words = _WORD.findall((name or "").upper())
    whole = _WHOLE_NAME_ALIASES.get("".join(words))
    if whole:
        words = _WORD.findall(whole)
    out: list[str] = []
    for word in words:
        out.extend(_WORD.findall(_ALIASES.get(word, word)))
    return " ".join(out)


def tokens(name: Optional[str]) -> frozenset[str]:
    """
    Every word of a name, aliases expanded. Empty for nothing usable.

    **Single characters are dropped**, because punctuation is where they come
    from: "Bank of America, N.A." tokenises to ``N`` and ``A``, which then look
    like distinguishing words and stop it matching "Bank of America". No
    institution is identified by one letter.
    """
    return frozenset(
        word for word in _WORD.findall(_expand(name or "")) if len(word) > 1
    )


def distinctive(name: Optional[str]) -> frozenset[str]:
    """
    The words that actually identify the institution.

    Empty when the name is nothing but furniture ("First National Bank"), which
    callers read as "compare the whole thing instead".
    """
    return frozenset(word for word in tokens(name) if word not in _FURNITURE)


def same(left: Optional[str], right: Optional[str]) -> bool:
    """
    One institution written two ways, with no doubt about it.

    Used where a match SUPPRESSES a finding, so it is deliberately strict:
    identical token sets after alias expansion, or identical distinctive tokens.
    "Bank of America" and "Bank of America, N.A." are the same; "First National
    Bank of Texas" and "First National Bank of Omaha" are not.
    """
    if not left or not right:
        return False
    if tokens(left) == tokens(right):
        return True
    mine, theirs = distinctive(left), distinctive(right)
    return bool(mine) and mine == theirs


def similar(left: Optional[str], right: Optional[str]) -> bool:
    """
    Probably the same institution, on the evidence available.

    Used only where a match DEMOTES a finding — never where it hides one. True
    when one name's distinctive words are all present in the other's, so
    "JPMorgan Chase Bank" matches "Chase", and where neither has a distinctive
    word, when one full token set contains the other.

    Still false for two different banks sharing their furniture: Texas and
    Omaha are each distinctive and neither contains the other.
    """
    if not left or not right:
        return False
    if same(left, right):
        return True

    mine, theirs = distinctive(left), distinctive(right)
    if mine and theirs:
        return mine <= theirs or theirs <= mine
    # One of them is furniture all the way down. The full names are all the
    # evidence there is, and a subset is as much agreement as can be had.
    whole_mine, whole_theirs = tokens(left), tokens(right)
    if not whole_mine or not whole_theirs:
        return False
    return whole_mine <= whole_theirs or whole_theirs <= whole_mine


def first_match(name: Optional[str], candidates: Iterable) -> Optional[object]:
    """
    The first candidate whose institution is the SAME as ``name``.

    :param candidates: Objects carrying an ``institution`` attribute, or plain
        strings.
    """
    for candidate in candidates:
        other = getattr(candidate, "institution", candidate)
        if same(name, other):
            return candidate
    return None


def first_similar(name: Optional[str], candidates: Iterable) -> Optional[object]:
    """The first candidate whose institution is merely SIMILAR. Demotion only."""
    for candidate in candidates:
        other = getattr(candidate, "institution", candidate)
        if similar(name, other):
            return candidate
    return None
