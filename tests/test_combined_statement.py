"""
tests/test_combined_statement.py - Several accounts, one document, no page breaks.

Capital One 360 prints a combined statement: every account the customer holds,
one register straight after the next. A page therefore ends one account and
begins another, and on the real eight-page document behind this suite page 5
carried the close of one register, the whole of a second, and the opening of a
third.

The extraction divided by the page, so the pass reading account A was handed a
page ending with account B's opening balance under a context line saying "these
pages belong to" A. It believed it, and filed B's first transactions against A —
on every account after the first. The fix divides at the printed header instead,
found literally in Python, the same argument as Bates numbers and account
numbers.

Every string below is copied from that statement.

Run:  venv/Scripts/python.exe tests/test_combined_statement.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services import bates_service  # noqa: E402
from services.statement_service import StatementService, _squash_institution  # noqa: E402

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


# ── The document ─────────────────────────────────────────────────────────────
#
# Trimmed to the seams, which is where the whole failure lived. Page 3 closes
# Main Checking and opens Amandas Checking; page 5 closes Amandas Checking,
# holds the entirety of KLH Trust Checking, and opens Amandas Savings.

PAGES = {
    1: "Capital One 360\nAccount Summary\nMain Checking Account $1,497.47 $396.36",
    2: ("Main Checking Account - 36066233009\n"
        "360 CHECKING | JOINT WITH BENJAMIN J RUSHING\n"
        "Mar 1 Opening Balance $1,497.47\n"
        "Mar 2 Withdrawal from APPLECARD GSBANK PAYMENT Debit - $25.00 $1,472.47"),
    3: ("Mar 31 Withdrawal from TARGET CARD SRVC PAYMENT Debit - $403.68 $396.17\n"
        "Mar 31 Monthly Interest Paid Credit + $0.19 $396.36\n"
        "Mar 31 Closing Balance $396.36\n"
        "Amandas Checking Account - 36325262322\n"
        "360 CHECKING\n"
        "Mar 1 Opening Balance $710.53\n"
        "Mar 1 Electricity - Deposit from Amandas Savings Account\n"
        "XXXXXXX3039 Credit + $151.00 $861.53"),
    4: "Mar 3 Citibank1612 - Deposit from Amandas Savings Account\nXXXXXXX3039 Credit + $430.43",
    5: ("Mar 31 Closing Balance $1,836.91\n"
        "KLH Trust Checking - 36334271933\n"
        "360 CHECKING\n"
        "Mar 1 Opening Balance $100.70\n"
        "Mar 31 Monthly Interest Paid Credit + $0.01 $100.71\n"
        "Mar 31 Closing Balance $100.71\n"
        "Amandas Savings Account - 36325263039\n"
        "360 PERFORMANCE SAVINGS\n"
        "Mar 1 Opening Balance $2,595.98\n"
        "Mar 1 Electricity - Withdrawal to Amandas Checking Account\n"
        "XXXXXXX2322 Debit - $151.00 $2,444.98"),
    6: ("Mar 31 Closing Balance $2,424.29\n"
        "KLH Trust for DLP Jr. - 36334271139\n"
        "360 PERFORMANCE SAVINGS\n"
        "Mar 1 Opening Balance $145,135.55"),
    7: "Mar 31 Closing Balance $145,528.58",
    8: "Note: The last four digits of your external accounts may not match.",
}
RAW = "\n\n".join("<<<PAGE %d>>>\n%s" % (n, PAGES[n]) for n in sorted(PAGES))
PARSED = bates_service.split_pages(RAW)

INDEXED = [
    {"header_text": "Main Checking Account - 36066233009"},
    {"header_text": "Amandas Checking Account - 36325262322"},
    {"header_text": "KLH Trust Checking - 36334271933"},
    {"header_text": "Amandas Savings Account - 36325263039"},
    {"header_text": "KLH Trust for DLP Jr. - 36334271139"},
]


def own(index):
    return StatementService._statement_pages(RAW, PARSED, INDEXED, index)


print("Cutting each account out at its printed header")

check("five accounts on eight pages", len(PARSED), 8)

# The seams. Three statements touch page 5; two touch page 3.
check("Main Checking runs to the page it closes on", sorted(own(0)), [2, 3])
check("Amandas Checking starts on that same page", sorted(own(1)), [3, 4, 5])
check("KLH Trust Checking fits inside one shared page", sorted(own(2)), [5])
check("Amandas Savings opens on it too", sorted(own(3)), [5, 6])
check("the last account runs to the end", sorted(own(4)), [6, 7, 8])

print("\nNo account's text reaches another's pass")

for i, entry in enumerate(INDEXED):
    text = "\n".join(own(i)[n] for n in sorted(own(i)))
    strays = [o["header_text"] for j, o in enumerate(INDEXED) if j != i and o["header_text"] in text]
    check("%s sees only itself" % entry["header_text"].split(" - ")[0], strays, [])

# The specific line that was mis-filed: Amandas Checking's first transaction,
# printed at the foot of page 3 under Main Checking's register.
main = "\n".join(own(0)[n] for n in sorted(own(0)))
check("Main Checking stops at its own closing balance",
      "Electricity - Deposit from Amandas Savings Account" in main, False)
check("and keeps its own last line", "Monthly Interest Paid Credit + $0.19" in main, True)

amandas = "\n".join(own(1)[n] for n in sorted(own(1)))
check("the mis-filed line lands on the account that printed it",
      "Electricity - Deposit from Amandas Savings Account" in amandas, True)

print("\nA statement beginning mid-page still knows its page number")

# It starts partway down page 3, so its text carries no marker of its own. Left
# alone, everything up to the next marker would be attributed to page 4 — off by
# one, exactly at the seam.
check("page 3, not page 4", min(own(1)), 3)
check("its opening line is on page 3", "Opening Balance $710.53" in own(1)[3], True)

print("\nWhat happens without a usable header")

check("a header the index did not report", own_missing := StatementService._statement_pages(
    RAW, PARSED, [{"header_text": ""}], 0), None)
check("a header that is not printed in the document", StatementService._statement_pages(
    RAW, PARSED, [{"header_text": "Some Account - 99999999"}], 0), None)

print("\nTelling a combined statement's seam from a phantom register")

# The seam test at commit time is one shared page between two different accounts
# at the SAME institution. "Unknown institution" is the absence of an answer, so
# two statements that both failed to read the letterhead have not agreed — which
# is what keeps the split guard firing on the case it was built for.
check("a real bank name squashes to something", _squash_institution("Capital One 360"), "CAPITALONE360")
check("two spellings of one bank agree",
      _squash_institution("Capital One") == _squash_institution("capital-one"), True)
check("an unread letterhead is not an answer", _squash_institution("Unknown institution"), "")
check("nor is a missing one", _squash_institution(None), "")

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all combined-statement checks passed")
