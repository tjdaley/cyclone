"""
tests/test_document_to_statement.py - A split document reaching the statement ingest.

Until this link existed, splitting a statement pile on the bench stranded the
pieces — which is what kept the oversized upload REFUSING rather than routing a
person to the bench. One link, and the refusal becomes a route.

What this pins:

- **A document holding several statements is NOT linked to one of them.** A
  combined bank package is one document with three accounts in it, and
  `documents.statement_id` is singular. Picking one would make the production
  index cite one account's period for a document covering three. The ids go on
  the document's flags instead, and the index reads the document's own period.
- **An ingest that found nothing is a finding about the document**, not a failed
  job: somebody sent a letter to the statement processor. It comes back flagged
  and needing review.
- **The job points at the document's existing object**, so nothing is uploaded a
  third time and the statement's "show me the source" opens the split document
  rather than page 47 of a 396-page production.
- **Losing the back-link never fails a job that succeeded.** The statements are
  committed by that point.

Run:  venv/Scripts/python.exe tests/test_document_to_statement.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import services.document_processor_service as proc_mod  # noqa: E402
from services.document_processor_service import (  # noqa: E402
    ProcessorError, document_processor_service as proc,
)
from services.job_service import JobService  # noqa: E402

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


def check_raises(label, fn, fragment, kind=ProcessorError):
    try:
        fn()
    except kind as e:
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


class FakeDocs:
    def __init__(self, row):
        self.row = row
        self.patches: list[dict] = []

    def select_one(self, condition):
        return self.row if self.row and condition.get("id") == self.row.id else None

    def update(self, rid, patch):
        self.patches.append(patch)
        self.row.__dict__.update(patch)
        return self.row


class FakeExtents:
    def __init__(self, ranges):
        self.ranges = [Row(page_first=a, page_last=b, seq=i + 1)
                       for i, (a, b) in enumerate(ranges)]

    def get_by_document(self, _):
        return self.ranges


class FakeSources:
    def select_one(self, condition):
        return Row(id=5, original_filename="Shahnawaz vol 2.pdf")


ENQUEUED: list[dict] = []


class FakeJobService:
    def enqueue_statement_ingest(self, manager, staff_id, matter_id, pdf_bytes,
                                 bates_prefix=None, source_filename=None,
                                 storage_path=None, document_id=None):
        ENQUEUED.append({
            "staff_id": staff_id, "matter_id": matter_id,
            "pdf_bytes": pdf_bytes, "bates_prefix": bates_prefix,
            "source_filename": source_filename,
            "storage_path": storage_path, "document_id": document_id,
        })
        return Row(id="job-1", status=Row(value="queued"))


def wire(**overrides):
    row = Row(id=11, matter_id=311, source_document_id=5,
              storage_path="matters/311/documents/11.pdf",
              statement_id=None, flags=[], needs_review=True, withheld=False)
    row.__dict__.update(overrides)
    docs = FakeDocs(row)
    proc_mod.DocumentRepository = lambda m: docs
    proc_mod.DocumentSourcePageRepository = lambda m: FakeExtents([(10, 12), (15, 17)])
    proc_mod.SourceDocumentRepository = lambda m: FakeSources()
    proc_mod.job_service = FakeJobService()
    ENQUEUED.clear()
    return row, docs


# ── 1. Sending it ─────────────────────────────────────────────────────────────

print("\n1. A document goes to the statement ingest")

row, docs = wire()
job = proc.send_to_statement_ingest(None, 11, staff_id=7)
check("a job comes back", job.id, "job-1")
check("for the document's matter", ENQUEUED[0]["matter_id"], 311)
check("naming the document, so the statement can be linked back",
      ENQUEUED[0]["document_id"], 11)
# Nothing is uploaded a third time, and the statement's "show me the source"
# then opens the split document rather than page 47 of a 396-page production.
check("pointing at the object already in storage",
      ENQUEUED[0]["storage_path"], "matters/311/documents/11.pdf")
check("and sending no bytes", ENQUEUED[0]["pdf_bytes"], b"")
# Storage renames every object, so without a label a statement in the
# exceptions queue traces back to a uuid.
check("with a label naming the file AND the pages",
      ENQUEUED[0]["source_filename"], "Shahnawaz vol 2 pp. 10-12, 15-17")

print("\n2. What it refuses")

wire(storage_path=None)
check_raises("a document whose split did not finish",
             lambda: proc.send_to_statement_ingest(None, 11, 7), "no file yet")

wire(statement_id=900)
check_raises("one a statement has already been read out of",
             lambda: proc.send_to_statement_ingest(None, 11, 7), "already been read")

wire()
check_raises("a document that is not on record",
             lambda: proc.send_to_statement_ingest(None, 404, 7), "not on record")

# ── 3. Linking back ───────────────────────────────────────────────────────────

print("\n3. Linking the statement back to its document")

link = JobService._link_document
import services.job_service as job_mod  # noqa: E402


def wire_link(**overrides):
    row, docs = wire(**overrides)
    # _link_document imports the repository inside the function, so patch the
    # module it imports from.
    import db.repositories.document as repo_mod  # noqa: PLC0415
    repo_mod.DocumentRepository = lambda m: docs
    return row, docs


row, docs = wire_link()
link(None, Row(params={"document_id": 11}),
     {"results": [{"statement_id": 900, "status": "auto_accepted"}]})
check("one statement is linked", row.statement_id, 900)
check("and the document no longer needs review", row.needs_review, False)

# A combined bank package is one document with three accounts in it. Picking one
# would make the index cite one account's period for a document covering three.
row, docs = wire_link()
link(None, Row(params={"document_id": 11}),
     {"results": [{"statement_id": 900}, {"statement_id": 901}, {"statement_id": 902}]})
check("several statements link to NONE of them", row.statement_id, None)
check("and say so on the document",
      [f["code"] for f in row.flags], ["SEVERAL_STATEMENTS"])
check("naming the statements, so they are findable",
      "900, 901, 902" in row.flags[0]["note"], True)

# Somebody sent a letter to the statement processor. That is a finding about the
# document, not a failure of the job.
row, docs = wire_link()
link(None, Row(params={"document_id": 11}), {"results": []})
check("finding no statement is flagged on the document",
      [f["code"] for f in row.flags], ["NOT_A_STATEMENT"])
check("at warn, because somebody has to look", row.flags[0]["severity"], "warn")
check("and it goes back for review", row.needs_review, True)

row, docs = wire_link()
link(None, Row(params={}), {"results": [{"statement_id": 900}]})
check("a job with no document_id links nothing", row.statement_id, None)
check("and touches nothing at all", docs.patches, [])

print("\n4. Losing the link never fails a job that succeeded")


class Exploding:
    def select_one(self, condition):
        raise RuntimeError("database unavailable")


import db.repositories.document as repo_mod  # noqa: E402
repo_mod.DocumentRepository = lambda m: Exploding()
try:
    link(None, Row(params={"document_id": 11}), {"results": [{"statement_id": 900}]})
    print("  ok   a failure to link is swallowed, not raised")
except Exception as e:  # noqa: BLE001
    print("  FAIL a failure to link raised %s" % type(e).__name__)
    FAILURES.append("link failure swallowed")

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all document-to-statement checks passed")
