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
from typing import Callable, Optional

from util.llm_profiles import LLMCandidate, LLMProfile, LLMUnavailableError, llm_profiles
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_MAX_LOG_CHARS = 200  # Truncate prompt/response in DEBUG logs


# LLMUnavailableError is defined in util.llm_profiles (so the catalog can raise
# it on an unknown profile) and re-exported here — it is the error every LLM
# call site sees, and `from services.llm_service import LLMUnavailableError`
# keeps working.
__all__ = ["LLMService", "LLMUnavailableError", "llm_service"]


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
        )

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

    def _run(
        self,
        profile_name: str,
        system_prompt: str,
        user_message: str,
        image_base64: Optional[str] = None,
        image_media_type: Optional[str] = None,
    ) -> str:
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
        :return: Response text from the first candidate that succeeds.
        :rtype: str
        :raises LLMUnavailableError: If the profile is unknown or no candidate
            produced a response.
        """
        is_vision = image_base64 is not None
        handlers = self._vision_handlers if is_vision else self._text_handlers
        call_type = "vision" if is_vision else "text"
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
            try:
                if is_vision:
                    response = handler(profile, candidate, system_prompt, user_message, image_base64, image_media_type)
                else:
                    response = handler(profile, candidate, system_prompt, user_message)
                if not response or not response.strip():
                    raise LLMUnavailableError("empty response")
            except Exception as e:  # noqa: BLE001 — any failure means: try the next candidate
                last_error = e
                LOGGER.warning(
                    "LLMService: profile=%s position=%d vendor=%s model=%s failed (%s: %s) — failing over",
                    profile_name, position, candidate.vendor, candidate.model,
                    type(e).__name__, str(e),
                )
                continue

            if position > 1:
                LOGGER.info(
                    "LLMService: profile=%s served by fallback position=%d vendor=%s model=%s",
                    profile_name, position, candidate.vendor, candidate.model,
                )
            LOGGER.debug(
                "LLMService: profile=%s vendor=%s response=%.*s",
                profile_name, candidate.vendor, _MAX_LOG_CHARS, response,
            )
            return response

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

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=candidate.model,
            max_tokens=_max_tokens(profile, candidate),
            temperature=_temperature(profile, candidate),
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text  # type: ignore[attr-defined]

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
        from google.genai import types  # noqa: PLC0415

        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=candidate.model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=_temperature(profile, candidate),
                top_p=_top_p(profile, candidate),
            ),
        )
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

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=candidate.model,
            temperature=_temperature(profile, candidate),
            top_p=_top_p(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
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

        client = OpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )
        response = client.chat.completions.create(
            model=candidate.model,
            temperature=_temperature(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
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

        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        response = client.chat.completions.create(
            model=candidate.model,
            temperature=_temperature(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    # ── Vision implementations ────────────────────────────────────────────

    def _call_gemini_vision(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        """Call Gemini with an inline image part."""
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415
        import base64 as b64mod  # noqa: PLC0415

        client = genai.Client(api_key=settings.gemini_api_key)
        image_bytes = b64mod.b64decode(image_base64)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=image_media_type)
        response = client.models.generate_content(
            model=candidate.model,
            contents=[user_message, image_part],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=_temperature(profile, candidate),
            ),
        )
        return response.text  # type: ignore[attr-defined]

    def _call_anthropic_vision(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        """Call the Anthropic Messages API with a base64 image block."""
        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
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
        return response.content[0].text  # type: ignore[attr-defined]

    def _call_openai_vision(
        self, profile: LLMProfile, candidate: LLMCandidate, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        """Call the OpenAI Chat Completions API with an inline image URL."""
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=candidate.model,
            max_tokens=_max_tokens(profile, candidate),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_message},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image_media_type};base64,{image_base64}",
                    }},
                ]},
            ],
        )
        return response.choices[0].message.content or ""


# Module-level singleton — import this everywhere LLM calls are needed
llm_service = LLMService()
