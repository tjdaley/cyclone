"""
app/services/pdf_service.py - PDF text extraction with LLM vision fallback.

Uses PyMuPDF for native text extraction from searchable PDFs. For pages that
yield no text (image-only scanned pages), renders the page to an enhanced
image and uses the LLM's multimodal vision capability for OCR.
"""
import base64
import io
import re
import time
from typing import Any, Optional
from PIL import ImageFile, Image
import pymupdf

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# ── IS THIS PAGE'S TEXT LAYER WORTH USING? ───────────────────────────────────
#
# One definition, here, because three had drifted: this module OCR'd anything
# under 20 characters, `source_document_service` called 100 characters "has
# text", and the splitting bench required 100 characters AND that they be
# language. The most permissive of the three was the one deciding whether to
# OCR, so it won every time, and the cost was total.
#
# MEASURED, on a real 396-page scanned production: 344 of its pages carried
# between 20 and 99 characters, cleared the old bar, and were never rendered.
# What those characters were is the whole story:
#
#     '(2026.08.25) SShahnawaz 003067'
#
# The Bates stamp, applied digitally as a text object on top of an image. Thirty
# characters, and every one of them a real word or number -- so no measure of
# text QUALITY can reject it. The page is a scan; the only text on it is an
# annotation somebody added afterwards.
#
# A statement sent through that path produced one model call, 49 output tokens,
# and the honest answer that there is no account statement in it. Nothing
# failed. Nothing was logged. The ingest reported success.
#
# So the length bar is 100, which sits in an empty region: a stamp is about 30
# characters, a real statement page is thousands, and nothing observed falls
# between. The word-like test stays as well, for the other shape -- a page
# dense with OCR debris, which is long enough to pass a length test and is not
# language.
_MIN_TEXT_LENGTH = 100

# Below this share of word-like tokens, a page has text but not language.
_MIN_WORDLIKE = 0.60

# A token that is a word, a number, a date, or money. OCR debris is not.
_WORDLIKE = re.compile(
    r"^[A-Za-z]{2,}$"
    r"|^[A-Za-z]$"
    r"|^\$?[\d,]+(\.\d+)?$"
    r"|^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}$"
    r"|^\d{1,4}$"
)


def wordlike_ratio(text: str) -> float:
    """
    Share of tokens that are language rather than OCR debris.

    NOT the share of non-ASCII characters, which is blind to this: measured on a
    real pile, an unusable ATM-receipt scan scored 0.00 "garbage" under that
    rule, because OCR noise is entirely ordinary ASCII.
    """
    tokens = [t.strip(".,;:!?()[]{}\"'") for t in text.split()]
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if _WORDLIKE.match(t)) / len(tokens)


#: A punctuation character sitting BETWEEN two letters. Real prose has almost
#: none of these; a scrambled encoding is full of them, because the mapping
#: turns letters into punctuation mid-word — "SEGA&INI", "an)", "Be$innin$".
_INNER_PUNCT = re.compile(r"(?<=[A-Za-z])[$&)(!%*@#](?=[A-Za-z])")

#: Conflicting codes on one page before its font is called scrambled. Not zero:
#: subsetting legitimately remaps a code or two, and a lone remap is a quirk
#: rather than a cipher. Measured on the real specimen: 23 to 75 per font.
_SCRAMBLE_MIN_CODES = 4

#: Inner-punctuation per 1,000 characters before the SYMPTOM is taken as
#: present. Measured: 6.2 to 13.4 on affected pages, 0.0 on clean text.
_SCRAMBLE_MIN_INNER_PUNCT = 2.0

#: Share of a page covered by images before it reads as a scan rather than a
#: generated page. Measured: 77% on a re-scanned production copy carrying a
#: foreign OCR layer, 0.3% to 13% on every bank-generated page examined.
_OCR_LAYER_MIN_IMAGE = 0.50

#: The faces an OCR tool writes. A bank's own generator embeds its brand fonts
#: (Connections, ITC Franklin Gothic, Chase's own); Tesseract and Acrobat write
#: the base fourteen. Text in nothing but these, over an image-dominated page,
#: was added by somebody after the fact.
_BASE14_NAMES = ("helvetica", "times", "courier", "arial", "symbol", "zapf")

