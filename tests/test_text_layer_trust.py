"""
tests/test_text_layer_trust.py - A text layer can be present, word-like, and wrong.

``page_needs_ocr`` answers whether a page's TEXT is usable, from the text. It
catches two shapes and is right about both: too little of it, and debris that is
not language. Neither of the failures here is either of those.

  * **A SUBSTITUTION CIPHER.** A Bank of America statement, native text, 300 DPI
    equivalent, renders perfectly — and extracts as `SEGA&INI`, `an)`,
    `Be$innin$ ba-ance`, `..O. Bo(`. Its subset fonts carry a private
    ``/Differences`` encoding where code 38 means "D" and code 33 means "1",
    and the ToUnicode CMap covers 23 codes out of hundreds, so extraction falls
    back to the raw byte. Every `L` an `&`, every `d` a `)`, **and digits as
    punctuation**. Measured: `page_needs_ocr` trusted 5 of its 8 pages,
    including page 1 — which carries the account summary, so the beginning
    balance was handed to extraction corrupted, on a page nothing re-read.
  * **SOMEBODY ELSE'S OCR OVER A SCAN.** A subpoenaed Chase copy, re-scanned,
    77% image, with a text layer in Helvetica and Arial: `TIIIS ITE~l IS l'AIIT
    OF A STA'l'E~IENT COl'Y IIEQlJEST`, one originator id as both `1060013402`
    and `1860813402`. Word-like ratio **0.70** — above the 0.60 bar. Trusted.
    Twenty-seven amounts on that account have been corrected by hand since.

No content test can see either: the first is language with the letters swapped,
the second is language with the digits wrong. Both are plain in the document's
STRUCTURE, which is why the verdict is read from fonts and image coverage —
the same argument as finding a Bates number by pattern rather than asking a
model. The two compose with ``page_needs_ocr``; either sends a page to vision.

Run:  venv/Scripts/python.exe tests/test_text_layer_trust.py
"""
import glob
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import pymupdf  # noqa: E402
from services.pdf_service import (  # noqa: E402
    _OCR_LAYER_MIN_IMAGE, _SCRAMBLE_MIN_CODES, _SCRAMBLE_MIN_INNER_PUNCT,
    foreign_ocr_layer, inner_punct_rate, page_needs_ocr, scrambled_encoding,
    untrustworthy_text_layer,
)

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, got):
    check(name, bool(got), True)


# ── A stand-in document, so the unit tests need no PDF on disk ───────────
#
# test_data/ is gitignored (187 MB of client productions), so nothing here may
# depend on it. The real specimens are exercised at the end, when present.
class FakePage:
    def __init__(self, text, images=(), width=612, height=792):
        self._text = text
        self.rect = pymupdf.Rect(0, 0, width, height)
        self._images = images

    def get_text(self, *_a, **_k):
        return self._text

    def get_image_info(self, *_a, **_k):
        return [{"bbox": tuple(b)} for b in self._images]


class FakeDoc:
    """Enough of a pymupdf document for the verdict: fonts, pages, xrefs."""

    def __init__(self, pages, fonts=(), objects=None):
        self._pages = pages
        self._fonts = fonts
        self._objects = objects or {}
        self.page_count = len(pages)

    def load_page(self, index):
        return self._pages[index]

    def get_page_fonts(self, _index):
        return self._fonts

    def xref_object(self, xref):
        return self._objects[xref]


def differences(pairs):
    """A /Differences array: (code, 'uniXXXX') pairs, as a font object would hold."""
    body = " ".join("%d /%s" % (code, name) for code, name in pairs)
    return "<< /Type /Font /Subtype /Type1 /Encoding << /Differences [ %s ] >> >>" % body


# A private encoding of the shape the real statement uses: code 38 ("&") spells
# "D", code 36 ("$") spells "R", code 33 ("!") spells "1".
SCRAMBLED_FONT = differences([
    (33, "uni0031"), (36, "uni0052"), (38, "uni0044"), (41, "uni0037"),
    (42, "uni0039"), (46, "uni002F"), (64, "uni0036"),
])
# An ordinary subset font: every code spells the character it already is.
HONEST_FONT = differences([(65, "uni0041"), (66, "uni0042"), (67, "uni0043")])

CIPHERED = ("Be$innin$ ba-ance on May 28 2020\n"
            "Deposits an) other a))itions\nMAR&ENA JO SEGA&INI\n..O. Bo( 25118\n"
            "TEXAS OAG DES:TX48000 INDN:SEGA&INI MAR&ENA JO 57 .92\n") * 3
CLEAN = ("Beginning balance on May 28, 2020\n"
         "Deposits and other additions\nMARLENA JO SEGALINI\nP.O. Box 25118\n"
         "TEXAS OAG DES:TX48000 INDN:SEGALINI MARLENA JO 578.92\n") * 3


