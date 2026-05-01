"""Weasel provider wiring (agent W5).

Resolves the model id and constructs a :class:`~chimera.providers.base.Provider`
for the ``chimera weasel`` subcommand. Encapsulates weasel's default chain so
the REPL, one-shot ``-p`` flow, RPC transport, and embedded SDK share a single
code path.

Resolution chain (first match wins):

1. Explicit ``args.model`` (CLI ``--model``).
2. ``$WEASEL_MODEL`` environment variable.
3. ``$OPENAI_API_KEY`` set         -> default :data:`_DEFAULT_OPENAI_MODEL`.
4. ``$ANTHROPIC_API_KEY`` set      -> default :data:`_DEFAULT_ANTHROPIC_MODEL`.
5. ``$OPENROUTER_API_KEY`` set     -> default :data:`_DEFAULT_OPENROUTER_MODEL`
                                      (routed through the OpenAI-compatible
                                      provider against ``openrouter.ai``).
6. ``$LLAMACPP_API_KEY`` set       -> default :data:`_DEFAULT_LLAMACPP_MODEL`
                                      (routed through the OpenAI-compatible
                                      provider against ``127.0.0.1:8888``).
7. ``$OLLAMA_API_KEY`` set         -> default :data:`_DEFAULT_OLLAMA_MODEL`
                                      (routed through ``OllamaProvider`` at
                                      ``127.0.0.1:11434``).
8. Friendly :class:`ValueError` listing every supported env var.

Once a model id is in hand we choose a provider:

* OpenRouter is preferred when ``$OPENROUTER_API_KEY`` is set and the model
  contains a ``/`` separator (the OpenRouter convention, e.g.
  ``openai/gpt-4o``). Routed through the ``compatible`` provider.
* llama.cpp is preferred when ``$LLAMACPP_API_KEY`` is set or the model id
  matches the llama.cpp default; routed through the ``compatible`` provider
  pointed at the local server.
* Ollama is preferred when the model id has the ``name:tag`` shape (mirrors
  otter's tag detection) or when only ``$OLLAMA_API_KEY`` is set among the
  cloud keys.
* Otherwise the regular :func:`chimera.providers.factory.create_provider`
  inference picks Anthropic / OpenAI / Google by model prefix.

Trademark hygiene: this module avoids naming the upstream open-source
coding agent. ``OPENROUTER_API_KEY`` / ``LLAMACPP_API_KEY`` / ``OLLAMA_API_KEY``
are vendor identifiers (not brand claims about the upstream).
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.providers.base import Provider

# WHY: weasel ships defaults that are concrete, supported today, and have
# pricing entries in ``chimera/providers/cost.py``. ``gpt-4o`` is preferred
# over Anthropic at the cloud layer (matches the spec's chain order, which
# prioritises OpenAI when both keys happen to be set).
_DEFAULT_OPENAI_MODEL = "gpt-4o"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o"
# llama.cpp's HTTP server typically serves whatever GGUF is loaded; the
# advertised model id is informational. We pick a generic placeholder so
# callers can override via ``--model`` while still getting a working chain
# when only ``$LLAMACPP_API_KEY`` is set.
_DEFAULT_LLAMACPP_MODEL = "gpt-oss"
# Ollama's default tag for cloud serving; users override via ``--model``
# when they want a specific local id (e.g. ``llama3.2:3b``).
_DEFAULT_OLLAMA_MODEL = "qwen3.5:cloud"
# WHY: ``$XAI_API_KEY`` is a late-binding fallback after every existing
# key path. Users with only an xAI key get ``grok-3``; everyone else
# keeps their current default unless they pass ``--model grok-*``.
_DEFAULT_XAI_MODEL = "grok-3"

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_LLAMACPP_BASE_URL = "http://127.0.0.1:8888/v1"
_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# Local OpenAI-compatible serving defaults — mirror
# :data:`chimera.providers.factory._VLLM_DEFAULT_BASE_URL` /
# ``_SGLANG_DEFAULT_BASE_URL`` so the weasel chain stays in sync with
# the upstream factory.
_VLLM_BASE_URL = "http://localhost:8000/v1"
_SGLANG_BASE_URL = "http://localhost:30000/v1"

# Default model ids when only the corresponding local server probe answers.
# The ``vllm/`` / ``sglang/`` prefix is consumed by
# :func:`chimera.providers.factory.create_provider` (it strips the head
# before forwarding the id to the OpenAI-compatible provider). Keeping
# the prefix in the resolved id makes the ``--list-models`` / chain-step
# disambiguation obvious.
_DEFAULT_VLLM_MODEL = "vllm/qwen3.6-35b-a3b"
_DEFAULT_SGLANG_MODEL = "sglang/qwen3.6-35b-a3b"

# WHY: OpenRouter recommends every client send ``HTTP-Referer`` and
# ``X-Title`` so requests show up identifiably in their dashboard. These
# are cosmetic — the API works without them — but setting them is the
# polite default. Users override via the matching env vars.
_OPENROUTER_DEFAULT_REFERER = "https://github.com/0bserver07/chimera"
_OPENROUTER_DEFAULT_TITLE = "chimera weasel 0.5.0"


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

# WHY: the friendly error message lists every supported env var so users
# know which knobs to flip. Kept as a module constant so tests can assert
# on the exact wording.
_NO_KEY_MESSAGE = (
    "weasel: no provider configured.\n"
    "Set one of:\n"
    "  - OPENAI_API_KEY (default model: "
    f"{_DEFAULT_OPENAI_MODEL})\n"
    "  - ANTHROPIC_API_KEY (default model: "
    f"{_DEFAULT_ANTHROPIC_MODEL})\n"
    "  - OPENROUTER_API_KEY (default model: "
    f"{_DEFAULT_OPENROUTER_MODEL})\n"
    "  - LLAMACPP_API_KEY (default model: "
    f"{_DEFAULT_LLAMACPP_MODEL}, server: "
    f"{_LLAMACPP_BASE_URL})\n"
    "  - OLLAMA_API_KEY (default model: "
    f"{_DEFAULT_OLLAMA_MODEL}, server: "
    f"{_OLLAMA_BASE_URL})\n"
    "  - XAI_API_KEY (default model: "
    f"{_DEFAULT_XAI_MODEL})\n"
    "  - VLLM_API_KEY (default model: "
    f"{_DEFAULT_VLLM_MODEL}, server: "
    f"{_VLLM_BASE_URL})\n"
    "  - SGLANG_API_KEY (default model: "
    f"{_DEFAULT_SGLANG_MODEL}, server: "
    f"{_SGLANG_BASE_URL})\n"
    "or override the model via --model / $WEASEL_MODEL."
)


# ---------------------------------------------------------------------------
# Catalog (used by ``--list-models``)
# ---------------------------------------------------------------------------


def resolved_catalog() -> list[tuple[str, str]]:
    """Return the resolved weasel model catalog.

    Each entry is a ``(model_id, source)`` tuple where ``source`` describes
    which env var / chain step the default would fire from. Used by the
    ``--list-models`` flag and exposed for tests.

    Returns:
        Ordered list of catalog entries, mirroring the resolution chain.
    """
    return [
        (_DEFAULT_OPENAI_MODEL, "OPENAI_API_KEY"),
        (_DEFAULT_ANTHROPIC_MODEL, "ANTHROPIC_API_KEY"),
        (_DEFAULT_OPENROUTER_MODEL, "OPENROUTER_API_KEY"),
        (_DEFAULT_LLAMACPP_MODEL, f"LLAMACPP_API_KEY @ {_LLAMACPP_BASE_URL}"),
        (_DEFAULT_OLLAMA_MODEL, f"OLLAMA_API_KEY @ {_OLLAMA_BASE_URL}"),
        (_DEFAULT_XAI_MODEL, "XAI_API_KEY"),
        (_DEFAULT_VLLM_MODEL, f"VLLM_API_KEY @ {_VLLM_BASE_URL}"),
        (_DEFAULT_SGLANG_MODEL, f"SGLANG_API_KEY @ {_SGLANG_BASE_URL}"),
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
    """Resolve which model id to use for this weasel invocation.

    Implements steps 1-7 of the resolution chain. Step 8 (friendly error)
    is reached by raising :class:`ValueError`.

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

    env_model = os.environ.get("WEASEL_MODEL")
    if env_model:
        return env_model

    if os.environ.get("OPENAI_API_KEY"):
        return _DEFAULT_OPENAI_MODEL
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _DEFAULT_ANTHROPIC_MODEL
    if os.environ.get("OPENROUTER_API_KEY"):
        return _DEFAULT_OPENROUTER_MODEL
    if os.environ.get("LLAMACPP_API_KEY"):
        return _DEFAULT_LLAMACPP_MODEL
    if os.environ.get("OLLAMA_API_KEY"):
        return _DEFAULT_OLLAMA_MODEL
    # Late-binding xAI fallback. Routed via the factory's ``grok-*``
    # prefix inference -> ``xai`` provider -> ``api.x.ai/v1``.
    if os.environ.get("XAI_API_KEY"):
        return _DEFAULT_XAI_MODEL
    # Late-binding local-server fallbacks. ``$VLLM_API_KEY`` /
    # ``$SGLANG_API_KEY`` are presence flags; setting either of them
    # signals "I'm running this server locally, use it as a last resort".
    # An additional reachability probe (250ms timeout) runs only when the
    # presence flag is set so we don't pay for a network round trip on
    # every chain miss.
    if os.environ.get("VLLM_API_KEY") and _probe_vllm():
        return _DEFAULT_VLLM_MODEL
    if os.environ.get("SGLANG_API_KEY") and _probe_sglang():
        return _DEFAULT_SGLANG_MODEL

    raise ValueError(_NO_KEY_MESSAGE)


