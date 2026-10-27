# Document pipeline — specs

**Status: proposal. Nothing here is built.** Everything else in `docs/`
describes shipped behaviour; these files describe something that does not exist
yet, and in three months that is the distinction most likely to mislead
somebody.

## What is here, and which file is authoritative

| File | Provenance | Authority |
| ---- | ---------- | --------- |
| [`SPEC_document_pipeline.md`](SPEC_document_pipeline.md) | Written in this repo, 19 Oct 2026, from the outline Tom laid out | **Authoritative.** Read this one |
| `db/migrations/047_document_pipeline.sql` | Same | **Authoritative** data model, and now a real migration. Verified against Postgres 16 by `tests/test_document_pipeline_sql.py`. Run by hand after 046 |
| [`SPEC_splitting_bench.md`](SPEC_splitting_bench.md) | Written in this repo, 20 Oct 2026 | **Authoritative** design for the manual split screen. Revises 047: a document's extent becomes a list of page ranges |
| [`SPEC_discovery_pipeline.md`](SPEC_discovery_pipeline.md) | Claude, chat interface, 19 Oct 2026 — **no access to this repository** | As received. Superseded, kept for its reasoning |
| [`discovery_classifier_schema.json`](discovery_classifier_schema.json) | Same | As received. Taxonomy draft, still the starting point — but the option list is to be **generated from the processor registry**, not maintained here |
| [`production_index_schema.sql`](production_index_schema.sql) | Same | As received. Superseded by `schema.sql`, which maps it onto the deployed tables |

**The three as-received files are kept byte for byte and are not edited.** That
is deliberate, and it is the same rule the spec itself argues for: keep the
document as it arrived, work on a copy, and make the lineage a matter of record
rather than of memory. Provenance lives in this table, not in headers added to
the originals.

## Why the received spec needed revising

It was written without access to the repository, so it proposed a few layers
that already exist and a data model with three tables that duplicate deployed
ones. The reasoning in it is good and most of it survives — §2 of
`SPEC_document_pipeline.md` lists every change and why.

## A note on the folder name

The folder is `document-labeling`; labeling is step 4 of nine. If this gets
renamed, `document-pipeline` is the better name — and the row in
[`docs/README.md`](../../README.md) needs renaming with it.
