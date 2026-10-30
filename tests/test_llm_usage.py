"""
tests/test_llm_usage.py - Every model call is counted, reconciled, and attributed.

Nothing captured token usage before this. Every `_call_*` returned a bare string
and discarded the response carrying the counts, so what running Cyclone cost was
unknowable from inside it. Four things have to hold for the ledger to be worth
anything, and each has a way to be quietly wrong:

  1. **The same prompt costs the same whoever answers it.** Vendors disagree
     about whether "input tokens" includes the cache, and Gemini reports
     thinking outside its output count. Summed raw, a cache hit on OpenAI is
     billed twice and a thinking model on Gemini is billed half.
  2. **Every attempt is a row, not just the winner.** A call that fails over
     after the vendor started generating is still billed.
  3. **Spend lands on the right job, matter, request or lead** -- and the two
     mechanisms that obviously should carry that and do not (a scope set around
     `pool.submit`, and one set inside a sync dependency) are pinned here.
  4. **Recording never fails the work,** and a missing table says so once.
  5. **The model that ANSWERED is recorded beside the one requested** (042),
     because a call is priced on it -- and code deployed ahead of 042 still
     records the tokens rather than losing the row.

Run:  venv/Scripts/python.exe tests/test_llm_usage.py
"""
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from services import llm_usage  # noqa: E402
from util import usage_scope as scope_mod  # noqa: E402
from util.usage_scope import usage_scope  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


NS = types.SimpleNamespace

# ── 1. Normalization ─────────────────────────────────────────────────────────
#
# One logical call -- 1,000 fresh input tokens, 5,000 served from cache, 300 of
# visible output, 700 of thinking -- reported the way each vendor reports it.

print("The same call normalizes to the same figures whoever answered it")

anthropic = llm_usage.normalize("anthropic", NS(
    input_tokens=1000,              # EXCLUDES the cache
    cache_read_input_tokens=5000,
    cache_creation_input_tokens=0,
    output_tokens=1000,             # INCLUDES thinking
))
openai = llm_usage.normalize("openai", NS(
    prompt_tokens=6000,             # INCLUDES the cache
    prompt_tokens_details=NS(cached_tokens=5000),
    completion_tokens=1000,         # INCLUDES reasoning
    completion_tokens_details=NS(reasoning_tokens=700),
))
gemini = llm_usage.normalize("gemini", NS(
    prompt_token_count=6000,        # INCLUDES cached content
    cached_content_token_count=5000,
    candidates_token_count=300,     # EXCLUDES thinking
    thoughts_token_count=700,       # billed as output, reported beside it
))
deepseek = llm_usage.normalize("deepseek", {
    "prompt_tokens": 6000,
    "prompt_cache_hit_tokens": 5000,
    "prompt_cache_miss_tokens": 1000,
    "completion_tokens": 1000,
})

for name, got in (("anthropic", anthropic), ("openai", openai),
                  ("gemini", gemini), ("deepseek", deepseek)):
    check("%-9s full-rate input is 1000" % name, got["input_uncached_tokens"], 1000)
    check("%-9s cache reads are 5000" % name, got["input_cache_read_tokens"], 5000)
    check("%-9s output, thinking included, is 1000" % name, got["output_tokens"], 1000)

# The two traps, named, so a refactor that "simplifies" them fails here.
check("OpenAI's cached share is SUBTRACTED, or a cache hit is billed twice",
      openai["input_uncached_tokens"], 1000)
check("Gemini's thinking is ADDED to output, or a thinking model is half-billed",
      gemini["output_tokens"], 1000)
check("and reported as the reasoning share", gemini["reasoning_tokens"], 700)

