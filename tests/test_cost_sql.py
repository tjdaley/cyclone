"""
tests/test_cost_sql.py - The pricing arithmetic, against real Postgres.

Migrations here are run by hand, which means SQL that has never executed reaches
production and fails there, in front of somebody. The cost figures are SQL -- a
view, an exclusion constraint, two functions -- so this starts a throwaway
Postgres 16 in Docker, applies 039 through 043 in order on top of the few
Supabase objects they assume, and checks what comes out.

Needs Docker. Without it this prints SKIPPED and exits 2 -- deliberately not 0,
because a check that silently did not run is indistinguishable from one that
passed.

Run:  venv/Scripts/python.exe tests/test_cost_sql.py
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

# What a Supabase database already has that 039-041 rely on.
PRELUDE = """
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create table staff (id integer primary key, email text, supabase_uid text);
create table matters (id integer primary key, matter_name text);
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end; $$;
insert into staff values (7, 'paralegal@firm.test', 'uid-para'), (8, 'attorney@firm.test', 'uid-atty');
insert into matters values (311, 'Salmons v. Salmons'), (6, 'Doe v. Doe');
-- A view like 004's: owner-privileged and readable by anon until 040 runs.
create view v_matter_summary as select id, matter_name from matters;
grant select on v_matter_summary to anon;
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
    print("The pricing SQL was NOT tested. Exit code 2 so this cannot read as a pass.")
    sys.exit(2)


if shutil.which("docker") is None:
    skip("docker is not on PATH")
if docker("info", check_rc=False).returncode != 0:
    skip("the Docker daemon is not reachable")

name = "cyclone-costsql-%s" % uuid.uuid4().hex[:8]


def psql(sql, stop_on_error=True):
    args = ["exec", "-i", name, "psql", "-U", "postgres", "-q", "-tA", "-F", "|"]
    if stop_on_error:
        args += ["-v", "ON_ERROR_STOP=1"]
    return docker(*args, stdin=sql, check_rc=False)


def rows(sql):
    result = psql(sql)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip())
    return [line.split("|") for line in result.stdout.strip().splitlines() if line]


