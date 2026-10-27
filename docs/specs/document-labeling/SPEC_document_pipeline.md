# Document Pipeline — working specification

**Owner:** Tom Daley
**Status:** Design agreed in outline; data model drafted; nothing built.
**Supersedes:** `SPEC_discovery_pipeline.md` (as received — kept untouched, see `README.md`)
**Companion:** `db/migrations/047_document_pipeline.sql`, `discovery_classifier_schema.json` (taxonomy draft, as received)

---

## 1. What this is

A pipeline that takes a folder of documents as produced — by opposing counsel,
by a client, by a subpoena witness — and turns it into a labeled, searchable,
indexed production that can answer two questions:

- **Ours:** which documents respond to which requests, served as a Rule 196.3(c)
  production index over counsel's signature.
- **Theirs:** which requests they have not answered, and which months of which
  accounts are missing, as the first draft of a deficiency letter.

The existing statement ingest is one processor inside it, not the thing being
replaced.

## 2. What changed from the spec as received

The received spec was written without access to this repository, so it proposed
layers that exist and a data model that duplicates three deployed tables. The
reasoning in it is sound and mostly survives.

| As received | Revised | Why |
| --- | --- | --- |
| New `matter`, `request_set`, `request` tables (uuid) | Use `matters`, `discovery_requests`, `discovery_request_items` (integer) | They exist. Twins would give the database two matter tables and break the matter boundary every query depends on |
| `document` holds Bates, periods, description for everything | `documents.statement_id` references the statement when there is one | A bank statement already has periods and a Bates range. Two records of one Bates range is how the index comes to cite a range the statement record contradicts |
| `bates_prefix` + `bates_begin/end` as integers | `bates_first` / `bates_last` as text, parsed by `bates_service.parse_stamp` | The firm's stamps are `KF 000101`, and the separator is reprinted as the production writes it. One parser, already written |
| **One document per file; no splitting** (§2, §9) | **Splitting is step 2 and in scope** | Productions arrive as scanned blobs. The upload already refuses >50 pages / >8 statements because the alternative was a total loss |
| Vendor-neutral classifier interface with per-vendor adapters (§4.5) | A profile in `llm_profiles.json`, dispatched by `llm_service` | A second dispatch layer bypasses failover, timeouts, the usage ledger, who answered, and why it stopped. Classifier spend would be invisible on the Costs page |
| Responsiveness scored **per document** against each request (§4.6) | Scored **per label**, with per-document overrides | Tom's change, and the better one. See §8 |
| Working copies only | **As-produced record kept beside working copies** | See §4. This is the section the rest of the design rests on |

## 3. The pipeline

```
[0] Convert to PDF            non-PDF original (TIFF, HEIC, DOCX, screenshot)
 │                            → as-produced original kept, byte for byte
[1] Folder scan               detect new files; key on CONTENT HASH, not path
 │
[2] Split                     one file → one document per document
 │                            cut on what the page PRINTS, not a model's guess
[3] Text layer                native where it exists; OCR where it does not
 │
[4] Label                     taxonomy; one call per document, first 1–2 pages
 │
[5] Dispatch                  registry keyed on label → processor
 │                                 │
 │                                 ├─[6] statements → EXISTING ingest
 │                                 ├─ brokerage → + holdings extraction
 │                                 └─ everything else → metadata only (for now)
 │
[7] Discovery requests        ours and theirs, with operative_text
[8] Label → request mapping   many-to-many, approved once, inherited by label
[9] Reports                   production index (ours) · deficiency (theirs)
```

Each stage writes its result, so any stage re-runs without repeating the others.
That is not a nicety: step 3 is the expensive one and step 4 is the one whose
prompt will change most often.

## 4. Fidelity: the as-produced record and the working copy

**Two records of every document. The original is never worked from, and the
working copy is never confused for it.**

In manual practice a split, OCR'd PDF is treated as equivalent to the file
received, and that holds up because a *person* did the splitting and can say
what they did. A pipeline that splits a 700-page scan into 120 children, burns
in an OCR layer, and rescales a 200 MB TIFF to letter size is four
transformations nobody witnessed. The as-produced copy is what makes the
pipeline's output defensible rather than merely useful — the same principle
already applied at smaller scale by `MANUAL_ENTRY`, `BALANCE_MANUALLY_ENTERED`
and extraction provenance, applied to the document itself.

**Step 0 is why this is not optional.** Converting a TIFF or a phone photograph
to a letter-size PDF discards EXIF: capture time, GPS, device. In a family-law
matter a photograph's timestamp and location are routinely *the* evidence. If
the only copy kept is the converted one, our own pipeline destroyed the evidence
before anybody asked for it. Everything else in the pipeline is recoverable by
re-running work; that is not.

### The rules

| | |
| --- | --- |
| **Hash on receipt** | `source_documents.content_sha256`, taken before anything else touches the file. Every working copy's lineage is then provable rather than asserted, and it is the dedup key: a supplement re-serves documents and the cumulative index must not list one twice |
| **Record the bytes on the row** | Not derived from storage later. See §10 |
| **Append-only** | Nothing in the pipeline updates `source_documents` after insert |
| **Never a working surface** | The as-produced copy is not indexed, classified, OCR'd, searched or served. One button — *original as produced* — and otherwise invisible. The moment people work from it there are two working repositories and the discipline is gone |
| **Lineage is a column** | `documents.source_document_id` plus `source_page_first/last`. "Where did this come from" is a join, not an afternoon matching mangled filenames |

### The storage layer can re-encode an image, and that is a fidelity problem

Supabase turns image transformation on automatically. It cannot touch anything
today and the reasons are worth writing down, because both of them expire the
moment step 0 is built.

**Today it is harmless, twice over.** PDF is not a transformable source -- the
feature covers PNG, JPEG, WebP, AVIF, GIF, ICO, SVG, HEIC, BMP and TIFF -- and a
transformation happens only when a caller asks for one, which produces a
`/storage/v1/render/image/` URL. Nothing here asks. Checked against Supabase's
documentation in October 2026: transformations are applied on request, never at
upload, and a file nobody transforms is untouched.

**Step 0 ends both of those.** Converting a photograph or a TIFF to PDF is
precisely where metadata dies -- EXIF capture time and GPS are routinely the
evidence in a family-law matter, which is the sharpest argument for keeping the
as-produced record at all (§4). But the as-produced original of such a file is a
JPEG, a HEIC or a TIFF, and **all three are transformable sources.**

Serving one through a transform returns a re-encoded image, WebP by default,
with its metadata stripped -- and it looks like the photograph. **A derived
image offered as the original is an evidentiary failure that leaves no trace in
the file it produces**, which is the same shape as a pro-forma statement wearing
a court caption, and worse because nothing on the image says what happened to
it.

So when images are stored:

| | |
| - | - |
| **The as-produced original is served by one function and no other.** | `storage_service.get_signed_url`, with no `transform` -- not even `format: 'origin'`, which still goes through the render path. |
| **A thumbnail is a DERIVED artifact and says so.** | The same distinction the working copy already carries. An exhibit photograph's preview is not the photograph. |
| **Or turn the feature off.** | It costs nothing while no image is ever transformed, and a setting that cannot be reached is stronger than a convention about who calls what. |

The limits are a tell rather than a defence: transformation caps at 25 MB and 50
megapixels, so a scanned TIFF would often fail it outright. That means the
mistake would be noticed, not that it cannot be made.

**Enforcement, not convention.** The multipart upload routes have refused
anything but `application/pdf` since they were written. The signed-URL path did
not -- "we only upload PDFs" held there by habit -- and it does now, because the
whole argument above rests on it.

### Why we hold it rather than the attorney

The alternative — Cyclone keeps working copies, the firm keeps its own
as-produced folder — fails in one predictable way: **two repositories with no
link between them.** When the question comes, somebody matches a working
document back to a file in a folder nobody has opened in two years, by filename,
which is exactly what splitting and conversion mangle.

Being honest about where this earns nothing: it is not needed for presentation
(the working copy is visually identical and is the better one to print), and
most matters will never call on it. It is insurance. The metadata case is thin
for bank statements and decisive for photographs, texts and native files.

### Storage

Supabase storage is roughly **$0.021/GB/month**. Ten gigabytes of as-produced
archive is about **21¢ a month**; a 2 GB production is about four cents. One
long statement ingest — chunked calls to Claude or OpenAI — costs more than
storing the document it read for a year. If storage ever becomes the line worth
cutting, the Costs page will say so, and it will not.

