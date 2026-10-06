"""
tests/test_index_windowing.py - A long document is indexed whole, not as far as 60,000 characters.

THE FAILURE THIS REPRODUCES, reported from the field as *"eight months ingest
and the rest has numerous errors"*:

A long document is read in two stages — one pass indexes it (accounts, periods,
balances, page ranges), then each statement's pages are walked. The index pass
was handed ``raw_text[:_MAX_STATEMENT_CHARS]`` and nothing else. A scanned
statement page yields two to four thousand characters, so 60,000 is fifteen to
thirty pages: **about eight monthly statements.** Everything past that was never
in the text the index saw.

Two things made it worse than a clean cutoff. The single-pass path at least
appends "[DOCUMENT TRUNCATED]" so the model knows; this path clipped silently,
with no marker, no log line and no flag. And a statement whose ``last_page`` the
index did not give defaults to the END of the document, so months nine onward
were not cleanly absent — they were absorbed by statement eight, which is what
"numerous errors" looks like from the outside.

What is tested here: the index runs in page windows that each fit the budget,
overlapping so a statement at a seam is still described whole; the descriptions
are stitched rather than one of them being discarded; and any page no statement
accounts for is reported, because an import that is short must never look
complete.

Run:  venv/Scripts/python.exe tests/test_index_windowing.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.statement_service as mod  # noqa: E402
from services.llm_service import LLMResult  # noqa: E402
from services.statement_service import _MAX_STATEMENT_CHARS, statement_service  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def reply(text):
    return LLMResult(text=text, profile="extract_account_statement",
                     vendor="anthropic", model="claude-opus-5", attempts=1)


# ── A two-year production: 24 statements, three pages each ───────────────
#
# Page text is padded to the size a scanned statement page actually extracts to,
# because the whole bug is about character count. 2,600 is deliberately modest:
# at 3,500 the old clip would have reached even fewer statements.
PAGE_CHARS = 2_600
PAGES_PER_STATEMENT = 3
STATEMENTS = 24


def statement_pages(index):
    """The 1-based page numbers of statement ``index`` (0-based)."""
    first = index * PAGES_PER_STATEMENT + 1
    return list(range(first, first + PAGES_PER_STATEMENT))


def last4(index):
    return "%04d" % (9000 + index)


def period(index):
    month = index % 12 + 1
    year = 2024 + index // 12
    return ("%04d-%02d-01" % (year, month), "%04d-%02d-28" % (year, month))


def document(statements=STATEMENTS, blank_pages=()):
    """Page-marked text for a production of monthly statements."""
    out = []
    for index in range(statements):
        start, end = period(index)
        for offset, page in enumerate(statement_pages(index)):
            out.append("<<<PAGE %d>>>" % page)
            if page in blank_pages:
                out.append("")   # a separator sheet: no content to claim
                continue
            if offset == 0:
                # The header the index reports and `_statement_pages` cuts on.
                out.append("FIRST FINANCIAL BANK")
                out.append("Checking Account - %s" % last4(index))
                out.append("STATEMENT PERIOD %s to %s" % (start, end))
            out.append("PAGE %d of %d" % (offset + 1, PAGES_PER_STATEMENT))
            out.append("FILLER " * (PAGE_CHARS // 7))
    return "\n".join(out)


class FakeLLM:
    """
    A model that answers only about the text it was given.

    That fidelity is the whole point: the old code's bug was invisible precisely
    because a fake that answers from the full document hides it.
    """

    def __init__(self, skip=()):
        #: Statement indexes the "model" fails to notice, to exercise the gap check.
        self.skip = set(skip)
        self.index_bodies: list[str] = []
        self.transaction_bodies: list[str] = []

    def complete_detailed(self, system, body, profile=None, **kwargs):
        if "indexing" in system:
            self.index_bodies.append(body)
            return reply('{"statements": [%s]}' % ",".join(self._visible(body)))
        self.transaction_bodies.append(body)
        return reply('{"transactions": []}')

    def _visible(self, body):
        """Describe each statement from what this window can actually see."""
        blocks = []
        for index in range(STATEMENTS):
            if index in self.skip:
                continue
            pages = [p for p in statement_pages(index) if "<<<PAGE %d>>>" % p in body]
            if not pages:
                continue
            start, end = period(index)
            # A window that cannot see the header cannot know the account or the
            # period — it sees the middle of a statement. That is the fragment
            # the stitch has to recognise.
            saw_header = statement_pages(index)[0] in pages
            fields = [
                '"first_page": %d' % min(pages),
                '"last_page": %d' % max(pages),
            ]
            if saw_header:
                fields.append(
                    '"account": {"institution": "First Financial Bank",'
                    ' "account_type": "checking", "account_number_last4": "%s"}' % last4(index))
                fields.append(
                    '"period": {"start_date": "%s", "end_date": "%s"}' % (start, end))
                fields.append('"balances": {"beginning_balance": 100, "ending_balance": 100}')
                fields.append('"header_text": "Checking Account - %s"' % last4(index))
            blocks.append("{%s}" % ",".join(fields))
        return blocks


# ── 1. What the old single clip could see ────────────────────────────────
print("1. the bug, measured")
text = document()
clipped = text[:_MAX_STATEMENT_CHARS]
reachable = sum(
    1 for index in range(STATEMENTS)
    if "<<<PAGE %d>>>" % statement_pages(index)[0] in clipped
)
print("   %d pages, %d chars; the first %d chars reach %d of %d statements"
      % (STATEMENTS * PAGES_PER_STATEMENT, len(text), _MAX_STATEMENT_CHARS,
         reachable, STATEMENTS))
check("a single clipped call could not see the whole production", reachable < STATEMENTS, True)

# ── 2. Windowed, every statement is indexed ──────────────────────────────
print("2. indexed in windows")
llm = FakeLLM()
mod.llm_service = llm
result = statement_service.extract(text)

check("more than one index call", len(llm.index_bodies) > 1, True)
check("every index call fits the budget",
      max(len(b) for b in llm.index_bodies) <= _MAX_STATEMENT_CHARS, True)
check("all %d statements found" % STATEMENTS, len(result["statements"]), STATEMENTS)
check("no statement counted twice",
      len({(s["account"]["account_number_last4"],
            s["period"]["start_date"]) for s in result["statements"]}), STATEMENTS)
check("in document order",
      [s["account"]["account_number_last4"] for s in result["statements"]],
      [last4(i) for i in range(STATEMENTS)])
# The walk consumes first_page/last_page, so the ranges are checked on the
# index itself — which is also the unit the stitching belongs to.
from services import bates_service  # noqa: E402
indexed = statement_service._index_document(bates_service.split_pages(text), [])
check("page ranges are right, stitched across the seams",
      [(s["first_page"], s["last_page"]) for s in indexed],
      [(statement_pages(i)[0], statement_pages(i)[-1]) for i in range(STATEMENTS)])
check("and the last statement is the last month",
      result["statements"][-1]["account"]["account_number_last4"], last4(STATEMENTS - 1))
check("nothing reported as unindexed", result.get("unindexed_pages"), None)

# ── 3. A short document still takes exactly one index call ───────────────
print("3. a short document is unchanged")
llm = FakeLLM()
mod.llm_service = llm
short = statement_service.extract(document(statements=3))
check("one index call", len(llm.index_bodies), 1)
check("labelled 'index', as it always was",
      [p["pass"] for p in short["extraction"]["passes"] if p["pass"].startswith("index")],
      ["index"])
check("three statements", len(short["statements"]), 3)

# ── 4. Pages no statement claims are reported ────────────────────────────
print("4. the gap check")
llm = FakeLLM(skip=(5, 6))        # the model misses two months entirely
mod.llm_service = llm
missed = statement_service.extract(text)
check("the statements it did find are kept", len(missed["statements"]), STATEMENTS - 2)
# The two missed months are reported by their FIRST pages (16 and 19), not as
# six unclaimed pages -- and that is the real failure's shape. The walk cuts a
# statement at the next INDEXED header, so a month nobody indexed gets absorbed
# by the one before it: every page has an owner and the unclaimed-page check
# sees nothing. What survives is the document's own pagination restarting on a
# page that begins no statement anybody read.
check("the pages that begin an unread statement are named",
      missed["unindexed_pages"], [statement_pages(5)[0], statement_pages(6)[0]])

print("   a blank separator sheet is not a gap")
llm = FakeLLM()
mod.llm_service = llm
# Page 2 holds nothing: an insert, or a scan that produced no text. It is a
# different fact from a month nobody read, and PAGE_UNREADABLE covers it.
with_blank = statement_service.extract(document(blank_pages=(2,)))
check("not reported", with_blank.get("unindexed_pages"), None)

# ── 5. The flag reaches the statements, so nothing looks complete ────────
print("5. PAGES_NOT_INDEXED on commit")


class Row(dict):
    __getattr__ = dict.get


class FakeAccounts:
    def __init__(self): self.rows = []
    def get_by_matter(self, m): return self.rows
    def find_match(self, m, inst, last_four):
        for a in self.rows:
            if a["account_number_last4"] == last_four and a["institution"].lower() == inst.lower():
                return a
        return None
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r
    def others_with_last4(self, m, institution, last_four): return []


class FakeStatements:
    def __init__(self): self.rows = []
    def get_by_account(self, a): return [r for r in self.rows if r["financial_account_id"] == a]
    def find_period(self, a, s, e): return None
    def find_overlapping(self, a, s, e): return []
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


class FakeTransactions:
    def __init__(self): self.rows = []
    def insert(self, data):
        r = Row(data); r["id"] = len(self.rows) + 1; self.rows.append(r); return r


accounts, statements_repo, transactions = FakeAccounts(), FakeStatements(), FakeTransactions()
mod.FinancialAccountRepository = lambda m: accounts
mod.FinancialAccountStatementRepository = lambda m: statements_repo
mod.FinancialAccountTransactionRepository = lambda m: transactions

one = {
    "account": {"institution": "First Financial Bank", "account_type": "checking",
                "account_number_last4": "9000"},
    "period": {"start_date": "2024-01-01", "end_date": "2024-01-28"},
    "balances": {"beginning_balance": 100, "ending_balance": 100},
    "transactions": [],
}
summary = statement_service.commit_document(
    manager=None, matter_id=1, staff_id=1,
    extracted={"statements": [dict(one)], "unindexed_pages": [10, 11, 12, 20]},
    raw_text=document(statements=1),
)
flags = {f["code"]: f for f in statements_repo.rows[0]["flags"]}
check("flagged", "PAGES_NOT_INDEXED" in flags, True)
check("as a warning, so the document is held for review",
      flags["PAGES_NOT_INDEXED"]["severity"], "warn")
check("naming the pages as ranges",
      "10-12, 20" in flags["PAGES_NOT_INDEXED"]["note"], True)
check("and saying lines there may have been filed against another statement",
      "filed against that statement" in flags["PAGES_NOT_INDEXED"]["note"], True)
check("and saying what to do about it",
      "split the PDF" in flags["PAGES_NOT_INDEXED"]["note"], True)
check("so it is not auto-accepted", summary["auto_accepted"], 0)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