#: Fonts that NAME THEMSELVES as an OCR layer. `HiddenHorzOCR` is the invisible
#: face Acrobat writes over a scan; `GlyphLessFont` is Tesseract's. This is not
#: an inference — it is the tool declaring what it did, which makes it stronger
#: than every other signal here and worth testing first.
#:
#: Found on a Chase Amazon card production whose text layer reads "Mcl1age your
#: account online", "w,yw.ct)ooe.com/amazoo", "Do.vnkm Ille Chase Motie app
#: txlay" — and which scored **0.93 word-like** on page 1, because the garbage
#: is made of ordinary letters. The base-14 test below missed it precisely
#: because this font is not one of the base fourteen.
#: The tool is worth NAMING, not just detecting. Each marker belongs to one
#: program, so the font attributes a file to the workflow that produced it —
#: which is what tells somebody whose process to change. Both of this firm's
#: were real: Acrobat run over a scanned pile before splitting (a pre-Cyclone
#: habit, done so Egnyte could search the text), and a Tesseract script a
#: litigation assistant generated to split on "Page 1".
_OCR_FONT_MARKERS = {
    "hiddenhorzocr": "Adobe Acrobat",
    "hiddenvertocr": "Adobe Acrobat",
    "glyphless": "Tesseract",
    "ocrmypdf": "OCRmyPDF (Tesseract)",
    "tesseract": "Tesseract",
}


def ocr_tool(doc: Any, page_no: int) -> Optional[str]:
    """Which OCR program wrote this page's text layer, if it named itself."""
    for font in doc.get_page_fonts(page_no):
        face = font[3].split("+")[-1].lower().replace("-", "").replace(" ", "")
        for marker, tool in _OCR_FONT_MARKERS.items():
            if marker in face:
                return tool
    return None

#: Pages sampled for the document-level verdict. The fonts and the scanning are
#: properties of the whole document, so reading every page of a 396-page pile to
#: learn the same fact would cost minutes for nothing.
_VERDICT_SAMPLE_PAGES = 8


def inner_punct_rate(text: str) -> float:
    """Punctuation-inside-a-word, per 1,000 characters. The scrambling symptom."""
    if not text:
        return 0.0
    return 1000.0 * len(_INNER_PUNCT.findall(text)) / len(text)


def scrambled_encoding(doc: Any, page_no: int) -> int:
    """
    Codes this page's fonts remap to a DIFFERENT character than they spell.

    A subset font may carry its own ``/Differences`` encoding where code 38
    ("&") means "D" and code 33 ("!") means "1". That is ordinary, and harmless
    while the font also ships a ToUnicode CMap covering it. The specimen's
    covered 23 codes out of hundreds, so extraction fell back to the raw byte
    and produced a one-to-one substitution cipher: every ``L`` an ``&``, every
    ``d`` a ``)``, every ``g`` a ``$``, and digits as punctuation.

    The glyphs are right — the page RENDERS correctly — so the repair is to read
    the image rather than the text, and the only hard part is noticing.

    :return: Count of conflicting codes; 0 when the encoding is unremarkable.
    """
    conflicts = 0
    for font in doc.get_page_fonts(page_no):
        try:
            obj = doc.xref_object(font[0])
        except Exception:  # noqa: BLE001 — a font we cannot read is not a verdict
            continue
        found = re.search(r"/Differences\s*\[(.*?)\]", obj, re.S)
        if not found:
            continue
        code = None
        for number, name in re.findall(r"(\d+)|/([A-Za-z0-9_.]+)", found.group(1)):
            if number:
                code = int(number)
                continue
            if code is None:
                continue
            # A glyph named uniXXXX states its own Unicode outright, which is
            # what makes the comparison exact rather than a guess.
            if name.startswith("uni") and len(name) >= 7:
                try:
                    spelled = chr(int(name[3:7], 16))
                except ValueError:
                    spelled = None
                if spelled and 33 <= code < 127 and spelled != chr(code):
                    conflicts += 1
            code += 1
    return conflicts


