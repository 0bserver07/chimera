# tests/function_synthesis/test_errors.py
from __future__ import annotations

import pytest

from chimera.function_synthesis.errors import CacheMissError, OfflineError


def test_offline_error_is_runtime_error():
    assert issubclass(OfflineError, RuntimeError)


def test_cache_miss_carries_key():
    err = CacheMissError(kind="model", key="repo/file.gguf")
    assert err.kind == "model"
    assert err.key == "repo/file.gguf"
    assert "repo/file.gguf" in str(err)


def test_offline_error_carries_operation():
    err = OfflineError(operation="download base model 'foo'")
    assert "foo" in str(err)
    assert "offline" in str(err).lower()
