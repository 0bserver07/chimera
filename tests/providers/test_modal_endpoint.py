# tests/providers/test_modal_endpoint.py
"""Tests for the Modal managed-Endpoints provider.

Everything is offline: the HTTP transport is mocked at
``chimera.providers.compatible.httpx`` (the OpenAI-compatible provider's
module global, same pattern as test_provider_compatible.py) and endpoint
discovery is mocked at ``chimera.providers.modal_endpoint.subprocess.run``.
No network traffic, no ``modal`` CLI, no billable resources.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.modal_endpoint import (
    ModalEndpointProvider,
    discover_endpoint_base_url,
    list_modal_endpoints,
    normalize_endpoint_base_url,
)
from chimera.types import Message

# ---------------------------------------------------------------------------
# CRAFTED FIXTURE — the JSON schema of ``modal endpoint list --json`` is not
# publicly documented (the Modal docs show the command, not its output), so
# this is a plausible shape assembled from the documented concepts: an
# endpoint has a name, serves a base model (HF repo id), and exposes a URL.
# The production parser is alias-tolerant for exactly this reason; if the
# real schema turns out to differ, update this fixture alongside the alias
# tuples in chimera/providers/modal_endpoint.py.
# ---------------------------------------------------------------------------
_ENDPOINT_LIST_FIXTURE: list[dict[str, Any]] = [
    {
        "name": "glm-5-2-fp8",
        "model": "zai-org/GLM-5.2-FP8",
        "url": "https://myworkspace--glm-5-2-fp8.modal.run",
        "state": "running",
    },
    {
        "name": "qwen3-5-4b",
        "model": "Qwen/Qwen3.5-4B",
        "url": "https://myworkspace--qwen3-5-4b.modal.run",
        "state": "running",
    },
]


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    """Build a fake CompletedProcess for the mocked ``modal`` CLI."""
    return subprocess.CompletedProcess(
        args=["modal", "endpoint", "list", "--json"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var that influences this provider or the factory."""
    for var in (
        "MODAL_PROXY_TOKEN_ID",
        "MODAL_PROXY_TOKEN_SECRET",
        "MODAL_ENVIRONMENT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def _make_provider(**overrides: Any) -> ModalEndpointProvider:
    """Construct a provider with mocked transport and sane defaults."""
    kwargs: dict[str, Any] = {
        "model": "zai-org/GLM-5.2-FP8",
        "base_url": "https://myworkspace--glm-5-2-fp8.modal.run",
        "token_id": "wk-test-id",
        "token_secret": "ws-test-secret",
    }
    kwargs.update(overrides)
    with patch("chimera.providers.compatible.httpx"):
        return ModalEndpointProvider(**kwargs)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


def test_normalize_appends_v1_to_bare_url() -> None:
    assert (
        normalize_endpoint_base_url("https://ws--ep.modal.run")
        == "https://ws--ep.modal.run/v1"
    )


def test_normalize_strips_trailing_slash_then_appends_v1() -> None:
    assert (
        normalize_endpoint_base_url("https://ws--ep.modal.run/")
        == "https://ws--ep.modal.run/v1"
    )


def test_normalize_keeps_existing_v1() -> None:
    assert (
        normalize_endpoint_base_url("https://ws--ep.modal.run/v1")
        == "https://ws--ep.modal.run/v1"
    )


def test_normalize_trims_v1_trailing_slash() -> None:
    assert (
        normalize_endpoint_base_url("https://ws--ep.modal.run/v1/")
        == "https://ws--ep.modal.run/v1"
    )


def test_normalize_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="base_url is empty"):
        normalize_endpoint_base_url("   ")


# ---------------------------------------------------------------------------
# Constructor: headers and auth
# ---------------------------------------------------------------------------


def test_modal_key_secret_headers_from_args(clear_env: None) -> None:
    provider = _make_provider()
    assert provider._headers["Modal-Key"] == "wk-test-id"
    assert provider._headers["Modal-Secret"] == "ws-test-secret"