def _probe_vllm() -> bool:
    """Return ``True`` when a vLLM server answers at ``$VLLM_BASE_URL``.

    Stdlib-only (mirrors :func:`chimera.shrew.providers.probe_ollama`):
    250ms timeout, ``/models`` endpoint, treats 401/403 as "alive".
    Lazy-imports the shared helper from
    :mod:`chimera.providers.factory` so the weasel module stays
    stdlib-clean at import time.
    """
    from chimera.providers.factory import probe_vllm

    return probe_vllm()


def _probe_sglang() -> bool:
    """Return ``True`` when an SGLang server answers at ``$SGLANG_BASE_URL``."""
    from chimera.providers.factory import probe_sglang

    return probe_sglang()


def _is_ollama_id(model: str) -> bool:
    """Return True when the model id looks like an Ollama tag.

    Mirrors :func:`chimera.otter.providers._is_ollama_id`. Ollama ids carry
    a ``name:tag`` shape (``qwen3.5:cloud``, ``llama3.2:3b``,
    ``deepseek-v4-pro:cloud``). The factory's prefix inference doesn't
    catch the cloud-tag variants, so we route through OllamaProvider
    explicitly when we see a colon-tagged id.

    We deliberately skip the ``foo/bar`` OpenRouter shape here because
    those route through :func:`_should_use_openrouter` instead.
    """
    if "/" in model:
        return False
    return ":" in model


