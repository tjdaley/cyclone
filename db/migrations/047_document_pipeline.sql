-- 047_document_pipeline.sql — the document pipeline data model
--
-- RUN BY HAND, after 046. Verified against real Postgres 16 by
-- tests/test_document_pipeline_sql.py, which also pins that it is re-runnable:
-- a hand-run migration gets run twice by somebody unsure whether it took.
--
-- Design: docs/specs/document-labeling/SPEC_document_pipeline.md, and
-- SPEC_splitting_bench.md for document_source_pages.
--
-- Satisfies tests/test_migration_rls.py: every table enables RLS, every view is
-- security_invoker and revoked from anon.
--
-- ── WHAT THE AS-RECEIVED DRAFT GOT WRONG, AND WHY IT MATTERS ─────────────────
--
-- It created `matter`, `request_set` and `request`. All three already exist as
-- `matters`, `discovery_requests` and `discovery_request_items`, on integer
-- keys. Adding uuid twins would give the database two matter tables and leave
-- nothing tying a produced document to the statement it IS -- which is the one
-- link the index depends on, because the index is served over a signature.
--
-- So this file creates only what is genuinely new, and alters two existing
-- tables to carry two facts they cannot express today.

begin;

-- An exclusion constraint on (document_id with =, int4range with &&) needs
-- gist support for the equality operator on an integer, which core gist does
-- not provide. No earlier migration installs it -- 001 installs pg_trgm and
-- pgcrypto -- so it is installed here, and it is the first thing to fail if
-- the extension is unavailable on the server.
create extension if not exists btree_gist;

-- ── Existing tables, two facts they are missing ──────────────────────────────
--
-- `discovery_requests` models only discovery served ON our client: it has
-- propounded_date, due_date and response_served_date, all of which read from
-- that side. Auditing the other side's production (step 9) needs the same
-- tables to hold a set we served, so the direction has to be recorded rather
-- than assumed. Defaulted to 'inbound' because every existing row is one.
alter table discovery_requests
    add column if not exists direction text not null default 'inbound';

alter table discovery_requests
    drop constraint if exists discovery_requests_direction_check;
alter table discovery_requests
    add constraint discovery_requests_direction_check
    check (direction in ('inbound', 'outbound'));

comment on column discovery_requests.direction is
    'inbound = served on our client; outbound = we served it. The compliance '
    'reports are two reports, bounded by different dates, exactly as '
    'matters.client_produces_since and opposing_produces_since already are.';

-- The text a model is scored against, never the verbatim request.
--
-- `source_text` is the request as served and must stay that way. `interpretations`
-- holds the attorney's readings and `objections` what was narrowed, but neither
-- is a single self-contained statement a model can be handed. This column is
-- that statement: definitions, the time period, and any narrowing by objection,
-- agreement or order folded in. Nullable -- a request nobody has restated yet
-- is scored by label and metadata only, which is the first pass anyway.
alter table discovery_request_items
    add column if not exists operative_text text;

comment on column discovery_request_items.operative_text is
    'Self-contained restatement fed to a model. The model sees only what it is '
    'given, so a narrowing that lives only in objections never reaches it.';

-- May this client receive their own minor children's PHI?
--
-- Default FALSE and flipped by hand, true in something like 99% of matters.
-- The rare case it exists for is a client whose rights are restricted far
-- enough that they have no right to medical information about their children.
-- There is no equivalent for the client's OWN records: nobody is barred from
-- those, which is why only one flag exists.
--
-- Default-deny is the right default and it has a cost: a flag flipped on almost
-- every matter gets flipped reflexively.
--
-- SO IT IS NOT ON THE MATTER-CREATE FORM. Only on edit. A field on a setup
-- screen is completed along with everything else, and the one situation this
-- exists for is the one where a reflexive click sends a child's medical records
-- to a parent a court has barred from receiving them. The friction is the
-- feature. Defaulting true is worse again: it removes the decision.
--
-- This is a FINDING about a court order, not a preference, which is why it
-- behaves like `ownership` and `property_character`: cautious default, recorded
-- by a person. Every change is written to `audit_log` via AuditLogger -- the
-- record of who authorised a client to receive their children's PHI.
--
-- The cost is that the 99% case is restricted by inaction, so withholding must
-- ANNOUNCE itself to staff and be counted where somebody looks (the amber-count
-- treatment an account with no recorded ownership gets). Silence reads as a bug
-- and sends somebody hunting one.
alter table matters
    add column if not exists may_receive_childrens_phi boolean not null default false;

