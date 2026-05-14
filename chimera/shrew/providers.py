"""Shrew provider wiring (agent S5) — small-model-first chain.

Resolves the model id and constructs a :class:`~chimera.providers.base.Provider`
for the ``chimera shrew`` subcommand. Where weasel's chain fronts cloud keys
(OpenAI / Anthropic / OpenRouter) and only falls back to local servers, shrew
**inverts the priority**: a reachable llama.cpp on ``127.0.0.1:8888`` is the
default, Ollama on ``localhost:11434`` is next, and cloud providers are the
fallback. This matches shrew's small-local-model-first thesis.

Resolution chain (first match wins):

1. Explicit ``args.model`` (CLI ``--model``).
2. ``$SHREW_MODEL`` environment variable.
3. **llama.cpp** at ``$LLAMACPP_BASE_URL`` (default
   ``http://127.0.0.1:8888/v1``) — probed via ``/health`` then
   ``/v1/models``. Default model: :data:`_DEFAULT_LLAMACPP_MODEL`
   (``qwen3.6-35b-a3b``). Routed through the OpenAI-compatible provider.
4. **vLLM** at ``$VLLM_BASE_URL`` (default ``http://localhost:8000/v1``)
   — probed via ``/v1/models``. Default model: :data:`_DEFAULT_VLLM_MODEL`.
   Routed through the OpenAI-compatible provider.
5. **SGLang** at ``$SGLANG_BASE_URL`` (default
   ``http://localhost:30000/v1``) — probed via ``/v1/models``. Default
   model: :data:`_DEFAULT_SGLANG_MODEL`. Routed through the
   OpenAI-compatible provider.
6. **Ollama** at ``$OLLAMA_BASE_URL`` (default ``http://localhost:11434``)
   — probed via ``/api/tags``. Default model: :data:`_DEFAULT_OLLAMA_MODEL`
   (``qwen3.5:cloud``). Routed through the OpenAI-compatible provider
   (``/v1`` shim) so tool-calling shape matches the rest of the catalog.
7. ``$ANTHROPIC_API_KEY`` set      -> :data:`_DEFAULT_ANTHROPIC_MODEL`.
8. ``$OPENAI_API_KEY`` set         -> :data:`_DEFAULT_OPENAI_MODEL`.
9. ``$OPENROUTER_API_KEY`` set     -> :data:`_DEFAULT_OPENROUTER_MODEL`
                                      (routed through the OpenAI-compatible
                                      provider against ``openrouter.ai``).
10. Friendly :class:`ValueError` listing every supported env var.

Once a model id is in hand we choose a provider:

* The model id ``qwen3.6-35b-a3b`` (or anything in the local llama.cpp
  catalog) routes through ``compatible`` against ``$LLAMACPP_BASE_URL``.
* The model id ``qwen3.5:cloud`` (or any colon-tagged Ollama id) routes
  through ``compatible`` against ``$OLLAMA_BASE_URL`` + ``/v1``.
* The OpenRouter ``vendor/name`` shape routes through ``compatible``
  against ``openrouter.ai``.
* Otherwise the regular :func:`chimera.providers.factory.create_provider`
  inference picks Anthropic / OpenAI / Google by model prefix.

Trademark hygiene: this module avoids naming the upstream open-source
small-model coding agent. ``LLAMACPP_BASE_URL`` / ``OLLAMA_BASE_URL`` are
generic vendor identifiers; ``qwen3.6-35b-a3b`` is the public Qwen MoE
checkpoint id.

Stdlib only at module import time. ``urllib.request`` powers the probe;
``httpx`` is only touched inside provider constructors that the factory
late-binds.
"""

from __future__ import annotations

import argparse
import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.providers.base import Provider


# ---------------------------------------------------------------------------
# Defaults + base URLs
# ---------------------------------------------------------------------------


_DEFAULT_LLAMACPP_MODEL = "qwen3.6-35b-a3b"
"""Default llama.cpp model id when only the local server is reachable.

Qwen3.6-35B-A3B is the upstream small-model coding agent's preferred MoE
checkpoint — small enough to run on a 32-64 GB Mac with 4-bit quantisation,
big enough to handle real coding tasks. The id is informational; llama.cpp
serves whatever GGUF is loaded regardless of the advertised name."""

