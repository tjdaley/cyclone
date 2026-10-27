# The splitting bench

**Status: proposal. Nothing here is built.** The argument for it, and the
measurements behind that argument, are
[`SPEC_document_pipeline.md`](SPEC_document_pipeline.md) §5a and §13. This file
is the design.

---

## 1. One job

Turn one received PDF into N documents, by having a person say which pages
group. That is all it does.

**It does not classify, name, OCR, or stamp Bates numbers.** Each of those is a
separate step with its own screen or its own pass, and keeping them apart is
most of why the bench is fast: the operator makes one kind of decision, and the
decision is always visible on the thumbnail in front of them.

| | |
| - | - |
| **In** | A `source_documents` row — the as-produced file, OCR'd already if it needed it (§5 step 3 precedes step 2 for scanned input) |
| **Also in** | Any pile past the upload limits. Since 21 Oct 2026 those **route here instead of being refused**; an operator who does not want to split it cancels, and the unsplit source is kept |
| **Out** | One `documents` row per bundle, each with its page ranges and a working-copy PDF in Storage |
| **Reached when** | `pagination_coverage` is low — the pile does not describe its own structure. High coverage splits automatically and gets a spot check |

**Why a person and not a better model:** reviewing a wrong split costs more than
making the right one, measured — §5a. The 169-document pile has no good
automatic operating point at any cost ratio.

---

## 2. The data model changes, and this revises 047

A bundle may hold **discontinuous** pages, so a document's extent is a list of
ranges, not a first and a last.

```sql
create extension if not exists btree_gist;   -- for the exclusion constraint

create table if not exists document_source_pages (
    id              serial  primary key,
    document_id     integer not null references documents (id) on delete cascade,
    seq             integer not null,          -- reading order of the ranges
    page_first      integer not null,
    page_last       integer not null,
    unique (document_id, seq),
    check (page_last >= page_first),
    -- One document may not claim a page twice. Enforceable, and worth
    -- enforcing: a double-claimed page duplicates evidence inside one exhibit.
    exclude using gist (
        document_id with =,
        int4range(page_first, page_last, '[]') with &&
    )
);
```

`documents.source_page_first` and `source_page_last` are **removed.** Two
representations of one fact is the drift this codebase keeps meeting — the
published guide copies, the model-vs-column schema check, a price stored on a
usage row. The span is a view:

```sql
create or replace view document_page_spans
    with (security_invoker = on) as
select document_id,
       min(page_first)                           as page_first,
       max(page_last)                            as page_last,
       count(*) > 1                              as discontinuous,
       sum(page_last - page_first + 1)           as page_count
from document_source_pages
group by document_id;

revoke all on document_page_spans from anon, authenticated;
```

**The DDL above has not been run against a live server** — no Docker on the
machine it was written on, so `tests/test_cost_sql.py`'s path was unavailable.
The exclusion constraint is the part to check first: `document_id with =`
requires `btree_gist`, which no migration has installed before (001 installs
`pg_trgm` and `pgcrypto`). Add it to that test, which applies migrations against
real Postgres and exits **2 with SKIPPED** when Docker is absent — a check that
did not run must not read as a pass.

### Why this reverses an earlier decision, deliberately

`SPEC_document_pipeline.md` argued that an interrupted document must stay **two**
documents related by `continues_document_id`, because the production index cites
a Bates *range* and a list invites *"what is on 002986?"*. The condition
attached to that argument was that stitching two page ranges into one document
is an **assertion**, and so must be a recorded decision with somebody's name on
it rather than an inference by a splitter.

**The bench satisfies that condition directly.** An operator selecting pages
10–12 and 15–17 and pressing DONE *is* that assertion, made and recorded at the
moment of splitting. So one document with two ranges is correct here, and
splitting it into fragments to suit the exhibit's preferred shape would let the
output format dictate the evidence model.

`continues_document_id` survives as the **automatic** path's fallback, where no
person has made that assertion. It is not the primary representation.

