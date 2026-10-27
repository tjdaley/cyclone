"""
tests/test_document_pipeline_sql.py - The 047 data model, against real Postgres.

Migrations here are run by hand, so SQL that has never executed reaches
production and fails there, in front of somebody. This starts a throwaway
Postgres 16 in Docker, builds the few Supabase objects 047 assumes, applies it,
and checks the things that would be discovered at the worst moment:

- It applies at all. `btree_gist` has never been installed by a migration here
  (001 installs pg_trgm and pgcrypto), and the exclusion constraint cannot be
  created without it.
- It is re-runnable. A hand-run migration gets run twice by somebody who is not
  sure whether it took.
- RLS on every table and `security_invoker` plus a revoke on every view --
  tests/test_migration_rls.py checks the TEXT; this checks the database.
- The exclusion constraint rejects a document claiming one page twice, and
  ALLOWS two documents sharing a boundary page. The second is the one worth
  pinning: a combined statement prints one account's close and the next
  account's opening on the same sheet, and the obvious stricter constraint makes
  that unexpressible.

Needs Docker. Without it this prints SKIPPED and exits 2 -- deliberately not 0,
because a check that silently did not run is indistinguishable from one that
passed.

Run:  venv/Scripts/python.exe tests/test_document_pipeline_sql.py
"""
import os
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCHEMA = os.path.join(ROOT, "db", "migrations", "047_document_pipeline.sql")
IMAGE = "postgres:16"

FAILURES: list[str] = []

# What a Supabase database already holds that 047 references.
PRELUDE = """
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create table matters (
    id integer primary key,
    matter_name text,
    client_id integer,
    case_style text
);
create table staff (id integer primary key, email text);
create table clients (id integer primary key, client_name text);
create table firms (id integer primary key, name text);
create table financial_account_statements (
    id integer primary key,
    period_start date,
    period_end date,
    review_status text
);
create table discovery_requests (
    id integer primary key,
    matter_id integer references matters (id),
    propounded_date date,
    due_date date,
    request_type text,
    response_served_date date
);
-- 007 RENAMED the old discovery_requests to discovery_request_items, so the
-- items table carries matter_id, request_type and request_number -- the three
-- columns 007's dropped unique constraint names. Getting this wrong is what
-- the first run of this test did, and it reported a bug in schema.sql that was
-- really a bug in this prelude: a fixture that does not match the deployed
-- schema tests the fixture.
create table discovery_request_items (
    id integer primary key,
    matter_id integer references matters (id),
    discovery_request_id integer references discovery_requests (id),
    request_type text,
    request_number integer,
    source_text text
);
insert into matters values (311, 'Salmons v. Salmons', 42, 'No. DF-24-01234');
insert into clients values (42, 'Salmons');
insert into financial_account_statements values
    (900, '2026-01-04', '2026-02-03', 'auto_accepted');
insert into discovery_requests values (5, 311, '2026-02-01', '2026-03-03', 'production', null);
insert into discovery_request_items values (77, 311, 5, 'production', 12, 'All bank statements...');
"""