# EVERY VENDOR MUST REPORT ITS REASONING SHARE, because that is the only answer
# to "what did a reasoning model cost us" -- and a reasoning model sits second
# in both production chains. The fixtures above already carried the field; for
# every vendor but Gemini nothing asserted it arrived, so a refactor that
# dropped `completion_tokens_details` from the OpenAI-compatible branch would
# have passed. Anthropic's was NULL by a mistaken reading until 2026-10-29.
for _vendor, _usage, _want in (
    ("anthropic", {"input_tokens": 300, "output_tokens": 1000,
                   "output_tokens_details": {"thinking_tokens": 700}}, 700),
    ("openai", {"prompt_tokens": 300, "completion_tokens": 1000,
                "completion_tokens_details": {"reasoning_tokens": 700}}, 700),
    ("groq", {"prompt_tokens": 300, "completion_tokens": 1000,
              "completion_tokens_details": {"reasoning_tokens": 700}}, 700),
    ("deepseek", {"prompt_tokens": 300, "completion_tokens": 1000,
                  "completion_tokens_details": {"reasoning_tokens": 700}}, 700),
    ("gemini", {"prompt_token_count": 300, "candidates_token_count": 300,
                "thoughts_token_count": 700}, 700),
):
    _got = llm_usage.normalize(_vendor, _usage)
    check("%-9s reports its reasoning share" % _vendor, _got["reasoning_tokens"], _want)
    # And it is a BREAKDOWN of output, never an addition to it: reasoning is
    # billed at the output rate on every one of these vendors, so a reasoning
    # count above the output total would mean the pricing double-counts.
    check("%-9s output total is at least its reasoning" % _vendor,
          _got["output_tokens"] >= _got["reasoning_tokens"], True)

print("")
print("Unreported is None, never zero")
nothing = llm_usage.normalize("anthropic", None)
check("no response at all -> every figure None", set(nothing.values()), {None})
bare = llm_usage.normalize("openai", NS(prompt_tokens=800, completion_tokens=50))
check("no cache breakdown -> the whole prompt at the full rate (overstates, never hides)",
      bare["input_uncached_tokens"], 800)
check("but cache reads stay UNKNOWN rather than claimed zero",
      bare["input_cache_read_tokens"], None)
check("a vendor that never writes a cache reports None there, not 0",
      openai["input_cache_write_tokens"], None)


# ── 2. The failover loop records every attempt ───────────────────────────────

print("")
print("Every attempt is a row, including the one that failed over")

from services.llm_service import LLMService  # noqa: E402
from util import llm_profiles as profiles_mod  # noqa: E402
from util.llm_profiles import LLMCandidate, LLMProfile  # noqa: E402
from util.settings import Settings  # noqa: E402

recorded: list = []
previous_sink = llm_usage.set_sink(recorded.append)

PROFILE = LLMProfile(name="extract_account_statement", chain=[
    LLMCandidate(vendor="anthropic", model="claude-opus-5"),
    LLMCandidate(vendor="openai", model="gpt-5"),
])
original_get = profiles_mod.llm_profiles.get
original_key = Settings.llm_api_key
profiles_mod.llm_profiles.get = lambda name: PROFILE
Settings.llm_api_key = lambda self, vendor: "configured"

service = LLMService()


def anthropic_answers_empty(profile, candidate, system, user):
    # The vendor RESPONDED, and billed for it, and the reply had no text --
    # a reply that is then rejected is still a cost.
    llm_usage.note_response("anthropic", NS(usage=NS(
        input_tokens=4000, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, output_tokens=12)))
    return ""


def openai_answers(profile, candidate, system, user):
    llm_usage.note_response("openai", NS(
        model="gpt-5-2025-08-07", id="chatcmpl-abc123", usage=NS(
        prompt_tokens=4000, prompt_tokens_details=NS(cached_tokens=0),
        completion_tokens=900, completion_tokens_details=NS(reasoning_tokens=0))))
    return '{"transactions": []}'


service._text_handlers["anthropic"] = anthropic_answers_empty
service._text_handlers["openai"] = openai_answers

