"""Declarative provider capability matrix keyed by wire protocol.

Chimera historically organised LLM backends per brand — ``anthropic.py``,
``openai.py``, ``google.py``, ``compatible.py`` — with each file re-encoding
the small request/response quirks its vendor demands. But most of those
quirks are not per-brand at all: they are per **wire protocol**. Every
OpenAI-compatible backend (OpenRouter, Together, Groq, vLLM, DeepSeek, xAI,
…) speaks the same ``/v1/chat/completions`` shape and diverges only in a
handful of knobs; likewise every Anthropic-compatible backend (Claude, GLM
via z.ai, Kimi via Moonshot, …) speaks the Messages API.

This module makes that divergence **data**. A :class:`ProviderCapabilities`
record holds the quirk knobs; :data:`PROTOCOL_DEFAULTS` maps each
:class:`WireProtocol` to its baseline record; and per-provider / per-model
overrides layer on top via :func:`register_capabilities` /
:func:`resolve_capabilities`. Adding a new backend on an existing protocol
becomes a ~20-line data row (base URL + auth + a capability override) rather
than a new :class:`~chimera.providers.base.Provider` subclass.

Resolution order (each layer overrides the previous, most-specific wins)::

    protocol default  →  provider override  →  model-prefix override

``extra_payload`` is the one field merged additively across layers (a union
of the payload dicts); every other field is replaced by the more-specific
layer.

Zero-dependency: this module is pure stdlib and imports nothing from the
rest of :mod:`chimera.providers`, so it can seed provider construction
without import cycles and without pulling any optional SDK.

Three-tier API:

* **One-liner** — ``resolve_capabilities(WireProtocol.OPENAI_COMPAT,
  model="o3-mini")`` returns the fully-merged record.
* **Configuration** — ``register_capabilities(WireProtocol.OPENAI_COMPAT,
  provider="acmecloud", supports_strict_tools=True)`` adds a data row.
* **Framework-author** — subclass nothing; a provider factory passes a
  ``provider=`` hint and consumes the resolved record.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WireProtocol(str, Enum):
    """The HTTP request/response shape a backend speaks.

    A wire protocol — not a brand — is the real unit of divergence. Many
    vendors share one protocol; the matrix keys quirks off these values so a
    new vendor on an existing protocol needs no new code.
    """

    OPENAI_COMPAT = "openai-compat"
    ANTHROPIC_COMPAT = "anthropic-compat"
    GOOGLE = "google"


class ThinkingFormat(str, Enum):
    """How a protocol expresses extended-reasoning ("thinking") on the wire.

    The :class:`~chimera.providers.thinking.ThinkingLevel` ladder maps a
    requested depth to a token budget; *this* enum records how that budget is
    encoded in the request for a given protocol — one axis of the matrix.
    """

    #: Thinking is not expressed on the wire (the request carries no reasoning
    #: knob; a passed ``thinking`` level is ignored by the provider).
    NONE = "none"
    #: Anthropic Messages: ``{"type": "enabled", "budget_tokens": N}`` plus a
    #: forced ``temperature=1``.
    ANTHROPIC_BUDGET = "anthropic-budget"
    #: OpenAI reasoning models: a ``reasoning_effort`` of ``low``/``medium``/``high``.
    OPENAI_EFFORT = "openai-effort"


class CacheStyle(str, Enum):
    """How a protocol lets a caller mark reusable prompt prefixes for caching."""

    #: No client-side cache control (the backend may still cache server-side,
    #: but the request carries no markers).
    NONE = "none"
    #: Anthropic ``cache_control`` blocks (``{"type": "ephemeral"}``, optional
    #: ``"ttl": "1h"``) attached to the stable prefix and rolling suffix.
    ANTHROPIC_EPHEMERAL = "anthropic-ephemeral"
    #: OpenAI-style automatic prefix caching — no request markers; cache hits
    #: are reported back via ``prompt_tokens_details.cached_tokens``.
    OPENAI_AUTOMATIC = "openai-automatic"


@dataclass(frozen=True)
class CompatFlags:
    """OpenAI-compatible request projection of :class:`ProviderCapabilities`.

    This is the narrow, three-knob view the
    :class:`~chimera.providers.compatible.OpenAICompatibleProvider` consumes
    while building a ``/v1/chat/completions`` payload. It is not a parallel
    quirk system: it is produced from a resolved
    :class:`ProviderCapabilities` by :meth:`ProviderCapabilities.to_compat_flags`,
    so the matrix remains the single source of truth. Kept as a public type
    for backwards compatibility (callers pass ``flags=`` and read
    ``provider._flags``).

    Attributes:
        max_tokens_field: Request field naming the output cap. Newer OpenAI
            reasoning models require ``max_completion_tokens``; most compat
            backends only accept ``max_tokens``.
        supports_temperature: Some reasoning models reject ``temperature``
            outright; when ``False`` it is omitted from the payload.
        extra_payload: Backend-specific request params merged into every
            payload (e.g. a reasoning-effort knob).
    """

    max_tokens_field: str = "max_tokens"
    supports_temperature: bool = True
    extra_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declarative quirk record for one resolved (protocol, provider, model).

    Every field is a single quirk knob. Instances are frozen and compared by
    value, so they double as snapshot fixtures. Resolve one with
    :func:`resolve_capabilities`; never mutate — layer overrides with
    :func:`register_capabilities` instead.

    Some knobs are *consumed* today (they shape an outgoing request); others
    are *declared* metadata that callers and cost logic can read. The
    docstring of each notes which.

    Attributes:
        protocol: The wire protocol this record describes.
        max_tokens_field: Consumed. Request field naming the output cap.
        supports_temperature: Consumed. When ``False``, ``temperature`` is
            omitted from the request.
        extra_payload: Consumed. Extra request params merged into every
            payload; unioned additively across override layers.
        supports_tool_use: Declared. Whether the backend supports function
            calling at all.
        supports_strict_tools: Consumed (OpenAI-compat). Emit ``strict: true``
            on each function tool for schema-faithful arguments.
        supports_tool_search: Declared. Backend offers a server-side
            tool/function search facility.
        thinking_format: Declared. How extended reasoning is encoded
            (see :class:`ThinkingFormat`).
        cache_style: Declared. How reusable prefixes are marked
            (see :class:`CacheStyle`).
        one_hour_cache_write_premium: Declared. A 1-hour cache write costs a
            premium over the 5-minute write (Anthropic's extended-TTL tier).
        supports_stop_sequences: Declared. Backend honours stop sequences.
        accepts_extra_headers: Declared. Backend tolerates arbitrary extra
            request headers (cosmetic ``HTTP-Referer`` / ``X-Title`` etc.).
        tiered_pricing: Declared. Per-token price changes above a context
            threshold (e.g. Anthropic/Gemini long-context tiers).
        default_max_tokens: Consumed (Anthropic-compat). Per-turn output cap
            used when the caller passes no explicit ``max_tokens``.
    """

    protocol: WireProtocol
    # --- request-shaping knobs (the CompatFlags projection) ---
    max_tokens_field: str = "max_tokens"
    supports_temperature: bool = True
    extra_payload: dict[str, Any] = field(default_factory=dict)
    # --- tool knobs ---
    supports_tool_use: bool = True
    supports_strict_tools: bool = False
    supports_tool_search: bool = False
    # --- reasoning / thinking ---
    thinking_format: ThinkingFormat = ThinkingFormat.NONE
    # --- caching ---
    cache_style: CacheStyle = CacheStyle.NONE
    one_hour_cache_write_premium: bool = False
    # --- misc wire quirks ---
    supports_stop_sequences: bool = True
    accepts_extra_headers: bool = True
    tiered_pricing: bool = False
    # --- output cap ---
    default_max_tokens: int = 8_192

    def to_compat_flags(self) -> CompatFlags:
        """Project this record onto the OpenAI-compat request knobs.

        Returns:
            A :class:`CompatFlags` carrying the three fields the
            OpenAI-compatible provider needs at payload-build time. The
            ``extra_payload`` dict is copied so later mutation of the flags
            (the provider's 400-retry) never touches the shared matrix record.
        """
        return CompatFlags(
            max_tokens_field=self.max_tokens_field,
            supports_temperature=self.supports_temperature,
            extra_payload=dict(self.extra_payload),
        )


