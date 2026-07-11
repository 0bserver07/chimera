# chimera/providers/modal_endpoint.py
"""Modal managed-Endpoints provider (OpenAI-compatible, proxy-token auth).

Modal's managed **Endpoints** feature (``modal endpoint create --model
<hf-repo-id>``) serves catalog models — the GLM, Qwen, Gemma, DeepSeek,
Kimi, Nemotron, and GPT-OSS families — behind an OpenAI Chat Completions
API mounted under ``/v1`` at the endpoint URL. The ``model`` request
parameter is the base model's Hugging Face repo id. Authentication uses
**workspace proxy tokens** (``modal workspace proxy-tokens create``) sent
as the request headers ``Modal-Key`` / ``Modal-Secret`` — *not* an
``Authorization: Bearer`` token — unless the endpoint was created with
``--unauthenticated``.

This provider complements :class:`chimera.providers.modal.ModalProvider`
(the older self-deployed-vLLM path). Use *this* one when Modal manages the
endpoint for you; use the old one when you deploy your own vLLM app.

Three API tiers
---------------

1. **One-liner** — the ``modal-endpoint/<hf-repo-id>`` model-string
   convention. The endpoint URL is discovered via the local ``modal`` CLI
   (``modal endpoint list --json``) and proxy tokens come from
   ``$MODAL_PROXY_TOKEN_ID`` / ``$MODAL_PROXY_TOKEN_SECRET``::

       from chimera.providers.factory import create_provider

       provider = create_provider(model="modal-endpoint/zai-org/GLM-5.2-FP8")

2. **Configured** — explicit endpoint URL and tokens; no CLI involved::

       from chimera.providers.modal_endpoint import ModalEndpointProvider

       provider = ModalEndpointProvider(
           model="zai-org/GLM-5.2-FP8",
           base_url="https://myworkspace--glm-5-2-fp8.modal.run",  # /v1 optional
           token_id="wk-...",
           token_secret="ws-...",
       )

3. **Subclassable** — override the discovery hook (or anything inherited
   from :class:`~chimera.providers.compatible.OpenAICompatibleProvider`)
   for custom fleets::

       class PinnedEndpoints(ModalEndpointProvider):
           def _discover_base_url(self, model: str) -> str:
               return MY_FLEET[model]

Model-string convention: ``modal-endpoint/<hf-repo-id>`` (e.g.
``modal-endpoint/zai-org/GLM-5.2-FP8``). The prefix equals the registry
name, exactly the scheme ``vllm/…`` and ``sglang/…`` already use;
everything after the first ``/`` is the Hugging Face repo id, which is
also the ``model`` parameter the endpoint expects on the wire.

Dependency posture: stdlib only at import time (``json``, ``os``,
``subprocess``); the HTTP transport is ``httpx`` via the inherited
OpenAI-compatible provider — no new dependency.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING, Any

from chimera.providers.compatible import OpenAICompatibleProvider

if TYPE_CHECKING:
    from chimera.providers.compatible import CompatFlags

MODAL_ENDPOINT_PREFIX = "modal-endpoint/"
"""Model-string prefix routing to this provider (matches the registry name)."""

MODAL_PROXY_TOKEN_ID_ENV = "MODAL_PROXY_TOKEN_ID"
"""Env var holding the proxy-token id (the ``Modal-Key`` header value)."""

MODAL_PROXY_TOKEN_SECRET_ENV = "MODAL_PROXY_TOKEN_SECRET"
"""Env var holding the proxy-token secret (the ``Modal-Secret`` header value)."""

MODAL_ENVIRONMENT_ENV = "MODAL_ENVIRONMENT"
"""Env var naming the Modal environment used for endpoint discovery."""

_MODAL_CLI_TIMEOUT_SECONDS = 30.0
"""Per-invocation timeout for ``modal endpoint list`` (the CLI can be slow)."""

# ``modal endpoint list --json`` field names are not publicly documented
# (the guide shows the command but not its schema), so the parser accepts
# the obvious aliases for each concept and errors loudly — never silently —
# when nothing matches. See tests/providers/test_modal_endpoint.py for the
# crafted fixture these are exercised against.
_MODEL_FIELDS = ("model", "base_model", "model_id", "hf_repo")
_URL_FIELDS = ("url", "endpoint_url", "base_url")
_NAME_FIELDS = ("name", "endpoint_name", "label")


def normalize_endpoint_base_url(url: str) -> str:
    """Normalize a Modal endpoint URL to its OpenAI-compatible ``/v1`` root.

    Modal surfaces the endpoint URL without the API mount (e.g.
    ``https://myworkspace--glm-5-2-fp8.modal.run``) while the Chat
    Completions API lives under ``/v1``. Callers may paste either form.

    Args:
        url: Endpoint URL with or without a trailing ``/v1`` (trailing
            slashes tolerated).

    Returns:
        The URL ending in ``/v1`` and free of trailing slashes, ready for
        the OpenAI-compatible provider (which appends ``/chat/completions``).

    Raises:
        ValueError: If *url* is empty or whitespace.
    """
    trimmed = url.strip().rstrip("/")
    if not trimmed:
        raise ValueError(
            "base_url is empty. Pass the Modal endpoint URL "
            "(e.g. 'https://myworkspace--my-endpoint.modal.run')."
        )
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"


def _resolve_proxy_tokens(
    token_id: str | None,
    token_secret: str | None,
) -> tuple[str, str]:
    """Resolve the proxy-token pair from args or environment.

    Args:
        token_id: Explicit token id (the ``Modal-Key`` header value).
        token_secret: Explicit token secret (the ``Modal-Secret`` value).

    Returns:
        ``(token_id, token_secret)`` with env fallbacks applied.

    Raises:
        ValueError: When either half is missing — with the exact commands
            that fix it. Never falls back silently.
    """
    resolved_id = token_id or os.environ.get(MODAL_PROXY_TOKEN_ID_ENV, "")
    resolved_secret = token_secret or os.environ.get(MODAL_PROXY_TOKEN_SECRET_ENV, "")
    if resolved_id and resolved_secret:
        return resolved_id, resolved_secret

    missing = []
    if not resolved_id:
        missing.append(MODAL_PROXY_TOKEN_ID_ENV)
    if not resolved_secret:
        missing.append(MODAL_PROXY_TOKEN_SECRET_ENV)
    raise ValueError(
        f"Modal endpoint auth is not configured (missing: {', '.join(missing)}).\n"
        "Managed Modal endpoints authenticate with workspace proxy tokens sent as\n"
        "the 'Modal-Key' / 'Modal-Secret' request headers. Fix one of:\n"
        "  1. Create a token pair and export it:\n"
        "       modal workspace proxy-tokens create\n"
        f"       export {MODAL_PROXY_TOKEN_ID_ENV}='wk-...'\n"
        f"       export {MODAL_PROXY_TOKEN_SECRET_ENV}='ws-...'\n"
        "  2. Pass token_id= / token_secret= to ModalEndpointProvider.\n"
        "  3. Pass unauthenticated=True if the endpoint was created with\n"
        "     'modal endpoint create --unauthenticated'."
    )


def list_modal_endpoints(
    env: str | None = None,
    timeout: float = _MODAL_CLI_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """List managed endpoints via ``modal endpoint list --json`` (read-only).

    Args:
        env: Modal environment to scope the listing (``--env <env>``).
            ``None`` uses the CLI's default environment.
        timeout: Seconds to wait for the CLI before giving up.

    Returns:
        The endpoint entries as dicts. A top-level JSON list is used
        directly; a dict wrapping the list under ``endpoints`` / ``items``
        / ``data`` is unwrapped (the CLI's JSON schema is not publicly
        documented, so both shapes are accepted).

    Raises:
        ValueError: The ``modal`` CLI is missing, or is too old to know the
            ``endpoint`` subcommand — with install/upgrade instructions.
        RuntimeError: The CLI ran but failed (non-zero exit, timeout, or
            output that is not the expected JSON).
    """
    cmd = ["modal", "endpoint", "list", "--json"]
    if env:
        cmd += ["--env", env]
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as err:
        raise ValueError(
            "The 'modal' CLI was not found, so the endpoint URL cannot be "
            "discovered.\nEither install it (pip install modal && modal setup) "
            "or skip discovery by passing base_url= (the endpoint URL from the "
            "Modal dashboard)."
        ) from err
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(
            f"'{' '.join(cmd)}' timed out after {timeout:.0f}s. Check your "
            "network / Modal auth (modal setup), or pass base_url= to skip "
            "discovery."
        ) from err

    if proc.returncode != 0:
        combined = f"{proc.stderr}\n{proc.stdout}".lower()
        if "no such command" in combined:
            raise ValueError(
                "Your 'modal' CLI does not know the 'endpoint' subcommand — "
                "managed Endpoints need a newer client.\nUpgrade with "
                "'pip install --upgrade modal', or pass base_url= (the "
                "endpoint URL from the Modal dashboard) to skip discovery."
            )
        detail = proc.stderr.strip() or proc.stdout.strip() or "<no output>"
        raise RuntimeError(
            f"'{' '.join(cmd)}' failed (exit {proc.returncode}):\n{detail}"
        )

    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as err:
        raise RuntimeError(
            f"'{' '.join(cmd)}' returned output that is not valid JSON:\n"
            f"{(proc.stdout or '').strip()[:500] or '<empty>'}"
        ) from err

    if isinstance(data, dict):
        for key in ("endpoints", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
        else:
            raise RuntimeError(
                f"'{' '.join(cmd)}' returned an unrecognized JSON shape "
                f"(top-level keys: {sorted(data.keys())}). Pass base_url= to "
                "skip discovery."
            )
    if not isinstance(data, list):
        raise RuntimeError(
            f"'{' '.join(cmd)}' returned {type(data).__name__}, expected a "
            "JSON list of endpoints. Pass base_url= to skip discovery."
        )
    return [entry for entry in data if isinstance(entry, dict)]


def _entry_field(entry: dict[str, Any], candidates: tuple[str, ...]) -> str:
    """Return the first non-empty string among *candidates* keys of *entry*."""
    for key in candidates:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _describe_endpoints(endpoints: list[dict[str, Any]]) -> str:
    """One-line-per-endpoint summary for error messages."""
    lines = []
    for entry in endpoints:
        name = _entry_field(entry, _NAME_FIELDS) or "<unnamed>"
        model = _entry_field(entry, _MODEL_FIELDS) or "<unknown model>"
        lines.append(f"  - {name}: {model}")
    return "\n".join(lines)


def discover_endpoint_base_url(
    model: str,
    env: str | None = None,
    timeout: float = _MODAL_CLI_TIMEOUT_SECONDS,
) -> str:
    """Resolve the endpoint URL serving *model* via the ``modal`` CLI.

    Args:
        model: Base model repo id to match (e.g. ``"zai-org/GLM-5.2-FP8"``),
            compared case-insensitively against each endpoint's model field.
        env: Modal environment to search (``--env``); ``None`` = default.
        timeout: Seconds to wait for the CLI.

    Returns:
        The matched endpoint's URL (not yet ``/v1``-normalized).

    Raises:
        ValueError: No endpoints exist, none serve *model*, several do
            (ambiguous), or the matched entry exposes no URL — each with
            the exact command or argument that fixes it.
        RuntimeError: Propagated CLI failures from
            :func:`list_modal_endpoints`.
    """
    endpoints = list_modal_endpoints(env=env, timeout=timeout)
    scope = f" in Modal environment '{env}'" if env else ""
    if not endpoints:
        raise ValueError(
            f"No Modal endpoints exist{scope}. Create one with:\n"
            f"  modal endpoint create --model {model}\n"
            "then retry, or pass base_url= explicitly."
        )

    wanted = model.lower()
    matches = [
        entry for entry in endpoints
        if _entry_field(entry, _MODEL_FIELDS).lower() == wanted
    ]
    if not matches:
        raise ValueError(
            f"No Modal endpoint{scope} serves model '{model}'. Endpoints found:\n"
            f"{_describe_endpoints(endpoints)}\n"
            f"Create one with 'modal endpoint create --model {model}', or pass "
            "base_url= explicitly."
        )
    if len(matches) > 1:
        raise ValueError(
            f"{len(matches)} Modal endpoints{scope} serve model '{model}':\n"
            f"{_describe_endpoints(matches)}\n"
            "Pass base_url= to pick one explicitly."
        )

    url = _entry_field(matches[0], _URL_FIELDS)
    if not url:
        raise ValueError(
            f"The Modal endpoint serving '{model}' exposes no URL field in "
            f"'modal endpoint list --json' (keys seen: "
            f"{sorted(matches[0].keys())}). Pass base_url= (the endpoint URL "
            "from the Modal dashboard) explicitly."
        )
    return url


class ModalEndpointProvider(OpenAICompatibleProvider):
    """Provider for Modal's managed inference Endpoints.

    Thin composition over
    :class:`~chimera.providers.compatible.OpenAICompatibleProvider`: the
    wire protocol is stock OpenAI Chat Completions under ``/v1``; what
    differs is auth (``Modal-Key`` / ``Modal-Secret`` proxy-token headers
    instead of ``Authorization: Bearer``), base-URL normalization (the
    dashboard URL works with or without ``/v1``), and optional endpoint
    discovery through the local ``modal`` CLI when no ``base_url`` is
    given.

    See the module docstring for the three API tiers and the
    ``modal-endpoint/<hf-repo-id>`` model-string convention.
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        token_id: str | None = None,
        token_secret: str | None = None,
        api_key: str | None = None,
        unauthenticated: bool = False,
        modal_environment: str | None = None,
        context_length: int = 128_000,
        extra_headers: dict[str, str] | None = None,
        flags: CompatFlags | None = None,
    ) -> None:
        """Initialise the provider (fails fast on missing auth or endpoint).

        Args:
            model: Base model repo id (e.g. ``"zai-org/GLM-5.2-FP8"``) — the
                value the endpoint expects as the request ``model``. A
                leading ``modal-endpoint/`` prefix is stripped, so the CLI
                model-string form works here too.
            base_url: Endpoint URL, with or without the ``/v1`` suffix.
                ``None`` triggers discovery via ``modal endpoint list
                --json`` (see :meth:`_discover_base_url`).
            token_id: Proxy-token id (the ``Modal-Key`` header). Falls back
                to ``$MODAL_PROXY_TOKEN_ID``.
            token_secret: Proxy-token secret (the ``Modal-Secret`` header).
                Falls back to ``$MODAL_PROXY_TOKEN_SECRET``.
            api_key: Optional bearer token. Modal endpoints do not use
                ``Authorization``; only set this when something in front of
                the endpoint (a gateway, a relay) wants it. When ``None``
                (the default) **no** ``Authorization`` header is sent — and
                ``$OPENAI_API_KEY`` is deliberately *not* read.
            unauthenticated: ``True`` for endpoints created with
                ``--unauthenticated``: skips proxy-token resolution and
                sends no ``Modal-Key`` / ``Modal-Secret`` headers.
            modal_environment: Modal environment for discovery
                (``--env``). Falls back to ``$MODAL_ENVIRONMENT``.
            context_length: Advertised context window (informational).
            extra_headers: Additional headers merged last (they win on key
                collision), mirroring the OpenAI-compatible provider.
            flags: Backend quirk parameterization; ``None`` auto-detects
                from the model id.

        Raises:
            ValueError: Missing/incomplete proxy tokens, an empty model, or
                any discovery failure (no CLI, no endpoints, no match) —
                each with actionable instructions. Never a silent fallback.
            RuntimeError: The ``modal`` CLI ran but failed during discovery.
            ImportError: ``httpx`` (the transport of the underlying
                OpenAI-compatible provider) is not installed.
        """
        bare_model = model.strip()
        if bare_model.lower().startswith(MODAL_ENDPOINT_PREFIX):
            bare_model = bare_model[len(MODAL_ENDPOINT_PREFIX):]
        if not bare_model:
            raise ValueError(
                "model is required — the base model repo id the endpoint "
                "serves (e.g. 'zai-org/GLM-5.2-FP8')."
            )

        self._modal_environment = (
            modal_environment or os.environ.get(MODAL_ENVIRONMENT_ENV) or None
        )
        self._unauthenticated = unauthenticated

        headers: dict[str, str] = {}
        if not unauthenticated:
            resolved_id, resolved_secret = _resolve_proxy_tokens(token_id, token_secret)
            headers["Modal-Key"] = resolved_id
            headers["Modal-Secret"] = resolved_secret

        if base_url is None:
            base_url = self._discover_base_url(bare_model)
        resolved_base_url = normalize_endpoint_base_url(base_url)

        # The parent falls back to $OPENAI_API_KEY when api_key is falsy —
        # never appropriate here (it would leak an unrelated key to the
        # endpoint). Feed a placeholder, then drop the Authorization header
        # entirely unless the caller explicitly opted in with api_key=.
        super().__init__(
            model=bare_model,
            base_url=resolved_base_url,
            api_key=api_key or "unused",
            headers=headers,
            context_length=context_length,
            extra_headers=extra_headers,
            flags=flags,
        )
        if not api_key:
            self._headers.pop("Authorization", None)

    def _discover_base_url(self, model: str) -> str:
        """Resolve the endpoint URL for *model* (tier-3 subclass hook).

        The default implementation shells out to ``modal endpoint list
        --json`` via :func:`discover_endpoint_base_url`, scoped to the
        provider's Modal environment. Subclasses may override to consult a
        pinned fleet map, a service registry, etc.

        Args:
            model: Bare model repo id (prefix already stripped).

        Returns:
            The endpoint URL (``/v1`` optional; it is normalized later).
        """
        return discover_endpoint_base_url(model, env=self._modal_environment)


from chimera.providers.registry import register_provider as _register  # noqa: E402

_register(
    "modal-endpoint",
    lambda model="", base_url=None, api_key=None, **kw: ModalEndpointProvider(
        model=model, base_url=base_url, api_key=api_key, **kw,
    ),
)


__all__ = [
    "MODAL_ENDPOINT_PREFIX",
    "MODAL_ENVIRONMENT_ENV",
    "MODAL_PROXY_TOKEN_ID_ENV",
    "MODAL_PROXY_TOKEN_SECRET_ENV",
    "ModalEndpointProvider",
    "discover_endpoint_base_url",
    "list_modal_endpoints",
    "normalize_endpoint_base_url",
]