def test_modal_key_secret_headers_from_env(
    clear_env: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODAL_PROXY_TOKEN_ID", "wk-env-id")
    monkeypatch.setenv("MODAL_PROXY_TOKEN_SECRET", "ws-env-secret")
    provider = _make_provider(token_id=None, token_secret=None)
    assert provider._headers["Modal-Key"] == "wk-env-id"
    assert provider._headers["Modal-Secret"] == "ws-env-secret"


def test_no_authorization_header_by_default(clear_env: None) -> None:
    """Modal endpoints auth via headers, not bearer tokens — none is sent."""
    provider = _make_provider()
    assert "Authorization" not in provider._headers


def test_openai_api_key_env_never_leaks(
    clear_env: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent's $OPENAI_API_KEY fallback must not reach a Modal endpoint."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    provider = _make_provider()
    assert "Authorization" not in provider._headers
    assert "sk-should-not-leak" not in "".join(provider._headers.values())


def test_explicit_api_key_keeps_authorization_header(clear_env: None) -> None:
    """api_key= is an explicit opt-in for gateways fronting the endpoint."""
    provider = _make_provider(api_key="front-key")
    assert provider._headers["Authorization"] == "Bearer front-key"
    assert provider._headers["Modal-Key"] == "wk-test-id"


def test_extra_headers_merged(clear_env: None) -> None:
    provider = _make_provider(extra_headers={"X-Trace": "abc"})
    assert provider._headers["X-Trace"] == "abc"
    assert provider._headers["Modal-Key"] == "wk-test-id"


def test_extra_headers_win_on_collision(clear_env: None) -> None:
    provider = _make_provider(extra_headers={"Modal-Key": "wk-override"})
    assert provider._headers["Modal-Key"] == "wk-override"


def test_missing_both_tokens_raises_actionably(clear_env: None) -> None:
    with pytest.raises(ValueError) as exc:
        _make_provider(token_id=None, token_secret=None)
    msg = str(exc.value)
    assert "MODAL_PROXY_TOKEN_ID" in msg
    assert "MODAL_PROXY_TOKEN_SECRET" in msg
    assert "modal workspace proxy-tokens create" in msg
    assert "unauthenticated" in msg


def test_missing_secret_only_raises(clear_env: None) -> None:
    with pytest.raises(ValueError, match="MODAL_PROXY_TOKEN_SECRET"):
        _make_provider(token_secret=None)


def test_unauthenticated_skips_tokens_and_headers(clear_env: None) -> None:
    provider = _make_provider(
        token_id=None, token_secret=None, unauthenticated=True,
    )
    assert "Modal-Key" not in provider._headers
    assert "Modal-Secret" not in provider._headers
    assert "Authorization" not in provider._headers


# ---------------------------------------------------------------------------
# Constructor: model and base_url handling
# ---------------------------------------------------------------------------


def test_model_string_prefix_stripped(clear_env: None) -> None:
    provider = _make_provider(model="modal-endpoint/zai-org/GLM-5.2-FP8")
    assert provider.model_name == "zai-org/GLM-5.2-FP8"


def test_empty_model_raises(clear_env: None) -> None:
    with pytest.raises(ValueError, match="model is required"):
        _make_provider(model="")


def test_prefix_only_model_raises(clear_env: None) -> None:
    with pytest.raises(ValueError, match="model is required"):
        _make_provider(model="modal-endpoint/")


def test_base_url_normalized_in_ctor(clear_env: None) -> None:
    provider = _make_provider(base_url="https://ws--ep.modal.run/")
    assert provider._base_url == "https://ws--ep.modal.run/v1"


def test_base_url_with_v1_unchanged(clear_env: None) -> None:
    provider = _make_provider(base_url="https://ws--ep.modal.run/v1")
    assert provider._base_url == "https://ws--ep.modal.run/v1"


def test_context_window_default_and_override(clear_env: None) -> None:
    assert _make_provider().context_window == 128_000
    assert _make_provider(context_length=200_000).context_window == 200_000


# ---------------------------------------------------------------------------
# complete(): wire format
# ---------------------------------------------------------------------------


def test_complete_posts_to_v1_chat_completions_with_modal_headers(
    clear_env: None,
) -> None:
    with patch("chimera.providers.compatible.httpx") as mock_httpx:
        provider = ModalEndpointProvider(
            model="modal-endpoint/zai-org/GLM-5.2-FP8",
            base_url="https://myworkspace--glm-5-2-fp8.modal.run",
            token_id="wk-test-id",
            token_secret="ws-test-secret",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"role": "assistant", "content": "hello back"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        }
        mock_httpx.post.return_value = mock_response

        result = provider.complete([Message.user("hello")])

    assert result.content == "hello back"
    url = mock_httpx.post.call_args[0][0]
    assert url == "https://myworkspace--glm-5-2-fp8.modal.run/v1/chat/completions"
    headers = mock_httpx.post.call_args[1]["headers"]
    assert headers["Modal-Key"] == "wk-test-id"
    assert headers["Modal-Secret"] == "ws-test-secret"
    assert "Authorization" not in headers
    # The wire model id is the bare HF repo id, prefix stripped.
    assert mock_httpx.post.call_args[1]["json"]["model"] == "zai-org/GLM-5.2-FP8"


# ---------------------------------------------------------------------------
# Discovery: happy paths
# ---------------------------------------------------------------------------


def test_discover_resolves_url_from_fixture(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(_ENDPOINT_LIST_FIXTURE)),
    ) as run:
        url = discover_endpoint_base_url("zai-org/GLM-5.2-FP8")
    assert url == "https://myworkspace--glm-5-2-fp8.modal.run"
    assert run.call_args[0][0] == ["modal", "endpoint", "list", "--json"]


def test_discover_match_is_case_insensitive(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(_ENDPOINT_LIST_FIXTURE)),
    ):
        url = discover_endpoint_base_url("ZAI-ORG/glm-5.2-fp8")
    assert url == "https://myworkspace--glm-5-2-fp8.modal.run"


