"""
app/services/source_document_service.py - Receiving a file, and deciding what to do with it.

Step 1 of the pipeline: a file arrives, the as-produced record is written, and
the document is **routed** rather than processed blindly. Design and the
measurements behind every threshold here are
``docs/specs/document-labeling/SPEC_document_pipeline.md`` §5a.

**The routing decision is the whole point.** Two real piles from one firm came
out with inverted profiles: an orderly run of bank statements splits
automatically at 95% precision, and a 396-page stack of scraps has no good
automatic operating point at any cost ratio. There is no one way to process a
received file, so the pipeline has to decide per upload what it is looking at —
and it can, from signals that cost nothing.
"""
import hashlib
import re
from typing import Optional

from db.models.document import SourceDocumentInDB
from db.repositories.document import SourceDocumentRepository
from db_handler import DatabaseManager

from services.pdf_service import page_needs_ocr, pdf_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

# ── Routing thresholds ────────────────────────────────────────────────────────
#
# Measured, not chosen. Two hand-marked piles:
#
#   Pile A  84 pages, 22 documents, 100% text layer, 74% pagination coverage
#           -> 86% of boundaries found at 95% precision
#   Pile B 396 pages, 169 documents,  96% text layer,  8% pagination coverage
#           -> no operating point better than 49% recall at 76% precision
#
# 74% against 8% needs no tuned threshold; anything between 0.2 and 0.6
# separates them. 0.5 is the middle of that gap rather than a fitted value, and
# saying so matters: a number tuned to two piles would read as more certain than
# it is.
_MIN_PAGINATION_COVERAGE = 0.50

# Below this share of pages carrying a usable text layer, nothing is decidable:
# the printed-signal method and a model's page-continuation judgment both need
# readable pages, so OCR runs first and the file is routed again afterwards.
# Deliberately low — a production with a few photographs in it is still
# measurable.
_MIN_TEXT_SHARE = 0.50

# Past either of these the automatic split cannot work, so the file goes to a
# person. SAME NUMBERS as statement_service's refusal and the same measurement
# behind them — a 700-page PDF of 122 statements that was warned about, imported
# anyway, and was a total loss — but a DIFFERENT consequence: there the import
# is refused, here the work is offered to somebody who can actually do it.
#
# Module constants rather than settings, and now for a second reason. They no
# longer need to resist being raised under deadline pressure, because exceeding
# them is not a refusal. They need to resist being raised *casually*: a 60-page
# pile sent down the automatic path produces the mess the limits were written
# about.
_MAX_AUTOMATIC_PAGES = 50
_MAX_AUTOMATIC_DOCUMENTS = 8

_PAGE_OF = re.compile(r"page\s+(\d{1,3})\s+of\s+(\d{1,3})", re.I)

# What the routing verdict can be.
ROUTE_OCR = "ocr_first"
ROUTE_BENCH = "bench"
ROUTE_AUTOMATIC = "automatic"


