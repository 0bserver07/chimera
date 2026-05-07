"""Otter provider wiring (agent O12).

Resolves the model id and constructs a :class:`~chimera.providers.base.Provider`
for the otter subcommand. Encapsulates otter's default chain so the REPL,
one-shot ``-p`` flow, ACP transport, and HTTP server share one code path.

Resolution chain (first match wins):

1. Explicit ``args.model`` (CLI ``--model``).
2. ``$OTTER_MODEL`` environment variable.
3. ``$ANTHROPIC_API_KEY`` set        -> default :data:`_DEFAULT_ANTHROPIC_MODEL`.
4. ``$OPENROUTER_API_KEY`` set       -> default :data:`_DEFAULT_OPENROUTER_MODEL`
                                       (routed through the OpenAI-compatible
                                       provider against ``openrouter.ai``).
5. ``$OPENAI_API_KEY`` set           -> default :data:`_DEFAULT_OPENAI_MODEL`.
6. ``$XAI_API_KEY`` set               -> default :data:`_DEFAULT_XAI_MODEL`
                                       (Grok via ``api.x.ai``, OpenAI-compatible).
7. Friendly error pointing at the four env vars above.

Once a model id is in hand we choose a provider:

* OpenRouter is preferred when ``$OPENROUTER_API_KEY`` is set and the model
  contains a ``/`` separator (the OpenRouter convention, e.g.
  ``anthropic/claude-sonnet-4``). Routed through the ``compatible`` provider.
* Otherwise the regular :func:`chimera.providers.factory.create_provider`
  inference picks Anthropic / OpenAI / Google / Ollama by model prefix.

Trademark hygiene: this module avoids naming the upstream open-source
coding agent. ``OPENROUTER_API_KEY`` is a vendor identifier (not a brand
claim about the upstream).
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.providers.base import Provider

# WHY: otter ships defaults that are cheap, real, and supported today.
# ``claude-sonnet-4-6`` is the first-class Anthropic model the repo tests
# against; ``gpt-4o`` is the first-class OpenAI model with cost data in
# ``chimera/providers/cost.py``. We pick concrete ids so the resolver is
# deterministic across machines.
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o"
_DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4"
# WHY: ``$XAI_API_KEY`` is recognised as a late-binding fallback so users
# with only an xAI key get ``grok-3`` as the default. Higher-priority keys
# (Anthropic / OpenRouter / OpenAI) still win when set, preserving existing
# behaviour for users who already have a chain in place.
_DEFAULT_XAI_MODEL = "grok-3"

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# WHY: OpenRouter recommends every client send ``HTTP-Referer`` and
# ``X-Title`` so requests show up identifiably in their dashboard. These
# are cosmetic — the API works without them — but setting them is the
# polite default. Users override via the matching env vars.
_OPENROUTER_DEFAULT_REFERER = "https://github.com/0bserver07/chimera"
_OPENROUTER_DEFAULT_TITLE = "chimera otter 0.6.0"


def _openrouter_extra_headers() -> dict[str, str]:
    """Return the cosmetic OpenRouter headers (``HTTP-Referer`` / ``X-Title``).

    Resolution order per field:

    1. ``$OPENROUTER_REFERER`` / ``$OPENROUTER_TITLE`` (user override).
    2. Module defaults baked in above.

    Returns:
        Two-key dict ready to pass as ``extra_headers=`` to the
        OpenAI-compatible provider.
    """
    referer = os.environ.get("OPENROUTER_REFERER") or _OPENROUTER_DEFAULT_REFERER
    title = os.environ.get("OPENROUTER_TITLE") or _OPENROUTER_DEFAULT_TITLE
    return {"HTTP-Referer": referer, "X-Title": title}

# WHY: the friendly error message lists all three env vars users can set.
# Kept as a module constant so tests can assert on the exact wording.
_NO_KEY_MESSAGE = (
    "otter: no provider configured.\n"
    "Set one of:\n"
    "  - ANTHROPIC_API_KEY (default model: "
    f"{_DEFAULT_ANTHROPIC_MODEL})\n"
    "  - OPENROUTER_API_KEY (default model: "
    f"{_DEFAULT_OPENROUTER_MODEL})\n"
    "  - OPENAI_API_KEY (default model: "
    f"{_DEFAULT_OPENAI_MODEL})\n"
    "  - XAI_API_KEY (default model: "
    f"{_DEFAULT_XAI_MODEL})\n"
    "or override the model via --model / $OTTER_MODEL."
)


def _resolve_model(args: argparse.Namespace) -> str:
    """Resolve which model id to use for this otter invocation.

    Args:
        args: Parsed argparse namespace. Reads ``args.model`` if present.

    Returns:
        The model id string to feed to the provider factory.

    Raises:
        ValueError: When no explicit model and no provider env var is set.
    """
    explicit = getattr(args, "model", None)
    if explicit:
        return str(explicit)

    env_model = os.environ.get("OTTER_MODEL")
    if env_model:
        return env_model

    if os.environ.get("ANTHROPIC_API_KEY"):
        return _DEFAULT_ANTHROPIC_MODEL
    if os.environ.get("OPENROUTER_API_KEY"):
        return _DEFAULT_OPENROUTER_MODEL
    if os.environ.get("OPENAI_API_KEY"):
        return _DEFAULT_OPENAI_MODEL
    # WHY: ``$XAI_API_KEY`` is added as a late-binding fallback after the
    # historical chain so existing users see no behaviour change. The
    # factory's ``grok-*`` prefix inference handles routing once the model
    # id is in hand.
    if os.environ.get("XAI_API_KEY"):
        return _DEFAULT_XAI_MODEL

    raise ValueError(_NO_KEY_MESSAGE)


def _is_ollama_id(model: str) -> bool:
    """Return True when the model id looks like an Ollama tag.

    Ollama ids carry a ``name:tag`` shape (``glm-5.1:cloud``,
    ``deepseek-v4-pro:cloud``, ``llama3.2:3b``). The factory's prefix
    inference doesn't catch the cloud-tag variants, so we route through
    OllamaProvider explicitly when we see a colon-tagged id and a local
    Ollama daemon is reachable on the standard port.

    We deliberately skip the ``foo/bar`` OpenRouter shape here because
    those route through ``_should_use_openrouter`` instead.
    """
    if "/" in model:
        return False
    return ":" in model


def _build_ollama_provider(model: str, max_tokens: int | None) -> Provider:
    """Construct an OllamaProvider with sensible context defaults.

    Mirrors the mink builder so otter behaves identically when pointed at
    an Ollama-served model. Defaults the base URL to
    ``$OLLAMA_HOST or http://localhost:11434`` so users don't need to set
    anything for the standard local install.
    """
    from chimera.providers.ollama import OllamaProvider

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    # Cloud Kimi advertises 262k; deepseek-v4 cloud variants pack the same
    # context window. Default to 262k for ``:cloud``-tagged ids and 131k
    # otherwise.
    ctx = 262_144 if model.endswith(":cloud") else 131_072
    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": host,
        "context_length": ctx,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return OllamaProvider(**kwargs)


def _should_use_openrouter(model: str) -> bool:
    """Return True when the model should be routed through OpenRouter.

    Heuristic: ``$OPENROUTER_API_KEY`` is set AND the model id contains a
    ``/`` (the OpenRouter ``vendor/name`` convention). This avoids
    accidentally routing a bare ``claude-sonnet-4-6`` through OpenRouter
    when the user actually has ``$ANTHROPIC_API_KEY`` set as well.

    Args:
        model: The resolved model id.

    Returns:
        ``True`` if the model should route through the OpenAI-compatible
        provider pointed at OpenRouter.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False
    return "/" in model


