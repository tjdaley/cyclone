"""
tests/test_pdf_authoring.py - Writing PDFs, not only reading them.

Nothing in Cyclone wrote a PDF before this. `pdf_service` read them, and the
exhibit renderer produced its own from scratch; splitting a received production
into documents, stamping Bates numbers onto it, and writing an OCR layer back
all need the other direction.

Two functions, and the traps they are built around:

- `render_page` is deliberately NOT the OCR rendering. `_vision_extract` runs at
  300 DPI in greyscale with contrast 2.0 and sharpness 1.5, tuned for a model
  reading characters; the splitting bench needs a human to RECOGNISE a page from
  a letterhead and a date, and those settings make that harder. Optimising for
  the wrong reader would have been invisible — the thumbnails would simply have
  been worse to work with.

- `write_pages` copies ranges IN THE ORDER GIVEN and refuses overlaps. The order
  is the document's reading order, which on the bench is what the operator
  asserted; sorting would overrule them silently. An overlap means one document
  holding the same page twice, which duplicates evidence inside one exhibit.

- `coalesce_pages` is the single conversion from a selection to ranges. Two
  implementations of it would let a document's stored extent disagree with the
  pages it actually contains.

Run:  venv/Scripts/python.exe tests/test_pdf_authoring.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import pymupdf  # noqa: E402
from services.pdf_service import (  # noqa: E402
    _THUMBNAIL_WIDTH, coalesce_pages, pdf_service,
)

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


def check_raises(label, fn, fragment):
    try:
        fn()
    except ValueError as e:
        if fragment.lower() in str(e).lower():
            print("  ok   %s" % label)
        else:
            print("  FAIL %s\n         message %r lacks %r" % (label, str(e), fragment))
            FAILURES.append(label)
    except Exception as e:  # noqa: BLE001
        print("  FAIL %s\n         raised %s, not ValueError" % (label, type(e).__name__))
        FAILURES.append(label)
    else:
        print("  FAIL %s — did not raise" % label)
        FAILURES.append(label)


def pdf(pages, text=lambda n: "", width=612, height=792):
    """A PDF whose every page says which page it is, so copies are checkable."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=width, height=height)
        page.insert_text((54, 72), "PAGE-MARKER-%d" % n, fontsize=14)
        body = text(n)
        if body:
            page.insert_text((54, 120), body, fontsize=10)
    out = doc.tobytes()
    doc.close()
    return out


