"""
A long statement is read in passes, and short extractions are caught.

Reproduces the real failure. First Financial account x9260, 11/28/23-12/26/23:
27 pages, 286 printed entries. One call returned 38 of them — every credit,
then fifteen debits, then a closing brace. The JSON was valid, the input was
36,863 characters against a 60,000 ceiling, and the output used about a seventh
of its 32,000-token budget. Nothing failed and nothing was truncated: the model
stopped. No limit can be raised to fix that, because no limit was reached.

Two answers, tested here:

  * Read a long document in passes, so each answer is small enough that
    finishing is the easy option.
  * Check what was extracted against the totals the statement prints about
    itself, so a short extraction can never pass quietly again.
"""
import sys

sys.path.insert(0, r"d:\Local Projects\cyclone\app")

import services.statement_service as mod  # noqa: E402
from services.llm_service import LLMResult  # noqa: E402
from services.statement_service import _completeness_findings, statement_service  # noqa: E402

FAILURES = []


def reply(text):
    """An answer plus the candidate behind it, which is what the service now asks for."""
    return LLMResult(text=text, profile="extract_account_statement",
                     vendor="anthropic", model="claude-opus-5", attempts=1)


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


# ── A stand-in for the model, recording what it was asked ────────────────
class FakeLLM:
    """Answers an index pass and a transaction pass, and remembers the calls."""

    def __init__(self, pages_with_lines, per_page=15, first_page=1, last_page=27):
        self.pages_with_lines = pages_with_lines
        self.per_page = per_page
        self.first_page, self.last_page = first_page, last_page
        self.calls = []

    def complete_detailed(self, system, body, profile=None, **kwargs):
        self.calls.append((system, body))
        if "indexing" in system:
            return reply(
                '{"statements": [{"first_page": %d, "last_page": %d,'
                ' "account": {"institution": "First Financial Bank", "account_type": "checking",'
                ' "account_number_last4": "9260"},'
                ' "period": {"start_date": "2023-11-28", "end_date": "2023-12-26"},'
                ' "balances": {"beginning_balance": 0, "ending_balance": 0,'
                ' "printed_totals": {"deposits_credits": 100, "checks_debits": -100}},'
                ' "printed_counts": {"deposits_credits": 1, "checks_debits": 1}}]}'
                % (self.first_page, self.last_page)
            )
        # A transaction pass: return entries for each requested page that has any.
        wanted = [n for n in self.pages_with_lines if "<<<PAGE %d>>>" % n in body]
        rows = []
        for page in wanted:
            for i in range(self.per_page):
                rows.append(
                    '{"line_no": %d, "transaction_date": "2023-12-04",'
                    ' "description": "LINE p%d n%d", "amount": -1.00,'
                    ' "physical_page_number": %d}' % (i + 1, page, i, page)
                )
        return reply('{"transactions": [%s]}' % ",".join(rows))


def document(page_count):
    """Page-marked text for a document of a given length."""
    out = []
    for page in range(1, page_count + 1):
        out.append("<<<PAGE %d>>>" % page)
        out.append("ACCOUNT NUMBER 848579260")
        out.append("STATEMENT DATES 11/28/23-12/26/23")
        out.append("PAGE %d of %d" % (page, page_count))
        out.append("GS%d" % (1885 + page))
    return "\n".join(out)


# ── 1. The real document: 27 pages, read in passes ───────────────────────
print("1. a 27-page statement")
llm = FakeLLM(pages_with_lines=list(range(1, 26)))
mod.llm_service = llm
result = statement_service.extract(document(27))

index_calls = [c for c in llm.calls if "indexing" in c[0]]
line_calls = [c for c in llm.calls if "extracting transactions from PART" in c[0]]
check("indexed once", len(index_calls), 1)
# 27 pages in fours.
check("walked in seven passes", len(line_calls), 7)
check("one statement", len(result["statements"]), 1)
check("every line collected", len(result["statements"][0]["transactions"]), 25 * 15)
check("renumbered end to end",
      [t["line_no"] for t in result["statements"][0]["transactions"][:3]], [1, 2, 3])
check("last line numbered %d" % (25 * 15),
      result["statements"][0]["transactions"][-1]["line_no"], 25 * 15)
check("page numbers survive the slicing",
      result["statements"][0]["transactions"][-1]["physical_page_number"], 25)
