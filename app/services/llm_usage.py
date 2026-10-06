"""
app/services/llm_usage.py - What each model call used, and who it was for.

Called from ``llm_service`` only. Two jobs:

1. **Normalize** a vendor's usage report into one shape (``normalize``).
2. **Record** every attempt to the ``llm_calls`` ledger (``record_attempt``),
   in a way that can never slow down or break the call being recorded.

VENDORS DISAGREE ABOUT WHAT "INPUT TOKENS" MEANS
────────────────────────────────────────────────────────────────────────────
Summed naively, the same prompt costs different amounts depending on who
answered it. The traps:

  * **Anthropic's ``input_tokens`` EXCLUDES cached tokens**, which it reports
    separately as ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
  * **OpenAI's ``prompt_tokens`` INCLUDES them**; the cached share is
    ``prompt_tokens_details.cached_tokens``. Subtract, or a cache hit is billed
    twice. Groq speaks the same dialect.
  * **Gemini's ``prompt_token_count`` INCLUDES cached content** too
    (``cached_content_token_count``), and its **thinking tokens sit OUTSIDE
    ``candidates_token_count``** in ``thoughts_token_count`` while being billed
    at the output rate. Leave them out and a thinking model's output is
    understated by exactly the expensive part.
  * **DeepSeek** reports ``prompt_cache_hit_tokens`` and
    ``prompt_cache_miss_tokens`` directly, which is the cleanest of the lot.

So every row carries five reconciled figures: input billed at full rate, cache
reads, cache writes, output (reasoning included), and the reasoning share. The
vendor's own object is stored beside them verbatim (``usage_raw``), which is the
defence against this table itself being wrong: **field names here were written
against the SDKs as documented, and vendor SDKs drift** (CLAUDE.md §11). If a
mapping turns out wrong, the normalized columns can be recomputed from the raw.

NONE IS NOT ZERO
────────────────────────────────────────────────────────────────────────────
A figure the vendor did not report is None, never 0. A call that timed out
before any response has no usage at all, and a report summing that as zero
would understate spend while looking complete. The one deliberate exception is
subtraction: an OpenAI response with no cache breakdown is taken as nothing
cached, so its whole prompt counts at the full rate -- the direction that
overstates cost rather than hides it.

RECORDING NEVER FAILS THE WORK
────────────────────────────────────────────────────────────────────────────
The same rule as ``AuditLogger``, with a harder edge. Rows go onto a bounded
queue drained by one background thread, because ``SupabaseManager`` retries a
failed insert three times with backoff: a database hiccup would otherwise add
seconds to every LLM call, and a missing table (migration 039 not yet run) would
add them to every call forever. When the table is missing the writer says so
ONCE at ERROR, naming the migration, and stops trying for the life of the
process.

**Known loss:** rows still queued when a process exits are flushed for up to
five seconds and then abandoned. Accepted -- the alternative is a shutdown that
can hang on a database that is down.

THE MODEL WE ASKED FOR IS NOT EVIDENCE OF THE MODEL THAT ANSWERED
────────────────────────────────────────────────────────────────────────────
``model`` is the catalog's name for what was requested. A vendor can serve an
alias or a "preview" name from whatever version it currently points at, and it
says which in the response -- ``model`` on Anthropic and OpenAI-compatible
replies (OpenAI resolves an alias to a dated snapshot there), ``model_version``
on Gemini. That is ``served_model``, and it is what a call is PRICED on (042):
a price entered for the requested name would be charged against a model that
may never have run. The vendor's own id for the response is kept beside it
(``vendor_response_id``), because it is the one reference a vendor's billing
support can look up.

Read separately from usage, so an SDK that moved its usage object still reports
who answered, and the other way round.
"""
import atexit
import json
import queue
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from util import usage_scope
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

#: The usage fields every row carries, in the order a report reads them.
USAGE_FIELDS = (
    "input_uncached_tokens",
    "input_cache_read_tokens",
    "input_cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
)


# ── Normalization ────────────────────────────────────────────────────────────

def _num(obj: Any, *path: str) -> Optional[int]:
    """A non-negative int at ``path``, reading attributes or dict keys alike."""
    for key in path:
        if obj is None:
            return None
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    if obj is None or isinstance(obj, bool):
        return None
    try:
        return max(0, int(obj))
    except (TypeError, ValueError):
        return None