def foreign_ocr_layer(doc: Any, page_no: int, text: str) -> bool:
    """
    Is this page a SCAN carrying text somebody else's OCR put there?

    Three facts together, none sufficient alone: the page is mostly image, it
    has a text layer worth trusting by the ordinary test, and that text is set
    in nothing but the base-fourteen faces an OCR tool writes.

    It matters because such a layer is **plausibly wrong**. Measured on a
    re-scanned Chase production copy: ``TIIIS ITE~l IS l'AIIT OF A STA'l'E~IENT``,
    one originator id rendered as both ``1060013402`` and ``1860813402``, and a
    word-like ratio of 0.70 — above the 0.60 bar, so trusted. Twenty-seven
    amounts on that account have since been corrected by hand. No content test
    can catch it: a transposed digit reads as perfect language.
    """
    import pymupdf  # noqa: PLC0415

    if page_needs_ocr(text):
        return False          # already going to vision; nothing to add
    faces = [f[3].split("+")[-1].lower().replace("-", "").replace(" ", "")
             for f in doc.get_page_fonts(page_no)]
    if not faces:
        return False

    # A FONT THAT NAMES ITSELF AN OCR LAYER IS A DECLARATION, not a deduction,
    # so it needs no corroboration from image coverage. Tested first because it
    # is the only signal here that cannot be wrong about what it is.
    if ocr_tool(doc, page_no):
        return True

    page = doc.load_page(page_no)
    area = abs(page.rect)
    if not area:
        return False
    covered = sum(abs(pymupdf.Rect(i["bbox"]) & page.rect) for i in page.get_image_info())
    if covered / area < _OCR_LAYER_MIN_IMAGE:
        return False
    return all(any(base in face for base in _BASE14_NAMES) for face in faces)


