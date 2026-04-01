"""Tests for chimera.core.cache_safe_params — Phase 5."""
from __future__ import annotations

from chimera.core.cache_safe_params import CacheSafeParams, CacheSafeParamsStore
from chimera.core.system_prompt import SystemPromptBuilder


def _make_prompt(base_text: str = "Base.", cacheable: bool = True):
    return SystemPromptBuilder().add_layer("base", base_text, cacheable=cacheable).build()


class TestCacheSafeParams:
    """CacheSafeParams save/get and matching."""

    def test_save_and_get(self):
        # Reset singleton state
        CacheSafeParamsStore._current = None
        prompt = _make_prompt()
        params = CacheSafeParams(
            system_prompt=prompt,
            tools=[{"name": "read"}],
            messages=[],
            model="claude-3",
        )
        CacheSafeParamsStore.save(params)
        retrieved = CacheSafeParamsStore.get()
        assert retrieved is params

    def test_matches(self):
        prompt_a = _make_prompt("Same base.")
        prompt_b = _make_prompt("Same base.")
        prompt_c = _make_prompt("Different base.")

        params_a = CacheSafeParams(
            system_prompt=prompt_a,
            tools=[{"name": "read"}],
            messages=[],
            model="claude-3",
        )
        params_b = CacheSafeParams(
            system_prompt=prompt_b,
            tools=[{"name": "read"}],
            messages=[],
            model="claude-3",
        )
        params_c = CacheSafeParams(
            system_prompt=prompt_c,
            tools=[{"name": "read"}],
            messages=[],
            model="claude-3",
        )
        params_d = CacheSafeParams(
            system_prompt=prompt_a,
            tools=[{"name": "write"}],
            messages=[],
            model="claude-3",
        )
        assert params_a.matches(params_b)
        assert not params_a.matches(params_c)
        assert not params_a.matches(params_d)
