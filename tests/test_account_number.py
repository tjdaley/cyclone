"""
tests/test_account_number.py - Finding the account number by pattern.

The cases below are built from the real digit runs on the Salmons Chase
statements — the ones query 5 pulled out of stored `raw_text`. The account
number is constant across every statement of an account; the barcode is
regenerated per mailing and the transaction references are unique per line.
That is the whole signal, and these tests are here to keep it working when
somebody meets a bank that prints something new.

The negative cases matter most. A wrong account number silently attaches a
statement to somebody else's account, which is worse than the null read this
exists to fix — so "cannot tell" must stay available as an answer.

Run:  venv/Scripts/python.exe tests/test_account_number.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.account_number_service import (  # noqa: E402
    detect, looks_like_a_number, reconcile,
)

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# ── The real document ────────────────────────────────────────────────────────
#
# Chase savings x5410, December 2024. The account number is orphaned fourteen
# lines above its own label, with the barcode between them.

CHASE_P1 = """<<<PAGE 1>>>
 000002746655410
CUSTOMER SERVICE INFORMATION
04850130101000000021
JPMorgan Chase Bank, N.A.
P O Box 182051
Columbus, OH 43218 - 2051
November 09, 2024 through December 09, 2024
Account Number:
Chase.com
1-800-935-9935
KIMBERLY A HARRISON
1021 SIR LANCELOT CIR
LEWISVILLE TX 75056-5760
11/15 Online Transfer From Chk ...4448 Transaction#: 22385628138 2,636.31
11/20 Online Transfer To Chk ...4448 Transaction#: 22780733026 -1,200.00 1,436.31
12/09 Interest Payment 1,436.32
KH000880"""

CHASE_P2 = """<<<PAGE 2>>>
 000002746655410