try:
    with usage_scope(source_type="job", source_id="job-42", staff_id=7, matter_id=311):
        result = service.complete_detailed("system", "a statement", profile="extract_account_statement")

    check("the call still succeeds on the fallback", result.vendor, "openai")
    check("two attempts, two rows", len(recorded), 2)
    first, second = recorded
    check("the first is recorded as failed", first.outcome, "failed")
    check("naming the error class", first.error_class, "LLMUnavailableError")
    check_true("and keeping its cost, noted before the reply was rejected",
               first.input_uncached_tokens == 4000 and first.output_tokens == 12)
    check("the second succeeded at position 2", (second.outcome, second.chain_position),
          ("succeeded", 2))
    check("with its own counts", (second.input_uncached_tokens, second.output_tokens),
          (4000, 900))
    check("the raw usage travels with it", second.usage_raw["completion_tokens"], 900)
    check("the requested model stays what the catalog named", second.model, "gpt-5")
    check("and the model that ANSWERED is recorded beside it",
          (second.served_model, second.vendor_response_id), ("gpt-5-2025-08-07", "chatcmpl-abc123"))
    check("a response naming no model records None, never the requested name",
          (first.served_model, first.vendor_response_id), (None, None))
    check_true("both carry the job's attribution",
               all((r.source_type, r.source_id, r.staff_id, r.matter_id)
                   == ("job", "job-42", 7, 311) for r in recorded))
    check_true("and a start time", all(isinstance(r.occurred_at, datetime) for r in recorded))

    print("")
    print("A call that died before any response is recorded with no counts")
    recorded.clear()

    def times_out(profile, candidate, system, user):
        raise TimeoutError("read timed out")

    service._text_handlers["anthropic"] = times_out
    service.complete("s", "u", profile="extract_account_statement")
    timeout_row = recorded[0]
    check("failed, as TimeoutError", (timeout_row.outcome, timeout_row.error_class),
          ("failed", "TimeoutError"))
    check("with every count None -- not zero, and not the previous attempt's",
          {getattr(timeout_row, f) for f in llm_usage.USAGE_FIELDS}, {None})
    check("and no served model inherited from the previous attempt",
          (timeout_row.served_model, timeout_row.vendor_response_id), (None, None))
    check("and unattributed, because no scope was set this time",
          timeout_row.source_type, "unattributed")

    print("")
    print("The error MESSAGE never reaches the ledger")
    recorded.clear()

    def leaks(profile, candidate, system, user):
        raise ValueError("could not parse: 'ZELLE PAYMENT TO KATHY GUNN $4,000.00'")

    service._text_handlers["anthropic"] = leaks
    service.complete("s", "u", profile="extract_account_statement")
    check_true("only the class is kept -- a vendor message can quote the statement",
               all("KATHY" not in str(r.model_dump()) for r in recorded))
finally:
    profiles_mod.llm_profiles.get = original_get
    Settings.llm_api_key = original_key


# ── 3. Attribution ───────────────────────────────────────────────────────────

print("")
print("Scopes nest, and refuse a field they do not know")
with usage_scope(source_type="crm", source_id="welcome poll"):
    with usage_scope(source_type="crm_lead", source_id="lead-9"):
        inner = scope_mod.current()
    outer = scope_mod.current()
check("the inner scope narrows", (inner["source_type"], inner["source_id"]),
      ("crm_lead", "lead-9"))
check("and the outer is restored after", (outer["source_type"], outer["source_id"]),
      ("crm", "welcome poll"))
with usage_scope(matter_id=5):
    with usage_scope(matter_id=None):
        check("None does not wipe an outer value", scope_mod.current()["matter_id"], 5)
try:
    with usage_scope(matter=5):
        pass
    check("a misspelt field raises", False, True)
except TypeError:
    check("a misspelt field raises rather than recording nothing", True, True)

print("")
print("A scope set around pool.submit does NOT reach the pool thread")
# The trap, pinned. This is why job_service sets its scope INSIDE run_claimed.
with ThreadPoolExecutor(max_workers=1) as pool:
    with usage_scope(source_type="job", source_id="set-outside"):
        seen = pool.submit(lambda: scope_mod.current()["source_type"]).result()
check("the thread sees nothing", seen, "unattributed")

print("")
print("job_service.run_claimed attributes from inside the pool thread")
from db.models.job import JobInDB, JobKind  # noqa: E402
from services.job_service import job_service  # noqa: E402

seen_in_job: list = []
original_ingest = job_service._run_statement_ingest
job_service._run_statement_ingest = lambda manager, repo, job: seen_in_job.append(scope_mod.current())
job_service_module = sys.modules["services.job_service"]
original_repo = job_service_module.JobRepository
job_service_module.JobRepository = lambda manager: None
try:
    job = JobInDB.model_construct(id="job-77", kind=JobKind.statement_ingest,
                                  matter_id=311, requested_by_staff_id=7)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(job_service.run_claimed, None, job).result()
finally:
    job_service._run_statement_ingest = original_ingest
    job_service_module.JobRepository = original_repo
got = seen_in_job[0]
check("source is the job", (got["source_type"], got["source_id"]), ("job", "job-77"))
check("with its matter and requester", (got["matter_id"], got["staff_id"]), (311, 7))

