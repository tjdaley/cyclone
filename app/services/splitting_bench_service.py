"""
app/services/splitting_bench_service.py - Turning one received PDF into documents, by hand.

The design and the measurements behind it are
``docs/specs/document-labeling/SPEC_splitting_bench.md``. One job: a person says
which pages group, and each group becomes a document. It does not classify,
name, OCR, or stamp Bates numbers — each of those is a separate step, and
keeping them apart is most of why the bench is quick.

**Why a person rather than a better model.** Measured on two real piles: an
orderly run of bank statements splits automatically at 95% precision, and a
396-page stack of scraps has no good automatic operating point at any cost
ratio. Reviewing a wrong split costs more than making the right one, because the
reviewer must reconstruct what the machine did before correcting it, discover
welds hidden inside fragments, and recognise that fragment 47 belongs with
fragment 52.

**There is no session table, deliberately.** The grid is derived state — the
source document, plus which pages the existing documents already claim — so
opening a session only takes the lock and resuming is free. Somebody can close
the browser halfway through 700 pages and come back to exactly the same grid.
"""
import hashlib
import re
import threading
from collections import OrderedDict
from typing import Optional

from db.models.document import DocumentInDB, SourceDocumentInDB
from db.repositories.document import (
    DocumentRepository,
    DocumentSourcePageRepository,
    SourceDocumentRepository,
)
from db_handler import DatabaseManager

from services.audit_logger import AuditLogger
from services.pdf_service import (
    coalesce_pages, page_needs_ocr, pdf_service, wordlike_ratio,
)
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory
from util.redis_client import claim_for, release_claim

LOGGER = LoggerFactory.create_logger(__name__)

# How long one operator holds a pile. Long, because splitting 700 pages is a
# long session and losing the lock mid-way would let a second operator start
# claiming the same pages. Refreshed on every bundle.
_LOCK_TTL_SECONDS = 3600

# The label a swept-up remainder gets. A blank separator sheet was still
# produced and still carries a Bates number, so it cannot simply be left out: a
# cumulative index that omits a produced page is short, which is the one thing
# that index must not be.
SEPARATOR_DOC_TYPE = "blank_or_separator"

# ── The source PDF cache ──────────────────────────────────────────────────────
#
# THE THING WORTH CACHING IS THE PDF, NOT THE THUMBNAILS. Rendering a page costs
# 25 ms from bytes already in hand; downloading a 33 MB original costs orders of
# magnitude more, and a twenty-thumbnail screen would otherwise download it
# twenty times. So the bytes are cached per node and every thumbnail renders
# from memory.
#
# Bounded by total bytes rather than entry count, because these differ by two
# orders of magnitude: one pile is 33 MB and another is 200 KB.
_PDF_CACHE_BYTES = 256 * 1024 * 1024
_pdf_cache: "OrderedDict[str, bytes]" = OrderedDict()
_pdf_cache_lock = threading.Lock()


def _cached_pdf(storage_path: str, storage: StorageService) -> Optional[bytes]:
    """The source bytes, downloaded once per node and kept."""
    with _pdf_cache_lock:
        data = _pdf_cache.get(storage_path)
        if data is not None:
            _pdf_cache.move_to_end(storage_path)
            return data

    # Downloaded outside the lock: a 33 MB fetch must not block every other
    # thumbnail request on the node.
    data = storage.download(storage_path)
    if data is None:
        return None

    with _pdf_cache_lock:
        _pdf_cache[storage_path] = data
        _pdf_cache.move_to_end(storage_path)
        total = sum(len(v) for v in _pdf_cache.values())
        while total > _PDF_CACHE_BYTES and len(_pdf_cache) > 1:
            _, evicted = _pdf_cache.popitem(last=False)
            total -= len(evicted)
    return data


def forget_cached_pdf(storage_path: str) -> None:
    """Drop a cached original — after a re-OCR replaces it in storage."""
    with _pdf_cache_lock:
        _pdf_cache.pop(storage_path, None)


class BenchError(Exception):
    """Something the caller can fix: a bad page list, a held lock, a finished pile."""


