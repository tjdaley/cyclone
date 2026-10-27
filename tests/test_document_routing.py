"""
tests/test_document_routing.py - Deciding what a received file IS, before anybody looks.

Two real piles from one firm came out with inverted profiles, which is the whole
reason this exists:

  Pile A   84 pages, 22 documents, 100% text layer, 74% pagination coverage
           -> 86% of boundaries found at 95% precision
  Pile B  396 pages, 169 documents, 96% text layer,  8% pagination coverage
           -> no operating point better than 49% recall at 76% precision

So there is no one way to process a received file, and the routing test has to
run on an upload nobody has looked at -- which rules out precision and recall,
because those need hand-marked truth.

What this pins:

- **Pagination coverage is the predictor, not text quality.** Pile B has a 96%
  text layer and cannot be split automatically. A text-quality gate would have
  sent it down the automatic path and produced 163 false splits.
- **A run must COMPLETE to count.** A lone "Page 1 of 9" with nothing after it
  says the opposite of what it appears to: the document is not here.
- **Nothing is refused for size.** Past the automatic split's capacity a file
  goes to a person, who can split it or decline. The file is kept either way.
- **An unreadable file is not a routing decision.** A gate that fires when it
  cannot see would reject a document for being unreadable by the one check that
  never read it.

Run:  venv/Scripts/python.exe tests/test_document_routing.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import pymupdf  # noqa: E402
from services.source_document_service import (  # noqa: E402
    ROUTE_AUTOMATIC, ROUTE_BENCH, ROUTE_OCR,
    _MAX_AUTOMATIC_DOCUMENTS, _MAX_AUTOMATIC_PAGES,
    source_document_service as svc,
)

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


def pdf(specs):
    """specs: list of (pages_in_this_document, paginated, body_text or None)."""
    doc = pymupdf.open()
    for count, paginated, body in specs:
        for n in range(1, count + 1):
            page = doc.new_page(width=612, height=792)
            if body is not None:
                page.insert_text((54, 72), body, fontsize=10)
                page.insert_text((54, 100), (body + " ") * 6, fontsize=9)
                page.insert_text((54, 130), (body + " ") * 6, fontsize=9)
            if paginated:
                page.insert_text((54, 740), "Page %d of %d" % (n, count), fontsize=9)
    out = doc.tobytes()
    doc.close()
    return out


STATEMENT = "ACME BANK statement of account for the period ending"

# ── 1. The orderly pile ───────────────────────────────────────────────────────

print("\n1. A run of statements that paginate themselves")

orderly = pdf([(4, True, STATEMENT)] * 5)       # 20 pages, 5 documents
v = svc.route(orderly)
check("every page is inside a complete run", v["pagination_coverage"], 1.0)
check("the documents are counted", v["likely_documents"], 5)
check("so it splits automatically", v["route"], ROUTE_AUTOMATIC)
check("and the reason says why", "pagination" in v["reason"], True)

# ── 2. The stack of scraps ────────────────────────────────────────────────────

print("\n2. A stack of odds and ends, each with a readable text layer")

# This is pile B's shape: plenty of text, almost no self-description.
scraps = pdf([(1, False, "WALGREENS RECEIPT 14.32 thank you for shopping with us today")] * 12
             + [(2, False, "handwritten note about the account and the money owed")] * 4)
v = svc.route(scraps)
check("the text layer is fine", v["text_share"], 1.0)
# A text-quality gate would have sent pile B down the automatic path and
# produced 163 false splits on 169 documents.
check("but nothing says which document a page belongs to",
      v["pagination_coverage"], 0.0)
check("so it goes to a person", v["route"], ROUTE_BENCH)
check("and the reason names the real problem",
      "say which document" in v["reason"], True)

print("\n3. A mixture: half the pages self-describe")

half = pdf([(4, True, STATEMENT)] * 2 + [(1, False, "RECEIPT 9.99 thanks")] * 8)
v = svc.route(half)
check("coverage is the share of pages, not of documents",
      v["pagination_coverage"], 0.5)
# 0.5 is the threshold itself, and the test pins which side of it counts: the
# gap between the measured piles is 8% to 74%, so the exact cut is arbitrary
# and the BOUNDARY BEHAVIOUR must at least be decided rather than accidental.
check("exactly at the threshold is good enough for the automatic path",
      v["route"], ROUTE_AUTOMATIC)

# ── 4. Size routes, never refuses ─────────────────────────────────────────────

print("\n4. Size sends a file to a person; it never refuses one")

big = pdf([(4, True, STATEMENT)] * 20)          # 80 pages, all self-describing
v = svc.route(big)
check("a long pile still reports perfect coverage", v["pagination_coverage"], 1.0)
# The automatic split cannot handle it, and the answer is a person rather than a
# refusal: a 700-page pile was warned about, imported anyway, and was a total
# loss, but refusing it offered nothing instead.
check("and still goes to the bench, because it is too long",
      v["route"], ROUTE_BENCH)
check("the reason says splitting by hand is quicker than fixing a bad split",
      "by hand is quicker" in v["reason"], True)
check("nothing in the verdict refuses it",
      [k for k in v if "refus" in k or "reject" in k], [])

many = pdf([(2, True, STATEMENT)] * (_MAX_AUTOMATIC_DOCUMENTS + 2))
v = svc.route(many)
check("too many documents does the same", v["route"], ROUTE_BENCH)
check("even though the file is short", v["pages"] <= _MAX_AUTOMATIC_PAGES, True)

at_limit = pdf([(2, True, STATEMENT)] * _MAX_AUTOMATIC_DOCUMENTS)
check("exactly the document limit is allowed — it is a maximum, not a bound",
      svc.route(at_limit)["route"], ROUTE_AUTOMATIC)

# ── 5. No text layer: nothing is decidable ────────────────────────────────────

print("\n5. Without a text layer, no other question can be asked")

blank = pdf([(10, False, None)])
v = svc.route(blank)
check("no page has readable text", v["text_share"], 0.0)
check("so OCR comes first", v["route"], ROUTE_OCR)
check("and the reason says it will be routed again",
      "routed again" in v["reason"], True)
# Both the printed-signal method and a model's page-continuation judgment need
# readable pages, so this ordering is not a preference.
check("coverage is reported as zero rather than guessed",
      v["pagination_coverage"], 0.0)

mostly_blank = pdf([(7, False, None), (3, True, STATEMENT)])
check("a mostly-unreadable pile goes to OCR even with some good pages",
      svc.route(mostly_blank)["route"], ROUTE_OCR)

# Deliberately low: a production with a few photographs in it is still
# measurable and must not be sent round the OCR loop for them.
some_photos = pdf([(2, False, None), (8, True, STATEMENT)])
check("but a few image-only pages do not derail a readable pile",
      svc.route(some_photos)["route"], ROUTE_AUTOMATIC)

# ── 6. A run that does not finish ─────────────────────────────────────────────

print("\n6. An incomplete run counts for nothing, which is the honest answer")

# "Page 1 of 9" followed by two pages is a document that is NOT here. Counting
# its pages as covered would route a truncated production to the automatic
# split and report the missing pages as somebody else's problem.
truncated = pymupdf.open()
for n in range(1, 4):
    page = truncated.new_page(width=612, height=792)
    page.insert_text((54, 72), STATEMENT, fontsize=10)
    page.insert_text((54, 100), (STATEMENT + " ") * 6, fontsize=9)
    page.insert_text((54, 740), "Page %d of 9" % n, fontsize=9)
short = truncated.tobytes()
truncated.close()

v = svc.route(short)
check("an unfinished run covers nothing", v["pagination_coverage"], 0.0)
check("and no document is claimed", v["likely_documents"], 0)
check("so it goes to a person", v["route"], ROUTE_BENCH)

print("\n7. Pagination is read from the edges of the page only")

# A figure mid-paragraph is not pagination. Without this a contract reciting
# "see page 1 of 4 above" would read as a document boundary.
prose = pymupdf.open()
page = prose.new_page(width=612, height=792)
page.insert_text((54, 300), "as set out in page 1 of 4 of the agreement, the party",
                 fontsize=10)
page.insert_text((54, 330), ("filler text to give this page a real text layer " * 3),
                 fontsize=9)
body = prose.tobytes()
prose.close()
check("prose in the middle of a page is not pagination",
      svc.route(body)["pagination_coverage"], 0.0)

print("\n8. A file that cannot be read is not a routing decision")

# A gate that fires when it cannot see would reject a document for being
# unreadable by the one check that never read it properly. The ingest reports a
# corrupt file far better than this could.
v = svc.route(b"this is not a pdf at all")
check("it goes to a person rather than being refused", v["route"], ROUTE_BENCH)
check("and says so plainly", "could not be read" in v["reason"], True)
check("with no invented figures", (v["pages"], v["pagination_coverage"]), (0, 0.0))
check("an empty file likewise", svc.route(b"")["route"], ROUTE_BENCH)

# ── 9. The two ways of reaching the verdict must agree ───────────────────────

print("\n9. Deriving the verdict from stored facts gives the same answer")

# The upload no longer reads the file: a 74 MB production moving twice inside
# one request is what haproxy cut with a 504. A survey job records per-page
# facts instead, and the verdict is DERIVED from those rather than stored —
# a stored verdict computed before a file was OCR'd would still say "no text
# layer" afterwards.
#
# That leaves the same rules reachable by two routes, which is exactly the
# shape that drifts. This is what stops it.
from services.splitting_bench_service import splitting_bench_service as bench  # noqa: E402

for name, doc in (
    ("a run of self-paginating statements", orderly),
    ("a stack of scraps", scraps),
    ("a half-and-half mixture", half),
    ("too long for the automatic path", big),
    ("too many documents", many),
    ("no text layer at all", blank),
    ("a few image-only pages", some_photos),
    ("an unfinished run", short),
):
    from_pdf = svc.route(doc)
    from_facts = svc.route_from_features(bench.page_features(doc))
    check("%s: same route" % name, from_facts["route"], from_pdf["route"])
    check("%s: same coverage" % name,
          from_facts["pagination_coverage"], from_pdf["pagination_coverage"])
    check("%s: same document count" % name,
          from_facts["likely_documents"], from_pdf["likely_documents"])

print("\n10. Before the survey has run, it says so rather than guessing")

# A verdict invented from no information would route a file somewhere on no
# evidence. Null is the honest answer, and the screen shows the file is safe.
pending = svc.route_from_features(None)
check("no route yet", pending["route"], None)
check("and the reason says the file is stored and waiting",
      "stored exactly as it arrived" in pending["reason"], True)
check("with no invented figures",
      (pending["pages"], pending["pagination_coverage"]), (0, 0.0))
check("an empty feature list is the same", svc.route_from_features([])["route"], None)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all document-routing checks passed")
