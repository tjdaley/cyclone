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

WHY IT STOPPED IS A FACT, NOT AN INFERENCE
────────────────────────────────────────────────────────────────────────────
"The extraction came back short — did it run out of room, or decide it was
done?" wants opposite fixes: a bigger ceiling, or smaller chunks. ``output_tokens``
against a ``max_tokens`` that lives in a JSON file inside the image can only
ever imply the answer; every vendor states it outright, in its own vocabulary
(``stop_reason`` on Anthropic, ``choices[0].finish_reason`` on OpenAI-compatible
replies, ``candidates[0].finish_reason`` on Gemini — or ``prompt_feedback.block_reason``
when Gemini blocks a prompt outright and returns no candidates at all).

Both are stored (046): the vendor's own word verbatim, for the same reason
``usage_raw`` is kept, and ``stop_reason_class`` reconciling three vocabularies
into ``complete`` / ``truncated`` / ``refused`` / ``other`` — because a query
for "what truncated?" should not require knowing all three spellings. An
unrecognized word classes as ``other`` rather than being guessed at.

**``refused`` closes a blind spot rather than adding a nicety.** Gemini's
filters decline material describing crimes against minors, which in family law
is routine evidence, and that refusal is invisible today: the failover just
serves the next model (CLAUDE.md §11). This is where it becomes countable.
"""
import atexit
import json
import queue
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, NamedTuple, Optional

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
        # input_tokens already EXCLUDES the cache. output_tokens INCLUDES
        # thinking and remains the authoritative billing total.
        #
        # THINKING *IS* BROKEN OUT, in `output_tokens_details.thinking_tokens`
        # -- a read-only observability breakdown, always <= output_tokens, and
        # present on non-streaming responses (when streaming it arrives only on
        # the final message_delta, which this service does not use). This was
        # read as unavailable for a session, which left 88% of one statement
        # chunk's billed output unexplained; see 049.
        #
        # It reports the RAW reasoning the model generated, which is the figure
        # that was billed -- not the summarized thinking text the response body
        # carries. Those two differ by a lot, which is why characters off the
        # blocks cannot stand in for it.
        return {
            "input_uncached_tokens": _num(usage, "input_tokens"),
            "input_cache_read_tokens": _num(usage, "cache_read_input_tokens"),
            "input_cache_write_tokens": _num(usage, "cache_creation_input_tokens"),
            "output_tokens": _num(usage, "output_tokens"),
            "reasoning_tokens": _num(usage, "output_tokens_details", "thinking_tokens"),
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


#: Each vendor's word for "I stopped because I ran out of room", and for "the
#: filters stopped me". Compared lowercased, so Gemini's SHOUTING enum matches.
#: Anything unlisted classes as 'other' RATHER than being guessed at -- the
#: verbatim word is stored beside the class, so a value a vendor adds later can
#: be reclassified over history instead of only going forward.
_TRUNCATED_WORDS = frozenset({"max_tokens", "length", "model_length", "max_output_tokens"})
_REFUSED_WORDS = frozenset({
    "refusal", "content_filter", "safety", "prohibited_content", "blocklist", "spii",
    "recitation", "image_safety",
})
#: Said by every vendor to mean "it finished". Gemini's is STOP; Anthropic also
#: ends on a stop sequence or a tool call, both of which are the model choosing
#: to stop rather than being cut off.
_COMPLETE_WORDS = frozenset({"end_turn", "stop", "stop_sequence", "tool_use", "tool_calls",
                             "function_call", "pause_turn"})


def _stop_class(reason: Optional[str]) -> Optional[str]:
    """``stop_reason`` reconciled to complete | truncated | refused | other."""
    if reason is None:
        return None
    word = reason.lower()
    if word in _TRUNCATED_WORDS:
        return "truncated"
    if word in _REFUSED_WORDS:
        return "refused"
    if word in _COMPLETE_WORDS:
        return "complete"
    return "other"


def _enum_name(value: Any) -> Optional[str]:
    """A finish reason as text. Gemini reports an enum member, the others a string."""
    if value is None:
        return None
    return _identity(getattr(value, "name", None) or str(value))


def stop_of(vendor: str, response: Any) -> Optional[str]:
    """
    Why the vendor stopped generating, in its own word.

    The answer to "did it run out of room, or decide it was done?", which
    ``output_tokens`` can only ever imply -- and the only place a Gemini safety
    refusal becomes visible, since the failover otherwise just serves the next
    model and nothing says why (CLAUDE.md §11).

    :return: The vendor's word, lowercased, or None where not reported.
    """
    if response is None:
        return None
    if vendor == "gemini":
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            name = _enum_name(getattr(candidates[0], "finish_reason", None))
            if name:
                return name.lower()
        # No candidates at all is the shape a BLOCKED PROMPT takes -- the
        # refusal this firm actually meets, and it is reported somewhere else
        # entirely. Without this branch it would record as nothing happening.
        blocked = _enum_name(getattr(getattr(response, "prompt_feedback", None),
                                     "block_reason", None))
        return blocked.lower() if blocked else None
    if vendor == "anthropic":
        name = _identity(getattr(response, "stop_reason", None))
        return name.lower() if name else None
    # OpenAI, Groq, DeepSeek, and anything else OpenAI-compatible.
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    name = _identity(getattr(choices[0], "finish_reason", None))
    return name.lower() if name else None


# ── The side channel from a vendor call back to the failover loop ────────────
#
# The `_call_*` functions return text, and every caller and test depends on that
# shape. Rather than change eight signatures and the loop that reads them, each
# one NOTES its response here the moment the vendor returns -- before parsing,
# so a reply that then turns out empty or unparseable still carries its cost --
# and the loop reads it back when it records the attempt. Same thread, same
# context, so a plain ContextVar set is visible to the caller on return.

#: Response block types that are thinking, and the attribute each carries it in.
#:
#: `redacted_thinking` holds encrypted bytes rather than readable text, and its
#: length is counted all the same: it was generated and it was billed, and a
#: reply whose reasoning came back redacted must not read as a reply that did
#: not reason.
_THINKING_BLOCKS = {"thinking": "thinking", "redacted_thinking": "data"}


def blocks_of(vendor: str, response: Any) -> tuple[Optional[int], Optional[int]]:
    """
    Characters of thinking SUMMARY in a reply, and characters of answer.

    **THE FIRST NUMBER IS NOT A MEASURE OF THINKING, AND READING IT AS ONE IS A
    MISTAKE THIS DOCSTRING EXISTS TO PREVENT.** Anthropic returns thinking as a
    *summary* of the reasoning, not the reasoning: "you are billed for the full
    thinking process, not the thinking content visible in the response", and the
    two differ by a lot. So this systematically understates, and under
    ``display: "omitted"`` it would be zero on a reply that thought hard.

    **The real figure is ``reasoning_tokens``**, which `normalize` reads from
    Anthropic's ``output_tokens_details.thinking_tokens`` and Gemini's
    ``thoughts_token_count``. Use that for cost and for the thinking share.

    What these two are still good for: ``text_chars`` is the answer's true
    length, which is the only way to sanity-check a reply against its token
    count — a chunk that reported 28,056 output tokens for 9,774 characters of
    answer is 0.35 chars per token, where text runs 2.5 to 3.5, and that
    arithmetic is what exposed the thinking in the first place. A non-zero
    ``thinking_chars`` is NOT a yes/no that the model reasoned, which is what
    this docstring first claimed. **Measured: it is 0 on every call of a
    statement walk where ``reasoning_tokens`` shows reasoning at 28-75% of
    output.** Thinking blocks are simply not returned on this path. A non-zero
    value still means the model reasoned; a zero means nothing at all.

    ``(None, None)`` for a vendor whose replies are not block-shaped, because a
    zero would assert that a reply carried no thinking when nothing looked.

    :return: ``(thinking_chars, text_chars)``.
    :rtype: tuple[Optional[int], Optional[int]]
    """
    content = getattr(response, "content", None)
    if not isinstance(content, (list, tuple)):
        return None, None

    thinking = answer = 0
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "text":
            answer += len(getattr(block, "text", None) or "")
        elif kind in _THINKING_BLOCKS:
            thinking += len(getattr(block, _THINKING_BLOCKS[kind], None) or "")
    return thinking, answer


def _nothing_noted() -> dict[str, Any]:
    return {
        "counts": {field: None for field in USAGE_FIELDS},
        "raw": None,
        "served_model": None,
        "vendor_response_id": None,
        "stop_reason": None,
        "stop_reason_class": None,
        "thinking_chars": None,
        "text_chars": None,
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
        noted["stop_reason"] = stop_of(vendor, response)
        noted["stop_reason_class"] = _stop_class(noted["stop_reason"])
        # Said out loud, because both of these are silent today: a truncated
        # reply reaches the caller as valid JSON that merely stops, and a
        # refusal reaches it as an empty answer that fails over to the next
        # vendor. Rare by nature, so a line each is cheap. The stop reason is a
        # vendor token, never content, so §6 is satisfied.
        if noted["stop_reason_class"] in ("truncated", "refused"):
            LOGGER.warning("llm_usage: %s/%s stopped as %s (%s)",
                           vendor, noted["served_model"] or "?",
                           noted["stop_reason"], noted["stop_reason_class"])
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("llm_usage: could not read the stop reason from a %s response (%s)",
                       vendor, type(e).__name__)
    try:
        usage = usage_of(vendor, response)
        noted["counts"], noted["raw"] = normalize(vendor, usage), _plain(usage)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("llm_usage: could not read usage from a %s response (%s)",
                       vendor, type(e).__name__)
    try:
        noted["thinking_chars"], noted["text_chars"] = blocks_of(vendor, response)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("llm_usage: could not measure the blocks of a %s response (%s)",
                       vendor, type(e).__name__)
    _NOTED.set(noted)


# ── Recording ────────────────────────────────────────────────────────────────

class _LateColumns(NamedTuple):
    """Columns a migration adds, and what a database still missing them loses."""

    columns: tuple[str, ...]
    migration: str
    consequence: str


#: Groups added after the ledger shipped. A node running new code against an
#: older database would otherwise have EVERY row rejected for one unknown
#: column -- so the row is written without that group instead. Losing who
#: answered, or why it stopped, is bad; losing the tokens as well is worse.
_LATE_COLUMNS = (
    _LateColumns(
        ("served_model", "vendor_response_id"), "042_served_model.sql",
        "usage is recorded WITHOUT the model the vendor actually served, and is priced "
        "on the model requested"),
    _LateColumns(
        ("stop_reason", "stop_reason_class"), "046_stop_reason.sql",
        "usage is recorded WITHOUT why the vendor stopped, so a truncated reply and a "
        "refusal cannot be told apart from a complete one"),
    _LateColumns(
        ("thinking_chars", "text_chars"), "049_thinking_measurement.sql",
        "usage is recorded WITHOUT how much of the reply was thinking rather than "
        "answer, which on one measured statement chunk was 88% of the billed output "
        "and 276 of its 311 seconds"),
)


def _missing_group(error: BaseException) -> Optional[_LateColumns]:
    """The group a PostgREST rejection (PGRST204) names a column from, if any."""
    text = str(error)
    if "PGRST204" not in text:
        return None
    for group in _LATE_COLUMNS:
        if any(column in text for column in group.columns):
            return group
    return None


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
        # Columns this database turned out not to have. Grows at most once per
        # group in _LATE_COLUMNS, for the life of the process.
        self._shed: set[str] = set()

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
                self._insert(repo, call.model_dump(exclude_none=True))
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
                # NO CREDENTIALS IS THE SAME SHAPE OF FAILURE AS NO TABLE, AND
                # WAS BEING REPORTED AS A HICCUP. `SupabaseManager()` raises
                # ValueError when the URL or the service-role key is empty, and
                # that cannot come right inside this process -- settings are
                # read once at import. Retrying it per row logged a WARNING at
                # 1, 10, 100 and every 1000th row, which reads as an
                # intermittent database problem rather than "this node is not
                # recording spend at all". Matched on the message rather than
                # the type, narrowly, because a bare ValueError from anywhere
                # else really may be transient.
                if isinstance(e, ValueError) and "SUPABASE_SERVICE_ROLE_KEY" in text:
                    self._disabled = True
                    LOGGER.error(
                        "llm_usage: no Supabase service-role credentials, so the ledger "
                        "cannot be written. LLM usage is NOT being recorded by this "
                        "process until it restarts with SUPABASE_URL and "
                        "SUPABASE_SERVICE_ROLE_KEY set.")
                    continue
                self._failures += 1
                repo = None   # a broken connection is rebuilt on the next row
                if self._failures in (1, 10, 100) or self._failures % 1000 == 0:
                    LOGGER.warning("llm_usage: could not write a cost record (%s); %d "
                                   "consecutive failure(s)", type(e).__name__, self._failures)
            finally:
                self._queue.task_done()

    def _insert(self, repo: Any, row: dict[str, Any]) -> None:
        """
        Insert a row, shedding columns this database's migrations have not added.

        **A loop rather than one retry, because PostgREST names only ONE missing
        column per rejection** -- a database behind by two migrations needs two
        passes, and a single retry would write nothing at all on the second.
        Each turn either returns, re-raises something else, or grows ``_shed``,
        which is bounded by ``_LATE_COLUMNS``, so it always terminates.
        """
        while True:
            try:
                repo.insert({k: v for k, v in row.items() if k not in self._shed})
                return
            except Exception as e:  # noqa: BLE001
                group = _missing_group(e)
                if group is None or self._shed.issuperset(group.columns):
                    raise
                self._shed.update(group.columns)
                LOGGER.error(
                    "llm_usage: llm_calls has no %s column. Run db/migrations/%s, then "
                    "restart api and worker. Until this process restarts, %s.",
                    group.columns[0], group.migration, group.consequence)

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
            stop_reason=noted["stop_reason"],
            stop_reason_class=noted["stop_reason_class"],
            thinking_chars=noted["thinking_chars"],
            text_chars=noted["text_chars"],
            usage_raw=noted["raw"],
            **noted["counts"],
        )
        _sink(call)
    except Exception as e:  # noqa: BLE001 -- recording must never fail the work
        LOGGER.warning("llm_usage: could not record a %s call (%s)", vendor, type(e).__name__)


def now() -> datetime:
    """The clock ``llm_service`` stamps a call with. Timezone-aware UTC."""
    return datetime.now(timezone.utc)
