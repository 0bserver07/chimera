"""xAI / Grok provider (agent P2, wave 8).

xAI exposes an OpenAI-compatible Chat Completions API at
``https://api.x.ai/v1``. We treat it the same way we treat OpenRouter or
Together AI: a thin factory that delegates to
:class:`chimera.providers.compatible.OpenAICompatibleProvider` with the
right ``base_url`` and ``$XAI_API_KEY`` lookup.

Shipped Grok models as of 2026-04:

* ``grok-3`` — flagship reasoning model.
* ``grok-3-mini`` — small/cheap reasoning model.
* ``grok-4`` — successor flagship (verify availability per account; not
  every API tier sees it yet).

The factory honors the standard environment variable ``XAI_API_KEY``.
``GROK_API_KEY`` is accepted as a synonym for users who reach for the
brand name first.

Trademark hygiene: ``XAI`` and ``GROK`` are vendor identifiers used here
solely to route HTTP traffic. This module is OpenAI-compatible plumbing,
not a brand claim about the upstream service.

Usage::

    from chimera.providers.factory import create_provider

    # By prefix:
    provider = create_provider(model="grok-3", api_key="xai-...")

    # By explicit type:
    provider = create_provider(provider_type="xai", model="grok-3-mini")

Stdlib only at module import time. ``httpx`` is imported lazily by the
underlying :class:`OpenAICompatibleProvider`.
"""
from __future__ import annotations

import os
from typing import Any

from chimera.providers.compatible import OpenAICompatibleProvider
from chimera.providers.registry import register_provider as _register_provider

XAI_BASE_URL = "https://api.x.ai/v1"
"""Default xAI Chat Completions base URL."""

# Per-model context windows. Source: xAI public docs (verify per account).
_CONTEXT_WINDOWS: dict[str, int] = {
    "grok-3": 131_072,
    "grok-3-mini": 131_072,
    "grok-4": 256_000,
}
_DEFAULT_CONTEXT_WINDOW = 131_072


def _resolve_api_key(api_key: str | None) -> str:
    """Resolve the xAI API key from arg, ``$XAI_API_KEY``, or ``$GROK_API_KEY``.

    Args:
        api_key: Explicit key. If non-empty, used as-is.

    Returns:
        Resolved key string. Empty string when nothing is configured —
        the underlying provider will then raise on the first request,
        matching the behaviour of every other Chimera provider.
    """
    if api_key:
        return api_key
    return os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or ""


def create_xai_provider(
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    context_length: int | None = None,
    **kwargs: Any,
) -> OpenAICompatibleProvider:
    """Construct an xAI-targeted :class:`OpenAICompatibleProvider`.

    Args:
        model: Grok model id (e.g. ``"grok-3"``, ``"grok-3-mini"``,
            ``"grok-4"``).
        api_key: xAI API key. Falls back to ``$XAI_API_KEY`` then
            ``$GROK_API_KEY`` when ``None``.
        base_url: Override the default ``https://api.x.ai/v1`` endpoint.
            Useful for proxies and tests.
        context_length: Override the per-model context window. When
            ``None``, looks up :data:`_CONTEXT_WINDOWS` and falls back
            to :data:`_DEFAULT_CONTEXT_WINDOW`.
        **kwargs: Forwarded to :class:`OpenAICompatibleProvider`.

    Returns:
        A live provider configured to talk to xAI.
    """
    resolved_key = _resolve_api_key(api_key)
    resolved_base_url = base_url or XAI_BASE_URL
    if context_length is None:
        context_length = _CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)

    return OpenAICompatibleProvider(
        model=model,
        base_url=resolved_base_url,
        api_key=resolved_key,
        context_length=context_length,
        provider="xai",
        **kwargs,
    )


# Register under the ``xai`` provider_type. The factory key matches the
# canonical env-var prefix users will recognise.
_register_provider(
    "xai",
    lambda model="", api_key=None, base_url=None, **kw: create_xai_provider(
        model=model, api_key=api_key, base_url=base_url, **kw,
    ),
)


__all__ = [
    "XAI_BASE_URL",
    "create_xai_provider",
]
