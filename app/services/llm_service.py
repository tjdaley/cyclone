"""
app/services/llm_service.py - Centralized multi-vendor LLM dispatch service.

ALL LLM calls in Cyclone go through this module. No other file imports an
LLM SDK directly.

Callers name **what the call is for** — never a vendor, never a model::

    llm_service.complete(system, msg, profile="analyze_pleading")
    llm_service.complete_with_image(system, msg, b64, mime, profile="ocr_document_page")

Each profile resolves through ``util/llm_profiles.py`` to an ordered failover
chain of vendor+model candidates. The service tries the first candidate; if the
call fails for any reason — network, quota, auth, API error, empty response —
it logs a warning and moves to the next candidate. When every candidate fails,
``LLMUnavailableError`` is raised. Naming a profile that the catalog does not
define raises ``LLMUnavailableError`` too: profile names are config, and a
typo should not silently run on some other model.

Which models serve a task is therefore a config edit
(``app/config/llm_profiles.json``), never a code change.
"""
import re
import time
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from services import llm_usage
from util.llm_profiles import LLMCandidate, LLMProfile, LLMUnavailableError, llm_profiles
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_MAX_LOG_CHARS = 200  # Truncate prompt/response in DEBUG logs

# THE SDKs RETRY UNDERNEATH US, AND IT MULTIPLIES THE DEADLINE.
#
# Both the Anthropic and OpenAI clients default to ``max_retries=2``
# (``anthropic/_constants.py``, ``openai/_constants.py``), and a timeout is in
# the set they retry. The deadline is applied *per attempt*, so an
# `extract_account_statement` profile asking for 300 seconds was really asking
# for 900 — measured exactly: one Chase card statement's `pages 5-8` chunk
# failed with `APITimeoutError` after **901,587 ms**, 3 x 300s plus connection
# setup, and that single dead call was fifteen of the job's twenty-three
# minutes.
#
# Three reasons it is zero rather than one:
#
#   * **The failover IS the retry, and it is the better one.** The chain moves
#     to a different vendor; an SDK retry asks the model that just failed the
#     same question again. Here the second candidate answered in 196 seconds,
#     after the first had burned 901 doing nothing.
#   * **It made the documented behaviour false.** This module's contract is "no
#     same-candidate retry" — true of the code and untrue of the process.
#   * **The retries were invisible and billable.** `llm_usage` records one row
#     per *our* attempt, and a timed-out call reports no usage at all, so three
#     generations of a ~34,000-token answer left a single row with NULL tokens.
#     Spend nothing on the Costs page can see is the one kind that cannot be
#     questioned.
#
# Gemini needs nothing here: with ``retry_options`` unset, google-genai's
# ``retry_args`` returns ``stop_after_attempt(1)``. The vendor last in every
# chain was the only one already honouring the deadline as written.
_MAX_RETRIES = 0


# LLMUnavailableError is defined in util.llm_profiles (so the catalog can raise
# it on an unknown profile) and re-exported here — it is the error every LLM
# call site sees, and `from services.llm_service import LLMUnavailableError`
# keeps working.
__all__ = ["LLMResult", "LLMService", "LLMUnavailableError", "llm_service"]


def _temperature(profile: LLMProfile, candidate: LLMCandidate) -> float:
    """Resolve the sampling temperature: candidate -> profile -> global."""
    if candidate.temperature is not None:
        return candidate.temperature
    return profile.temperature if profile.temperature is not None else settings.llm_temperature


def _top_p(profile: LLMProfile, candidate: LLMCandidate) -> float:
    """Resolve the nucleus-sampling cutoff: candidate -> profile -> global."""
    if candidate.top_p is not None:
        return candidate.top_p
    return profile.top_p if profile.top_p is not None else settings.llm_top_p


def _max_tokens(profile: LLMProfile, candidate: LLMCandidate) -> int:
    """Resolve the response token ceiling: candidate -> profile -> global."""
    if candidate.max_tokens is not None:
        return candidate.max_tokens
    return profile.max_tokens if profile.max_tokens is not None else settings.llm_max_tokens


