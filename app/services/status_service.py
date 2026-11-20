"""
app/services/status_service.py - What the public status page says, and why it is true.

``GET /api/health`` answers "ok" whenever the API process is running. It checks
nothing else, so it said all was well with the database down or the worker
dead — the second being the outage users feel first, because every upload sits
in the queue and nothing says why. This module looks at each part a user
actually depends on and says, in a sentence a lawyer can act on, what state it
is in.

Rules, each guarding against a status page that misleads:

* **"Could not check" is its own state (``unknown``),** never reported as down
  and never as fine. A blind spot is not an outage, and it is not reassurance.
* **"Nothing happening" is not a failure (``idle``).** No AI calls in the last
  half hour on a quiet Sunday is not an outage, and a page that turned amber
  every weekend would teach everyone to ignore it.
* **Every check has a deadline** (``_CHECK_TIMEOUT``). A down database usually
  shows up as a hang, not an error, and a status page that hangs with it is
  itself down. The queries skip the manager's retry-with-backoff for the same
  reason.
* **Public, so coarse.** States and sentences only: no counts of matters or
  jobs, no vendor names, no error text (which can quote a document). The node
  id and version are already public on ``/api/config``.
* **Cached for ``_CACHE_SECONDS``** per process, because the endpoint is
  unauthenticated and each refresh touches the database, storage and Redis.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from db.repositories.job import JobRepository
from db.repositories.llm_call import LlmCallRepository
from services.storage_service import StorageService
from util import redis_client
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_CACHE_SECONDS = 15
_CHECK_TIMEOUT = 6.0

# A queued job older than this, with a live worker, means work is backing up.
_QUEUE_SLOW = timedelta(minutes=10)
# With NO way to see the worker (Redis unreadable), only a queue this old is
# taken as evidence that nothing is picking work up.
_QUEUE_STUCK = timedelta(minutes=15)
_AI_WINDOW = timedelta(minutes=30)

OPERATIONAL, DEGRADED, DOWN, IDLE, UNKNOWN = "operational", "degraded", "down", "idle", "unknown"
# Worst first. idle counts as healthy; unknown is a warning, not an outage.
_RANK = {DOWN: 3, DEGRADED: 2, UNKNOWN: 1, IDLE: 0, OPERATIONAL: 0}


def _component(key: str, name: str, state: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "name": name, "state": state, "summary": summary, **extra}


def _minutes(delta: timedelta) -> str:
    m = max(1, int(delta.total_seconds() // 60))
    return "about %d minute%s" % (m, "" if m == 1 else "s")


class StatusService:
    """Checks each part of Cyclone a user depends on. Stateless apart from the cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: Optional[dict[str, Any]] = None
        self._cached_at = 0.0
        # A small pool of its own, so a hung check never borrows a thread the
        # request handlers need. Threads of a timed-out check finish (or die
        # with their socket timeout) in the background; nothing waits on them.
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="status")

    def report(self, manager_factory: Callable[[], Any]) -> dict[str, Any]:
        """The current status, at most ``_CACHE_SECONDS`` old."""
        with self._lock:
            if self._cached and time.monotonic() - self._cached_at < _CACHE_SECONDS:
                return self._cached
        result = self._check(manager_factory)
        with self._lock:
            self._cached, self._cached_at = result, time.monotonic()
        return result

    # ── checks ───────────────────────────────────────────────────────────────

    def _check(self, manager_factory: Callable[[], Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        def timed(fn: Callable[[], Any]) -> Callable[[], tuple[Any, int]]:
            def inner() -> tuple[Any, int]:
                start = time.monotonic()
                value = fn()
                return value, int((time.monotonic() - start) * 1000)
            return inner

        # Submitted together, so the page costs the slowest check, not the sum.
        # A MANAGER PER CHECK: SupabaseManager is not thread-safe
        # (dependencies.get_db_manager), and these run on three threads at once.
        futures = {
            "db": self._pool.submit(timed(
                lambda: JobRepository(manager_factory()).oldest_queued_at())),
            "storage": self._pool.submit(lambda: StorageService(manager_factory()).ping()),
            "ai": self._pool.submit(
                lambda: LlmCallRepository(manager_factory()).outcomes_since(now - _AI_WINDOW)),
            "workers": self._pool.submit(redis_client.live_workers),
        }

        results: dict[str, tuple[str, Any]] = {}
        deadline = time.monotonic() + _CHECK_TIMEOUT
        for key, future in futures.items():
            try:
                results[key] = ("ok", future.result(timeout=max(0.1, deadline - time.monotonic())))
            except FutureTimeout:
                results[key] = ("timeout", None)
            except Exception as e:  # noqa: BLE001
                results[key] = ("error", type(e).__name__)

        # With the database unreachable, the storage and AI checks failed for
        # the same reason (they could not even build a connection, or the AI
        # check reads a table there), so they are "could not check", not down.
        db_ok = results["db"][0] == "ok"
        components = [
            # "Website", not "and sign-in": sign-in is Supabase Auth, which nothing
            # here checks. This row only proves the server answered this request.
            _component("api", "Website", OPERATIONAL, "Answering normally."),
            self._database(results["db"]),
            self._storage(db_ok, results["storage"]),
            self._processor(results["workers"], results["db"], now),
            self._ai(db_ok, results["ai"]),
        ]
        for c in components:
            if c["state"] in (DOWN, DEGRADED, UNKNOWN):
                LOGGER.warning("status: %s is %s", c["key"], c["state"])

        overall = max((c["state"] for c in components), key=lambda s: _RANK[s])
        return {
            "overall": overall if overall in (DOWN, DEGRADED, UNKNOWN) else OPERATIONAL,
            "checked_at": now.isoformat(),
            "version": settings.version,
            "server": settings.id,
            "components": components,
        }

    @staticmethod
    def _database(db: tuple[str, Any]) -> dict[str, Any]:
        name = "Case records"
        state, value = db
        if state == "timeout":
            return _component("database", name, DOWN,
                              "The database is not answering. Pages that show case records "
                              "will hang or fail until it recovers.")
        if state == "error":
            return _component("database", name, DOWN,
                              "Cyclone cannot reach its database. Matters, statements and "
                              "billing cannot be opened until it is back.")
        _, ms = value
        if ms > 2000:
            return _component("database", name, DEGRADED,
                              "Answering, but slowly. Pages may take longer than usual to load.",
                              latency_ms=ms)
        return _component("database", name, OPERATIONAL, "Answering normally.", latency_ms=ms)

    @staticmethod
    def _storage(db_ok: bool, storage: tuple[str, Any]) -> dict[str, Any]:
        name = "Document storage"
        if not db_ok and storage[0] != "ok":
            return _component("storage", name, UNKNOWN,
                              "Could not be checked while the database is unreachable.")
        state, _ = storage
        if state == "ok":
            return _component("storage", name, OPERATIONAL,
                              "Reachable. Uploads and source PDFs are available.")
        if state == "timeout":
            return _component("storage", name, DOWN,
                              "Not answering. Uploads will fail and source PDFs will not open.")
        return _component("storage", name, DOWN,
                          "Refusing requests. Uploads will fail and source PDFs will not open.")

    @staticmethod
    def _processor(workers: tuple[str, Any], db: tuple[str, Any],
                   now: datetime) -> dict[str, Any]:
        """
        The worker that reads uploads. Two signals, because neither is enough:
        a heartbeat says a worker is alive, and the age of the oldest queued job
        says whether work is actually moving.
        """
        name = "Statement and document processing"
        oldest: Optional[datetime] = None
        queue_known = db[0] == "ok"
        if queue_known:
            oldest = db[1][0]
        waiting = (now - oldest) if oldest else None

        if workers[0] != "ok":
            # Cannot see the workers. Say so, unless the queue proves the point.
            if waiting is not None and waiting > _QUEUE_STUCK:
                return _component("processor", name, DOWN,
                                  "Uploads have been waiting %s and are not being picked up. "
                                  "They are kept, and will be read once processing resumes."
                                  % _minutes(waiting))
            return _component("processor", name, UNKNOWN,
                              "Cannot confirm the processor is running right now"
                              + ("; nothing is waiting to be read." if queue_known and not waiting
                                 else "."))

        beats = workers[1] or []
        if not beats:
            return _component("processor", name, DOWN,
                              "The processor is not running, so new uploads are not being read. "
                              "Uploads are kept and wait in line until it is back.")
        if waiting is not None and waiting > _QUEUE_SLOW:
            busy = all((b.get("running") or 0) >= (b.get("capacity") or 1) for b in beats)
            return _component("processor", name, DEGRADED,
                              ("Busy: uploads are waiting their turn, the oldest for %s."
                               if busy else
                               "Running, but uploads are waiting longer than expected — the "
                               "oldest for %s.") % _minutes(waiting))
        return _component("processor", name, OPERATIONAL,
                          "Running. New uploads are picked up within seconds.")

    @staticmethod
    def _ai(db_ok: bool, ai: tuple[str, Any]) -> dict[str, Any]:
        """
        Judged from the attempts already made, never by making one: a probe call
        costs money every fifteen seconds, and says less than real work does.
        """
        name = "AI reading"
        if not db_ok or ai[0] != "ok":
            return _component("ai", name, UNKNOWN, "Could not be checked just now.")
        outcomes = ai[1]
        if not outcomes:
            return _component("ai", name, IDLE,
                              "No AI work in the last half hour, so nothing to report.")
        ok = sum(1 for o in outcomes if o == "succeeded")
        failed = len(outcomes) - ok
        if ok == 0 and failed >= 3:
            return _component("ai", name, DOWN,
                              "Recent attempts to read documents have all failed. Imports started "
                              "now are likely to fail; please try again later.")
        if failed > ok:
            return _component("ai", name, DEGRADED,
                              "Some AI providers are failing and Cyclone is switching to backups, "
                              "so reading may be slower than usual.")
        return _component("ai", name, OPERATIONAL, "Working normally.")


status_service = StatusService()