## 5. Stages 0–4

### Step 0 — convert to PDF

New capability, shared with steps 2 and 3: **nothing in this codebase writes
PDFs today.** `pdf_service` reads (`survey`, `extract_text`, `ask_page`, vision
fallback); the only `.save()` calls are DOCX and generated exhibits. One
module, authored once, serving step 0, step 2, and the **paused Bates stamping**
in §16a — which stops being a separate decision once Cyclone is writing PDFs
anyway.

Record the original's media type and byte count; keep the original bytes.

### Step 1 — folder scan

Already designed in §16a ("the upload monitor"): a connection per provider
(Egnyte today; Dropbox, Drive, S3 later), a folder path per matter, app
credentials fleet-wide in SSM and the per-connection token encrypted with
`pgcrypto`. **Key on content hash, not path** — files are renamed and moved.
Triage cheaply before asking a model anything: page count, text layer,
`statement period` / `beginning balance` / account-number markers.

### Step 2 — split

**Cut on what the page prints.** `_statement_pages()` already does this for
combined statements, slicing at each account's printed header, for a stated
reason: a header is a line the bank prints, and locating it is exact where
judging where a document *ends* is a model's opinion. `_PAGE_ONE` already
detects restarted pagination. Generalise both; treat a model's boundary guess
as the fallback, not the method.

**ON SCANNED INPUT, STEP 3 COMES FIRST.** Cutting on what the page prints
needs the page's printing to be readable, and so does any model asked whether
a page continues the one before it. A pile that arrives as images has neither,
so the order is 0 → 1 → **3** → 2 → 4 for that input and 0 → 1 → 2 → 3 → 4 for
native text. Measured rather than assumed: `scripts/split_lab.py features`
reports the share of pages carrying a usable text layer and says so when most
of them do not. The practical consequence is that OCR is **not** scoped to a
document, because at that point there are no documents yet — it runs over the
whole received file, and `source_documents` is what it attaches to.

**Two consequences:**

- **The upload gate moves.** The 50-page / 8-statement refusal exists because a
  700-page production could not work. With a splitter in front, the limit
  belongs on the document after the split, not on the upload.
- **Splitting and Bates are coupled.** A child's range derives from the parent's
  page numbering, so stamps must be established before or during the split, and
  the split records parent→child page offsets (`source_page_first/last`).
- **Cyclone applies stamps where none are detected — but never silently.** A
  confirmation names what was found and what it is about to do, because applying
  a series to a production that already carries one **defaces it**, leaving a
  document with two competing series. (That is why the `bates_prefix` upload
  override exists.)

  Worth stating precisely, because the symptom is easy to misread: detection is
  **pattern-based over the page text, not a model reading the page**
  (`bates_service`). On a one- or two-page document it declines not because it
  "did not see" the stamp but because **repetition is the whole signal** and two
  pages cannot establish a run — the same rule that makes an account number
  undetectable on a single page. So the confirmation has to distinguish *no
  series could be established* from *a series exists and this page is
  unstamped*: the first invites stamping, the second forbids it.

### Step 3 — text layer

Two different jobs, and conflating them is expensive:

| Job | Tool | Why |
| --- | --- | --- |
| Make a document **classifiable and findable** | **Hosted OCR with geometry — AWS Textract is the candidate** | Per-page accuracy matters less than coverage, and this runs over whole productions |
| **Read evidence** off a page | LLM vision (`pdf_service`) | Amounts and payees have to be right |

**Tesseract is ruled out, and not on theory.** Measured on this firm's documents
last week: bank statements came back **unusable**. It is already absent from the
codebase for the evidence-reading job; it is now absent from this one too. An
earlier draft of this section said "deterministic OCR" and meant something like
Tesseract — that was wrong, and the correction is the measurement.

**Textract, from a year of prior use, with three consequences:**

| What it does | What that means here |
| --- | --- |
| **Asynchronous past five pages** | OCR is a **job**, never inline. Fits §11a exactly: anything that can take more than a few seconds does not hold a request open |
| **Returns geometry, not a text layer** | *We* build the layer, inserting invisible text at the reported word boxes. That is the second job of the PDF-authoring module (§5 step 0), after split and stamp |
| **No font information** | Irrelevant for an invisible search layer. Word *position* is what Ctrl-F highlights, so geometry fidelity is what matters |

**Cost:** per page, which makes OCR the first **non-LLM metered service** — and
it fits `service_rates` unchanged (`provider='aws'`, `model='textract'`,
`meter='page'`), priced at read time like everything else. That is
cost-tracking step 3's "monthly service-cost entries" arriving earlier than
expected, and it is a reason to prefer a metered engine over a flat-rate one:
the per-matter figure stays derivable.

**And it is already inside the trust boundary.** The nodes run under an IAM
instance role and already talk to AWS for SSM, so there is no new credential and
no new vendor holding client records — a real advantage over a third-party OCR
service given §11. **To verify before committing:** that Textract is on AWS's
HIPAA-eligible services list and whether the firm needs a BAA in place, since
this corpus carries PHI.

#### A text-layer artifact worth knowing about

Some PDFs render **bold by drawing the same text twice at slightly offset
coordinates**. The extracted text then carries doubled words — and a doubled
*amount* is the kind of thing that reconciles to a wrong number rather than an
obviously wrong one. Not seen recently, but some bank is still running that
software.

The tell is identical adjacent tokens at near-identical coordinates, which is
exactly what geometry makes detectable and a flat text stream does not. Worth a
check in the text-layer step rather than a surprise inside an extraction.

Record which happened (`documents.text_layer`), because a figure read from an
OCR layer is a reading of a reading and the UI should say so. `_sanitize` still
applies: a NUL from a badly encoded page makes Postgres reject the whole row.

#### Settled: a text layer in the PDF, not a markdown conversion

**One artifact per document.** The alternative considered was converting pages
to markdown, preserving tables as far as markdown can express them. Tom's three
reasons for rejecting it are the right ones, and two of them generalise:

- **The working copy has to be displayable and printable**, which is the whole
  job of the working copy. Markdown is not the document.
- **Markdown has no page**, and here the page number is a *citation*, not
  formatting: `physical_page_number` on every transaction, a Bates stamp per
  page, "open the PDF at the page this line is printed on", and a gap scan that
  runs over page spans. Page markers *can* be carried in a text stream — the
  statement path already does it with `extract_text(page_markers=True)` — but
  that produces text for a model to read, not a document for a person. Both
  exist; only the second is missing.
- **A part-PDF, part-markdown document** makes every downstream consumer ask
  which pages came from where. An OCR layer keeps every page a page.

And the reason that matters most here: **two representations of one document is
the failure this codebase repeats most.** The prompt's account-type list against
`_CASH_FLOW_ACCOUNTS`, a statement's balance against an index's copy of it, the
published guide against its source — each one drifted, and the markdown variant
has no test that could catch it.

Four precisions on the burned-in layer:

1. **No visible mark on the page.** A watermark would make the working copy
   *less* faithful to what it represents, and faithfulness is the point. The
   record lives in `documents.text_layer` plus the as-produced hash, not on the
   paper.
2. **OCR quality is recorded and gates trust.** Where the engine reports
   confidence, keep it in `text_quality`; a poor layer routes to review rather
   than being quietly relied on.
3. **A statement whose text layer is OURS carries a flag** — the existing rule
   is vision for evidence, and a figure extracted from a synthetic layer is a
   reading of a reading. `TEXT_LAYER_SYNTHETIC` (info) on the statement, by the
   same rule that flags `YEAR_INFERRED` and `ACCOUNT_NUMBER_DERIVED`.
4. **The original is never the thing OCR'd.** The layer goes into the working
   copy; `source_documents` is untouched (§4).

### Step 4 — label

Send the **first one or two pages** only: type is evident from the header, and
sending less is cheaper and exposes less. Keep the companion yes/no questions —
they cost nothing in the same call and each one routes or warns.

| Answer | Action |
| --- | --- |
| confidence below threshold, or `other_unknown` | Review queue. `other_unknown` stays mandatory: without it the model forces every oddball into a real class |
| `multiple_documents` | Back to step 2 — a **mixed-type** file |
| `multiple_accounts` | **Not** back to step 2. A combined statement is one file, several accounts, and the statement path handles it deliberately |
| `attorney_involved` | Privilege **tripwire**, gating production. Never a privilege determination |

