"""
tests/test_status_service.py - The status page says what is true, including "I could not tell".

`/api/health` answered "ok" with the database down or the worker dead. The
status service checks each part a user depends on; this drives every state
through fakes and checks the two distinctions that matter most:

* "could not check" (unknown) is never reported as down, and never as fine;
* nothing happening (idle) is never reported as a failure.

And that a HANGING database — the usual shape of a real outage — is reported
within the deadline rather than hanging the status page with it.

Run:  venv/Scripts/python.exe tests/test_status_service.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

import services.status_service as mod  # noqa: E402

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


NOW = datetime.now(timezone.utc)
WORLD: dict = {}


def reset(**over):
    WORLD.clear()
    WORLD.update(oldest=None, storage_ok=True, outcomes=["succeeded"] * 5,
                 workers=[{"running": 0, "capacity": 5}], db_raise=None, db_sleep=0.0,
                 redis_raise=None)
    WORLD.update(over)


class FakeJobs:
    def __init__(self, manager): pass

    def oldest_queued_at(self):
        if WORLD["db_sleep"]:
            time.sleep(WORLD["db_sleep"])
        if WORLD["db_raise"]:
            raise WORLD["db_raise"]
        return WORLD["oldest"]


class FakeStorage:
    def __init__(self, manager): pass

    def ping(self):
        if WORLD["db_raise"]:
            raise WORLD["db_raise"]          # no connection could be built at all
        if not WORLD["storage_ok"]:
            raise ConnectionError("storage")


class FakeCalls:
    def __init__(self, manager): pass

    def outcomes_since(self, since):
        if WORLD["db_raise"]:
            raise WORLD["db_raise"]
        return WORLD["outcomes"]


class FakeRedis:
    @staticmethod
    def live_workers():
        if WORLD["redis_raise"]:
            raise WORLD["redis_raise"]
        return WORLD["workers"]


mod.JobRepository, mod.StorageService, mod.LlmCallRepository = FakeJobs, FakeStorage, FakeCalls
mod.redis_client = FakeRedis


def run(**over):
    reset(**over)
    report = mod.StatusService()._check(lambda: object())
    return report, {c["key"]: c for c in report["components"]}


# ── 1. Everything fine ───────────────────────────────────────────────────
print("1. all well")
report, c = run()
check("overall operational", report["overall"], "operational")
check("every component operational",
      {k: v["state"] for k, v in c.items()},
      {"api": "operational", "database": "operational", "storage": "operational",
       "processor": "operational", "ai": "operational"})
check("database latency measured", isinstance(c["database"].get("latency_ms"), int), True)

# ── 2. The worker ────────────────────────────────────────────────────────
print("2. processing")
report, c = run(workers=[])
check("no heartbeat is DOWN", c["processor"]["state"], "down")
check("and the page is down overall", report["overall"], "down")
check("saying uploads are kept", "kept" in c["processor"]["summary"], True)

report, c = run(redis_raise=ConnectionError("redis"), oldest=None)
check("cannot see workers, nothing waiting: UNKNOWN, not down", c["processor"]["state"], "unknown")
check("overall is unknown, not down", report["overall"], "unknown")

report, c = run(redis_raise=ConnectionError("redis"), oldest=NOW - timedelta(minutes=40))
check("cannot see workers but the queue is stuck: DOWN", c["processor"]["state"], "down")
check("saying how long", "40 minutes" in c["processor"]["summary"], True)

report, c = run(redis_raise=ConnectionError("redis"), oldest=NOW - timedelta(minutes=5))
check("cannot see workers, short wait: still only unknown", c["processor"]["state"], "unknown")

report, c = run(oldest=NOW - timedelta(minutes=25), workers=[{"running": 5, "capacity": 5}])
check("every slot full and work waiting: DEGRADED (busy)", c["processor"]["state"], "degraded")
check("worded as busy", c["processor"]["summary"].startswith("Busy"), True)

report, c = run(oldest=NOW - timedelta(minutes=25), workers=[{"running": 1, "capacity": 5}])
check("free slots yet work waiting: DEGRADED, worded as unexpected",
      (c["processor"]["state"], "longer than expected" in c["processor"]["summary"]),
      ("degraded", True))

# ── 3. AI reading ────────────────────────────────────────────────────────
print("3. AI reading")
report, c = run(outcomes=[])
check("no recent calls is IDLE", c["ai"]["state"], "idle")
check("and idle does not spoil the overall", report["overall"], "operational")

report, c = run(outcomes=["failed"] * 4)
check("every recent attempt failed: DOWN", c["ai"]["state"], "down")

report, c = run(outcomes=["failed"] * 2)
# Too little to call an outage (that needs three), too much to call fine.
check("two failures and no successes: amber, not yet red", c["ai"]["state"], "degraded")

report, c = run(outcomes=["failed", "failed", "failed", "succeeded"])
check("mostly failing over: DEGRADED", c["ai"]["state"], "degraded")

report, c = run(outcomes=["failed", "succeeded", "succeeded"])
check("an occasional failover is normal", c["ai"]["state"], "operational")

# ── 4. Storage ───────────────────────────────────────────────────────────
print("4. storage")
report, c = run(storage_ok=False)
check("storage refusing: DOWN", c["storage"]["state"], "down")

# ── 5. The database ──────────────────────────────────────────────────────
print("5. database")
report, c = run(db_raise=ConnectionError("db"))
check("database unreachable: DOWN", c["database"]["state"], "down")
check("storage reported as could-not-check, not as a second outage", c["storage"]["state"], "unknown")
check("AI reported as could-not-check", c["ai"]["state"], "unknown")
check("overall down", report["overall"], "down")

saved = mod._CHECK_TIMEOUT
mod._CHECK_TIMEOUT = 0.5
start = time.monotonic()
report, c = run(db_sleep=2.0)
took = time.monotonic() - start
mod._CHECK_TIMEOUT = saved
check("a HANGING database is reported as down", c["database"]["state"], "down")
check("within the deadline, not after the hang (%.2fs)" % took, took < 1.5, True)

# ── 6. Public, so coarse ─────────────────────────────────────────────────
print("6. nothing private")
report, c = run(db_raise=RuntimeError("relation jobs: row (Jane Doe, acct 0909...) failed"))
text = repr(report)
check("error text never reaches the response", "Jane" in text or "0909" in text, False)

# ── 7. Cache ─────────────────────────────────────────────────────────────
print("7. cache")
reset()
service = mod.StatusService()
calls = {"n": 0}


def factory():
    calls["n"] += 1
    return object()


service.report(factory)
service.report(factory)
check("a second request within the window does not re-check", calls["n"], 3)

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