And the question is still a good one: *what is on 002986?* The index answers it,
because another document is listed carrying that page.

### The trap: a boundary page can legitimately belong to TWO documents

There is deliberately **no** exclusion constraint across documents. A combined
statement prints one account's close and the next account's opening on the same
physical page — `_statement_pages()` cuts at printed headers precisely because
of this, and `SUSPECT_SPLIT` treats a single shared boundary page as normal
while treating a wider overlap as suspect.

So: non-overlap **within** a document is a constraint; overlap **between**
documents is a **finding**. The bench shows a page already claimed with a badge
naming the document that holds it, and allows the claim. Forbidding it would
make the commonest real multi-account statement unexpressible.

---

## 3. The screen

Twenty thumbnails on a large display, large enough to read a letterhead and a
date — that is the entire decision being made, so thumbnail size is the one
dimension not to economise on.

- Click or touch a page to add it to the current bundle; it highlights. Click
  again to release it. Non-adjacent pages are ordinary, not a special mode.
- **DONE** bundles the selection, which then disappears from the grid. The
  remaining grid *is* the progress indicator; no separate counter can disagree
  with it.
- Keyboard for a desk: arrows move, space toggles, Enter is DONE, Ctrl+Z undoes.
  Touch for a tablet. Both, because the operator's posture differs by pile size.

### Decisions that are not obvious

| | |
| - | - |
| **Never pre-select the detector's guess.** | The same rule as the truth worksheet's blank column, and it binds harder here: a pile reaches the bench *because* the free signals are weak, so a pre-selection reintroduces exactly the review-a-wrong-split cost this screen exists to remove. |
| **But show what the page PRINTS.** | A badge per thumbnail: `Page 1 of 4`, a header date, an account number, `unreadable`. These are facts read off the page and they are what the operator is looking for. A fact helps a decision; a conclusion has to be undone. Same distinction as `hdr_date` on the worksheet. |
| **UNDO is load-bearing, not a convenience.** | Bundled pages vanish, which is what makes the grid self-tracking and also what makes a mis-click unrecoverable by eye. The stack lasts the whole session. |
| **No name field, not even optional.** | Naming is what makes the Acrobat workflow slow, and it is a cost imposed by the file system being the data model. Here a document is a row; the file is `20261020-1021-001.pdf` and carries nothing. An *optional* name gets filled in, and then somebody downstream depends on it. |
| **The machine names it afterward.** | Step 4 classifies and proposes the `description` from the document's own content. The division of labour is the reverse of the obvious one: the human groups, which only a human can do on this pile; the machine names, which is the part that was slow. |
| **Bundle from the as-produced source by page number.** | Never from a re-uploaded reassembly. That is what keeps the lineage in §4 true. |

### Pages that are not documents

A blank separator sheet was still produced and still carries a Bates number, so
it cannot simply be left out: a cumulative index that omits a produced page is
short, which is the one thing that index must not be (§5a).

At **finish**, the remaining unclaimed pages are swept into one document with
`doc_type = 'blank_or_separator'`, named and described like any other. That
accounts for them without asserting they are documents — the same move as
`no_marital_interest` on an account, which exists so a judgment leaves the queue
with a record instead of being carried in somebody's head.

Finishing with unclaimed pages is therefore never silent, and never blocked.

---

## 4. API

Routes thin, logic in `services/splitting_bench_service.py`, schemas in
`app/schemas/bench.py`, all under `require_role`.

| Method | Path | Does |
| --- | --- | --- |
| `POST` | `/api/v1/source-documents/{id}/bench` | Open or resume a session; takes the lock |
| `GET` | `/api/v1/source-documents/{id}/bench/pages` | The grid: per page its number, badges, and which document claims it |
| `GET` | `/api/v1/source-documents/{id}/bench/pages/{n}/thumbnail` | PNG/JPEG, `?w=450` |
| `POST` | `/api/v1/source-documents/{id}/bench/bundles` | `{pages: [10,11,12,15,16,17]}` → creates the document, its ranges, its working PDF |
| `DELETE` | `/api/v1/bench/bundles/{document_id}` | Undo: deletes the document, its ranges and its working PDF |
| `POST` | `/api/v1/source-documents/{id}/bench/finish` | Sweeps unclaimed pages, releases the lock, reports the result |