`text_quality` and the flags land in `documents.flags`, by the house rule that
an inference is flagged.

## 6. Step 5 — the processor registry

**Config for the shape, code for the control flow.**

One registry keyed on taxonomy label. Each entry declares the profile name, the
extraction shape (a Pydantic model), the page/chunking policy, and the
post-commit checks. A generic runner walks it. Subclass only where a type needs
genuinely different *control flow* — and statements prove some do: index →
window → chunk → reconcile → date audit → completeness is not a config knob.

Two rules, both lessons already paid for here:

- **The classifier's option list is generated from the registry.** The statement
  prompt once stated its account types in English while Python held them in
  `_CASH_FLOW_ACCOUNTS`; they drifted, and a policy statement was *read* with
  bank rules and *checked* with brokerage rules. The prompts are now built from
  the enum at import. One list, derived — never two.
- **Most labels need no extractor on day one.** About 30 of the 38 need label +
  Bates + period to populate the index, and a metadata-only processor is the
  correct processor for them. That is what keeps the first slice small.

## 7. Step 6 — statements, and brokerage holdings

Bank, card, brokerage, retirement, life-insurance and annuity statements route
to the existing ingest unchanged.

**Brokerage holdings are a second extraction over the same document, not a
re-route.** A brokerage statement is read cash-flow only today: trades
deliberately skipped, balances taken as total account value from the portfolio
summary. Holdings — positions, share counts, market value as of the period end —
are a different question over the same pages, so the document gains an output
rather than changing processor.

- Its own record (`statement_holdings`, one row per position per statement
  period). Not stretched into the transaction table, which carries one sign
  convention for a reason.
- Worth capturing before tracing exists: inception-and-tracing arguments need
  holdings at the marriage date and at key dates, and those statements are in
  the production today. The known gap — a liquidation that stays in cash inside
  the account — is exactly what holdings close.

## 8. Steps 7–9 — requests, mapping, reports

### Step 7 — requests, both directions

`discovery_requests` models only discovery served **on** our client. `direction`
(047) is what lets the same tables hold a set we served, and the
compliance matrix's precedent applies wholesale: **there are two of these
reports, not one**, each bounded by its own side's dates.

`operative_text` is new and load-bearing: a model is scored against a
self-contained restatement, never the verbatim request, because a narrowing that
lives only in `objections` never reaches it otherwise.

### Step 8 — label → request, which is the spine

**One judgment per (request, label), not per (document, request).**

| | Per document | Per label |
| --- | --- | --- |
| Judgments per matter | 3,000 docs × 20 requests | ~6 labels present × 20 requests |
| What counsel reviews | A probability per row | *"bank_statement is responsive to 1 and 7"* — a sentence an attorney can affirm |
| Next production month | Score every new document | Inherits by label; no new judgments |

It is a legal conclusion, so it is per matter (through the request), attributed,
and correctable — the shape payee rulings and account characterizations already
have.

**Two things the label alone cannot do**, which is why `document_request_overrides`
exists and holds both directions:

- **Scope.** "All statements since January 2022" — a 2019 statement carries the
  right label and falls outside the request. The label answers *kind*; period
  and account holder answer *scope*, and the rules pass must do both.
- **Document-level facts.** A tax return is responsive to a debts request only
  where it shows an amount due. Tom's own example named this case.

An override is a **row, not a delete** — the same reason a cleared client tag is
a row: a suppression has to say who made it.

Worth noting what the taxonomy makes visible: a request for "documents showing
rewards points or miles" maps to `credit_card_statement`, a document whose
rewards block Cyclone deliberately refuses to extract because it double-counts
purchases. **Responsiveness and extraction are different questions**, and the
index is built on the first.

### Step 9 — the reports

Both render through `exhibit_service` — four formats, caption, footer — with the
**Rule 1006 notice replaced**, exactly as the normalized statement swaps in its
disclaimer. This is a Rule 196.3(c) index, not a summary offered in place of
records.

**Production index** (ours), sorted by request set, request number, Bates:
request set · request no. · Bates range · document type · description · document
date · date produced · volume. Cumulative — each supplement re-serves the full
index, so the latest exhibit is always complete. Withheld documents never
appear; they belong in the withholding statement and the privilege log.

**Deficiency report** (theirs): per request, which accounts and which months are
present, and which are missing inside the requested period — from labels and
extracted metadata, not from a responsiveness score. `requests_without_documents`
is its first draft.

The response language from the received spec (§4.7) stands as written. Hold it
as a `_SYSTEM_*` constant the way `_SYSTEM_CAPTION` is, with the same note that
the per-firm override belongs in `matter_preferences` when that exists — do not
block on a table nobody has built.

## 9. Data model

`db/migrations/047_document_pipeline.sql`. Every new table enables
RLS, every view is `security_invoker` and revoked from `anon`, enforced by
`tests/test_migration_rls.py`. Two existing tables gain one column each
(`discovery_requests.direction`, `discovery_request_items.operative_text`).

## 10. Storage cost, by matter

This is **step 3 of the cost-tracking plan** (`docs/cost-tracking-design.md`),
scoped to what per-matter allocation actually needs.

**Supabase attributes nothing.** Billing is project-level; there is no
per-object cost API. `storage.objects` does hold `metadata->>'size'` per object,
but the `storage` schema is not exposed through PostgREST, and **egress is not
attributable at all** — a signed-URL download leaves no row to query. Storage at
rest can be allocated; transfer cannot.

**Do not derive it from paths.** Today:

```
matters/{matter_id}/pleadings/{id}.pdf     ← matter-scoped
matters/{matter_id}/discovery/{id}.pdf     ← matter-scoped
intake/{job_id}.pdf                        ← every statement ingest lands here
```

`enqueue_statement_ingest` calls `upload_intake`, so an entire production is
stored with **no matter in its path** even though `matter_id` is known at
enqueue (§16a: "moving the intake PDF onto the matter at commit" — not started).
A path-prefix total would report that the firm has almost no storage.

**Record bytes on the row that owns the file, at upload.** Four reasons:

1. The owning row knows the matter; the path may not.
2. One upload backs several statements. A storage object cannot divide itself;
   the owning row can name its source document.
3. It is auditable. A per-matter figure computed from our own rows can be shown
   and checked; a storage-API total cannot be tied to a matter at all.
4. **It survives the retention policy.** When a PDF is purged, the byte count
   and the hash remain, so last year's figure does not rewrite itself — the same
   discipline as pricing at read time while never discarding token counts.

`storage_snapshots` is monthly because storage is a **level, not an event**.
Price it at read time from `service_rates` (041): `provider='supabase'`,
`meter='gb_month'`, with the same "unpriced is NULL, never zero" rule.

**And reconcile.** Compare the sum of recorded bytes against the project's actual
storage total — the same two-source argument as the printed-totals check and the
portfolio summary. Divergence means something is writing to storage outside
`StorageService`, which is a thing to learn from a report rather than a bill.

### Closed matters are one line, and that is not a presentation preference

Per-matter storage makes sense while a matter is billing. Once it closes, **the
firm eats the cost** — it is overhead, not an allocable expense — and listing it
among per-matter figures implies somebody can be billed for it.

So the report rolls `closed` and `archived` into a single **Closed matters**
line, expandable. Three constraints on that expansion, all driven by scale:

| | |
| --- | --- |
| **Sorted by accumulated cost, descending** | The question being asked is *which closed matters are costing us*, and at this size no other order answers it |
| **Searchable, and paged** | KoonsFuller's historic billing database holds **over 14,000 clients**, many with several matters. Rendering the closed set is not an option — the same discipline as the export cap: never render what you cannot read |
| **Rolled up by status AT THE SNAPSHOT** | `storage_snapshots.matter_status`. If the rollup read current status, closing a matter would move a year of history out of the per-matter figures and last month's report would stop reproducing |

A matter can be closed and still carry a final invoice, so the rollup key is
`status in ('closed', 'archived')` rather than any notion of "billing activity"
the schema does not model.

### Retention is a decision, never a sweep

**The as-produced originals and the working copies get the same retention. They
must not diverge** — a working copy outliving the original it is derived from is
the exact state this design exists to prevent.