#: Baseline capabilities for each wire protocol. Provider- and model-level
#: overrides layer on top; see :func:`resolve_capabilities`.
PROTOCOL_DEFAULTS: dict[WireProtocol, ProviderCapabilities] = {
    WireProtocol.OPENAI_COMPAT: ProviderCapabilities(
        protocol=WireProtocol.OPENAI_COMPAT,
        max_tokens_field="max_tokens",
        supports_temperature=True,
        thinking_format=ThinkingFormat.NONE,
        cache_style=CacheStyle.OPENAI_AUTOMATIC,
        supports_strict_tools=False,
        accepts_extra_headers=True,
        supports_stop_sequences=True,
        tiered_pricing=False,
        default_max_tokens=8_192,
    ),
    WireProtocol.ANTHROPIC_COMPAT: ProviderCapabilities(
        protocol=WireProtocol.ANTHROPIC_COMPAT,
        max_tokens_field="max_tokens",
        supports_temperature=True,
        thinking_format=ThinkingFormat.ANTHROPIC_BUDGET,
        cache_style=CacheStyle.ANTHROPIC_EPHEMERAL,
        one_hour_cache_write_premium=True,
        supports_strict_tools=False,
        accepts_extra_headers=True,
        supports_stop_sequences=True,
        tiered_pricing=True,
        default_max_tokens=8_192,
    ),
    WireProtocol.GOOGLE: ProviderCapabilities(
        protocol=WireProtocol.GOOGLE,
        max_tokens_field="max_output_tokens",
        supports_temperature=True,
        thinking_format=ThinkingFormat.NONE,
        cache_style=CacheStyle.NONE,
        supports_strict_tools=False,
        accepts_extra_headers=False,
        supports_stop_sequences=True,
        tiered_pricing=True,
        default_max_tokens=8_192,
    ),
}

