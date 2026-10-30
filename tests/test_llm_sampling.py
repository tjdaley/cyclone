"""
tests/test_llm_sampling.py - What actually reaches each vendor.

**A REASONING MODEL DOES NOT TAKE `temperature` OR `top_p`, AND THE TWO VENDORS
DISAGREE ABOUT HOW IT SAYS SO.** OpenAI's own migration guidance for
`gpt-6.1-sol`: remove `temperature`, `top_p` and `top_logprobs` whenever
reasoning effort is not `none` — and that model does not support `none`, so no
setting makes them acceptable and a request carrying them is refused. Gemini 4
Argon's guidance says to remove the same parameters because they are **ignored**,
which is the worse failure: nothing raises, and a profile asking for
`temperature: 0.0` quietly samples.

Why it needs a test rather than a careful edit: the failover hides exactly this.
Every OpenAI text call would have come back 400, been logged as one WARNING, and
handed the work to Gemini — the vendor that sits last precisely because its
filters decline this firm's ordinary evidence. "When one vendor is always
serving, suspect the ones ahead of it in the chain."

Run:  venv/Scripts/python.exe tests/test_llm_sampling.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from google.genai import types  # noqa: E402
from util.llm_profiles import VALID_EFFORTS, LLMCandidate, LLMProfile, llm_profiles  # noqa: E402
from services.llm_service import (  # noqa: E402
    _GEMINI_SYNC_MAX_OUTPUT, _gemini_config, _gemini_effort, _openai_effort,
    _openai_sampling, _sends_sampling, llm_service,
)

FAILURES: list[str] = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def profile(**kwargs):
    base = {"name": "t", "chain": [], "temperature": 0.0, "top_p": 0.1}
    base.update(kwargs)
    return LLMProfile(**base)


print("1. the catalog's reasoning models are declared, not sniffed")
# Per CANDIDATE: one chain is one task served by three vendors, so a
# profile-level answer cannot express it — and sniffing the model name is the
# mistake `_anthropic_takes_temperature` exists to document.
for name in ("default", "vision"):
    chain = {c.vendor: c for c in llm_profiles.get(name).chain}
    check("%s: openai declares no sampling" % name, chain["openai"].sampling, False)
    check("%s: gemini declares no sampling" % name, chain["gemini"].sampling, False)
    # Anthropic is untouched: opus-5 drops temperature through its own
    # version-aware guard, and top_p it accepts.
    check("%s: anthropic is left alone" % name, chain["anthropic"].sampling, None)

fast = {c.vendor: c for c in llm_profiles.get("fast").chain}
check("the fast chain is unchanged, so its models still get sampling",
      [c.sampling for c in llm_profiles.get("fast").chain], [None, None, None])

print("")
print("2. OpenAI: the parameters that would be refused are ABSENT, not None")
# Absent rather than None because the SDK forwards an explicit temperature=None
# as a field in the request body — which is the rejection this avoids.
reasoning = LLMCandidate(vendor="openai", model="gpt-6.1-sol", sampling=False)
ordinary = LLMCandidate(vendor="openai", model="gpt-5.4-nano")
check("nothing at all for a reasoning model",
      _openai_sampling(profile(), reasoning), {})
check("and the keys are not merely null", "temperature" in _openai_sampling(profile(), reasoning), False)
check("an ordinary model still gets both",
      _openai_sampling(profile(), ordinary), {"temperature": 0.0, "top_p": 0.1})

print("")
print("3. effort is sent when a profile asks and omitted when it does not")
check("unset sends nothing, so the vendor's own default (medium) applies",
      _openai_effort(profile(), reasoning), {})
check("set sends the flat chat.completions spelling",
      _openai_effort(profile(reasoning_effort="high"), reasoning),
      {"reasoning_effort": "high"})
check("a candidate overrides its profile",
      _openai_effort(profile(reasoning_effort="low"),
                     LLMCandidate(vendor="openai", model="m", reasoning_effort="max")),
      {"reasoning_effort": "max"})
check("and it rides alongside sampling where both apply",
      sorted(_openai_sampling(profile(reasoning_effort="low"), ordinary)),
      ["reasoning_effort", "temperature", "top_p"])

print("")
print("4. Gemini: ignored parameters are not sent, and the ceiling now IS")
gem = LLMCandidate(vendor="gemini", model="gemini-4-argon", sampling=False)
cfg = _gemini_config(types, profile(max_tokens=128_000), gem, "sys")
check("no temperature", cfg.temperature, None)
check("no top_p", cfg.top_p, None)
# It was never sent at all, so a profile's ceiling reached Anthropic and OpenAI
# and not Gemini. On Gemini 4 that budget covers THINKING as well as the answer,
# so an unset one is how a long extraction gets cut off mid-register.
check("an explicit ceiling is clamped to the synchronous maximum",
      cfg.max_output_tokens, _GEMINI_SYNC_MAX_OUTPUT)
check("a ceiling under the cap passes through",
      _gemini_config(types, profile(max_tokens=32_768), gem, "s").max_output_tokens, 32_768)
# The global default never reached Gemini either. Adopting it now would be a
# silent CUT — ocr_document_page would drop from the vendor's 65,536 to the
# global 16,384, itself below the 32,768 floor Gemini's guidance advises.
check("no explicit ceiling sends nothing, not the global default",
      _gemini_config(types, profile(), gem, "s").max_output_tokens, None)

print("")
print("5. thinking_level, on Gemini's shorter scale")
check("sent where asked",
      _gemini_config(types, profile(reasoning_effort="low"), gem, "s").thinking_config,
      types.ThinkingConfig(thinking_level="low"))
check("omitted where not", _gemini_config(types, profile(), gem, "s").thinking_config, None)
# Gemini stops at `high`; clamping keeps the chain's last candidate rather than
# losing it to a value only OpenAI understands.
check("xhigh and max clamp to high",
      [_gemini_effort(e) for e in ("low", "medium", "high", "xhigh", "max")],
      ["low", "medium", "high", "high", "high"])

print("")
print("6. a real profile, end to end")
extract = llm_profiles.get("extract_account_statement")
sent = {c.vendor: (_openai_sampling(extract, c) if c.vendor == "openai"
                   else _gemini_config(types, extract, c, "s") if c.vendor == "gemini"
                   else "unchanged")
        for c in extract.chain}
check("the profile still asks for determinism", extract.temperature, 0.0)
check("but OpenAI is sent nothing it would refuse", sent["openai"], {})
check("and Gemini gets the ceiling it was never given",
      sent["gemini"].max_output_tokens, _GEMINI_SYNC_MAX_OUTPUT)

print("")
print("7. a bad effort value is REPORTED at startup, never raised")
# Reported like every other catalog problem: a wrong effort costs one vendor's
# candidate, and refusing to start the process over it would cost all of them.
check("the valid set is the five both vendors can be mapped onto",
      VALID_EFFORTS, ("low", "medium", "high", "xhigh", "max"))
check("'none' is not offered — no model in these chains supports it, and it is "
      "the only setting under which OpenAI would still take temperature",
      "none" in VALID_EFFORTS, False)
check_true("the live catalog reports no effort problem",
           not [p for p in llm_service.validate_profiles() if "reasoning_effort" in p])

print("")
print("FAILURES: %d" % len(FAILURES))
for name in FAILURES:
    print("  - " + name)
sys.exit(1 if FAILURES else 0)