Page of 2 2
November 09, 2024 through December 09, 2024
Account Number:
Call us at 1-866-564-2262 or write us at the address on the front
JPMorgan Chase Bank, N.A. Member FDIC
KH000881"""

print("\nThe statement that started this")

found = detect({1: CHASE_P1, 2: CHASE_P2})
check("found the account number", found.number, "000002746655410")
check("last four", found.last4, "5410")
check("across both pages", found.pages, 2)
check("nothing else spans the document", found.rivals, ())
check("unambiguous", found.unambiguous, True)

print("\nWhat it had to reject")
runs_on_page_1 = {
    "04850130101000000021": "the barcode — regenerated per mailing, page 1 only",
    "22385628138": "a transaction reference — unique to its line",
    "22780733026": "another transaction reference",
}
for run, why in runs_on_page_1.items():
    check("rejected %s (%s)" % (run, why), run in (found.number,) + found.rivals, False)

check("a phone number never forms a long run", "18009359935" in CHASE_P1, False)
check("the page marker did not contribute its digits",
      any(r.startswith("1") and len(r) < 9 for r in (found.number,) + found.rivals), False)


print("\nEvery month of x5410 agrees, and every barcode differs")

# The barcodes query 5 returned, month by month. The account number is the
# constant; if the detector keyed on anything else it would drift monthly.
BARCODES = [
    "04593790101000000021", "04626560101000000021", "04648750101000000021",
    "04671930101000000021", "04714250101000000021", "04758000201000000022",
    "04809290101000000021", "04824370101000000021", "04850130101000000021",
]
for barcode in BARCODES:
    page1 = " 000002746655410\n%s\nAccount Number:\n" % barcode
    page2 = " 000002746655410\nPage of 2 2\n"
    found = detect({1: page1, 2: page2})
    check("%s -> 5410" % barcode, (found.number, found.last4),
          ("000002746655410", "5410"))


print("\nThe checking account, from the same production")

# x4448: 000001822714448. Its statements also carry check numbers, which are
# long zero-padded runs and appear once each.
p1 = """ 000001822714448
00002340201000000022
CHECK 0000005617 -250.00
CHECK 0000005619 -75.00
Transaction#: 20124495544
"""
p2 = """ 000001822714448
10002340202000000062
Transaction#: 20135713299
"""
found = detect({1: p1, 2: p2})
check("found it", found.number, "000001822714448")
check("last four", found.last4, "4448")
check("a check number did not win", "0000005617" in (found.number,) + found.rivals, False)
check("the second barcode did not win", found.rivals, ())


print("\nPages that carry no account number must not veto the answer")

# This is the bug the first version shipped with. Requiring the number on EVERY
# page meant a single cover sheet silenced the detector — and a cover sheet is
# exactly what a brokerage package opens with, often followed by a letter about
# a change of terms before the first substantive page.
brokerage = {
    1: "IMPORTANT INFORMATION ABOUT YOUR ACCOUNT\nEnclosed is your quarterly statement.",
    2: "A CHANGE TO OUR TERMS OF SERVICE\nEffective January 1 we are updating the agreement.",
    3: " 000002746655410\nACCOUNT SUMMARY\nBeginning Balance 2,136.31",
    4: " 000002746655410\nTRANSACTION DETAIL\n11/15 Purchase 500.00",
    5: " 000002746655410\nPage 5\nDISCLOSURES",
}
found = detect(brokerage)
check("found it despite two pages without it", found.number, "000002746655410")
check("last four", found.last4, "5410")
check("counted the pages it was on, not the pages in the document", found.pages, 3)
check("unambiguous", found.unambiguous, True)

# A marketing insert in the middle is the same problem from another angle.
interleaved = {
    1: " 000001822714448\npage one",
    2: "SAVE ON YOUR NEXT MORTGAGE - no account number here",
    3: " 000001822714448\npage three",
}
check("an insert in the middle does not veto it", detect(interleaved).last4, "4448")

print("\nTwo appearances is the floor")

check("a number on one page of three is not a pattern",
      detect({1: " 000002746655410 x", 2: "nothing", 3: "nothing either"}), None)
check("two of three is enough",
      detect({1: " 000002746655410 x", 2: " 000002746655410 y", 3: "nothing"}).last4, "5410")

print("\nThe most-repeated run wins over a longer one")

# A barcode appears once per statement; the account number appears per page. The
# barcode is the LONGER run, so length must not be the tiebreak.
pages = {
    1: " 000002746655410\n04850130101000000021",
    2: " 000002746655410\nPage 2",
    3: " 000002746655410\nPage 3",
}
check("the barcode did not win on length", detect(pages).number, "000002746655410")


print("\nA run too long to be an account number is excluded, not trimmed")

# The length cap read as protection while providing none: without boundary
# assertions the pattern matched the first 19 digits OF the 20-digit barcode and
# offered the truncation as a candidate. It lost here only because it appears on
# one page — on a statement where the barcode repeated, it would have won.
barcode_pages = {
    1: " 000002746655410\n04319560101000000021\nAccount Number:",
    2: " 000002746655410\n04319560101000000021\nPage 2 of 2",
}
found = detect(barcode_pages)
check("the account number wins even when the barcode repeats too",
      found.number, "000002746655410")
check("the truncated barcode is not even a rival", found.rivals, ())
check("nothing derived from the 20-digit run survives",
      any("0431956" in candidate for candidate in (found.number,) + found.rivals), False)


print("\nWhen it must refuse to answer")

check("a single page proves nothing about repetition",
      detect({1: CHASE_P1}), None)
check("no run appears twice",
      detect({1: "12345678901 alpha", 2: "98765432109 beta"}), None)
check("empty pages", detect({1: "", 2: ""}), None)
check("no pages at all", detect({}), None)

# A document holding two statements has no run on every page — which is the
# safe outcome, not a missed one: it falls back to whatever was extracted.
two_statements = {
    1: " 000002746655410\nsavings page\n",
    2: " 000002746655410\nsavings page 2\n",
    3: " 000001822714448\nchecking page\n",
    4: " 000001822714448\nchecking page 2\n",
}
# Two accounts, each on two pages, tied on count. The detector reports the tie
# rather than picking — and `reconcile` then keeps whatever was extracted, so an
# ambiguous document can never be filed against the wrong account.
tied = detect(two_statements)
check("a two-statement document reports a tie rather than guessing",
      tied.unambiguous, False)
check("naming both candidates", sorted((tied.number,) + tied.rivals),
      ["000001822714448", "000002746655410"])
_, tie_flag = reconcile("4448", tied)
check("and reconcile keeps the extracted value", tie_flag["code"], "ACCOUNT_NUMBER_AMBIGUOUS")

print("\n...but a page span rescues the two-statement case")
found = detect(two_statements, only=[1, 2])
check("savings, by span", found.last4, "5410")
found = detect(two_statements, only=[3, 4])
check("checking, by span", found.last4, "4448")


print("\nTwo numbers on every page")

both = {1: "111111112222 999999998888 x", 2: "111111112222 999999998888 y"}
found = detect(both)
check("reported as ambiguous", found.unambiguous, False)
check("both are named", sorted((found.number,) + found.rivals),
      ["111111112222", "999999998888"])

found = detect(both, expected_last4="8888")
check("the model's reading breaks the tie", found.number, "999999998888")
check("and the loser is still named", found.rivals, ("111111112222",))

found = detect(both, expected_last4="7777")
check("a last4 matching neither does not force a choice", found.unambiguous, False)


# ── Settling model against pattern ───────────────────────────────────────────

print("\nreconcile()")

found = detect({1: CHASE_P1, 2: CHASE_P2})

last4, flag = reconcile(None, found)
check("a null read is filled from the pattern", last4, "5410")
check("and flagged", flag["code"], "ACCOUNT_NUMBER_DERIVED")
check("as info, not a problem", flag["severity"], "info")

last4, flag = reconcile("5410", found)
check("agreement needs no flag", (last4, flag), ("5410", None))

last4, flag = reconcile("4448", found)
check("disagreement keeps the extracted value", last4, "4448")
check("and warns", (flag["code"], flag["severity"]),
      ("ACCOUNT_NUMBER_CONFLICT", "warn"))
check("naming both", "000002746655410" in flag["note"], True)

last4, flag = reconcile("5410", None)
check("no pattern, no change", (last4, flag), ("5410", None))
last4, flag = reconcile(None, None)
check("nothing anywhere", (last4, flag), (None, None))

ambiguous = detect({1: "111111112222 999999998888 x", 2: "111111112222 999999998888 y"})
last4, flag = reconcile(None, ambiguous)
check("ambiguous and nothing extracted is a warning", flag["severity"], "warn")
check("and does not invent a number", last4, None)
last4, flag = reconcile("2222", ambiguous)
check("ambiguous but extracted keeps the extracted", last4, "2222")
check("as info", flag["severity"], "info")


# ── The narrow question ──────────────────────────────────────────────────────

print("\nask(): one question, and the answer is checked against the page")

import services.account_number_service as mod  # noqa: E402


class FakeLLM:
    """Stands in for llm_service, recording what it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, str, str]] = []

    def complete(self, system, message, profile=None, **kwargs):
        self.calls.append((system, message, profile))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def with_llm(reply, pages, only=None):
    fake = FakeLLM(reply)
    import services.llm_service as llm_module
    original = llm_module.llm_service
    llm_module.llm_service = fake
    try:
        return mod.ask(pages, only=only), fake
    finally:
        llm_module.llm_service = original