#: Per-provider overrides, keyed by ``(protocol, provider_name)``. Each value
#: is a partial field map applied over the protocol default.
_PROVIDER_OVERRIDES: dict[tuple[WireProtocol, str], dict[str, Any]] = {}

#: Per-model-prefix overrides, keyed by protocol then lowercased model prefix.
#: Longest matching prefix wins (mirrors ``chimera.providers.cost``).
_MODEL_OVERRIDES: dict[WireProtocol, dict[str, dict[str, Any]]] = {
    protocol: {} for protocol in WireProtocol
}

# Valid ProviderCapabilities field names, minus ``protocol`` which is fixed by
# the layer being resolved. Used to validate override kwargs at registration.
_OVERRIDABLE_FIELDS = frozenset(
    f.name for f in dataclasses.fields(ProviderCapabilities) if f.name != "protocol"
)


def register_capabilities(
    protocol: WireProtocol,
    *,
    provider: str | None = None,
    model_prefix: str | None = None,
    **fields: Any,
) -> None:
    """Register a capability override layer as data.

    Exactly one of *provider* or *model_prefix* selects the layer; passing
    neither, or both, is an error. The keyword *fields* are
    :class:`ProviderCapabilities` field names (``protocol`` is not
    overridable). Re-registering the same key merges the new fields over the
    existing override, so a later call refines an earlier one.

    Args:
        protocol: The wire protocol the override applies to.
        provider: Provider name for a provider-level override (e.g.
            ``"acmecloud"``).
        model_prefix: Case-insensitive model-id prefix for a model-level
            override (e.g. ``"o3"``). Longest matching prefix wins at resolve.
        **fields: Partial :class:`ProviderCapabilities` field values.

    Raises:
        ValueError: Neither/both of *provider* and *model_prefix* given, or an
            unknown field name is passed.
    """
    if (provider is None) == (model_prefix is None):
        raise ValueError(
            "register_capabilities requires exactly one of provider= or "
            "model_prefix="
        )
    unknown = set(fields) - _OVERRIDABLE_FIELDS
    if unknown:
        raise ValueError(
            f"unknown ProviderCapabilities field(s): {sorted(unknown)}; "
            f"valid: {sorted(_OVERRIDABLE_FIELDS)}"
        )
    if provider is not None:
        key = (protocol, provider.lower())
        _PROVIDER_OVERRIDES[key] = {**_PROVIDER_OVERRIDES.get(key, {}), **fields}
    else:
        assert model_prefix is not None  # narrowed by the guard above
        prefix = model_prefix.lower()
        table = _MODEL_OVERRIDES[protocol]
        table[prefix] = {**table.get(prefix, {}), **fields}