- **`pages` takes a page list, not ranges.** The client holds a selection, not
  an interval; the service coalesces into ranges, which is the one place that
  conversion happens.
- **The undo is a hard delete**, matching the rule for a statement or an account:
  the as-produced source in Storage is the undo, so soft state here would infect
  the grid, the unclaimed count and the index for no gain. It is refused once
  anything downstream points at the document — a classification is cheap to
  redo, a request mapping somebody approved is not.
- **Finish is idempotent** and reports counts rather than returning 204: how many
  documents, how many pages swept, how many pages claimed twice.

---

## 5. Thumbnails: rendered on demand, nothing stored

Measured on the 396-page pile with PyMuPDF, which is already a dependency:

| Width | Per page | A 20-page screen | All 396 pages |
| --- | --- | --- | --- |
| 320px | 18.9 ms | 0.38 s | 14.8 MB |
| **450px** | **24.6 ms** | **0.49 s** | 27.6 MB |
| 700px | 43.4 ms | 0.87 s | 58.6 MB |

Confirmed on the real 396-page scanned production, not only the synthetic
fixture: **0.54 s per 20-page screen at 450px as JPEG, 0.52 s as PNG** (62 KB
and 81 KB per page). JPEG's size advantage over PNG is smaller on these scans
than on a native-text page, and the time is the same; the default stands
because a scan is a photograph, but either is affordable.

Half a second for a screen at a readable size, so **render per request with a
small in-process LRU and store nothing.** What that avoids is worth more than
the milliseconds it costs:

- **No multi-node cache problem.** The API runs behind haproxy on more than one
  node; a disk cache on one is invisible to the other, and sticky sessions to
  fix that is a load-balancer change to serve a thumbnail.
- **No storage cost line and nothing to purge.** A thumbnail is derived and
  disposable — regenerable from the as-produced file forever. Storing 28 MB per
  pile would land in the §10 cost report and in the retention question for no
  reason.
- **No stale cache after a re-OCR.** The source changes, the thumbnail follows.

Render through a narrow function in `pdf_service` (the only module that may
import `pymupdf`, §18) rather than in the bench service.

---

### Writing a bundle is free, and the storage answer is the one already accepted

Measured on the same production (33.2 MB, 396 pages, 81.9 KB per page):

| Bundle | Size | Per page | Time |
| --- | --- | --- | --- |
| 3 contiguous pages | 221 KB | 73.5 KB | 4 ms |
| Discontinuous, 10–12 + 15–17 | 478 KB | 79.6 KB | 5 ms |

**A child carries its own weight, not its parent's**, which was the thing worth
checking: `insert_pdf` on a scanned PDF can drag a shared resource into every
output and inflate 169 documents to the size of the source each. With
`garbage=3` it does not — per-page size comes out slightly *below* the source's.

Which also answers the storage question raised when the as-produced record was
first argued for: all the working copies of a pile together come to roughly the
size of the pile, so holding both is about **2x**, not 170x. That is the number
already accepted, and the derived artifacts that would have multiplied it —
thumbnails — are not stored at all.

## 6. Bates numbers

`bates_service` already produces a per-page map for the parent. A bundle's
stamps follow from its page ranges, and nothing is re-detected per child.

- `documents.bates_first` / `bates_last` keep the **outer bounds**, because the
  index sorts on a single first stamp, plus a `bates_discontinuous` flag.
- The **precise list** comes from the ranges, and the index prints each
  contiguous run: `003066–003068, 003071–003073`.
- A page with no detected stamp contributes nothing rather than a constructed
  number — unchanged rule, and the reason the bench never invents one.