def untrustworthy_text_layer(doc: Any) -> Optional[str]:
    """
    Should this document's text layer be disbelieved and the pages re-read?

    **A DOCUMENT-LEVEL VERDICT, deliberately.** Both failures are properties of
    the whole file — one bad font applies throughout, and a production is
    scanned in one pass — so judging page by page gets the short pages wrong.
    On the real specimen the per-page text test cleared 5 of 8 pages, including
    page 1, which carries the account summary with its digits corrupted: the
    beginning balance, on a page nothing re-read.

    This does **not** replace ``page_needs_ocr``, which remains the one
    definition of whether a page's TEXT is usable. It answers a different
    question — whether the text layer can be believed at all — from the
    document's structure rather than its content, the same way Bates numbers
    and account numbers are found by pattern rather than asked of a model. The
    two compose: either one sends a page to vision.

    :return: A short reason for the log, or None when the layer is fine.
    """
    pages = doc.page_count
    if not pages:
        return None
    step = max(1, pages // _VERDICT_SAMPLE_PAGES)
    sampled = list(range(0, pages, step))[:_VERDICT_SAMPLE_PAGES]

    worst_conflicts, inner, chars = 0, 0, 0
    declared_ocr, inferred_ocr = 0, 0
    tools: set[str] = set()
    for index in sampled:
        try:
            text = doc.load_page(index).get_text().strip()
        except Exception:  # noqa: BLE001
            continue
        worst_conflicts = max(worst_conflicts, scrambled_encoding(doc, index))
        inner += len(_INNER_PUNCT.findall(text))
        chars += len(text)
        if not foreign_ocr_layer(doc, index, text):
            continue
        tool = ocr_tool(doc, index)
        if tool:
            declared_ocr += 1
            tools.add(tool)
        else:
            inferred_ocr += 1

    rate = 1000.0 * inner / chars if chars else 0.0
    # STRUCTURE AND SYMPTOM TOGETHER. A private encoding on its own is ordinary;
    # one whose consequence is visible in the extracted text is the cipher.
    if worst_conflicts >= _SCRAMBLE_MIN_CODES and rate >= _SCRAMBLE_MIN_INNER_PUNCT:
        return ("font encoding is scrambled (%d remapped codes, %.1f inner-punctuation "
                "per 1k chars) — the text layer is a substitution cipher" % (worst_conflicts, rate))
    # A DECLARATION NEEDS ONE PAGE; AN INFERENCE NEEDS A MAJORITY. A font named
    # HiddenHorzOCR cannot be wrong about what it is, and an OCR tool writes it
    # on the pages it processed — so one is proof the layer is not the
    # generator's. The base-14 inference CAN be wrong, and a majority is what
    # protects the mixed document: a real 396-page production carries 16
    # genuine scans inside otherwise native pages, and condemning it on those
    # would turn 28 vision calls into 396.
    if declared_ocr:
        return ("text layer was written by %s, which named itself in the fonts (%d of %d "
                "sampled pages) — its digits cannot be trusted"
                % (" and ".join(sorted(tools)) or "an OCR tool", declared_ocr, len(sampled)))
    if inferred_ocr and inferred_ocr >= len(sampled) / 2:
        return ("text layer looks added by an outside OCR tool over a scan (%d of %d sampled "
                "pages, base-14 fonts only) — its digits cannot be trusted"
                % (inferred_ocr, len(sampled)))
    return None


def text_layer_verdict(pdf_bytes: bytes) -> Optional[str]:
    """
    ``untrustworthy_text_layer`` for a caller holding bytes rather than a document.

    The survey job and the bench reach this from page features, where there is
    no open document to pass. Opening one costs nothing against the verdict's
    own work — it samples a handful of pages and reads their fonts.

    **Returns None rather than raising on an unopenable file.** A verdict is an
    opinion about a text layer; a file that cannot be opened has no text layer
    to hold an opinion about, and that is `page_texts`' error to report.

    :param pdf_bytes: Raw PDF file content.
    :return: A short reason the layer cannot be believed, or None.
    """
    import pymupdf  # noqa: PLC0415

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("pdf_service.text_layer_verdict: could not open the PDF (%s)",
                       type(e).__name__)
        return None
    try:
        return untrustworthy_text_layer(doc)
    finally:
        doc.close()


def page_needs_ocr(text: str) -> bool:
    """
    Should this page be rendered and read, rather than trusted as it stands?

    Two ways a text layer fails, and they look nothing alike:

    - **Too little of it.** A scan whose only text is a Bates stamp. The
      characters are perfect; there are thirty of them.
    - **Not language.** A page dense with OCR debris, long enough to clear any
      length test and meaning nothing.

    :param text: The page's raw text layer.
    :return: True if the page should go to vision.
    """
    stripped = text.strip()
    return len(stripped) < _MIN_TEXT_LENGTH or wordlike_ratio(stripped) < _MIN_WORDLIKE

# Control characters to strip from extracted text, keeping tab, newline, and
# carriage return. A NUL in particular is fatal downstream: Postgres rejects
# one in text and jsonb ("unsupported Unicode escape sequence", SQLSTATE
# 22P05), so a single one in an exhibit page fails the whole ingest. Badly
# encoded PDFs — scanned exhibits, forms flattened by odd tooling — produce
# them routinely.
_CONTROL_CHARS = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CONTROL_CHARS[0x7F] = None


# A statement's own pagination, restarted. Two spellings cover nearly every
# statement seen so far: "Page 1 of 8", and a bare "Page 1".
#
# THE WORD BOUNDARY IS THE WHOLE PATTERN. Without it, "PAGE 1" also matches
# inside "PAGE 10", "PAGE 19" and "PAGE 142" — so a single long statement reads
# as dozens of first pages and every upload gets warned about. `\b` after the
# digit fails on "10" because a digit is a word character, which is exactly the
# distinction wanted.
_PAGE_ONE = re.compile(r"\bpage\s+1\b(?!\d)", re.I)

# The most pages a survey reads. It is a warning, not an extraction: a document
# past this is already far beyond the threshold that triggers the warning, so
# reading more would change nothing but the number quoted.
_SURVEY_CAP = 250

# Wide enough to recognise a letterhead, a logo and a date, which is the whole
# decision on the splitting bench. Measured: 24.6 ms and 68 KB per page, so a
# twenty-page screen is half a second and needs no stored cache.
_THUMBNAIL_WIDTH = 450


def coalesce_pages(pages) -> list[tuple[int, int]]:
    """
    Turn a set of selected page numbers into contiguous 1-based ranges.

    The splitting bench's client holds a *selection* — the pages an operator
    clicked — and the database holds *ranges*. This is the single place that
    conversion happens, so the two cannot drift: a selection converted one way
    on the way in and another way on the way out produces a document whose
    stored extent is not the pages it contains.

    Duplicates collapse and order is irrelevant on the way in, because a
    selection is a set: clicking page 12 twice is one page, and the operator
    clicking 15 before 12 still means pages 12 and 15. The ranges come back
    ascending, which is the reading order of a document assembled from one
    pile.

    :param pages: Any iterable of 1-based page numbers.
    :return: ``[(first, last), ...]``, ascending and non-overlapping.
    :rtype: list[tuple[int, int]]
    :raises ValueError: If a page number is not a positive integer.
    """
    ordered = sorted({int(p) for p in pages})
    if any(p < 1 for p in ordered):
        raise ValueError("Page numbers are 1-based; got %r" % (min(ordered),))

    ranges: list[tuple[int, int]] = []
    for page in ordered:
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], page)
        else:
            ranges.append((page, page))
    return ranges