try:
    docker("run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=t", IMAGE)
    for _ in range(60):
        if docker("exec", name, "pg_isready", "-U", "postgres", check_rc=False).returncode == 0:
            break
        time.sleep(1)
    time.sleep(1)  # pg_isready answers a moment before the init restart completes

    print("The migrations apply, in order, on Postgres 16")
    sql = PRELUDE
    for filename in ("039_cost_tracking.sql", "040_enable_rls.sql", "041_service_rates.sql"):
        with open(os.path.join(MIGRATIONS, filename), encoding="utf-8") as fh:
            sql += "\n" + fh.read()
    applied = psql(sql)
    check("039 + 040 + 041 apply without error", applied.returncode, 0)
    if applied.returncode != 0:
        print(applied.stderr[-1500:])
        raise SystemExit(1)
    check("and 041 can be run a second time",
          psql(open(os.path.join(MIGRATIONS, "041_service_rates.sql"), encoding="utf-8").read()).returncode, 0)
    migration_042 = open(os.path.join(MIGRATIONS, "042_served_model.sql"), encoding="utf-8").read()
    check("042 applies on top, and can be run a second time",
          [psql(migration_042).returncode for _ in range(2)], [0, 0])
    migration_043 = open(os.path.join(MIGRATIONS, "043_cost_report_by_firm.sql"), encoding="utf-8").read()
    check("043 applies on top, and can be run a second time",
          [psql(migration_043).returncode for _ in range(2)], [0, 0])
    migration_046 = open(os.path.join(MIGRATIONS, "046_stop_reason.sql"), encoding="utf-8").read()
    check("046 applies on top, and can be run a second time",
          [psql(migration_046).returncode for _ in range(2)], [0, 0])
    check("and llm_calls carries both stop-reason columns",
          rows("select column_name from information_schema.columns "
               "where table_name = 'llm_calls' and column_name like 'stop_reason%' "
               "order by column_name"), [["stop_reason"], ["stop_reason_class"]])
    migration_049 = open(os.path.join(MIGRATIONS, "049_thinking_measurement.sql"),
                         encoding="utf-8").read()
    check("049 applies on top, and can be run a second time",
          [psql(migration_049).returncode for _ in range(2)], [0, 0])
    check("and llm_calls carries both thinking-measurement columns",
          rows("select column_name, data_type from information_schema.columns "
               "where table_name = 'llm_calls' "
               "and column_name in ('thinking_chars', 'text_chars') "
               "order by column_name"),
          [["text_chars", "integer"], ["thinking_chars", "integer"]])
    # The partial index is the whole reason to have an index here: once a
    # non-thinking model serves most calls, the rows worth finding are the few
    # that thought.
    check("the thinking index is partial",
          rows("select indexdef like '%%WHERE (thinking_chars > 0)' from pg_indexes "
               "where indexname = 'idx_llm_calls_thinking'"), [["t"]])
    # NULL and 0 must stay distinguishable: a NULL is a call nothing measured,
    # a 0 is a reply that genuinely carried no thinking. Reading the first as
    # the second reports a fleet-wide absence of thinking that is an absence of
    # measurement.
    check("the columns are nullable, so 'not measured' survives",
          rows("select column_name, is_nullable from information_schema.columns "
               "where table_name = 'llm_calls' "
               "and column_name in ('thinking_chars', 'text_chars') "
               "order by column_name"),
          [["text_chars", "YES"], ["thinking_chars", "YES"]])
    # 046 touches no view, so unlike 042 it creates no ordering hazard with 041
    # -- which is checked immediately below and would fail if it had.
    check("and the priced view still prices on the served model",
          rows("select count(*) from information_schema.columns "
               "where table_name = 'llm_call_costs' and column_name = 'billed_model'"), [["1"]])
    # Deliberate: 041's CREATE OR REPLACE cannot reshape 042's views, and if it
    # could it would silently put back pricing on the REQUESTED model. It runs
    # in a transaction, so the refusal changes nothing.
    rerun_041 = psql(open(os.path.join(MIGRATIONS, "041_service_rates.sql"), encoding="utf-8").read())
    check("re-running 041 after 042 is refused rather than reverting the pricing",
          rerun_041.returncode != 0, True)
    check("and leaves 042's view in place",
          rows("select count(*) from information_schema.columns "
               "where table_name = 'llm_call_costs' and column_name = 'billed_model'"), [["1"]])

    print("")
    print("040 closed the owner-privileged view and turned on RLS")
    check("anon can no longer read v_matter_summary",
          rows("select has_table_privilege('anon', 'v_matter_summary', 'SELECT')"), [["f"]])
    check("every table has RLS",
          rows("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace "
               "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"), [["0"]])

    psql("""
    insert into llm_calls (occurred_at, staff_id, matter_id, auth_uid, source_type, source_id, profile,
                           call_type, vendor, model, chain_position, outcome,
                           input_uncached_tokens, input_cache_read_tokens, output_tokens)
    values
     ('2026-09-10 12:00+00', 7, 311, null, 'job', 'job-1', 'extract_account_statement',
      'text', 'anthropic', 'claude-opus-5', 1, 'succeeded', 1000000, 2000000, 100000),
     ('2026-10-05 12:00+00', 7, 311, null, 'job', 'job-2', 'extract_account_statement',
      'text', 'anthropic', 'claude-opus-5', 1, 'succeeded', 1000000, null, 100000),
     ('2026-09-17 03:34+00', null, 6, 'uid-atty', 'request', 'POST /api/v1/billing/parse',
      'parse_billing_entry', 'text', 'gemini', 'flash', 1, 'succeeded', 241, null, 68),
     ('2026-09-11 12:00+00', 7, 311, null, 'job', 'job-1', 'extract_account_statement',
      'text', 'anthropic', 'claude-opus-5', 1, 'failed', null, null, null);
    select set_service_rate('anthropic','claude-opus-5','input_uncached',   15, 1000000, '2026-09-01+00', null, null, 8);
    select set_service_rate('anthropic','claude-opus-5','input_cache_read', 1.5, 1000000, '2026-09-01+00', null, null, 8);
    select set_service_rate('anthropic','claude-opus-5','output',           75, 1000000, '2026-09-01+00', null, null, 8);
    select set_service_rate('anthropic','claude-opus-5','input_uncached',   10, 1000000, '2026-10-01+00', null, 'price cut', 8);
    """)

    print("")
    print("A new price closes the old one, atomically")
    check("September's input price now ends where October's begins",
          rows("select price, effective_from::date, coalesce(effective_to::date::text, 'open') "
               "from service_rates where meter = 'input_uncached' order by effective_from"),
          [["15.000000", "2026-09-01", "2026-10-01"], ["10.000000", "2026-10-01", "open"]])

    backdated = psql("select set_service_rate('anthropic','claude-opus-5','input_uncached', 99, 1000000,"
                     " '2026-08-01+00', null, null, 8)")
    check("a price dated before a later one is refused", backdated.returncode != 0, True)
    check("with SQLSTATE 23P01, which the API answers as a 409",
          "23P01" in psql("\\set VERBOSITY verbose\nselect set_service_rate('anthropic','claude-opus-5',"
                          "'input_uncached', 99, 1000000, '2026-08-01+00', null, null, 8)").stderr, True)
    check("and a sentence saying what to do",
          "Correct that row, or choose a date after it" in backdated.stderr, True)

    overlap = psql("insert into service_rates (provider, model, meter, price, effective_from) "
                   "values ('anthropic','claude-opus-5','output', 1, '2026-09-15+00')")
    check("a hand-inserted overlapping price is refused by the constraint",
          "service_rates_one_price_at_a_time" in overlap.stderr, True)

    print("")
    print("Each call is priced at the rate in force on ITS date")
    priced = {r[0] + "@" + r[1]: (r[2], r[3]) for r in rows(
        "select source_id, occurred_at::date, pricing_status, coalesce(round(total_cost, 6)::text, 'null') "
        "from llm_call_costs")}
    # 1M x $15 + 2M x $1.50 + 0.1M x $75, per million
    check("September call: 15 + 3 + 7.50", priced["job-1@2026-09-10"], ("priced", "25.500000"))
    # 1M x $10 (the October price) + 0.1M x $75
    check("October call uses the October price: 10 + 7.50", priced["job-2@2026-10-05"], ("priced", "17.500000"))
    check("a model with no price is UNPRICED and costs null, not zero",
          priced["POST /api/v1/billing/parse@2026-09-17"], ("unpriced", "null"))
    check("a call with no usage reported is NO_USAGE and null",
          priced["job-1@2026-09-11"], ("no_usage", "null"))

    print("")
    print("The to-do list names exactly what is missing")
    check("the two gemini meters that carried tokens, and nothing else",
          rows("select vendor, model, meter, calls, tokens from llm_unpriced_meters order by meter"),
          [["gemini", "flash", "input_uncached", "1", "241"], ["gemini", "flash", "output", "1", "68"]])

    print("")
    print("The report groups by month and dimension")
    check("by vendor/model, newest month first, counting what is not priced",
          rows("select month, group_label, calls, failed_calls, no_usage_calls, unpriced_calls, priced_cost "
               "from llm_cost_report('2026-09-01+00', '2026-11-01+00', 'vendor_model')"),
          [["2026-10-01", "anthropic / claude-opus-5", "1", "0", "0", "0", "17.50"],
           ["2026-09-01", "anthropic / claude-opus-5", "2", "1", "1", "0", "25.50"],
           ["2026-09-01", "gemini / flash", "1", "0", "0", "1", "0.00"]])
    check("by staff: a job's staff_id AND a request's auth_uid both resolve",
          sorted(tuple(r) for r in rows(
              "select month, group_label, calls from llm_cost_report("
              "'2026-09-01+00', '2026-11-01+00', 'staff')")),
          sorted([("2026-10-01", "paralegal@firm.test", "1"),
                  ("2026-09-01", "paralegal@firm.test", "2"),
                  ("2026-09-01", "attorney@firm.test", "1")]))
    check("by matter, labelled with the matter name",
          sorted(r[1] for r in rows("select month, group_label from llm_cost_report("
                                    "'2026-09-01+00', '2026-11-01+00', 'matter')")),
          ["Doe v. Doe", "Salmons v. Salmons", "Salmons v. Salmons"])
    check("an unknown grouping is refused, not interpolated",
          psql("select * from llm_cost_report('2026-09-01+00','2026-11-01+00','x; drop table llm_calls')")
          .returncode != 0, True)
    check("and llm_calls is still there", rows("select count(*) from llm_calls"), [["4"]])

    print("")
    print("042: a call is priced on the model that ANSWERED, not the one requested")
    psql("""
    insert into llm_calls (occurred_at, source_type, source_id, profile, call_type, vendor, model,
                           served_model, vendor_response_id, chain_position, outcome,
                           input_uncached_tokens, output_tokens)
    values ('2026-12-03 12:00+00', 'job', 'job-9', 'extract_account_statement', 'text',
            'openai', 'gpt-5', 'gpt-5-2025-08-07', 'chatcmpl-1', 1, 'succeeded', 1000000, 100000);
    select set_service_rate('openai','gpt-5','input_uncached', 1.25, 1000000, '2026-12-01+00', null, null, 8);
    select set_service_rate('openai','gpt-5','output',          10, 1000000, '2026-12-01+00', null, null, 8);
    """)
    check("a price entered under the REQUESTED name does not price it",
          rows("select billed_model, billed_model_source, pricing_status from llm_call_costs "
               "where source_id = 'job-9'"), [["gpt-5-2025-08-07", "served", "unpriced"]])
    check("the to-do list names the served model, and what was asked for",
          rows("select model, meter, array_to_string(requested_models, ',') from llm_unpriced_meters "
               "where vendor = 'openai' order by meter"),
          [["gpt-5-2025-08-07", "input_uncached", "gpt-5"], ["gpt-5-2025-08-07", "output", "gpt-5"]])
    psql("""
    select set_service_rate('openai','gpt-5-2025-08-07','input_uncached', 1.25, 1000000, '2026-12-01+00', null, null, 8);
    select set_service_rate('openai','gpt-5-2025-08-07','output',          10, 1000000, '2026-12-01+00', null, null, 8);
    """)
    check("priced once the served model has a price: 1.25 + 1.00",
          rows("select pricing_status, round(total_cost, 6) from llm_call_costs where source_id = 'job-9'"),
          [["priced", "2.250000"]])
    check("a row with no served model is billed on the requested one, and says so",
          rows("select distinct billed_model, billed_model_source from llm_call_costs "
               "where vendor = 'anthropic'"), [["claude-opus-5", "requested"]])
    check("the report groups on the model that answered",
          rows("select group_label, priced_cost from llm_cost_report("
               "'2026-12-01+00', '2027-01-01+00', 'vendor_model')"),
          [["openai / gpt-5-2025-08-07", "2.25"]])
    check("the vendor's response id is kept for a billing dispute",
          rows("select vendor_response_id from llm_call_costs where source_id = 'job-9'"), [["chatcmpl-1"]])

    print("")
    print("043: spend by firm, which is what the other groupings mean once there are two")
    psql("""
    insert into firms (id, name) values (2, 'Second firm') on conflict (id) do nothing;
    insert into llm_calls (occurred_at, firm_id, source_type, source_id, profile, call_type,
                           vendor, model, served_model, chain_position, outcome,
                           input_uncached_tokens, output_tokens)
    values ('2026-12-04 12:00+00', 2, 'job', 'job-10', 'extract_account_statement', 'text',
            'openai', 'gpt-5', 'gpt-5-2025-08-07', 1, 'succeeded', 2000000, 200000);
    """)
    check("each firm's spend on its own row, labelled with the name it has now",
          rows("select group_key, group_label, calls, priced_cost from llm_cost_report("
               "'2026-12-01+00', '2027-01-01+00', 'firm')"),
          [["2", "Second firm", "1", "4.50"], ["1", "Default firm", "1", "2.25"]])
    psql("update firms set name = 'Daley Law' where id = 1;")
    check("a renamed firm reports its new name, because the key is the id",
          rows("select group_label from llm_cost_report("
               "'2026-12-01+00', '2027-01-01+00', 'firm') where group_key = '1'"),
          [["Daley Law"]])
    check("a call made before 039's firm column existed still lands on the one firm",
          rows("select group_label, calls from llm_cost_report("
               "'2026-09-01+00', '2026-10-01+00', 'firm')"), [["Daley Law", "3"]])
    check("an unknown grouping is still refused after 043",
          psql("select * from llm_cost_report('2026-09-01+00','2026-11-01+00','firms')")
          .returncode != 0, True)

    print("")
    print("Nothing here is reachable with the anon key")
    check("RLS is on for service_rates",
          rows("select relrowsecurity from pg_class where relname = 'service_rates'"), [["t"]])
    check("both views run as the caller",
          rows("select relname, array_to_string(reloptions, ',') from pg_class "
               "where relname in ('llm_call_costs', 'llm_unpriced_meters') order by 1"),
          [["llm_call_costs", "security_invoker=on"], ["llm_unpriced_meters", "security_invoker=on"]])
    check("anon cannot run either function",
          rows("select has_function_privilege('anon', 'llm_cost_report(timestamptz,timestamptz,text)', 'EXECUTE'),"
               " has_function_privilege('anon', 'set_service_rate(text,text,text,numeric,bigint,"
               "timestamptz,text,text,integer)', 'EXECUTE')"), [["f", "f"]])
    check("the service role can",
          rows("select has_function_privilege('service_role', "
               "'llm_cost_report(timestamptz,timestamptz,text)', 'EXECUTE')"), [["t"]])
finally:
    docker("rm", "-f", name, check_rc=False)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all cost-SQL checks passed")