class SplittingBenchService:
    """The manual split path."""

    # ── Page facts, computed once ─────────────────────────────────────────────

    _PAGE_OF = re.compile(r"page\s+(\d{1,3})\s+of\s+(\d{1,3})", re.I)
    _MONTHS = ("january february march april may june july august september "
               "october november december").split()
    _DATE_WORD = re.compile(
        r"\b(%s)\w*\.?\s+(\d{1,2})\s*,?\s+(\d{4})\b" % "|".join(_MONTHS), re.I)
    _DATE_NUM = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b")
    _ACCT_LABEL = re.compile(
        r"account[^0-9A-Za-z]{0,4}(?:no|num|number|#|it|11)?[^0-9]{0,6}", re.I)
    _ACCT_GROUP = re.compile(r"\b\d{4,}(?:\s\d{3,})*\b")

    # Nothing to read at all, as opposed to something unreadable. The two are
    # different facts and the grid shows them differently: a blank separator
    # sheet is ordinary, a page of OCR debris is a problem.
    _BLANK_CHARS = 20

    def page_features(self, pdf_bytes: bytes) -> list[dict]:
        """
        Per-page facts for the grid's badges.

        **Facts the page PRINTS, never a conclusion about where a document
        starts.** A pile reaches the bench precisely because the automatic
        signals are weak there, so showing their guess — or worse, pre-selecting
        it — would reintroduce the review-a-wrong-split cost the bench exists to
        remove. A fact helps the operator decide; a conclusion has to be undone.

        :param pdf_bytes: The source PDF.
        :return: One dict per page, in order.
        :rtype: list[dict]
        """
        out: list[dict] = []
        for index, text in enumerate(self._page_texts(pdf_bytes), start=1):
            head = text[:300]
            match = self._PAGE_OF.search(text[:500] + "\n" + text[-500:])
            chars = len(text.strip())
            out.append({
                "page": index,
                "chars": chars,
                "page_of": "%s of %s" % (match.group(1), match.group(2)) if match else None,
                "date": self._header_date(head),
                "account": self._header_account(head),
                "snippet": re.sub(r"\s+", " ", text).strip()[:120],
                # Two different facts: nothing to read, and something
                # unreadable. The second uses `pdf_service.page_needs_ocr`, the
                # single definition of a usable text layer -- three copies of
                # that test had drifted, and the most permissive one was the
                # one deciding whether to OCR.
                "blank": chars < self._BLANK_CHARS,
                "unreadable": chars >= self._BLANK_CHARS and page_needs_ocr(text),
            })
        return out

    def _page_texts(self, pdf_bytes: bytes) -> list[str]:
        """Raw text layer per page — no OCR fallback, by design.

        The badges are a convenience computed in one pass over a whole pile;
        falling back to a vision call per image-only page would make opening a
        396-page bench session a minutes-long, billable operation. A page with
        no text layer simply has no badges, which is honest.
        """
        return pdf_service.page_texts(pdf_bytes)

    def _header_date(self, head: str) -> Optional[str]:
        m = self._DATE_WORD.search(head[:200])
        if m:
            return "%04d-%02d-%02d" % (int(m.group(3)),
                                       self._MONTHS.index(m.group(1).lower()) + 1,
                                       int(m.group(2)))
        m = self._DATE_NUM.search(head[:200])
        if m:
            year = int(m.group(3))
            year += 2000 if year < 50 else 1900 if year < 100 else 0
            return "%04d-%02d-%02d" % (year, int(m.group(1)), int(m.group(2)))
        return None

    def _header_account(self, head: str) -> Optional[str]:
        """The digit group the header LABELS as an account number.

        Every digit group was the first rule and it was useless: a cover sheet
        prints a PO box, a ZIP and a mail code. Six digits minimum, because
        "your account ... 2018" otherwise puts a year here.
        """
        for label in self._ACCT_LABEL.finditer(head):
            m = self._ACCT_GROUP.search(head, label.end(), label.end() + 40)
            if m:
                digits = m.group(0).replace(" ", "")
                if len(digits) >= 6:
                    return digits
        return None

    # ── Opening ───────────────────────────────────────────────────────────────

    def open_session(self, manager: DatabaseManager, source_document_id: int,
                     supabase_uid: Optional[str] = None) -> dict:
        """
        Start or resume work on one received file.

        Reports the size of the job, which is what makes cancelling a real
        choice: an oversized pile is routed here rather than refused, and an
        offer whose cost is hidden is a trap.

        **IT NO LONGER READS THE FILE.** It used to compute the page badges
        inline when they were missing, which on a 73 MB production meant
        downloading 73 MB inside this request -- and racing the survey job doing
        exactly the same work. The survey job owns that now; this reports
        whether it has finished (`surveyed`) so the screen can say "still being
        read" instead of showing an empty grid.

        An empty grid was the actual damage: with no features stored, nothing is
        listed and nothing is claimed, and the screen said **"Every page is in a
        document"** -- the opposite of the truth, to somebody whose file had not
        been read yet.
        """
        source = self._source(manager, source_document_id)
        features = source.page_features or []
        claimed = self._claimed(manager, source_document_id)
        unclaimed = [f["page"] for f in features if f["page"] not in claimed]
        return {
            "source_document_id": source.id,
            "matter_id": source.matter_id,
            "original_filename": source.original_filename,
            # False while the survey job is still queued or running. Everything
            # below it is zero in that state, and zero pages must not read as
            # "nothing left to do".
            "surveyed": bool(source.page_features),
            "page_count": len(features),
            "documents_so_far": len({d for d in claimed.values()}),
            "pages_unclaimed": len(unclaimed),
            "readable_pages": sum(1 for f in features if not f["blank"] and not f["unreadable"]),
        }

    def grid(self, manager: DatabaseManager, source_document_id: int,
             offset: int = 0, limit: int = 20,
             include_claimed: bool = False) -> dict:
        """
        One screen of the grid: the pages, their badges, and who claims them.

        Reads the stored badges rather than the PDF, so paging through 396 pages
        costs twenty cheap queries instead of twenty 33 MB downloads.

        **Claimed pages drop out by default, and that is the progress
        indicator**: bundled pages disappear, the remaining grid is what is
        left, and no separate counter can disagree with it.

        ``include_claimed`` brings them back, and it is not a debugging
        convenience — it is the only way to reach the case where a page
        legitimately belongs to TWO documents. A combined statement prints one
        account's close and the next account's opening on one sheet, so that
        page has to be selectable again after it has been claimed. Paging runs
        over whichever list is in force, or a screen late in a pile would come
        back nearly empty.
        """
        source = self._source(manager, source_document_id)
        features = source.page_features or []
        claimed = self._claimed(manager, source_document_id)

        shown = features if include_claimed else [
            f for f in features if f["page"] not in claimed]
        pages = []
        for f in shown[offset:offset + limit]:
            page = dict(f)
            page["claimed_by_document_id"] = claimed.get(f["page"])
            pages.append(page)
        return {
            "source_document_id": source.id,
            "page_count": len(features),
            "pages_shown": len(shown),
            "offset": offset,
            "pages": pages,
        }

    def thumbnail(self, manager: DatabaseManager, source_document_id: int,
                  page_number: int, width: int = 450, fmt: str = "jpeg") -> Optional[bytes]:
        """One rendered page, from the cached original."""
        source = self._source(manager, source_document_id)
        pdf_bytes = _cached_pdf(source.storage_path, StorageService(manager))
        if pdf_bytes is None:
            return None
        return pdf_service.render_page(pdf_bytes, page_number, width=width, fmt=fmt)

    # ── Bundling ──────────────────────────────────────────────────────────────

    def bundle(self, manager: DatabaseManager, source_document_id: int,
               pages: list[int], supabase_uid: Optional[str] = None,
               doc_type: Optional[str] = None) -> DocumentInDB:
        """
        Make the selected pages into one document.

        :param pages: The selection, in any order. Duplicates collapse.
        :param doc_type: Normally None — the bench does not classify. Set only
            for the swept-up remainder at finish.
        :raises BenchError: If the selection is empty, out of range, or every
            page is already claimed by the same document.
        """
        source = self._source(manager, source_document_id)
        total = source.page_count or len(source.page_features or [])
        if not pages:
            raise BenchError("No pages selected.")
        bad = sorted({p for p in pages if p < 1 or (total and p > total)})
        if bad:
            raise BenchError(
                "Pages %s are not in this document, which has %d page(s)."
                % (", ".join(str(p) for p in bad[:10]), total))

        ranges = coalesce_pages(pages)
        docs = DocumentRepository(manager)
        extents = DocumentSourcePageRepository(manager)

        document = docs.insert({
            "matter_id": source.matter_id,
            "source_document_id": source.id,
            "doc_type": doc_type,
            "needs_review": True,
        })

        # Ranges after the row, because the exclusion constraint lives on them:
        # if one is rejected the document has to go, and a document with no
        # extent is worse than no document.
        try:
            for seq, (first, last) in enumerate(ranges, start=1):
                extents.insert({
                    "document_id": document.id,
                    "seq": seq,
                    "page_first": first,
                    "page_last": last,
                })
            pdf_bytes = _cached_pdf(source.storage_path, StorageService(manager))
            if pdf_bytes is None:
                raise BenchError(
                    "The original could not be retrieved from storage, so the "
                    "selected pages could not be written.")
            child = pdf_service.write_pages(pdf_bytes, ranges)
            path = StorageService(manager).upload_document(
                source.matter_id, document.id, child)
        except Exception:
            # Leaving a document with no pages and no file would put a phantom
            # on the grid claiming nothing, which nobody could clear.
            docs.delete(document.id)
            raise

        document = docs.update(document.id, {
            "storage_path": path,
            "byte_count": len(child),
        })

        page_count = sum(last - first + 1 for first, last in ranges)
        LOGGER.info(
            "bench.bundle: document_id=%s from source_document_id=%s, %d page(s) in %d range(s)",
            document.id, source.id, page_count, len(ranges))
        AuditLogger(manager).log(
            action="document.split",
            entity_type="document",
            entity_id=str(document.id),
            supabase_uid=supabase_uid,
            after_json={
                "source_document_id": source.id,
                "ranges": ["%d-%d" % r for r in ranges],
                "page_count": page_count,
            },
        )
        return document

    def undo(self, manager: DatabaseManager, document_id: int,
             supabase_uid: Optional[str] = None) -> int:
        """
        Put a bundle's pages back on the grid.

        A **hard** delete, matching the rule for a statement or an account: the
        as-produced original is the undo, so soft state here would infect the
        grid, the unclaimed count and the index for no gain. Its page ranges go
        with it by cascade.

        Refused once anything downstream points at the document: a
        classification is cheap to redo, a request mapping somebody approved is
        not.

        :return: The source document id the pages returned to.
        :raises BenchError: If the document is not there, or is no longer free.
        """
        docs = DocumentRepository(manager)
        document = docs.select_one(condition={"id": document_id})
        if document is None:
            raise BenchError("That bundle no longer exists.")
        if document.withheld or document.statement_id:
            raise BenchError(
                "This document has been taken up by something downstream and can "
                "no longer be unbundled here.")

        if document.storage_path:
            StorageService(manager).delete(document.storage_path)
        docs.delete(document_id)

        LOGGER.info("bench.undo: document_id=%s returned to source_document_id=%s",
                    document_id, document.source_document_id)
        AuditLogger(manager).log(
            action="document.split_undone",
            entity_type="document",
            entity_id=str(document_id),
            supabase_uid=supabase_uid,
            before_json={
                "source_document_id": document.source_document_id,
                "storage_path": document.storage_path,
            },
        )
        return document.source_document_id

    def finish(self, manager: DatabaseManager, source_document_id: int,
               supabase_uid: Optional[str] = None) -> dict:
        """
        Declare the pile split, sweeping whatever is left.

        Unclaimed pages become **one** document labelled
        ``blank_or_separator``. They were produced and they carry Bates numbers,
        so leaving them out would make the production index short — which is the
        one thing that index must not be. Labelling them accounts for them
        without asserting they are documents, the same move as
        ``no_marital_interest`` on an account.

        :raises BenchError: If nothing has been bundled at all. That is a
            cancel, not a finish, and sweeping an entire pile into one
            separator document would be a silent way to lose it.
        """
        source = self._source(manager, source_document_id)
        features = source.page_features or []
        claimed = self._claimed(manager, source_document_id)
        documents = {d for d in claimed.values()}
        if not documents:
            raise BenchError(
                "Nothing has been bundled yet, so there is nothing to finish. "
                "Leave the bench instead — the file is kept, unsplit.")

        unclaimed = [f["page"] for f in features if f["page"] not in claimed]
        swept = None
        if unclaimed:
            # A FLAG MUST BE TRUE OF THE ROW IT SITS ON. Labelling the remainder
            # `blank_or_separator` asserts those pages are not documents, which
            # is right for a separator sheet and false for most of a pile that
            # somebody swept up by pressing Finish too early -- which is exactly
            # what happened the first time a person used this. So the label is
            # applied only when every swept page really is blank or unreadable;
            # anything else is an unsorted remainder with no label at all,
            # waiting for review.
            by_page = {f["page"]: f for f in features}
            all_empty = all(by_page[pg]["blank"] or by_page[pg]["unreadable"]
                            for pg in unclaimed)
            swept = self.bundle(
                manager, source_document_id, unclaimed, supabase_uid=supabase_uid,
                doc_type=SEPARATOR_DOC_TYPE if all_empty else None)

        self.leave(source_document_id)
        LOGGER.info(
            "bench.finish: source_document_id=%s, %d document(s), %d page(s) swept",
            source_document_id, len(documents), len(unclaimed))
        return {
            "source_document_id": source_document_id,
            "documents": len(documents) + (1 if swept else 0),
            "pages_swept": len(unclaimed),
            "swept_document_id": swept.id if swept else None,
            "swept_as_separator": bool(swept and swept.doc_type == SEPARATOR_DOC_TYPE),
        }

    def bundles(self, manager: DatabaseManager, source_document_id: int) -> list[dict]:
        """
        Every document split off this file, with the pages each one holds.

        **This is server state and the screen must read it, not remember it.**
        The first version kept the session's bundles in the client, so closing
        the page or finishing lost the list: an operator who came back was told
        there were four documents, with no way to see which pages were in which
        and no way to undo any of them. They then had to guess, and guessing on
        this screen creates duplicates.
        """
        docs = DocumentRepository(manager).get_by_source(source_document_id)
        if not docs:
            return []
        extents = DocumentSourcePageRepository(manager).get_by_documents(
            [d.id for d in docs])
        by_doc: dict[int, list] = {}
        for extent in extents:
            by_doc.setdefault(extent.document_id, []).append(extent)

        named = self._statement_names(manager, [d.statement_id for d in docs])

        out = []
        for doc in docs:
            ranges = by_doc.get(doc.id, [])
            out.append({
                "document_id": doc.id,
                "doc_type": doc.doc_type,
                "ranges": ["%d-%d" % (e.page_first, e.page_last)
                           if e.page_last > e.page_first else str(e.page_first)
                           for e in ranges],
                "pages": sorted(
                    pg for e in ranges for pg in range(e.page_first, e.page_last + 1)),
                "page_count": sum(e.page_last - e.page_first + 1 for e in ranges),
                "statement_id": doc.statement_id,
                "statement_label": named.get(doc.statement_id),
                # Undo is refused downstream, so the screen should not offer it.
                "can_undo": not doc.withheld and doc.statement_id is None,
            })
        return out

    @staticmethod
    def _statement_names(manager: DatabaseManager, ids: list) -> dict:
        """
        What each statement IS, for a person reading a list.

        **A row id is not an identity.** The bench showed "statement #771",
        which correlates to nothing anybody can see: not the exceptions queue,
        not the Accounts tab, not the document in their hand. A statement is
        recognised the way it is printed -- the institution, the account, the
        period -- which is the same argument that put the filename and the page
        range on the ingest label instead of a job uuid.

        One query for the statements and one for their accounts, not one per
        row. Any failure returns what it has: a missing label costs a less
        useful line, and must never cost the list.
        """
        wanted = sorted({i for i in ids if i})
        if not wanted:
            return {}
        try:
            from db.repositories.financial import (  # noqa: PLC0415
                FinancialAccountRepository, FinancialAccountStatementRepository,
            )

            statements = FinancialAccountStatementRepository(manager).select_many(
                condition={"id": wanted})[0]
            if not statements:
                return {}
            accounts = {
                a.id: a for a in FinancialAccountRepository(manager).select_many(
                    condition={"id": sorted({s.financial_account_id for s in statements})})[0]
            }
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("bench._statement_names: could not name statements: %s", str(e))
            return {}

        out = {}
        for st in statements:
            account = accounts.get(st.financial_account_id)
            who = account.institution if account else "Unknown institution"
            if account and account.account_number_last4:
                who += " \u00b7\u00b7\u00b7\u00b7%s" % account.account_number_last4
            out[st.id] = "%s, %s to %s" % (who, st.period_start, st.period_end)
        return out

    # ── Internals ─────────────────────────────────────────────────────────────

    def _source(self, manager: DatabaseManager, source_document_id: int) -> SourceDocumentInDB:
        source = SourceDocumentRepository(manager).select_one(
            condition={"id": source_document_id})
        if source is None:
            raise BenchError("That received file is not on record.")
        return source

    def _claimed(self, manager: DatabaseManager, source_document_id: int) -> dict:
        """
        Which document claims each page of the source.

        A page may legitimately appear in TWO documents — a combined statement
        prints one account's close and the next account's opening on one sheet —
        so this reports the last claimant and the grid shows the page as
        claimed. Forbidding it would make the commonest multi-account statement
        unexpressible; reporting it is a finding, not an error.
        """
        docs = DocumentRepository(manager).get_by_source(source_document_id)
        if not docs:
            return {}
        extents = DocumentSourcePageRepository(manager).get_by_documents(
            [d.id for d in docs])
        claimed: dict[int, int] = {}
        for extent in extents:
            for page in range(extent.page_first, extent.page_last + 1):
                claimed[page] = extent.document_id
        return claimed

    # ── The lock ──────────────────────────────────────────────────────────────

    def take(self, source_document_id: int, owner: str) -> tuple[bool, Optional[str]]:
        """
        Claim this pile for one operator, and HOLD it across requests.

        One operator per pile, because two people bundling the same grid race on
        the same pages and the loser's work is a duplicate document nobody
        notices until the index appears.

        **THE CLAIM HAS TO NAME ITS OWNER**, and using an anonymous one here was
        a bug that reached production. The bench re-opens its session after
        every bundle, so an anonymous claim reported the operator as somebody
        else from their second bundle onward -- a warning that was false every
        time it appeared, which is worse than no warning, because the next real
        one gets dismissed along with it.

        ``lock`` cannot express this either: it is a context manager, so it
        releases when the function returns, and a claim lasting one HTTP request
        is not a claim at all.

        Re-entry by the owner extends the claim, so an operator working steadily
        through 700 pages never loses the pile to a TTL. ``claim_once`` could
        not do that either: `SET NX` on an existing key is a no-op, so the
        refresh it was used for did nothing whatever.

        Best-effort, like job claiming: Redis unreachable grants the claim
        rather than halting the bench. The cost of the degraded case is two
        operators on one pile, which the exclusion constraint still keeps from
        corrupting a single document.

        :param owner: The caller's identity -- their supabase uid.
        :return: ``(is_ours, current_owner)``.
        """
        return claim_for(self._lock_key(source_document_id), owner,
                         ttl_seconds=_LOCK_TTL_SECONDS)

    def leave(self, source_document_id: int) -> None:
        """Give the pile up — on finish, or when an operator cancels."""
        release_claim(self._lock_key(source_document_id))

    @staticmethod
    def _lock_key(source_document_id: int) -> str:
        return "bench:%s" % source_document_id


def sha256_of(content: bytes) -> str:
    """Content hash for the as-produced dedup key. Filenames are useless."""
    return hashlib.sha256(content).hexdigest()


splitting_bench_service = SplittingBenchService()