PAGES = {1: CHASE_P1, 2: CHASE_P2}

found, fake = with_llm(
    '{"account_number": "000002746655410", "found_near": "Account Number:", "confidence": "high"}',
    PAGES)
check("the number comes back", found.number, "000002746655410")
check("last four", found.last4, "5410")
check("recorded as a lookup, not a pattern hit", found.source, "lookup")
check("asked the narrow profile", fake.calls[0][2], "find_account_number")
check_true("was shown the page text", "000002746655410" in fake.calls[0][1])

found, _ = with_llm(
    '```json\n{"account_number": "000002746655410", "found_near": null, "confidence": "high"}\n```',
    PAGES)
check("markdown fences are stripped", found.last4, "5410")

found, _ = with_llm('{"account_number": "0000 0274 6655 410", "confidence": "high"}', PAGES)
check("a grouped answer still matches the printed run", found.last4, "5410")

print("\nThe barcode trap")

# What actually happened on twelve Chase statements. The lookup was shown ONE
# page — because a statement's transaction pages are not its extent, and every
# Chase transaction prints on page 1 — and that page carries both the account
# number and a 20-digit mail barcode. It chose the barcode every time.
BARCODE = "04850130101000000021"
check("the barcode is longer than any real account number", len(BARCODE), 20)