def build_provider(args: argparse.Namespace) -> Provider:
    """Build a :class:`Provider` for the otter subcommand.

    Honors:

    * ``args.model`` (explicit CLI override).
    * ``$OTTER_MODEL`` (env override).
    * ``args.no_color`` — read but not consumed here; rendering layers
      consume it. Kept in the signature so callers can pass a single
      args namespace without a separate filter step.
    * ``args.max_tokens`` — forwarded to providers that accept it as a
      constructor kwarg. Currently only the OpenAI-compatible OpenRouter
      path uses it; SDK-backed providers honor ``max_tokens`` per-call,
      not per-instance, so the kwarg is ignored unless explicitly accepted.

    Args:
        args: Parsed argparse namespace from ``chimera otter``.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.

    Raises:
        ValueError: When no model can be resolved (no ``--model`` /
            ``$OTTER_MODEL`` and no provider env var set).
    """
    # Lazy import: the factory imports SDKs (anthropic, openai) on first
    # touch; keeping it inside the function preserves the otter promise
    # that ``import chimera.otter.providers`` is stdlib-only.
    from chimera.providers.factory import create_provider

    model = _resolve_model(args)

    # Forward ``no_color`` only if the caller actually attached it; we
    # don't need it for provider construction, but reading it here keeps
    # the API contract documented (and silences linters that want all
    # parameters touched).
    _ = bool(getattr(args, "no_color", False))

    extra_kwargs: dict[str, Any] = {}
    max_tokens = getattr(args, "max_tokens", None)
    if max_tokens is not None:
        # WHY: providers that accept ``max_tokens`` at construction time
        # (e.g. Ollama) read it through ``**kwargs``; the rest ignore it
        # silently. We prefer this over per-call wiring so otter has one
        # tunable knob exposed via the CLI flag surface.
        extra_kwargs["max_tokens"] = max_tokens

    # vLLM / SGLang prefix recognition (handled BEFORE OpenRouter so a
    # ``vllm/<id>`` model isn't hijacked by a stray ``$OPENROUTER_API_KEY``).
    model_lower = model.lower()
    if model_lower.startswith("vllm/"):
        return create_provider(provider_type="vllm", model=model)
    if model_lower.startswith("sglang/"):
        return create_provider(provider_type="sglang", model=model)

    if _should_use_openrouter(model):
        # OpenRouter is OpenAI-compatible. Route through the
        # ``compatible`` provider with the OpenRouter base URL and key.
        # Pass the cosmetic ``HTTP-Referer`` / ``X-Title`` headers so
        # requests show up correctly in OpenRouter's dashboard.
        api_key = os.environ.get("OPENROUTER_API_KEY")
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_OPENROUTER_BASE_URL,
            extra_headers=_openrouter_extra_headers(),
        )

    if _is_ollama_id(model):
        # WHY: ``deepseek-v4-pro:cloud``, ``glm-5.1:cloud``, ``llama3.2:3b``
        # etc. don't match the factory's prefix inference list. Route them
        # to OllamaProvider directly so users can ``--model <name>:cloud``
        # against a local Ollama daemon (which proxies to ``ollama.com``).
        return _build_ollama_provider(
            model, max_tokens=getattr(args, "max_tokens", None)
        )

    return create_provider(model=model, **extra_kwargs)


__all__ = [
    "build_provider",
]
