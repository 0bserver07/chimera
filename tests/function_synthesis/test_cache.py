# tests/function_synthesis/test_cache.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.function_synthesis.cache import CacheDirs


def test_default_home_under_dot_chimera(monkeypatch):
    monkeypatch.delenv("CHIMERA_FS_HOME", raising=False)
    monkeypatch.setenv("HOME", "/tmp/fake-home")
    dirs = CacheDirs.default()
    assert dirs.root == Path("/tmp/fake-home/.chimera/function_synthesis")
    assert dirs.models == dirs.root / "models"
    assert dirs.bundles == dirs.root / "bundles"


def test_env_var_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    dirs = CacheDirs.default()
    assert dirs.root == tmp_path


def test_ensure_creates_subdirs(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    dirs = CacheDirs.default()
    dirs.ensure()
    assert dirs.models.is_dir()
    assert dirs.bundles.is_dir()
