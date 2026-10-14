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


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all llm-usage checks passed")
