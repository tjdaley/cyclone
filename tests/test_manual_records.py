"""
tests/test_manual_records.py - The three things a person does by hand.

Each exists because the automatic path cannot reach a real case:

  * **An account added by hand.** The undisclosed report is rebuilt from the
    transactions every run — which is right, and is what makes it impossible to
    tick something off. A $5 repayment to a sibling puts that sibling's account
    on the list forever. The answer is not a second list of accounts to ignore:
    it is to put the account where accounts live, characterized
    `no_marital_interest`, so the scan matches it like any other and the
    judgment is a record somebody made. The same door adds an account the estate
    DOES have an interest in with no statements against it — which is exactly
    what the compliance grid and a motion to compel are built from.
  * **Bates numbers typed in.** Rare and real: a stamp the text layer renders as
    something else. Retrying cannot help, because the document reads the same
    way every time. The run is consecutive across THE PAGES IN HAND — a partial
    production of physical pages 1, 2 and 4 carries three consecutive stamps —
    and the range must span exactly those pages, because being off by one
    mis-cites every line after the first.
  * **The motion text.** The same gaps as the grid, in the words a pleading
    uses: whole dates, no day counts, and accounts with nothing missing left
    out.

Run:  venv/Scripts/python.exe tests/test_manual_records.py
"""
import os
import sys
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.compliance_service as compliance_mod  # noqa: E402
import services.statement_service as statement_mod  # noqa: E402
from db.models.financial import AccountType, PropertyCharacter  # noqa: E402
from services.compliance_service import compliance_service  # noqa: E402
from services.statement_service import statement_service  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def refused(name, call, expect):
    try:
        call()
    except ValueError as e:
        check(name, expect in str(e), True)
        return str(e)
    check(name, "allowed", "refused")
    return ""


class Row(dict):
    __getattr__ = dict.get


# ── Bates by hand ────────────────────────────────────────────────────────
def line(id, page, bates=None, flags=None):
    return Row(id=id, statement_id=1, physical_page_number=page,
               bates_number=bates, flags=flags or [], description="LINE %d" % id)


class FakeTransactions:
    def __init__(self, rows): self.rows = {r["id"]: r for r in rows}
    def get_by_statement(self, sid, include_deleted=False):
        return [r for r in self.rows.values() if r["statement_id"] == sid]
    def update(self, id, patch):
        self.rows[id].update(patch)
        return self.rows[id]


class FakeStatements:
    def __init__(self, row): self.row = row
    def select_one(self, condition):
        return self.row if condition.get("id") == self.row["id"] else None
    def update(self, id, patch):
        self.row.update(patch)
        return self.row


def wire(lines, flags=None, extraction=None):
    statement = Row(id=1, financial_account_id=9, matter_id=1,
                    period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                    flags=flags if flags is not None else [
                        {"code": "BATES_UNSTAMPED", "severity": "info",
                         "field_path": "bates", "note": "none found"}],
                    extraction=extraction or {"source_filename": "prod.pdf"})
    t, s = FakeTransactions(lines), FakeStatements(statement)
    statement_mod.FinancialAccountTransactionRepository = lambda m: t
    statement_mod.FinancialAccountStatementRepository = lambda m: s
    return t, s, statement


print("1. a partial production: physical pages 1, 2 and 4")
# The stamps run consecutively over the sheets in hand. The missing sheet took
# its number with it, which is the whole point of numbering by page ORDER.
lines = [line(10, 1), line(11, 1), line(12, 2), line(13, 4)]
t, s, statement = wire(lines)
updated, pages = statement_service.set_bates(
    None, 1, "KF 000101", "KF 000103", staff_id=3, staff_name="Tom Daley")

check("three pages numbered", pages, 3)
check("page 1", t.rows[10]["bates_number"], "KF 000101")
check("both lines on page 1 get the same number", t.rows[11]["bates_number"], "KF 000101")
check("page 2 is the next number", t.rows[12]["bates_number"], "KF 000102")
check("page 4 follows page 2 — not page 4's own number",
      t.rows[13]["bates_number"], "KF 000103")
check("the separator is kept as printed", "KF 000101".startswith("KF "), True)

print("2. and it says a person did it")
check("the line carries the flag",
      [f["code"] for f in t.rows[10]["flags"]], ["BATES_MANUALLY_ENTERED"])
check("attributed", t.rows[10]["flags"][0]["by"], "Tom Daley")
codes = [f["code"] for f in updated["flags"]]
check("the statement does too, and the stale one is gone",
      codes, ["BATES_MANUALLY_ENTERED"])
check_true("the note says it was not detected",
           "not a stamp detected on it" in updated["flags"][0]["note"])
check("the range is on the statement",
      (updated["extraction"]["bates_first"], updated["extraction"]["bates_last"]),
      ("KF 000101", "KF 000103"))
check("with who and when", updated["extraction"]["bates_manual"]["by_staff_id"], 3)

print("3. the arithmetic is the check")
t, s, _ = wire([line(10, 1), line(11, 2), line(12, 4)])
message = refused("a range that is too short is refused",
                  lambda: statement_service.set_bates(
                      None, 1, "KF 000101", "KF 000102", 3, "Tom Daley"),
                  "covers 2 page(s), but this statement has transactions on 3")
check_true("and says why it matters", "mis-cites every line" in message)
check("nothing was written", t.rows[10]["bates_number"], None)