check("index pass carries no transactions", "transactions" in index_calls[0][0].lower(), True)

# Each transaction pass must know the period, or a line printed "12/04" has no year.
check("every pass told the period",
      all("2023-11-28 to 2023-12-26" in body for _, body in line_calls), True)
check("every pass told the institution",
      all("First Financial Bank" in body for _, body in line_calls), True)

# ── 2. A short statement still takes the single call ─────────────────────
print("2. a 3-page statement is unchanged")


class SinglePass:
    def __init__(self): self.calls = 0
    def complete_detailed(self, system, body, profile=None, **kwargs):
        self.calls += 1
        return reply('{"statements": [{"account": {}, "period": {}, "balances": {}, "transactions": []}]}')


single = SinglePass()
mod.llm_service = single
statement_service.extract(document(3))
check("one call, no index pass", single.calls, 1)

# ── 3. Pages beyond the statement are not walked ─────────────────────────
print("3. a statement occupying part of a document")
llm = FakeLLM(pages_with_lines=[1, 2, 3, 4], last_page=4)
mod.llm_service = llm
statement_service.extract(document(20))
line_calls = [c for c in llm.calls if "extracting transactions from PART" in c[0]]
check("only its own pages", len(line_calls), 1)
check("did not read past page 4",
      all("<<<PAGE 5>>>" not in body for _, body in line_calls), True)

# ── 4. The completeness check, on the real figures ───────────────────────
print("4. what the statement says about itself")
lines = [{"amount": 5000}] * 23 + [{"amount": -100}] * 15
findings = _completeness_findings(
    lines,
    {"deposits_credits": 202100.41, "checks_debits": -195600.04},
    {"deposits_credits": 24, "checks_debits": 262},
)
check("both sides reported", len(findings), 4)
check("names the debit shortfall",
      any("15 debit line(s) extracted, but the statement prints 262" in f for f in findings), True)
check("credits and debits told apart",
      any(f.startswith("Debit lines total 1500.00") for f in findings), True)

print("5. a complete statement raises nothing")
lines = [{"amount": 60}, {"amount": 40}, {"amount": -100}]
check("silent when it ties", _completeness_findings(
    lines, {"deposits_credits": 100.00, "checks_debits": -100.00},
    {"deposits_credits": 2, "checks_debits": 1}), [])

print("6. nothing printed to check against")
check("silent when the statement says nothing",
      _completeness_findings([{"amount": -5}], {}, {}), [])

# ── 7. Chunk boundaries: lookahead, no loss, no duplication ──────────────
# An entry runs to several lines and a page break falls in the middle of one
# constantly. Each slice is given the following page to read but not report
# from, so a description is never cut in half and no entry is counted twice.
print("7. page-break entries")


class BoundaryLLM:
    """Reports one transaction per page, anchored on that page."""

    def __init__(self, last_page=9):
        self.last_page = last_page
        self.slices = []
        self.audits = 0

    def complete_detailed(self, system, body, profile=None, **kwargs):
        if "indexing" in system:
            return reply('{"statements": [{"first_page": 1, "last_page": %d,'
                         ' "account": {}, "period": {}, "balances": {}}]}' % self.last_page)
        if "re-reading ONLY the dates" in system:
            # These rows carry no dates, so every batch triggers the audit. It
            # is not a slice of the document, so it must not be recorded as one.
            self.audits += 1
            return reply('{"dates": []}')
        primary = [n for n in range(1, self.last_page + 1)
                   if "<<<PAGE %d>>>" % n in body.split("=== CONTINUATION CONTEXT ===")[0]]
        context = body.split("=== CONTINUATION CONTEXT ===")
        self.slices.append((primary, context[1] if len(context) > 1 else ""))
        # Deliberately answers for the lookahead page too, which the service
        # must discard rather than double-count.
        every = primary + ([primary[-1] + 1] if primary and primary[-1] < self.last_page else [])
        rows = ['{"description": "entry on page %d", "amount": -1.00,'
                ' "physical_page_number": %d}' % (n, n) for n in every]
        return reply('{"transactions": [%s]}' % ",".join(rows))


boundary = BoundaryLLM(last_page=9)
mod.llm_service = boundary
result = statement_service.extract(document(9))
lines = result["statements"][0]["transactions"]

