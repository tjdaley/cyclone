-- Production index data model (PostgreSQL / Supabase)
-- One row per document, one row per document-request mapping.
-- The exhibit is a view over the join, so duplicate rows can never drift apart.

create table matter (
  matter_id        uuid primary key default gen_random_uuid(),
  cause_number     text not null,
  style            text not null,
  court            text
);

-- A served set of requests, e.g. "Petitioner's First Request for Production".
create table request_set (
  request_set_id   uuid primary key default gen_random_uuid(),
  matter_id        uuid not null references matter,
  title            text not null,              -- as captioned on the served set
  short_name       text not null,              -- "1st RFP", shown in the exhibit
  requesting_party text not null,
  responding_party text not null,
  served_on        date,
  unique (matter_id, short_name)
);

create table request (
  request_id       uuid primary key default gen_random_uuid(),
  request_set_id   uuid not null references request_set,
  request_number   int  not null,
  request_text     text not null,              -- verbatim, as served
  operative_text   text,                       -- self-contained restatement fed to the model:
                                               -- definitions, time period, and any narrowing
                                               -- by objection, agreement, or order folded in
  unique (request_set_id, request_number)
);

-- A production event: one service of documents on one date.
create table production (
  production_id    uuid primary key default gen_random_uuid(),
  matter_id        uuid not null references matter,
  volume_label     text not null,              -- "PROD001"
  producing_party  text not null,              -- ours or theirs: same model audits both sides
  produced_on      date not null,
  unique (matter_id, volume_label)
);

create table document (
  document_id      uuid primary key default gen_random_uuid(),
  production_id    uuid not null references production,
  bates_prefix     text not null,
  bates_begin      int  not null,
  bates_end        int  not null,
  internal_file    text not null,              -- working file name; never shown in the exhibit
  doc_type         text not null,              -- classifier label, e.g. bank_statement
  doc_type_conf    numeric(4,3),               -- classifier probability
  description      text not null,              -- exhibit-facing: "Chase checking x1234, Jan. 2023"
  doc_date         date,                       -- document or statement-end date, if known
  period_begin     date,                       -- for statements: enables coverage/gap analysis
  period_end       date,
  check (bates_end >= bates_begin)
);

-- Exhibit-facing names for classifier labels.
create table doc_type_display (
  doc_type         text primary key,
  display_label    text not null               -- "Bank statement"
);

-- Many-to-many: a document may respond to several requests.
create table document_request (
  document_id      uuid not null references document,
  request_id       uuid not null references request,
  source           text not null check (source in ('rule', 'model', 'manual')),
                                               -- rule   = label + metadata match
                                               -- model  = responsiveness score
                                               -- manual = added by reviewer
  model_prob       numeric(4,3),               -- null unless source = 'model'
  status           text not null default 'proposed'
                     check (status in ('proposed', 'approved', 'rejected')),
  reviewed_by      text,
  reviewed_at      timestamptz,
  primary key (document_id, request_id)
);

-- The exhibit. Only approved mappings appear.
create view production_index as
select
  rs.short_name                                          as request_set,
  r.request_number                                       as request_no,
  d.bates_prefix || lpad(d.bates_begin::text, 6, '0')
    || case when d.bates_end > d.bates_begin
            then '-' || d.bates_prefix || lpad(d.bates_end::text, 6, '0')
            else '' end                                  as bates_range,
  coalesce(t.display_label, d.doc_type)                  as document_type,
  d.description,
  d.doc_date,
  p.produced_on                                          as date_produced,
  p.volume_label                                         as production_volume,
  rs.matter_id,
  p.producing_party
from document_request dr
join document   d  on d.document_id   = dr.document_id
join request    r  on r.request_id    = dr.request_id
join request_set rs on rs.request_set_id = r.request_set_id
join production p  on p.production_id = d.production_id
left join doc_type_display t on t.doc_type = d.doc_type
where dr.status = 'approved'
order by rs.served_on, rs.short_name, r.request_number, d.bates_prefix, d.bates_begin;

-- QC 1: produced documents not yet tied to any approved request.
create view unmapped_documents as
select d.*
from document d
where not exists (
  select 1 from document_request dr
  where dr.document_id = d.document_id and dr.status = 'approved'
);

-- QC 2: requests with nothing produced. On our side, confirm the response says so;
-- on theirs, this is the starting list for a deficiency letter.
create view requests_without_documents as
select rs.matter_id, rs.short_name, r.request_number, r.request_text
from request r
join request_set rs on rs.request_set_id = r.request_set_id
where not exists (
  select 1 from document_request dr
  where dr.request_id = r.request_id and dr.status = 'approved'
);