class SourceDocumentService:
    """Receive a file, record it as produced, and say what to do with it."""

    # ── Receiving ─────────────────────────────────────────────────────────────

    def receive(self, manager: DatabaseManager, matter_id: int, content: bytes,
                original_filename: str, media_type: Optional[str] = None,
                production_id: Optional[int] = None,
                received_by_staff_id: Optional[int] = None) -> tuple[SourceDocumentInDB, bool]:
        """
        Write the as-produced record for a file that has arrived.

        **The object is stored BEFORE the row exists**, which is the rule §11a
        paid for once already: inserting first publishes a record whose file is
        still uploading, and anything that reads it in that window fails with
        "the original could not be retrieved". Here the path is the content
        hash, so the upload needs no row to name it and a retry writes the same
        object instead of a second one.

        :return: ``(record, created)``. ``created`` is False when this matter
            already holds these exact bytes — a supplement re-serves documents,
            and the cumulative index must not list one twice.
        """
        digest = hashlib.sha256(content).hexdigest()
        repo = SourceDocumentRepository(manager)

        existing = repo.find_by_hash(matter_id, digest)
        if existing is not None:
            # Keyed on the bytes, not the filename: productions arrive with
            # names that are useless and routinely re-served under a new one.
            LOGGER.info(
                "source_document.receive: matter=%s already holds this content as id=%s",
                matter_id, existing.id)
            return existing, False

        extension = self._extension(original_filename, media_type)
        path = StorageService(manager).upload_source_document(
            matter_id, digest, content, extension=extension)

        record = repo.insert({
            "matter_id": matter_id,
            "production_id": production_id,
            "storage_path": path,
            "original_filename": original_filename[:255],
            "media_type": media_type,
            "content_sha256": digest,
            "byte_count": len(content),
            "received_by_staff_id": received_by_staff_id,
        })
        LOGGER.info(
            "source_document.receive: matter=%s id=%s %d bytes",
            matter_id, record.id, len(content))
        return record, True

    @staticmethod
    def _extension(filename: str, media_type: Optional[str]) -> str:
        """The extension as served, or one inferred from the type.

        Kept because the as-produced record holds what arrived, and a TIFF that
        step 0 will convert to PDF has to still be identifiable as a TIFF.
        """
        tail = (filename or "").rsplit(".", 1)
        if len(tail) == 2 and 1 <= len(tail[1]) <= 5 and tail[1].isalnum():
            return tail[1].lower()
        return {"application/pdf": "pdf", "image/tiff": "tif",
                "image/jpeg": "jpg", "image/png": "png"}.get(media_type or "", "bin")

    def prepare_upload(self, manager: DatabaseManager, matter_id: int,
                       content_sha256: str, filename: str,
                       media_type: Optional[str] = None) -> dict:
        """
        Where to PUT a file, so it never passes through this application.

        Returns an existing record instead of a URL when this matter already
        holds these bytes. **That is worth more than it looks on a 74 MB
        production**: a re-send costs nothing at all, where the old path moved
        the whole file before discovering it was a duplicate.

        :return: ``{"already_held": bool, "source_document_id"?, "path",
            "signed_url"?, "token"?}``
        """
        existing = SourceDocumentRepository(manager).find_by_hash(matter_id, content_sha256)
        if existing is not None:
            LOGGER.info("source_document.prepare_upload: matter=%s already holds id=%s",
                        matter_id, existing.id)
            return {"already_held": True, "source_document_id": existing.id,
                    "path": existing.storage_path}

        storage = StorageService(manager)
        path = storage.as_produced_path(
            matter_id, content_sha256, self._extension(filename, media_type))
        return {"already_held": False, **storage.create_signed_upload_url(path)}

    def register(self, manager: DatabaseManager, matter_id: int, storage_path: str,
                 content_sha256: str, original_filename: str, byte_count: int,
                 media_type: Optional[str] = None,
                 production_id: Optional[int] = None,
                 received_by_staff_id: Optional[int] = None) -> tuple[SourceDocumentInDB, bool]:
        """
        Record a file the browser has already put into storage.

        **The object is confirmed present before a row claims it exists.** The
        same ordering rule as every other upload here, arrived at from the other
        side: a record whose file is not there fails for everything that reads
        it, and "the original could not be retrieved" is a far worse message
        than a failed upload.

        The HASH is not verified here, deliberately. Checking it would mean
        downloading the file back into the request this whole design exists to
        keep the file out of. The survey job verifies it when it reads the
        file, which it does anyway, and flags a mismatch on the record.

        :raises ValueError: If nothing is at that path.
        """
        repo = SourceDocumentRepository(manager)
        existing = repo.find_by_hash(matter_id, content_sha256)
        if existing is not None:
            return existing, False

        if StorageService(manager).stat(storage_path) is None:
            raise ValueError(
                "No file was found at that location, so nothing was recorded. The upload "
                "may not have finished — try it again.")

        record = repo.insert({
            "matter_id": matter_id,
            "production_id": production_id,
            "storage_path": storage_path,
            "original_filename": original_filename[:255],
            "media_type": media_type,
            "content_sha256": content_sha256,
            "byte_count": byte_count,
            "received_by_staff_id": received_by_staff_id,
        })
        LOGGER.info("source_document.register: matter=%s id=%s %d bytes",
                    matter_id, record.id, byte_count)
        return record, True

    # ── Routing ───────────────────────────────────────────────────────────────

    def route_from_features(self, features: Optional[list]) -> dict:
        """
        The same verdict, derived from the per-page facts already stored.

        `page_features` holds every input this needs -- the characters on each
        page and the pagination it prints -- so the verdict is DERIVED rather
        than stored beside them. A second copy of an answer is the drift this
        codebase keeps meeting, and this one would be worse than most: a stored
        verdict computed before a file was OCR'd would still say "no text
        layer" afterwards.

        :param features: ``source_documents.page_features``, or None before the
            survey has run.
        """
        if not features:
            return {"route": None, "reason":
                    "This file has not been read yet. It is stored exactly as it arrived; "
                    "the survey that decides what to do with it runs in the background.",
                    "pages": 0, "text_share": 0.0,
                    "pagination_coverage": 0.0, "likely_documents": 0}

        pages = len(features)
        # `unreadable` is already `page_needs_ocr` applied at survey time, so
        # this is the same test reaching the same answer from stored facts.
        with_text = sum(1 for f in features
                        if f.get("chars", 0) >= 20 and not f.get("unreadable"))
        covered, starts = self._pagination_from_features(features)
        return self._verdict(pages, with_text, len(covered), starts)

    @staticmethod
    def _verdict(pages: int, with_text: int, covered: int, starts: int) -> dict:
        """
        The routing decision, from four counts.

        Shared by the pass that reads a PDF and the one that reads the stored
        per-page facts, so the two cannot give different answers about the same
        file -- which they would eventually, being the same rules written twice.
        """
        text_share = with_text / pages if pages else 0.0
        coverage = covered / pages if pages else 0.0
        result = {
            "pages": pages,
            "text_share": round(text_share, 3),
            "pagination_coverage": round(coverage, 3),
            # A lower bound: only documents that paginate themselves are counted.
            "likely_documents": starts,
        }

        if text_share < _MIN_TEXT_SHARE:
            result.update(route=ROUTE_OCR, reason=(
                "Only %d of %d pages have readable text, so neither the automatic split "
                "nor anything else can read this yet. It needs a text layer first; it "
                "will be routed again once it has one." % (with_text, pages)))
        elif pages > _MAX_AUTOMATIC_PAGES:
            result.update(route=ROUTE_BENCH, reason=(
                "%d pages is more than the automatic split handles well. Splitting it by "
                "hand is quicker than correcting a bad automatic split, and you can leave "
                "it unsplit if you would rather not." % pages))
        elif starts > _MAX_AUTOMATIC_DOCUMENTS:
            result.update(route=ROUTE_BENCH, reason=(
                "This looks like at least %d separate documents, which is more than the "
                "automatic split handles well." % starts))
        elif coverage < _MIN_PAGINATION_COVERAGE:
            result.update(route=ROUTE_BENCH, reason=(
                "Only %d%% of these pages say which document they belong to, so there is "
                "little for an automatic split to go on — this looks like a stack of "
                "scanned odds and ends rather than a run of statements."
                % round(100 * coverage)))
        else:
            result.update(route=ROUTE_AUTOMATIC, reason=(
                "%d%% of these pages carry their own pagination, so the split can be made "
                "automatically and checked rather than done by hand."
                % round(100 * coverage)))
        return result

    @staticmethod
    def _pagination_from_features(features: list) -> tuple[set, int]:
        """Complete, self-consistent runs, read off the stored `page_of` strings
        rather than the PDF. Same rule as `_pagination`: a lone "Page 1 of 9"
        with nothing after it says the document is NOT here."""
        parsed = []
        for f in features:
            raw = f.get("page_of")
            if not raw:
                parsed.append(None)
                continue
            try:
                a, _, b = raw.partition(" of ")
                parsed.append((int(a), int(b)))
            except (ValueError, AttributeError):
                parsed.append(None)

        covered: set = set()
        starts = 0
        for index, entry in enumerate(parsed):
            if not entry or entry[0] != 1:
                continue
            total = entry[1]
            if total < 1 or index + total > len(parsed):
                continue
            run = [index]
            for step in range(1, total):
                nxt = parsed[index + step]
                if nxt and nxt == (step + 1, total):
                    run.append(index + step)
                else:
                    break
            if len(run) == total:
                starts += 1
                covered.update(p + 1 for p in run)
        return covered, starts

    def route(self, pdf_bytes: bytes) -> dict:
        """
        Decide what this file is, from signals that cost nothing.

        No model, no OCR, one pass over the text layer. Every figure here is
        deterministic and computable on an upload nobody has looked at, which is
        the constraint that rules out precision and recall: those need
        hand-marked truth.

        :return: ``{route, reason, pages, text_share, pagination_coverage,
            likely_documents}``
        :rtype: dict
        """
        try:
            texts = pdf_service.page_texts(pdf_bytes)
        except ValueError:
            # A file that will not open is not a routing decision. The ingest
            # that tries to read it reports a corrupt file far better than this
            # could, and a gate that fires when it cannot see would reject a
            # document for being unreadable by the one check that never read it.
            LOGGER.warning("source_document.route: could not read the PDF; sending to the bench")
            return {"route": ROUTE_BENCH, "reason":
                    "This file could not be read as a PDF, so nothing can be decided about "
                    "it automatically. Open it on the bench and see what it is.",
                    "pages": 0, "text_share": 0.0,
                    "pagination_coverage": 0.0, "likely_documents": 0}

        pages = len(texts)
        with_text = sum(1 for t in texts if not page_needs_ocr(t))
        text_share = with_text / pages if pages else 0.0
        covered, starts = self._pagination(texts)
        coverage = len(covered) / pages if pages else 0.0

        result = self._verdict(pages, with_text, len(covered), starts)

        LOGGER.info(
            "source_document.route: %d page(s), text %.0f%%, pagination %.0f%%, "
            "%d likely document(s) -> %s",
            pages, 100 * text_share, 100 * coverage, starts, result["route"])
        return result

    @staticmethod
    def _pagination(texts: list[str]) -> tuple[set, int]:
        """
        Pages inside a COMPLETE, self-consistent ``Page i of N`` run.

        The question a routing test can ask without truth is whether the pile
        describes its own structure. A statement prints "Page 1 of 4" and is
        followed by pages agreeing they are 2, 3 and 4 of 4; a stack of receipts
        cannot do that. Measured: 74% of pages on an orderly pile, 8% on a stack
        of scraps, with a 96% text layer on both — which is why **text quality
        is not the predictor** and this is.

        Requiring the run to COMPLETE is what keeps it honest. A lone "Page 1 of
        9" with nothing after it says the opposite of what it appears to: that
        the document is not here.

        :return: ``(pages inside a complete run, how many runs began)``
        """
        covered: set = set()
        starts = 0
        for index, text in enumerate(texts):
            # The ends of the page only: pagination prints in a header or a
            # footer, and a figure mid-paragraph is not pagination.
            edges = text[:500] + "\n" + text[-500:]
            match = _PAGE_OF.search(edges)
            if not (match and match.group(1) == "1"):
                continue
            total = int(match.group(2))
            if total < 1 or index + total > len(texts):
                continue
            run = [index]
            for step in range(1, total):
                nxt_text = texts[index + step]
                nxt = _PAGE_OF.search(nxt_text[:500] + "\n" + nxt_text[-500:])
                if nxt and int(nxt.group(1)) == step + 1 and int(nxt.group(2)) == total:
                    run.append(index + step)
                else:
                    break
            if len(run) == total:
                starts += 1
                covered.update(p + 1 for p in run)
        return covered, starts


source_document_service = SourceDocumentService()