print("")
print("The real AuthMiddleware attributes a request")
# Imported with the JWKS fetch stubbed: the module fetches keys over the network
# at import, and a test must not.
import httpx  # noqa: E402

real_get = httpx.get
httpx.get = lambda *a, **k: NS(json=lambda: {"keys": []})
try:
    import middleware.auth_middleware as auth_mod  # noqa: E402
finally:
    httpx.get = real_get

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

auth_mod._decode_token = lambda token: ("uid-abc", "attorney", "a@example.com")
app = FastAPI()
app.add_middleware(auth_mod.AuthMiddleware)


def sync_dependency():
    # Stands in for require_role: a sync dependency. Anything it set would be
    # lost; it sets nothing, and the endpoint must still see the request scope.
    return None


@app.post("/api/v1/billing/parse")
def endpoint(_=Depends(sync_dependency)):
    return scope_mod.current()


response = TestClient(app).post("/api/v1/billing/parse",
                                headers={"Authorization": "Bearer x"}).json()
check("a sync endpoint behind a sync dependency sees the request scope",
      (response["source_type"], response["source_id"], response["auth_uid"]),
      ("request", "POST /api/v1/billing/parse", "uid-abc"))


# ── 4. Recording never fails the work ────────────────────────────────────────

print("")
print("A broken sink or a strange response never raises")


def exploding_sink(call):
    raise RuntimeError("database on fire")


llm_usage.set_sink(exploding_sink)
try:
    llm_usage.record_attempt(profile="p", call_type="text", vendor="openai", model="m",
                             chain_position=1, succeeded=True, error=None,
                             started_at=datetime.now(timezone.utc), duration_ms=1)
    check("record_attempt swallowed the sink's exception", True, True)
except Exception:
    check("record_attempt swallowed the sink's exception", False, True)
llm_usage.set_sink(recorded.append)


class Hostile:
    @property
    def usage(self):
        raise AttributeError("SDK changed shape")


try:
    llm_usage.note_response("openai", Hostile())
    check("note_response survives an SDK that changed shape", True, True)
except Exception:
    check("note_response survives an SDK that changed shape", False, True)

print("")
print("Who answered, per SDK -- read independently of usage")
check("Gemini: model_version and response_id",
      llm_usage.served_of("gemini", NS(model_version="gemini-3.5-flash-lite-001",
                                       response_id="resp-9", model="not-this")),
      ("gemini-3.5-flash-lite-001", "resp-9"))
check("Anthropic: model and id",
      llm_usage.served_of("anthropic", NS(model="claude-opus-5", id="msg_01")),
      ("claude-opus-5", "msg_01"))
check("a blank or non-string value is None, not an empty name that prices nothing",
      llm_usage.served_of("openai", NS(model="  ", id=12345)), (None, None))


class UsageMoved:
    model = "gpt-5-2025-08-07"
    id = "chatcmpl-x"

    @property
    def usage(self):
        raise AttributeError("SDK changed shape")


llm_usage.begin_attempt()
llm_usage.note_response("openai", UsageMoved())
noted = llm_usage._NOTED.get()
check("an SDK whose usage moved still reports who answered",
      (noted["served_model"], noted["counts"]["output_tokens"]), ("gpt-5-2025-08-07", None))

print("")
print("Why it stopped -- three vocabularies, one question (046)")
# "Short extraction: out of room, or did it decide it was done?" wants opposite
# fixes, and output_tokens against a max_tokens living in a JSON file inside
# the image can only ever imply the answer. Every vendor states it.
check("Anthropic says end_turn",
      llm_usage.stop_of("anthropic", NS(stop_reason="end_turn")), "end_turn")
check("OpenAI says it on the choice, not the response",
      llm_usage.stop_of("openai", NS(choices=[NS(finish_reason="length")])), "length")
check("Gemini reports an ENUM, not a string",
      llm_usage.stop_of("gemini", NS(candidates=[NS(finish_reason=NS(name="MAX_TOKENS"))])),
      "max_tokens")
# The refusal this firm actually meets: Gemini blocks the PROMPT, so there are
# no candidates at all and the reason is somewhere else entirely. Without this
# branch it would record as nothing having happened.
check("a blocked Gemini prompt has no candidates, and still says why",
      llm_usage.stop_of("gemini", NS(candidates=[],
                                     prompt_feedback=NS(block_reason=NS(name="SAFETY")))),
      "safety")
