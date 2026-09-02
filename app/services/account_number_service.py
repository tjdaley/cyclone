"""
app/services/account_number_service.py - Reading an account number off a page.

An account number is a pattern before it is a comprehension task: it is the long
digit run printed on **more pages than anything else** in a statement. Nothing
else on a bank statement behaves that way — a barcode is regenerated per
mailing, a transaction reference is unique to its line, a check number appears
once, a routing line prints on the first sheet only.

Note "most pages", not "every page". Every page was the first rule here and it
was wrong: statements open with pages that carry no account number at all — a
brokerage package leads with a cover sheet and often a letter about a change of
terms, and disclosures or inserts turn up anywhere. One such page vetoed the
real answer and the detector went silent on the very documents it was built for.

When repetition cannot settle it, `ask()` puts the single question to a fast
model over the first few pages, and **verifies the answer against the printed
text** before accepting it. The model locates a value; it never produces one.

The failure this exists to end, measured on twelve months of Chase statements:

    Chase x4448   9 of 12 read the number, 3 returned null
    Chase x5410   3 of 12 read the number, 9 returned null

Same model (claude-opus-5), no failover on any of the 24, same prompt. Chase
prints the account number in a text object that extracts *fourteen lines above*
its own "Account Number:" label, past a barcode and a mail-routing line, so a
model reading the text layer sees an empty label — two of those runs returned
the literal string "Account Number:" as the masked value, which is the model
landing on the label and finding nothing after it. Sometimes it scans up and
finds the orphan; sometimes it does not.

**A coin flip on the dedup key is worse than a clean failure.** Institution plus
last four is how a statement finds its account, so nine null reads opened nine
separate accounts for one savings account — one per month, each holding a single
statement, invisible as a problem until somebody asks what the balance history
looks like.
"""
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# Shortest run treated as a possible account number. Long enough to exclude a
# ZIP code, a year, a dollar figure with the separators stripped, and a phone
# number — which survives as short fragments anyway, since the hyphens in
# "1-800-935-9935" break it into 1/800/935/9935.
_MIN_DIGITS = 8

# Longest. Beyond this it is a barcode or a machine-readable mail string.
_MAX_DIGITS = 19

# The lookarounds make the bounds mean what they say. Without them the pattern
# matches the first 19 digits OF a 20-digit barcode and offers the truncation as
# a candidate — the length cap reads as protection while providing none. A run
# must be a whole run: too long is excluded, not trimmed to fit.
_DIGIT_RUN = re.compile(r"(?<!\d)\d{%d,%d}(?!\d)" % (_MIN_DIGITS, _MAX_DIGITS))

# Page markers pdf_service writes. Stripped before scanning, or "<<<PAGE 12>>>"
# contributes its own digits.
_PAGE_MARKER = re.compile(r"<<<PAGE\s+\d+>>>")


@dataclass(frozen=True)
class AccountNumber:
    """A number found by pattern, with what was found alongside it."""
    number: str
    last4: str
    #: Pages the number was seen on (pattern), or pages examined (lookup).
    pages: int
    #: Other runs tied on page count. Empty is the clean case; anything here
    #: means the document could not decide and the caller must not pick for it.
    rivals: tuple[str, ...] = ()
    #: "pattern" — repetition across pages, exact and free. "lookup" — a narrow
    #: question to a fast model, verified against the printed text. The two
    #: warrant different notes in the record, so the distinction is kept.
    source: str = "pattern"

    @property
    def unambiguous(self) -> bool:
        return not self.rivals


def _runs(text: str) -> set[str]:
    """Distinct digit runs on one page, with page markers removed first."""
    return set(_DIGIT_RUN.findall(_PAGE_MARKER.sub(" ", text or "")))


