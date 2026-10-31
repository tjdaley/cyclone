"""
tests/test_guest_sql.py - Migration 052 against real Postgres 16, in Docker.

052 rewrites two live constraints — who a user_roles row may belong to, and who
may have applied a tag — and a constraint that is too loose admits a guest role
pointing at a staff record, while one too tight refuses every existing row. So
the checks run on a real database with the constraints 002, 021 and 044 left.

Without Docker it exits **2** and prints SKIPPED: a check that silently did not
run must not read as a pass.

Run:  venv/Scripts/python.exe tests/test_guest_sql.py
"""
import os
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MIGRATIONS = os.path.join(ROOT, "db", "migrations")
IMAGE = "postgres:16"
FAILURES: list[str] = []

# What production already has that 052 relies on: the 002 inline checks on
# user_roles (role list and role_link_check), 021's subject check, and 044's tag
# constraints.
PRELUDE = """
create role anon nologin;
create role authenticated nologin;
create table firms (id integer primary key, name text);
insert into firms values (1, 'Default firm');
create table staff (id integer primary key);
create table clients (id integer primary key);
create table matters (id integer primary key);
insert into staff values (7); insert into clients values (5); insert into matters values (1), (2);

create table user_roles (
    id serial primary key,
    supabase_uid text,
    auth_email text,
    role text not null check (role in ('client', 'attorney', 'paralegal', 'admin')),
    staff_id integer references staff (id),
    client_id integer references clients (id),
    created_at timestamptz not null default now(),
    updated_at timestamptz,
    constraint role_link_check check (
        (role in ('attorney', 'paralegal', 'admin') and staff_id is not null and client_id is null)
        or (role = 'client' and client_id is not null and staff_id is null))
);
alter table user_roles add constraint user_roles_subject_matches_role check (
    (role = 'client' and client_id is not null and staff_id is null)
    or (role in ('attorney', 'paralegal', 'admin') and staff_id is not null and client_id is null));
insert into user_roles (auth_email, role, staff_id) values ('para@firm.test', 'paralegal', 7);
insert into user_roles (auth_email, role, client_id) values ('client@x.test', 'client', 5);

create table transaction_tags (id serial primary key, matter_id integer, label text not null);
create table financial_account_transaction_tags (
    id serial primary key,
    transaction_id integer not null,
    tag_id integer not null references transaction_tags (id),
    tagged_by_staff_id integer,
    tagged_by_client_id integer references clients (id),
    source text not null default 'staff',
    cleared_at timestamptz,
    cleared_by_staff_id integer,
    unique (transaction_id, tag_id),
    constraint financial_account_transaction_tags_source check (source in ('staff', 'client')),
    constraint financial_account_transaction_tags_one_actor check (
        (source = 'staff' and tagged_by_staff_id is not null and tagged_by_client_id is null)
        or (source = 'client' and tagged_by_client_id is not null and tagged_by_staff_id is null))
);
insert into transaction_tags (matter_id, label) values (1, 'Staff tag');
insert into financial_account_transaction_tags (transaction_id, tag_id, tagged_by_staff_id)
values (100, 1, 7);
"""


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def docker(*args, stdin=None, check_rc=True):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    result = subprocess.run(["docker", *args], input=stdin, capture_output=True,
                            text=True, encoding="utf-8", env=env)
    if check_rc and result.returncode != 0:
        raise RuntimeError("docker %s failed: %s" % (args[0], result.stderr.strip()[-500:]))
    return result


def skip(reason):
    print("SKIPPED: %s" % reason)
    print("Migration 052 was NOT tested. Exit code 2 so this cannot read as a pass.")
    sys.exit(2)


if shutil.which("docker") is None:
    skip("docker is not on PATH")
if docker("info", check_rc=False).returncode != 0:
    skip("the Docker daemon is not reachable")

name = "cyclone-guestsql-%s" % uuid.uuid4().hex[:8]


def psql(sql):
    return docker("exec", "-i", name, "psql", "-U", "postgres", "-q", "-tA",
                  "-v", "ON_ERROR_STOP=1", stdin=sql, check_rc=False)


def ok(sql):
    return psql(sql).returncode == 0


def value(sql):
    return psql(sql).stdout.strip()