check("every page reported once", [t["physical_page_number"] for t in lines], list(range(1, 10)))
check("no duplicates from the lookahead", len(lines), len(set(t["physical_page_number"] for t in lines)))
# Slices are 1-4, 5-8, 9; the first two carry a lookahead page.
check("first slice looks ahead to page 5", "<<<PAGE 5>>>" in boundary.slices[0][1], True)
check("second slice looks ahead to page 9", "<<<PAGE 9>>>" in boundary.slices[1][1], True)
check("last slice has no lookahead", boundary.slices[2][1].strip(), "")
# Every batch here comes back undated, so every batch is re-read.
check("each batch had its dates audited", boundary.audits, len(boundary.slices))
check("lookahead is fenced off, not primary",
      boundary.slices[0][0], [1, 2, 3, 4])


# ── 8. A page nobody could read is not a blank page ──────────────────────
# When the text layer is unusable and vision also fails, the page used to
# contribute an empty string and nothing recorded the loss.
print("8. unreadable pages")
from services.pdf_service import _UNREADABLE_MARKER  # noqa: E402
from services.statement_service import _unreadable_pages  # noqa: E402

doc = "\n\n".join([
    "<<<PAGE 1>>>\nfine",
    "<<<PAGE 2>>>\n" + _UNREADABLE_MARKER,
    "<<<PAGE 3>>>\nfine",
])
check("names the lost page", _unreadable_pages(doc), [2])
check("silent on a clean document", _unreadable_pages("<<<PAGE 1>>>\nall good"), [])
check("needs a page marker to attribute a loss", _unreadable_pages(_UNREADABLE_MARKER), [])
check("the marker says what happened", "OCR both failed" in _UNREADABLE_MARKER, True)

# ── 9. A statement that splits its debits across buckets ─────────────────
# Bank of Texas, account 8098173954, 09-01-25 to 09-30-25. Its summary prints
# "2 Checks & Withdrawals 159.11" and "Service Fees 2.00" as separate lines,
# so the three debits we correctly extract exceed the bucket this compares
# against. Exceeding a printed figure is ordinary; falling short is not.
print("9. debits split across named buckets")
bank_of_texas = [
    {"amount": 147.00},    # INTERNET XFER FROM CHKG
    {"amount": -12.97},    # MICHAELS STORES
    {"amount": -146.14},   # SP POPALOCK
    {"amount": -2.00},     # Service Charge
]
check("a bucketed summary raises nothing", _completeness_findings(
    bank_of_texas,
    {"deposits": 147.00, "checks_withdrawals": -159.11, "service_fees": -2.00},
    {"deposits": 1, "checks_withdrawals": 2}), [])

# The shortfall it exists to catch still reports, in both directions.
short = [{"amount": -12.97}]
findings = _completeness_findings(
    short, {"checks_withdrawals": -159.11}, {"checks_withdrawals": 2})
check("one line where two are printed still reports",
      any("1 debit line(s) extracted, but the statement prints 2" in f for f in findings), True)
check("and the money that is missing",
      any("short by 146.14" in f for f in findings), True)


# ── 10. A pass whose answer will not parse ───────────────────────────────
#
# The real failure: one chunk of a five-account Capital One upload came back as
# invalid JSON, and the ValueError propagated out of extract(), out of the
# worker, and failed the job. Four accounts that had read perfectly were never
# committed, and nothing on screen suggested the file would fare any better on
# a second attempt.
print("10. one chunk comes back malformed")


class BrokenOnce:
    """Bad JSON on the first read of a chunk, good on the retry."""

    def __init__(self, break_pages, always=False):
        self.break_pages = break_pages
        self.always = always
        self.calls = []
        self.seen = set()

    def complete_detailed(self, system, body, profile=None, **kwargs):
        self.calls.append((system, body))
        if "indexing" in system:
            return reply(
                '{"statements": [{"first_page": 1, "last_page": 8,'
                ' "header_text": "ACCOUNT NUMBER 848579260",'
                ' "account": {"institution": "First Financial Bank", "account_type": "checking",'
                ' "account_number_last4": "9260"},'
                ' "period": {"start_date": "2023-11-28", "end_date": "2023-12-26"},'
                ' "balances": {"beginning_balance": 0, "ending_balance": 0}}]}'
            )
        marker = next((p for p in self.break_pages if "PRIMARY PAGES: %d" % p in body), None)
        if marker is not None and (self.always or marker not in self.seen):
            self.seen.add(marker)
            # A description with an unescaped quote in it — the shape the real
            # failure took, and not something json.loads can be talked into.
            return reply('{"transactions": [{"description": "PAYMENT TO "THE GROVE"",')
        page = int(body.split("PRIMARY PAGES: ")[1].split(" ")[0])
        return reply(
            '{"transactions": [{"line_no": 1, "transaction_date": "2023-12-04",'
            ' "description": "LINE p%d", "amount": -1.00,'
            ' "physical_page_number": %d}]}' % (page, page)
        )