def detect(
    pages: dict[int, str],
    only: Optional[list[int]] = None,
    expected_last4: Optional[str] = None,
) -> Optional[AccountNumber]:
    """
    Find the account number printed on every page of a statement.

    :param pages: Page number to page text, as ``bates_service.split_pages``
        returns.
    :param only: Restrict to one statement's pages. Omit to use the whole
        document — correct when the document holds one statement, and safely
        useless when it holds several, since no run is then on every page and
        this returns None rather than guessing.
    :param expected_last4: What the model reported, when it reported anything.
        Used **only** to break a tie between rivals, never to override a clean
        single candidate: a tiebreak is the one place the model's reading adds
        information the pattern cannot supply.
    :return: The number, or None when the pages do not agree on one.
    :rtype: Optional[AccountNumber]
    """
    numbers = sorted(only) if only else sorted(pages)
    numbers = [n for n in numbers if pages.get(n)]

    # One page cannot demonstrate repetition. Returning a lone page's longest
    # digit run would be a guess dressed up as a pattern.
    if len(numbers) < 2:
        return None

    seen: Counter = Counter()
    for number in numbers:
        for run in _runs(pages[number]):
            seen[run] += 1

    # The most-repeated run wins — NOT one printed on every page. Requiring
    # every page was the first rule here and it was wrong: a statement opens
    # with pages that carry no account number at all. A brokerage package leads
    # with a cover sheet and often a letter about a change of terms before the
    # first substantive page, and a disclosure or marketing insert can appear
    # anywhere. One such page vetoed the real answer and the whole detector
    # went quiet.
    best = max(seen.values(), default=0)
    if best < 2:
        return None

    leaders = sorted(run for run, count in seen.items() if count == best)

    if len(leaders) == 1:
        chosen, rivals = leaders[0], ()
    else:
        # Several runs tie on page count. Prefer the one whose last four match
        # what the model read; without that there is nothing to choose on, and
        # the honest answer is to report the ambiguity.
        matching = [r for r in leaders if expected_last4 and r.endswith(expected_last4)]
        if len(matching) == 1:
            chosen = matching[0]
            rivals = tuple(r for r in leaders if r != chosen)
        else:
            chosen = leaders[0]
            rivals = tuple(leaders[1:])

    return AccountNumber(
        number=chosen, last4=chosen[-4:], pages=best, rivals=rivals,
    )


# How many pages to show the narrow question. Three, because a brokerage
# package opens with a cover sheet and often a letter about a change of terms
# before the first substantive page — asking about page one alone would be
# asking about the envelope.
_ASK_PAGES = 3

# Enough to carry three pages of a statement, short enough that the question
# stays the only thing in the context.
_ASK_CHARS = 12_000

_ASK_SYSTEM = """\
You are reading the first pages of a bank, brokerage, or credit-card statement.
Answer ONE question: what is the account number of the account this statement is for?

The text was extracted from a PDF and the layout is often scrambled — a value
can appear far above or below its own label, and "Account Number:" may look
empty because its value was extracted somewhere else on the page. Look for a
number anywhere on the page, not only beside the label.

These are NOT account numbers, and each is a common trap:
  - A barcode or machine-readable mail string, usually 18-20 digits, often
    ending in a lone digit or two. It changes on every statement.
  - The bulk-mail routing line near the address block, e.g.
    "00485013 DRE 201 219 34524 NNNNNNNNNNN 1 000000000 06 0000".
  - A transaction, confirmation, or reference number sitting on a single entry.
  - A check number, a routing/ABA number, a phone number, a ZIP code.
  - The number of a DIFFERENT account named in a transfer description
    ("Online Transfer From Chk ...4448"). You want the account the statement
    itself is FOR — the one whose balance is being reported.

Return ONLY a valid JSON object:

{"account_number": "<digits exactly as printed, or null>",
 "found_near": "<the label or words you saw it next to, or null>",
 "confidence": "high" | "low"}

Copy the digits EXACTLY as printed, including leading zeros. Do not reformat,
group, or shorten. If you cannot find it, return null — a null is corrected in
seconds, a wrong number silently files this statement against another account.

Respond ONLY with the JSON object. No markdown fences, no explanation.\
"""