print("1. the symptom: punctuation inside a word")
# The property, not a magic number: this fixture is deliberately denser in
# cipher artifacts than a real page, which carries clean prose around them.
# Measured on the real specimens: 6.2 to 13.4 per 1k, against a 2.0 floor.
check_true("the cipher shows it, well clear of the floor",
           inner_punct_rate(CIPHERED) >= 4 * _SCRAMBLE_MIN_INNER_PUNCT)
check("clean prose does not, at all", inner_punct_rate(CLEAN), 0.0)
check("and empty text is not a signal", inner_punct_rate(""), 0.0)

print("")
print("2. the structure: a code that spells a different character")
doc = FakeDoc([FakePage(CIPHERED)], fonts=[(1, "", "Type1", "AAAADW+Connections")],
              objects={1: SCRAMBLED_FONT})
check("every conflicting code is counted", scrambled_encoding(doc, 0), 7)
honest = FakeDoc([FakePage(CLEAN)], fonts=[(1, "", "Type1", "AAAADW+Connections")],
                 objects={1: HONEST_FONT})
check("a subset font that spells itself is not a conflict", scrambled_encoding(honest, 0), 0)
bare = FakeDoc([FakePage(CLEAN)], fonts=[(1, "", "Type1", "Helvetica")],
               objects={1: "<< /Type /Font /BaseFont /Helvetica >>"})
check("nor is a font with no /Differences at all", scrambled_encoding(bare, 0), 0)

print("")
print("3. the verdict needs BOTH, because a private encoding alone is ordinary")
check_true("structure + symptom is the cipher",
           "scrambled" in (untrustworthy_text_layer(doc) or ""))
# Plenty of generators subset with a private encoding and ship a ToUnicode that
# covers it. Flagging those would send every one of their pages to vision.
structure_only = FakeDoc([FakePage(CLEAN)], fonts=[(1, "", "Type1", "X+Sub")],
                         objects={1: SCRAMBLED_FONT})
check("structure without the symptom is left alone",
      untrustworthy_text_layer(structure_only), None)
symptom_only = FakeDoc([FakePage(CIPHERED)], fonts=[(1, "", "Type1", "Helvetica")],
                       objects={1: "<< /Type /Font /BaseFont /Helvetica >>"})
check("and the symptom without the structure is too",
      untrustworthy_text_layer(symptom_only), None)
check("the floor is above one or two remapped codes", _SCRAMBLE_MIN_CODES > 1, True)

print("")
print("4. somebody else's OCR over a scan")
FULL_PAGE = (0, 0, 612, 792)
scan = FakeDoc([FakePage(CLEAN, images=[FULL_PAGE])],
               fonts=[(1, "", "Type1", "Helvetica"), (2, "", "Type1", "Arial")],
               objects={1: "<<>>", 2: "<<>>"})
check_true("image-dominated, text present, base-14 faces",
           foreign_ocr_layer(scan, 0, CLEAN))
check_true("so the document's layer is distrusted",
           "outside OCR tool" in (untrustworthy_text_layer(scan) or ""))

# Each of the three facts is necessary. A bank's own generator embeds its brand
# fonts, so a branded face over an image is a generated page, not a scan.
branded = FakeDoc([FakePage(CLEAN, images=[FULL_PAGE])],
                  fonts=[(1, "", "Type1", "AAAADW+Connections_CZEX0A60")],
                  objects={1: "<<>>"})
check("a brand font over an image is not an OCR layer",
      foreign_ocr_layer(branded, 0, CLEAN), False)
textless = FakeDoc([FakePage("x", images=[FULL_PAGE])],
                   fonts=[(1, "", "Type1", "Helvetica")], objects={1: "<<>>"})
check("a page with no usable text is already going to vision",
      foreign_ocr_layer(textless, 0, "x"), False)
small_img = FakeDoc([FakePage(CLEAN, images=[(0, 0, 100, 100)])],
                    fonts=[(1, "", "Type1", "Helvetica")], objects={1: "<<>>"})
check("and a logo on a text page is not a scan",
      foreign_ocr_layer(small_img, 0, CLEAN), False)
check("the image bar is a majority of the page", _OCR_LAYER_MIN_IMAGE >= 0.5, True)

print("")
print("4a. a font that NAMES itself an OCR layer is a declaration")
# HiddenHorzOCR is the invisible face Acrobat writes over a scan; GlyphLessFont
# is Tesseract's. Found on a Chase Amazon production reading "Mcl1age your
# account online" and "Do.vnkm Ille Chase Motie app txlay" — at 0.93 word-like,
# because the garbage is made of ordinary letters. The base-14 test MISSED it,
# for the pointed reason that this font is not one of the base fourteen.
for marker in ("HiddenHorzOCR", "GlyphLessFont", "OCRmyPDF-Invisible2"):
    declared = FakeDoc([FakePage(CLEAN)], fonts=[(1, "", "Type1", marker)],
                       objects={1: "<<>>"})
    check_true("%s is taken at its word" % marker,
               foreign_ocr_layer(declared, 0, CLEAN))

