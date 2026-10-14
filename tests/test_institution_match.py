"""
tests/test_institution_match.py - Are these two spellings the same bank?

Asked four times by the undisclosed-accounts report, and the answer decides
whether something is reported as NOT PRODUCED. The two ways to be wrong are not
symmetric, and this suite is built around that:

  * **Too strict** reports an account the firm already holds statements for.
    Annoying, visible, and it teaches the reader to skim the report — which is
    the complaint this module answers.
  * **Too loose** hides a genuinely undisclosed account. Silent, and far worse.

So `same()` is strict and may suppress; `similar()` is generous and may only
DEMOTE — the row survives, says which account it probably is, and stays out of
the exhibit.

What it replaced was substring containment on a squashed name, which managed to
be wrong in both directions at once: it could not see that "BofA" is "Bank of
America", and it matched "CHASE" inside "PURCHASE".

Run:  venv/Scripts/python.exe tests/test_institution_match.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from util.institution_match import (  # noqa: E402
    distinctive, first_match, first_similar, same, similar, tokens,
)

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


print("1. the same bank, written as people write it")
for left, right in [
    ("Bank of America", "BANK OF AMERICA"),
    ("Bank of America", "Bank of America, N.A."),
    ("BofA", "Bank of America, N.A."),
    ("Chase", "JPMorgan Chase Bank"),
    ("Wells Fargo", "WELLS FARGO BANK NA"),
    ("Capital One", "CapOne"),
    ("Synchrony Bank", "Synchrony"),
    ("Navy Federal Credit Union", "Navy Federal"),
    ("American Express", "AMEX"),
    ("Citibank", "Citi"),
    ("Frost Bank", "FROST BANK"),
]:
    check("%s = %s" % (left, right), same(left, right), True)

print("")
print("2. DIFFERENT banks, which must never be merged")
for left, right in [
    # The case the distinctive-token rule exists for: four words in common.
    ("First National Bank of Texas", "First National Bank of Omaha"),
    ("Frost Bank", "Frontier Bank"),
    ("Chase", "Chevy Chase Trust"),
]:
    check("%s is not %s" % (left, right), same(left, right), False)
    check("  and not even close", similar(left, right), False)

# "Chevy Chase" is the case the alias split exists for: CHASE means JPMorgan
# Chase on its own and nothing of the sort inside somebody else's name.
check("a brand word inside another name is not that brand",
      similar("JPMorgan Chase", "Chevy Chase Trust"), False)

# ONE SHARED DISTINCTIVE WORD DEMOTES, AND THAT IS THE TRADE. "Capital Federal
# Savings" is not Capital One, and {CAPITAL} is a subset of {CAPITAL, ONE}, so
# it reads as near. The consequence is bounded on purpose: a demoted row stays
# on the screen with the account it might be, and only loses its place in the
# filed exhibit. Getting this wrong the other way -- suppressing -- would hide
# it outright, so the direction of the error is chosen rather than accepted.
check("a shared first word is near, not the same",
      (same("Capital One", "Capital Federal Savings"),
       similar("Capital One", "Capital Federal Savings")), (False, True))

print("")
print("3. the containment bug, pinned")
# "CHASE" is a substring of "PURCHASE", so the old test suppressed any creditor
# whose name contained the word — a produced Chase card quietly answering for
# somebody else's debt. Tokens cannot do this: a word is matched as a word.
check("a produced Chase card does not answer for PURCHASE POWER",
      similar("Chase", "Purchase Power"), False)
check("nor for a payee that merely contains the letters",
      similar("JPMorgan Chase", "PURCHASING SOLUTIONS LLC"), False)

print("")
print("4. near, but not certain — demote, never suppress")
for left, right in [
    ("UBS", "UBS AG Stamford Branch"),
    ("First National Bank", "First National Bank of Omaha"),
    ("U.S. Bank", "US Bank National Association"),
]:
    check("%s vs %s is similar" % (left, right), similar(left, right), True)
    check("  but not the same, so nothing is suppressed", same(left, right), False)

print("")
print("5. the furniture is not the name")
check("a legal suffix distinguishes nothing",
      distinctive("Wells Fargo Bank, N.A."), distinctive("Wells Fargo"))
check("neither does 'Credit Union'",
      distinctive("Navy Federal Credit Union"), frozenset({"NAVY"}))
check("a name that is ALL furniture has no distinctive word",
      distinctive("First National Bank"), frozenset({"FIRST"}))
check("single letters are dropped — they come from punctuation",
      "N" in tokens("Bank of America, N.A."), False)

print("")
print("6. nothing is not something")
check("no name on one side is never a match", same("Chase", None), False)
check("nor on the other", same(None, "Chase"), False)
check("and an empty string is not a wildcard", similar("", "Chase"), False)

print("")
print("7. finding the account a reference belongs to")


class Account:
    def __init__(self, institution):
        self.institution = institution


held = [Account("Bank of America, N.A."), Account("Frost Bank")]
check("an exact match is found", first_match("BofA", held).institution, "Bank of America, N.A.")
check("a near one is not, by first_match", first_match("UBS AG", held), None)
check("but is by first_similar",
      first_similar("Frost Bank Texas", held).institution, "Frost Bank")
check("and a different bank matches neither",
      first_similar("Frontier Bank", held), None)
check("a plain string list works too", first_match("AMEX", ["American Express"]), "American Express")

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