def test_discover_passes_env_flag(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(_ENDPOINT_LIST_FIXTURE)),
    ) as run:
        discover_endpoint_base_url("zai-org/GLM-5.2-FP8", env="prod")
    assert run.call_args[0][0] == [
        "modal", "endpoint", "list", "--json", "--env", "prod",
    ]


def test_discover_tolerates_field_aliases(clear_env: None) -> None:
    """base_model / endpoint_url aliases parse too (schema undocumented)."""
    aliased = [{
        "endpoint_name": "glm",
        "base_model": "zai-org/GLM-5.2-FP8",
        "endpoint_url": "https://ws--glm.modal.run",
    }]
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(aliased)),
    ):
        url = discover_endpoint_base_url("zai-org/GLM-5.2-FP8")
    assert url == "https://ws--glm.modal.run"


def test_list_unwraps_dict_and_skips_non_dict_entries(clear_env: None) -> None:
    wrapped = {"endpoints": [_ENDPOINT_LIST_FIXTURE[0], "stray-string"]}
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(wrapped)),
    ):
        entries = list_modal_endpoints()
    assert entries == [_ENDPOINT_LIST_FIXTURE[0]]


def test_ctor_discovers_when_no_base_url(clear_env: None) -> None:
    with (
        patch(
            "chimera.providers.modal_endpoint.subprocess.run",
            return_value=_proc(stdout=json.dumps(_ENDPOINT_LIST_FIXTURE)),
        ),
        patch("chimera.providers.compatible.httpx"),
    ):
        provider = ModalEndpointProvider(
            model="zai-org/GLM-5.2-FP8",
            token_id="wk-test-id",
            token_secret="ws-test-secret",
        )
    assert provider._base_url == "https://myworkspace--glm-5-2-fp8.modal.run/v1"


def test_ctor_discovery_honors_modal_environment_env_var(
    clear_env: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODAL_ENVIRONMENT", "staging")
    with (
        patch(
            "chimera.providers.modal_endpoint.subprocess.run",
            return_value=_proc(stdout=json.dumps(_ENDPOINT_LIST_FIXTURE)),
        ) as run,
        patch("chimera.providers.compatible.httpx"),
    ):
        ModalEndpointProvider(
            model="zai-org/GLM-5.2-FP8",
            token_id="wk-test-id",
            token_secret="ws-test-secret",
        )
    assert run.call_args[0][0][-2:] == ["--env", "staging"]


def test_subclass_can_override_discovery_hook(clear_env: None) -> None:
    """Tier-3: a subclass pins its own fleet; the modal CLI is never run."""

    class PinnedEndpoints(ModalEndpointProvider):
        def _discover_base_url(self, model: str) -> str:
            return "https://pinned--fleet.modal.run"

    with (
        patch("chimera.providers.modal_endpoint.subprocess.run") as run,
        patch("chimera.providers.compatible.httpx"),
    ):
        provider = PinnedEndpoints(
            model="zai-org/GLM-5.2-FP8",
            token_id="wk-test-id",
            token_secret="ws-test-secret",
        )
    assert provider._base_url == "https://pinned--fleet.modal.run/v1"
    run.assert_not_called()


# ---------------------------------------------------------------------------
# Discovery: every error path
# ---------------------------------------------------------------------------


def test_discover_cli_missing_raises_value_error(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        side_effect=FileNotFoundError("modal"),
    ):
        with pytest.raises(ValueError) as exc:
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")
    msg = str(exc.value)
    assert "pip install modal" in msg
    assert "base_url" in msg


def test_discover_old_cli_without_endpoint_subcommand(clear_env: None) -> None:
    """A modal client predating Endpoints says: No such command 'endpoint'."""
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(
            stderr="Error: No such command 'endpoint'.", returncode=2,
        ),
    ):
        with pytest.raises(ValueError, match="--upgrade modal"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_cli_failure_raises_runtime_error(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stderr="token expired", returncode=1),
    ):
        with pytest.raises(RuntimeError, match="token expired"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_cli_timeout_raises_runtime_error(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="modal", timeout=30),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_bad_json_raises_runtime_error(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout="✓ Listing endpoints…  not json"),
    ):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_unrecognized_dict_shape_raises(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps({"page": 1})),
    ):
        with pytest.raises(RuntimeError, match="unrecognized JSON shape"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_non_list_json_raises(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout="42"),
    ):
        with pytest.raises(RuntimeError, match="expected a JSON list"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_no_endpoints_suggests_create(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout="[]"),
    ):
        with pytest.raises(ValueError) as exc:
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")
    assert (
        "modal endpoint create --model zai-org/GLM-5.2-FP8" in str(exc.value)
    )