llm = BrokenOnce(break_pages=[5])
mod.llm_service = llm
result = statement_service.extract(document(8))
statement = result["statements"][0]

# Eight pages walk as two chunks of four, and this fake answers one
# transaction per chunk — so a whole document is two, not eight.
check("the malformed pass is asked again",
      sum(1 for s, b in llm.calls if "PRIMARY PAGES: 5" in b), 2)
check("the retry says the previous answer was rejected",
      any("NOT VALID JSON" in b for s, b in llm.calls if "PRIMARY PAGES: 5" in b), True)
check("and the document is whole", len(statement["transactions"]), 2)
check("nothing is flagged when the retry works", "unread_pages" in statement, False)

# Now the same chunk fails both times.
print("11. and when the retry fails too")
llm = BrokenOnce(break_pages=[5], always=True)
mod.llm_service = llm
result = statement_service.extract(document(8))
statement = result["statements"][0]

check("the job is not abandoned", len(result["statements"]), 1)
check("the chunk that read cleanly still committed", len(statement["transactions"]), 1)
# The unit of loss is the CHUNK, not the page: the pass covering 5-8 is the one
# that could not be parsed, so all four of its pages are unaccounted for and the
# flag has to say so rather than understate it.
check("every page of the failed pass is named", statement["unread_pages"], [5, 6, 7, 8])
check("it gave up after two attempts",
      sum(1 for s, b in llm.calls if "PRIMARY PAGES: 5" in b), 2)

# ── 12. A reply that stops mid-array ─────────────────────────────────────
#
# The second real failure, and the opposite of a bad escape: two dense pages of
# a Capital One register produced about forty entries and seventeen thousand
# characters of JSON, cut off mid-array. `max_tokens` was 32,000 and the answer
# used a fraction of it — nothing hit a ceiling, the reply simply stopped.
#
# Asking again gets an answer the same length. The range has to get smaller.
print("12. a reply that runs off the end")


class Truncates:
    """Cuts the reply short for any range wider than `fits` pages."""

    def __init__(self, fits=1):
        self.fits = fits
        self.ranges = []

    def complete_detailed(self, system, body, profile=None, **kwargs):
        if "indexing" in system:
            return reply(
                '{"statements": [{"first_page": 1, "last_page": 8,'
                ' "header_text": "ACCOUNT NUMBER 848579260",'
                ' "account": {"institution": "First Financial Bank", "account_type": "checking",'
                ' "account_number_last4": "9260"},'
                ' "period": {"start_date": "2023-11-28", "end_date": "2023-12-26"},'
                ' "balances": {"beginning_balance": 0, "ending_balance": 0}}]}'
            )
        head = body.split("PRIMARY PAGES: ")[1].split(".")[0]
        first, last = (int(n) for n in head.split(" to "))
        self.ranges.append((first, last))
        rows = ','.join(
            '{"line_no": 1, "transaction_date": "2023-12-04", "description": "LINE p%d",'
            ' "amount": -1.00, "physical_page_number": %d}' % (p, p)
            for p in range(first, last + 1)
        )
        if last - first + 1 > self.fits:
            # Valid up to the point it stops, which is what truncation looks
            # like: the parser consumes a whole entry and then finds nothing.
            return reply('{"transactions": [%s,' % rows)
        return reply('{"transactions": [%s]}' % rows)


llm = Truncates(fits=1)
mod.llm_service = llm
result = statement_service.extract(document(8))
statement = result["statements"][0]