comment on column matters.may_receive_childrens_phi is
    'Whether our client may receive PHI concerning their minor children. '
    'Drives the portal gate and the wording of the restriction on a report.';

-- ── A production: one service of documents, by either side ───────────────────

create table if not exists document_productions (
    id                  serial primary key,
    matter_id           integer not null references matters (id),
    volume_label        text not null,                 -- 'PROD001'
    -- Ours or theirs. The same tables index our production and audit theirs;
    -- without this the deficiency report has no side to run against.
    producing_party     text not null
                          check (producing_party in ('client', 'opposing', 'third_party')),
    produced_on         date,
    received_on         date,
    source_description  text,                          -- 'OC email 14 Oct', 'subpoena return, Chase'
    notes               text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz,
    unique (matter_id, volume_label)
);

alter table document_productions enable row level security;

-- ── The AS-PRODUCED record. Immutable. ───────────────────────────────────────
--
-- The bytes exactly as they reached us, before splitting, OCR, or conversion
-- from TIFF. Nothing in the pipeline writes to this table after insert and
-- nothing reads it to work from: it exists to answer "that is not what we
-- produced", and to hold the metadata the working copy destroys. A photograph's
-- EXIF capture time and GPS are routinely the evidence in a family-law matter,
-- and step 0 (convert to PDF) discards them irreversibly.
--
-- `content_sha256` is taken on receipt and is what makes every working copy's
-- lineage provable rather than asserted. It is also the dedup key: a supplement
-- re-serves documents, and the cumulative index must not list one twice.
create table if not exists source_documents (
    id                  serial primary key,
    matter_id           integer not null references matters (id),
    production_id       integer references document_productions (id),
    -- as-produced/{matter}/{content_sha256}.{ext} -- the HASH, not the id, so
    -- the object can be stored before this row exists (§11a: a record whose
    -- file is still uploading fails for anything that reads it in that window)
    -- and a retry rewrites the same object instead of a second copy.
    storage_path        text not null,
    original_filename   text not null,                 -- as served, however mangled
    media_type          text,
    content_sha256      text not null,
    byte_count          bigint not null,               -- recorded here, not derived from storage
    page_count          integer,                       -- null for a non-paginated original
    -- Per-page facts the splitting bench shows as badges: restarted pagination,
    -- a header date, an account number, whether the page is readable. Derived
    -- from the text layer, computed ONCE when a bench session opens, and
    -- cached here because the alternative is re-downloading a 33 MB original
    -- on every grid request. Same argument as statements.extraction holding
    -- provenance: derived, disposable, and far cheaper to keep than to redo.
    page_features       jsonb,
    received_on         date not null default current_date,
    received_by_staff_id integer references staff (id),
    created_at          timestamptz not null default now(),
    -- One matter cannot hold the same bytes twice. Across matters it can: the
    -- same bank statement is produced in two cases and each production is its
    -- own record.
    unique (matter_id, content_sha256)
);

alter table source_documents enable row level security;

comment on table source_documents is
    'The document as it reached us. Append-only: never split, OCR''d, '
    'converted, classified or served from. Work happens on `documents`.';

