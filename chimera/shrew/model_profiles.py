"""Per-model configuration loader for the ``chimera shrew`` CLI.

Reads ``~/.chimera/shrew/settings.json`` (override-able) and exposes a
:class:`ModelProfile` per configured model id, each merged with the
user-defined ``default_model_profile`` and (optionally) a per-benchmark
override block.

Settings file shape::

    {
      "default_model_profile": {
        "max_tokens": 4096,
        "context_limit": 32768,
        "temperature": 0.3,
        "thinking_budget": 2048,
        "system_prompt_prefix": "",
        "skill_token_budget": 300,
        "knowledge_token_budget": 200
      },
      "model_profiles": {
        "qwen3.6-35b-a3b": {
          "max_tokens": 6144,
          "temperature": 0.2,
          "system_prompt_prefix": "You are an MoE coding agent...",
          "benchmark_overrides": {
            "terminal_bench": { "thinking_budget": 3000, "max_turns": 40 },
            "gaia":           { "thinking_budget": 2000, "context_limit": 65536 }
          }
        }
      }
    }

Lookup order for a request:

1. **Per-benchmark override** under
   ``model_profiles[<id>].benchmark_overrides[<benchmark>]``.
2. **Per-model profile** under ``model_profiles[<id>]``.
3. **Default profile** under ``default_model_profile``.
4. **Hard-coded fallbacks** in :data:`PROFILE_DEFAULTS`.

Each step is merged shallowly so a benchmark override only needs to
specify the keys it wants to change; everything else falls through.

Stdlib only. ``json.JSONDecodeError`` and ``OSError`` are caught and
surfaced as :class:`ModelProfileError` so the caller can decide whether
to fall back to defaults or fail loudly. Schema validation is
intentionally permissive: unknown keys pass through untouched (so the
loader stays forward-compatible with new profile fields).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from chimera.config.paths import store_path

__all__ = [
    "DEFAULT_SETTINGS_PATH",
    "ModelProfile",
    "ModelProfileError",
    "ModelProfileSettings",
    "PROFILE_DEFAULTS",
    "PROFILE_KEYS",
    "default_settings_path",
    "load_settings",
    "merge_profiles",
    "resolve_profile",
]


PROFILE_KEYS: Final[tuple[str, ...]] = (
    "max_tokens",
    "context_limit",
    "temperature",
    "thinking_budget",
    "system_prompt_prefix",
    "skill_token_budget",
    "knowledge_token_budget",
    "max_turns",
    "tool_choice",
)
"""Canonical profile keys recognised by :class:`ModelProfile`.

The set mirrors the upstream small-coder reference plus a small
extension (``tool_choice``, used by shrew to gate provider-side tool
selection). Unknown keys still round-trip through :meth:`as_dict` —
they're stored on the ``extras`` field so plugin authors can co-locate
custom flags in the same JSON without losing them.
"""


PROFILE_DEFAULTS: Final[Mapping[str, Any]] = {
    "max_tokens": 4096,
    "context_limit": 32768,
    "temperature": 0.3,
    "thinking_budget": 2048,
    "system_prompt_prefix": "",
    "skill_token_budget": 300,
    "knowledge_token_budget": 200,
    "max_turns": 30,
    "tool_choice": "auto",
}
"""Hard-coded fallbacks when neither default-profile nor model-profile defines a key.

Values mirror the shrew CLI's current implicit defaults (``max-steps=30``,
context-limit ranges from MoE catalog, etc.) so a fresh install with no
settings file behaves identically to today.
"""


def default_settings_path() -> Path:
    """Return ``~/.chimera/shrew/settings.json`` honouring the current ``Path.home()``."""
    return store_path("shrew") / "settings.json"


DEFAULT_SETTINGS_PATH: Final[Path] = default_settings_path()
"""Cached default location.

