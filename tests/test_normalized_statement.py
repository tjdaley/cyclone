"""
tests/test_normalized_statement.py - One statement redrawn as a statement.

A working document for hand-checking against the original, and the things that
make it NOT an exhibit are the things most worth pinning:

  * **No court caption.** A pro-forma reconstruction wearing a case style
    invites exactly the mistake it exists to avoid.
  * **The Rule 1006 notice is REPLACED by a disclaimer.** That notice asserts
    the underlying records are available for examination; this document is not
    a summary offered in their place, it is a redrawing of one of them.
  * **The running balance is COMPUTED and says so.** Opening balance plus each
    amount in turn, so reading down the column beside the original shows the
    line where the two part company -- which is the whole job when a statement
    will not reconcile. A figure that looks like one the bank printed must
    never be mistaken for one.

Run:  venv/Scripts/python.exe tests/test_normalized_statement.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import db.repositories.client as client_mod  # noqa: E402
import db.repositories.matter as matter_mod  # noqa: E402
import services.statement_service as mod  # noqa: E402
from db.models.financial import AccountType  # noqa: E402
from db.models.staff import FullName  # noqa: E402
from services import exhibit_service  # noqa: E402
from services.exhibit_service import NOTICE  # noqa: E402
from services.statement_service import statement_service  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, got):
    check(name, bool(got), True)


class Row(dict):
    __getattr__ = dict.get


def tx(i, day, description, amount, check_number=None, counterparty=None):
    return Row(id=i, statement_id=1, line_no=i, transaction_date=date(2026, 6, day),
               posted_date=None, description=description, amount=Decimal(amount),
               check_number=check_number, counterparty=counterparty, deleted_at=None)


LINES = [
    tx(1, 1, "PAYMENT THANK YOU - WEB", "-500.00"),
    tx(2, 3, "WHOLE FOODS MKT #10265 DALLAS TX", "127.44"),
    tx(3, 9, "CHECK", "-250.00", check_number="2493"),
    tx(4, 14, "SOUTHWEST AIRLINES DALLAS TX", "412.00", counterparty="Southwest Airlines"),
]


def wire_matter(matter=True, client=True, raises=False):
    """The footer's two lookups, each able to be absent or to blow up."""
    class Matters:
        def __init__(self, manager): pass
        def select_one(self, condition):
            if raises:
                raise RuntimeError("database is down")
            return Row(id=1, client_id=7 if client else None,
                       matter_name="Segalini v. Segalini") if matter else None

    class Clients:
        def __init__(self, manager): pass
        def select_one(self, condition):
            return Row(id=7, name=FullName(first_name="Peter", last_name="Segalini")) \
                if client else None

    matter_mod.MatterRepository = Matters
    client_mod.ClientRepository = Clients


def wire(statement, lines=LINES, deleted=0, account=None):
    wire_matter()
    class Transactions:
        def get_by_statement(self, sid, include_deleted=False):
            return list(lines) + ([tx(99, 20, "DROPPED", "0.00")] * deleted
                                  if include_deleted else [])

    class Statements:
        def select_one(self, condition):
            return statement if condition.get("id") == statement["id"] else None

    class Accounts:
        def select_one(self, condition):
            return account

    mod.FinancialAccountTransactionRepository = lambda m: Transactions()
    mod.FinancialAccountStatementRepository = lambda m: Statements()
    mod.FinancialAccountRepository = lambda m: Accounts()


def statement(beginning="1000.00", ending="789.44", computed="789.44", reconciled=True,
              delta=None):
    return Row(id=1, financial_account_id=9, matter_id=1,
               period_start=date(2026, 5, 18), period_end=date(2026, 6, 17),
               beginning_balance=Decimal(beginning), ending_balance=Decimal(ending),
               computed_ending_balance=Decimal(computed), reconciled=reconciled,
               reconciliation_delta=None if delta is None else Decimal(delta),
               flags=[], extraction={"source_filename": "Chase x8445 2026.06.pdf",
                                     "bates_first": "KF 000101", "bates_last": "KF 000110"})


def account(kind=AccountType.credit_card):
    return Row(id=9, institution="JPMorgan Chase Bank, N.A.", account_type=kind,
               account_number_last4="8445")


print("1. it is not an exhibit, and that is the point")
wire(statement(), account=account())
ex = statement_service.build_normalized_exhibit(None, 1)
check("no court caption", ex.caption and len(ex.caption) > 0, True)
check_true("and nothing in it is a cause number or a court",
           not any("Cause" in r.text or "COURT" in r.text.upper()
                   for line in ex.caption for r in line.runs))
check_true("the letterhead names the institution",
           any("JPMorgan" in r.text for line in ex.caption for r in line.runs))
check_true("and says what the document is, on its face",
           any("PRO-FORMA" in r.text for line in ex.caption for r in line.runs))
check("the Rule 1006 notice is replaced, not kept", ex.notice == NOTICE, False)
check_true("by a disclaimer naming what it is", "pro-forma" in ex.notice.lower())
check_true("and what it is not", "not an evidentiary substitute" in ex.notice.lower())

print("")
print("2. the running balance is computed, line by line")
# 1000.00 -500 -> 500.00, +127.44 -> 627.44, -250 -> 377.44, +412 -> 789.44
balances = [r.cells[-1] for r in ex.rows if r.cells[0] and "/" in str(r.cells[0])]
check("one per register line, carried forward",
      balances, ["$500.00", "$627.44", "$377.44", "$789.44"])