-- ── The WORKING copy: one row per document after splitting ───────────────────
--
-- `source_document_id` plus the parent page range is the lineage. A split child
-- carries the pages it came from so a Bates range can be derived and so
-- "where did this come from" is a column rather than an afternoon.
create table if not exists documents (
    id                  serial primary key,
    matter_id           integer not null references matters (id),
    source_document_id  integer not null references source_documents (id),
    -- The page range lives in `document_source_pages`, NOT here. A bundle made
    -- on the splitting bench may hold discontinuous pages, so a document's
    -- extent is a list of ranges and a stored first/last alongside it would be
    -- a second representation of the same fact -- which is the drift this
    -- codebase keeps meeting. The span is the `document_page_spans` view.
    storage_path         text,                         -- the working PDF; null while it is being built

    -- Classification (step 4). `doc_type` is the taxonomy label, which is also
    -- the routing key -- see the registry in the spec.
    doc_type            text,
    doc_type_confidence numeric(4,3),
    classified_at       timestamptz,
    needs_review        boolean not null default true,

    -- Exhibit-facing. Never the file name: internal and client file names carry
    -- commentary that must not be served.
    description         text,
    doc_date            date,
    period_begin        date,                          -- statements: drives coverage/gap analysis
    period_end          date,

    -- Bates, as text through bates_service.parse_stamp rather than an integer
    -- split: the firm's productions print 'KF 000101' and the separator is
    -- reprinted as the production writes it.
    bates_first         text,
    bates_last          text,
    bates_source        text check (bates_source is null or bates_source in
                          ('detected', 'manual', 'applied')),

    -- Where a bank, card, brokerage, retirement, policy or annuity statement
    -- was routed to the existing ingest, this is that statement. The index then
    -- reads periods and Bates from ONE record instead of holding a second copy
    -- that can disagree with it.
    statement_id        integer references financial_account_statements (id) on delete set null,

    -- A FRAGMENT OF AN INTERRUPTED DOCUMENT, pointing at the fragment before it.
    --
    -- Opposing counsel's scanned piles contain documents interrupted mid-way by
    -- unrelated pages and then resumed. A split child stays a CONTIGUOUS page
    -- range of its parent and such a document becomes two children, related
    -- here rather than merged, for three reasons:
    --
    --   1. The production index cites a Bates RANGE. A document assembled from
    --      non-contiguous pages needs a list, and an exhibit row reading
    --      '002983-002985, 002987-002990' invites the question "what is on
    --      002986?" -- a real question whose answer is "a different document".
    --      It has to be asked, not papered over.
    --   2. Cutting is mechanical; stitching is an ASSERTION that two page
    --      ranges are one document. That is attorney judgment, so it is a
    --      recorded decision with somebody's name on it, never an inference
    --      drawn by a splitter. Same rule as ownership and property_character.
    --   3. The pages between them were produced and must still appear in the
    --      index. Merging across them tempts an implementation to drop them.
    --
    -- Set by a person. Nothing in the pipeline may write it.
    continues_document_id integer references documents (id) on delete set null,

    -- How the text being classified was obtained. 'native' is the text layer as
    -- produced; 'ocr' means we made one, so a figure read from it is a reading
    -- of a reading and the UI says so.
    --
    -- 'unreadable' is a TERMINAL verdict, and it has to exist: a 396-page pile
    -- measured at 0% usable text contains scans that will never produce one, and
    -- without a terminal state such a page is indistinguishable from one not yet
    -- processed. It would be re-OCR'd on every run, re-reported as a gap, and
    -- re-queued forever. A page recorded unreadable is a page somebody has to
    -- read by eye, which is the correct outcome rather than a failure.
    --
    -- AN UNREADABLE DOCUMENT STILL APPEARS IN THE PRODUCTION INDEX. It was
    -- produced, it carries a Bates range, and omitting it from a cumulative
    -- index served under Rule 196.3(c) is the one thing the index must not do.
    -- It appears with its range and a description saying it could not be read.
    text_layer          text check (text_layer is null or text_layer in
                          ('native', 'ocr', 'mixed', 'none', 'unreadable')),
    text_quality        integer check (text_quality is null or text_quality between 0 and 100),

    -- PROTECTED HEALTH INFORMATION. Set from the label (medical_record,
    -- mental_health_record, drug_or_alcohol_test_result) OR from what an
    -- extractor pulled: a diagnosis code taken out of an email makes that email
    -- PHI whatever it was labelled. `phi_basis` records which, because the two
    -- are reviewed differently -- a label is a guess somebody can correct, an
    -- extracted diagnosis is the thing itself.
    --
    -- Drug and alcohol results are treated as PHI here even though under the
    -- governing statutes they qualify only where the purpose of the test was
    -- diagnosis or treatment rather than compliance with a court order. That is
    -- deliberately over-inclusive and must not be "simplified" later: the cost
    -- of the wrong call in this direction is a warning nobody needed.
    contains_phi        boolean not null default false,
    phi_basis           text check (phi_basis is null or phi_basis in
                          ('label', 'extraction', 'manual')),

    -- WHOSE PHI, which is what the warnings and the exclusions turn on.
    -- `contains_phi` is the superset: true whenever either subject flag is.
    -- Both can be true at once -- a record covering mother and child -- and the
    -- stricter rule then governs.
    --
    -- Neither set, with contains_phi true, is the FOURTH and commonest case in
    -- a production: the opposing party's or a third party's records. It is also
    -- the one a client most wants to see and may never be shown.
    --
    -- Whose it is, is a determination, not an extraction: match the patient
    -- against the client and `matter_children` on FIRST AND LAST name, the
    -- intake_service._match_confidence rule. A surname match is categorically
    -- wrong here -- both parties and every child share one, so it would route
    -- the opposing party's psychiatric records into the client's portal.
    -- Unmatched stays in the strictest bucket.
    phi_subject_client      boolean not null default false,
    phi_subject_minor_child boolean not null default false,

    -- The companion answers from the classifier call. Cheap in the same call,
    -- and each one routes or warns: attorney_involved is a privilege TRIPWIRE
    -- and never a privilege determination.
    flags               jsonb not null default '[]'::jsonb,
    withheld            boolean not null default false, -- withheld documents never reach the index
    byte_count          bigint,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz,
    -- A fragment cannot continue itself. A longer cycle is not expressible in a
    -- check constraint and is prevented in the service, which is also where the
    -- "same matter, same source document" rule belongs.
    check (continues_document_id is null or continues_document_id <> id)
);