_DEFAULT_VLLM_MODEL = "vllm/qwen3.6-35b-a3b"
"""Default vLLM model id when a vLLM server is the only reachable backend.

The ``vllm/`` prefix is stripped by
:func:`chimera.providers.factory.create_provider` before the request
reaches the server, but it keeps the id distinguishable from the
llama.cpp default (which serves the same Qwen MoE checkpoint on a
different port). vLLM serves whatever ``--model`` was passed at server
start; the id is informational and overridable via ``--model`` /
``$SHREW_MODEL``."""

_DEFAULT_SGLANG_MODEL = "sglang/qwen3.6-35b-a3b"
"""Default SGLang model id when an SGLang server is the only reachable backend.

The ``sglang/`` prefix mirrors vLLM's behaviour: it tags the chain step
that resolved this id without affecting what the server itself sees."""

_DEFAULT_OLLAMA_MODEL = "qwen3.5:cloud"
"""Default Ollama tag when llama.cpp isn't reachable but Ollama is."""

_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o"
_DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o"
# WHY: ``$XAI_API_KEY`` is added as a late-binding fallback after the
# small-model-first chain. Local servers and existing cloud keys keep
# precedence; xAI only kicks in when nothing else resolves.
_DEFAULT_XAI_MODEL = "grok-3"

_LLAMACPP_DEFAULT_BASE_URL = "http://127.0.0.1:8888/v1"
"""Default llama.cpp HTTP server base URL — overridable via
``$LLAMACPP_BASE_URL``."""

_VLLM_DEFAULT_BASE_URL = "http://localhost:8000/v1"
"""Default vLLM HTTP server base URL — overridable via ``$VLLM_BASE_URL``.

Mirrors :data:`chimera.providers.factory._VLLM_DEFAULT_BASE_URL`. vLLM's
upstream documentation defaults to port ``8000``; we keep the same value
here so users running ``vllm serve <model>`` with no extra flags land on
shrew's chain automatically."""

_SGLANG_DEFAULT_BASE_URL = "http://localhost:30000/v1"
"""Default SGLang HTTP server base URL — overridable via ``$SGLANG_BASE_URL``.

Mirrors :data:`chimera.providers.factory._SGLANG_DEFAULT_BASE_URL`. SGLang's
upstream documentation defaults to port ``30000``."""

_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
"""Default Ollama daemon base URL — overridable via ``$OLLAMA_BASE_URL``.

We always append ``/v1`` to this when constructing the OpenAI-compatible
provider URL because Ollama exposes its OpenAI-shape endpoint at ``/v1``."""

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Per-probe socket timeout. Kept tight (250ms) so the chain feels snappy
# when the local server isn't running — users don't want shrew to hang on
# a stale base URL.
_PROBE_TIMEOUT_SECONDS = 0.25


# ---------------------------------------------------------------------------
# Catalog (model id -> capability metadata)
# ---------------------------------------------------------------------------


_CATALOG: dict[str, dict[str, Any]] = {
    # Local llama.cpp ids (from the upstream models.json).
    "qwen3.6-35b-a3b": {
        "context_window": 32_768,
        "max_output_tokens": 4_096,
        "moe": True,
        "backend": "llamacpp",
    },
    "qwen3.5-9b": {
        "context_window": 32_768,
        "max_output_tokens": 4_096,
        "moe": False,
        "backend": "llamacpp",
    },
    # Ollama ids — colon-tagged. ``:cloud`` advertises 262k context.
    "qwen3.5:cloud": {
        "context_window": 262_144,
        "max_output_tokens": 8_192,
        "moe": False,
        "backend": "ollama",
    },
    "qwen3.5": {
        "context_window": 32_768,
        "max_output_tokens": 4_096,
        "moe": False,
        "backend": "ollama",
    },
}
"""Model capability catalog used by ``--list-models`` and the
context-window heuristics in :mod:`chimera.shrew.extensions.moe_offload`.

Each entry records:

* ``context_window`` — provider-side max prompt+response tokens.
* ``max_output_tokens`` — sane default cap for new generations.
* ``moe`` — True when the checkpoint is a Mixture-of-Experts model
  (drives the MoE-offload extension's RAM/GPU split heuristic).
* ``backend`` — which probe path the id maps to (``llamacpp`` or
  ``ollama``); not authoritative for routing (the resolution chain
  re-derives that from env vars + probes), but exposed so docs and
  ``--list-models`` can show the user where each id will land.
"""


