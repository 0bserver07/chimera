# tests/function_synthesis/test_cache.py
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.cache import BaseModelCache, BundleCache, CacheDirs
from chimera.function_synthesis.errors import CacheMissError, OfflineError
from chimera.function_synthesis.spec import FunctionSpec


# --- T1: CacheDirs ---


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


# --- T3: BaseModelCache ---


def _install_fake_hub(monkeypatch, captured: dict):
    fake = types.ModuleType("huggingface_hub")

    def hf_hub_download(*, repo_id, filename, local_dir, **kwargs):
        captured["repo_id"] = repo_id
        captured["filename"] = filename
        captured["local_dir"] = local_dir
        target = local_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"FAKE_GGUF")
        return str(target)

    fake.hf_hub_download = hf_hub_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)


def test_cache_hit_returns_local_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    cache = BaseModelCache(CacheDirs.default())
    target = cache.dirs.models / "org--repo" / "model.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"X")
    path = cache.get("org/repo", "model.gguf")
    assert path == target


def test_cache_miss_triggers_download(tmp_path, monkeypatch):
    captured: dict = {}
    _install_fake_hub(monkeypatch, captured)
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    cache = BaseModelCache(CacheDirs.default())
    path = cache.get("org/repo", "model.gguf")
    assert path.read_bytes() == b"FAKE_GGUF"
    assert captured["repo_id"] == "org/repo"
    assert captured["filename"] == "model.gguf"


def test_offline_raises_on_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_FS_OFFLINE", "1")
    cache = BaseModelCache(CacheDirs.default())
    with pytest.raises((OfflineError, CacheMissError)):
        cache.get("org/repo", "model.gguf")


def test_missing_hub_extra_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    cache = BaseModelCache(CacheDirs.default())
    with pytest.raises(ImportError, match="huggingface_hub"):
        cache.get("org/repo", "model.gguf")


# --- T4: BundleCache ---


def _sample_bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"A",
        prompts={"system": "", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "test"},
    )


def test_bundle_cache_install_and_get(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    cache = BundleCache(CacheDirs.default())
    path = cache.install(slug="echo-abc12345", bundle=_sample_bundle())
    assert path.exists()
    assert path.suffix == ".chi"
    assert cache.get("echo-abc12345") == path


def test_bundle_cache_get_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    cache = BundleCache(CacheDirs.default())
    with pytest.raises(CacheMissError):
        cache.get("nope-00000000")


def test_bundle_cache_list_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    cache = BundleCache(CacheDirs.default())
    cache.install(slug="a-00000000", bundle=_sample_bundle())
    cache.install(slug="b-11111111", bundle=_sample_bundle())
    assert sorted(cache.list()) == ["a-00000000", "b-11111111"]
    cache.remove("a-00000000")
    assert cache.list() == ["b-11111111"]
