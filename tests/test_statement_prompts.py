"""
tests/test_statement_prompts.py - The prompt and the code must agree.

The same fact was stated twice, in two languages, and drifted both times.

  * The metadata prompts listed the account types the model may answer with,
    in English. `crypto` shipped in 036 and was never added, so for two
    migrations it was not an answer the model could give. 038's
    `life_insurance` and `annuity` would have been the same.

  * The transactions prompt gated its cash-flow rule on the literal words
    "brokerage" or "retirement". Adding a type to `_CASH_FLOW_ACCOUNTS` moved
    the PYTHON checks onto the cash-flow path and left the MODEL on the bank
    rule — so a RiverSource policy statement was read with bank rules, checked
    with brokerage rules, and came back with **zero transactions** where the
    previous run had extracted four that tied to the cent.

Both lists are now built from the enums at import. This file asserts the
substitution happened and covers everything, so the next account type cannot
ship half-wired.

Run:  venv/Scripts/python.exe tests/test_statement_prompts.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from db.models.financial import AccountType  # noqa: E402
import services.statement_service as mod  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


PROMPTS = {
    "_STATEMENT_SYSTEM": mod._STATEMENT_SYSTEM,
    "_METADATA_SYSTEM": mod._METADATA_SYSTEM,
    "_TRANSACTIONS_SYSTEM": mod._TRANSACTIONS_SYSTEM,
}

print("Substitution actually happened")
# `<<<PAGE n>>>` markers are legitimate in these prompts, so the check names the
# sentinels rather than looking for angle brackets.
for name, text in PROMPTS.items():
    for token in ("<<ACCOUNT_TYPES>>", "<<CASH_FLOW_TYPES>>"):
        check("%s carries no %s" % (name, token), token in text, False)

print("")
print("Every account type is an answer the model can give")
for kind in AccountType:
    check_true('metadata prompt offers "%s"' % kind.value,
               '"%s"' % kind.value in mod._STATEMENT_SYSTEM
               and '"%s"' % kind.value in mod._METADATA_SYSTEM)

print("")
print("The cash-flow rule names exactly the types the code routes")
# THE ONE THAT BROKE. If these disagree, the model and the checks are reading
# the same statement under different rules.
for kind in AccountType:
    named = '"%s"' % kind.value in mod._CASH_FLOW_NAMES
    routed = kind in mod._CASH_FLOW_ACCOUNTS
    check("%-15s prompt=%-5s code=%s" % (kind.value, named, routed), named, routed)

print("")
print("A policy statement's summary is described as its register")
# Without this the model obeys "ACCOUNT SUMMARY is not transactions" -- correctly
# -- and extracts nothing, because a policy statement has no other activity
# block. That is exactly what happened.
for phrase in ("HAS NO REGISTER", "Premium payments", "NEVER extract a SUBTOTAL",
               "Beginning value and ending value are BALANCES"):
    check_true("the prompt says %r" % phrase, phrase in mod._TRANSACTIONS_SYSTEM)

print("")
print("The printed market-change line is recognised, and nothing else is")
for label in ("Fixed and variable account results", "Investment results",
              "Change in Market Value", "Unrealized gain for the period"):
    check_true("%r is market change" % label,
               mod._is_market_change({"description": label}))
# These are FLOWS. Classifying one as market change would drop it from the
# comparison against the statement's own summary -- the check that finds a
# withdrawal nobody extracted.
for label in ("Premium payments", "Monthly deductions", "Premium expense charges",
              "Cost of insurance", "Transfer to checking"):
    check("%r is NOT market change" % label,
          mod._is_market_change({"description": label}), False)


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all statement-prompt checks passed")