def _minus(total: Optional[int], part: Optional[int]) -> Optional[int]:
    """``total - part``, treating an unreported part as nothing to subtract."""
    if total is None:
        return None
    return max(0, total - (part or 0))


def _plus(a: Optional[int], b: Optional[int]) -> Optional[int]:
    """``a + b``, None only when neither was reported."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def normalize(vendor: str, usage: Any) -> dict[str, Optional[int]]:
    """
    One vendor's usage report, reconciled to the five ledger figures.

    :param vendor: ``anthropic``, ``gemini``, ``openai``, ``groq``, ``deepseek``.
        Anything else is read as OpenAI-compatible, which every other vendor
        this service could plausibly add speaks.
    :param usage: The vendor's usage object or an equivalent dict; None when the
        call produced no response.
    :return: ``USAGE_FIELDS`` mapped to counts, each None where not reported.
    :rtype: dict[str, Optional[int]]
    """
    empty = {field: None for field in USAGE_FIELDS}
    if usage is None:
        return empty

    if vendor == "anthropic":
        # input_tokens already EXCLUDES the cache; output_tokens INCLUDES
        # extended thinking, which is not broken out.
        return {
            "input_uncached_tokens": _num(usage, "input_tokens"),
            "input_cache_read_tokens": _num(usage, "cache_read_input_tokens"),
            "input_cache_write_tokens": _num(usage, "cache_creation_input_tokens"),
            "output_tokens": _num(usage, "output_tokens"),
            "reasoning_tokens": None,
        }

    if vendor == "gemini":
        prompt = _num(usage, "prompt_token_count")
        cached = _num(usage, "cached_content_token_count")
        thoughts = _num(usage, "thoughts_token_count")
        return {
            "input_uncached_tokens": _minus(prompt, cached),
            "input_cache_read_tokens": cached,
            "input_cache_write_tokens": None,
            # Thinking is billed as output but reported beside it, not in it.
            "output_tokens": _plus(_num(usage, "candidates_token_count"), thoughts),
            "reasoning_tokens": thoughts,
        }

    if vendor == "deepseek":
        prompt = _num(usage, "prompt_tokens")
        hit = _num(usage, "prompt_cache_hit_tokens")
        miss = _num(usage, "prompt_cache_miss_tokens")
        cached = hit if hit is not None else _num(usage, "prompt_tokens_details", "cached_tokens")
        return {
            "input_uncached_tokens": miss if miss is not None else _minus(prompt, cached),
            "input_cache_read_tokens": cached,
            "input_cache_write_tokens": None,
            "output_tokens": _num(usage, "completion_tokens"),
            "reasoning_tokens": _num(usage, "completion_tokens_details", "reasoning_tokens"),
        }

    # OpenAI, Groq, and anything else OpenAI-compatible. prompt_tokens INCLUDES
    # the cached share; completion_tokens INCLUDES reasoning.
    prompt = _num(usage, "prompt_tokens")
    cached = _num(usage, "prompt_tokens_details", "cached_tokens")
    return {
        "input_uncached_tokens": _minus(prompt, cached),
        "input_cache_read_tokens": cached,
        "input_cache_write_tokens": None,
        "output_tokens": _num(usage, "completion_tokens"),
        "reasoning_tokens": _num(usage, "completion_tokens_details", "reasoning_tokens"),
    }


def _plain(obj: Any) -> Optional[dict[str, Any]]:
    """A vendor object as a JSON-safe dict, or None when it cannot be made one."""
    if obj is None:
        return None
    try:
        if isinstance(obj, dict):
            data = obj
        elif hasattr(obj, "model_dump"):
            data = obj.model_dump(exclude_none=True)
        elif hasattr(obj, "to_dict"):
            data = obj.to_dict()
        else:
            data = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        return json.loads(json.dumps(data, default=str))
    except Exception:  # noqa: BLE001 -- the raw copy is a convenience, never a failure
        return None


def usage_of(vendor: str, response: Any) -> Any:
    """Where each SDK keeps its usage report on a response."""
    if response is None:
        return None
    if vendor == "gemini":
        return getattr(response, "usage_metadata", None)
    return getattr(response, "usage", None)


#: Longer than any real model name or response id; a cap on a value we store verbatim.
_MAX_IDENTITY_CHARS = 200


def _identity(value: Any) -> Optional[str]:
    """A vendor-reported name or id, verbatim but trimmed; None unless a non-empty string."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:_MAX_IDENTITY_CHARS] or None