check("no response, no reason", llm_usage.stop_of("anthropic", None), None)
check("an empty choices list does not raise",
      llm_usage.stop_of("openai", NS(choices=[])), None)

check("three spellings of 'it ran out of room' reconcile",
      [llm_usage._stop_class(w) for w in ("max_tokens", "length", "max_tokens")],
      ["truncated", "truncated", "truncated"])
check("and three of 'the filters stopped it'",
      [llm_usage._stop_class(w) for w in ("refusal", "content_filter", "safety")],
      ["refused", "refused", "refused"])
check("finishing is finishing, whoever says it",
      [llm_usage._stop_class(w) for w in ("end_turn", "stop", "stop_sequence")],
      ["complete", "complete", "complete"])
# A word no vendor documents yet is 'other', never guessed into a class that
# would make a report confidently wrong. The verbatim word is stored beside it,
# so it can be reclassified over history rather than only going forward.
check("a word nobody has seen before is 'other', not a guess",
      llm_usage._stop_class("quantum_collapse"), "other")
check("and nothing reported stays None", llm_usage._stop_class(None), None)

llm_usage.begin_attempt()
llm_usage.note_response("anthropic", NS(model="claude-opus-5", id="msg_1",
                                        stop_reason="max_tokens", usage=None))
noted = llm_usage._NOTED.get()
check("note_response carries both the word and the class",
      (noted["stop_reason"], noted["stop_reason_class"]), ("max_tokens", "truncated"))

# Reading the stop reason must not be able to cost us the usage, or the fix
# would be worse than the gap -- same rule as the served model above.


class StopExplodes:
    model = "claude-opus-5"
    id = "msg_2"
    usage = NS(input_tokens=10, output_tokens=20)

    @property
    def stop_reason(self):
        raise AttributeError("SDK changed shape")


llm_usage.begin_attempt()
llm_usage.note_response("anthropic", StopExplodes())
noted = llm_usage._NOTED.get()
check("an SDK that moved the stop reason still reports the tokens",
      (noted["stop_reason"], noted["counts"]["output_tokens"]), (None, 20))

print("")
print("The database writer: firm_id omitted, and a missing table says so once")
inserted: list = []


class FakeRepo:
    fail_with = None
    before_042 = False
    before_046 = False

    def __init__(self, manager):
        pass

    def insert(self, data):
        if FakeRepo.fail_with:
            raise RuntimeError(FakeRepo.fail_with)
        # PostgREST names exactly ONE missing column per rejection, however many
        # the row carries that the table has not got. That is why the writer
        # loops instead of retrying once.
        if FakeRepo.before_042 and "served_model" in data:
            raise RuntimeError("{'code': 'PGRST204', 'message': \"Could not find the "
                               "'served_model' column of 'llm_calls' in the schema cache\"}")
        if FakeRepo.before_046 and "stop_reason" in data:
            raise RuntimeError("{'code': 'PGRST204', 'message': \"Could not find the "
                               "'stop_reason' column of 'llm_calls' in the schema cache\"}")
        inserted.append(data)


fake_repo_module = types.ModuleType("db.repositories.llm_call")
fake_repo_module.LlmCallRepository = FakeRepo
saved_repo_module = sys.modules.get("db.repositories.llm_call")
sys.modules["db.repositories.llm_call"] = fake_repo_module
import db_handler  # noqa: E402

