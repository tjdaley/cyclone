"""
app/services/llm_service.py - Centralized multi-vendor LLM dispatch service.

ALL LLM calls in Cyclone go through this module. No other file imports an
LLM SDK directly. Vendor is selected from ``settings.llm_vendor``; use
``settings.llm_fast_vendor`` for latency-sensitive calls by passing
``use_fast_vendor=True``.
"""
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_MAX_LOG_CHARS = 200  # Truncate prompt/response in DEBUG logs


class LLMService:
    """
    Multi-vendor LLM completion service.

    Instantiated once at module level (``llm_service`` singleton). Route
    handlers and services call ``llm_service.complete()`` or
    ``llm_service.complete_fast()``.

    Supported vendors: ``anthropic``, ``gemini``, ``openai``, ``groq``, ``deepseek``.
    """

    def complete(self, system_prompt: str, user_message: str) -> str:
        """
        Dispatch a completion request to the configured primary LLM vendor.

        :param system_prompt: Instructions for the model (system role).
        :type system_prompt: str
        :param user_message: The user-facing input to process.
        :type user_message: str
        :return: Model response text.
        :rtype: str
        :raises ValueError: If the configured vendor is not supported.
        """
        return self._dispatch(settings.llm_vendor, system_prompt, user_message)

    def complete_fast(self, system_prompt: str, user_message: str) -> str:
        """
        Dispatch a completion request to the configured fast LLM vendor.

        Use for latency-sensitive paths such as real-time billing entry parse.

        :param system_prompt: Instructions for the model (system role).
        :type system_prompt: str
        :param user_message: The user-facing input to process.
        :type user_message: str
        :return: Model response text.
        :rtype: str
        """
        return self._dispatch(settings.llm_fast_vendor, system_prompt, user_message)

    def _dispatch(self, vendor: str, system_prompt: str, user_message: str) -> str:
        """
        Route to the appropriate vendor implementation.

        :param vendor: Vendor identifier string.
        :type vendor: str
        :param system_prompt: System instructions.
        :type system_prompt: str
        :param user_message: User message.
        :type user_message: str
        :return: Response text from the vendor.
        :rtype: str
        :raises ValueError: If ``vendor`` is not in the supported set.
        """
        LOGGER.debug(
            "LLMService._dispatch: vendor=%s prompt=%.*s",
            vendor,
            _MAX_LOG_CHARS,
            user_message,
        )
        dispatch_map = {
            "anthropic": self._call_anthropic,
            "gemini": self._call_gemini,
            "openai": self._call_openai,
            "groq": self._call_groq,
            "deepseek": self._call_deepseek,
        }
        handler = dispatch_map.get(vendor)
        if handler is None:
            raise ValueError("Unsupported LLM vendor: %s" % vendor)
        try:
            response = handler(system_prompt, user_message)
        except Exception as e:
            LOGGER.error("LLMService._dispatch: vendor=%s error: %s", vendor, str(e))
            raise
        LOGGER.debug(
            "LLMService._dispatch: vendor=%s response=%.*s",
            vendor,
            _MAX_LOG_CHARS,
            response,
        )
        return response

    # ── Vendor implementations ─────────────────────────────────────────────

    def _call_anthropic(self, system_prompt: str, user_message: str) -> str:
        """
        Call the Anthropic Messages API.

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
            model=settings.anthropic_model,
            max_tokens=2048,
            temperature=settings.llm_temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def _call_gemini(self, system_prompt: str, user_message: str) -> str:
        """
        Call the Google Gemini API via the google-genai SDK.

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
            model=settings.gemini_model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=settings.llm_temperature,
                top_p=settings.llm_top_p,
            ),
        )
        return response.text

    def _call_openai(self, system_prompt: str, user_message: str) -> str:
        """
        Call the OpenAI Chat Completions API.

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
            model=settings.openai_model,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    def _call_groq(self, system_prompt: str, user_message: str) -> str:
        """
        Call the Groq API (OpenAI-compatible interface).

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
            model=settings.groq_model,
            temperature=settings.llm_temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    def _call_deepseek(self, system_prompt: str, user_message: str) -> str:
        """
        Call the DeepSeek API (OpenAI-compatible interface).

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
            model=settings.deepseek_model,
            temperature=settings.llm_temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""


# Module-level singleton — import this everywhere LLM calls are needed
llm_service = LLMService()