check_true("the column says it is computed",
           any("computed" in c.heading.lower() for c in ex.columns))
check_true("and the notes say what it is not",
           any("not the running balance the institution printed" in v
               for _, v in ex.selection))

print("")
print("3. the summary a statement leads with")
text = exhibit_service.to_markdown(ex).decode("utf-8")
check_true("opening balance", "Beginning balance" in text)
check_true("the printed close", "Ending balance (as printed)" in text)
check_true("and ours beside it", "Ending balance (computed)" in text)
# On a card the summary words name the opposite side: a charge RAISES what is
# owed. Same rule as _completeness_findings.
check_true("a card's charges are called charges", "Charges (2)" in text)
check_true("and its credits payments", "Payments and credits (2)" in text)
check_true("the source document is named", "Chase x8445 2026.06.pdf" in text)
check_true("with its Bates range", "KF 000101" in text)

wire(statement(), account=account(AccountType.checking))
bank = exhibit_service.to_markdown(
    statement_service.build_normalized_exhibit(None, 1)).decode("utf-8")
check_true("a bank account's words are the other way round",
           "Deposits and credits (2)" in bank and "Withdrawals and debits (2)" in bank)

print("")
print("4. a statement that does not tie says so on its face")
wire(statement(ending="1789.44", computed="789.44", reconciled=False, delta="1000.00"))
ex = statement_service.build_normalized_exhibit(None, 1)
text = exhibit_service.to_markdown(ex).decode("utf-8")
check_true("the difference is a banner across the document",
           "DIFFERENCE OF $1,000.00" in text)
check_true("and the download warns before it is printed",
           any("does not reconcile" in w for w in ex.warnings))

wire(statement())
quiet = statement_service.build_normalized_exhibit(None, 1)
check("a statement that ties carries no difference row and no warning",
      (any("DIFFERENCE" in str(r.cells[0]) for r in quiet.rows), quiet.warnings),
      (False, []))

print("")
print("5. dropped lines are excluded, counted, and admitted to")
# A hand-check that silently omitted a row sends somebody hunting an
# extraction bug that is really a deletion somebody made on purpose.
wire(statement(), deleted=1)
ex = statement_service.build_normalized_exhibit(None, 1)
removed = [v for label, v in ex.selection if label == "Lines removed"]
check("the removal is reported", len(removed), 1)
check_true("in the singular, because there was one", "1 line on this statement was" in removed[0])
check_true("the line itself is not in the document",
           "DROPPED" not in exhibit_service.to_markdown(ex).decode("utf-8"))

print("")
print("6. the check column appears only when there are checks")
check_true("present here", any(c.heading == "Check" for c in ex.columns))
wire(statement(), lines=[tx(1, 1, "PAYMENT THANK YOU", "-500.00")])
no_checks = statement_service.build_normalized_exhibit(None, 1)
check("absent when nothing on the statement is one",
      any(c.heading == "Check" for c in no_checks.columns), False)

print("")
print("7. every format renders, and the disclaimer survives into each")
wire(statement(), account=account())
ex = statement_service.build_normalized_exhibit(None, 1)
for fmt in ("csv", "md", "docx", "pdf"):
    content, _media, filename = exhibit_service.render(ex, fmt)
    check_true("%s renders" % fmt, len(content) > 0)
    # The filename carries the account and the period: a year of these would
    # otherwise be twelve files with one name.
    check_true("%s is named for the account and period" % fmt,
               "8445" in filename and "2026_06" in filename)

check_true("markdown carries the disclaimer",
           "not an evidentiary substitute" in exhibit_service.to_markdown(ex).decode("utf-8"))
# CSV is deliberately the clean data, with no preamble and no notice -- the
# same rule every other export follows. It is the register, not the document.
check("the CSV does not, by the same rule as every other export",
      "evidentiary" in exhibit_service.to_csv(ex).decode("utf-8-sig"), False)

print("")
print("8. the footer says what the sheet pertains to")
# These are printed and read in a stack. The letterhead names the BANK, and
# without this nothing on the page says which case it belongs to.
wire(statement(), account=account())
ex = statement_service.build_normalized_exhibit(None, 1)
check("client then matter", ex.footer, "Peter Segalini - Segalini v. Segalini")
check_true("it reaches the markdown",
           "*Peter Segalini - Segalini v. Segalini*"
           in exhibit_service.to_markdown(ex).decode("utf-8"))
check("but not the CSV, like the caption and the notice",
      "Segalini v." in exhibit_service.to_csv(ex).decode("utf-8-sig"), False)

# Whatever can be resolved is used; a footer must never cost somebody the sheet.
wire_matter(client=False)
check("no readable client still names the matter",
      statement_service.build_normalized_exhibit(None, 1).footer, "Segalini v. Segalini")
wire_matter(matter=False)
check("no matter at all is a blank band, not a crash",
      statement_service.build_normalized_exhibit(None, 1).footer, "")
wire_matter(raises=True)
check("and a lookup that blows up is swallowed",
      statement_service.build_normalized_exhibit(None, 1).footer, "")

print("")
print("9. a statement that does not exist")
wire(statement())
try:
    statement_service.build_normalized_exhibit(None, 999)
    check("refused", False, True)
except ValueError as e:
    check("refused", str(e), "Statement 999 not found")

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