def ask(pages: dict[int, str], only: Optional[list[int]] = None) -> Optional[AccountNumber]:
    """
    Ask a fast model the one question, over the first few pages.

    The full extraction prompt asks for twenty fields at once and the account
    number is one line of it; on some bank forms it comes back null about half
    the time. A prompt that asks only this, over only the pages that could hold
    the answer, is a different proposition — attention is the scarce resource
    and this spends all of it on one field.

    **The answer is verified verbatim against the text before it is accepted.**
    That is what makes it safe to use a fast model here: a number that is not a
    substring of the pages we showed it was invented, and is discarded. The
    model is being used to locate a value, never to produce one.

    :param pages: Page number to page text.
    :param only: Restrict to one statement's pages.
    :return: The number, or None when nothing survived verification.
    :rtype: Optional[AccountNumber]
    """
    import json

    from services.llm_service import llm_service

    numbers = sorted(only) if only else sorted(pages)
    numbers = [n for n in numbers if pages.get(n)][:_ASK_PAGES]
    if not numbers:
        return None

    body = "\n\n".join("<<<PAGE %d>>>\n%s" % (n, pages[n]) for n in numbers)[:_ASK_CHARS]

    try:
        answer = llm_service.complete(_ASK_SYSTEM, body, profile="find_account_number")
    except Exception as e:  # noqa: BLE001 — a failed lookup is a null, not an error
        LOGGER.warning("account_number_service.ask: lookup failed: %s", str(e))
        return None

    text = (answer or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        parsed = json.loads(text.strip())
    except (ValueError, TypeError):
        LOGGER.warning("account_number_service.ask: response was not JSON")
        return None

    reported = "".join(c for c in str(parsed.get("account_number") or "") if c.isdigit())
    if len(reported) < 4:
        return None

    # Length. `detect` has always been bounded at _MAX_DIGITS and that is the
    # only reason it never picked one of these barcodes — a Chase mail barcode
    # is 20 digits where the account number is 15. The lookup inherited none of
    # that and chose the barcode on twelve statements running.
    if len(reported) > _MAX_DIGITS:
        LOGGER.warning(
            "account_number_service.ask: a %d-digit answer is too long to be an account number "
            "— discarded", len(reported),
        )
        return None

    # The verification. Each printed run is compared on its own rather than
    # against the page's digits concatenated together: run everything into one
    # string and a "match" can straddle the boundary between two unrelated
    # numbers, which would confirm a number nobody printed.
    printed = {
        re.sub(r"\D", "", run)
        for run in re.findall(r"\d[\d\s–—.-]{2,}\d", body)
    }
    if not any(reported == run or reported in run for run in printed):
        LOGGER.warning(
            "account_number_service.ask: a %d-digit answer is not printed on the pages supplied "
            "— discarded", len(reported),
        )
        return None

    # How it behaves across pages. "Printed on the page" was too weak a test on
    # its own, because a barcode is printed on the page too. An account number
    # repeats; a barcode is regenerated per mailing and appears once. So when
    # more than one page was examined, an answer seen on a single page loses to
    # any run seen on more.
    if len(numbers) > 1:
        appearances = Counter()
        for page in numbers:
            for run in _runs(pages[page]):
                appearances[run] += 1
        mine = max((count for run, count in appearances.items() if reported in run), default=0)
        best = max(appearances.values(), default=0)
        if mine <= 1 and best > 1:
            LOGGER.warning(
                "account_number_service.ask: answer appears on %d page(s) but another number "
                "appears on %d — discarded as not an account number", mine, best,
            )
            return None

    LOGGER.info("account_number_service.ask: found a %d-digit number near %r",
                len(reported), str(parsed.get("found_near") or "")[:40])
    return AccountNumber(
        number=reported, last4=reported[-4:], pages=len(numbers), source="lookup",
    )


def reconcile(
    reported_last4: Optional[str],
    found: Optional[AccountNumber],
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """
    Settle the model's answer against the pattern's.

    Deliberately **not** the Bates rule. There the pattern owns the field
    outright, because a Bates stamp is one token in one place and a model asked
    for it on an unstamped page will invent a plausible one. An account number
    is different: it is printed in prose as well as in the header ("your Chase
    Savings account ...5410"), so a model answer that disagrees with the pattern
    is evidence of a real conflict rather than a hallucination — and picking a
    side silently is how a wrong number comes to look settled.

    :return: ``(last4, flag)`` — the number to use, and a flag to record, or
        None when there is nothing worth saying.
    :rtype: tuple[Optional[str], Optional[dict[str, Any]]]
    """
    reported = (reported_last4 or "").strip() or None

    if found is None:
        return reported, None

    if not found.unambiguous:
        # Several runs span every page. Say so, keep whatever the model gave.
        return reported, {
            "code": "ACCOUNT_NUMBER_AMBIGUOUS",
            "severity": "info" if reported else "warn",
            "field_path": "account.account_number_last4",
            "note": "Several numbers are printed on as many pages as each other (%s). %s"
                    % (", ".join((found.number,) + found.rivals),
                       "Kept the extracted %s." % reported if reported
                       else "No account number was extracted, so this statement could not be "
                            "matched to an account."),
        }

    if reported is None:
        how = (
            "%s — the number printed on the most pages (%d of them)." % (found.number, found.pages)
            if found.source == "pattern"
            else "%s — found by re-reading the first %d page(s) and asking only for the account "
                 "number, then checking the answer against the printed text."
                 % (found.number, found.pages)
        )
        return found.last4, {
            "code": "ACCOUNT_NUMBER_DERIVED",
            "severity": "info",
            "field_path": "account.account_number_last4",
            "note": "The account number was not extracted from the statement text, so it was taken "
                    "from %s" % how,
        }

    if reported == found.last4:
        return reported, None

    return reported, {
        "code": "ACCOUNT_NUMBER_CONFLICT",
        "severity": "warn",
        "field_path": "account.account_number_last4",
        "note": "The extracted account number ends %s, but %s appears on more pages (%d) than "
                "anything else. Kept %s. Check the statement before relying on either."
                % (reported, found.number, found.pages, reported),
    }


def looks_like_a_number(masked: Optional[str]) -> bool:
    """
    Is a masked account number a number at all?

    Two of the Chase runs stored the literal string ``"Account Number:"`` here —
    the model reached the label, found the value missing, and returned the label.
    A masked form always carries digits ("ending in 4357", "XXXX-1234"); one with
    none is a caption that got scraped, and storing it makes a wrong answer look
    like a recorded fact.
    """
    return bool(masked and any(c.isdigit() for c in masked))