Recomputed lazily by :func:`default_settings_path` so tests that monkey
patch ``Path.home()`` stay isolated; the cached constant exists for
convenience callers that want a stable import-time value.
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ModelProfileError(Exception):
    """Raised on settings-file parse / schema problems.

    Wraps :class:`json.JSONDecodeError`, :class:`OSError`, and
    schema validation issues so the CLI can present a single
    failure mode regardless of the underlying cause.
    """


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelProfile:
    """Resolved per-model profile after merging defaults + overrides.

    Attributes:
        model_id: The model identifier the profile applies to (e.g.
            ``"qwen3.6-35b-a3b"``). Empty when this profile came from
            ``default_model_profile`` directly.
        max_tokens: Cap on assistant tokens per turn.
        context_limit: Cap on the total context window (input + output).
        temperature: Sampling temperature.
        thinking_budget: Cap on reasoning-channel tokens (only honoured
            by providers that expose a ``thinking`` knob).
        system_prompt_prefix: Optional prefix prepended to the rendered
            system prompt. Useful for per-model persona injection.
        skill_token_budget: Cap on tokens spent rendering active
            skills (used by the :mod:`chimera.shrew.skill_injector`).
        knowledge_token_budget: Cap on tokens spent rendering retrieved
            knowledge snippets (used by the planned ``knowledge_inject``
            extension and by the new knowledge axis).
        max_turns: Cap on agent ReAct turns for this profile.
        tool_choice: Provider-side tool selection mode (``"auto"`` /
            ``"any"`` / ``"none"`` / specific tool name).
        extras: Forward-compat storage for unknown JSON keys. Returned
            verbatim by :meth:`as_dict`.
    """

    model_id: str = ""
    max_tokens: int = 4096
    context_limit: int = 32768
    temperature: float = 0.3
    thinking_budget: int = 2048
    system_prompt_prefix: str = ""
    skill_token_budget: int = 300
    knowledge_token_budget: int = 200
    max_turns: int = 30
    tool_choice: str = "auto"
    extras: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Render as a flat dict (canonical keys + extras)."""
        out: dict[str, Any] = {
            "model_id": self.model_id,
            "max_tokens": self.max_tokens,
            "context_limit": self.context_limit,
            "temperature": self.temperature,
            "thinking_budget": self.thinking_budget,
            "system_prompt_prefix": self.system_prompt_prefix,
            "skill_token_budget": self.skill_token_budget,
            "knowledge_token_budget": self.knowledge_token_budget,
            "max_turns": self.max_turns,
            "tool_choice": self.tool_choice,
        }
        if self.extras:
            out.update(dict(self.extras))
        return out


@dataclass(frozen=True)
class ModelProfileSettings:
    """Parsed contents of the shrew settings JSON.

    Attributes:
        default_profile: The merged-with-defaults profile used when no
            model-specific profile applies.
        profiles: Map of ``model_id -> raw profile dict``. Stored
            unmerged so :func:`resolve_profile` can apply per-benchmark
            overrides at lookup time.
        path: The file the settings were loaded from (or
            :data:`Path("(builtin)")` when defaults were used).
    """

    default_profile: ModelProfile
    profiles: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    path: Path = field(default_factory=lambda: Path("(builtin)"))

    def model_ids(self) -> tuple[str, ...]:
        """Sorted tuple of configured model ids."""
        return tuple(sorted(self.profiles.keys()))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_settings(
    path: Path | None = None,
    *,
    create_missing: bool = False,
) -> ModelProfileSettings:
    """Load and parse ``path`` (default: :func:`default_settings_path`).

    Args:
        path: Override the settings file path. ``None`` uses
            :func:`default_settings_path` so the loader respects an
            in-test ``Path.home()`` override.
        create_missing: When ``True`` and the file does not exist, the
            loader returns a built-in :class:`ModelProfileSettings`
            (defaults only) WITHOUT raising. When ``False`` (the default)
            the loader still treats missing files as benign — the absence
            of a settings file is the common case for fresh installs.

    Returns:
        A :class:`ModelProfileSettings` populated from the file, or
        from :data:`PROFILE_DEFAULTS` when the file is missing /
        malformed and the caller asked for ``create_missing=True``.

    Raises:
        ModelProfileError: When the file exists but is malformed JSON
            or has the wrong top-level shape and ``create_missing`` is
            ``False``.
    """
    target = path or default_settings_path()
    if not target.exists():
        return ModelProfileSettings(
            default_profile=_profile_from_dict("", PROFILE_DEFAULTS),
            profiles={},
            path=target if create_missing else Path("(builtin)"),
        )
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelProfileError(
            f"failed to read shrew settings at {target}: {exc}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelProfileError(
            f"shrew settings {target} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ModelProfileError(
            f"shrew settings {target} must be a JSON object, got {type(data).__name__}"
        )

    raw_default = data.get("default_model_profile", {})
    raw_profiles = data.get("model_profiles", {})
    if raw_default is None:
        raw_default = {}
    if raw_profiles is None:
        raw_profiles = {}
    if not isinstance(raw_default, dict):
        raise ModelProfileError(
            f"default_model_profile in {target} must be an object"
        )
    if not isinstance(raw_profiles, dict):
        raise ModelProfileError(
            f"model_profiles in {target} must be an object"
        )

    # Validate per-model profile shape (each value must be a dict).
    cleaned_profiles: dict[str, Mapping[str, Any]] = {}
    for model_id, profile_data in raw_profiles.items():
        if not isinstance(profile_data, dict):
            raise ModelProfileError(
                f"model_profiles[{model_id!r}] in {target} must be an object"
            )
        cleaned_profiles[str(model_id)] = profile_data

    merged_default = merge_profiles(PROFILE_DEFAULTS, raw_default)
    return ModelProfileSettings(
        default_profile=_profile_from_dict("", merged_default),
        profiles=cleaned_profiles,
        path=target,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_profile(
    settings: ModelProfileSettings,
    model_id: str,
    *,
    benchmark: str | None = None,
) -> ModelProfile:
    """Return the merged :class:`ModelProfile` for ``model_id``.

    Resolution order (last write wins):

    1. :data:`PROFILE_DEFAULTS` (always present).
    2. ``settings.default_profile.as_dict()`` (the user-defined defaults
       merged with built-ins).
    3. ``settings.profiles[model_id]`` (per-model block, sans
       ``benchmark_overrides``).
    4. ``settings.profiles[model_id]["benchmark_overrides"][benchmark]``
       (only when ``benchmark`` is provided and the override exists).

    Args:
        settings: Loaded settings.
        model_id: Model identifier to look up.
        benchmark: Optional benchmark name (``"terminal_bench"``,
            ``"gaia"``, ``"aider_polyglot"``, ``"harbor"``, etc.).

    Returns:
        A new :class:`ModelProfile` with the resolved values. Even when
        ``model_id`` isn't in ``settings.profiles`` we still return the
        default profile (with its ``model_id`` field set) so callers
        always get a usable profile.
    """
    raw = dict(settings.default_profile.as_dict())
    raw.pop("model_id", None)  # preserve the input model_id below
    profile_data = settings.profiles.get(model_id, {})
    if profile_data:
        # Strip benchmark_overrides before merging so they don't leak into
        # the top-level profile fields.
        sans_overrides = {k: v for k, v in profile_data.items() if k != "benchmark_overrides"}
        raw = merge_profiles(raw, sans_overrides)

    if benchmark:
        overrides = (profile_data or {}).get("benchmark_overrides") or {}
        if isinstance(overrides, dict):
            bench_block = overrides.get(benchmark) or {}
            if isinstance(bench_block, dict):
                raw = merge_profiles(raw, bench_block)

    return _profile_from_dict(model_id, raw)


def merge_profiles(
    base: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    """Shallow-merge ``overlay`` onto ``base`` and return a new dict.

    Keys present in ``overlay`` overwrite ``base``. Both inputs are left
    unmodified. ``overlay`` keys that are ``None`` are skipped (so a
    JSON ``"key": null`` falls back to the base value rather than
    nuking it).
    """
    merged: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if v is None:
            continue
        merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _profile_from_dict(model_id: str, data: Mapping[str, Any]) -> ModelProfile:
    """Construct a :class:`ModelProfile` from a flat dict.

    Unknown keys are stashed into ``extras``. Numeric coercions are
    permissive (a JSON ``"max_tokens": "4096"`` string still works) so
    hand-edited settings files don't surprise users with type errors.
    """
    known: dict[str, Any] = {}
    extras: dict[str, Any] = {}

    for key, value in data.items():
        if key in PROFILE_KEYS:
            known[key] = value
        else:
            extras[key] = value

    return ModelProfile(
        model_id=model_id,
        max_tokens=_to_int(known.get("max_tokens"), PROFILE_DEFAULTS["max_tokens"]),
        context_limit=_to_int(known.get("context_limit"), PROFILE_DEFAULTS["context_limit"]),
        temperature=_to_float(known.get("temperature"), PROFILE_DEFAULTS["temperature"]),
        thinking_budget=_to_int(known.get("thinking_budget"), PROFILE_DEFAULTS["thinking_budget"]),
        system_prompt_prefix=str(known.get("system_prompt_prefix") or ""),
        skill_token_budget=_to_int(
            known.get("skill_token_budget"), PROFILE_DEFAULTS["skill_token_budget"],
        ),
        knowledge_token_budget=_to_int(
            known.get("knowledge_token_budget"), PROFILE_DEFAULTS["knowledge_token_budget"],
        ),
        max_turns=_to_int(known.get("max_turns"), PROFILE_DEFAULTS["max_turns"]),
        tool_choice=str(known.get("tool_choice") or PROFILE_DEFAULTS["tool_choice"]),
        extras=extras,
    )


def _to_int(value: Any, fallback: int) -> int:
    """Best-effort int coercion with fallback."""
    if value is None:
        return int(fallback)
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — treat as fallback so a
        # JSON ``true`` doesn't accidentally become ``1`` for a numeric
        # field.
        return int(fallback)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _to_float(value: Any, fallback: float) -> float:
    """Best-effort float coercion with fallback."""
    if value is None:
        return float(fallback)
    if isinstance(value, bool):
        return float(fallback)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def list_known_keys() -> Iterable[str]:
    """Yield the canonical profile keys (for help / documentation)."""
    yield from PROFILE_KEYS
