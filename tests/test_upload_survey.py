"""
tests/test_upload_survey.py - Warning about a PDF before anything reads it.

A paralegal uploaded a 142-page client production holding twenty-four months.
The browser timed out while the server carried on, some statements imported, the
last was a mess, and the whole thing had to be rejected and split by hand. Every
part of that is recoverable and none of it is quick.

The survey is what would have said so up front. It is advisory on purpose: a
document holding several statements is legal input — a combined statement is one
upload with five accounts in it — so blocking would trade a real workflow for a
rare one.

Run:  venv/Scripts/python.exe tests/test_upload_survey.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import pymupdf  # noqa: E402
from services.pdf_service import _PAGE_ONE, pdf_service  # noqa: E402
from services.statement_service import _MANY_PAGES, statement_service  # noqa: E402

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
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all upload-survey checks passed")
