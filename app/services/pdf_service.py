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
from typing import Optional
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
        page: pymupdf.Page
        for page in doc:
            page_num += 1
            text = page.get_text().strip()  # type: ignore[union-attr]
            if not page_needs_ocr(text):  # type: ignore[union-attr]
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