---

## 7. Concurrency, and audit

**One operator per pile**, enforced with the Redis `claim_once` already used for
job claiming (§11a): the key is `bench:{source_document_id}`, and Redis being
unreachable degrades to the session row in the database rather than halting the
bench. Two people bundling the same grid would otherwise race on the same pages,
and the loser's work is a duplicate document nobody notices until the index.

**Splitting is an evidentiary act, so every bundle writes an `audit_log` entry**
through `AuditLogger` — who, when, the page list, the document created. §13's
list gains a row. A split is how a produced page becomes a document somebody
cites; "who decided these pages were one document" has to be answerable without
asking around.

---

## 8. Build order

1. ✅ **PDF authoring in `pdf_service`** — `render_page`, `write_pages`,
   `coalesce_pages`, plus `page_texts` for the badges.
   `tests/test_pdf_authoring.py`.
2. ✅ **`document_source_pages`, the span view, and the `documents` revision** —
   now `db/migrations/047_document_pipeline.sql`, verified against real
   Postgres 16 by `tests/test_document_pipeline_sql.py`.
3. ✅ **The endpoints and the grid** — all six routes, `splitting_bench_service`,
   and `SplittingBenchPage`. `tests/test_splitting_bench.py`.
4. **`pagination_coverage` on the upload**, so a pile routes itself here instead
   of being sent here by hand. **Next.**
5. **Reaching the bench from somewhere** — nothing yet creates a
   `source_documents` row, so the bench is reachable only by URL. The upload
   path is what step 4 needs anyway.

### What the build taught, which the design had not

- **The grid needs no session table.** It is derived from the file plus the page
  ranges existing documents claim, so opening only takes the lock and resuming
  is free. One fewer table, and a browser can be closed mid-pile.
- **The thing worth caching is the source PDF, not the thumbnails.** §5 argued
  for rendering on demand and storing nothing, which is right, but a 33 MB
  download per thumbnail is 660 MB for one screen. The bytes are cached per node
  and every thumbnail renders from them: measured, twenty thumbnails, one
  download.
- **Badges cannot be computed per request.** They need the text layer of every
  page, so they are computed once when a session opens and cached in
  `source_documents.page_features`. Paging through 396 pages is then twenty
  cheap queries rather than twenty downloads.
- **Claimed pages have to be recoverable.** They drop out of the grid by
  default — that is the progress indicator — but `include_claimed` brings them
  back, and it is not a debugging convenience: it is the only way to reach the
  page that belongs to two documents, which a combined statement requires.
- **A thumbnail cannot be an `<img src>`.** The endpoint is behind
  `require_role` and an image element carries no bearer token, so each is
  fetched as a blob and the object URL is revoked on replacement and unmount.
  A 396-page pile scrolled end to end is 396 of them.

Steps 1–3 are usable by a person on a real pile before any classifier exists,
which is the same property that made the compliance grid worth shipping early.

---

## 9. Open

- **Does the operator want the pile in received order, or sorted?** Received
  order preserves adjacency, which is most of the signal. Sorting by anything
  destroys it. Probably no option at all, but worth asking somebody who will use
  it.
- **How is the size of the job estimated?** Deciding to route an oversized pile
  here rather than refuse it (§5a of the pipeline spec) means the bench has to
  say what the operator is in for before they start, or cancelling is not a real
  choice. Page count and expected document count are free; minutes needs a
  measured interaction rate, which does not exist until somebody uses it. Ship
  the counts, add the estimate once there is one to base it on.
- **Should the bench show the OCR text of a page on demand?** A thumbnail at
  450px shows a letterhead, not a transaction line. One click to read the text
  would settle the occasional hard page — and it is also how the screen starts
  becoming a document viewer, which is a different feature.
- **Whether `blank_or_separator` wants to be excluded from the taxonomy's
  options** or be one of them. It is a real label and it is also not a document;
  the registry (§6) decides what it routes to, which is nothing.