def _should_use_openrouter(model: str) -> bool:
    """Return True when the model should be routed through OpenRouter.

    Heuristic mirrors otter's: ``$OPENROUTER_API_KEY`` is set AND the
    model id contains a ``/`` (the OpenRouter ``vendor/name`` convention).

    Args:
        model: The resolved model id.

    Returns:
        ``True`` if the model should route through the OpenAI-compatible
        provider pointed at OpenRouter.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False
    return "/" in model


def _should_use_llamacpp(model: str) -> bool:
    """Return True when the model should be routed through llama.cpp.

    Heuristic: ``$LLAMACPP_API_KEY`` is set, AND either:

    * the model id matches the llama.cpp default (no other env var
      pre-empted the chain), or
    * the user has not set any of the higher-priority cloud keys.

    The check stays narrow on purpose — we don't want to hijack a bare
    ``gpt-4o`` when the user has both ``$OPENAI_API_KEY`` and a stray
    ``$LLAMACPP_API_KEY`` exported in their shell rc.

    Args:
        model: The resolved model id.

    Returns:
        ``True`` if the resolved model should route through the
        OpenAI-compatible provider pointed at the local llama.cpp server.
    """
    if not os.environ.get("LLAMACPP_API_KEY"):
        return False
    # Only kick in when the model is the llama.cpp default, i.e. the
    # resolution chain landed on llama.cpp because no cloud key was set.
    return model == _DEFAULT_LLAMACPP_MODEL


def _should_use_ollama_default(model: str) -> bool:
    """Return True when the chain landed on the Ollama default.

    We hit this path when the user has ``$OLLAMA_API_KEY`` set but no
    higher-priority cloud key, and they didn't pass ``--model``. The
    resolved id will equal :data:`_DEFAULT_OLLAMA_MODEL`.
    """
    if not os.environ.get("OLLAMA_API_KEY"):
        return False
    return model == _DEFAULT_OLLAMA_MODEL


def _build_ollama_provider(model: str, max_tokens: int | None) -> Provider:
    """Construct an :class:`OllamaProvider` with sensible context defaults.

    Mirrors :func:`chimera.otter.providers._build_ollama_provider`. Defaults
    the base URL to ``$OLLAMA_HOST`` or :data:`_OLLAMA_BASE_URL` so users
    don't need to set anything for the standard local install.

    Args:
        model: The resolved Ollama model id.
        max_tokens: Optional hard cap forwarded to the provider.

    Returns:
        A live :class:`OllamaProvider` instance.
    """
    from chimera.providers.ollama import OllamaProvider

    host = os.environ.get("OLLAMA_HOST", _OLLAMA_BASE_URL)
    # Cloud Kimi advertises 262k; deepseek-v4 cloud variants pack the same
    # context window. Default to 262k for ``:cloud``-tagged ids and 131k
    # otherwise, matching otter's heuristic.
    ctx = 262_144 if model.endswith(":cloud") else 131_072
    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": host,
        "context_length": ctx,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return OllamaProvider(**kwargs)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_provider(args: argparse.Namespace) -> Provider:
    """Build a :class:`Provider` for the ``chimera weasel`` subcommand.

    Honors:

    * ``args.model`` (explicit CLI override).
    * ``$WEASEL_MODEL`` (env override).
    * ``args.max_tokens`` — forwarded to providers that accept it as a
      constructor kwarg. Currently only the Ollama and llama.cpp paths use
      it; SDK-backed providers honor ``max_tokens`` per-call, not
      per-instance, so the kwarg is ignored unless explicitly accepted.
    * ``args.no_color`` — read but not consumed here; rendering layers
      consume it. Kept in the signature so callers can pass a single args
      namespace without a separate filter step.

    Args:
        args: Parsed argparse namespace from ``chimera weasel``.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.

    Raises:
        ValueError: When no model can be resolved (no ``--model`` /
            ``$WEASEL_MODEL`` and no provider env var set).
    """
    # Lazy import: the factory imports SDKs (anthropic, openai) on first
    # touch; keeping it inside the function preserves the weasel promise
    # that ``import chimera.weasel.providers`` is stdlib-only.
    from chimera.providers.factory import create_provider

    model = _resolve_model(args)

    # Forward ``no_color`` only if the caller actually attached it; we
    # don't need it for provider construction, but reading it here keeps
    # the API contract documented (and silences linters that want all
    # parameters touched).
    _ = bool(getattr(args, "no_color", False))

    max_tokens = getattr(args, "max_tokens", None)
    extra_kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        extra_kwargs["max_tokens"] = max_tokens

    # --- vLLM / SGLang explicit prefix (handled BEFORE OpenRouter so
    #     the slash form isn't hijacked by a stray $OPENROUTER_API_KEY) ---
    model_lower = model.lower()
    if model_lower.startswith("vllm/"):
        return create_provider(provider_type="vllm", model=model)
    if model_lower.startswith("sglang/"):
        return create_provider(provider_type="sglang", model=model)

    # --- OpenRouter (vendor/name convention) ---
    if _should_use_openrouter(model):
        # Pass cosmetic ``HTTP-Referer`` / ``X-Title`` so OpenRouter's
        # dashboard attributes the traffic correctly.
        api_key = os.environ.get("OPENROUTER_API_KEY")
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_OPENROUTER_BASE_URL,
            extra_headers=_openrouter_extra_headers(),
        )

    # --- llama.cpp local server (OpenAI-compatible) ---
    if _should_use_llamacpp(model):
        api_key = os.environ.get("LLAMACPP_API_KEY")
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_LLAMACPP_BASE_URL,
        )

    # --- Ollama (tag-detected or default-by-key) ---
    if _is_ollama_id(model) or _should_use_ollama_default(model):
        return _build_ollama_provider(model, max_tokens=max_tokens)

    # --- Default: prefix-inference factory ---
    return create_provider(model=model, **extra_kwargs)


__all__ = [
    "build_provider",
    "format_catalog",
    "resolved_catalog",
]