found, _ = with_llm('{"account_number": "%s", "confidence": "high"}' % BARCODE, PAGES)
check("too long to be an account number, so it is refused", found, None)

# The barcode changes on every statement, which is the other half of the tell.
for other in ("04593790101000000021", "04626560101000000021", "04319560101000000021"):
    found, _ = with_llm('{"account_number": "%s"}' % other, PAGES)
    check("refused: %s" % other, found, None)

print("\nA number seen on one page loses to one seen on more")

# The behavioural guard, for a decoy short enough to pass the length test.
# "Printed on the page" cannot tell an account number from a barcode; how the
# number behaves across pages can.
decoy_pages = {
    1: " 000002746655410\n999888777666\nAccount Number:",
    2: " 000002746655410\nPage 2 of 2",
}
found, _ = with_llm('{"account_number": "999888777666"}', decoy_pages)
check("a one-page number is refused when another repeats", found, None)
found, _ = with_llm('{"account_number": "000002746655410"}', decoy_pages)
check("the repeating one is accepted", found.last4, "5410")

# With only one page there is nothing to compare against, so the guard stands
# down rather than refusing everything.
found, _ = with_llm('{"account_number": "999888777666"}', {1: decoy_pages[1]})
check("on a single page the guard does not fire", found.last4, "7666")


print("\nAn answer that is not on the page is discarded")

# The whole reason a fast model is safe here: it is being used to LOCATE a
# value, never to produce one.
found, _ = with_llm('{"account_number": "999999999999", "confidence": "high"}', PAGES)
check("an invented number is refused", found, None)

# The subtle one. Concatenating every digit on the page into one string would
# let a "match" straddle two unrelated numbers and confirm something nobody
# printed, so each printed run is compared on its own.
found, _ = with_llm('{"account_number": "5410048501", "confidence": "high"}', PAGES)
check("a match straddling two numbers is refused", found, None)

print("\nAnd everything else that can go wrong")

for label, reply in (
    ("null answer", '{"account_number": null, "confidence": "low"}'),
    ("empty string", '{"account_number": "", "confidence": "low"}'),
    ("too few digits", '{"account_number": "410", "confidence": "low"}'),
    ("not JSON at all", "I could not find an account number."),
    ("JSON of the wrong shape", '{"answer": "5410"}'),
):
    found, _ = with_llm(reply, PAGES)
    check(label, found, None)

found, _ = with_llm(RuntimeError("every vendor is down"), PAGES)
check("a vendor outage is a null, not an exception", found, None)

found, fake = with_llm('{"account_number": "000002746655410"}',
                       {n: "page %d\n 000002746655410" % n for n in range(1, 9)})
check("never shows more than the opening pages", fake.calls[0][1].count("<<<PAGE"), 3)


# ── The label-as-value guard ─────────────────────────────────────────────────

print("\nA masked value has to contain a number")

check("the label the model scraped is rejected",
      looks_like_a_number("Account Number:"), False)
check("empty", looks_like_a_number(""), False)
check("null", looks_like_a_number(None), False)
check("a real masked form", looks_like_a_number("ending in 4357"), True)
check("the full number Chase prints", looks_like_a_number("000002746655410"), True)
check("X-masked", looks_like_a_number("XXXX-1234"), True)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all account-number checks passed")