Current practice keeps files indefinitely; the floor is the two-year
limitations period on malpractice claims, **which can be tolled**, so no date
computes to "safe to delete". That settles the shape of the retention item in
§16a: a purge somebody requests, reviews and approves per matter — never a
scheduled job that deletes on an age rule.

## 11. Protected health information

A production in a family-law matter routinely carries medical records, mental
health records, and drug or alcohol testing. The taxonomy already separates
them, and `involves_child` is already asked. What this section adds is the rule
about **what may leave the firm**, because that is the part no classifier
decides.

### What counts

`documents.contains_phi`, set two ways and recording which (`phi_basis`):

- **By label** — `medical_record`, `mental_health_record`,
  `drug_or_alcohol_test_result`.
- **By extraction** — a diagnosis code, medication change, treating provider or
  date of service pulled out of a document makes that document PHI **whatever
  it was labelled**. An email attaching a diagnosis is the ordinary case.

The two are reviewed differently: a label is a guess a person can correct, an
extracted diagnosis is the thing itself.

**Drug and alcohol results are treated as PHI here**, deliberately
over-inclusively. Under the governing statutes they qualify only where the
purpose of the test was diagnosis or treatment rather than compliance with a
court order — a distinction the document itself rarely states. The cost of the
over-inclusive call is a warning nobody needed; the cost of the other one is a
disclosure. **This must not be "simplified" later.**

### Whose PHI it is, which drives everything else

`contains_phi` says a document carries PHI. It does not say **whose**, and that
is the fact the warnings and the exclusions turn on. Four buckets, and the
fourth is the one that would otherwise be discovered late:

| | Flags | May the client see it? |
| --- | --- | --- |
| The client's own | `contains_phi`, `phi_subject_client` | **Yes** |
| The client's minor child's | `contains_phi`, `phi_subject_minor_child` | **Only if `matters.may_receive_childrens_phi`** |
| Both (a record covering mother and child) | both subject flags | Governed by the stricter — the child's |
| **The opposing party's, or a third party's** | `contains_phi` only | **No.** This is the common case in a production and the one a client most wants to see |

The flags are not exclusive, and `contains_phi` is the superset: true whenever
any subject flag is.

**Whose it is, is a determination and not an extraction.** Matching a patient
name against our client or a child uses what already exists: `matter_children`
(names and dates of birth) and the **first-and-last-name rule** from
`intake_service._match_confidence`. Surname alone is categorically not enough
here — the parties share a surname with each other *and* with the children, so a
surname match would route the opposing party's psychiatric records into the
client's portal.

**Unmatched defaults to the strictest bucket:** `contains_phi` alone,
firm-only. Unnamed beats wrongly named, as everywhere else in this codebase.

### `matters.may_receive_childrens_phi`

Defaults to **false**, flipped by hand, and true in something like 99% of
matters. The rare case it exists for: a client whose rights are restricted far
enough that they have no right to receive medical information about their own
children. There is no corresponding case for a client's *own* PHI — nobody is
barred from their own records.

#### It is on matter EDIT, deliberately not on matter setup

**The friction is the feature, and this reverses an earlier draft of this
section.** Putting it on the create form was the wrong instinct for the reason
Tom named: a field on a setup screen gets filled in while somebody is opening a
matter, and **the single case this flag exists for is the case where a reflexive
click discloses a child's medical records to a parent a court has barred from
receiving them.** Something has gone badly wrong in that family's story, and
this must not be an easy gateway to making it worse.

Defaulting to **true** is slightly worse again: it removes the decision
entirely.

So: **default false, absent from matter setup, present on matter edit.** A
deliberate second act, by somebody who has read the file.

It is not really a setting at all — it is a **finding about a court order**, and
findings in this codebase default to the cautious answer and are recorded by a
person: `ownership` defaults to `unknown`, `property_character` is left null
until an attorney characterises it, and a statement is not marked an account's
first until somebody says so. This is the same shape.

**What it costs, stated plainly:** the 99% case is restricted until somebody
acts, so a client can be denied records they are entitled to, by inaction. Two
things make that a work item rather than a silent denial, and both are now
load-bearing rather than nice to have:

- **The withholding announces itself to staff.** When the flag is false and a
  child's record exists, the staff view says *"children's PHI withheld — not
  authorised on this matter"*. A paralegal who cannot see why a document is
  missing assumes a bug and goes hunting one; the client simply never sees it.
- **Count it where somebody looks.** The same amber-count treatment the Accounts
  tab gives an account with no recorded ownership: a pending decision, counted,
  not an error. That is what turns inaction into a queue.

**Flipping it is audited.** `AuditLogger.log()` on every change, with who and
when — §13's list of actions requiring an `audit_log` entry gains a row. The
entry is immutable like every other, and it is the record that answers *who
authorised this client to receive their children's medical records, and when*.

#### What the manuals must say, and why that is a requirement

**This will be reported as a bug**, and moving the flag to the matter-create
page will be requested as a feature. Both deserve an answer that is written
down rather than re-derived under pressure, so it goes in the **user manual**
(where the flag is, how to set it, what the client sees while it is false) and
the **operator manual** (why it is not on the create page, and why it is not
defaulted true).

The explanation, in the form it should take:

> The flag is not on the matter-creation form on purpose. It records a finding
> about a court order limiting a parent's access to their own child's medical
> information — not a preference. On a setup screen it would be completed
> reflexively along with everything else, and the one situation it exists for is
> the one where a reflexive click sends a child's records to a parent a court has
> barred from receiving them. Nearly every matter needs it set; the extra step
> is what makes that a decision somebody made rather than a default they tabbed
> past.

**Not written yet, and that is correct**: the manuals describe shipped
behaviour, and documenting a flag that does not exist would make them wrong in
exactly the way the lead-routing toggle already demonstrates. The obligation
ships with the feature.

### The disclosure rule

**Any report displaying PHI-derived content carries a restriction: it may not be
shared outside the firm.** The exceptions, and there are only four:

1. **To the client, their own PHI.**
2. **To the client, their minor child's PHI** — and only where
   `may_receive_childrens_phi` is set on the matter.
3. A **court order** authorises the release.
4. A **Texas Medical Privacy Act authorisation** covering **that particular
   data** to **that particular recipient**.

Exceptions 1 and 2 are **releases to the client and nobody else.** That is the
distinction the warning has to carry, because it is the one a reader will get
wrong: *the client's own PHI may go to the client, and still may not go
anywhere else without exception 3 or 4.*

Exception 4 is the one a UI must not flatten into a checkbox: an authorisation
is specific in both directions, so a stored "client consented" flag would be
wrong the first time a second recipient asked.

**The warning is written for somebody holding a screenshot.** Anyone who can see
the data can photograph it, so the restriction has to travel on the page rather
than live in a permission model — and it has to say the non-obvious half out
loud, not merely "confidential":

> Contains protected health information. **Do not share outside the firm.** The
> client's own PHI, and their minor child's PHI where authorised on this matter,
> may be released **to the client**. Any other disclosure requires a court order
> or a signed Texas medical authorisation naming this data and this recipient.

### Where this lands in the code

| | |
| --- | --- |
| **The warning is computed from the data on the page, not the type of report** | A transaction exhibit normally carries none; the same exhibit built from lines sourced from a PHI document does. PHI propagates document → extracted field → report, and the report asks |
| **It sits at the HEAD of the document, not the foot** | The Rule 1006 notice qualifies content a reader has already read, so the foot is right. A restriction governs what somebody **does with the page**, so it has to be read first — a disclosure rule nobody sees before sharing is decoration |
| **It is additive** | `Exhibit.notice` is replaced by the normalized statement's disclaimer; this is not a replacement. A PHI restriction rides *with* whatever notice the document already carries |
| **PHI-derived data does not export as CSV** | CSV deliberately carries no caption and no notice — "that file is data". A restriction cannot be dropped the way the Rule 1006 notice is, so the one format that cannot carry a warning is the one format this data must not leave in. A deliberate trade, and the narrower one |
| **The portal gate is the subject flags, not a blanket rule** | `phi_subject_client` always; `phi_subject_minor_child` only with `may_receive_childrens_phi`; **everything else never** — and the opposing party's records are the bulk of a production. This is live the moment the portal shows documents rather than only transactions |
| **Logs never carry it** | §6 already forbids PII; nothing new, but the constraint handler's lesson applies — an unhandled error that prints the offending row would print a diagnosis |