check("the wide range was tried first", llm.ranges[0], (1, 4))
# Asked once more before splitting — on the real document that second ask was
# enough both times, because the retry note changes the question rather than
# re-rolling an identical one.
check("asked again at the same width", llm.ranges[1], (1, 4))
check("then halved", llm.ranges[2], (1, 2))
check("and halved again until it fitted", (1, 1) in llm.ranges, True)
check("every page ends up read", len(statement["transactions"]), 8)
check("in page order",
      [t["physical_page_number"] for t in statement["transactions"]], [1, 2, 3, 4, 5, 6, 7, 8])
check("nothing reported unread", "unread_pages" in statement, False)

# The case production actually hit: cut off once, complete on the second ask.
# No split, no loss, and — because the statement then reconciles — no flag.
print("12a. cut off once, then complete")


class TruncatesOnce:
    """Truncates the first reply for a range, answers it whole the second time."""

    def __init__(self):
        self.ranges = []
        self.bodies = []
        self.seen = set()

    def complete_detailed(self, system, body, profile=None, **kwargs):
        self.bodies.append(body)
        if "indexing" in system:
            return reply(
                '{"statements": [{"first_page": 1, "last_page": 8,'
                ' "header_text": "ACCOUNT NUMBER 848579260",'
                ' "account": {"institution": "First Financial Bank", "account_type": "checking",'
                ' "account_number_last4": "9260"},'
                ' "period": {"start_date": "2023-11-28", "end_date": "2023-12-26"},'
                ' "balances": {"beginning_balance": 0, "ending_balance": 0}}]}'
            )
        head = body.split("PRIMARY PAGES: ")[1].split(".")[0]
        first, last = (int(n) for n in head.split(" to "))
        self.ranges.append((first, last))
        rows = ','.join(
            '{"line_no": 1, "transaction_date": "2023-12-04", "description": "LINE p%d",'
            ' "amount": -1.00, "physical_page_number": %d}' % (p, p)
            for p in range(first, last + 1)
        )
        if (first, last) not in self.seen:
            self.seen.add((first, last))
            return reply('{"transactions": [%s,' % rows)
        return reply('{"transactions": [%s]}' % rows)


llm = TruncatesOnce()
mod.llm_service = llm
result = statement_service.extract(document(8))
statement = result["statements"][0]

check("each range asked exactly twice", llm.ranges, [(1, 4), (1, 4), (5, 8), (5, 8)])
check("never split", all(last - first == 3 for first, last in llm.ranges), True)
check("every page read", len(statement["transactions"]), 8)
check("nothing reported unread", "unread_pages" in statement, False)
# The retry has to name the failure it is answering. At temperature 0.0 an
# identical prompt returns an identical reply, so the changed note IS the
# mechanism — and telling a model that ran out of room to escape its quotes
# more carefully addresses nothing.
retries = [b for b in llm.bodies if "YOUR PREVIOUS ANSWER" in b]
check("both retries went out", len(retries), 2)
check("and say the reply was cut off",
      all("WAS CUT OFF" in b for b in retries), True)
check("not that it was malformed",
      any("NOT VALID JSON" in b for b in retries), False)

# A single page that still will not read is as small as a slice gets.
print("13. a single page that will not read at any width")
llm = Truncates(fits=0)
mod.llm_service = llm
result = statement_service.extract(document(8))
statement = result["statements"][0]
check("every page reported unread", statement["unread_pages"], [1, 2, 3, 4, 5, 6, 7, 8])
check("and the document still commits", len(result["statements"]), 1)

# One bad page must not take its siblings with it.
print("14. one page fails, the rest survive")


class OnePageBreaks(Truncates):
    def complete_detailed(self, system, body, profile=None, **kwargs):
        if "indexing" not in system:
            head = body.split("PRIMARY PAGES: ")[1].split(".")[0]
            first, last = (int(n) for n in head.split(" to "))
            if first == last == 3:
                return reply('{"transactions": [{"description": "X",')
        return Truncates.complete_detailed(self, system, body, profile=profile, **kwargs)


llm = OnePageBreaks(fits=1)
mod.llm_service = llm
result = statement_service.extract(document(8))
statement = result["statements"][0]
check("only the page that failed is reported", statement["unread_pages"], [3])
check("its siblings are kept",
      [t["physical_page_number"] for t in statement["transactions"]], [1, 2, 4, 5, 6, 7, 8])


print()
print("FAILURES: %d" % len(FAILURES))
for f in FAILURES:
    print("  - " + f)
sys.exit(1 if FAILURES else 0)
