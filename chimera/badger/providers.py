"""Badger provider wiring — general-purpose, Anthropic-first chain.

Resolves the model id and constructs a :class:`~chimera.providers.base.Provider`
for the badger subcommand. Encapsulates badger's default chain so the REPL,
one-shot ``-p`` flow, and parity check all share a single code path.

Badger inherits a **general-purpose, Anthropic-first** posture: when no
explicit model is given, ``$ANTHROPIC_API_KEY`` wins because the
harness-rewrite tradition was first articulated against Anthropic models.

Resolution chain (first match wins):

1. Explicit ``args.model`` (CLI ``--model``).
2. ``$BADGER_MODEL`` environment variable.
3. ``$ANTHROPIC_API_KEY`` set     -> default :data:`_DEFAULT_ANTHROPIC_MODEL`.
4. ``$OPENAI_API_KEY`` set        -> default :data:`_DEFAULT_OPENAI_MODEL`.
5. ``$OPENROUTER_API_KEY`` set    -> default :data:`_DEFAULT_OPENROUTER_MODEL`.
6. ``$OLLAMA_HOST`` reachable     -> default :data:`_DEFAULT_OLLAMA_MODEL`.
7. Friendly :class:`ValueError` pointing at the four env vars above.

Trademark hygiene: this module avoids naming the upstream by brand.
``OPENROUTER_API_KEY``, ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY`` are
vendor identifiers (not brand claims about the upstream).
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.providers.base import Provider

_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o"
_DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4-6"
_DEFAULT_OLLAMA_MODEL = "qwen3:32b"

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Cosmetic OpenRouter headers — same pattern as ferret.
_OPENROUTER_DEFAULT_REFERER = "https://github.com/0bserver07/chimera"
_OPENROUTER_DEFAULT_TITLE = "chimera badger 0.7.0"


def _openrouter_extra_headers() -> dict[str, str]:
    """Return the cosmetic OpenRouter headers (``HTTP-Referer`` / ``X-Title``)."""
    referer = os.environ.get("OPENROUTER_REFERER") or _OPENROUTER_DEFAULT_REFERER
    title = os.environ.get("OPENROUTER_TITLE") or _OPENROUTER_DEFAULT_TITLE
    return {"HTTP-Referer": referer, "X-Title": title}


_NO_KEY_MESSAGE = (
    "badger: no provider configured.\n"
    "Set one of:\n"
    "  - ANTHROPIC_API_KEY (default model: "
    f"{_DEFAULT_ANTHROPIC_MODEL})\n"
    "  - OPENAI_API_KEY (default model: "
    f"{_DEFAULT_OPENAI_MODEL})\n"
    "  - OPENROUTER_API_KEY (default model: "
    f"{_DEFAULT_OPENROUTER_MODEL})\n"
    "or run a local Ollama daemon and pass --model qwen3:32b.\n"
    "Override the model via --model / $BADGER_MODEL."
)


def _resolve_model(args: argparse.Namespace) -> str:
    """Resolve which model id to use for this badger invocation.

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

    env_model = os.environ.get("BADGER_MODEL")
    if env_model:
        return env_model

    # Anthropic-first ordering — the harness-rewrite tradition was first
    # articulated against Anthropic. Don't reorder without updating
    # research/badger/SPEC.md.
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _DEFAULT_ANTHROPIC_MODEL
    if os.environ.get("OPENAI_API_KEY"):
        return _DEFAULT_OPENAI_MODEL
    if os.environ.get("OPENROUTER_API_KEY"):
        return _DEFAULT_OPENROUTER_MODEL
    if os.environ.get("OLLAMA_HOST"):
        return _DEFAULT_OLLAMA_MODEL

    raise ValueError(_NO_KEY_MESSAGE)


def _is_ollama_id(model: str) -> bool:
    """Return True when the model id looks like an Ollama tag.

    Ollama ids carry a ``name:tag`` shape (``qwen3:32b``,
    ``kimi-k2.6:cloud``). The factory's prefix inference doesn't catch
    cloud-tag variants, so we route through OllamaProvider when we see a
    colon-tagged id.
    """
    if "/" in model:
        return False
    return ":" in model


def _build_ollama_provider(model: str, max_tokens: int | None) -> "Provider":
    """Construct an OllamaProvider with sensible context defaults."""
    from chimera.providers.ollama import OllamaProvider

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
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
    ``/`` (the OpenRouter ``vendor/name`` convention).
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False
    return "/" in model


def build_provider(args: argparse.Namespace) -> "Provider":
    """Build a :class:`Provider` for the badger subcommand.

    Honors:

    * ``args.model`` (explicit CLI override).
    * ``$BADGER_MODEL`` (env override).
    * ``args.max_tokens`` — forwarded to providers that accept it as a
      constructor kwarg.

    Args:
        args: Parsed argparse namespace from ``chimera badger``.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.

    Raises:
        ValueError: When no model can be resolved.
    """
    from chimera.providers.factory import create_provider

    model = _resolve_model(args)

    extra_kwargs: dict[str, Any] = {}
    max_tokens = getattr(args, "max_tokens", None)
    if max_tokens is not None:
        extra_kwargs["max_tokens"] = max_tokens

    if _should_use_openrouter(model):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_OPENROUTER_BASE_URL,
            extra_headers=_openrouter_extra_headers(),
        )

    if _is_ollama_id(model):
        return _build_ollama_provider(
            model, max_tokens=max_tokens
        )

    return create_provider(model=model, **extra_kwargs)


__all__ = [
    "build_provider",
]