**The warning is a sentence somebody has to be able to act on**, naming the four
exceptions, not "contains sensitive data". Hold it as a `_SYSTEM_*` constant
beside the Rule 1006 notice and the pro-forma disclaimer.

## 12. Model dispatch

- **One dispatcher.** Every call goes through `llm_service` with a **task
  profile** in `app/config/llm_profiles.json` — `classify_discovery_document`,
  `map_request_to_labels`, and so on. No vendor or model name appears anywhere
  else. A separate classifier interface would bypass failover, per-task
  timeouts, the usage ledger (039), who answered (042) and why it stopped (046),
  and would make per-document classification spend invisible on the page built
  to see it.
- **Gemini stays last.** This corpus includes `mental_health_record`,
  `drug_or_alcohol_test_result`, `law_enforcement_or_cps_record` and an
  `involves_child` flag — the material Gemini's filters decline. Last in the
  chain, as a backup that processes what it will.
- **Measure the refusals.** 046 classes a stop reason as
  `truncated` / `refused` / `complete`, so refusal rate per vendor is now a
  *measurable* bake-off criterion, and the documents nobody classified can be
  listed rather than falling silently into `other_unknown`.
- **Hosted models are cleared, and the condition is a procurement fact rather
  than a model property.** Tom, 20 October 2026: a hosted model satisfies HIPAA
  and the Texas Medical Privacy Act **so long as transmission is over a secure
  connection to a service that will not share the data outside our domain** —
  which in practice means it does not train on it. So the question is not
  "which vendor is safe" but "which vendor's terms say this", and the answer
  changes when a vendor changes its terms, not when it changes its weights.
  Record the terms relied on, per vendor, with the date they were read: that is
  the artifact an opposing motion or a client's own counsel would ask for, and
  nothing in a chain configuration carries it.
- **A locally hosted option stays in the bake-off anyway**, not as a
  confidentiality fallback but as a cost one: boundary judgment is a per-page
  question over whole productions, which is the one task here whose volume can
  justify local inference.

## 13. Evaluation

The received spec's plan (§7) stands: a few hundred hand-labeled documents
sampled across clients and look-back windows, over-sampling older periods,
including deliberate `other_unknown` and look-alike pairs. Metrics: per-class
confusion, **calibration** (does 90% hold up nine times in ten), review rate at
candidate thresholds, latency, cost per document — the last two now measurable
rather than estimated.

Add **refusal rate per vendor** as a fifth metric, and keep the existing single
large prompt as the baseline. If the baseline wins, say so and keep it.

### Splitting is evaluated before classification, and against a free baseline

Two separate tasks share one labeled set, and the boundary task comes first
because the classifier's input does not exist until the pile is cut.

- **Hand-marked boundaries are the prerequisite for everything in this
  section.** Nothing produces them. `scripts/split_lab.py worksheet` emits one
  row per page with a text snippet so a pile can be marked in one sitting; the
  `starts_doc` column is deliberately **blank** rather than pre-filled with the
  detector's guess, because truth anchored on the thing being measured scores
  itself.
- **The model is scored against the printed signals, not against nothing.**
  Pagination resets, masthead changes, geometry changes, blank separator sheets
  and text-layer changes are free, deterministic and already half the method
  (§5 step 2). `split_lab.py score` reports each one's precision and recall and
  then the figure that actually sets the budget: **the share of boundaries no
  printed signal finds.** Those are the only page breaks worth paying a model
  for, on every production, forever.
- **Score over pages 2..N.** Page 1 always begins a document, so counting it
  gives every candidate a free true positive and makes a signal that fires
  nowhere look useful.
- **Precision and recall are not equally priced here either.** A missed
  boundary welds two documents into one, which mislabels both and carries the
  wrong request mappings into §8; a false boundary splits one document in two,
  which a reviewer sees immediately as a two-page statement. So err toward
  splitting — the same direction as the text-layer dial, for the same reason.
- **Bates continuity is not a boundary signal**, and it is the one people reach
  for first. A production is numbered consecutively straight through, so the
  numbers are continuous *across* document boundaries and a jump means a
  missing page. `split_lab.py` scores it anyway, to show that it finds nothing.

### First measurement, 20 October 2026

84 pages, 22 documents, marked by hand. One of the firm's own pile files, and
described as the easiest of them.

| Signal | Precision | Recall |
| --- | --- | --- |
| Restarted pagination (`page 1`, `page 1 of N`) | **100%** | **76%** |
| Page geometry change | 67% | 10% |
| Blank separator sheet, text-layer change, Bates jump | — | fired nowhere |

**So roughly a quarter of boundaries need judging, not all of them.** On this
file: 5 of 21.

Two findings worth more than the percentages:

- **A near-constant signal reads as a perfect one.** The masthead-change rule
  was "the first lines are not byte-identical", which fired on **82 of 83**
  candidate pages — the first lines of a dense page carry content that changes
  every page. Recall was 100% and precision 26%, and because the headline
  unioned every signal it reported *no boundaries need a model* when the
  honest answer was five. The rule is now token overlap below a threshold, the
  threshold is swept against the labeled set on every run, and any signal
  firing on more than half of pages is excluded from the baseline and named as
  excluded. **Same failure as a warning that fires on every correct import**:
  it costs nothing to generate and it hides the real number.
- **That pile's text layer was Acrobat's, applied by hand weeks earlier.** Which
  makes the measurement one of the *post-OCR* state — the right thing to
  measure, in the right order (§5 step 2), and not a wasted run. It leaves one
  question open that no amount of marking answers: **whether Textract's layer
  supports these signals as well as Acrobat's does.** Measure the same pile
  through the chosen engine and compare, because every number above is a
  property of the OCR as much as of the pile.

### Second measurement, same pile: two signals replaced

**The masthead signal is dead on this corpus, at every threshold.** Swept from
0.15 to 0.80 token overlap it never exceeds **41% precision**, and the missed
boundaries say why: page 23 is a Bank of America statement dated 1 July 2020
and page 25 is a Bank of America statement dated 25 June 2020 -- same bank,
same customer, same address block. The masthead is **constant across the
boundary and variable within the document**, so it fails in both directions,
which also kills the repeated-masthead veto proposed alongside it. A production
is usually one institution for one person; that is the normal case, not an
unlucky one.

**What separates those two pages is the date, and that generalises.** A
statement prints one date throughout; the next statement prints another.
Documents in a scanned stack are not in chronological order, so the header date
is constant within a document and changes at a boundary -- the Bates
monotonicity argument with the direction dropped, because a pile can be filed
either way round. `date_change` reads only the first 300 characters: a
transaction date in the body changes every line and is a different claim.

### The text-layer check as specified would have passed unusable pages

This is the one finding that is not about splitting at all.

§5 step 3 gates on a **maximum garbage ratio**, and the obvious
implementation -- the share of characters that are not ASCII or are replacement
characters -- **cannot see OCR noise, because OCR noise is ordinary ASCII**.
Measured on three real pages of that pile:

| Page text (first 70 characters) | Non-ASCII ratio | Word-like ratio |
| --- | --- | --- |
| `Bank of America, N.A. July 01, 2020 MUHAMMAD SHAHNAWAZ 2401 BRENNAN` | 0.00 | 0.92 |
| `Bank of America THEIVI Visit hankohanerk.:a,Lorri/ATI'41 02/13119 1` | 0.00 | 0.67 |
| `Tr:tee._ cqz, eivs/tal- 115s 009220.7/6 4,7,4-vt 0 444 soo Bank of` | 0.00 | 0.55 |

The file reported **zero** garbled pages. The last two are a photographed ATM
receipt and a handwritten deposit slip, and under the specified rule both would
have gone to the classifier as clean text -- which is the exact failure the
check exists to prevent, and the classifier would have labelled them
confidently.

**Measure the share of tokens that are language** -- a word, a number, a date,
or an amount -- not the share of characters that are exotic. Keep the non-ASCII
ratio as well: a broken encoding and a bad scan are different faults, and only
one of them is repaired by OCR'ing the page again. The threshold is not
guessable from here either, so `split_lab.py features` lists the least
word-like pages with their scores and the number is set by reading them.

### Third measurement: the header is the signal, and OCR quality is a variable

Two piles of the same production, one carrying Acrobat's text layer and one
carrying a worse one, turned out to be the useful pair.