saved_manager = db_handler.SupabaseManager
db_handler.SupabaseManager = lambda: object()
try:
    from db.models.llm_call import LlmCall  # noqa: E402

    def a_call():
        return LlmCall(occurred_at=datetime.now(timezone.utc), source_type="job",
                       profile="p", call_type="text", vendor="openai", model="m",
                       chain_position=1, outcome="succeeded", output_tokens=10)

    writer = llm_usage._DatabaseWriter()
    writer.submit(a_call())
    check_true("the row is written", writer.flush(timeout=5) and len(inserted) == 1)
    check("firm_id is OMITTED so the column default applies, not sent as null",
          "firm_id" in inserted[0], False)
    check("and so are the unreported counts", "input_uncached_tokens" in inserted[0], False)

    FakeRepo.fail_with = ("{'code': 'PGRST205', 'message': \"Could not find the table "
                          "'public.llm_calls' in the schema cache\"}")
    missing = llm_usage._DatabaseWriter()
    missing.submit(a_call())
    missing.flush(timeout=5)
    check("a missing table disables the writer", missing._disabled, True)
    missing.submit(a_call())
    check("and later rows are not queued to fail again", missing._queue.qsize(), 0)

    FakeRepo.fail_with = None
    FakeRepo.before_042 = True
    inserted.clear()
    served = a_call().model_copy(update={"served_model": "gpt-5-2025-08-07",
                                         "vendor_response_id": "chatcmpl-1"})
    old_table = llm_usage._DatabaseWriter()
    old_table.submit(served)
    old_table.submit(served)
    old_table.flush(timeout=5)
    check("before 042 the rows are still written, without the new columns",
          [sorted(set(r) & {"served_model", "vendor_response_id"}) for r in inserted], [[], []])
    check("keeping the tokens", [r["output_tokens"] for r in inserted], [10, 10])
    check("and the writer is not disabled", (old_table._disabled, old_table._failures), (False, 0))

    # A database behind by TWO migrations. The first rejection names
    # served_model and the second names stop_reason, so a writer that retried
    # only once would write nothing at all here -- which is the whole reason
    # _insert is a loop.
    FakeRepo.before_046 = True
    inserted.clear()
    full = a_call().model_copy(update={"served_model": "gpt-5-2025-08-07",
                                       "vendor_response_id": "chatcmpl-1",
                                       "stop_reason": "length",
                                       "stop_reason_class": "truncated"})
    behind_two = llm_usage._DatabaseWriter()
    behind_two.submit(full)
    behind_two.flush(timeout=5)
    check("a database behind by two migrations still gets its row", len(inserted), 1)
    check("without either group of columns",
          sorted(set(inserted[0]) & {"served_model", "vendor_response_id",
                                     "stop_reason", "stop_reason_class"}), [])
    check("and the tokens survive both sheds", inserted[0]["output_tokens"], 10)
    check("the writer stays healthy",
          (behind_two._disabled, behind_two._failures), (False, 0))

    # Having shed them once, it must not pay the two rejections again per row.
    inserted.clear()
    behind_two.submit(full)
    behind_two.flush(timeout=5)
    check("later rows go straight out, already shed", len(inserted), 1)
    check("nothing was counted as a failure", behind_two._failures, 0)

    # A rejection that is NOT a late column must still surface as a failure,
    # or a real schema problem would be silently shed forever.
    FakeRepo.before_042 = FakeRepo.before_046 = False
    FakeRepo.fail_with = ("{'code': 'PGRST204', 'message': \"Could not find the "
                          "'something_else' column of 'llm_calls' in the schema cache\"}")
    unknown = llm_usage._DatabaseWriter()
    unknown.submit(a_call())
    unknown.flush(timeout=5)
    check("an unknown missing column is a failure, not a shed",
          (unknown._failures, unknown._shed), (1, set()))
    FakeRepo.fail_with = None
finally:
    db_handler.SupabaseManager = saved_manager
    if saved_repo_module is None:
        sys.modules.pop("db.repositories.llm_call", None)
    else:
        sys.modules["db.repositories.llm_call"] = saved_repo_module
    llm_usage.set_sink(previous_sink)


# ── 5. How much of the reply was thinking (049) ──────────────────────────────
#
# Measured 2026-10-28: one statement chunk returned 9,774 characters of answer
# against 28,056 reported output tokens -- 0.35 chars per token, where text runs
# 2.5 to 3.5, so roughly 88% of the billed output never appeared in the reply.
# `_call_anthropic` sends no `thinking` parameter; the model reasons anyway.
#
# RETRACTED 2026-10-29, and both halves were wrong. `thinking_chars` came back
# 0 on nine of nine calls -- no thinking blocks at all -- and the 2.5-to-3.5
# benchmark is PROSE, where this schema's JSON measures 1.29 chars per output
# token. So 0.35 was a comparison against the wrong constant, not an
# impossibility. Anthropic DOES break thinking out, in
# `output_tokens_details.thinking_tokens`; see section 5b. These two columns
# keep the narrower jobs described in `blocks_of`.

print("")
print("-- 5. Thinking measurement --")


class Block:
    def __init__(self, kind, **fields):
        self.type = kind
        for name, value in fields.items():
            setattr(self, name, value)