def served_of(vendor: str, response: Any) -> tuple[Optional[str], Optional[str]]:
    """
    Which model the vendor says served a response, and its id for the response.

    :return: ``(served_model, vendor_response_id)``, each None where not reported.
    """
    if response is None:
        return None, None
    if vendor == "gemini":
        return (_identity(getattr(response, "model_version", None)),
                _identity(getattr(response, "response_id", None)))
    # Anthropic and every OpenAI-compatible SDK.
    return (_identity(getattr(response, "model", None)),
            _identity(getattr(response, "id", None)))


# ── The side channel from a vendor call back to the failover loop ────────────
#
# The `_call_*` functions return text, and every caller and test depends on that
# shape. Rather than change eight signatures and the loop that reads them, each
# one NOTES its response here the moment the vendor returns -- before parsing,
# so a reply that then turns out empty or unparseable still carries its cost --
# and the loop reads it back when it records the attempt. Same thread, same
# context, so a plain ContextVar set is visible to the caller on return.

def _nothing_noted() -> dict[str, Any]:
    return {
        "counts": {field: None for field in USAGE_FIELDS},
        "raw": None,
        "served_model": None,
        "vendor_response_id": None,
    }


_NOTED: ContextVar[Optional[dict[str, Any]]] = ContextVar("llm_usage_noted", default=None)


def begin_attempt() -> None:
    """Forget the previous attempt's usage, so a failed call cannot inherit it."""
    _NOTED.set(None)


def note_response(vendor: str, response: Any) -> None:
    """
    Remember what a vendor response used. Called inside each ``_call_*``.

    **Must never raise.** It runs inside the vendor call, so an exception here
    would fail a call that succeeded and hand the work to the next vendor in the
    chain -- the exact disguise that hid two SDK bugs for a session (§11).
    """
    noted = _nothing_noted()
    try:
        noted["served_model"], noted["vendor_response_id"] = served_of(vendor, response)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("llm_usage: could not read the served model from a %s response (%s)",
                       vendor, type(e).__name__)
    try:
        usage = usage_of(vendor, response)
        noted["counts"], noted["raw"] = normalize(vendor, usage), _plain(usage)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("llm_usage: could not read usage from a %s response (%s)",
                       vendor, type(e).__name__)
    _NOTED.set(noted)


# ── Recording ────────────────────────────────────────────────────────────────

_COLUMNS_042 = ("served_model", "vendor_response_id")


def _drop_042_columns(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _COLUMNS_042}


def _is_missing_042_column(error: BaseException) -> bool:
    """PostgREST refusing a row for a column 042 adds (PGRST204)."""
    text = str(error)
    return "PGRST204" in text and any(column in text for column in _COLUMNS_042)


