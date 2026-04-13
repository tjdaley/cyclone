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
        model = getattr(settings, f"{settings.llm_vendor}_model", None)
        if not model:
            LOGGER.error(
                "LLMService.complete: no model configured for vendor=%s",
                settings.llm_vendor,
             )
            raise ValueError(f"No model configured for vendor {settings.llm_vendor}")
        return self._dispatch(settings.llm_vendor, model, system_prompt, user_message)

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
        model = getattr(settings, f"{settings.llm_fast_vendor}_fast_model", None)
        if not model:
            LOGGER.error(
                "LLMService.complete_fast: no fast model configured for vendor=%s, falling back to primary model",
                settings.llm_fast_vendor,
             )
            raise ValueError(f"No fast model configured for vendor {settings.llm_fast_vendor}")
        return self._dispatch(settings.llm_fast_vendor, model, system_prompt, user_message)

    def complete_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image_base64: str,
        image_media_type: str = "image/png",
    ) -> str:
        """
        Dispatch a multimodal completion with an image to the primary LLM vendor.

        Used for OCR of scanned document pages via the LLM's vision capability.

        :param system_prompt: System instructions.
        :param user_message: Text prompt accompanying the image.
        :param image_base64: Base64-encoded image data.
        :param image_media_type: MIME type of the image (e.g. 'image/png').
        :return: Model response text.
        :rtype: str
        """
        vendor = settings.llm_vendor
        model = getattr(settings, f"{vendor}_model", None)
        if not model:
            raise ValueError("No model configured for vendor %s" % vendor)

        LOGGER.debug("LLMService.complete_with_image: vendor=%s", vendor)

        if vendor == "gemini":
            return self._call_gemini_vision(model, system_prompt, user_message, image_base64, image_media_type)
        elif vendor == "anthropic":
            return self._call_anthropic_vision(model, system_prompt, user_message, image_base64, image_media_type)
        elif vendor == "openai":
            return self._call_openai_vision(model, system_prompt, user_message, image_base64, image_media_type)
        else:
            raise ValueError("Vision not supported for vendor: %s" % vendor)

    def _dispatch(self, vendor: str, model: str, system_prompt: str, user_message: str) -> str:
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
            response = handler(model, system_prompt, user_message)
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

    def _call_anthropic(self, model: str, system_prompt: str, user_message: str) -> str:
        """
        Call the Anthropic Messages API.

        :param model: Model identifier string.
        :type model: str
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
            model=model,
            max_tokens=16384,
            temperature=settings.llm_temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text  # type: ignore[attr-defined]

    def _call_gemini(self, model: str, system_prompt: str, user_message: str) -> str:
        """
        Call the Google Gemini API via the google-genai SDK.

        :param model: Model identifier string.
        :type model: str
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
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=settings.llm_temperature,
                top_p=settings.llm_top_p,
            ),
        )
        return response.text  # type: ignore[attr-defined]

    def _call_openai(self, model: str, system_prompt: str, user_message: str) -> str:
        """
        Call the OpenAI Chat Completions API.

        :param model: Model identifier string.
        :type model: str
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
            model=model,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    def _call_groq(self, model: str, system_prompt: str, user_message: str) -> str:
        """
        Call the Groq API (OpenAI-compatible interface).

        :param model: Model identifier string.
        :type model: str
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
            model=model,
            temperature=settings.llm_temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    def _call_deepseek(self, model: str, system_prompt: str, user_message: str) -> str:
        """
        Call the DeepSeek API (OpenAI-compatible interface).

        :param model: Model identifier string.
        :type model: str
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
            model=model,
            temperature=settings.llm_temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    # ── Vision implementations ────────────────────────────────────────────

    def _call_gemini_vision(
        self, model: str, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415
        import base64 as b64mod  # noqa: PLC0415

        client = genai.Client(api_key=settings.gemini_api_key)
        image_bytes = b64mod.b64decode(image_base64)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=image_media_type)
        response = client.models.generate_content(
            model=model,
            contents=[user_message, image_part],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
            ),
        )
        return response.text  # type: ignore[attr-defined]

    def _call_anthropic_vision(
        self, model: str, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
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
                    },
                    {"type": "text", "text": user_message},
                ],
            }],
        )
        return response.content[0].text  # type: ignore[attr-defined]

    def _call_openai_vision(
        self, model: str, system_prompt: str, user_message: str,
        image_base64: str, image_media_type: str,
    ) -> str:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
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
