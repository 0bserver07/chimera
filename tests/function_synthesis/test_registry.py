# tests/function_synthesis/test_registry.py
from __future__ import annotations

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.errors import CacheMissError
from chimera.function_synthesis.registry import ProgramRegistry, slug_for
from chimera.function_synthesis.spec import FunctionSpec


def _spec(name: str = "echo") -> FunctionSpec:
    return FunctionSpec(name=name, description="echo")


def _bundle(spec: FunctionSpec) -> ChiBundle:
    return ChiBundle(
        spec=spec,
        adapter_bytes=b"A",
        prompts={"system": "", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "test"},
    )


def test_slug_is_deterministic():
    s1 = slug_for(_spec())
    s2 = slug_for(_spec())
    assert s1 == s2
    assert s1.startswith("echo-")
    assert len(s1.split("-")[-1]) == 8


def test_slug_changes_when_spec_changes():
    s1 = slug_for(FunctionSpec(name="echo", description="A"))
    s2 = slug_for(FunctionSpec(name="echo", description="B"))
    assert s1 != s2


def test_registry_install_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    spec = _spec()
    slug = registry.install(spec=spec, bundle=_bundle(spec))
    resolved = registry.resolve(slug)
    assert resolved.slug == slug
    assert resolved.bundle_path.exists()
    assert resolved.spec == spec


def test_registry_list_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    registry.install(spec=_spec("a"), bundle=_bundle(_spec("a")))
    registry.install(spec=_spec("b"), bundle=_bundle(_spec("b")))
    entries = registry.list()
    assert {e.spec.name for e in entries} == {"a", "b"}


def test_registry_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    spec = _spec()
    slug = registry.install(spec=spec, bundle=_bundle(spec))
    registry.remove(slug)
    assert registry.list() == []


# ---------------------------------------------------------------------------
# rename()
# ---------------------------------------------------------------------------


def test_registry_rename_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    spec = _spec("orig")
    slug = registry.install(spec=spec, bundle=_bundle(spec))

    registry.rename(slug, "new-name")

    # Old slug is gone; new slug resolves to the bundle on disk.
    with pytest.raises(CacheMissError):
        registry.resolve(slug)
    entry = registry.resolve("new-name")
    assert entry.slug == "new-name"
    assert entry.bundle_path.exists()
    assert entry.bundle_path.name == "new-name.chi"
    # Old file is no longer present.
    assert not (tmp_path / "bundles" / f"{slug}.chi").exists()


def test_registry_rename_missing_slug_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    with pytest.raises(CacheMissError):
        registry.rename("does-not-exist", "whatever")


def test_registry_rename_collision_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    slug_a = registry.install(spec=_spec("a"), bundle=_bundle(_spec("a")))
    slug_b = registry.install(spec=_spec("b"), bundle=_bundle(_spec("b")))

    with pytest.raises(ValueError):
        registry.rename(slug_a, slug_b)
    # Both slugs still resolve unchanged.
    assert registry.resolve(slug_a).slug == slug_a
    assert registry.resolve(slug_b).slug == slug_b


def test_registry_rename_same_slug_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    slug = registry.install(spec=_spec("c"), bundle=_bundle(_spec("c")))
    with pytest.raises(ValueError):
        registry.rename(slug, slug)


def test_registry_rename_empty_new_slug_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    slug = registry.install(spec=_spec("d"), bundle=_bundle(_spec("d")))
    with pytest.raises(ValueError):
        registry.rename(slug, "")


def test_registry_rename_preserves_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    registry = ProgramRegistry.default()
    spec = _spec("keep")
    slug = registry.install(spec=spec, bundle=_bundle(spec))
    registry.rename(slug, "renamed")
    entry = registry.resolve("renamed")
    assert entry.metadata.get("compiler_backend") == "test"
    assert entry.spec == spec
