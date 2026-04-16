# tests/function_synthesis/test_registry.py
from __future__ import annotations

from chimera.function_synthesis.bundle import ChiBundle
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