alter table documents enable row level security;

-- ── A document's extent: one or more page ranges of its source ──────────────
--
-- 1-based and inclusive, matching physical_page_number and every other page
-- number here. `seq` is the reading order, which on the splitting bench is the
-- order the operator selected -- an assertion about the document, not an
-- incidental detail, so nothing sorts it.
--
-- WHY A LIST AND NOT A FIRST AND LAST: opposing counsel's scanned piles contain
-- documents interrupted mid-way by unrelated pages and resumed later. Where a
-- PERSON groups those pages, the assertion that they are one document has been
-- made and recorded, which is the condition for treating them as one. Where
-- only the automatic splitter has looked, nobody has asserted anything and the
-- fragments stay separate, related by `continues_document_id`.
create table if not exists document_source_pages (
    id              serial  primary key,
    document_id     integer not null references documents (id) on delete cascade,
    seq             integer not null,
    page_first      integer not null,
    page_last       integer not null,
    created_at      timestamptz not null default now(),
    unique (document_id, seq),
    check (page_last >= page_first),
    check (page_first >= 1),

    -- One document may not claim a page twice: that duplicates a page of
    -- evidence inside a single exhibit.
    --
    -- THERE IS DELIBERATELY NO CONSTRAINT ACROSS DOCUMENTS. A combined
    -- statement prints one account's closing lines and the next account's
    -- opening on the same physical page -- `_statement_pages()` cuts at printed
    -- headers precisely because of this, and SUSPECT_SPLIT treats a single
    -- shared boundary page as normal while treating a wider overlap as suspect.
    -- Forbidding it here would make the commonest real multi-account statement
    -- unexpressible. Overlap between documents is a FINDING, reported on the
    -- bench and by the split guard; overlap within one is an error.
    exclude using gist (
        document_id with =,
        int4range(page_first, page_last, '[]') with &&
    )
);

alter table document_source_pages enable row level security;

create index if not exists idx_document_source_pages_doc
    on document_source_pages (document_id, seq);

