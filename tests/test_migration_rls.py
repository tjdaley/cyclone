"""
tests/test_migration_rls.py - A new table must not be readable with the public key.

Only migration 005 ever enabled Row-Level Security. Every table created after it
-- jobs, statements, transactions, the CRM tables, the cost ledger -- was created
without it, and Supabase grants the `anon` role access to `public` by default.
The anon key is compiled into the frontend bundle, so a table without RLS is
readable through PostgREST by anybody who opens the app. The views in 004 were
worse: a view runs with its owner's privileges, so it bypassed RLS even on the
tables 005 protected.

040 closes everything that existed up to it, by scanning the catalog. This test
stops the next migration reopening the hole:

  * a migration AFTER 040 that creates a table must enable RLS on it in the
    same file
  * a migration AFTER 040 that creates a view must declare it security_invoker
  * a migration AFTER 040 that creates a callable function must revoke EXECUTE
    from anon -- PostgREST serves functions at /rest/v1/rpc

The file is read as text, the same way test_migration_seeds.py reads SQL. That
is crude, and it is enough: the failure it guards against is forgetting, not
disguise.

Run:  venv/Scripts/python.exe tests/test_migration_rls.py
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MIGRATIONS = os.path.join(ROOT, "db", "migrations")

FAILURES: list[str] = []

#: 040 sweeps every table and view that existed before it, from the catalog.
SWEPT_THROUGH = 40


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def without_comments(sql: str) -> str:
    """Drop `--` comments, so a table named in prose is not mistaken for DDL."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def number(filename: str) -> int:
    match = re.match(r"(\d+)_", filename)
    return int(match.group(1)) if match else -1


files = sorted(f for f in os.listdir(MIGRATIONS) if f.endswith(".sql") and number(f) >= 0)

print("040 exists and sweeps by catalog")
sweep = next((f for f in files if number(f) == SWEPT_THROUGH), None)
check("040_enable_rls.sql is present", sweep is not None, True)
if sweep:
    body = without_comments(io.open(os.path.join(MIGRATIONS, sweep), encoding="utf-8").read()).lower()
    check("it enables RLS on every table lacking it",
          "enable row level security" in body and "relrowsecurity" in body, True)
    check("and closes views to anon", "revoke all on" in body and "security_invoker" in body, True)

print("")
print("Every migration after 040 protects what it creates")

CREATE_TABLE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?([a-z_][a-z0-9_]*)", re.I)
CREATE_VIEW = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+(?:public\.)?([a-z_][a-z0-9_]*)"
    r"(.*?)\s+as\s", re.I | re.S)

checked = 0
for filename in files:
    if number(filename) <= SWEPT_THROUGH:
        continue
    sql = without_comments(io.open(os.path.join(MIGRATIONS, filename), encoding="utf-8").read())
    for table in CREATE_TABLE.findall(sql):
        checked += 1
        enabled = re.search(
            r"alter\s+table\s+(?:if\s+exists\s+)?(?:public\.)?%s\s+enable\s+row\s+level\s+security"
            % re.escape(table), sql, re.I)
        check("%s: table %s enables RLS" % (filename, table), bool(enabled), True)
    for view, options in CREATE_VIEW.findall(sql):
        checked += 1
        check("%s: view %s is security_invoker" % (filename, view),
              "security_invoker" in options.lower(), True)

    # A function is callable at /rest/v1/rpc by anyone holding the anon key:
    # Postgres grants EXECUTE to PUBLIC on creation. Trigger functions cannot be
    # called that way and are exempt.
    for match in re.finditer(
            r"create\s+(?:or\s+replace\s+)?function\s+(?:public\.)?([a-z_][a-z0-9_]*)\s*\((.*?)\)"
            r"\s*returns\s+(\w+)", sql, re.I | re.S):
        function, returns = match.group(1), match.group(3).lower()
        if returns == "trigger":
            continue
        checked += 1
        revoked = re.search(
            r"revoke\s+execute\s+on\s+function\s+(?:public\.)?%s\s*\([^)]*\)\s*from\s+[^;]*\banon\b"
            % re.escape(function), sql, re.I)
        check("%s: function %s revokes EXECUTE from anon" % (filename, function), bool(revoked), True)

print("  (%d object(s) created after 040 checked)" % checked)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all migration-RLS checks passed")