def test_discover_no_match_lists_available(clear_env: None) -> None:
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(_ENDPOINT_LIST_FIXTURE)),
    ):
        with pytest.raises(ValueError) as exc:
            discover_endpoint_base_url("meta-llama/Llama-4-8B")
    msg = str(exc.value)
    assert "zai-org/GLM-5.2-FP8" in msg
    assert "Qwen/Qwen3.5-4B" in msg


def test_discover_ambiguous_match_raises(clear_env: None) -> None:
    doubled = [
        _ENDPOINT_LIST_FIXTURE[0],
        {**_ENDPOINT_LIST_FIXTURE[0], "name": "glm-5-2-fp8-eu"},
    ]
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(doubled)),
    ):
        with pytest.raises(ValueError, match="Pass base_url="):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


def test_discover_match_without_url_field_raises(clear_env: None) -> None:
    no_url = [{"name": "glm", "model": "zai-org/GLM-5.2-FP8"}]
    with patch(
        "chimera.providers.modal_endpoint.subprocess.run",
        return_value=_proc(stdout=json.dumps(no_url)),
    ):
        with pytest.raises(ValueError, match="no URL field"):
            discover_endpoint_base_url("zai-org/GLM-5.2-FP8")


# ---------------------------------------------------------------------------
# Registry + factory integration
# ---------------------------------------------------------------------------


def test_registered_alongside_old_modal_provider() -> None:
    from chimera.providers.registry import (
        _ensure_builtins_registered,
        list_providers,
    )

    _ensure_builtins_registered()
    names = list_providers()
    assert "modal-endpoint" in names
    assert "modal" in names  # the self-deployed-vLLM path stays


def test_infer_provider_modal_endpoint_prefix(clear_env: None) -> None:
    from chimera.providers.factory import _infer_provider

    assert (
        _infer_provider("modal-endpoint/zai-org/GLM-5.2-FP8")
        == "modal-endpoint"
    )
    assert (
        _infer_provider("Modal-Endpoint/Qwen/Qwen3.5-4B") == "modal-endpoint"
    )


def test_infer_prefix_beats_anthropic_env_override(
    clear_env: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GLM ids normally route to anthropic; the explicit prefix must win."""
    from chimera.providers.factory import _infer_provider

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "zai-token")
    assert (
        _infer_provider("modal-endpoint/zai-org/GLM-5.2-FP8")
        == "modal-endpoint"
    )
    # And the plain GLM id keeps its existing anthropic routing.
    assert _infer_provider("glm-5.2") == "anthropic"


def test_create_provider_strips_prefix_and_normalizes(clear_env: None) -> None:
    from chimera.providers.factory import create_provider

    with patch("chimera.providers.compatible.httpx"):
        provider = create_provider(
            model="modal-endpoint/zai-org/GLM-5.2-FP8",
            base_url="https://myworkspace--glm-5-2-fp8.modal.run",
            token_id="wk-test-id",
            token_secret="ws-test-secret",
        )
    assert isinstance(provider, ModalEndpointProvider)
    assert provider.model_name == "zai-org/GLM-5.2-FP8"
    assert provider._base_url == "https://myworkspace--glm-5-2-fp8.modal.run/v1"


def test_create_provider_explicit_type(clear_env: None) -> None:
    from chimera.providers.factory import create_provider

    with patch("chimera.providers.compatible.httpx"):
        provider = create_provider(
            provider_type="modal-endpoint",
            model="zai-org/GLM-5.2-FP8",
            base_url="https://ws--ep.modal.run",
            token_id="wk-test-id",
            token_secret="ws-test-secret",
        )
    assert isinstance(provider, ModalEndpointProvider)
    assert provider._base_url == "https://ws--ep.modal.run/v1"
    assert "Authorization" not in provider._headers