try:
    docker("run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=t", IMAGE)
    for _ in range(60):
        if docker("exec", name, "pg_isready", "-U", "postgres", check_rc=False).returncode == 0:
            break
        time.sleep(1)
    time.sleep(1)

    print("052 applies on top of what production has")
    migration = open(os.path.join(MIGRATIONS, "052_financial_guest_access.sql"), encoding="utf-8").read()
    applied = psql(PRELUDE + "\n" + migration)
    check("applies without error", applied.returncode, 0)
    if applied.returncode != 0:
        print(applied.stderr[-1500:])
        raise SystemExit(1)
    check("and can be run a second time", psql(migration).returncode, 0)
    check("existing staff and client rows still stand",
          value("select count(*) from user_roles"), "2")

    print("user_roles: a guest row belongs to a guest and nothing else")
    ok(  # a guest to point at
        "insert into guest_professionals (full_name, email) values ('Pat', 'pat@x.test');")
    check("a guest role row with a guest id is accepted",
          ok("insert into user_roles (auth_email, role, guest_id) "
             "values ('pat@x.test', 'financial_professional', 1);"), True)
    check("a guest role row pointing at staff is refused",
          ok("insert into user_roles (role, staff_id) values ('financial_professional', 7);"), False)
    check("a staff role row carrying a guest id is refused",
          ok("insert into user_roles (role, staff_id, guest_id) values ('paralegal', 7, 1);"), False)
    check("an unknown role is still refused",
          ok("insert into user_roles (role, staff_id) values ('superuser', 7);"), False)

    print("tag links: exactly one actor, now one of three")
    ok("insert into transaction_tags (matter_id, label, guest_id) values (1, 'Mine', 1);")
    check("a guest link is accepted",
          ok("insert into financial_account_transaction_tags "
             "(transaction_id, tag_id, source, tagged_by_guest_id) values (100, 2, 'guest', 1);"), True)
    check("a guest link naming staff as well is refused",
          ok("insert into financial_account_transaction_tags (transaction_id, tag_id, source, "
             "tagged_by_guest_id, tagged_by_staff_id) values (101, 2, 'guest', 1, 7);"), False)
    check("a staff link is still accepted",
          ok("insert into financial_account_transaction_tags (transaction_id, tag_id, "
             "tagged_by_staff_id) values (102, 1, 7);"), True)

    print("grants: active only with an activation time; one live grant per guest per matter")
    future = "now() + interval '180 days'"
    check("a pending grant is accepted",
          ok("insert into matter_guest_grants (matter_id, guest_id, designation, expires_at) "
             "values (1, 1, 'consulting_expert', %s);" % future), True)
    check("active with no activation time is refused",
          ok("insert into matter_guest_grants (matter_id, guest_id, designation, status, expires_at) "
             "values (2, 1, 'non_expert', 'active', %s);" % future), False)
    check("a second live grant on the same matter is refused",
          ok("insert into matter_guest_grants (matter_id, guest_id, designation, expires_at) "
             "values (1, 1, 'non_expert', %s);" % future), False)
    check("revoked needs who and when",
          ok("update matter_guest_grants set status = 'revoked' where id = 1;"), False)
    ok("update matter_guest_grants set status = 'revoked', revoked_at = now(), revoked_by = 'Tom' "
       "where id = 1;")
    check("after revocation a new grant may follow",
          ok("insert into matter_guest_grants (matter_id, guest_id, designation, expires_at) "
             "values (1, 1, 'testifying_expert', %s);" % future), True)
    check("an unknown designation is refused",
          ok("insert into matter_guest_grants (matter_id, guest_id, designation, expires_at) "
             "values (2, 1, 'oracle', %s);" % future), False)

    print("the ledger outlives a statement")
    check("a row naming a statement id that does not exist is accepted",
          ok("insert into guest_document_ledger (grant_id, statement_id) "
             "select max(id), 99999 from matter_guest_grants;"), True)

    print("every new table is closed to the public key (040)")
    for table in ("guest_professionals", "matter_guest_grants", "guest_consents",
                  "guest_document_ledger", "guest_sign_ins"):
        check("%s has RLS on" % table,
              value("select relrowsecurity from pg_class where relname = '%s';" % table), "t")
        check("%s is not readable by anon" % table,
              value("select has_table_privilege('anon', 'public.%s', 'select');" % table), "f")
finally:
    docker("rm", "-f", name, check_rc=False)

print("")
print("FAILURES: %d" % len(FAILURES))
sys.exit(1 if FAILURES else 0)