# No corroboration required: the font cannot be wrong about what it is, so it
# needs neither image coverage nor base-14 company.
no_image = FakeDoc([FakePage(CLEAN)],
                   fonts=[(1, "", "Type1", "HiddenHorzOCR"),
                          (2, "", "Type1", "AAAADW+Connections_CZEX0A60")],
                   objects={1: "<<>>", 2: "<<>>"})
check_true("declared, with no image coverage and a brand font beside it",
           foreign_ocr_layer(no_image, 0, CLEAN))
check("but a page with no usable text is still already going to vision",
      foreign_ocr_layer(FakeDoc([FakePage("x")], fonts=[(1, "", "Type1", "HiddenHorzOCR")],
                                objects={1: "<<>>"}), 0, "x"), False)

print("")
print("4b. a declaration needs ONE page; an inference needs a majority")
# An OCR tool writes its font on the pages it processed, so one is proof the
# layer is not the generator's.
one_declared = FakeDoc(
    [FakePage(CLEAN)] * 8,
    fonts=[(1, "", "Type1", "Helvetica")], objects={1: "<<>>"})
one_declared.get_page_fonts = lambda i: (
    [(1, "", "Type1", "HiddenHorzOCR")] if i == 3 else [(1, "", "Type1", "Helvetica")])
check_true("one declared page in eight condemns the layer",
           "named itself" in (untrustworthy_text_layer(one_declared) or ""))

# The base-14 inference CAN be wrong, and the majority is what protects a mixed
# document: a real 396-page production carries 16 genuine scans inside
# otherwise native pages, and condemning it on those would turn 28 vision calls
# into 396.
one_inferred = FakeDoc(
    [FakePage(CLEAN, images=[FULL_PAGE])] + [FakePage(CLEAN)] * 7,
    fonts=[(1, "", "Type1", "Helvetica")], objects={1: "<<>>"})
one_inferred.load_page = lambda i: one_inferred._pages[i]
check("one inferred page in eight does not",
      untrustworthy_text_layer(one_inferred), None)

print("")
print("5. the verdict is per DOCUMENT, so a short page cannot clear it")
# On the real specimen the per-page text test passed 5 of 8 pages. The fonts are
# document-wide and a production is scanned in one pass, so one page's evidence
# condemns the layer.
mixed = FakeDoc([FakePage(CIPHERED), FakePage("Page 5 of 8"), FakePage(CIPHERED)],
                fonts=[(1, "", "Type1", "X+Sub")], objects={1: SCRAMBLED_FONT})
check_true("a clean-looking page does not rescue the document",
           untrustworthy_text_layer(mixed) is not None)
check("and page_needs_ocr is untouched — still the per-page TEXT test",
      (page_needs_ocr("Page 5 of 8"), page_needs_ocr(CLEAN)), (True, False))
check("an empty document is not a verdict", untrustworthy_text_layer(FakeDoc([])), None)

print("")
print("6. the real specimens, when they are on this machine")
# test_data/ is gitignored, so this section is informational. A skip is printed
# rather than passed over in silence: a check that did not run must never read
# as a check that passed.
SPECIMENS = {
    "bad_ocr_01.pdf": "scrambled",
    "ocr_diagnosis/682f61b8-4d7d-4d46-8ce8-1d23a9c2d6c1.pdf": "scrambled",
    "ocr_diagnosis/473ec3e6-ebfd-45d2-9e4c-9aa761b83d6b.pdf": "outside OCR tool",
    "ocr_diagnosis/cccf204b-fc3d-4801-8f65-612ded92cc07.pdf": "named itself",
    "ocr_diagnosis/86bedeb3-c60a-44bb-ba4e-5cea61abd518.pdf": None,
    "ocr_diagnosis/50dac4e4-732e-4ec9-bfb5-63dfee66c43b.pdf": None,
    "ocr_diagnosis/a564bd8a-b2ee-4c17-8511-abc00252f1ab.pdf": None,
    "ocr_diagnosis/Chase x6026 2026.08.pdf": None,
    "ocr_diagnosis/Chase x6026 2026.09.pdf": None,
}
root = os.path.join(ROOT, "test_data")
if not os.path.isdir(root):
    print("  SKIPPED - test_data/ is not on this machine (it is gitignored)")
else:
    for name, expect in SPECIMENS.items():
        path = os.path.join(root, name)
        if not os.path.exists(path):
            print("  SKIPPED - %s is not here" % name)
            continue
        doc = pymupdf.open(path)
        try:
            verdict = untrustworthy_text_layer(doc)
        finally:
            doc.close()
        label = os.path.basename(name)[:40]
        if expect is None:
            check("%s: layer trusted" % label, verdict, None)
        else:
            check("%s: %s" % (label, expect), expect in (verdict or ""), True)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