**The strongest free signal is the document's own printed identity**, which is
`_statement_pages()`'s method arriving by a second route. On a real Bank of
America production the page header reads:

```
MUHAMMAD SHAHNAWAZ I Account # 4880 5304 6662 I August 22, 2018 to Sep...
```

The account number and the statement period, on nearly every page. Two
signals come out of it, and they are not redundant: a combined production of
checking and savings for one month changes the **account** and not the period,
while twelve months of one account changes the **period** and not the account.

**Pagination and header identity fail for unrelated reasons, so agreement
between them is the strongest free evidence there is.** On that pile they
concur on 13 of 17 boundaries, and both independently flag one page pagination
missed entirely. `split_lab.py score` reports that intersection separately for
exactly this reason.

### Three failures worth keeping, because each would have shipped

**A value-change signal places the boundary late, by the length of the silent
run.** A statement's cover sheet carries no account number and no date, so
"the account changed" is noticed on the page *after* the boundary — every
boundary one page late, which welds each cover sheet onto the preceding
statement and strips it from its own. Measured: pagination said 3, 7, 13, 19,
27 and the date said 4, 8, 14, 20, 28. The fix is to back the boundary up over
the pages that said nothing, because the signal can only establish that the
change happened *somewhere* in that run, and a cover page at the front of the
next document is the dominant reason for the silence. Wrong by a page in a
direction a reviewer sees, rather than wrong in a direction nobody checks.

**"This page names no account" was never true.** The first rule took every
digit group in the header, and a cover sheet prints a PO box, a ZIP and a mail
code — so the abstain guard never fired and the signal reported a boundary both
entering and leaving every cover page. Match the group the document itself
**labels** as an account, and require at least six digits: "your account ...
2018" otherwise puts a year in the set, and a ZIP is five. The label pattern
has to accept `Account it` and `Account 11` alongside `Account #`, because this
is OCR output and one glyph comes back three ways on one document.

**On a layerless pile the signals produced 39 confident boundaries from
nothing.** The has-text and word-like flags flap between blank pages and pages
holding a stray text object, and the tool printed that list directly beneath
its own warning that splitting was impossible. A list that cannot be right must
not be printed beside the warning saying so — that is how a warning gets
ignored, and it is the same mistake as a flag that contradicts the row it sits
on. The boundary list is now withheld, and a signal-based split refuses
outright. **The refusal, not the warning, is the fix**: the same argument as
`refuse_oversized_upload`.

### OCR quality is an input to every number here

The second copy's text layer renders the same glyph as `#`, `it` and `11` on
one document, and `paperless` as `paperies`. It is still entirely usable for
these signals, which is the encouraging finding — the header's account number
and period survive mediocre OCR, because digits in a header are the easiest
thing on the page.

But it means **every measurement in this section is a property of the OCR as
much as of the pile**, and the comparison is cheap to run: the same pile, the
same hand-marked truth, one engine swapped. Do that before choosing an engine,
because the decision is not which OCR reads best — it is which OCR preserves
the three things the pipeline actually reads: the header identity, the
pagination footer, and the Bates stamp.

Worth noting for `bates_service`: that production's stamps are **bare
six-digit numbers with no alphabetic prefix**, and they do appear in the text
layer. A stamp pattern requiring a prefix finds nothing on it and the statement
gets `BATES_UNSTAMPED` wrongly.

### Fourth measurement: two piles, inverted profiles, and a routing decision

Two real piles from one production, both hand-marked.

| | Pile A | Pile B |
| --- | --- | --- |
| Pages / documents | 84 / 22 | **396 / 169** |
| Usable text layer | 100% (Acrobat) | **0%** |
| Restarted pagination | **100% precision, 76% recall** | fires nowhere |
| Header account + period | agrees on 13 of 17 | fires nowhere |
| Page geometry change | 2 fires, marginal | **78% precision** |
| Short page | untestable | **79% precision** |
| Word-like drop | 2 fires | 70% precision |
| Best combination | 16 of 21 clean | 45 of 168, at 19 false splits |

**THERE IS NO ONE SIGNAL SET, AND THAT IS THE FINDING.** Pile A is an orderly
run of bank statements, so the signal is what the bank prints: pagination, the
account number, the period. Pile B is a scanned stack of scraps of different
physical sizes, so the signal is the paper: how big the page is and how little
is on it. Each pile's best signal finds nothing at all on the other.

So the free-signal coverage is not only a cost estimate (§13). **It is a routing
decision, taken per upload**, and `split_lab.py` now ends with that verdict
rather than a table.

**The triage inputs are free and need no truth and no model**: the share of
pages with a usable text layer, the number of distinct page geometries, and the
share of near-blank pages. Pile B reports 0% text and 23 geometry changes from
`features` alone, before anything is marked or classified -- which is to say a
pile announces what it is, and the announcement is readable in the cheapest
pass there is.

**A below-90% signal is usually still worth taking, because the two errors are
not priced alike.** A false split is a one-page fragment a reviewer sees at a
glance. A missed boundary welds two documents together, mislabels both, and
carries the wrong label→request mappings into the production index, where
nothing reveals it unless somebody reads the document. The scorer therefore
reports a second tier with the trade stated -- 45 found against 19 false splits
-- instead of refusing to speak because nothing reached 90%. **Refusing to
report at the point where the decision is hardest was the defect**, and it is
the same shape as a headline that unioned a near-constant signal.

**`short_page` was marked SPECULATIVE and has now earned its place -- on the
opposite pile from the one that prompted it.** It could not be measured on
pile A at all (one fire) and is the most precise signal on pile B. That is what
validating on a second pile is for, and it is also the warning: a signal set
tuned on either pile alone would have been wrong about the other.

### OCR existence is everything; OCR quality is nearly irrelevant here

Pile A exists in three states and has one hand-marked truth, which makes it a
controlled experiment.

| State of the text layer | Pagination | Free baseline |
| --- | --- | --- |
| Acrobat, applied by hand | 100% precision, 76% recall | 16 of 21 |
| A visibly worse OCR (`Account it` / `Account 11` for `#`, `paperies`) | 100% precision, 76% recall | **16 of 21 -- identical** |
| None | fires nowhere | 0 |

**Mediocre OCR cost nothing at all.** Digits in a header and a page number in a
footer are the easiest marks on a page, and both survived an engine that could
not spell "paperless". The gate in §5 step 3 is therefore **binary** -- is
there a text layer -- and not a quality threshold, for splitting purposes.

**Which changes what the engine decision is about.** Not accuracy: choose on
cost, throughput, and whether the terms clear §12. The place OCR quality does
matter is downstream, where an extractor reads an amount off the page, and that
is a different measurement against different ground truth.

**It also raises the cost of the step-3 gate**, because OCR is now the thing
standing between an unreadable pile and any progress at all, and it runs over
the whole received file before there are any documents to scope it to
(§5 step 2).

One inversion worth keeping beside the table above: `short_page` scored **0
true positives against 17 false ones** on this pile and **79% precision** on
pile B. A signal set tuned on either pile alone is wrong about the other.

### The frontier, and a recommendation retired

With OCR applied to both piles and one hand-marked truth each, the signal set
worth running is chosen per pile by marginal contribution -- not by each
signal's precision in isolation, which admits one that adds a false split for
every boundary it finds and drops one that is imprecise alone but reaches
boundaries nothing else does. `split_lab.py --fp-cost` prices a false split
against a missed boundary, and sweeping it maps the frontier.

**Pile A: stable at 86% recall and 95% precision across every cost ratio from
0.25 to 10.** 18 of 21 boundaries, one false split, and the chosen set barely
changes. An answer that does not depend on the policy is the strongest kind
available -- three boundaries are left for a model.

**Pile B: a cliff, and no good operating point anywhere.**

| False split costs | Recall | False splits | Precision |
| --- | --- | --- | --- |
| 0.50 | 89% | **163** | 48% |
| 1.00 | **49%** | 26 | 76% |
| 2.00 | 36% | 14 | 81% |

Either find 89% of boundaries and review some 330 fragments for 169 documents,
or find half and weld 86 documents together. **The frontier is bad everywhere**,
and that is a measurement rather than an impression.

