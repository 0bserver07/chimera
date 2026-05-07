"""Stoat provider wiring — Kimi-first chain.

Resolves the model id and constructs a :class:`~chimera.providers.base.Provider`
for the ``chimera stoat`` subcommand. The chain is **Kimi-first** because
the upstream shell-mode-toggle harness is tuned for Kimi K2.6 chat models
served via the Moonshot API (an OpenAI-compatible endpoint). We never name
the upstream brand in source — but ``$MOONSHOT_API_KEY`` is a vendor
identifier (a fact about the wire protocol), and ``kimi-k2.*`` is a model
family name routed through the OpenAI-compatible adapter.

Resolution chain (first match wins):

1. Explicit ``args.model`` (CLI ``--model``).
2. ``$STOAT_MODEL`` environment variable.
3. ``$MOONSHOT_API_KEY`` set      -> default :data:`_DEFAULT_KIMI_MODEL`
                                     (routed through the OpenAI-compatible
                                     provider against ``api.moonshot.ai``).
4. ``$ANTHROPIC_API_KEY`` set     -> default :data:`_DEFAULT_ANTHROPIC_MODEL`.
5. ``$OPENAI_API_KEY`` set        -> default :data:`_DEFAULT_OPENAI_MODEL`.
6. ``$OPENROUTER_API_KEY`` set    -> default :data:`_DEFAULT_OPENROUTER_MODEL`
                                     (routed through ``compatible``).
7. ``$OLLAMA_API_KEY`` set        -> default :data:`_DEFAULT_OLLAMA_MODEL`.
8. Friendly :class:`ValueError` listing every supported env var.

Trademark hygiene: this module avoids naming the upstream brand. The Kimi
K2.6 model family and Moonshot API base URL are filesystem/wire facts
(documented at the vendor's developer site); the upstream coding-agent
brand that pioneered the shell-mode toggle is never named here.
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.providers.base import Provider


# WHY: Kimi K2.6 is the upstream-tuned default for the shell-mode harness.
# We expose the specific tag so users know what they get without a
# ``--model`` override; substitute via ``$STOAT_MODEL`` or ``--model``.
_DEFAULT_KIMI_MODEL = "kimi-k2.6"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o"
_DEFAULT_OPENROUTER_MODEL = "moonshot/kimi-k2.6"
_DEFAULT_OLLAMA_MODEL = "qwen3.5:cloud"

_MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# WHY: cosmetic OpenRouter headers — same convention used by weasel.
_OPENROUTER_DEFAULT_REFERER = "https://github.com/0bserver07/chimera"
_OPENROUTER_DEFAULT_TITLE = "chimera stoat 0.6.0"


_NO_KEY_MESSAGE = (
    "stoat: no provider configured.\n"
    "Set one of:\n"
    "  - MOONSHOT_API_KEY (default model: "
    f"{_DEFAULT_KIMI_MODEL}, server: {_MOONSHOT_BASE_URL})\n"
    "  - ANTHROPIC_API_KEY (default model: "
    f"{_DEFAULT_ANTHROPIC_MODEL})\n"
    "  - OPENAI_API_KEY (default model: "
    f"{_DEFAULT_OPENAI_MODEL})\n"
    "  - OPENROUTER_API_KEY (default model: "
    f"{_DEFAULT_OPENROUTER_MODEL})\n"
    "  - OLLAMA_API_KEY (default model: "
    f"{_DEFAULT_OLLAMA_MODEL}, server: {_OLLAMA_BASE_URL})\n"
    "or override the model via --model / $STOAT_MODEL."
)


# ---------------------------------------------------------------------------
# Catalog (used by docs / future ``--list-models`` flag)
# ---------------------------------------------------------------------------


def resolved_catalog() -> list[tuple[str, str]]:
    """Return the resolved stoat model catalog.

    Each entry is a ``(model_id, source)`` tuple where ``source`` describes
    which env var / chain step the default would fire from. Mirrors
    :func:`chimera.weasel.providers.resolved_catalog` so the parity matrix
    can render a consistent table across CLIs.

    Returns:
        Ordered list of catalog entries, mirroring the resolution chain.
    """
    return [
        (_DEFAULT_KIMI_MODEL, f"MOONSHOT_API_KEY @ {_MOONSHOT_BASE_URL}"),
        (_DEFAULT_ANTHROPIC_MODEL, "ANTHROPIC_API_KEY"),
        (_DEFAULT_OPENAI_MODEL, "OPENAI_API_KEY"),
        (_DEFAULT_OPENROUTER_MODEL, f"OPENROUTER_API_KEY @ {_OPENROUTER_BASE_URL}"),
        (_DEFAULT_OLLAMA_MODEL, f"OLLAMA_API_KEY @ {_OLLAMA_BASE_URL}"),
    ]


def format_catalog() -> str:
    """Format :func:`resolved_catalog` for terminal display.

    Returns:
        Multi-line string with one ``model<TAB>source`` row per chain step,
        terminated by a trailing newline.
    """
    rows = [f"{model}\t{source}" for model, source in resolved_catalog()]
    return "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve_model(args: argparse.Namespace) -> str:
    """Resolve which model id to use for this stoat invocation.

    Implements the chain documented at the module top. Step 8 (friendly
    error) is reached by raising :class:`ValueError`.

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

    env_model = os.environ.get("STOAT_MODEL")
    if env_model:
        return env_model

    if os.environ.get("MOONSHOT_API_KEY"):
        return _DEFAULT_KIMI_MODEL
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _DEFAULT_ANTHROPIC_MODEL
    if os.environ.get("OPENAI_API_KEY"):
        return _DEFAULT_OPENAI_MODEL
    if os.environ.get("OPENROUTER_API_KEY"):
        return _DEFAULT_OPENROUTER_MODEL
    if os.environ.get("OLLAMA_API_KEY"):
        return _DEFAULT_OLLAMA_MODEL

    raise ValueError(_NO_KEY_MESSAGE)


