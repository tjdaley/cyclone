"""
Checks are transactions, and a check listed twice is one payment.

From ground truth on First Financial x9260, 11/28/23-12/26/23. Every amount in
the debit sections tied, but the statement printed "262 Checks/Debits" and only
249 were extracted. The missing thirteen were twelve checks in a table at the
back headed CHECKS IN SERIAL NUMBER ORDER — plus one ordinary line the model
skipped mid-page.

The prompt was the cause. It said a check "already appears as a debit; this
table only lists them again by number", which is a convention some banks follow
and this one does not: for eleven of the fourteen rows, that table is the ONLY
place the check appears.

The bank marks the exceptions itself. Checks 2487 and 2499 print "-See above-"
where the amount goes, and both do appear in the debit list. So the rule needs
no knowledge of the phrasing: a row whose amount is not a printed figure is
already itemised elsewhere.
"""
import sys
from decimal import Decimal

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

import services.statement_service as mod  # noqa: E402
from services.statement_service import _check_number, _dedupe_checks, statement_service  # noqa: E402

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


# The table as printed, read across its two column groups.
CHECKS_TABLE = [
    ("11/28", "2487*", "-See above-"), ("12/11", "2495", "2625.00"),
    ("11/28", "2488", "10000.00"),     ("12/18", "2496", "1500.00"),
    ("11/28", "2489", "660.80"),       ("12/13", "2497", "733.70"),
    ("11/29", "2490", "487.50"),       ("12/13", "2498", "477.50"),
    ("11/29", "2491", "699.23"),       ("12/13", "2499", "-See above-"),
    ("12/05", "2493*", "150.00"),      ("12/22", "2500", "150.00"),
    ("12/05", "2494", "1300.00"),      ("12/19", "",     "6885.66"),
]


# ── 1. The asterisk is a footnote, not part of the number ────────────────
print("1. check numbers as printed")
check("asterisk stripped", _check_number("2493*"), "2493")
check("plain number kept", _check_number("2488"), "2488")
check("leading zeros kept", _check_number("000142"), "000142")
check("blank reads as none", _check_number(""), None)
check("null reads as none", _check_number(None), None)

# ── 2. What the table is worth ───────────────────────────────────────────
print("2. the table's own arithmetic")
priced = [(n, Decimal(a)) for _, n, a in CHECKS_TABLE if a[0].isdigit()]
check("twelve priced rows", len(priced), 12)
check("worth $25,669.39", sum((a for _, a in priced), Decimal("0")), Decimal("25669.39"))
check("two rows point elsewhere",
      [n.rstrip("*") for _, n, a in CHECKS_TABLE if not a[0].isdigit()], ["2487", "2499"])
check("249 + 12 + 1 missed line = the printed count", 249 + len(priced) + 1, 262)

# ── 3. A check repeated with its amount is dropped, once ─────────────────
# This bank marks repeats with "-See above-", so the prompt skips them. Other
# banks reprint the check WITH its amount, and a doubled debit is worse than a
# missing one: it reconciles to a wrong number rather than an obvious one.
print("3. the same check printed twice")
lines = [
    {"description": "CHECK PYMT Paypal Credit ARC CHECK # 2487", "amount": -1000.00},
    {"description": "Check 2488", "check_number": "2488", "amount": -10000.00},
    {"description": "Check 2487", "check_number": "2487", "amount": -1000.00},
]
kept, notes = _dedupe_checks(lines)
check("one row removed", len(kept), 2)
check("the summary row went, not the debit",
      [line["description"] for line in kept],
      ["CHECK PYMT Paypal Credit ARC CHECK # 2487", "Check 2488"])
check("says which check", notes[0].startswith("check 2487"), True)

print("4. matched even when the debit carries no check_number field")
lines = [
    {"description": "CHECK PYMT CAPITAL ONE ARC  ARC CHECK # 2499", "amount": -89.58},
    {"description": "Check 2499", "check_number": "2499", "amount": -89.58},
]
kept, _ = _dedupe_checks(lines)
check("one survivor", len(kept), 1)
check("the fuller record survives", kept[0]["description"].startswith("CHECK PYMT"), True)

# ── 5. A reference number that merely contains the digits is not a check ─
print("5. a merchant reference is not a check")
lines = [
    {"description": "VENMO* REF# 334900022500 Visa Transfer,NY", "amount": -145.00},
    {"description": "Check 2500", "check_number": "2500", "amount": -150.00},
]
kept, notes = _dedupe_checks(lines)
check("both kept", len(kept), 2)
check("nothing reported", notes, [])

print("6. distinct checks are all kept")
lines = [{"description": "Check %s" % n, "check_number": n, "amount": -1.00}
         for n in ("2488", "2489", "2490")]
kept, notes = _dedupe_checks(lines)
check("three kept", len(kept), 3)
check("nothing reported", notes, [])

# ── 7. Through the commit, with the real figures ─────────────────────────
print("7. checks reach the record")


class Row(dict):
    __getattr__ = dict.get


class FakeAccounts:
    def __init__(self): self.rows = []
    def get_by_matter(self, m): return self.rows
    def find_match(self, m, i, l4): return None
    def others_with_last4(self, m, i, l4): return []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeStatements:
    def __init__(self): self.rows = []
    def get_by_account(self, a): return []
    def find_period(self, a, s, e): return None
    def find_overlapping(self, a, s, e): return []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, d):
        r = Row(d); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


a, s, t = FakeAccounts(), FakeStatements(), FakeTransactions()
mod.FinancialAccountRepository = lambda m: a
mod.FinancialAccountStatementRepository = lambda m: s
mod.FinancialAccountTransactionRepository = lambda m: t

transactions = [
    {"line_no": i + 1, "transaction_date": "2023-12-11",
     "description": "Check %s" % n, "check_number": n, "amount": float(-amount),
     "physical_page_number": 25}
    for i, (n, amount) in enumerate(priced[:3])
]
statement_service.commit_document(
    manager=None, matter_id=1, staff_id=1, raw_text="<<<PAGE 25>>>\nx\n",
    extracted={"statements": [{
        "account": {"institution": "First Financial Bank", "account_type": "checking",
                    "account_number_last4": "9260"},
        "period": {"start_date": "2023-11-28", "end_date": "2023-12-26"},
        "balances": {"beginning_balance": 0.00,
                     "ending_balance": float(-sum(a for _, a in priced[:3]))},
        "transactions": transactions,
    }]},
)
check("all three written", len(t.rows), 3)
# 2495, 2488, 2496 — not 2488, 2489, 2490. The table is two column groups read
# left to right, so its printed order interleaves them, and reading straight
# down the left column gives the wrong answer. This is exactly what the prompt
# has to warn about, and it caught the author of this test first.
check("numbers stored", [r["check_number"] for r in t.rows], ["2495", "2488", "2496"])
check("stored as debits", all(r["amount"] < 0 for r in t.rows), True)
check("statement reconciles", s.rows[0]["reconciled"], True)

print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
