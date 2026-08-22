"""
app/util/llm_profiles.py - Task-named LLM profile catalog.

A **profile** names what a call is *for* — ``analyze_pleading``,
``response_guardrail`` — never which vendor serves it. Each profile resolves
to an ordered failover chain of vendor+model candidates, so retuning which
models handle a task is a config edit, not a code change.

The catalog is a JSON file (``settings.llm_profiles_file``, default
``app/config/llm_profiles.json``). A profile entry takes one of three forms::

    "fast": [                                     # 1. a bare chain
        {"vendor": "gemini", "model": "gemini-3.1-flash-lite-preview"}
    ],
    "explain_message_edit": "fast",               # 2. an alias
    "response_guardrail": {                       # 3. an object
        "description": "Safety check on a drafted client reply",
        "extends": "fast",
        "temperature": 0.0
    }

Keys beginning with ``_`` are ignored, which is how you write a comment in a
JSON file. Sampling values resolve candidate -> profile -> global settings.

The catalog is loaded once at import. A malformed or missing file raises
``LLMProfileCatalogError`` and the process does not start — profiles are
required config. In development the file's mtime is checked on each lookup so
edits take effect without a restart.
"""
import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

# Profiles are resolved relative to the app package so the same relative path
# works under Docker (WORKDIR /app) and locally (uvicorn --app-dir app).
_APP_DIR = Path(__file__).resolve().parent.parent

_MAX_EXTENDS_DEPTH = 10  # Backstop for a pathological chain of extends


class LLMProfileCatalogError(RuntimeError):
    """Raised when the profile catalog file is missing, malformed, or inconsistent."""


class LLMUnavailableError(RuntimeError):
    """
    Raised when a profile cannot serve a call.

    Covers both an unknown profile name and a chain whose candidates all
    failed. Subclasses RuntimeError so existing broad handlers still catch it.
    """


class LLMCandidate(BaseModel):
    """
    One vendor+model pair inside a profile's failover chain.

    Sampling fields are optional overrides; when omitted the profile's value
    applies, and failing that the global ``llm_*`` setting.
    """

    vendor: str = Field(..., description="Vendor id: gemini | openai | anthropic | groq | deepseek")
    model: str = Field(..., description="Vendor-specific model identifier")
    temperature: Optional[float] = Field(default=None, description="Overrides the profile/global temperature")
    top_p: Optional[float] = Field(default=None, description="Overrides the profile/global top_p")
    max_tokens: Optional[int] = Field(default=None, description="Overrides the profile/global max_tokens")

    model_config = ConfigDict(extra="forbid")


class LLMProfile(BaseModel):
    """A fully resolved profile: a task name plus the chain that serves it."""

    name: str = Field(..., description="Task name callers pass to LLMService")
    description: str = Field(default="", description="What this profile is used for")
    chain: list[LLMCandidate] = Field(..., description="Ordered failover candidates")
    temperature: Optional[float] = Field(default=None, description="Profile-level temperature default")
    top_p: Optional[float] = Field(default=None, description="Profile-level top_p default")
    max_tokens: Optional[int] = Field(default=None, description="Profile-level max_tokens default")
    vision: bool = Field(default=False, description="True when this profile serves multimodal calls")