**So the recommendation to refuse Pile B is retired, and the reasoning that
produced it was wrong in a specific way.** It assumed the model pass would be a
VISION question over unreadable images, which is expensive per page and poor.
With a text layer present it is a text question -- the tail of one page and the
head of the next -- which a fast model answers for cents across 395 boundaries.
The binding constraint on Pile B is **accuracy, not cost**, which makes it the
right test bed for the bake-off rather than a candidate for refusal: it is the
case where the model's accuracy actually decides the outcome.

Refusal remains correct for the two cases it was built for: a pile with **no
text layer** (nothing to ask a text model about) and one past the size limits.

**The combination rule follows from the two piles disagreeing.** On Pile A the
free signals are 95% precise, so a model must not be allowed to overrule them
-- it adjudicates the three they missed. On Pile B they are 48% to 81% precise,
so the model carries most of the weight. Which means the pipeline does not pick
"signals or model"; it computes the free signals, reads its own precision off
them, and decides how much to ask. The triage verdict is that decision.

### Pile B is a candidate for REFUSAL, not for a bigger model

**SUPERSEDED by the frontier above, and kept because the reasoning is still
right for a layerless pile.** Pile B was measured at 0% text layer when this
was written; OCR'd, it is partly splittable and belongs in the model pass.

169 documents across 396 pages is 2.3 pages per document. No text layer, and
per Tom scans that will never yield one. A model asked 395 page-continuation
questions about unreadable images is spending money to guess, and a wrong guess
welds two documents.

**The answer to an unworkable pile is a refusal with a stated reason, not a
warning** -- `refuse_oversized_upload` again, and the 700-page production that
was warned about and imported anyway. What a refusal buys here is a person
deciding how to split 396 pages of scraps, which is work either way; what it
prevents is that work being done badly and invisibly, with a production index
built on top of it.

### Interrupted documents stay two documents

Pile B contains documents interrupted mid-way by unrelated pages and resumed
later. Tom marked each run separately -- document start, interrupting page,
continuation start -- and that is the rule rather than a concession:

| | |
| - | - |
| **The index cites a Bates RANGE.** | A document assembled from non-contiguous pages needs a list, and a row reading `002983-002985, 002987-002990` invites the question *"what is on 002986?"* The answer is "a different document", and it has to be asked rather than papered over. |
| **Cutting is mechanical; stitching is an assertion.** | That two page ranges are one document is attorney judgment, so it is a recorded decision with a name on it -- never an inference by a splitter. The same rule as `ownership` and `property_character`. |
| **The pages in between were produced.** | They must still appear in the index. Merging across them tempts an implementation to drop them. |

`documents.continues_document_id` relates the fragments without merging them,
written only by a person. See 047.

**REVISED by the bench.** Where a *person* groups the pages, the assertion this
rule demanded has been made and recorded, so one document may hold several page
ranges after all — `document_source_pages` in
[`SPEC_splitting_bench.md`](SPEC_splitting_bench.md) §2.
`continues_document_id` remains the automatic path's fallback, where nobody
asserted anything.

### An unreadable page needs a terminal state

A scan that will never produce a text layer must be recorded as such
(`text_layer = 'unreadable'`), or it is indistinguishable from a page not yet
processed: re-OCR'd on every run, re-reported as a gap, re-queued forever. A
page recorded unreadable is a page somebody reads by eye, which is the correct
outcome and not a failure.

**It still appears in the production index.** It was produced, it carries a
Bates range, and omitting it from a cumulative index served under Rule
196.3(c) is the one thing that index must not do. It appears with its range and
a description saying it could not be read.

## 5a. The splitting bench — a UI, not a better model

**The design is [`SPEC_splitting_bench.md`](SPEC_splitting_bench.md).** This
section is the argument for it and the measurements behind it.

**Tom, 20 October 2026, and the measurements agree with him.** The automatic
split of a bad pile does not save human time, because **reviewing a wrong split
costs more than making the right one**: the reviewer has to reconstruct what
the machine did before correcting it, discover welds hidden inside fragments by
reading pages they would otherwise skim, and recognise that fragment 47 belongs
with fragment 52. At pile B's best operating point that is 330 fragments, 18
hidden welds and a merge problem, against 169 selections made from scratch.

Automation pays when review collapses to a **spot check** — pile A's 22
documents with one false split is a two-minute glance. It does not pay at all
when review is page by page, and the frontier says which case you are in.

### What actually costs the time today, which was not the judgment

The current process is Acrobat: scroll, find a page 1, bookmark it with a name
meaningful in the file system (`BOA x0123 2026.06`), repeat, then split on
top-level bookmarks. **The naming is what eats the time, and it is a cost
imposed by the file system being the data model.** In Cyclone a document is a
row carrying `doc_type`, period, account, Bates range and `description`, so the
file can be `20261020-1021-001.pdf` and carry nothing — and the classifier
proposes the description afterward from the document's own content.

**Which inverts the division of labour.** The instinct was to automate the
judgment and leave naming to the person. It is the other way round: **the human
groups the pages, which on a pile like this only a human can do; the machine
names them, which is the part that was slow.**

Acrobat's bookmark method also has **no mechanism at all** for a document whose
pages are discontinuous, which pile B contains. The bench is not only faster,
it is the only thing that can express that case.

### The bench

Twenty large thumbnails on a large screen — big enough to read a letterhead and
a date, which is the whole decision. Click or touch pages that belong together,
including non-adjacent ones; clicked pages highlight, clicking again releases
them; a large DONE bundles the selection into a PDF with a sequential name.
Bundled pages disappear, so the remaining grid *is* the progress bar, and the
operator cycles until nothing is left. Tom's estimate for 396 pages is under ten
minutes.

Decisions that are not obvious:

| | |
| - | - |
| **Never pre-select the machine's guess.** | The same rule as the truth worksheet's blank column, and it matters more here: a pile reaches the bench precisely *because* the signals are weak, so a pre-selection reintroduces the review-a-wrong-split cost this screen exists to avoid. |
| **Do show what the page PRINTS.** | A small badge reading `Page 1 of 4`, a date, an account number — facts read off the page, which help the operator decide. A fact helps; a conclusion has to be undone. The same distinction as `hdr_date` on the worksheet. |
| **UNDO is load-bearing, not a nicety.** | Bundled pages vanish from the grid, so a mis-click is otherwise unrecoverable without finding the output file and starting over. The stack has to survive the whole session. |
| **Bundle from the as-produced source by page number.** | Never from a re-assembled upload. That is what keeps `source_page` lineage true (§4). |
| **No name, ever, not even optional.** | An optional field gets filled in, and then it is load-bearing for whoever filled it. |
| **Thumbnails are rendered once and cached.** | 396 pages at a readable size is real work; it is also why the grid must not re-render on every selection. |

### The trigger is pagination self-consistency, NOT text quality

The routing test has to run on an upload nobody has looked at, so it cannot use
precision or recall — those need hand-marked truth. The question it *can* ask
is whether the pile describes its own structure:

**`pagination_coverage` = the share of pages inside a complete, self-consistent
`Page i of N` run** — a page 1 of 4 followed by pages that each agree they are
2, 3 and 4 of 4. A bank statement does this; a stack of receipts cannot.

| Pile | Usable text layer | `pagination_coverage` | Measured outcome |
| --- | --- | --- | --- |
| A, OCR'd | 100% | **74%** | 86% recall at 95% precision |
| B, OCR'd | **96%** | **8%** | no good operating point at any cost ratio |
| Either, layerless | 0% | 0% | OCR first; nothing is decidable |

**A text-quality trigger would misroute pile B**, which has a 96% text layer and
is unsplittable. 74% against 8% needs no tuned threshold — anything from 20% to
60% separates them — and it is deterministic, free, and computed in the same
pass as the text-layer check.

### An oversized pile is ROUTED, not refused — Tom, 21 October 2026

`refuse_oversized_upload` turns 50 pages or 8 statements into a **fork rather
than a wall**: past either limit the upload goes to the bench, and an operator
who does not want to split it there can cancel.

That is strictly better than the refusal it replaces, which was written after a
700-page pile was warned about and imported anyway and was a total loss. The
refusal's reasoning was right about the *automatic* path and had nothing to
offer instead — "no" with no door. Cancelling reaches the same end state as a
refusal (nothing split, nothing imported) by way of a path somebody can take.

Four consequences, and the first two are what make cancelling honest:

| | |
| - | - |
| **The bench states the size of the job before it starts.** | 700 pages is a long session, and an offer whose cost is hidden is a trap rather than a choice. Page count, how many documents the free signals expect, and an estimate in minutes from the measured interaction rate. |
| **Resume stops being a nicety.** | Nobody splits 700 pages in one sitting. The session already persists (`SPEC_splitting_bench.md` §4); with this decision it is load-bearing, and the grid must come back exactly as it was left. |
| **A cancelled pile is kept, unsplit.** | The as-produced record is held whether or not anybody has split it — it was served on us, which is the whole argument of §4. It sits in a "received, not yet split" queue. Deleting it would discard evidence because a person declined a task. |
| **The limits stay module constants, for a NEW reason.** | They no longer need to resist being raised under deadline pressure, because exceeding them is not a refusal. They need to resist being raised *casually*: a 60-page pile sent down the automatic path produces the mess the limits were written about. |

Routing, in full:

1. **No usable text layer** → OCR, then re-test. Nothing is decidable before it.
2. **Over 50 pages or 8 statements** → the bench, with the job's size stated.
3. **`pagination_coverage` low** → the bench. The pile does not describe itself.
4. **Otherwise** → automatic split, then a spot check.

### This may remove the model boundary pass entirely

Worth stating before anybody builds it. Pile A leaves **three** boundaries for a
model, against a two-minute human verification of 22 documents — cents of spend
to save perhaps thirty seconds. Pile B goes to the bench. So the model would
earn its place only on a pile in the middle, at perhaps 40% coverage, **and no
such pile has been seen.** Defer it until one arrives, and keep the model where
it clearly pays: classification.

### A caution about these five

Five missed boundaries is not a validation set, and fitting signals to them
would be the same error as tuning a model on its test data. Each signal added
here has a rationale that holds without this file -- a pile is not in date
order; a photographed check is a different kind of document from a statement --
and `short_page` is marked SPECULATIVE in the source because it does not: the
last page of a long statement is also short. **Validate on the second pile
before trusting any of them**, and treat a signal that only works on the pile
it was invented for as not working.

**If a pile was ever split by hand, its truth already exists.** The parts' page
counts are the boundaries; `split_lab.py from-parts` reconstructs the truth
file from a folder of them, checking that the pages sum to the pile and that
each part's first page matches the pile page it claims — the sum alone can be
right while the order is wrong, which would misplace every boundary after the
first mistake.

## 14. Build order

1. **PDF authoring module** — split, stamp, write back. Unblocks steps 0, 2, 3
   and the paused Bates work.
2. **`047` plus the as-produced record** — `source_documents` with hash and
   bytes on receipt, `documents` with lineage and `statement_id`.
3. **Label → request mapping with review** (step 8) over the labels already
   available — statements — plus the rules pass for period and holder.
4. **The Rule 196.3(c) index** as an exhibit — **with the PHI restriction
   (§11) in `exhibit_service` before it ships**, not after. The index lists a
   document type and a description, so the first report is already capable of
   putting `mental_health_record` on a page that leaves the firm.
5. Classifier and registry (steps 4–5), once the confidentiality question and
   the bake-off are settled.
6. Folder watcher (step 1). Last, because everything above works on an upload.

Steps 2–4 ship a real deliverable and exercise the data model against real
statements before the classifier arrives.

## 15. Decisions, and what is still open

Settled by Tom, 19 October 2026:

| Question | Decision | Where |
| --- | --- | --- |
| Markdown conversion or an OCR text layer? | **Text layer in the PDF.** One artifact per document; the working copy has to be displayable, and the page number is a citation | §5 step 3 |
| Confidentiality for medical and mental-health records | **Same handling as everything else, plus a disclosure restriction on every report that displays it** | §11 |
| Does Cyclone apply Bates stamps? | **Yes — after a confirmation that says what was detected.** Never silently, because stamping a production that already carries a series defaces it | §5 step 2 |
| Retention for as-produced originals | **Identical to the working copies', and may never diverge.** No automatic deletion: the two-year malpractice period can be tolled, so no date computes to safe | §10 |
| Are file names ever clean enough to show? | **No.** Treat them as useless; the exhibit shows a description | §8 |
| 38 taxonomy options in one call, or two stages? | **Decide from the confusion matrix**, not in advance | §13 |

Settled 19 October, second pass:

| Question | Decision | Where |
| --- | --- | --- |
| Which OCR engine? | **Tesseract is ruled out — measured unusable on this firm's bank statements.** AWS Textract is the candidate: async past five pages, returns geometry we build the layer from, already inside the AWS trust boundary. Verify HIPAA eligibility and whether a BAA is needed | §5 step 3 |
| Does the PHI restriction need a narrower form? | **Yes, and the narrower half is the load-bearing one**: the client's own PHI — and their minor child's where authorised — may go **to the client and nowhere else**. Driven by `matters.may_receive_childrens_phi` (default false) and the document's subject flags | §11 |
| Where does `may_receive_childrens_phi` live? | **Matter EDIT, not matter setup.** Default false; defaulting true is worse. The friction is the feature — a create-form field gets completed reflexively, and the one case this guards is a child's records reaching a parent a court barred from them. Audited, and the manuals must carry the explanation | §11 |
| Column naming | `contains_phi` as the superset, with `phi_subject_client` / `phi_subject_minor_child` — a document *contains* PHI rather than *being* it | §11, 047 |

Settled 20 October:

| Question | Decision | Where |
| --- | --- | --- |
| Do hosted models clear the confidentiality review? | **Yes, conditionally** — secure transmission to a service that will not share the data outside our domain, i.e. does not train on it. A property of the vendor's **terms**, so record which terms were relied on and when they were read | §12 |
| What is measured first? | **Page boundaries, against the free deterministic signals** — and the hand-marked truth that both tasks need. The recurring cost is the boundaries no printed signal finds, not the page count | §13 |
| Does the split precede the text layer? | **Not for scanned input.** Both the printed-signal method and a model's page-continuation judgment need readable pages, so step 3 runs before step 2 there, over the whole received file | §5 step 2 |

Still open:

- **The taxonomy's shape.** Left open deliberately — and the open item is
  really the *process* for finalising it, which Tom has questions about, not
  only the answer. The confusion matrix decides the shape; what decides who
  reviews it, against which documents, and when it is called done is not
  settled.
- **Which model wins the bake-off.** Scoped by §13 to the boundaries the free
  signals miss, plus classification of the documents the split produces.
- **Whether AWS Textract is HIPAA-eligible under a BAA**, which is the same
  shape of question as the hosted-model one and has to be answered the same
  way: from the terms, recorded with a date.

### Starting thresholds — what the task actually is

This was written as a question without saying what was being asked, which is
why it read as a task with no handle. It is not "pick three numbers now."

There are three dials, and **the only decision needed in advance is which
direction each one errs**, because each sits between two costs that are not
symmetric. The numbers themselves come from the labeled set.

| Dial | What it decides | Which error is cheaper | So start |
| --- | --- | --- | --- |
| **Text-layer check** — min characters per page, max garbage ratio | Does this page have a usable text layer, or does it need OCR? | OCR'ing a page that did not need it: wasted pennies, no harm. The other way, garbage text reaches a classifier that **confidently mislabels** | **Aggressively toward OCR** |
| **Classifier confidence** | Below this, a document goes to human review instead of a processor | A document in the review queue costs a few seconds. A mislabeled one is routed to the wrong processor *and* inherits the wrong request mappings by label (§8) | **High**, and lower it as the confusion matrix earns trust |
| **Responsiveness probability** | Above this, a label→request mapping is proposed for approval | On **our** production a missed responsive document is the costly error. On **theirs** a false positive costs one look | **Low on both sides** |

The pattern is the house one: start where being wrong is visible and cheap, then
tune toward automation with measurements in hand. The review rate at each
candidate threshold is one of the metrics in §13 precisely so this is a reading
rather than a guess.

**Where they live:** module constants in the image to begin with, like
`_MAX_UPLOAD_PAGES` — one source, no fleet drift, and recorded with the
measurement that set them. Promote one to a setting only when its tuning cadence
actually demands it, and then into the `FLEET` bucket so a node that disagrees
shows up as a different `fleet_hash`.

## 16. Out of scope

- Privilege determinations. The pipeline flags; a person decides.
- Automatic service of anything.
- Separate-property tracing. Holdings capture (§7) is the groundwork.
- Client uploads into Cyclone (§16a: deliberately not supported).