def get_catalog() -> dict[str, dict[str, Any]]:
    """Return a shallow copy of the model capability catalog.

    Returns:
        A copy of :data:`_CATALOG`. Callers must not mutate the inner
        dicts — they are shared with the module-level constant.
    """
    return dict(_CATALOG)


# ---------------------------------------------------------------------------
# Friendly error message
# ---------------------------------------------------------------------------


_NO_KEY_MESSAGE = (
    "shrew: no provider configured.\n"
    "Set one of:\n"
    "  - Run a llama.cpp HTTP server on $LLAMACPP_BASE_URL "
    f"(default {_LLAMACPP_DEFAULT_BASE_URL}; default model: "
    f"{_DEFAULT_LLAMACPP_MODEL}).\n"
    "  - Run a vLLM HTTP server on $VLLM_BASE_URL "
    f"(default {_VLLM_DEFAULT_BASE_URL}; default model: "
    f"{_DEFAULT_VLLM_MODEL}).\n"
    "  - Run an SGLang HTTP server on $SGLANG_BASE_URL "
    f"(default {_SGLANG_DEFAULT_BASE_URL}; default model: "
    f"{_DEFAULT_SGLANG_MODEL}).\n"
    "  - Run Ollama on $OLLAMA_BASE_URL "
    f"(default {_OLLAMA_DEFAULT_BASE_URL}; default model: "
    f"{_DEFAULT_OLLAMA_MODEL}).\n"
    "  - ANTHROPIC_API_KEY (default model: "
    f"{_DEFAULT_ANTHROPIC_MODEL}).\n"
    "  - OPENAI_API_KEY (default model: "
    f"{_DEFAULT_OPENAI_MODEL}).\n"
    "  - OPENROUTER_API_KEY (default model: "
    f"{_DEFAULT_OPENROUTER_MODEL}).\n"
    "  - XAI_API_KEY (default model: "
    f"{_DEFAULT_XAI_MODEL}).\n"
    "or override the model via --model / $SHREW_MODEL."
)


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------