class Reply:
    def __init__(self, *blocks):
        self.content = list(blocks)


check("a reply measures both halves",
      llm_usage.blocks_of("anthropic",
                          Reply(Block("thinking", thinking="x" * 24798),
                                Block("text", text="y" * 9774))),
      (24798, 9774))

check("an answer with no thinking is 0, not None",
      llm_usage.blocks_of("anthropic", Reply(Block("text", text="y" * 500))),
      (0, 500))

# Encrypted bytes rather than readable text, and still generation that was
# billed. A reply whose reasoning came back redacted must not read as a reply
# that did not reason.
check("redacted thinking counts by its payload length",
      llm_usage.blocks_of("anthropic",
                          Reply(Block("redacted_thinking", data="z" * 100),
                                Block("text", text="a" * 10))),
      (100, 10))

check("several blocks of each kind add up",
      llm_usage.blocks_of("anthropic",
                          Reply(Block("thinking", thinking="ab"),
                                Block("text", text="cde"),
                                Block("thinking", thinking="f"),
                                Block("text", text="gh"))),
      (3, 5))

# NONE AND ZERO ARE DIFFERENT, as everywhere else in this ledger. A zero would
# assert that a reply carried no thinking when in fact nothing looked, which
# across a fleet reports an absence of thinking that is an absence of
# measurement.
check("a reply that is not block-shaped measures nothing",
      llm_usage.blocks_of("gemini", object()), (None, None))
check("content that is not a list measures nothing",
      llm_usage.blocks_of("anthropic", type("X", (), {"content": "nope"})()),
      (None, None))
check("an empty block list is a measured zero",
      llm_usage.blocks_of("anthropic", Reply()), (0, 0))
check("a block whose text attribute is missing counts as empty",
      llm_usage.blocks_of("anthropic", Reply(Block("text"), Block("thinking"))), (0, 0))
check("a tool_use block is neither thinking nor answer",
      llm_usage.blocks_of("anthropic",
                          Reply(Block("tool_use", input={"a": 1}), Block("text", text="hi"))),
      (0, 2))

# It must never raise: it runs inside the vendor call, where an exception would
# fail a call that succeeded and hand the work to the next vendor -- the exact
# disguise that hid two SDK bugs for a session.
class BlocksExplode:
    @property
    def content(self):
        raise RuntimeError("boom")


llm_usage.begin_attempt()
llm_usage.note_response("anthropic", BlocksExplode())
noted = llm_usage._NOTED.get() or {}
check_true("a response that explodes on .content does not raise",
           "thinking_chars" in noted)
check("...and leaves the measurement unreported",
      (noted.get("thinking_chars"), noted.get("text_chars")), (None, None))

llm_usage.begin_attempt()
llm_usage.note_response("anthropic", Reply(Block("thinking", thinking="tt"),
                                           Block("text", text="aaa")))
noted = llm_usage._NOTED.get() or {}
check("note_response records the measurement",
      (noted["thinking_chars"], noted["text_chars"]), (2, 3))

# begin_attempt must clear it, or a call that reports nothing inherits the
# previous attempt's figures -- the same trap the token counts already had.
# Read the way `record_attempt` reads it, which is `or _nothing_noted()`: a
# cleared slot is None, and the defaults are what a timed-out call records.
llm_usage.begin_attempt()
check("begin_attempt clears the slot outright", llm_usage._NOTED.get(), None)
noted = llm_usage._NOTED.get() or llm_usage._nothing_noted()
check("so a call that never got a response records no measurement",
      (noted["thinking_chars"], noted["text_chars"]), (None, None))

# A node whose database is behind by this migration must still write the row.
check("a PGRST204 naming thinking_chars sheds the 049 group",
      llm_usage._missing_group(
          Exception("PGRST204 Could not find the 'thinking_chars' column of 'llm_calls'")
      ).migration,
      "049_thinking_measurement.sql")
check("...and text_chars names the same group",
      llm_usage._missing_group(
          Exception("PGRST204 Could not find the 'text_chars' column of 'llm_calls'")
      ).migration,
      "049_thinking_measurement.sql")