def pages_of(pdf_bytes):
    """Which source pages a written PDF holds, read back off the markers."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    found = []
    for page in doc:
        t = page.get_text() or ""
        for token in t.split():
            if token.startswith("PAGE-MARKER-"):
                found.append(int(token.rsplit("-", 1)[1]))
                break
    doc.close()
    return found


SOURCE = pdf(20)

# ---------------------------------------------------------------- 1. coalesce
print("\n1. A selection becomes ranges, in one place only")

check("one page", coalesce_pages([7]), [(7, 7)])
check("a contiguous run", coalesce_pages([3, 4, 5]), [(3, 5)])
check("two runs", coalesce_pages([3, 4, 8, 9]), [(3, 4), (8, 9)])
check("order on the way in is irrelevant — a selection is a set",
      coalesce_pages([9, 3, 8, 4]), [(3, 4), (8, 9)])
check("clicking a page twice is one page",
      coalesce_pages([5, 5, 6]), [(5, 6)])
check("an empty selection is no ranges", coalesce_pages([]), [])
# Pages 10-12 and 15-17: the interrupted document the bench exists to express.
check("discontinuous, which Acrobat's bookmarks cannot express at all",
      coalesce_pages([10, 11, 12, 15, 16, 17]), [(10, 12), (15, 17)])
check_raises("page 0 is refused — page numbers are 1-based everywhere here",
             lambda: coalesce_pages([0, 1]), "1-based")

# ------------------------------------------------------------- 2. write_pages
print("\n2. Writing the pages, and only those pages")

check("a single range", pages_of(pdf_service.write_pages(SOURCE, [(3, 5)])), [3, 4, 5])
check("one page", pages_of(pdf_service.write_pages(SOURCE, [(11, 11)])), [11])
check("two ranges join into one document",
      pages_of(pdf_service.write_pages(SOURCE, [(10, 12), (15, 17)])),
      [10, 11, 12, 15, 16, 17])
check("the whole document", len(pages_of(pdf_service.write_pages(SOURCE, [(1, 20)]))), 20)

# The order is the operator's assertion about reading order, not a detail.
check("ranges are copied in the ORDER GIVEN, never sorted",
      pages_of(pdf_service.write_pages(SOURCE, [(15, 16), (3, 4)])),
      [15, 16, 3, 4])

print("\n3. What it refuses, and why each would be a silent wrong answer")

check_raises("no ranges at all", lambda: pdf_service.write_pages(SOURCE, []), "at least one")
check_raises("a reversed range", lambda: pdf_service.write_pages(SOURCE, [(9, 4)]), "reversed")
check_raises("past the end of the source",
             lambda: pdf_service.write_pages(SOURCE, [(19, 21)]), "outside the document")
check_raises("page 0", lambda: pdf_service.write_pages(SOURCE, [(0, 3)]), "outside the document")
# An overlap duplicates a page of evidence inside one exhibit, and the
# exclusion constraint on document_source_pages would reject it at the database
# with a far worse message.
check_raises("overlapping ranges",
             lambda: pdf_service.write_pages(SOURCE, [(3, 7), (5, 9)]), "overlaps")
check_raises("the same range twice",
             lambda: pdf_service.write_pages(SOURCE, [(3, 4), (3, 4)]), "overlaps")
check_raises("a corrupt source",
             lambda: pdf_service.write_pages(b"not a pdf", [(1, 1)]), "could not open")

print("\n4. Nothing is inherited that would describe the parent")

parent = pymupdf.open(stream=SOURCE, filetype="pdf")
parent.set_metadata({"title": "Shahnawaz production volume 2", "author": "Opposing Counsel"})
with_meta = parent.tobytes()
parent.close()

child = pymupdf.open(stream=pdf_service.write_pages(with_meta, [(4, 5)]), filetype="pdf")
# The parent's title describes a 396-page production. On a two-page child it
# would read as a description of the child, in any viewer anybody opens it in.
check("the child's title is not the parent's", child.metadata.get("title") or "", "")
check("nor the author", child.metadata.get("author") or "", "")
child.close()

# --------------------------------------------------------------- 5. rendering
print("\n5. Rendering a page for a person to look at")

png = pdf_service.render_page(SOURCE, 1, fmt="png")
jpeg = pdf_service.render_page(SOURCE, 1, fmt="jpeg")
check("a PNG comes back", png[:8], b"\x89PNG\r\n\x1a\n")
check("a JPEG comes back", jpeg[:3], b"\xff\xd8\xff")

img = pymupdf.Pixmap(png)
check("the requested width is the width delivered", img.width, _THUMBNAIL_WIDTH)
# Within a pixel: 450 x (792/612) is 582.35, and a pixmap has whole rows.
check("the aspect ratio of US letter is preserved",
      abs(img.height - img.width * 792 / 612) <= 1, True)

wide = pymupdf.Pixmap(pdf_service.render_page(SOURCE, 1, width=900, fmt="png"))
check("and a different width is honoured", wide.width, 900)

# A receipt-shaped page: the bench's grid has to cope with mixed geometry,
# which is exactly what a pile of scraps is.
receipt = pdf(1, width=288, height=648)
thumb = pymupdf.Pixmap(pdf_service.render_page(receipt, 1, fmt="png"))
check("a narrow page is rendered at the same width, taller",
      (thumb.width, thumb.height > thumb.width), (_THUMBNAIL_WIDTH, True))

print("\n6. Rendering says 'not there' differently from 'broken'")

# The grid already knows the page count, so an out-of-range page is the caller
# asking for something that does not exist: a 404, never a 500.
check("page 0 is None", pdf_service.render_page(SOURCE, 0), None)
check("one past the end is None", pdf_service.render_page(SOURCE, 21), None)
check("the last page is not", pdf_service.render_page(SOURCE, 20) is None, False)
# A file that will not open is a different fact and raises, as it does in
# extract_text and survey.
check_raises("a corrupt file raises instead",
             lambda: pdf_service.render_page(b"not a pdf", 1), "could not open")
check_raises("an unknown format raises rather than guessing",
             lambda: pdf_service.render_page(SOURCE, 1, fmt="webp"), "jpeg")
check_raises("a zero width raises", lambda: pdf_service.render_page(SOURCE, 1, width=0),
             "positive")

print("\n7. The round trip the bench actually performs")

# Select pages off a grid, coalesce, write, read back.
selected = [15, 10, 11, 16, 12, 17]          # clicked in whatever order
ranges = coalesce_pages(selected)
check("coalesced", ranges, [(10, 12), (15, 17)])
check("written and read back", pages_of(pdf_service.write_pages(SOURCE, ranges)),
      [10, 11, 12, 15, 16, 17])
check("every selected page survived, and nothing else",
      sorted(pages_of(pdf_service.write_pages(SOURCE, ranges))), sorted(selected))

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all pdf-authoring checks passed")
