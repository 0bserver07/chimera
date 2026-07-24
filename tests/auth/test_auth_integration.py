# tests/test_auth_integration.py
"""Tests for auth integration into the provider system."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.factory import create_provider


@pytest.fixture(autouse=True)
def _prime_builtin_registry() -> None:
    """Register built-in providers BEFORE any test here patches ``_registry``.

    ``create_provider`` lazily calls ``_ensure_builtins_registered()``, which
    flips a module-level guard *and then* imports every provider module so each
    self-registers. If that first call happens **inside** a
    ``patch("...registry._registry", {...})`` window, those registrations land
    in the temporary dict and are thrown away when the patch is restored —
    while the guard stays flipped, so the imports are never re-run and the real
    registry is left permanently missing its built-ins. That poisons
    ``list_providers()`` for every later test in the session.

    Priming the registry here forces the lazy import to happen against the real
    dict, so the patches below are pure substitutions with no global fallout.
    """
    from chimera.providers.registry import _ensure_builtins_registered

    _ensure_builtins_registered()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_auth_manager(token: str = "auth-tok-123") -> MagicMock:
    """Return a mock AuthManager whose get_token returns *token*."""
    mgr = MagicMock()
    mgr.get_token.return_value = token
    return mgr


def _failing_auth_manager() -> MagicMock:
    """Return a mock AuthManager whose get_token always raises."""
    mgr = MagicMock()
    mgr.get_token.side_effect = ValueError("no credential")
    return mgr


# ---------------------------------------------------------------------------
# create_provider — backward compatibility
# ---------------------------------------------------------------------------


class TestCreateProviderBackwardCompat:
    def test_works_without_auth_manager(self) -> None:
        """create_provider still works when auth_manager is not passed."""
        with patch("chimera.providers.anthropic.AnthropicProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            # Patch the registry factory to use the mocked class
            with patch(
                "chimera.providers.registry._registry",
                {"anthropic": lambda model="", api_key=None, base_url=None, **kw: mock_cls(
                    model=model, api_key=api_key, base_url=base_url, **kw,
                )},
            ):
                provider = create_provider(
                    provider_type="anthropic",
                    model="claude-sonnet-4-20250514",
                    api_key="sk-explicit",
                )
                assert provider is mock_cls.return_value
                mock_cls.assert_called_once_with(
                    model="claude-sonnet-4-20250514",
                    api_key="sk-explicit",
                    base_url=None,
                )


# ---------------------------------------------------------------------------
# create_provider — auth_manager token resolution
# ---------------------------------------------------------------------------


class TestCreateProviderAuthManager:
    def test_uses_auth_manager_when_no_api_key(self) -> None:
        """auth_manager.get_token is used when api_key is None."""
        mgr = _mock_auth_manager("auth-key-abc")
        with patch(
            "chimera.providers.registry._registry",
            {"anthropic": lambda model="", api_key=None, base_url=None, **kw: MagicMock(
                _resolved_key=api_key,
            )},
        ):
            provider = create_provider(
                provider_type="anthropic",
                model="claude-sonnet-4-20250514",
                auth_manager=mgr,
            )
            mgr.get_token.assert_called_once_with("anthropic")
            assert provider._resolved_key == "auth-key-abc"

    def test_explicit_api_key_beats_auth_manager(self) -> None:
        """Explicit api_key takes priority over auth_manager."""
        mgr = _mock_auth_manager("should-not-use")
        with patch(
            "chimera.providers.registry._registry",
            {"openai": lambda model="", api_key=None, base_url=None, **kw: MagicMock(
                _resolved_key=api_key,
            )},
        ):
            provider = create_provider(
                provider_type="openai",
                model="gpt-4o",
                api_key="sk-explicit",
                auth_manager=mgr,
            )
            mgr.get_token.assert_not_called()
            assert provider._resolved_key == "sk-explicit"

    def test_auth_failure_falls_through_silently(self) -> None:
        """When auth_manager.get_token raises, api_key stays None (env fallback)."""
        mgr = _failing_auth_manager()
        with patch(
            "chimera.providers.registry._registry",
            {"anthropic": lambda model="", api_key=None, base_url=None, **kw: MagicMock(
                _resolved_key=api_key,
            )},
        ):
            provider = create_provider(
                provider_type="anthropic",
                model="claude-sonnet-4-20250514",
                auth_manager=mgr,
            )
            mgr.get_token.assert_called_once_with("anthropic")
            # api_key should remain None — provider falls back to env vars
            assert provider._resolved_key is None

    def test_maps_provider_type_to_auth_name(self) -> None:
        """Different provider types map to correct auth provider names."""
        mgr = _mock_auth_manager("tok")
        with patch(
            "chimera.providers.registry._registry",
            {"google": lambda model="", api_key=None, base_url=None, **kw: MagicMock(
                _resolved_key=api_key,
            )},
        ):
            create_provider(
                provider_type="google",
                model="gemini-2.0-flash",
                auth_manager=mgr,
            )
            mgr.get_token.assert_called_once_with("google")


# ---------------------------------------------------------------------------
# Provider constructors — auth_manager param
# ---------------------------------------------------------------------------


class TestAnthropicProviderAuthManager:
    @patch("chimera.providers.anthropic.anthropic")
    def test_accepts_auth_manager(self, mock_anthropic_lib: MagicMock) -> None:
        from chimera.providers.anthropic import AnthropicProvider

        mgr = _mock_auth_manager("anth-key")
        mock_anthropic_lib.Anthropic.return_value = MagicMock()

        AnthropicProvider(model="claude-sonnet-4", auth_manager=mgr)
        mgr.get_token.assert_called_once_with("anthropic")
        # The key passed to Anthropic() should be the auth_manager token
        call_kwargs = mock_anthropic_lib.Anthropic.call_args
        assert call_kwargs[1]["api_key"] == "anth-key"

    @patch("chimera.providers.anthropic.anthropic")
    def test_explicit_key_over_auth_manager(self, mock_anthropic_lib: MagicMock) -> None:
        from chimera.providers.anthropic import AnthropicProvider

        mgr = _mock_auth_manager("should-not-use")
        mock_anthropic_lib.Anthropic.return_value = MagicMock()

        AnthropicProvider(model="claude-sonnet-4", api_key="sk-direct", auth_manager=mgr)
        mgr.get_token.assert_not_called()
        call_kwargs = mock_anthropic_lib.Anthropic.call_args
        assert call_kwargs[1]["api_key"] == "sk-direct"

    @patch("chimera.providers.anthropic.anthropic")
    def test_auth_failure_falls_to_env(
        self,
        mock_anthropic_lib: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from chimera.providers.anthropic import AnthropicProvider

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-fallback")
        mgr = _failing_auth_manager()
        mock_anthropic_lib.Anthropic.return_value = MagicMock()

        AnthropicProvider(model="claude-sonnet-4", auth_manager=mgr)
        call_kwargs = mock_anthropic_lib.Anthropic.call_args
        assert call_kwargs[1]["api_key"] == "sk-env-fallback"


class TestOpenAIProviderAuthManager:
    @patch("chimera.providers.openai.openai")
    def test_accepts_auth_manager(self, mock_openai_lib: MagicMock) -> None:
        from chimera.providers.openai import OpenAIProvider

        mgr = _mock_auth_manager("oai-key")
        mock_openai_lib.OpenAI.return_value = MagicMock()

        OpenAIProvider(model="gpt-4o", auth_manager=mgr)
        mgr.get_token.assert_called_once_with("openai")
        call_kwargs = mock_openai_lib.OpenAI.call_args
        assert call_kwargs[1]["api_key"] == "oai-key"

    @patch("chimera.providers.openai.openai")
    def test_explicit_key_over_auth_manager(self, mock_openai_lib: MagicMock) -> None:
        from chimera.providers.openai import OpenAIProvider

        mgr = _mock_auth_manager("should-not-use")
        mock_openai_lib.OpenAI.return_value = MagicMock()

        OpenAIProvider(model="gpt-4o", api_key="sk-direct", auth_manager=mgr)
        mgr.get_token.assert_not_called()
        call_kwargs = mock_openai_lib.OpenAI.call_args
        assert call_kwargs[1]["api_key"] == "sk-direct"

    @patch("chimera.providers.openai.openai")
    def test_auth_failure_falls_to_env(
        self,
        mock_openai_lib: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from chimera.providers.openai import OpenAIProvider

        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback")
        mgr = _failing_auth_manager()
        mock_openai_lib.OpenAI.return_value = MagicMock()

        OpenAIProvider(model="gpt-4o", auth_manager=mgr)
        call_kwargs = mock_openai_lib.OpenAI.call_args
        assert call_kwargs[1]["api_key"] == "sk-env-fallback"


class TestGoogleProviderAuthManager:
    @patch("chimera.providers.google.genai")
    def test_accepts_auth_manager(self, mock_genai: MagicMock) -> None:
        from chimera.providers.google import GoogleProvider

        mgr = _mock_auth_manager("goog-key")

        GoogleProvider(model="gemini-2.0-flash", auth_manager=mgr)
        mgr.get_token.assert_called_once_with("google")
        mock_genai.configure.assert_called_once_with(api_key="goog-key")

    @patch("chimera.providers.google.genai")
    def test_explicit_key_over_auth_manager(self, mock_genai: MagicMock) -> None:
        from chimera.providers.google import GoogleProvider

        mgr = _mock_auth_manager("should-not-use")

        GoogleProvider(model="gemini-2.0-flash", api_key="goog-direct", auth_manager=mgr)
        mgr.get_token.assert_not_called()
        mock_genai.configure.assert_called_once_with(api_key="goog-direct")

    @patch("chimera.providers.google.genai")
    def test_auth_failure_falls_to_env(
        self,
        mock_genai: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from chimera.providers.google import GoogleProvider

        monkeypatch.setenv("GOOGLE_API_KEY", "goog-env")
        mgr = _failing_auth_manager()

        GoogleProvider(model="gemini-2.0-flash", auth_manager=mgr)
        mock_genai.configure.assert_called_once_with(api_key="goog-env")