def _is_kimi_model(model: str) -> bool:
    """Return ``True`` when ``model`` should route through the Moonshot API.

    Heuristic: id starts with ``kimi-`` (case-insensitive) or matches
    :data:`_DEFAULT_KIMI_MODEL`. We deliberately don't match generic
    OpenRouter ids of the form ``moonshot/kimi-…`` here — those route
    through OpenRouter via :func:`_should_use_openrouter`.
    """
    return model.lower().startswith("kimi-")


def _should_use_openrouter(model: str) -> bool:
    """Return ``True`` when the model should be routed through OpenRouter.

    Heuristic: ``$OPENROUTER_API_KEY`` is set AND the model id contains
    a ``/`` (the OpenRouter ``vendor/name`` convention).
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False
    return "/" in model


def _is_ollama_id(model: str) -> bool:
    """Return ``True`` when ``model`` looks like an Ollama tag (``name:tag``)."""
    if "/" in model:
        return False
    return ":" in model


def _openrouter_extra_headers() -> dict[str, str]:
    """Return cosmetic OpenRouter headers (``HTTP-Referer`` / ``X-Title``)."""
    referer = os.environ.get("OPENROUTER_REFERER") or _OPENROUTER_DEFAULT_REFERER
    title = os.environ.get("OPENROUTER_TITLE") or _OPENROUTER_DEFAULT_TITLE
    return {"HTTP-Referer": referer, "X-Title": title}


def _build_kimi_provider(model: str) -> Provider:
    """Construct an OpenAI-compatible provider pointed at Moonshot.

    Args:
        model: A Kimi model id (e.g. ``kimi-k2.6``, ``kimi-k2-thinking``).

    Returns:
        A live :class:`~chimera.providers.base.Provider` configured to
        speak the OpenAI chat-completions wire to ``api.moonshot.ai``.

    Raises:
        ValueError: When ``$MOONSHOT_API_KEY`` is missing.
    """
    from chimera.providers.factory import create_provider

    api_key = os.environ.get("MOONSHOT_API_KEY")
    if not api_key:
        raise ValueError(
            "stoat: $MOONSHOT_API_KEY is required for kimi-* models"
        )
    base_url = os.environ.get("MOONSHOT_BASE_URL") or _MOONSHOT_BASE_URL
    return create_provider(
        provider_type="compatible",
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def _build_ollama_provider(model: str) -> Provider:
    """Construct an :class:`OllamaProvider` with sensible context defaults."""
    from chimera.providers.ollama import OllamaProvider

    host = os.environ.get("OLLAMA_HOST", _OLLAMA_BASE_URL)
    ctx = 262_144 if model.endswith(":cloud") else 131_072
    return OllamaProvider(
        model=model,
        base_url=host,
        context_length=ctx,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_provider(args: argparse.Namespace) -> Provider:
    """Build a :class:`Provider` for the ``chimera stoat`` subcommand.

    Honors:

    * ``args.model`` (explicit CLI override).
    * ``$STOAT_MODEL`` (env override).
    * The Kimi-first chain documented at the module top.

    Args:
        args: Parsed argparse namespace from ``chimera stoat``.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.

    Raises:
        ValueError: When no model can be resolved (no ``--model`` /
            ``$STOAT_MODEL`` and no provider env var set), or when the
            Kimi path is selected without ``$MOONSHOT_API_KEY``.
    """
    # Lazy import — keeps ``import chimera.stoat.providers`` stdlib-only.
    from chimera.providers.factory import create_provider

    model = _resolve_model(args)
    extra_kwargs: dict[str, Any] = {}

    # --- Kimi-first ---
    if _is_kimi_model(model):
        return _build_kimi_provider(model)

    # --- OpenRouter (vendor/name convention) ---
    if _should_use_openrouter(model):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_OPENROUTER_BASE_URL,
            extra_headers=_openrouter_extra_headers(),
        )

    # --- Ollama (tag-detected or default-by-key) ---
    if _is_ollama_id(model):
        return _build_ollama_provider(model)
    if os.environ.get("OLLAMA_API_KEY") and model == _DEFAULT_OLLAMA_MODEL:
        return _build_ollama_provider(model)

    # --- Default: prefix-inference factory ---
    return create_provider(model=model, **extra_kwargs)


__all__ = [
    "build_provider",
    "format_catalog",
    "resolved_catalog",
]