def _match_model_override(
    protocol: WireProtocol, model: str,
) -> dict[str, Any] | None:
    """Return the longest-prefix model override for *model*, or ``None``.

    Matching is case-insensitive on the model id.
    """
    table = _MODEL_OVERRIDES.get(protocol, {})
    lowered = model.lower()
    best: dict[str, Any] | None = None
    best_len = -1
    for prefix, override in table.items():
        if lowered.startswith(prefix) and len(prefix) > best_len:
            best = override
            best_len = len(prefix)
    return best


def _apply(base: ProviderCapabilities, partial: dict[str, Any]) -> ProviderCapabilities:
    """Return *base* with *partial* applied; ``extra_payload`` merges additively."""
    merged = dict(partial)
    if "extra_payload" in partial:
        merged["extra_payload"] = {**base.extra_payload, **partial["extra_payload"]}
    return dataclasses.replace(base, **merged)


def resolve_capabilities(
    protocol: WireProtocol,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> ProviderCapabilities:
    """Resolve the effective capabilities for a (protocol, provider, model).

    Layers the protocol default, then any provider override, then the
    longest-matching model-prefix override — most specific wins. Pure and
    side-effect-free; the returned record is safe to cache.

    Args:
        protocol: The wire protocol to start from.
        provider: Optional provider name to apply a provider-level override.
        model: Optional model id to apply a model-prefix override.

    Returns:
        The fully-merged :class:`ProviderCapabilities`.
    """
    caps = PROTOCOL_DEFAULTS[protocol]
    if provider is not None:
        override = _PROVIDER_OVERRIDES.get((protocol, provider.lower()))
        if override is not None:
            caps = _apply(caps, override)
    if model is not None:
        override = _match_model_override(protocol, model)
        if override is not None:
            caps = _apply(caps, override)
    return caps


# ---------------------------------------------------------------------------
# Built-in override rows — the brand divergence that used to live in code.
# ---------------------------------------------------------------------------

# OpenAI reasoning models (o1/o3/o4/gpt-5) served over any OpenAI-compatible
# endpoint require the ``max_completion_tokens`` field and reject
# ``temperature``. This is the data that ``detect_compat_flags`` used to carry
# as the ``_REASONING_PREFIXES`` tuple in ``compatible.py``.
for _reasoning_prefix in ("o1", "o3", "o4", "gpt-5"):
    register_capabilities(
        WireProtocol.OPENAI_COMPAT,
        model_prefix=_reasoning_prefix,
        max_tokens_field="max_completion_tokens",
        supports_temperature=False,
        thinking_format=ThinkingFormat.OPENAI_EFFORT,
    )

# Anthropic-compatible endpoints serving non-Claude models (GLM via z.ai, Kimi
# via Moonshot, Qwen/DeepSeek over Anthropic-compat, z-*) support much larger
# outputs than Claude, so they get a bigger default output cap. This is the
# data that ``AnthropicProvider._default_max_tokens`` used to carry as a
# hardcoded prefix tuple.
for _large_output_prefix in ("glm", "kimi", "qwen", "deepseek", "z-"):
    register_capabilities(
        WireProtocol.ANTHROPIC_COMPAT,
        model_prefix=_large_output_prefix,
        default_max_tokens=32_768,
    )


__all__ = [
    "CacheStyle",
    "CompatFlags",
    "PROTOCOL_DEFAULTS",
    "ProviderCapabilities",
    "ThinkingFormat",
    "WireProtocol",
    "register_capabilities",
    "resolve_capabilities",
]
