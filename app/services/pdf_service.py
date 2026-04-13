"""
app/services/pdf_service.py - PDF text extraction with LLM vision fallback.

Uses PyMuPDF for native text extraction from searchable PDFs. For pages that
yield no text (image-only scanned pages), renders the page to an enhanced
image and uses the LLM's multimodal vision capability for OCR.
"""
import base64
import io

from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

_MIN_TEXT_LENGTH = 20  # Pages shorter than this are treated as image-only

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
        import fitz  # PyMuPDF  # noqa: PLC0415

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            LOGGER.error("pdf_service.extract_text: failed to open PDF: %s", str(e))
            raise ValueError("Could not open PDF — file may be corrupted or password-protected") from e

        pages: list[str] = []
        for page_num, page in enumerate(doc):
            text = page.get_text().strip()
            if len(text) >= _MIN_TEXT_LENGTH:
                LOGGER.debug("pdf_service: page %s text extraction ok (%s chars)", page_num, len(text))
                pages.append(text)
            else:
                LOGGER.debug("pdf_service: page %s text too short (%s chars), using LLM vision", page_num, len(text))
                ocr_text = self._vision_extract(page)
                pages.append(ocr_text)

        doc.close()
        return "\n\n".join(pages)

    def _vision_extract(self, page) -> str:
        """
        Render a page to an enhanced image and use LLM vision to extract text.

        :param page: PyMuPDF page object.
        :return: Extracted text from the LLM vision call.
        :rtype: str
        """
        from PIL import Image, ImageEnhance  # noqa: PLC0415
        from services.llm_service import llm_service  # noqa: PLC0415

        # Render page at 300 DPI
        pix = page.get_pixmap(dpi=300)
        img_bytes = pix.tobytes("png")
        image = Image.open(io.BytesIO(img_bytes))

        # Enhance for better LLM processing
        image = self._enhance_image(image)

        # Encode as base64 PNG for the LLM
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        # Use the LLM's vision capability
        try:
            text = llm_service.complete_with_image(
                system_prompt="You are a precise OCR system for legal documents.",
                user_message=_VISION_OCR_PROMPT,
                image_base64=b64,
                image_media_type="image/png",
            )
            return text.strip()
        except Exception as e:
            LOGGER.warning("pdf_service._vision_extract: LLM vision failed: %s", str(e))
            return ""

    def _enhance_image(self, image) -> "Image.Image":
        """
        Enhance an image for better LLM vision processing.

        Converts to grayscale, increases contrast and sharpness.
        """
        from PIL import ImageEnhance  # noqa: PLC0415

        image = image.convert("L")
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(1.5)
        return image


pdf_service = PDFService()
