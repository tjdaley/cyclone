"""
tests/test_db_error_handler.py - A constraint violation must not leak a row.

Trying to set an account type the database had not yet been migrated to accept
produced a 500 and a full traceback, and PostgREST puts the OFFENDING ROW in the
error's `details`:

    Failing row contains (1, 1, RiverSource Life Insurance Company,
    life_insurance, 0004, 0909 0823 2663 0 004, GABRIEL M SALMONS, ...)

A client name and a full account number, printed into the application log. §6
forbids that outright, and it happens for a constraint violation on ANY table —
so the worst case is a row of a financial register rather than an account name.

Two things follow, and the first is the reason this exists:

  * the row is never logged and never returned
  * a refused value is a 4xx, not a server fault, and the CONSTRAINT NAME is the
    one fact worth passing on: it names the table and column, it is not client
    data, and it turns "Internal Server Error" into "run migration 038"

Run:  venv/Scripts/python.exe tests/test_db_error_handler.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import asyncio  # noqa: E402
import json  # noqa: E402

from postgrest.exceptions import APIError  # noqa: E402

import main as app_main  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


class _Request:
    """Only `url.path` is read, and only for the log line."""
    class url:
        path = "/api/v1/financial-accounts/1"


# The real payload, as PostgREST returned it.
ROW = ("Failing row contains (1, 1, RiverSource Life Insurance Company, "
       "life_insurance, 0004, 0909 0823 2663 0 004, GABRIEL M SALMONS, 1, "
       "community, Life Insurance, null, f, 2026-08-24 03:45:38.838087+00).")
CHECK_VIOLATION = APIError({
    "message": 'new row for relation "financial_accounts" violates check '
               'constraint "financial_accounts_account_type"',
    "code": "23514",
    "hint": None,
    "details": ROW,
})


def handle(exc):
    return asyncio.get_event_loop().run_until_complete(
        app_main._db_rejection_handler(_Request(), exc))


print("A check violation answers instead of raising")
response = handle(CHECK_VIOLATION)
body = json.loads(response.body.decode())

check("it is a 422, not a 500", response.status_code, 422)
check_true("the constraint is named, so the fix is findable",
           "financial_accounts_account_type" in body["detail"])
check_true("and it says a migration may be missing",
           "migration" in body["detail"].lower())

# THE POINT OF THE WHOLE FILE.
check("the failing row is NOT in the response",
      any(bit in body["detail"]
          for bit in ("GABRIEL", "0909 0823", "Failing row")), False)


print("")
print("Other rejections are answered too, with their own status")
for code, want_status in (("23505", 409), ("23503", 409), ("23502", 422)):
    exc = APIError({"message": "constraint c", "code": code, "details": ROW})
    out = handle(exc)
    check("sqlstate %s -> %d" % (code, want_status), out.status_code, want_status)
    check("and %s leaks no row" % code,
          "GABRIEL" in json.loads(out.body.decode())["detail"], False)


print("")
print("A genuine database failure still looks like one")
# Anything not in the table is re-raised untouched. A connection drop, a
# permissions error or a timeout must NOT be dressed up as a 4xx the caller
# could fix by editing their input.
for code in ("08006", "42501", "57014", ""):
    raised = None
    try:
        handle(APIError({"message": "boom", "code": code, "details": ROW}))
    except APIError:
        raised = True
    check_true("sqlstate %r is re-raised, not swallowed" % code, raised)


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all db-error-handler checks passed")
