"""
tests/test_transaction_text_search.py - The text box finds a payee, against real PostgREST.

A check prints only its number, so the payee somebody typed in is the one thing a
person would search for. Searching three columns means an `or` filter, and two
things about that cannot be checked with a fake: whether it survives punctuation
a person types (a comma or a bracket is `or` syntax), and whether it still ANDs
with the other `or` the amount range uses when it searches both directions.

Needs Docker. Without it this exits 2 and prints SKIPPED, because a check that
silently did not run must not read as a pass.

Run:  venv/Scripts/python.exe tests/test_transaction_text_search.py
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from db.repositories.financial import _apply_amount_range, _apply_text  # noqa: E402
from postgrest import SyncPostgrestClient  # noqa: E402
from util import amount_filter  # noqa: E402

DB, API, NET, PORT = "cyclone-text-db", "cyclone-text-api", "cyclone-text-net", 3987
FAILURES: list[str] = []

SCHEMA = r"""
create role web_anon nologin;
create table lines (id serial primary key, description text, counterparty text,
                    location text, amount numeric(14,2));
grant select on lines to web_anon;
grant usage, select on lines_id_seq to web_anon;
insert into lines (description, counterparty, location, amount) values
 ('Check 2072', 'Blue Bee Construction', 'McKinney, TX', -11767.89),
 ('Check 2073', null, null, -50.00),
 ('INSURANCE DEPOSIT', null, 'Plano, TX', 11767.89),
 ('TARGET T-1234', 'Target', 'Allen, TX', -20.00),
 ('Smith, Jones (Trust) "A"', null, null, -75.00),
 ('Half 50% off', null, null, -80.00),
 ('Half 500 off', null, null, -81.00),
 ('Under_score', null, null, -82.00),
 ('UnderXscore', null, null, -83.00),
 ('Back\slash', null, null, -84.00);
"""


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def cleanup():
    docker("rm", "-f", API, DB)
    docker("network", "rm", NET)


if shutil.which("docker") is None or docker("info").returncode != 0:
    print("SKIPPED: Docker is not available, so nothing was checked.")
    sys.exit(2)

cleanup()
try:
    docker("network", "create", NET)
    docker("run", "-d", "--name", DB, "--network", NET, "-e", "POSTGRES_PASSWORD=p", "postgres:16")
    for _ in range(60):
        if docker("exec", DB, "pg_isready", "-U", "postgres", "-h", "127.0.0.1").returncode == 0:
            break
        time.sleep(1)
    loaded = docker("exec", "-i", DB, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", input=SCHEMA)
    assert loaded.returncode == 0, loaded.stderr
    docker("run", "-d", "--name", API, "--network", NET, "-p", "%d:3000" % PORT,
           "-e", "PGRST_DB_URI=postgres://postgres:p@%s:5432/postgres" % DB,
           "-e", "PGRST_DB_SCHEMAS=public", "-e", "PGRST_DB_ANON_ROLE=web_anon",
           "postgrest/postgrest:v12.2.3")
    base = "http://127.0.0.1:%d" % PORT
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/lines?limit=1", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)

    client = SyncPostgrestClient(base)

    def found(text, amount_min=None, amount_max=None):
        query = client.from_("lines").select("id,description")
        query = _apply_text(query, text)
        query = _apply_amount_range(query, amount_filter.resolve(amount_min, amount_max))
        return sorted(r["description"] for r in query.execute().data)

    print("1. it reaches all three columns, and ignores case")
    check("the description", found("insurance"), ["INSURANCE DEPOSIT"])
    check("the payee", found("blue bee"), ["Check 2072"])
    check("the location", found("mckinney"), ["Check 2072"])

    print("2. what a person types cannot break the filter")
    check("a comma", found("McKinney, TX"), ["Check 2072"])
    check("brackets", found("(Trust)"), ['Smith, Jones (Trust) "A"'])
    check("a double quote", found('"A"'), ['Smith, Jones (Trust) "A"'])
    check("a dot", found("T-1234"), ["TARGET T-1234"])
    check("a literal percent", found("50%"), ["Half 50% off"])
    check("a literal underscore", found("Under_score"), ["Under_score"])
    check("a backslash", found("Back\\slash"), ["Back\\slash"])
    check("an asterisk is not a wildcard to anyone's surprise", found("zzz*"), [])

    print("3. it ANDs with the amount range's own or")
    check("the payee plus an either-direction amount", found("blue bee", "11000"), ["Check 2072"])
    check("the payee with an amount it does not have", found("blue bee", "50000"), [])
    check("an amount alone still finds the pair", found(None, "11000", "12000"),
          ["Check 2072", "INSURANCE DEPOSIT"])
    check("a location plus money in only", found("TX", "+100"), ["INSURANCE DEPOSIT"])
finally:
    cleanup()

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