class LLMResult(BaseModel):
    """
    A completion, plus which candidate actually produced it.

    The profile name alone says what was *asked for*; a chain has several
    vendors and any of them may have served the call. For work that becomes
    evidence that is not a detail — "which tool produced this figure" is a
    question with one right answer, and it is not "extract_account_statement".
    """

    text: str = Field(..., description="The completion")
    profile: str = Field(..., description="Profile the caller named")
    vendor: str = Field(..., description="Vendor that answered")
    model: str = Field(..., description="Model that answered")
    attempts: int = Field(
        ...,
        description="Candidates called before this one succeeded, inclusive. Above 1 means "
                    "the preferred model did not answer and the chain fell through",
    )

    def label(self) -> str:
        """``vendor/model``, for a summary line."""
        return "%s/%s" % (self.vendor, self.model)


def _anthropic_text(response: Any) -> str:
    """
    Pull the answer out of an Anthropic response.

    Not ``content[0].text``. A model with extended thinking returns its
    reasoning first, so the opening block is a ``ThinkingBlock`` with no
    ``text`` attribute at all — indexing straight into it raised
    ``AttributeError`` and the candidate was failed over as if the vendor had
    refused, when in fact the answer was sitting in the next block. Every
    Anthropic call silently fell through to the next vendor.

    Selecting by block type rather than position also covers the other blocks a
    response can carry — redacted thinking, tool use — without needing to know
    which are possible.

    :return: Every text block joined, or "" when the response carried none.
        ``_run`` treats empty as a failure, which is the right reading.
    :rtype: str
    """
    parts = [
        block.text for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "".join(parts).strip()


def _anthropic_takes_temperature(model: str) -> bool:
    """
    Whether an Anthropic model still accepts ``temperature``.

    The 5.x family rejects it. Testing for ``"-5" in model`` is the obvious
    reading and the wrong one: ``claude-haiku-4-5-20251001`` contains ``-5`` and
    is a 4.5 model that takes temperature perfectly well. Dropping it there
    would not error — it would quietly leave the sampling at the vendor default
    on exactly the tasks that ask for 0.0, so a guardrail or a triage call would
    stop being deterministic and nothing would say so.

    So the *major* version decides: the first number after the family name.
    ``claude-opus-5`` is 5, ``claude-haiku-4-5-20251001`` is 4.
    """
    numbers = re.findall(r"\d+", model)
    return not numbers or int(numbers[0]) < 5


#: Thinking levels, weakest first. OpenAI takes all five; Gemini 4 stops at
#: `high`, so a profile asking for more is clamped there rather than refused —
#: the effort is a property of the TASK, and one vendor's shorter scale is not a
#: reason for the chain to lose its last candidate.
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_GEMINI_MAX_EFFORT = "high"

#: Gemini 4 Argon caps a synchronous response at this, thinking included. Above
#: it the vendor requires background execution, which this service does not
#: implement — so a profile asking for more is clamped, not sent. Unclamped it
#: is at best ignored and at worst refused, and both read as a vendor fault.
_GEMINI_SYNC_MAX_OUTPUT = 65_536


def _gemini_config(
    types_module: Any,
    profile: LLMProfile,
    candidate: LLMCandidate,
    system_prompt: str,
    fine_detail: bool = False,
) -> Any:
    """
    Build a `GenerateContentConfig` for one candidate.

    Three things this fixes, all of which were silent:

    * **`max_output_tokens` was never sent at all**, so a profile's ceiling
      reached Anthropic and OpenAI and not Gemini — `extract_account_statement`
      asks for 128,000 and Gemini was answering under whatever its own default
      was. On Gemini 4 that budget covers THINKING as well as the answer, so an
      unset one is how a long extraction gets cut off mid-register.
    * **Sampling is omitted where the model ignores it**, so the catalog stops
      claiming a determinism it does not get.
    * **`thinking_level` is sent where a profile asks for one**, clamped to the
      vendor's ceiling.
    """
    config: dict[str, Any] = {"system_instruction": system_prompt}
    if _sends_sampling(profile, candidate):
        config["temperature"] = _temperature(profile, candidate)
        config["top_p"] = _top_p(profile, candidate)

    # Only an EXPLICIT ceiling, never the global default. That default never
    # reached Gemini — this call simply did not send the field — so adopting it
    # now would be a silent cut: `ocr_document_page` would have gone from the
    # vendor's 65,536 to the global 16,384, which is also below the 32,768 floor
    # Gemini's own migration guidance advises. Unset stays unset, and the
    # vendor's default applies as it always has.
    ceiling = candidate.max_tokens or profile.max_tokens
    if ceiling:
        config["max_output_tokens"] = min(ceiling, _GEMINI_SYNC_MAX_OUTPUT)

    effort = _reasoning_effort(profile, candidate)
    if effort:
        config["thinking_config"] = types_module.ThinkingConfig(
            thinking_level=_gemini_effort(effort))

    # Only where there is an image to resolve. Gemini's guidance is that a
    # higher media resolution helps fine text and small details, which is the
    # whole job when the pages are a scanned register — and it buys nothing at
    # all on a text-only call, where it would only cost tokens.
    if fine_detail:
        config["media_resolution"] = types_module.MediaResolution.MEDIA_RESOLUTION_HIGH
    return types_module.GenerateContentConfig(**config)


def _gemini_effort(effort: str) -> str:
    """A profile's effort on Gemini's shorter scale — `xhigh` and `max` are `high`."""
    if effort in _EFFORTS and _EFFORTS.index(effort) > _EFFORTS.index(_GEMINI_MAX_EFFORT):
        return _GEMINI_MAX_EFFORT
    return effort


def _openai_sampling(profile: LLMProfile, candidate: LLMCandidate) -> dict[str, Any]:
    """
    The optional OpenAI keyword arguments: sampling where allowed, effort where set.

    Built as a dict and splatted so an unset value is **absent** rather than
    `None`. The SDK forwards an explicit `temperature=None` as a field in the
    request body, which is exactly the rejection this avoids.
    """
    extra: dict[str, Any] = {}
    if _sends_sampling(profile, candidate):
        extra["temperature"] = _temperature(profile, candidate)
        extra["top_p"] = _top_p(profile, candidate)
    extra.update(_openai_effort(profile, candidate))
    return extra


def _openai_effort(profile: LLMProfile, candidate: LLMCandidate) -> dict[str, Any]:
    """`reasoning_effort`, or nothing at all. Flat on chat.completions."""
    effort = _reasoning_effort(profile, candidate)
    return {"reasoning_effort": effort} if effort else {}


def _reasoning_effort(profile: LLMProfile, candidate: LLMCandidate) -> Optional[str]:
    """How hard to think: candidate -> profile -> the vendor's own default."""
    if candidate.reasoning_effort is not None:
        return candidate.reasoning_effort
    return profile.reasoning_effort


def _sends_sampling(profile: LLMProfile, candidate: LLMCandidate) -> bool:
    """
    Whether ``temperature`` and ``top_p`` may be SENT to this candidate.

    **A reasoning model does not take them, and the two vendors disagree about
    how it says so.** OpenAI's migration guidance is explicit — "when reasoning
    effort is not `none`, remove `temperature`, `top_p` and `top_logprobs`" —
    and `gpt-6.1-sol` does not support an effort of `none`, so there is no
    setting that makes them acceptable; sent anyway they come back 400. Gemini 4
    Argon's guidance says to remove the same parameters because they are
    **ignored**, which is the worse of the two failures: nothing raises, and a
    profile that asked for `temperature: 0.0` quietly samples.

    **Declared per candidate rather than inferred from the model name.** Every
    chain here is one task served by three vendors, so a profile-level answer
    cannot express it — and sniffing the name is the mistake
    `_anthropic_takes_temperature` exists to document, where testing `"-5" in
    model` silently matched `claude-haiku-4-5` and dropped temperature on tasks
    that asked for 0.0.
    """
    if candidate.sampling is not None:
        return candidate.sampling
    return True


def _timeout(profile: LLMProfile, candidate: LLMCandidate) -> float:
    """
    Resolve the request deadline: candidate -> profile -> global.

    Worth its own knob rather than one global number, because the cost of
    getting it wrong is not what it looks like. A deadline too short for the
    work does not simply fail — it *selects for the model that gives up
    soonest*, since a short answer is the only kind that fits inside it. A
    27-page statement extraction proved that twice: the run that "succeeded"
    returned 38 of 286 transactions inside 90 seconds, and the run where every
    vendor tried to do the job properly was killed at 90 seconds three times
    over.
    """
    if candidate.timeout_seconds is not None:
        return candidate.timeout_seconds
    if profile.timeout_seconds is not None:
        return profile.timeout_seconds
    return settings.llm_timeout_seconds


def _gemini_timeout_ms(seconds: float) -> int:
    """
    google-genai takes its request timeout in milliseconds, unlike the others.

    It also enforces the deadline server-side rather than in the client, so an
    overrun comes back as a structured ``504 DEADLINE_EXCEEDED`` from Google
    instead of a client timeout — the same event, wearing different clothes
    from Anthropic's and OpenAI's.

    Floored at 10s because Gemini rejects a shorter deadline outright
    ("Manually set deadline 1s is too short") — which would turn a tight
    timeout setting into an immediate INVALID_ARGUMENT on every Gemini call
    rather than the timeout the setting asked for.
    """
    return max(int(seconds * 1000), 10_000)


class LLMService:
    """
    Multi-vendor LLM completion service with per-profile failover.

    Instantiated once at module level (``llm_service`` singleton).

    Supported vendors: ``anthropic``, ``gemini``, ``openai``, ``groq``,
    ``deepseek``. Vision is supported on ``anthropic``, ``gemini``, and
    ``openai`` only — candidates naming any other vendor are skipped on
    multimodal calls.
    """

    def __init__(self) -> None:
        """Bind vendor ids to their call implementations."""
        self._text_handlers: dict[str, Callable[..., str]] = {
            "anthropic": self._call_anthropic,
            "gemini": self._call_gemini,
            "openai": self._call_openai,
            "groq": self._call_groq,
            "deepseek": self._call_deepseek,
        }
        self._vision_handlers: dict[str, Callable[..., str]] = {
            "anthropic": self._call_anthropic_vision,
            "gemini": self._call_gemini_vision,
            "openai": self._call_openai_vision,
        }
        # A THIRD CALL TYPE: hand over the PDF and let the vendor read its own
        # pages. Measured against the chunked pipeline on a 10-page, 196-line
        # Chase card statement: identical on every amount, date and page
        # number, and RIGHT on the one figure the chunked read could not make
        # out — because that page's Adobe OCR layer had dropped a decimal point
        # and the page scored 0.61 against the 0.60 word-like bar, so it was
        # trusted. One request where the chunked path made fourteen.
        self._document_handlers: dict[str, Callable[..., str]] = {
            "anthropic": self._call_anthropic_document,
            "gemini": self._call_gemini_document,
            "openai": self._call_openai_document,
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def complete(self, system_prompt: str, user_message: str, profile: str = "default") -> str:
        """
        Dispatch a text completion through a task profile's failover chain.

        :param system_prompt: Instructions for the model (system role).
        :type system_prompt: str
        :param user_message: The user-facing input to process.
        :type user_message: str
        :param profile: Task profile name from the catalog, e.g. 'analyze_pleading'.
        :type profile: str
        :return: Model response text.
        :rtype: str
        :raises LLMUnavailableError: If the profile is unknown, or every
            candidate in its chain failed.
        """
        return self._run(profile, system_prompt, user_message).text

    def complete_detailed(
        self,
        system_prompt: str,
        user_message: str,
        profile: str = "default",
    ) -> LLMResult:
        """
        Complete, and report which candidate actually answered.

        Same call as :meth:`complete`; use this where the answer becomes part of
        a record that has to say how it was produced. The profile name is not
        enough on its own — a chain holds several vendors, and which one served
        a given call depends on what was failing that minute.

        :return: The response, the vendor and model behind it, and how many
            candidates were tried to get there.
        :rtype: LLMResult
        :raises LLMUnavailableError: If the profile is unknown, or every
            candidate in its chain failed.
        """
        return self._run(profile, system_prompt, user_message)

    def complete_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image_base64: str,
        image_media_type: str = "image/png",
        profile: str = "vision",
    ) -> str:
        """
        Dispatch a multimodal completion through a task profile's chain.

        Used for OCR of scanned document pages via the LLM's vision capability.
        Candidates whose vendor has no vision support are skipped.

        :param system_prompt: System instructions.
        :type system_prompt: str
        :param user_message: Text prompt accompanying the image.
        :type user_message: str
        :param image_base64: Base64-encoded image data.
        :type image_base64: str
        :param image_media_type: MIME type of the image (e.g. 'image/png').
        :type image_media_type: str
        :param profile: Task profile name, e.g. 'ocr_document_page'.
        :type profile: str
        :return: Model response text.
        :rtype: str
        :raises LLMUnavailableError: If the profile is unknown, or every
            vision-capable candidate failed.
        """
        return self._run(
            profile,
            system_prompt,
            user_message,
            image_base64=image_base64,
            image_media_type=image_media_type,
        ).text

    def describe_profiles(self) -> str:
        """
        Render the catalog as a single log-safe line.

        :return: e.g. ``analyze_pleading=[anthropic:claude-opus-4-6, ...] fast=[...]``
        :rtype: str
        """
        return llm_profiles.describe()

    def validate_profiles(self) -> list[str]:
        """
        Check the catalog for problems, without calling any vendor.

        Catches a typo'd vendor id, a vendor with no API key, a non-vision
        vendor in a vision profile, and profiles with nothing usable left.

        :return: Human-readable problem descriptions; empty when all is well.
        :rtype: list[str]
        """
        return llm_profiles.problems(set(self._text_handlers), set(self._vision_handlers))

    # ── Failover engine ────────────────────────────────────────────────────

    def complete_with_document(
        self,
        system_prompt: str,
        user_message: str,
        pdf_bytes: bytes,
        filename: str = "document.pdf",
        profile: str = "vision",
    ) -> LLMResult:
        """
        Hand the whole PDF to the model and let it read its own pages.

        **The alternative to rendering every page ourselves.** `extract_text`
        rasterizes each page at 300 DPI and spends one vision call on it, then
        the statement walk spends more on the text — fourteen sequential round
        trips on a ten-page statement. A document block is one.

        It also removes a whole class of question: the page is never rasterized
        by us, so the DPI, the tiling, and `_enhance_image` stop mattering for
        a statement read this way.

        Returns ``LLMResult`` rather than a bare string because a document pass
        becomes part of a record that has to say how it was produced, and on a
        failover chain the answer may come from any vendor.

        :param pdf_bytes: The PDF itself. Vendors cap page count and size —
            check before sending a production rather than a statement.
        :param filename: Shown to the vendors that accept one; OpenAI requires it.
        :param profile: Defaults to ``vision``, whose three vendors all accept
            PDFs. Name a task profile for real work, as everywhere else.
        """
        return self._run(profile, system_prompt, user_message,
                         document_bytes=pdf_bytes, document_name=filename)

    def _run(
        self,
        profile_name: str,
        system_prompt: str,
        user_message: str,
        image_base64: Optional[str] = None,
        image_media_type: Optional[str] = None,
        document_bytes: Optional[bytes] = None,
        document_name: str = "document.pdf",
    ) -> LLMResult:
        """
        Walk a profile's chain until a candidate returns a non-empty response.

        A candidate is skipped without being called when its vendor cannot
        serve this call type or has no API key. A candidate that raises, or
        that returns an empty response, triggers failover to the next one.

        :param profile_name: Profile whose chain to walk.
        :type profile_name: str
        :param system_prompt: System instructions.
        :type system_prompt: str
        :param user_message: User message.
        :type user_message: str
        :param image_base64: Base64 image data; presence selects the vision handlers.
        :type image_base64: Optional[str]
        :param image_media_type: MIME type of the image.
        :type image_media_type: Optional[str]
        :return: The response, and which candidate produced it.
        :rtype: LLMResult
        :raises LLMUnavailableError: If the profile is unknown or no candidate
            produced a response.
        """
        # Three call types, and the ledger records which — so the cost of a
        # document pass can be compared against the chunked path it replaces
        # rather than guessed at.
        is_document = document_bytes is not None
        is_vision = image_base64 is not None
        if is_document:
            handlers, call_type = self._document_handlers, "document"
        elif is_vision:
            handlers, call_type = self._vision_handlers, "vision"
        else:
            handlers, call_type = self._text_handlers, "text"
        profile = llm_profiles.get(profile_name)   # raises on an unknown name
        chain = profile.chain

        last_error: Optional[Exception] = None
        attempted = 0

        for position, candidate in enumerate(chain, start=1):
            handler = handlers.get(candidate.vendor)
            if handler is None:
                LOGGER.warning(
                    "LLMService: profile=%s position=%d vendor=%s cannot serve %s calls — skipping",
                    profile_name, position, candidate.vendor, call_type,
                )
                continue
            if not settings.llm_api_key(candidate.vendor):
                LOGGER.warning(
                    "LLMService: profile=%s position=%d vendor=%s has no API key — skipping",
                    profile_name, position, candidate.vendor,
                )
                continue

            attempted += 1
            LOGGER.debug(
                "LLMService: profile=%s position=%d vendor=%s model=%s prompt=%.*s",
                profile_name, position, candidate.vendor, candidate.model,
                _MAX_LOG_CHARS, user_message,
            )
            # EVERY ATTEMPT IS RECORDED, including the ones that fail over. A
            # call that timed out after the vendor started generating, or whose
            # reply came back empty, is billed all the same, and a ledger of
            # successes only would understate spend exactly when the chain is
            # misbehaving. Recording itself never raises (llm_usage).
            llm_usage.begin_attempt()
            started_at = llm_usage.now()
            clock = time.monotonic()

            def _record(succeeded: bool, error: Optional[BaseException] = None) -> None:
                llm_usage.record_attempt(
                    profile=profile_name, call_type=call_type,
                    vendor=candidate.vendor, model=candidate.model,
                    chain_position=position, succeeded=succeeded, error=error,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - clock) * 1000),
                )

            try:
                if is_document:
                    response = handler(profile, candidate, system_prompt, user_message,
                                       document_bytes, document_name)
                elif is_vision:
                    response = handler(profile, candidate, system_prompt, user_message, image_base64, image_media_type)
                else:
                    response = handler(profile, candidate, system_prompt, user_message)
                if not response or not response.strip():
                    raise LLMUnavailableError("empty response")
            except Exception as e:  # noqa: BLE001 — any failure means: try the next candidate
                last_error = e
                _record(False, e)
                LOGGER.warning(
                    "LLMService: profile=%s position=%d vendor=%s model=%s failed (%s: %s) — failing over",
                    profile_name, position, candidate.vendor, candidate.model,
                    type(e).__name__, str(e),
                )
                continue

            _record(True)

            if position > 1:
                LOGGER.info(
                    "LLMService: profile=%s served by fallback position=%d vendor=%s model=%s",
                    profile_name, position, candidate.vendor, candidate.model,
                )
            LOGGER.debug(
                "LLMService: profile=%s vendor=%s response=%.*s",
                profile_name, candidate.vendor, _MAX_LOG_CHARS, response,
            )
            return LLMResult(
                text=response,
                profile=profile_name,
                vendor=candidate.vendor,
                model=candidate.model,
                attempts=attempted,
            )

        message = "All %d candidate(s) in LLM profile '%s' failed for a %s call (%d attempted)" % (
            len(chain), profile_name, call_type, attempted,
        )
        LOGGER.error("LLMService: %s", message)
        raise LLMUnavailableError(message) from last_error

    # ── Vendor implementations ─────────────────────────────────────────────

    def _call_anthropic(self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str) -> str:
        """
        Call the Anthropic Messages API.

        :param profile: Resolved profile, supplying sampling defaults.
        :type profile: LLMProfile
        :param candidate: Vendor+model configuration to use.
        :type candidate: LLMCandidate
        :param system_prompt: System prompt text.
        :type system_prompt: str
        :param user_message: User message text.
        :type user_message: str
        :return: Response content text.
        :rtype: str
        """
        import anthropic  # noqa: PLC0415 — imported lazily to avoid load cost when not in use
        timeout = _timeout(profile, candidate)

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout,
                                     max_retries=_MAX_RETRIES)

        params: dict[str, str | int | float | list[dict[str, str]]] = {
            "model": candidate.model,
            "max_tokens": _max_tokens(profile, candidate),
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        if _anthropic_takes_temperature(candidate.model):
            params["temperature"] = _temperature(profile, candidate) or 0.0

        response = client.messages.create(**params)  # type: ignore[arg-type]
        llm_usage.note_response("anthropic", response)
        return _anthropic_text(response)

    def _call_gemini(self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str) -> str:
        """
        Call the Google Gemini API via the google-genai SDK.

        :param profile: Resolved profile, supplying sampling defaults.
        :type profile: LLMProfile
        :param candidate: Vendor+model configuration to use.
        :type candidate: LLMCandidate
        :param system_prompt: System prompt text.
        :type system_prompt: str
        :param user_message: User message text.
        :type user_message: str
        :return: Response text.
        :rtype: str
        """
        from google import genai  # noqa: PLC0415
        timeout = _timeout(profile, candidate)
        from google.genai import types  # noqa: PLC0415

        client = genai.Client(api_key=settings.gemini_api_key,
                              http_options=types.HttpOptions(timeout=_gemini_timeout_ms(timeout)))
        response = client.models.generate_content(
            model=candidate.model,
            contents=user_message,
            config=_gemini_config(types, profile, candidate, system_prompt),
        )
        llm_usage.note_response("gemini", response)
        return response.text  # type: ignore[attr-defined]

    def _call_openai(self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str) -> str:
        """
        Call the OpenAI Chat Completions API.

        :param profile: Resolved profile, supplying sampling defaults.
        :type profile: LLMProfile
        :param candidate: Vendor+model configuration to use.
        :type candidate: LLMCandidate
        :param system_prompt: System prompt text.
        :type system_prompt: str
        :param user_message: User message text.
        :type user_message: str
        :return: Response content text.
        :rtype: str
        """
        from openai import OpenAI  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = OpenAI(api_key=settings.openai_api_key, timeout=timeout,
                        max_retries=_MAX_RETRIES)
        response = client.chat.completions.create(
            model=candidate.model,
            # max_completion_tokens, not max_tokens — chat.completions rejects
            # the old name outright, which is how every OpenAI call came to fail
            # over. Dropping the ceiling entirely fixes the error and replaces
            # it with a quieter problem: a profile asking for 32,000 tokens
            # would silently get the vendor default and truncate a long answer.
            max_completion_tokens=_max_tokens(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            # Sent only where the model takes them: a reasoning model answers a
            # request carrying temperature with a 400, and the failover would
            # dress that up as "OpenAI is down" while Gemini served every call.
            **_openai_sampling(profile, candidate),
        )
        llm_usage.note_response("openai", response)
        return response.choices[0].message.content or ""

    def _call_groq(self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str) -> str:
        """
        Call the Groq API (OpenAI-compatible interface).

        :param profile: Resolved profile, supplying sampling defaults.
        :type profile: LLMProfile
        :param candidate: Vendor+model configuration to use.
        :type candidate: LLMCandidate
        :param system_prompt: System prompt text.
        :type system_prompt: str
        :param user_message: User message text.
        :type user_message: str
        :return: Response content text.
        :rtype: str
        """
        from openai import OpenAI  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = OpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            timeout=timeout,
            max_retries=_MAX_RETRIES,
        )
        response = client.chat.completions.create(
            model=candidate.model,
            temperature=_temperature(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        llm_usage.note_response("groq", response)
        return response.choices[0].message.content or ""

    def _call_deepseek(self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str) -> str:
        """
        Call the DeepSeek API (OpenAI-compatible interface).

        :param profile: Resolved profile, supplying sampling defaults.
        :type profile: LLMProfile
        :param candidate: Vendor+model configuration to use.
        :type candidate: LLMCandidate
        :param system_prompt: System prompt text.
        :type system_prompt: str
        :param user_message: User message text.
        :type user_message: str
        :return: Response content text.
        :rtype: str
        """
        from openai import OpenAI  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=timeout,
            max_retries=_MAX_RETRIES,
        )
        response = client.chat.completions.create(
            model=candidate.model,
            temperature=_temperature(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        llm_usage.note_response("deepseek", response)
        return response.choices[0].message.content or ""

    # ── Vision implementations ────────────────────────────────────────────

    def _call_gemini_vision(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        """Call Gemini with an inline image part."""
        from google import genai  # noqa: PLC0415
        timeout = _timeout(profile, candidate)
        from google.genai import types  # noqa: PLC0415
        import base64 as b64mod  # noqa: PLC0415

        client = genai.Client(api_key=settings.gemini_api_key,
                              http_options=types.HttpOptions(timeout=_gemini_timeout_ms(timeout)))
        image_bytes = b64mod.b64decode(image_base64)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=image_media_type)
        response = client.models.generate_content(
            model=candidate.model,
            contents=[user_message, image_part],
            config=_gemini_config(types, profile, candidate, system_prompt),
        )
        llm_usage.note_response("gemini", response)
        return response.text  # type: ignore[attr-defined]

    def _call_anthropic_vision(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        """Call the Anthropic Messages API with a base64 image block."""
        import anthropic  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout,
                                     max_retries=_MAX_RETRIES)
        response = client.messages.create(
            model=candidate.model,
            max_tokens=_max_tokens(profile, candidate),
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_media_type,
                            "data": image_base64,
                        },
                    }, # type: ignore
                    {"type": "text", "text": user_message},
                ],
            }],
        )
        llm_usage.note_response("anthropic", response)
        return _anthropic_text(response)

    # ── Document (whole-PDF) implementations ──────────────────────────────
    #
    # THE ATTACHMENT GOES BEFORE THE TEXT in all three. Anthropic's guidance is
    # explicit that it works best that way, and the others behave the same: the
    # question should arrive after the thing it is about.

    def _call_anthropic_document(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str,
        user_message: str, pdf_bytes: bytes, filename: str,
    ) -> str:
        """Call Anthropic with the PDF as a document block."""
        import base64 as b64mod  # noqa: PLC0415
        import anthropic  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout,
                                     max_retries=_MAX_RETRIES)
        params: dict[str, Any] = {
            "model": candidate.model,
            "max_tokens": _max_tokens(profile, candidate),
            "system": system_prompt,
            "messages": [{"role": "user", "content": [
                {"type": "document", "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64mod.b64encode(pdf_bytes).decode("ascii"),
                }},
                {"type": "text", "text": user_message},
            ]}],
        }
        if _anthropic_takes_temperature(candidate.model):
            params["temperature"] = _temperature(profile, candidate) or 0.0
        response = client.messages.create(**params)  # type: ignore[arg-type]
        llm_usage.note_response("anthropic", response)
        return _anthropic_text(response)

    def _call_openai_document(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str,
        user_message: str, pdf_bytes: bytes, filename: str,
    ) -> str:
        """Call OpenAI with the PDF as a `file` content part."""
        import base64 as b64mod  # noqa: PLC0415
        from openai import OpenAI  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = OpenAI(api_key=settings.openai_api_key, timeout=timeout,
                        max_retries=_MAX_RETRIES)
        encoded = b64mod.b64encode(pdf_bytes).decode("ascii")
        response = client.chat.completions.create(
            model=candidate.model,
            max_completion_tokens=_max_tokens(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    # filename is required here, unlike the other two vendors.
                    {"type": "file", "file": {
                        "filename": filename,
                        "file_data": "data:application/pdf;base64,%s" % encoded,
                    }},
                    {"type": "text", "text": user_message},
                ]},
            ],
            # Effort only — a reasoning model refuses sampling (see _sends_sampling).
            **_openai_effort(profile, candidate),
        )
        llm_usage.note_response("openai", response)
        return response.choices[0].message.content or ""

    def _call_gemini_document(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str,
        user_message: str, pdf_bytes: bytes, filename: str,
    ) -> str:
        """Call Gemini with the PDF as an inline part."""
        from google import genai  # noqa: PLC0415
        timeout = _timeout(profile, candidate)
        from google.genai import types  # noqa: PLC0415

        client = genai.Client(api_key=settings.gemini_api_key,
                              http_options=types.HttpOptions(timeout=_gemini_timeout_ms(timeout)))
        part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        response = client.models.generate_content(
            model=candidate.model,
            contents=[part, user_message],
            # media_resolution HIGH, and only on this path: reading small print
            # off a scanned register IS the job here, and Gemini's own guidance
            # is that a higher setting helps fine text. On a text call there is
            # no image to resolve, so it would buy nothing.
            config=_gemini_config(types, profile, candidate, system_prompt, fine_detail=True),
        )
        llm_usage.note_response("gemini", response)
        return response.text  # type: ignore[attr-defined]

    def _call_openai_vision(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        """Call the OpenAI Chat Completions API with an inline image URL."""
        from openai import OpenAI  # noqa: PLC0415
        timeout = _timeout(profile, candidate)

        client = OpenAI(api_key=settings.openai_api_key, timeout=timeout,
                        max_retries=_MAX_RETRIES)
        response = client.chat.completions.create(
            model=candidate.model,
            max_completion_tokens=_max_tokens(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_message},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image_media_type};base64,{image_base64}",
                    }},
                ]},
            ],
            # Effort only. This path has never sent sampling — which is why it
            # was the one OpenAI call a reasoning model still accepted — and
            # starting now would be an unrequested change to a path that works.
            **_openai_effort(profile, candidate),
        )
        llm_usage.note_response("openai", response)
        return response.choices[0].message.content or ""


# Module-level singleton — import this everywhere LLM calls are needed
llm_service = LLMService()
