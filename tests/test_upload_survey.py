"""
tests/test_upload_survey.py - Warning about a PDF before anything reads it.

A paralegal uploaded a 142-page client production holding twenty-four months.
The browser timed out while the server carried on, some statements imported, the
last was a mess, and the whole thing had to be rejected and split by hand. Every
part of that is recoverable and none of it is quick.

The survey is what would have said so up front. It is advisory on purpose: a
document holding several statements is legal input — a combined statement is one
upload with five accounts in it — so warning rather than blocking is right in
the range the survey is about.

**And advice was not enough at the top end.** A 700-page PDF of 122 statements
was uploaded; the survey said "48 first pages in the first 250" and the import
went ahead anyway, because a warning is a warning. It was a total loss. Past
`_MAX_UPLOAD_PAGES` or `_MAX_UPLOAD_STATEMENTS` an upload is now REFUSED, and
refused **before anything is stored** — no object in storage, no job row, no
half-imported statements. The second half of this suite pins that.

Run:  venv/Scripts/python.exe tests/test_upload_survey.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import pymupdf  # noqa: E402
from services.pdf_service import _PAGE_ONE, pdf_service  # noqa: E402
from services.statement_service import (  # noqa: E402
    _MANY_PAGES, _MAX_UPLOAD_PAGES, _MAX_UPLOAD_STATEMENTS, statement_service,
)

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


def pdf(pages, text_for):
    """A PDF with a real text layer, so the survey reads it the way it will live."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        doc.new_page().insert_text((72, 72), text_for(n), fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def statements_of(size, label="Page %d of {n}"):
    """A production of consecutive statements, each paginated from one."""
    def text(n):
        return "ACME BANK STATEMENT\nPage %d of %d\nBalance 1,234.56" % ((n - 1) % size + 1, size)
    return text


def one_statement(total):
    """A single statement paginated straight through."""
    def text(n):
        return "ACME BANK STATEMENT\nPage %d of %d\nBalance 1,234.56" % (n, total)
    return text


# ── The pattern ──────────────────────────────────────────────────────────────
#
# THE WORD BOUNDARY IS THE POINT. The obvious spelling of this — "PAGE\\s1" —
# also matches inside "Page 10", "Page 19" and "Page 142", so one long statement
# reads as a dozen first pages and every long upload gets warned about.

print("Reading a page-1 marker")

for text, want in (
    ("Page 1 of 8", True),
    ("PAGE   1", True),
    ("page 1", True),
    ("PAGE 1 OF 8", True),
    ("Page 10 of 12", False),
    ("Page 19", False),
    ("Page 142 of 200", False),
    ("Page 100", False),
    ("1 of 8", False),
):
    check("%-16s -> %s" % (text, want), bool(_PAGE_ONE.search(text)), want)


# ── The survey ───────────────────────────────────────────────────────────────

print("\nCounting statements in a document")

survey = pdf_service.survey(pdf(144, statements_of(6)))
check("every page counted", survey["pages"], 144)
check("twenty-four months, twenty-four first pages", survey["first_pages"], 24)

# The case the naive pattern gets wrong: pages 1, 10-19 and 100-142 would all
# read as "page 1" and this would claim fifty-odd statements.
survey = pdf_service.survey(pdf(40, one_statement(40)))
check("one long statement is one first page", survey["first_pages"], 1)

# A statement printing its pagination twice per page — header and footer — is
# still one statement, because pages are counted rather than occurrences.
survey = pdf_service.survey(pdf(4, lambda n: "Page %d of 4\nACME\nPage %d of 4" % (n, n)))
check("counted by page, not by occurrence", survey["first_pages"], 1)

# An image-only PDF yields no text and no marker. The survey says nothing rather
# than rendering pages to find out — a warning that cost an OCR pass would cost
# more than the mistake it prevents.
survey = pdf_service.survey(pdf(3, lambda n: ""))
check("a page with no text layer is simply not counted", survey["first_pages"], 0)
check("but its length is still known", survey["pages"], 3)


# ── The warnings ─────────────────────────────────────────────────────────────

print("\nWhat the person dropping the file is told")

notes = statement_service.survey_upload(pdf(144, statements_of(6)))
check("one warning, not two", len(notes), 1)
check_true("names the count", "about 24 separate statements" in notes[0])
check_true("says what to do", "split the PDF into one file per statement" in notes[0])
check_true("and why that is now faster", "read several at a time" in notes[0])

