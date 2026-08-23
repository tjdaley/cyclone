"""
app/services/pdf_service.py - PDF text extraction with LLM vision fallback.

Uses PyMuPDF for native text extraction from searchable PDFs. For pages that
yield no text (image-only scanned pages), renders the page to an enhanced
image and uses the LLM's multimodal vision capability for OCR.
"""
import base64
import io
import time
from PIL import ImageFile, Image
import pymupdf

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

_MIN_TEXT_LENGTH = 20  # Pages shorter than this are treated as image-only

# Control characters to strip from extracted text, keeping tab, newline, and
# carriage return. A NUL in particular is fatal downstream: Postgres rejects
# one in text and jsonb ("unsupported Unicode escape sequence", SQLSTATE
# 22P05), so a single one in an exhibit page fails the whole ingest. Badly
# encoded PDFs — scanned exhibits, forms flattened by odd tooling — produce
# them routinely.
_CONTROL_CHARS = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CONTROL_CHARS[0x7F] = None


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

    def extract_text(self, pdf_bytes: bytes) -> str:
        """
        Extract all text from a PDF, page by page.

        :param pdf_bytes: Raw PDF file content.
        :type pdf_bytes: bytes
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
            if len(text) >= _MIN_TEXT_LENGTH:  # type: ignore[union-attr]
                LOGGER.debug("pdf_service: page %s text extraction ok (%s chars)", page_num, len(text))  # type: ignore[union-attr]
                pages.append(text)  # type: ignore[union-attr]
            else:
                LOGGER.debug("pdf_service: page %s text too short (%s chars), using LLM vision", page_num, len(text))  # type: ignore[union-attr]
                ocr_pages += 1
                ocr_text = self._vision_extract(page)
                pages.append(ocr_text)

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

    def _vision_extract(self, page: pymupdf.Page) -> str:
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
                user_message=_VISION_OCR_PROMPT,
                image_base64=b64,
                image_media_type="image/png",
                profile="ocr_document_page",
            )
            return text.strip()
        except Exception as e:
            LOGGER.warning("pdf_service._vision_extract: LLM vision failed: %s", str(e))
            return ""

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