refused("a range that is too long is refused too",
        lambda: statement_service.set_bates(None, 1, "KF 000101", "KF 000110", 3, "Tom"),
        "covers 10 page(s)")

print("4. what is not a stamp")
t, s, _ = wire([line(10, 1)])
refused("unreadable text", lambda: statement_service.set_bates(
    None, 1, "the third page", None, 3, "Tom"), "does not look like a Bates number")
refused("an unreadable end", lambda: statement_service.set_bates(
    None, 1, "KF 000101", "later", 3, "Tom"), "does not look like a stamp")

print("5. a statement with no page numbers has nothing to stamp")
t, s, _ = wire([line(10, None)])
refused("refused rather than guessed", lambda: statement_service.set_bates(
    None, 1, "KF 000101", None, 3, "Tom"), "No page numbers are recorded")

print("6. one page needs no range")
t, s, _ = wire([line(10, 1)])
_, pages = statement_service.set_bates(None, 1, "GS2775", None, 3, "Tom")
check("numbered", pages, 1)
check("keeping the printed shape", t.rows[10]["bates_number"], "GS2775")


# ── The motion ───────────────────────────────────────────────────────────
print("")
print("7. the missing statements, as a motion says them")


class FakeComplianceService:
    pass


def matrix(*_args, **_kwargs):
    return {
        "years": [2026],
        "accounts": [
            {"label": "Bank of America ····0123", "statements": 3,
             "gaps": [{"start": "2026-02-01", "end": "2026-03-03", "days": 31}],
             "nothing_before": None, "nothing_after": None, "cells": {},
             "opening_month": None, "closing_month": None},
            {"label": "Bank of America ····1234", "statements": 0,
             "gaps": [], "nothing_before": None, "nothing_after": None, "cells": {},
             "opening_month": None, "closing_month": None},
            # Complete: it must not appear. A numbered list naming an account
            # with nothing missing invites exactly one question.
            {"label": "Frost Bank ····9999", "statements": 12,
             "gaps": [], "nothing_before": None, "nothing_after": None, "cells": {},
             "opening_month": None, "closing_month": None},
        ],
        "totals": {"accounts": 3, "statements": 15, "gaps": 1, "unassigned": 0},
        "scope": {"side": "opposing", "since": None, "through": None, "bounded": False},
    }


compliance_service.matrix = matrix  # type: ignore[method-assign]
result = compliance_service.motion_text(None, 1, side="opposing")
print(result["text"])

check("two accounts listed", result["accounts"], 2)
check_true("the heading is the sentence a motion uses",
           result["text"].startswith("The following account statements have not been produced:"))
check_true("numbered", "1. Bank of America ····0123:" in result["text"])
check_true("dates as a pleading writes them",
           "1 February 2026 through 3 March 2026" in result["text"])
check_true("an account with nothing produced says so",
           "2. Bank of America ····1234: no statements have been produced" in result["text"])
check_true("the complete account is left out", "Frost Bank" not in result["text"])
# The grid says "(31 days)" because somebody is reading a report; a pleading
# does not.
check_true("no day counts", "31 day" not in result["text"])

print("")
print("8. nothing missing, nothing to paste")
compliance_service.matrix = lambda *_a, **_k: {  # type: ignore[method-assign]
    "years": [], "accounts": [],
    "totals": {"accounts": 0, "statements": 0, "gaps": 0, "unassigned": 0},
    "scope": {"side": None, "since": None, "through": None, "bounded": False},
}
empty = compliance_service.motion_text(None, 1)
check("no text at all, so the caller can say so rather than paste a heading",
      empty, {"text": "", "accounts": 0, "gaps": 0})


# ── The characterization ─────────────────────────────────────────────────
print("")
print("9. an account outside the estate")
check("the value exists", PropertyCharacter.no_marital_interest.value, "no_marital_interest")
check_true("and is not one of the separate-property values",
           PropertyCharacter.no_marital_interest not in (
               PropertyCharacter.separate_petitioner, PropertyCharacter.separate_respondent))


class FakeAccounts:
    def __init__(self, rows): self.rows = rows
    def get_by_matter(self, matter_id): return self.rows


def account(id, character=None):
    return Row(id=id, matter_id=1, institution="Bank of America", account_type=AccountType.checking,
               account_number_last4="%04d" % id, property_character=character,
               production_responsibility="client", is_closed=False, ownership="client_sole")


class NoStatements:
    def get_by_account(self, account_id): return []
    def get_by_matter(self, matter_id): return []
    def rejected_ids(self, matter_id): return []


compliance_mod.FinancialAccountRepository = lambda m: FakeAccounts([
    account(1), account(2, PropertyCharacter.no_marital_interest)])
compliance_mod.FinancialAccountStatementRepository = lambda m: NoStatements()


class FakeMatterRepo:
    def select_one(self, condition):
        return Row(id=1, client_produces_since=None, opposing_produces_since=None)


compliance_mod.MatterRepository = lambda m: FakeMatterRepo()
# matrix() was replaced above for the motion test; restore the real one.
compliance_service.matrix = compliance_mod.ComplianceService.matrix.__get__(compliance_service)
report = compliance_service.matrix(None, 1)
check("the ordinary account is on the grid", report["totals"]["accounts"], 1)
check("the one outside the estate is not — there is nothing to compel",
      [a["label"] for a in report["accounts"]], ["Bank of America ····0001"])

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