# Long, but paginated as one statement: the count signal found nothing, so the
# length signal speaks instead — and does not claim a number it cannot support.
notes = statement_service.survey_upload(pdf(40, one_statement(40)))
check("one warning", len(notes), 1)
check_true("names the length", "40 pages" in notes[0])
check("makes no claim about a count", "separate statements" in notes[0], False)

# An ordinary month says nothing at all. A guardrail that fires on every upload
# is one nobody reads.
check("a four-page statement is silent",
      statement_service.survey_upload(pdf(4, one_statement(4))), [])
check("and so is one exactly at the threshold",
      statement_service.survey_upload(pdf(_MANY_PAGES, one_statement(_MANY_PAGES))), [])
check_true("one page over is not",
           statement_service.survey_upload(pdf(_MANY_PAGES + 1, one_statement(_MANY_PAGES + 1))))

# A combined statement — several accounts, one bank, one document — is ordinary
# input. It is warned about, because it cannot be told apart from a production
# by pagination alone, and the warning is advice rather than a refusal.
notes = statement_service.survey_upload(pdf(8, lambda n: "Page %d of 8\nCapital One" % n))
check("a short combined statement is not warned about", notes, [])

print("\nA warning never costs an upload")

# The survey runs inside the request that queues the job. If it throws, the
# upload has still happened and the job is still queued; returning no warning is
# the only acceptable failure.
check("an unreadable file warns nothing rather than raising",
      statement_service.survey_upload(b"not a pdf at all"), [])
check("and neither does an empty one", statement_service.survey_upload(b""), [])

print("")
print("The hard limits: refused, not warned")

# The real one, scaled down: far too many pages AND far too many statements.
monster = pdf(_MAX_UPLOAD_PAGES + 10, statements_of(2))
refusal = statement_service.refuse_oversized_upload(monster)
check_true("a production of many statements is refused", refusal)
check_true("told how many it holds", "at least" in (refusal or ""))
check_true("told to split it", "Split it into one file per statement" in (refusal or ""))
# The sentence has to end the question "what happened to my upload?", because
# the answer is "nothing", and silence there reads as data loss.
check_true("and told nothing was kept", "Nothing was saved." in (refusal or ""))

# Statement count alone, inside the page limit: 10 one-page statements in 10
# pages. Length is not what makes this unworkable, so length is not the test.
many = pdf(_MAX_UPLOAD_STATEMENTS + 2, lambda n: "ACME BANK\nPage 1 of 1\nBalance 1.00")
refusal = statement_service.refuse_oversized_upload(many)
check_true("too many statements is enough on its own", refusal)
check_true("counted", "at least %d" % (_MAX_UPLOAD_STATEMENTS + 2) in (refusal or ""))

# Pages alone: ONE long statement, over the page limit. A single statement that
# long is rare and the limit still applies — it is a gate, not a guess.
long_one = pdf(_MAX_UPLOAD_PAGES + 1, one_statement(_MAX_UPLOAD_PAGES + 1))
refusal = statement_service.refuse_oversized_upload(long_one)
check_true("too many pages is enough on its own", refusal)
check_true("says the page count", "%d pages" % (_MAX_UPLOAD_PAGES + 1) in (refusal or ""))

print("")
print("And what must still be accepted")

check("a single ordinary statement", statement_service.refuse_oversized_upload(pdf(4, one_statement(4))), None)
check("a combined statement of several accounts",
      statement_service.refuse_oversized_upload(
          pdf(8, lambda n: 'Page %d of 8\\nCapital One' % n)), None)
check("exactly the page limit is allowed — the limit is a maximum, not a bound",
      statement_service.refuse_oversized_upload(
          pdf(_MAX_UPLOAD_PAGES, one_statement(_MAX_UPLOAD_PAGES))), None)
check("and exactly the statement limit",
      statement_service.refuse_oversized_upload(
          pdf(_MAX_UPLOAD_STATEMENTS, lambda n: "ACME BANK\nPage 1 of 1\nBalance 1.00")), None)

# A gate that fires when it cannot see would reject a document for being
# unreadable by the one check that never reads it properly. The ingest reports
# a corrupt file far better than this could.
check("an unreadable file is NOT refused by the size gate",
      statement_service.refuse_oversized_upload(b"not a pdf at all"), None)
check("nor an empty one", statement_service.refuse_oversized_upload(b""), None)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all upload-survey checks passed")