-- The span, derived rather than stored. `discontinuous` is what tells the
-- production index to print a list of Bates runs instead of one range.
create or replace view document_page_spans with (security_invoker = on) as
select document_id,
       min(page_first)                          as page_first,
       max(page_last)                           as page_last,
       count(*) > 1                             as discontinuous,
       sum(page_last - page_first + 1)          as page_count
from document_source_pages
group by document_id;

revoke all on document_page_spans from anon, authenticated;

create index if not exists idx_documents_matter_type on documents (matter_id, doc_type);
create index if not exists idx_documents_source on documents (source_document_id);
create index if not exists idx_documents_statement on documents (statement_id)
    where statement_id is not null;
create index if not exists idx_documents_review on documents (matter_id)
    where needs_review;
-- Every report has to ask "does anything here trace to PHI?", so the answer is
-- indexed rather than computed over a production.
create index if not exists idx_documents_phi on documents (matter_id)
    where contains_phi;

-- ── Exhibit-facing names for taxonomy labels ─────────────────────────────────
--
-- Internal labels are not exhibit labels. Unseeded here deliberately: the
-- taxonomy is generated from the processor registry (see the spec), so seeding
-- it in SQL would be a second list to keep in step.
create table if not exists document_type_labels (
    doc_type            text primary key,
    display_label       text not null,
    created_at          timestamptz not null default now()
);

alter table document_type_labels enable row level security;

-- ── STEP 8: the label-to-request mapping, which is the spine ─────────────────
--
-- One judgment per (request, label), not per (document, request). A matter with
-- six labels present and twenty requests is 120 decisions made once, reviewable
-- in a sitting, and every document produced later inherits by its label. The
-- as-received draft mapped documents directly, which is thousands of
-- probabilities nobody can affirm.
--
-- It is a legal conclusion, so it is per matter (through the request), recorded
-- with who approved it, and never firm-wide.
create table if not exists request_document_types (
    id                  serial primary key,
    request_item_id     integer not null references discovery_request_items (id) on delete cascade,
    doc_type            text not null,
    source              text not null check (source in ('rule', 'model', 'manual')),
    model_probability   numeric(4,3),
    status              text not null default 'proposed'
                          check (status in ('proposed', 'approved', 'rejected')),
    reviewed_by_staff_id integer references staff (id),
    reviewed_at         timestamptz,
    note                text,
    created_at          timestamptz not null default now(),
    unique (request_item_id, doc_type)
);

alter table request_document_types enable row level security;

-- ── Per-document exceptions, in both directions ──────────────────────────────
--
-- The label spine over-includes and under-includes in predictable ways: a 2019
-- statement carries the right label and falls outside a request reaching back
-- to 2022, and a tax return is responsive to a debts request only where it
-- happens to show an amount due. So an override is a ROW, never a delete --
-- the same reason a cleared client tag is a row: a suppression has to say who
-- made it.
create table if not exists document_request_overrides (
    id                  serial primary key,
    document_id         integer not null references documents (id) on delete cascade,
    request_item_id     integer not null references discovery_request_items (id) on delete cascade,
    effect              text not null check (effect in ('include', 'exclude')),
    reason              text,
    decided_by_staff_id integer references staff (id),
    decided_at          timestamptz not null default now(),
    unique (document_id, request_item_id)
);

alter table document_request_overrides enable row level security;

