# tests/function_synthesis/test_prefix_cache.py
from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.prefix_cache import PrefixCache


def test_prefix_cache_key_is_deterministic(tmp_path):
    cache = PrefixCache(root=tmp_path)
    k1 = cache.key(base_model_sha="a" * 64, slug="echo-abc12345", system_prompt="hi")
    k2 = cache.key(base_model_sha="a" * 64, slug="echo-abc12345", system_prompt="hi")
    assert k1 == k2


def test_prefix_cache_key_changes_on_prompt_change(tmp_path):
    cache = PrefixCache(root=tmp_path)
    k1 = cache.key(base_model_sha="a" * 64, slug="s", system_prompt="A")
    k2 = cache.key(base_model_sha="a" * 64, slug="s", system_prompt="B")
    assert k1 != k2


def test_prefix_cache_store_and_load(tmp_path):
    cache = PrefixCache(root=tmp_path)
    k = cache.key(base_model_sha="a" * 64, slug="s", system_prompt="x")
    cache.store(k, b"STATE_BYTES")
    assert cache.load(k) == b"STATE_BYTES"


def test_prefix_cache_load_missing_returns_none(tmp_path):
    cache = PrefixCache(root=tmp_path)
    k = cache.key(base_model_sha="a" * 64, slug="s", system_prompt="x")
    assert cache.load(k) is None


def test_prefix_cache_disabled_short_circuits(tmp_path):
    cache = PrefixCache(root=tmp_path, enabled=False)
    k = cache.key(base_model_sha="a" * 64, slug="s", system_prompt="x")
    cache.store(k, b"X")
    assert cache.load(k) is None