class _DatabaseWriter:
    """One background thread draining a bounded queue into ``llm_calls``."""

    _CAPACITY = 10_000
    _FLUSH_ON_EXIT_SECONDS = 5.0

    def __init__(self) -> None:
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=self._CAPACITY)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._disabled = False
        self._dropped = 0
        self._failures = 0
        # Set when the table predates 042. Rows are then written without the
        # served-model columns rather than not written at all: losing who
        # answered is bad, losing the tokens as well would be worse.
        self._without_042 = False

    def submit(self, call: Any) -> None:
        if self._disabled:
            return
        self._start()
        try:
            self._queue.put_nowait(call)
        except queue.Full:
            self._dropped += 1
            # A full queue means the database has been unreachable for a long
            # time. Say so rarely -- every LLM call would otherwise log.
            if self._dropped in (1, 100) or self._dropped % 1000 == 0:
                LOGGER.warning("llm_usage: ledger queue full; %d cost record(s) dropped",
                               self._dropped)

    def _start(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._drain, name="llm-usage-writer", daemon=True)
                self._thread.start()
                atexit.register(self.flush)

    def _drain(self) -> None:
        repo = None
        while True:
            call = self._queue.get()
            try:
                if self._disabled:
                    continue
                if repo is None:
                    # Imported here: llm_service must stay importable without a
                    # database, and only the writer thread ever needs one.
                    from db_handler import SupabaseManager  # noqa: PLC0415
                    from db.repositories.llm_call import LlmCallRepository  # noqa: PLC0415
                    repo = LlmCallRepository(SupabaseManager())
                # exclude_none: every nullable column defaults to NULL anyway,
                # and firm_id must be OMITTED rather than sent as null so the
                # column's default (the one firm) applies.
                row = call.model_dump(exclude_none=True)
                if self._without_042:
                    row = _drop_042_columns(row)
                try:
                    repo.insert(row)
                except Exception as e:  # noqa: BLE001
                    if self._without_042 or not _is_missing_042_column(e):
                        raise
                    self._without_042 = True
                    LOGGER.error(
                        "llm_usage: llm_calls has no served_model column. Run "
                        "db/migrations/042_served_model.sql, then restart api and worker. "
                        "Until this process restarts, usage is recorded WITHOUT the model "
                        "the vendor actually served, and is priced on the model requested.")
                    repo.insert(_drop_042_columns(row))
                if self._failures:
                    LOGGER.info("llm_usage: ledger writes recovered after %d failure(s)",
                                self._failures)
                    self._failures = 0
            except Exception as e:  # noqa: BLE001 -- the ledger must not take anything down
                text = str(e)
                if "llm_calls" in text and ("PGRST205" in text or "42P01" in text
                                            or "does not exist" in text
                                            or "Could not find the table" in text):
                    self._disabled = True
                    LOGGER.error(
                        "llm_usage: the llm_calls table does not exist. Run "
                        "db/migrations/039_cost_tracking.sql. LLM usage is NOT being "
                        "recorded by this process until it restarts.")
                    continue
                self._failures += 1
                repo = None   # a broken connection is rebuilt on the next row
                if self._failures in (1, 10, 100) or self._failures % 1000 == 0:
                    LOGGER.warning("llm_usage: could not write a cost record (%s); %d "
                                   "consecutive failure(s)", type(e).__name__, self._failures)
            finally:
                self._queue.task_done()

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait for queued rows to be written. True if the queue emptied in time."""
        deadline = timeout if timeout is not None else self._FLUSH_ON_EXIT_SECONDS
        done = threading.Event()

        def _wait() -> None:
            self._queue.join()
            done.set()

        threading.Thread(target=_wait, daemon=True).start()
        return done.wait(deadline)


_writer = _DatabaseWriter()
_sink: Callable[[Any], None] = _writer.submit


def set_sink(sink: Optional[Callable[[Any], None]]) -> Callable[[Any], None]:
    """
    Replace where records go, returning the previous sink. For tests.

    :param sink: Called with each ``LlmCall``; None restores the database writer.
    """
    global _sink
    previous = _sink
    _sink = sink if sink is not None else _writer.submit
    return previous


def record_attempt(
    *,
    profile: str,
    call_type: str,
    vendor: str,
    model: str,
    chain_position: int,
    succeeded: bool,
    error: Optional[BaseException],
    started_at: datetime,
    duration_ms: Optional[int],
) -> None:
    """
    Record one vendor attempt against the scope in force. Never raises.

    Usage comes from whatever the vendor call noted; an attempt that raised
    before any response records its attribution and outcome with no counts.
    """
    try:
        from db.models.llm_call import LlmCall  # noqa: PLC0415

        noted = _NOTED.get() or _nothing_noted()
        scope = usage_scope.current()
        call = LlmCall(
            occurred_at=started_at,
            firm_id=scope["firm_id"],
            staff_id=scope["staff_id"],
            matter_id=scope["matter_id"],
            auth_uid=scope["auth_uid"],
            source_type=scope["source_type"],
            source_id=scope["source_id"],
            profile=profile,
            call_type=call_type,
            vendor=vendor,
            model=model,
            served_model=noted["served_model"],
            vendor_response_id=noted["vendor_response_id"],
            chain_position=chain_position,
            outcome="succeeded" if succeeded else "failed",
            # The class only. A vendor error message can quote the prompt back,
            # and the prompt is somebody's bank statement.
            error_class=type(error).__name__ if error is not None else None,
            duration_ms=duration_ms,
            usage_raw=noted["raw"],
            **noted["counts"],
        )
        _sink(call)
    except Exception as e:  # noqa: BLE001 -- recording must never fail the work
        LOGGER.warning("llm_usage: could not record a %s call (%s)", vendor, type(e).__name__)


def now() -> datetime:
    """The clock ``llm_service`` stamps a call with. Timezone-aware UTC."""
    return datetime.now(timezone.utc)
