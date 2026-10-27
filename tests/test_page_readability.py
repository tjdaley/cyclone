"""
tests/test_page_readability.py - Is a page's text layer worth using, or must it be read?

One definition, in `pdf_service`, because three had drifted and the most
permissive of them was the one deciding whether to OCR:

    pdf_service              len >= 20
    source_document_service  len >= 100
    splitting_bench_service  len >= 100 AND the text is language

**WHAT IT COST.** A bank statement split off a scanned production was sent to
the statement ingest. One model call, 4,190 tokens in, 49 out, 2.4 seconds,
outcome `succeeded`, statements found: **zero**. Nothing failed, nothing was
logged, and the job reported success.

Every page of that document carried exactly this much text:

    (2026.08.25) SShahnawaz 003067

The Bates stamp, applied digitally as a text object on top of an image. Thirty
characters, every one a real word or number -- so no measure of text QUALITY
can reject it, and it cleared a 20-character bar with room to spare. The page
was never rendered. The model was handed a stamp and correctly reported that
there was no account statement in it.

Measured on the production itself: **344 of its 396 pages** sat in that band.

Run:  venv/Scripts/python.exe tests/test_page_readability.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from services.pdf_service import (  # noqa: E402
    _MIN_TEXT_LENGTH, _MIN_WORDLIKE, page_needs_ocr, wordlike_ratio,
)

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


STATEMENT_PAGE = (
    "ACME BANK statement of account for the period ending 31 January 2026. "
    "Beginning balance 1,203.02. Deposits and other credits 4,500.00. "
    "Withdrawals and debits 2,118.44. Ending balance 3,584.58."
)

# ── 1. The page that caused this ──────────────────────────────────────────────

print("\n1. A scan whose only text is a Bates stamp")

STAMP = "(2026.08.25) SShahnawaz 003067"
check("the stamp is 30 characters", len(STAMP), 30)
# The trap: it is PERFECT text. A quality measure cannot reject it, which is why
# the rule cannot rest on quality alone.
check("and every token of it is word-like", wordlike_ratio(STAMP), 1.0)
check("so the page must be judged by how LITTLE there is", page_needs_ocr(STAMP), True)

# The old bar was 20, and this is why that number was fatal rather than merely
# low: a stamp clears it, and the stamp is on every page of a stamped scan.
check("the bar is above a stamp, not below it", _MIN_TEXT_LENGTH > len(STAMP), True)

print("\n2. The other shape: long enough, but not language")

DEBRIS = (",01 711iL 6:ee /qc r3s---neo 4r,f cqz eivs/tal- 115s 009220.7/6 "
          "4,7,4-vt 0 444 soo Tr:tee._ hankohanerk.:a,Lorri/ATI'41 TTXN bete")
check("it clears the length bar", len(DEBRIS) >= _MIN_TEXT_LENGTH, True)
check("but it is not language", wordlike_ratio(DEBRIS) < _MIN_WORDLIKE, True)
check("so it is read again", page_needs_ocr(DEBRIS), True)

print("\n3. What must NOT be sent to vision")

check("a real statement page is used as it stands",
      page_needs_ocr(STATEMENT_PAGE), False)
check("even with a stamp on it too",
      page_needs_ocr(STATEMENT_PAGE + "\n" + STAMP), False)
# Whitespace is not content: a page of blank lines is not a readable page.
check("padding does not make a stamp readable",
      page_needs_ocr(STAMP + "\n" * 200), True)
check("nor does an empty page", page_needs_ocr(""), True)
check("nor one of pure whitespace", page_needs_ocr("   \n\n\t  "), True)

# ── 4. One definition, reached from three places ─────────────────────────────

print("\n4. The three callers now agree, which is the whole point")

from services.source_document_service import source_document_service as routing  # noqa: E402
from services.splitting_bench_service import splitting_bench_service as bench  # noqa: E402

for label, text, unreadable in (
    ("a stamp-only scan", STAMP, True),
    ("OCR debris", DEBRIS, True),
    ("a real page", STATEMENT_PAGE, False),
):
    # The bench marks a page unreadable with the same function pdf_service uses
    # to decide whether to render it. If those two ever disagree again, a page
    # the bench calls unusable is one the ingest quietly trusts.
    check("the bench agrees about %s" % label,
          len(text.strip()) >= 20 and page_needs_ocr(text), unreadable)

# Routing counts a page as carrying text when it does not need OCR, so a
# production of stamped scans reports 0% readable and is sent to OCR rather
# than to an automatic split that would find nothing.
stamped = [STAMP] * 10
check("routing sees a stamped scan as having no text layer",
      sum(1 for t in stamped if not page_needs_ocr(t)), 0)
check("and a real production as having one",
      sum(1 for t in [STATEMENT_PAGE] * 10 if not page_needs_ocr(t)), 10)

print("\n5. The bar sits in an empty region, which is why it is 100")

# A stamp is about 30 characters; a statement page is thousands. Nothing
# observed on a real production falls between, so the exact value is not
# delicate -- but it must be above a stamp and below a page.
check("above a stamp", _MIN_TEXT_LENGTH > 30, True)
check("below a real page", _MIN_TEXT_LENGTH < len(STATEMENT_PAGE), True)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all page-readability checks passed")
