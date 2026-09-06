"""
tests/test_job_concurrency.py - Several statements read at once.

The worker used to run each job to completion on its main loop. One
thirteen-month upload was still going at 1,600 seconds, and while it ran nothing
else did: no other job started, and the CRM tick did not run either, so a long
ingest was indistinguishable from a dead worker.

Two things had to change together, and neither works alone. The worker claims
only what it has capacity for and hands it to a pool; the page uploads the whole
stack before waiting on any of it. Without the second, the pool has exactly one
job to run however many files were dropped.

Run:  venv/Scripts/python.exe tests/test_job_concurrency.py
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db.models.job import JobKind, JobStatus  # noqa: E402
import services.job_service as mod  # noqa: E402
from services.job_service import job_service  # noqa: E402

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


class FakeJob:
    def __init__(self, id, kind=JobKind.statement_ingest):
        self.id = id
        self.kind = kind
        self.status = JobStatus.queued
        self.attempts = 0
        self.matter_id = 1
        self.requested_by_staff_id = 1
        self.storage_path = "intake/%s.pdf" % id
        self.params = {}


class FakeJobRepo:
    """The queue, with the same claim semantics the real one has."""

    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.updates = []
        self.lock = threading.Lock()

    def next_queued(self, kind, limit=5):
        with self.lock:
            return [j for j in self.jobs
                    if j.kind == kind and j.status == JobStatus.queued][:limit]

    def update(self, job_id, data):
        with self.lock:
            self.updates.append((job_id, data))
            for job in self.jobs:
                if job.id == job_id and data.get("status") == JobStatus.running.value:
                    job.status = JobStatus.running
        return None


def patch(repo, handler):
    """Point job_service at a fake queue and a fake handler."""
    mod.JobRepository = lambda m: repo
    job_service._run_statement_ingest = handler
    job_service._run_matter_intake = handler
    # Redis is not running in a test; the real _claim already degrades to the
    # status transition when it is unreachable, which is what happens here.


print("Claiming takes only what there is room to run")

repo = FakeJobRepo([FakeJob("job-%d" % n) for n in range(10)])
patch(repo, lambda m, r, j: None)

claimed = job_service.claim_pending(object(), limit=5)
check("five claimed, not ten", len(claimed), 5)
check("all marked running",
      [j.status for j in repo.jobs[:5]], [JobStatus.running] * 5)
check("the rest left queued for a node with room",
      [j.status for j in repo.jobs[5:]], [JobStatus.queued] * 5)

# A full pool asks for nothing. Claiming into a backlog of its own would hold
# jobs hostage on this node while another sat idle.
check("a full pool claims nothing", job_service.claim_pending(object(), limit=0), [])
check("and a negative one does not go looking either",
      job_service.claim_pending(object(), limit=-1), [])


print("\nWork actually overlaps")

# Each job sleeps; serial execution would take five times as long as one.
DURATION = 0.30
started: list[float] = []
peak = 0
running = 0
guard = threading.Lock()


def slow(manager, repo_, job):
    global running, peak
    with guard:
        running += 1
        peak = max(peak, running)
        started.append(time.monotonic())
    time.sleep(DURATION)
    with guard:
        running -= 1


repo = FakeJobRepo([FakeJob("job-%d" % n) for n in range(5)])
patch(repo, slow)

begin = time.monotonic()
with ThreadPoolExecutor(max_workers=5) as pool:
    jobs = job_service.claim_pending(object(), limit=5)
    futures = [pool.submit(job_service.run_claimed, object(), j) for j in jobs]
    for future in futures:
        future.result()
elapsed = time.monotonic() - begin

check("all five ran", len(started), 5)
check("five at once, not one after another", peak, 5)
# Serial would be 5 × DURATION. Allow generous slack for a slow machine while
# still failing outright if the pool silently serialized.
check_true("finished in about one job's time, not five", elapsed < DURATION * 3)


print("\nOne failing job does not take the others down")

survivors: list[str] = []


def one_bad(manager, repo_, job):
    if job.id == "job-2":
        raise RuntimeError("this statement is unreadable")
    survivors.append(job.id)


repo = FakeJobRepo([FakeJob("job-%d" % n) for n in range(5)])
patch(repo, one_bad)

jobs = job_service.claim_pending(object(), limit=5)
failed = 0
with ThreadPoolExecutor(max_workers=5) as pool:
    for future in [pool.submit(job_service.run_claimed, object(), j) for j in jobs]:
        try:
            future.result()
        except RuntimeError:
            failed += 1

check("the bad one raised", failed, 1)
check("the other four finished", sorted(survivors),
      ["job-0", "job-1", "job-3", "job-4"])


print("\nThe serial path still works for callers with nowhere to run threads")

repo = FakeJobRepo([FakeJob("job-%d" % n) for n in range(3)])
ran: list[str] = []
patch(repo, lambda m, r, j: ran.append(j.id))
check("run_pending claims and runs", job_service.run_pending(object(), limit=3), 3)
check("in order", ran, ["job-0", "job-1", "job-2"])


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all job-concurrency checks passed")