class _RawProfile(BaseModel):
    """The object form of a catalog entry, before extends/alias resolution."""

    description: str = ""
    extends: Optional[str] = None
    chain: Optional[list[LLMCandidate]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    vision: bool = False

    model_config = ConfigDict(extra="forbid")


def _normalize(name: str, value: Any) -> _RawProfile:
    """
    Coerce any of the three entry forms into a ``_RawProfile``.

    :param name: Profile name, used in error messages.
    :type name: str
    :param value: Raw JSON value for the entry.
    :type value: Any
    :return: Normalized entry.
    :rtype: _RawProfile
    :raises LLMProfileCatalogError: If the entry's shape or fields are invalid.
    """
    _value: dict[str, Any | list[Any] | str]
    if isinstance(value, str):
        _value = {"extends": value}
    elif isinstance(value, list):
        _value = {"chain": value}
    elif not isinstance(value, dict):
        raise LLMProfileCatalogError(
            "profile '%s' must be a chain (array), an alias (string), or an object — got %s"
            % (name, type(value).__name__)
        )
    else:
        _value = value  # type: ignore[assignment]  # mypy doesn't know dict[str, Any] is compatible with dict[str, Any | list[Any] | str]
    try:
        return _RawProfile.model_validate(_value)
    except Exception as e:  # noqa: BLE001 — surfaced as a catalog error with the profile name
        raise LLMProfileCatalogError("profile '%s' is invalid: %s" % (name, e)) from e


class LLMProfileRegistry:
    """
    Loads the profile catalog and resolves aliases, ``extends``, and defaults.

    Instantiated once at module level (``llm_profiles`` singleton).
    """

    def __init__(self, path: Path) -> None:
        """
        :param path: Absolute path to the catalog JSON file.
        :type path: Path
        :raises LLMProfileCatalogError: If the catalog cannot be loaded.
        """
        self._path = path
        self._profiles: dict[str, LLMProfile] = {}
        self._mtime: Optional[float] = None
        self.reload()

    # ── Loading ────────────────────────────────────────────────────────────

    def reload(self) -> None:
        """
        Read and resolve the catalog file, replacing the in-memory profiles.

        :raises LLMProfileCatalogError: If the file is missing or invalid.
        """
        if not self._path.is_file():
            raise LLMProfileCatalogError("LLM profile catalog not found: %s" % self._path)
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise LLMProfileCatalogError("LLM profile catalog %s is not valid JSON: %s" % (self._path, e)) from e
        if not isinstance(raw, dict):
            raise LLMProfileCatalogError("LLM profile catalog %s must be a JSON object" % self._path)

        self._profiles = _build(raw)  # type: ignore[assignment] 
        self._mtime = self._path.stat().st_mtime
        LOGGER.info("LLM profile catalog loaded: %d profiles from %s", len(self._profiles), self._path)

    def _maybe_reload(self) -> None:
        """
        In development, reload the catalog when the file changes on disk.

        A reload failure (e.g. the file is mid-edit) is logged and the previous
        catalog is kept, so a stray keystroke never takes the dev server down.
        """
        if not settings.is_development:
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if mtime == self._mtime:
            return
        try:
            self.reload()
        except LLMProfileCatalogError as e:
            self._mtime = mtime  # Don't retry until the file changes again
            LOGGER.error("LLM profile catalog reload failed, keeping previous profiles: %s", str(e))

    # ── Lookup ─────────────────────────────────────────────────────────────

    def get(self, name: str) -> LLMProfile:
        """
        Return a resolved profile by name.

        :param name: Task profile name.
        :type name: str
        :return: The resolved profile.
        :rtype: LLMProfile
        :raises LLMUnavailableError: If no profile by that name exists.
        """
        self._maybe_reload()
        profile = self._profiles.get(name)
        if profile is None:
            raise LLMUnavailableError(
                "Unknown LLM profile '%s' — add it to %s. Known profiles: %s"
                % (name, self._path.name, ", ".join(self.names()) or "(none)")
            )
        return profile

    def names(self) -> list[str]:
        """
        :return: Sorted catalog profile names.
        :rtype: list[str]
        """
        return sorted(self._profiles)

    def describe(self) -> str:
        """
        Render the catalog as a single log-safe line.

        :return: e.g. ``analyze_pleading=[anthropic:claude-opus-4-6, ...] fast=[...]``
        :rtype: str
        """
        parts = []
        for name in self.names():
            chain = self._profiles[name].chain
            parts.append("%s=[%s]" % (name, ", ".join("%s:%s" % (c.vendor, c.model) for c in chain)))  # type: ignore[union-attr]
        return " ".join(parts)  # type: ignore[union-attr]

    def problems(self, text_vendors: set[str], vision_vendors: set[str]) -> list[str]:
        """
        Report configuration problems without calling any vendor.

        :param text_vendors: Vendor ids the caller can dispatch text calls to.
        :type text_vendors: set[str]
        :param vision_vendors: Vendor ids the caller can dispatch vision calls to.
        :type vision_vendors: set[str]
        :return: Human-readable problem descriptions; empty when all is well.
        :rtype: list[str]
        """
        problems: list[str] = []
        for name in self.names():
            profile = self._profiles[name]
            supported = vision_vendors if profile.vision else text_vendors
            usable = 0
            for position, candidate in enumerate(profile.chain, start=1):
                where = "profile '%s' position %d" % (name, position)
                if candidate.vendor not in text_vendors:
                    problems.append("%s: unknown vendor '%s'" % (where, candidate.vendor))
                elif candidate.vendor not in supported:
                    problems.append("%s: vendor '%s' has no vision support" % (where, candidate.vendor))
                elif not settings.llm_api_key(candidate.vendor):
                    problems.append("%s: no API key configured for vendor '%s'" % (where, candidate.vendor))
                else:
                    usable += 1
            if usable == 0:
                problems.append("profile '%s' has no usable candidates" % name)
        return problems


def _build(raw: dict[str, Any]) -> dict[str, LLMProfile]:
    """
    Normalize and resolve every catalog entry.

    :param raw: Parsed catalog JSON.
    :type raw: dict[str, Any]
    :return: Profile name -> fully resolved profile.
    :rtype: dict[str, LLMProfile]
    :raises LLMProfileCatalogError: On any invalid or unresolvable entry.
    """
    entries = {name: _normalize(name, value) for name, value in raw.items() if not name.startswith("_")}
    if not entries:
        raise LLMProfileCatalogError("LLM profile catalog defines no profiles")

    resolved: dict[str, LLMProfile] = {}
    for name in entries:
        _resolve(name, entries, resolved, ())
    return resolved


def _resolve(
    name: str,
    entries: dict[str, _RawProfile],
    resolved: dict[str, LLMProfile],
    seen: tuple[str, ...],
) -> LLMProfile:
    """
    Resolve one entry, recursing into its ``extends`` parent first.

    :param name: Entry being resolved.
    :type name: str
    :param entries: All normalized entries.
    :type entries: dict[str, _RawProfile]
    :param resolved: Accumulator of already-resolved profiles.
    :type resolved: dict[str, LLMProfile]
    :param seen: Ancestors on the current resolution path, for cycle detection.
    :type seen: tuple[str, ...]
    :return: The resolved profile.
    :rtype: LLMProfile
    :raises LLMProfileCatalogError: On a cycle, missing parent, or empty chain.
    """
    if name in resolved:
        return resolved[name]
    if name in seen:
        raise LLMProfileCatalogError("circular 'extends' in LLM profiles: %s" % " -> ".join((*seen, name)))
    if len(seen) >= _MAX_EXTENDS_DEPTH:
        raise LLMProfileCatalogError("'extends' nested too deeply at profile '%s'" % name)

    entry = entries[name]
    parent: Optional[LLMProfile] = None
    if entry.extends is not None:
        if entry.extends not in entries:
            raise LLMProfileCatalogError(
                "profile '%s' extends '%s', which is not defined" % (name, entry.extends)
            )
        parent = _resolve(entry.extends, entries, resolved, (*seen, name))

    chain = entry.chain if entry.chain is not None else (parent.chain if parent else None)
    if not chain:
        raise LLMProfileCatalogError("profile '%s' has no candidates" % name)

    def inherited(own: Any, attr: str) -> Any:
        """Own value, else the parent's, else None."""
        return own if own is not None else (getattr(parent, attr) if parent else None)

    profile = LLMProfile(
        name=name,
        description=entry.description or (parent.description if parent else ""),
        chain=chain,
        temperature=inherited(entry.temperature, "temperature"),
        top_p=inherited(entry.top_p, "top_p"),
        max_tokens=inherited(entry.max_tokens, "max_tokens"),
        vision=entry.vision or bool(parent and parent.vision),
    )
    resolved[name] = profile
    return profile


def _catalog_path() -> Path:
    """
    Resolve the configured catalog path against the app package directory.

    :return: Absolute path to the catalog file.
    :rtype: Path
    """
    configured = Path(settings.llm_profiles_file)
    return configured if configured.is_absolute() else _APP_DIR / configured


# Module-level singleton — the catalog is required config, so a bad file fails
# the process at import rather than at the first LLM call.
llm_profiles = LLMProfileRegistry(_catalog_path())
