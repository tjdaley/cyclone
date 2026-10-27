"""
tests/test_splitting_bench.py - The manual split path.

The bench exists because the automatic split of a bad pile does not save human
time: reviewing a wrong split costs MORE than making the right one, since the
reviewer must reconstruct what the machine did, discover welds hidden inside
fragments, and recognise that fragment 47 belongs with fragment 52. Measured on
two real piles -- an orderly run of bank statements splits at 95% precision, a
396-page stack of scraps has no good operating point at any cost ratio.

What this pins, in order of how badly each would fail in the field:

- **Two documents may share a boundary page.** A combined statement prints one
  account's close and the next account's opening on one sheet. The obvious
  stricter rule makes the commonest multi-account statement unexpressible.
- **Badges are facts the page PRINTS, never a boundary guess.** A pile reaches
  the bench because the automatic signals are weak there, so showing their
  conclusion would reintroduce the cost the bench removes.
- **A failed bundle leaves no phantom.** A document row with no pages and no
  file would claim nothing on the grid and nobody could clear it.
- **Finishing with nothing bundled is refused.** That is a cancel, and sweeping
  a whole pile into one separator document is a silent way to lose it.
- **An unclaimed page is swept, not dropped.** It was produced and carries a
  Bates number; a cumulative index that omits a produced page is short.

Run:  venv/Scripts/python.exe tests/test_splitting_bench.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import pymupdf  # noqa: E402
import services.splitting_bench_service as bench_mod  # noqa: E402
from services.splitting_bench_service import (  # noqa: E402
    SEPARATOR_DOC_TYPE, BenchError, splitting_bench_service as bench,
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
    except BenchError as e:
        if fragment.lower() in str(e).lower():
            print("  ok   %s" % label)
        else:
            print("  FAIL %s\n         message %r lacks %r" % (label, str(e), fragment))
            FAILURES.append(label)
    except Exception as e:  # noqa: BLE001
        print("  FAIL %s\n         raised %s: %s" % (label, type(e).__name__, e))
        FAILURES.append(label)
    else:
        print("  FAIL %s — did not raise" % label)
        FAILURES.append(label)


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeSources:
    def __init__(self, row):
        self.row = row

    def select_one(self, condition):
        return self.row if condition.get("id") == self.row.id else None

    def update(self, rid, patch):
        self.row.__dict__.update(patch)
        return self.row


class FakeDocuments:
    def __init__(self):
        self.rows = {}
        self.next_id = 1
        self.fail_on_insert = False

    def insert(self, data):
        if self.fail_on_insert:
            raise RuntimeError("database said no")
        row = Row(id=self.next_id, withheld=False, statement_id=None,
                  storage_path=None, byte_count=None, **data)
        self.rows[self.next_id] = row
        self.next_id += 1
        return row

    def update(self, rid, patch):
        self.rows[rid].__dict__.update(patch)
        return self.rows[rid]

    def delete(self, rid):
        self.rows.pop(rid, None)
        return True

    def select_one(self, condition):
        return self.rows.get(condition.get("id"))

    def get_by_source(self, sid):
        return [r for r in self.rows.values() if r.source_document_id == sid]


class FakeExtents:
    def __init__(self):
        self.rows = []
        self.next_id = 1
        self.reject = None  # (page_first, page_last) to refuse, as the constraint would

    def insert(self, data):
        if self.reject and (data["page_first"], data["page_last"]) == self.reject:
            raise RuntimeError("23P01 conflicting key value violates exclusion constraint")
        row = Row(id=self.next_id, **data)
        self.rows.append(row)
        self.next_id += 1
        return row

    def get_by_document(self, did):
        return sorted([r for r in self.rows if r.document_id == did], key=lambda r: r.seq)

    def get_by_documents(self, dids):
        return sorted([r for r in self.rows if r.document_id in dids], key=lambda r: r.seq)


class FakeStorage:
    def __init__(self, pdf):
        self.pdf = pdf
        self.uploaded = {}
        self.deleted = []
        self.downloads = 0

    def download(self, path):
        self.downloads += 1
        return self.pdf

    def upload_document(self, matter_id, document_id, content):
        path = "matters/%d/documents/%d.pdf" % (matter_id, document_id)
        self.uploaded[path] = content
        return path

    def delete(self, path):
        self.deleted.append(path)


class FakeAudit:
    def __init__(self, *_a):
        pass

    def log(self, **kw):
        AUDIT.append(kw)


AUDIT: list[dict] = []


def pile(pages=20, text=lambda n: ""):
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((54, 72), "PAGE-MARKER-%d" % n, fontsize=12)
        body = text(n)
        if body:
            page.insert_text((54, 110), body, fontsize=9)
    out = doc.tobytes()
    doc.close()
    return out


def wire(pdf_bytes, pages=20, features=None):
    """Point the service at fakes and return them."""
    source = Row(id=5, matter_id=311, storage_path="as-produced/311/5.pdf",
                 original_filename="volume 2.pdf", page_count=pages,
                 page_features=features)
    sources, docs, extents = FakeSources(source), FakeDocuments(), FakeExtents()
    storage = FakeStorage(pdf_bytes)
    bench_mod.SourceDocumentRepository = lambda m: sources
    bench_mod.DocumentRepository = lambda m: docs
    bench_mod.DocumentSourcePageRepository = lambda m: extents
    bench_mod.StorageService = lambda m: storage
    bench_mod.AuditLogger = FakeAudit
    # No Redis in a test: taking the pile must not be what fails.
    bench_mod.claim_once = lambda *a, **k: True
    bench_mod.release_claim = lambda *a, **k: None
    bench_mod._pdf_cache.clear()
    AUDIT.clear()
    return source, docs, extents, storage


# ── 1. Badges are facts off the page ──────────────────────────────────────────

print("\n1. Badges report what the page prints, not where a document starts")

bank = pile(6, lambda n: {
    1: "Bank of America P.O. Box 15284 Wilmington DE 19850",
    2: "MUHAMMAD SHAHNAWAZ I Account # 4880 5304 6662 I August 22, 2018 to Sep\nPage 1 of 3",
    3: "Account # 4880 5304 6662  August 22, 2018\nPage 2 of 3",
}.get(n, "continued"))
features = bench.page_features(bank)

check("the pagination a page prints", features[1]["page_of"], "1 of 3")
check("a header date", features[1]["date"], "2018-08-22")
check("an account number the header LABELS as one", features[1]["account"], "488053046662")
# Every digit group was the first rule and it was useless: a cover sheet prints
# a PO box, a ZIP and a mail code, so "this page names no account" was never
# true.
check("a PO box and a ZIP are not account numbers", features[0]["account"], None)
check("no badge claims a page starts a document",
      [k for k in features[0] if "start" in k or "boundary" in k], [])

blank = pile(2, lambda n: "" if n == 1 else "ordinary readable text on this page here")
bf = bench.page_features(blank)
check("a page with nothing on it is blank, not unreadable",
      (bf[0]["blank"], bf[0]["unreadable"]), (True, False))

# OCR noise is entirely ordinary ASCII, so a non-ASCII garbage ratio scores it
# as clean prose. These two facts are different and want different responses.
noise = pile(1, lambda n: "Tr:tee._ cqz, eivs/tal- 115s 009220.7/6 4,7,4-vt 0 444 soo kk wv")
check("but a bad scan is unreadable, not blank",
      (bench.page_features(noise)[0]["blank"],
       bench.page_features(noise)[0]["unreadable"]), (False, True))

# ── 2. Bundling ───────────────────────────────────────────────────────────────

print("\n2. Bundling pages into a document")

src = pile(20)
source, docs, extents, storage = wire(src, features=bench.page_features(src))

d1 = bench.bundle(None, 5, [3, 4, 5], supabase_uid="uid-para")
check("a document was created", d1.id, 1)
check("its ranges", [(e.page_first, e.page_last) for e in extents.get_by_document(1)],
      [(3, 5)])
check("and a working PDF was uploaded", d1.storage_path,
      "matters/311/documents/1.pdf")

# Clicked out of order, and non-adjacent: the case Acrobat's bookmarks cannot
# express at all.
d2 = bench.bundle(None, 5, [15, 10, 11, 16, 12, 17])
check("a discontinuous bundle coalesces into two ranges",
      [(e.page_first, e.page_last) for e in extents.get_by_document(2)],
      [(10, 12), (15, 17)])
check("holding six pages", sum(e.page_last - e.page_first + 1
                               for e in extents.get_by_document(2)), 6)

written = pymupdf.open(stream=storage.uploaded["matters/311/documents/2.pdf"],
                       filetype="pdf")
got = []
for page in written:
    for token in (page.get_text() or "").split():
        if token.startswith("PAGE-MARKER-"):
            got.append(int(token.rsplit("-", 1)[1]))
            break
written.close()
check("and the PDF holds exactly those pages", got, [10, 11, 12, 15, 16, 17])

check("the split is audited, naming who did it",
      [(a["action"], a["supabase_uid"]) for a in AUDIT][:1], [("document.split", "uid-para")])

print("\n3. A page claimed twice, which is legal and must stay legal")

src = pile(10)
source, docs, extents, storage = wire(src, pages=10, features=bench.page_features(src))
bench.bundle(None, 5, [1, 2, 3, 4, 5])
# Page 5 is already claimed, so reaching it needs include_claimed -- which is
# exactly why that flag exists rather than being a debugging convenience. A
# combined statement prints one account's close and the next account's opening
# on one sheet, and forbidding this makes that document unexpressible.
visible = bench.grid(None, 5, offset=0, limit=10)
check("the claimed pages have dropped out of the default grid",
      [p["page"] for p in visible["pages"]], [6, 7, 8, 9, 10])
check("so the grid is its own progress indicator", visible["pages_shown"], 5)

shown = bench.grid(None, 5, offset=0, limit=10, include_claimed=True)
check("and include_claimed brings them back to be re-selected",
      [p["page"] for p in shown["pages"]], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])

bench.bundle(None, 5, [5, 6, 7, 8, 9, 10])
all_pages = bench.grid(None, 5, offset=0, limit=10, include_claimed=True)
check("page 5 belongs to two documents, and the grid says which holds it",
      all_pages["pages"][4]["claimed_by_document_id"], 2)
check("nothing is left unclaimed", bench.open_session(None, 5)["pages_unclaimed"], 0)
check("and the working grid is empty, which is how somebody knows they are done",
      bench.grid(None, 5)["pages_shown"], 0)

print("\n4. What it refuses")

src = pile(20)
source, docs, extents, storage = wire(src, features=bench.page_features(src))

check_raises("an empty selection", lambda: bench.bundle(None, 5, []), "no pages")
check_raises("a page past the end", lambda: bench.bundle(None, 5, [19, 20, 21]),
             "not in this document")
check_raises("page 0", lambda: bench.bundle(None, 5, [0, 1]), "not in this document")
check_raises("a file that is not on record", lambda: bench.bundle(None, 99, [1]),
             "not on record")

print("\n5. A failed bundle leaves no phantom behind")

src = pile(20)
source, docs, extents, storage = wire(src, features=bench.page_features(src))
extents.reject = (7, 9)
try:
    bench.bundle(None, 5, [7, 8, 9])
except Exception:
    pass
# A document row with no pages and no file would claim nothing on the grid, and
# nobody could clear it.
check("the document row was removed when its range was refused", len(docs.rows), 0)
check("and nothing was uploaded", len(storage.uploaded), 0)

extents.reject = None
d = bench.bundle(None, 5, [7, 8, 9])
check("the same selection succeeds once the conflict is gone", d.id, 2)

print("\n6. Undo returns the pages")

src = pile(10)
source, docs, extents, storage = wire(src, pages=10, features=bench.page_features(src))
d = bench.bundle(None, 5, [1, 2, 3])
check("claimed", bench.open_session(None, 5)["pages_unclaimed"], 7)
bench.undo(None, d.id, supabase_uid="uid-para")
check("released", bench.open_session(None, 5)["pages_unclaimed"], 10)
# The as-produced original is the undo, so soft state here would infect the
# grid, the unclaimed count and the index for no gain.
check("the working PDF is deleted outright, not soft-deleted",
      storage.deleted, ["matters/311/documents/1.pdf"])
check("and the undo is audited",
      [a["action"] for a in AUDIT][-1], "document.split_undone")

d = bench.bundle(None, 5, [4, 5])
docs.rows[d.id].statement_id = 900
check_raises("a document taken up downstream will not unbundle",
             lambda: bench.undo(None, d.id), "downstream")
check_raises("nor one that is gone", lambda: bench.undo(None, 404), "no longer exists")

print("\n7. Finishing sweeps what is left, and refuses an empty pile")

src = pile(10)
source, docs, extents, storage = wire(src, pages=10, features=bench.page_features(src))
# Finishing with nothing bundled would sweep a whole pile into one separator
# document, which is a silent way to lose it. That is a cancel.
check_raises("nothing bundled is a cancel, not a finish",
             lambda: bench.finish(None, 5), "nothing has been bundled")

bench.bundle(None, 5, [1, 2, 3])
bench.bundle(None, 5, [4, 5, 6, 7])
result = bench.finish(None, 5, supabase_uid="uid-para")
check("the sweep gathered the remainder", result["pages_swept"], 3)
check("into one document", result["swept_document_id"], 3)
check("labelled as not-a-document", docs.rows[3].doc_type, SEPARATOR_DOC_TYPE)
check("three documents in all", result["documents"], 3)
# They were produced and carry Bates numbers; a cumulative index that omits a
# produced page is short, which is the one thing it must not be.
check("every page of the pile is accounted for",
      bench.open_session(None, 5)["pages_unclaimed"], 0)

print("\n8. The original is downloaded once, not once per thumbnail")

src = pile(20)
source, docs, extents, storage = wire(src, features=bench.page_features(src))
for n in range(1, 21):
    bench.thumbnail(None, 5, n)
# A 33 MB download per thumbnail would be 660 MB for one screen. The thing
# worth caching is the PDF, not the rendered images.
check("twenty thumbnails, one download", storage.downloads, 1)
check("and they render", bench.thumbnail(None, 5, 1)[:3], b"\xff\xd8\xff")
check("a page past the end renders nothing", bench.thumbnail(None, 5, 99), None)

print("\n9. The grid pages through without touching the PDF")

src = pile(60)
source, docs, extents, storage = wire(src, pages=60, features=bench.page_features(src))
storage.downloads = 0
first = bench.grid(None, 5, offset=0, limit=20)
second = bench.grid(None, 5, offset=20, limit=20)
check("paging runs over the list in force, so a late screen is not empty",
      first["pages_shown"], 60)
check("twenty pages at a time", (len(first["pages"]), len(second["pages"])), (20, 20))
check("starting where asked", (first["pages"][0]["page"], second["pages"][0]["page"]), (1, 21))
check("the total is reported so the client can size the grid", first["page_count"], 60)
# Reading the stored badges rather than the file is what makes paging through
# 396 pages twenty cheap queries instead of twenty 33 MB downloads.
check("and no download was needed", storage.downloads, 0)

# ── 10. The three faults a real session found ─────────────────────────────────

print("\n10. Re-entering is not a conflict (the warning that was always false)")

import util.redis_client as redis_mod  # noqa: E402

# A fake Redis good enough to tell an owned claim from a contested one.
class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def expire(self, key, ttl):
        return key in self.store

    def delete(self, key):
        self.store.pop(key, None)


fake = FakeRedis()
redis_mod.get_redis = lambda: fake
bench_mod.claim_for = redis_mod.claim_for
bench_mod.release_claim = redis_mod.release_claim

check("the first operator takes the pile", bench.take(5, "uid-para"), (True, "uid-para"))
# THE BUG: the bench re-opens its session after every bundle, so an anonymous
# claim reported the operator as somebody else from their second bundle onward.
check("and taking it again is still theirs, not a conflict",
      bench.take(5, "uid-para"), (True, "uid-para"))
check("a third call too", bench.take(5, "uid-para")[0], True)
check("but a different person is told the truth",
      bench.take(5, "uid-atty"), (False, "uid-para"))
bench.leave(5)
check("and after leaving, anybody may take it", bench.take(5, "uid-atty"),
      (True, "uid-atty"))
bench.leave(5)

# Redis unreachable grants the claim: a bench nobody can open because a cache is
# down is worse than two operators on one pile, which the exclusion constraint
# still keeps from corrupting a document.
def boom():
    raise RuntimeError("connection refused")
redis_mod.get_redis = boom
check("with Redis down the claim is granted rather than halting the bench",
      bench.take(5, "uid-para"), (True, "uid-para"))
redis_mod.get_redis = lambda: fake

print("\n11. Bundles are server state, readable after a reload")

src = pile(12)
source, docs, extents, storage = wire(src, pages=12, features=bench.page_features(src))
bench.bundle(None, 5, [1, 2, 3])
bench.bundle(None, 5, [7, 8, 15 - 7])     # 7, 8, 8 -> dedupes to 7-8
bench.bundle(None, 5, [10, 11, 12])

listed = bench.bundles(None, 5)
# An operator who came back was told there were four documents with no way to
# see which pages were in which, because the list lived in the client.
check("every bundle is listed", [b["document_id"] for b in listed], [1, 2, 3])
check("with its page ranges", [b["ranges"] for b in listed],
      [["1-3"], ["7-8"], ["10-12"]])
check("and the pages themselves, so the grid can mark them",
      listed[2]["pages"], [10, 11, 12])
check("a single-page bundle reads as one number, not a range",
      bench.bundles(None, 5) and bench.bundle(None, 5, [5]) and
      bench.bundles(None, 5)[-1]["ranges"], ["5"])
check("undo is offered while nothing downstream points at it",
      all(b["can_undo"] for b in bench.bundles(None, 5)), True)
docs.rows[1].statement_id = 900
check("and withdrawn once something does",
      [b["can_undo"] for b in bench.bundles(None, 5)][0], False)

print("\n12. A swept remainder is not called a separator unless it is one")

# Pressing Finish early sweeps the rest of the pile into one document. Labelling
# that `blank_or_separator` asserts those pages are not documents, which was
# false for most of a real pile. A flag must be true of the row it sits on.
# Over _MIN_TEXT_CHARS and all word-like, so these pages really are readable
# and calling them separators would be false.
_READABLE = ("ACME BANK statement of account for the period ending with the "
             "balance forward and the deposits and other credits shown below")
readable = pile(8, lambda n: _READABLE)
source, docs, extents, storage = wire(readable, pages=8,
                                      features=bench.page_features(readable))
bench.bundle(None, 5, [1, 2])
result = bench.finish(None, 5)
check("the remainder is swept", result["pages_swept"], 6)
check("but NOT labelled a separator, because those pages have text on them",
      result["swept_as_separator"], False)
check("so it carries no label at all, waiting for review",
      docs.rows[result["swept_document_id"]].doc_type, None)
check("and it still needs review",
      docs.rows[result["swept_document_id"]].needs_review, True)

# The real separator case: every swept page is blank.
mixed = pymupdf.open()
for n in range(1, 5):
    page = mixed.new_page(width=612, height=792)
    if n <= 2:
        page.insert_text((54, 72), "PAGE-MARKER-%d ACME BANK statement balance" % n,
                         fontsize=10)
blanks = mixed.tobytes()
mixed.close()

source, docs, extents, storage = wire(blanks, pages=4,
                                      features=bench.page_features(blanks))
bench.bundle(None, 5, [1, 2])
result = bench.finish(None, 5)
check("where every swept page IS blank, the label is true and applied",
      result["swept_as_separator"], True)
check("and says so", docs.rows[result["swept_document_id"]].doc_type,
      SEPARATOR_DOC_TYPE)

# ── 13. A file nobody has read is not a file that is finished ────────────────

print("\n13. Before the survey has run, the bench says so")

# THE BUG THIS PINS. With no page_features stored, nothing is listed and nothing
# is claimed -- and the screen announced "Every page is in a document" to
# somebody whose 73 MB production had not been read yet. An empty grid has two
# possible meanings and only one of them is "you are finished".
src = pile(12)
source, docs, extents, storage = wire(src, pages=12, features=None)

session = bench.open_session(None, 5)
check("the session says the file has not been read", session["surveyed"], False)
check("and claims no pages rather than zero remaining",
      session["page_count"], 0)
check("with nothing bundled", session["documents_so_far"], 0)

# open_session used to compute the features here, downloading 73 MB inside the
# request and racing the survey job doing the same work.
check("and it does NOT read the file to find out", storage.downloads, 0)

grid = bench.grid(None, 5)
check("the grid is empty, which is why the screen must not interpret it alone",
      grid["pages_shown"], 0)

# Once the survey lands, the same call answers normally.
source.page_features = bench.page_features(src)
ready = bench.open_session(None, 5)
check("after the survey it is surveyed", ready["surveyed"], True)
check("with its pages", ready["page_count"], 12)
check("and all of them unclaimed", ready["pages_unclaimed"], 12)

print("\n14. Finishing an unread file is refused, not silently swept")

# Pressing Finish on a file with no features would otherwise sweep nothing,
# report success, and leave a pile nobody had split looking finished.
source, docs, extents, storage = wire(pile(8), pages=8, features=None)
check_raises("nothing bundled is still a cancel, not a finish",
             lambda: bench.finish(None, 5), "nothing has been bundled")

# ── 15. A statement is named by what it IS ───────────────────────────────────

print("\n15. The bundle list names a statement, it does not number it")

# "statement #771" correlates to nothing a person can see -- not the exceptions
# queue, not the Accounts tab, not the paper in their hand. The same argument
# that put the filename and page range on the ingest label instead of a uuid.
import db.repositories.financial as fin_mod  # noqa: E402


class FakeStatements:
    def select_many(self, condition, **kw):
        return ([Row(id=771, financial_account_id=9,
                     period_start="2026-01-04", period_end="2026-02-03")], 1)


class FakeAccounts:
    def select_many(self, condition, **kw):
        return ([Row(id=9, institution="Bank of America",
                     account_number_last4="9260")], 1)


src = pile(6)
source, docs, extents, storage = wire(src, pages=6, features=bench.page_features(src))
bench.bundle(None, 5, [1, 2, 3])
docs.rows[1].statement_id = 771

fin_mod.FinancialAccountStatementRepository = lambda m: FakeStatements()
fin_mod.FinancialAccountRepository = lambda m: FakeAccounts()

listed = bench.bundles(None, 5)
check("the row carries the statement's identity",
      listed[0]["statement_label"],
      "Bank of America " + chr(0xb7) * 4 + "9260, 2026-01-04 to 2026-02-03")
check("and still the id, for anything that needs to join on it",
      listed[0]["statement_id"], 771)
# A document a statement was read out of cannot be unbundled: doing so would
# orphan the statement.
check("and it can no longer be unbundled", listed[0]["can_undo"], False)

print("\n16. Failing to name a statement never costs the list")


class Exploding:
    def select_many(self, condition, **kw):
        raise RuntimeError("database unavailable")


fin_mod.FinancialAccountStatementRepository = lambda m: Exploding()
degraded = bench.bundles(None, 5)
check("the bundles still come back", len(degraded), 1)
check("with their pages", degraded[0]["ranges"], ["1-3"])
# The screen falls back to the id, which is less useful and not nothing.
check("just without a label", degraded[0]["statement_label"], None)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all splitting-bench checks passed")