-- ── Storage accounting: a level, not an event ────────────────────────────────
--
-- Supabase bills the project, not the matter, and there is no per-object cost
-- API. Bytes are recorded on the owning row at upload instead of derived from
-- storage paths, because `intake/{job_id}.pdf` -- where every statement PDF
-- lands today -- does not name a matter at all.
--
-- A snapshot rather than a running total: storage is a level, so a month's cost
-- has to be re-derivable from what was held, and a purge under the retention
-- policy must not silently rewrite last year's figure. Priced at read time from
-- `service_rates` (041), provider 'supabase', meter 'gb_month'.
create table if not exists storage_snapshots (
    id                  serial primary key,
    matter_id           integer references matters (id),   -- null = unattributed
    -- The matter's status AT THE SNAPSHOT, not read live.
    --
    -- Closed-matter storage is overhead the firm eats rather than a cost
    -- allocable to a client, so the report rolls `closed` and `archived` into
    -- one line. If that rollup read the CURRENT status, closing a matter would
    -- silently move a year of history out of the per-matter figures and last
    -- month's report would stop reproducing. Prices are read at read time
    -- because they get corrected; a status was a fact on a date.
    matter_status       text,
    taken_on            date not null,
    as_produced_bytes   bigint not null default 0,
    working_bytes       bigint not null default 0,
    object_count        integer not null default 0,
    created_at          timestamptz not null default now(),
    unique (matter_id, taken_on)
);

alter table storage_snapshots enable row level security;

-- ── The exhibit: Rule 196.3(c) production index ──────────────────────────────
--
-- Composed from the label spine plus overrides, so a document produced next
-- month appears against every request its label is approved for with no new
-- judgment. Only approved mappings appear; withheld documents never do -- they
-- belong in the withholding statement and the privilege log.
create or replace view production_index with (security_invoker = on) as
with mapped as (
    select d.id            as document_id,
           ri.id           as request_item_id
      from documents d
      join request_document_types rdt
        on rdt.doc_type = d.doc_type
       and rdt.status = 'approved'
      join discovery_request_items ri
        on ri.id = rdt.request_item_id
       and ri.matter_id = d.matter_id
     where not d.withheld
    union
    select o.document_id, o.request_item_id
      from document_request_overrides o
     where o.effect = 'include'
)
select dp.matter_id,
       dp.producing_party,
       dr.id                                   as request_set_id,
       dr.request_type,
       ri.request_number,
       d.bates_first,
       d.bates_last,
       coalesce(l.display_label, d.doc_type)   as document_type,
       d.description,
       d.doc_date,
       dp.produced_on,
       dp.volume_label
  from mapped m
  join documents d                 on d.id = m.document_id
  join discovery_request_items ri  on ri.id = m.request_item_id
  join discovery_requests dr       on dr.id = ri.discovery_request_id
  join source_documents sd         on sd.id = d.source_document_id
  join document_productions dp     on dp.id = sd.production_id
  left join document_type_labels l on l.doc_type = d.doc_type
 where not exists (
         select 1 from document_request_overrides x
          where x.document_id = d.id
            and x.request_item_id = ri.id
            and x.effect = 'exclude')
 order by dr.id, ri.request_number, d.bates_first;

revoke all on production_index from anon, authenticated;

-- ── QC 1: produced, mapped to nothing ────────────────────────────────────────
--
-- Must be empty or explained before an index is served. `note` is where the
-- explanation goes -- a document produced voluntarily responds to no request,
-- and with nowhere to say so the view never empties and becomes a queue nobody
-- reads.
create or replace view unmapped_documents with (security_invoker = on) as
select d.id, d.matter_id, d.doc_type, d.description, d.bates_first, d.bates_last
  from documents d
 where not d.withheld
   and not exists (select 1 from production_index pi
                    where pi.bates_first = d.bates_first
                      and pi.matter_id = d.matter_id);

revoke all on unmapped_documents from anon, authenticated;

-- ── QC 2: a request with nothing produced against it ─────────────────────────
--
-- On our side, confirm the written response says so. On theirs, this is the
-- first draft of a deficiency letter.
create or replace view requests_without_documents with (security_invoker = on) as
select ri.matter_id,
       dr.id            as request_set_id,
       dr.direction,
       dr.request_type,
       ri.request_number,
       ri.source_text
  from discovery_request_items ri
  join discovery_requests dr on dr.id = ri.discovery_request_id
 where not exists (
         select 1 from request_document_types rdt
          where rdt.request_item_id = ri.id and rdt.status = 'approved')
   and not exists (
         select 1 from document_request_overrides o
          where o.request_item_id = ri.id and o.effect = 'include');

revoke all on requests_without_documents from anon, authenticated;

commit;