def _sanitize(text: str) -> str:
    """
    Strip characters that cannot survive a round trip through the database.

    Removes control characters and any unpaired surrogate, which Postgres
    rejects for the same reason. Extraction is lossy on a mangled page either
    way; losing the unstorable bytes is better than losing the document.

    :param text: Raw text as extracted from the PDF.
    :type text: str
    :return: Text safe to persist.
    :rtype: str
    """
    cleaned = text.translate(_CONTROL_CHARS)
    # Drops lone surrogates (\ud800-\udfff), which are equally unstorable.
    return cleaned.encode("utf-8", "ignore").decode("utf-8", "ignore")

# Stands in for a page whose text could not be read at all — the text layer was
# too thin to use and the vision fallback failed. Deliberately conspicuous: it
# travels in raw_text, so it survives into the stored record, and any extractor
# reading downstream sees plainly that something is missing rather than
# inferring a blank page.
_UNREADABLE_MARKER = "[[PAGE COULD NOT BE READ — text extraction and OCR both failed]]"

_VISION_OCR_PROMPT = (
    "Extract ALL text from this image of a legal document page. "
    "Preserve the original formatting, indentation, and numbering as closely "
    "as possible. Output the text as markdown. Do not summarize or paraphrase."
)


class PDFService:
    """
    Extract text from a PDF file.

    Searchable pages use PyMuPDF's native text layer. Image-only pages are
    rendered to 300 DPI, enhanced for contrast/sharpness, and sent to the
    LLM's vision endpoint for extraction.
    """

    def survey(self, pdf_bytes: bytes, max_pages: int = _SURVEY_CAP) -> dict:
        """
        How long a PDF is, and how many statements it looks like it holds.

        Cheap on purpose: the page count is free and the text comes straight off
        the text layer with **no OCR fallback**, so this runs inside the upload
        request where `extract_text` could not. An image-only page yields
        nothing and is simply not counted, which is the honest answer — the
        survey is a warning, and a warning that had to render pages would cost
        more than the mistake it prevents.

        The signal is the page-1 marker. Almost every statement prints its own
        pagination, and a document holding twenty-four months restarts at one
        twenty-four times; nothing else on a statement behaves that way. Counted
        by **page**, not by occurrence — a statement that prints "Page 1 of 8"
        in both the header and the footer is still one statement.

        :param max_pages: Stop reading after this many. A survey is not an
            extraction, and the count it reports is a lower bound anyway.
        :return: ``{"pages", "scanned", "first_pages"}`` — total pages, pages
            actually read, and how many of them look like a statement's first.
        :rtype: dict
        """
        import pymupdf

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            LOGGER.error("pdf_service.survey: failed to open PDF: %s", str(e))
            raise ValueError("Could not open PDF — file may be corrupted or password-protected") from e

        try:
            total = doc.page_count
            scanned = min(total, max(1, max_pages))
            first_pages = 0
            for index in range(scanned):
                try:
                    text = doc.load_page(index).get_text() or ""
                except Exception:  # noqa: BLE001 — one bad page must not fail a warning
                    continue
                if _PAGE_ONE.search(text):
                    first_pages += 1
        finally:
            doc.close()

        LOGGER.info(
            "pdf_service.survey: %d page(s), read %d, %d look like a statement's first page",
            total, scanned, first_pages,
        )
        return {"pages": total, "scanned": scanned, "first_pages": first_pages}

    def extract_text(self, pdf_bytes: bytes, page_markers: bool = False) -> str:
        """
        Extract all text from a PDF, page by page.

        :param pdf_bytes: Raw PDF file content.
        :type pdf_bytes: bytes
        :param page_markers: Prefix each page with ``<<<PAGE n>>>``. Off by
            default because the pleading and discovery extractors were written
            against unmarked text. Statement extraction turns it on: an exhibit
            has to cite the page a line was printed on, and the model cannot
            report a page number it was never shown. The delimiter is chosen to
            be unmistakable — nothing on a bank statement looks like it.
        :type page_markers: bool
        :return: Full extracted text, pages separated by double newlines.
        :rtype: str
        :raises ValueError: If the PDF cannot be opened.
        """
        import pymupdf

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            LOGGER.error("pdf_service.extract_text: failed to open PDF: %s", str(e))
            raise ValueError("Could not open PDF — file may be corrupted or password-protected") from e

        pages: list[str] = []
        page_num = 0
        ocr_pages = 0
        started = time.monotonic()

        # A TEXT LAYER CAN BE PRESENT, WORD-LIKE, AND WRONG. Two real shapes:
        # a subset font whose encoding is a substitution cipher, and somebody
        # else's OCR laid over a scan. Both read as language, so no content test
        # sees them; both are visible in the document's structure. When the
        # verdict comes back, every page is re-read from the image — the pages
        # render correctly, it is only the text that lies.
        distrusted = untrustworthy_text_layer(doc)
        if distrusted:
            # The page count is in the line because it IS the cost: one vision
            # call per page, where a trusted layer costs none. Measured on a
            # real 396-page production carrying a foreign OCR layer, this turns
            # 28 calls into 396. Bounding the volume belongs at the entry point
            # that accepted the document — this says plainly what is about to
            # happen, so the bill is never a surprise.
            LOGGER.warning(
                "pdf_service.extract_text: distrusting the text layer and reading all %d "
                "page(s) by OCR — %s", doc.page_count, distrusted)

        page: pymupdf.Page
        for page in doc:
            page_num += 1
            text = page.get_text().strip()  # type: ignore[union-attr]
            if not distrusted and not page_needs_ocr(text):  # type: ignore[union-attr]
                LOGGER.debug("pdf_service: page %s text extraction ok (%s chars)", page_num, len(text))  # type: ignore[union-attr]
                pages.append(text)  # type: ignore[union-attr]
            else:
                LOGGER.debug(  # type: ignore[union-attr]
                    "pdf_service: page %s needs vision (%s chars, %.2f word-like)",
                    page_num, len(text), wordlike_ratio(text))
                ocr_pages += 1
                ocr_text = self._vision_extract(page)
                pages.append(ocr_text)

            if page_markers:
                pages[-1] = "<<<PAGE %d>>>\n%s" % (page_num, pages[-1])

        doc.close()
        # At INFO because this is the dominant cost of an ingest: one vision
        # call per image-only page. Without it a slow upload looks like a hang.
        raw = "\n\n".join(pages)
        text = _sanitize(raw)
        dropped = len(raw) - len(text)
        LOGGER.info(
            "pdf_service.extract_text: %s pages (%s needed OCR) in %.1fs, %s unstorable chars removed",
            page_num, ocr_pages, time.monotonic() - started, dropped,
        )
        return text

    def ask_page(self, pdf_bytes: bytes, page_number: int, prompt: str) -> Optional[str]:
        """
        Put one question to the model about one rendered page.

        ``extract_text`` only falls back to vision when a page has almost no
        text layer, which is the right rule for reading a document but leaves a
        real gap: a page can be dense with text and still carry its most
        important word — the institution's name — only inside the letterhead
        graphic. Nothing is wrong with the text layer. The name was never in it.

        This is the narrow remedy: one page, one question, asked only when
        something downstream found the answer missing.

        :param pdf_bytes: Raw PDF file content.
        :param page_number: 1-based page of the PDF.
        :param prompt: What to ask about the rendered page.
        :return: The model's answer, or None when the page cannot be read.
        :rtype: Optional[str]
        """
        import pymupdf

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("pdf_service.ask_page: could not open PDF: %s", str(e))
            return None
        try:
            if not 1 <= page_number <= doc.page_count:
                return None
            # A failed lookup must never fail the ingest around it.
            answer = self._vision_extract(doc[page_number - 1], prompt=prompt)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("pdf_service.ask_page: page %s failed: %s", page_number, str(e))
            return None
        finally:
            doc.close()
        return _sanitize(answer).strip() or None

    def page_texts(self, pdf_bytes: bytes) -> list[str]:
        """
        The text layer of each page, separately, with **no OCR fallback**.

        ``extract_text`` joins the pages and falls back to a vision call for any
        page with almost no text layer, which is right for reading a document
        and wrong for two callers that need to know what the text layer itself
        contains: the upload survey, and the splitting bench's page badges.

        Falling back here would make opening a bench session on a 396-page pile
        a minutes-long billable operation, one vision call per image-only page,
        to decorate thumbnails. A page with no text layer gets no badges, which
        is the honest answer — and it is the measurement that routes the pile to
        OCR in the first place.

        Sanitized like everything else this module returns: a NUL from a badly
        encoded exhibit page makes Postgres reject the whole row (22P05), and
        these strings are stored.

        :param pdf_bytes: Raw PDF file content.
        :return: One string per page, in order.
        :rtype: list[str]
        :raises ValueError: If the PDF cannot be opened.
        """
        import pymupdf

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            LOGGER.error("pdf_service.page_texts: failed to open PDF: %s", str(e))
            raise ValueError("Could not open PDF — file may be corrupted or password-protected") from e

        try:
            out = []
            for index in range(doc.page_count):
                try:
                    out.append(_sanitize(doc.load_page(index).get_text() or ""))
                except Exception:  # noqa: BLE001 — one bad page must not lose the rest
                    LOGGER.warning("pdf_service.page_texts: page %s unreadable", index + 1)
                    out.append("")
            return out
        finally:
            doc.close()

    def render_page(
        self,
        pdf_bytes: bytes,
        page_number: int,
        width: int = _THUMBNAIL_WIDTH,
        fmt: str = "jpeg",
    ) -> Optional[bytes]:
        """
        Render one page to an image for a person to look at.

        Deliberately **not** ``_vision_extract``'s rendering, which is 300 DPI,
        greyscale, contrast 2.0 and sharpness 1.5 — tuned for a model reading
        individual characters. Those settings make a page *harder* for a human
        to recognise, and recognition is the entire job on the splitting bench:
        a letterhead, a logo, a date, the shape of a form. Enhancing for OCR
        here would be optimising for the wrong reader.

        Measured on a 396-page production: **24.6 ms and 68 KB per page at
        450px**, so a twenty-page screen renders in half a second. That is what
        lets the bench render on demand and store nothing — a thumbnail is
        derived and disposable, and caching it would buy milliseconds in
        exchange for a cache the other node cannot see, a storage cost line,
        and a staleness rule for when a page is re-OCR'd.

        :param pdf_bytes: Raw PDF file content.
        :param page_number: 1-based page of the PDF.
        :param width: Target width in pixels; the aspect ratio is preserved.
        :param fmt: ``jpeg`` (default) or ``png``. JPEG because a scanned page
            is a photograph and compresses like one. PNG for a native-text
            page, where thin rules pick up ringing.
        :return: Encoded image bytes, or None when the page does not exist.
        :rtype: Optional[bytes]
        :raises ValueError: If the PDF cannot be opened, or ``fmt`` is neither.
        """
        import pymupdf

        if fmt not in ("jpeg", "png"):
            raise ValueError("fmt must be 'jpeg' or 'png', not %r" % (fmt,))
        if width < 1:
            raise ValueError("width must be positive")

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            LOGGER.error("pdf_service.render_page: failed to open PDF: %s", str(e))
            raise ValueError("Could not open PDF — file may be corrupted or password-protected") from e

        try:
            # A page number out of range is the caller asking for something that
            # is not there, not a broken file. The grid already knows the page
            # count, so this is a 404 and never a 500.
            if not 1 <= page_number <= doc.page_count:
                return None
            page = doc.load_page(page_number - 1)
            zoom = width / page.rect.width if page.rect.width else 1.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            return pix.tobytes(fmt)
        except Exception as e:  # noqa: BLE001 — one bad page must not fail the grid
            LOGGER.warning("pdf_service.render_page: page %s failed: %s", page_number, str(e))
            return None
        finally:
            doc.close()

    def write_pages(self, pdf_bytes: bytes, ranges: list[tuple[int, int]]) -> bytes:
        """
        Write a new PDF holding only the given page ranges of this one.

        The ranges are 1-based and inclusive, matching ``physical_page_number``
        and every other page number in this codebase, and they are copied **in
        the order given** rather than sorted. That order is the document's
        reading order — ``document_source_pages.seq`` — and on the splitting
        bench it is what the operator asserted by selecting pages in sequence.
        Sorting would quietly overrule them.

        Overlapping ranges are **refused**. One document may not hold the same
        page twice: it duplicates a page of evidence inside one exhibit, and the
        exclusion constraint on ``document_source_pages`` would reject it at the
        database with a far worse message. Non-adjacent ranges are ordinary — a
        document interrupted mid-way by unrelated pages is the case the bench
        exists to express.

        Nothing is added to the output: no stamp, no metadata, no annotation.
        The new document's metadata is empty rather than inherited, because the
        parent's title and author describe the parent — a 396-page production —
        and would read as a description of this child. Page annotations DO
        travel, which is deliberate: a Bates stamp applied as an annotation
        belongs to the page.

        :param pdf_bytes: Raw PDF file content of the source.
        :param ranges: ``[(first, last), ...]``, 1-based inclusive.
        :return: The new PDF.
        :rtype: bytes
        :raises ValueError: If the source cannot be opened, or the ranges are
            empty, reversed, out of bounds, or overlapping.
        """
        import pymupdf

        if not ranges:
            raise ValueError("No page ranges given — a document must hold at least one page")

        try:
            src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            LOGGER.error("pdf_service.write_pages: failed to open PDF: %s", str(e))
            raise ValueError("Could not open PDF — file may be corrupted or password-protected") from e

        try:
            total = src.page_count
            seen: set[int] = set()
            for first, last in ranges:
                if first > last:
                    raise ValueError(
                        "Page range %d-%d is reversed" % (first, last))
                if first < 1 or last > total:
                    raise ValueError(
                        "Page range %d-%d is outside the document, which has %d page(s)"
                        % (first, last, total))
                pages = set(range(first, last + 1))
                if pages & seen:
                    raise ValueError(
                        "Page range %d-%d overlaps an earlier range; one document "
                        "may not hold the same page twice" % (first, last))
                seen |= pages

            dst = pymupdf.open()
            try:
                for first, last in ranges:
                    dst.insert_pdf(src, from_page=first - 1, to_page=last - 1)
                out = dst.tobytes(garbage=3, deflate=True)
            finally:
                dst.close()
        finally:
            src.close()

        LOGGER.info(
            "pdf_service.write_pages: %d page(s) in %d range(s) from a %d-page source, %d bytes",
            len(seen), len(ranges), total, len(out),
        )
        return out

    def _vision_extract(self, page: pymupdf.Page, prompt: Optional[str] = None) -> str:
        """
        Render a page to an enhanced image and use LLM vision to extract text.

        :param page: PyMuPDF page object.
        :return: Extracted text from the LLM vision call.
        :rtype: str
        """
        from PIL import Image
        from services.llm_service import llm_service  # noqa: PLC0415

        # Render page at 300 DPI
        pix = page.get_pixmap(dpi=300)  # type: ignore[union-attr]
        img_bytes = pix.tobytes("png")  # type: ignore[union-attr]
        image = Image.open(io.BytesIO(img_bytes))  # type: ignore[union-attr]

        # Enhance for better LLM processing
        image = self._enhance_image(image)  # type: ignore[union-attr]

        # Encode as base64 PNG for the LLM
        buf = io.BytesIO()
        image.save(buf, format="PNG")  # type: ignore[union-attr]
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        # Use the LLM's vision capability
        try:
            text = llm_service.complete_with_image(
                system_prompt="You are a precise OCR system for legal documents.",
                user_message=prompt or _VISION_OCR_PROMPT,
                image_base64=b64,
                image_media_type="image/png",
                profile="ocr_document_page",
            )
            return text.strip()
        except Exception as e:
            # Say so in the text itself rather than returning "".
            #
            # An empty string was appended as the page's content and nothing
            # anywhere recorded that a page had been lost — the ingest carried
            # on as though the page were blank. A page we know we could not read
            # is a completely different thing from a page with nothing on it,
            # and only one of them is safe to build an exhibit over.
            LOGGER.warning("pdf_service._vision_extract: LLM vision failed: %s", str(e))
            return _UNREADABLE_MARKER

    def _enhance_image(self, image: ImageFile.ImageFile) -> "Image.Image":
        """
        Enhance an image for better LLM vision processing.

        Converts to grayscale, increases contrast and sharpness.
        """
        from PIL import ImageEnhance  # noqa: PLC0415

        _image: Image.Image = image.convert("L")
        enhancer = ImageEnhance.Contrast(_image)
        _image = enhancer.enhance(2.0)
        enhancer = ImageEnhance.Sharpness(_image)
        _image = enhancer.enhance(1.5)
        return _image


pdf_service = PDFService()