def _http_probe(
    url: str,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
    accept_auth_errors: bool = False,
) -> bool:
    """Return True when an HTTP GET to *url* yields a 2xx/3xx response.

    Uses :mod:`urllib.request` so the probe is stdlib-only and adds zero
    import-time cost. Any exception (connection refused, DNS failure,
    timeout, non-2xx) returns ``False``.

    Args:
        url: Full HTTP/HTTPS URL to probe.
        timeout: Per-request socket timeout, seconds. Defaults to a tight
            value so the resolution chain feels responsive.
        accept_auth_errors: When ``True``, treat 401/403 as "alive". The
            OpenAI-compat ``/v1/models`` endpoint may demand a key even
            when we just want a liveness signal; for those callers a
            ``Unauthorized`` answer is good enough. We deliberately do
            not blanket-accept all 4xx because a 404 means "endpoint
            doesn't exist", which is a distinct signal from "server is
            up and authenticated".

    Returns:
        ``True`` when the server answered with a non-error status, else
        ``False``.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(status) < 400
    except urllib.error.HTTPError as err:
        if accept_auth_errors and err.code in (401, 403):
            return True
        return False
    except Exception:  # noqa: BLE001 — any failure means "unreachable"
        return False


def _llamacpp_base_url() -> str:
    """Return the configured llama.cpp base URL (with trailing slash trimmed)."""
    return os.environ.get(
        "LLAMACPP_BASE_URL", _LLAMACPP_DEFAULT_BASE_URL,
    ).rstrip("/")


def _vllm_base_url() -> str:
    """Return the configured vLLM base URL (with trailing slash trimmed)."""
    return os.environ.get(
        "VLLM_BASE_URL", _VLLM_DEFAULT_BASE_URL,
    ).rstrip("/")


def _sglang_base_url() -> str:
    """Return the configured SGLang base URL (with trailing slash trimmed)."""
    return os.environ.get(
        "SGLANG_BASE_URL", _SGLANG_DEFAULT_BASE_URL,
    ).rstrip("/")


def _ollama_base_url() -> str:
    """Return the configured Ollama base URL (with trailing slash trimmed).

    Resolution order: ``$OLLAMA_BASE_URL`` > ``$OLLAMA_HOST`` > local daemon.
    Both env names are accepted because OLLAMA_HOST is the canonical name
    Ollama itself documents; OLLAMA_BASE_URL is the shrew-specific override
    kept for backwards compat. Bare hostnames (e.g. ``ollama.com``) get an
    ``https://`` scheme prepended.
    """
    url = (
        os.environ.get("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or _OLLAMA_DEFAULT_BASE_URL
    ).rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def probe_llamacpp(base_url: str | None = None) -> bool:
    """Return True when a llama.cpp HTTP server answers at *base_url*.

    Tries ``/health`` first (the standard llama.cpp readiness endpoint);
    falls back to ``/models`` (which the OpenAI-compatible shim always
    serves) when ``/health`` is missing or returns 404.

    Args:
        base_url: Override the ``$LLAMACPP_BASE_URL`` lookup. When
            ``None`` we read the env var (with a default of
            :data:`_LLAMACPP_DEFAULT_BASE_URL`).

    Returns:
        ``True`` if either probe succeeds, else ``False``.
    """
    url = (base_url or _llamacpp_base_url()).rstrip("/")
    # llama.cpp's standard ``/health`` lives at the server root, NOT
    # under the ``/v1`` OpenAI shim. We strip a trailing ``/v1`` so the
    # probe hits the actual liveness endpoint.
    root = url[: -len("/v1")] if url.endswith("/v1") else url
    if _http_probe(f"{root}/health"):
        return True
    # Fall back to the OpenAI-compat ``/models`` listing — accept
    # 401/403 because that proves the server is up and authenticating.
    return _http_probe(f"{url}/models", accept_auth_errors=True)


def probe_ollama(base_url: str | None = None) -> bool:
    """Return True when an Ollama daemon answers at *base_url*.

    Probes ``/api/tags`` (the canonical Ollama listing endpoint).

    Args:
        base_url: Override the ``$OLLAMA_BASE_URL`` lookup.

    Returns:
        ``True`` when the daemon answers, else ``False``.
    """
    url = (base_url or _ollama_base_url()).rstrip("/")
    return _http_probe(f"{url}/api/tags")


def probe_vllm(base_url: str | None = None) -> bool:
    """Return True when a vLLM server answers at *base_url*.

    Probes ``/models`` (the OpenAI-compatible listing endpoint, which vLLM
    serves under its ``/v1`` mount). Auth-error responses (401/403) are
    treated as "alive" because vLLM may demand ``--api-key`` even when we
    only want a liveness signal.

    Args:
        base_url: Override the ``$VLLM_BASE_URL`` lookup.

    Returns:
        ``True`` when the server answers, else ``False``.
    """
    url = (base_url or _vllm_base_url()).rstrip("/")
    return _http_probe(f"{url}/models", accept_auth_errors=True)


def probe_sglang(base_url: str | None = None) -> bool:
    """Return True when an SGLang server answers at *base_url*.

    Probes ``/models`` (the OpenAI-compatible listing endpoint).

    Args:
        base_url: Override the ``$SGLANG_BASE_URL`` lookup.

    Returns:
        ``True`` when the server answers, else ``False``.
    """
    url = (base_url or _sglang_base_url()).rstrip("/")
    return _http_probe(f"{url}/models", accept_auth_errors=True)


# ---------------------------------------------------------------------------
# Catalog formatting
# ---------------------------------------------------------------------------


def resolved_catalog() -> list[tuple[str, str]]:
    """Return the resolved shrew model catalog.

    Each entry is a ``(model_id, source)`` tuple where ``source``
    describes which env var / chain step the default would fire from.
    Used by ``chimera shrew --list-models``.

    Returns:
        Ordered list of catalog entries, mirroring the resolution chain.
    """
    return [
        (
            _DEFAULT_LLAMACPP_MODEL,
            f"llama.cpp @ {_LLAMACPP_DEFAULT_BASE_URL}",
        ),
        (
            _DEFAULT_VLLM_MODEL,
            f"vllm @ {_VLLM_DEFAULT_BASE_URL}",
        ),
        (
            _DEFAULT_SGLANG_MODEL,
            f"sglang @ {_SGLANG_DEFAULT_BASE_URL}",
        ),
        (
            _DEFAULT_OLLAMA_MODEL,
            f"ollama @ {_OLLAMA_DEFAULT_BASE_URL}",
        ),
        (_DEFAULT_ANTHROPIC_MODEL, "ANTHROPIC_API_KEY"),
        (_DEFAULT_OPENAI_MODEL, "OPENAI_API_KEY"),
        (_DEFAULT_OPENROUTER_MODEL, "OPENROUTER_API_KEY"),
        (_DEFAULT_XAI_MODEL, "XAI_API_KEY"),
    ]


def format_catalog() -> str:
    """Format :func:`resolved_catalog` for terminal display.

    Returns:
        Multi-line ``model<TAB>source`` rows, terminated by ``\\n``.
    """
    rows = [f"{model}\t{source}" for model, source in resolved_catalog()]
    return "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve_model(args: argparse.Namespace) -> str:
    """Resolve which model id to use for this shrew invocation.

    Implements steps 1-7 of the resolution chain. Step 8 (friendly
    error) is reached by raising :class:`ValueError`.

    Args:
        args: Parsed argparse namespace. Reads ``args.model`` if present.

    Returns:
        The model id string to feed to the provider factory.

    Raises:
        ValueError: When no explicit model and nothing in the chain
            resolves to a usable backend.
    """
    explicit = getattr(args, "model", None)
    if explicit:
        return str(explicit)

    env_model = os.environ.get("SHREW_MODEL")
    if env_model:
        return env_model

    # Local backends come first — shrew is small-model-first.
    if probe_llamacpp():
        return _DEFAULT_LLAMACPP_MODEL
    if probe_vllm():
        return _DEFAULT_VLLM_MODEL
    if probe_sglang():
        return _DEFAULT_SGLANG_MODEL
    if probe_ollama():
        return _DEFAULT_OLLAMA_MODEL

    # Cloud fallbacks. Anthropic before OpenAI to mirror otter (and
    # diverge from weasel) — small-model users who reach for a cloud key
    # tend to want Claude for the harder tasks.
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _DEFAULT_ANTHROPIC_MODEL
    if os.environ.get("OPENAI_API_KEY"):
        return _DEFAULT_OPENAI_MODEL
    if os.environ.get("OPENROUTER_API_KEY"):
        return _DEFAULT_OPENROUTER_MODEL
    # Late-binding xAI fallback — last resort before raising. Routed via
    # the factory's ``grok-*`` prefix inference -> ``xai`` provider.
    if os.environ.get("XAI_API_KEY"):
        return _DEFAULT_XAI_MODEL

    raise ValueError(_NO_KEY_MESSAGE)


def _is_ollama_id(model: str) -> bool:
    """Return True when *model* looks like an Ollama tag (``name:tag``).

    Mirrors :func:`chimera.weasel.providers._is_ollama_id`. Skips
    OpenRouter ``vendor/name`` shapes (those route via the slash check).
    """
    if "/" in model:
        return False
    return ":" in model


def _is_llamacpp_id(model: str) -> bool:
    """Return True when *model* is in the local llama.cpp catalog.

    Currently driven by :data:`_CATALOG` membership — any model whose
    backend metadata says ``llamacpp`` is treated as a llama.cpp id.
    """
    entry = _CATALOG.get(model)
    if entry is None:
        return False
    return entry.get("backend") == "llamacpp"


def _should_use_openrouter(model: str) -> bool:
    """Return True when the model should route through OpenRouter.

    Heuristic: ``$OPENROUTER_API_KEY`` is set AND the model id contains
    a ``/`` (the OpenRouter ``vendor/name`` convention).
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False
    return "/" in model


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_provider(args: argparse.Namespace) -> Provider:
    """Build a :class:`Provider` for the ``chimera shrew`` subcommand.

    Honors:

    * ``args.model`` (explicit CLI override).
    * ``$SHREW_MODEL`` (env override).
    * ``$LLAMACPP_BASE_URL`` / ``$OLLAMA_BASE_URL`` for local servers.
    * ``$LLAMACPP_API_KEY`` / ``$OLLAMA_API_KEY`` (passed through to the
      OpenAI-compatible provider; most local setups don't need a key,
      but the kwarg is there when the upstream server gates on one).
    * ``args.max_tokens`` / ``args.no_color`` — read defensively via
      ``getattr`` so callers can pass a bare namespace.

    Args:
        args: Parsed argparse namespace from ``chimera shrew``.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.

    Raises:
        ValueError: When no model can be resolved (no ``--model`` /
            ``$SHREW_MODEL``, no reachable local server, no cloud key).
    """
    # Lazy import: the factory imports SDKs on first touch; keeping it
    # inside the function preserves the shrew promise that
    # ``import chimera.shrew.providers`` is stdlib-only.
    from chimera.providers.factory import create_provider

    model = _resolve_model(args)

    # Read no_color for API contract documentation (and to satisfy
    # linters that want every namespace attribute touched). The actual
    # rendering layer consumes it elsewhere.
    _ = bool(getattr(args, "no_color", False))

    max_tokens = getattr(args, "max_tokens", None)

    # --- vLLM / SGLang explicit prefix (handled BEFORE OpenRouter so the
    #     ``vllm/`` / ``sglang/`` slash form isn't hijacked by a stray
    #     ``$OPENROUTER_API_KEY``) ---
    model_lower = model.lower()
    if model_lower.startswith("vllm/"):
        return create_provider(provider_type="vllm", model=model)
    if model_lower.startswith("sglang/"):
        return create_provider(provider_type="sglang", model=model)

    # --- OpenRouter (vendor/name convention) ---
    if _should_use_openrouter(model):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_OPENROUTER_BASE_URL,
        )

    # --- llama.cpp local server (OpenAI-compatible) ---
    # We hit this branch when:
    # (a) the resolved id is in our llama.cpp catalog, OR
    # (b) the user explicitly named a local-shaped id and the server
    #     answers a probe.
    if _is_llamacpp_id(model) or (
        model == _DEFAULT_LLAMACPP_MODEL and probe_llamacpp()
    ):
        api_key = os.environ.get("LLAMACPP_API_KEY") or "sk-noauth"
        catalog_entry = _CATALOG.get(model, {})
        ctx = int(catalog_entry.get("context_window", 32_768))
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=_llamacpp_base_url(),
            context_length=ctx,
        )

    # --- Ollama (tag-detected or default-by-probe) ---
    # We always go through the OpenAI-compatible shim for Ollama in
    # shrew (rather than ``OllamaProvider``) so the wire shape is
    # identical to llama.cpp — small-model evaluation depends on having
    # one tool-calling shape across the catalog.
    if _is_ollama_id(model) or model == _DEFAULT_OLLAMA_MODEL:
        api_key = os.environ.get("OLLAMA_API_KEY") or "sk-noauth"
        catalog_entry = _CATALOG.get(model, {})
        ctx = int(catalog_entry.get("context_window", 32_768))
        # Ollama exposes its OpenAI-compat shim under ``/v1``.
        return create_provider(
            provider_type="compatible",
            model=model,
            api_key=api_key,
            base_url=f"{_ollama_base_url()}/v1",
            context_length=ctx,
        )

    # --- Default: prefix-inference factory (anthropic / openai / google) ---
    extra_kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        extra_kwargs["max_tokens"] = max_tokens
    return create_provider(model=model, **extra_kwargs)


__all__ = [
    "build_provider",
    "format_catalog",
    "get_catalog",
    "probe_llamacpp",
    "probe_ollama",
    "probe_sglang",
    "probe_vllm",
    "resolved_catalog",
]