def docker(*args, stdin=None, check_rc=True):
    result = subprocess.run(["docker", *args], input=stdin, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if check_rc and result.returncode != 0:
        raise RuntimeError("docker %s failed: %s" % (args[0], result.stderr.strip()[-500:]))
    return result


def skip(reason):
    print("SKIPPED: %s" % reason)
    print("A check that did not run is not a check that passed.")
    sys.exit(2)


if shutil.which("docker") is None:
    skip("docker is not on PATH")
if docker("info", check_rc=False).returncode != 0:
    skip("docker is installed but not running")

name = "cyclone-047-%s" % uuid.uuid4().hex[:8]


def psql(sql, stop_on_error=True):
    args = ["exec", "-i", name, "psql", "-U", "postgres", "-q", "-tA", "-F", "|"]
    if stop_on_error:
        args += ["-v", "ON_ERROR_STOP=1"]
    return docker(*args, stdin=sql, check_rc=False)


def rows(sql):
    result = psql(sql)
    if result.returncode != 0:
        return ["ERROR: %s" % result.stderr.strip()[-300:]]
    return [line for line in result.stdout.strip().splitlines() if line]


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


print("Starting %s ..." % IMAGE)
docker("run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=t", IMAGE)
try:
    for _ in range(60):
        if docker("exec", name, "pg_isready", "-U", "postgres", check_rc=False).returncode == 0:
            break
        time.sleep(1)
    else:
        skip("postgres did not become ready")

    schema_sql = open(SCHEMA, encoding="utf-8").read()

    print("\n1. It applies, and applies twice")

    check("the Supabase objects it assumes", psql(PRELUDE).returncode, 0)

    first = psql(schema_sql)
    if first.returncode != 0:
        print("  FAIL schema.sql did not apply\n%s" % first.stderr.strip()[-1500:])
        FAILURES.append("schema.sql applies")
    else:
        print("  ok   schema.sql applies")

    # A hand-run migration gets run twice by somebody unsure whether it took.
    second = psql(schema_sql)
    check("and is re-runnable", second.returncode, 0)

    check("btree_gist is installed by the file itself",
          rows("select extname from pg_extension where extname = 'btree_gist'"),
          ["btree_gist"])

    print("\n2. RLS on every new table (040's rule, checked in the database)")

    unprotected = rows("""
        select c.relname from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
          and c.relrowsecurity = false
          and c.relname in ('document_productions','source_documents','documents',
                            'document_source_pages','document_type_labels',
                            'request_document_types','document_request_overrides',
                            'storage_snapshots')
        order by 1""")
    check("no new table is left without RLS", unprotected, [])

    print("\n3. Views are security_invoker and closed to the public key")

    # A view runs with its OWNER's privileges unless told otherwise, so it
    # bypasses RLS on every table it reads -- the live exposure 040 found.
    # The option is stored VERBATIM: `with (security_invoker = on)` persists as
    # 'on' and `= true` as 'true'. Comparing to a literal string therefore gets
    # the answer wrong for one of the two spellings -- measured, both ways, in a
    # throwaway container. Cast instead, which accepts on/true/yes/1.
    not_invoker = rows("""
        select c.relname from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'v'
          and not coalesce((select option_value::boolean from
                pg_options_to_table(c.reloptions) where option_name = 'security_invoker'), false)
        order by 1""")
    check("every view is security_invoker", not_invoker, [])

    readable = rows("""
        select table_name from information_schema.role_table_grants
        where grantee in ('anon','authenticated') and privilege_type = 'SELECT'
        order by 1""")
    check("no view or table is granted to anon/authenticated", readable, [])

    print("\n4. A document's extent: the constraint, and what it must NOT forbid")

    psql("""
        insert into document_productions (matter_id, volume_label, producing_party, produced_on)
            values (311, 'PROD001', 'opposing', '2026-03-01');
        insert into source_documents (matter_id, production_id, content_sha256, byte_count,
                                      storage_path, original_filename)
            values (311, 1, repeat('a', 64), 33200000, 'matters/311/received/1.pdf', 'vol2.pdf');
        insert into documents (matter_id, source_document_id) values (311, 1), (311, 1), (311, 1);
    """)
    check("three documents on one source", rows("select count(*) from documents"), ["3"])

    ok = psql("""insert into document_source_pages (document_id, seq, page_first, page_last)
                 values (1, 1, 10, 12), (1, 2, 15, 17)""")
    check("a document may hold discontinuous ranges", ok.returncode, 0)

    check("the span view reports the outer bounds and says it is discontinuous",
          rows("select page_first, page_last, discontinuous, page_count "
               "from document_page_spans where document_id = 1"),
          ["10|17|t|6"])

    dup = psql("""insert into document_source_pages (document_id, seq, page_first, page_last)
                  values (1, 3, 11, 11)""")
    check("but NOT the same page twice", "23P01" in dup.stderr or "exclusion" in dup.stderr.lower(), True)

    reversed_range = psql("""insert into document_source_pages (document_id, seq, page_first, page_last)
                             values (2, 1, 9, 4)""")
    check("nor a reversed range", reversed_range.returncode != 0, True)

    zero = psql("""insert into document_source_pages (document_id, seq, page_first, page_last)
                   values (2, 1, 0, 3)""")
    check("nor page 0 — page numbers are 1-based here", zero.returncode != 0, True)

    # THE ONE WORTH PINNING. A combined statement prints one account's close and
    # the next account's opening on the same sheet. The obvious stricter
    # constraint -- a page belongs to one document -- makes that unexpressible.
    shared = psql("""
        insert into document_source_pages (document_id, seq, page_first, page_last)
            values (2, 1, 1, 5);
        insert into document_source_pages (document_id, seq, page_first, page_last)
            values (3, 1, 5, 9);
    """)
    check("TWO documents MAY share a boundary page — a combined statement does",
          shared.returncode, 0)

    print("\n5. Lineage and cascade")

    check("a source document is unique per matter by content hash",
          psql("insert into source_documents (matter_id, production_id, content_sha256, "
               "byte_count, storage_path, original_filename) values "
               "(311, 1, repeat('a', 64), 1, 'x', 'again.pdf')").returncode != 0, True)

    psql("delete from documents where id = 1")
    check("deleting a document takes its page ranges with it",
          rows("select count(*) from document_source_pages where document_id = 1"), ["0"])
    check("and leaves the other documents' ranges alone",
          rows("select count(*) from document_source_pages"), ["2"])

    print("\n6. Every model field has a column (the PGRST204 class of bug)")

    # schema_check does this at startup against Supabase, which needs
    # credentials this test does not have. Doing it here against the migration
    # catches the same fault earlier and offline -- and it is not hypothetical:
    # writing these models by hand left `page_features` off SourceDocument and
    # `created_at` off DocumentSourcePage, each a guaranteed 500 the first time
    # a row is written, because model_dump() includes a field even when it is
    # None and PostgREST rejects the whole row for an unknown column.
    sys.path.insert(0, os.path.join(ROOT, "app"))
    from db.models.document import (  # noqa: E402
        DocumentInDB, DocumentSourcePageInDB, SourceDocumentInDB,
    )

    for model, table in ((SourceDocumentInDB, "source_documents"),
                         (DocumentInDB, "documents"),
                         (DocumentSourcePageInDB, "document_source_pages")):
        live = set(rows(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = '%s'" % table))
        declared = set(model.model_fields)
        check("%s: no model field without a column" % table,
              sorted(declared - live), [])
        # The reverse is only worth knowing, not failing on: a column no model
        # reads is harmless, and several are written by the database.
        unread = sorted(live - declared)
        if unread:
            print("       (columns no model reads, which is fine: %s)"
                  % ", ".join(unread))

    print("\n7. The PHI flag defaults to the restrictive answer")

    check("may_receive_childrens_phi defaults false",
          rows("select may_receive_childrens_phi from matters where id = 311"), ["f"])
    check("a document is not PHI until something says so",
          rows("select contains_phi from documents where id = 2"), ["f"])

finally:
    docker("stop", name, check_rc=False)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all 047 SQL checks passed")