# ── 5b. reasoning_tokens IS reported by Anthropic (corrected 2026-10-29) ─────
#
# 049 shipped on the belief that Anthropic does not break thinking out of
# output_tokens. It does: `usage.output_tokens_details.thinking_tokens`, a
# read-only breakdown, always <= output_tokens, present on non-streaming
# replies. So `reasoning_tokens` is populated for Anthropic as it already was
# for Gemini, and THAT is the figure for cost and for the thinking share.
#
# Which demotes `thinking_chars`: Anthropic returns thinking SUMMARIZED, and
# the billing is for the full reasoning, so characters off the blocks
# systematically understate -- and under `display: "omitted"` would read as zero
# on a reply that thought hard.

print("")
print("-- 5b. Anthropic reports its thinking tokens --")

_DOC_USAGE = {"input_tokens": 25, "output_tokens": 348,
              "output_tokens_details": {"thinking_tokens": 312}}
check("the shape the docs print maps straight through",
      llm_usage.normalize("anthropic", _DOC_USAGE)["reasoning_tokens"], 312)


class _Details:
    thinking_tokens = 312


class _Usage:
    input_tokens = 25
    output_tokens = 348
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens_details = _Details()


check("and so does the SDK object, read by attribute",
      llm_usage.normalize("anthropic", _Usage())["reasoning_tokens"], 312)
check("output_tokens stays the inclusive billing total",
      llm_usage.normalize("anthropic", _DOC_USAGE)["output_tokens"], 348)

# NULL and 0 again. A reply that genuinely did not think reports 0; a vendor or
# SDK version that does not carry the breakdown reports nothing, and reading the
# second as the first would say the fleet stopped thinking.
for label, usage, want in (
    ("no details key at all", {"input_tokens": 1, "output_tokens": 2}, None),
    ("details present but null",
     {"output_tokens": 2, "output_tokens_details": None}, None),
    ("thinking_tokens explicitly null",
     {"output_tokens": 2, "output_tokens_details": {"thinking_tokens": None}}, None),
    ("a genuine zero survives as zero",
     {"output_tokens": 2, "output_tokens_details": {"thinking_tokens": 0}}, 0),
):
    check(label, llm_usage.normalize("anthropic", usage)["reasoning_tokens"], want)

# Gemini's own path is untouched: thoughts sit BESIDE its output count and must
# still be added into it, or a thinking model is half-billed.
_gemini = llm_usage.normalize("gemini", {
    "prompt_token_count": 100, "cached_content_token_count": 10,
    "candidates_token_count": 50, "thoughts_token_count": 200})
check("gemini still adds thoughts into output", _gemini["output_tokens"], 250)
check("...and reports them as the reasoning share", _gemini["reasoning_tokens"], 200)


# ── 6. A node with no credentials says so once ───────────────────────────────
#
# `SupabaseManager()` raises ValueError when the URL or the service-role key is
# empty, and that cannot come right inside the process -- settings are read once
# at import. It was being retried per row and reported at the 1st, 10th, 100th
# and every 1000th, which reads as an intermittent database problem rather than
# "this node is not recording spend at all". Same shape as a missing table, so
# it gets the same treatment: disable, and say it once at ERROR.
#
# Found from a real WARNING in `document_pass_lab.py` output on a machine with
# no service-role key.

print("")
print("-- 6. No credentials --")

import db_handler  # noqa: E402

_NO_CREDS = ("Supabase URL and key must be provided either as arguments or via "
             "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables")


class _Row:
    def model_dump(self, **_kw):
        return {}


_saved_manager = db_handler.SupabaseManager
try:
    def _no_creds(*_a, **_k):
        raise ValueError(_NO_CREDS)

    db_handler.SupabaseManager = _no_creds
    writer = llm_usage._DatabaseWriter()
    for _ in range(3):
        writer.submit(_Row())
    writer.flush(timeout=5)
    check_true("a credential-less node disables the ledger", writer._disabled)
    check("...and counts no failures, because it is a state not a hiccup",
          writer._failures, 0)

    # A ValueError from somewhere else must stay transient: matched on the
    # message, not the type, so a real hiccup is still retried and counted.
    def _other_valueerror(*_a, **_k):
        raise ValueError("something else entirely")

    db_handler.SupabaseManager = _other_valueerror
    other = llm_usage._DatabaseWriter()
    other.submit(_Row())
    other.flush(timeout=5)
    check("an unrelated ValueError is still a counted failure",
          (other._disabled, other._failures), (False, 1))
finally:
    db_handler.SupabaseManager = _saved_manager


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all llm-usage checks passed")
