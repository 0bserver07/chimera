"""Tests for :mod:`chimera.function_synthesis.facade`.

All tests use mocks/monkeypatches — no real models are downloaded and no
real training is executed.  The goal is to lock in the two-line
``compile()``/``load()`` flow and the backend auto-detection behaviour.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from chimera.function_synthesis import facade
from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_GGUF,
    ADAPTER_FORMAT_PEFT,
    ChiBundle,
)
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _spec(name: str = "echo") -> FunctionSpec:
    return FunctionSpec(
        name=name,
        description=f"{name} description",
        examples=[{"input": "hi", "output": "hello"}],
    )


def _peft_bundle(spec: FunctionSpec | None = None) -> ChiBundle:
    return ChiBundle(
        spec=spec or _spec(),
        prompts={"system": "sys", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "test"},
        base_model="base-hf",
        adapter_format=ADAPTER_FORMAT_PEFT,
        adapter_peft_files={
            "adapter_config.json": b'{"peft_type": "LORA"}',
            "adapter_model.safetensors": b"\x00\x01WEIGHTS",
        },
    )


def _gguf_bundle(spec: FunctionSpec | None = None) -> ChiBundle:
    return ChiBundle(
        spec=spec or _spec("gguf-fn"),
        adapter_bytes=b"GGUF_ADAPTER_BYTES",
        prompts={"system": "sys", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "test"},
        base_model="some/model.gguf",
        adapter_format=ADAPTER_FORMAT_GGUF,
    )


class _RecordingCompiler(CompilerBackend):
    """Compiler stub that records calls and emits a fixed bundle."""

    def __init__(self, bundle: ChiBundle) -> None:
        self._bundle = bundle
        self.calls: list[FunctionSpec] = []

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        self.calls.append(spec)
        # Re-emit the bundle with the caller's spec so the registry slug
        # matches the caller's expectations.
        return ChiBundle(
            spec=spec,
            prompts=self._bundle.prompts,
            metadata=self._bundle.metadata,
            base_model=self._bundle.base_model,
            adapter_format=self._bundle.adapter_format,
            adapter_bytes=self._bundle.adapter_bytes,
            adapter_peft_files=dict(self._bundle.adapter_peft_files),
        )


class _RecordingRuntime(RuntimeBackend):
    """Runtime stub that records load() and returns a canned string."""

    def __init__(self, name: str = "stub") -> None:
        self.name = name
        self.loaded: ChiBundle | None = None
        self.invocations: list[str] = []

    def load(self, bundle: ChiBundle) -> None:
        self.loaded = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        self.invocations.append(user_input)
        return f"{self.name}:{user_input}"

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# compile()
# ---------------------------------------------------------------------------


def test_compile_defaults_to_local_compiler(tmp_path, monkeypatch):
    """compile(spec) with no compiler constructs LocalCompiler(default model)."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    monkeypatch.delenv("CHIMERA_FS_DEFAULT_COMPILE_MODEL", raising=False)

    seen: dict[str, Any] = {}
    recording = _RecordingCompiler(_peft_bundle())

    class _StubLocalCompiler:
        def __init__(self, base_model_name_or_path: str, **kwargs: Any) -> None:
            seen["base_model"] = base_model_name_or_path
            seen["kwargs"] = kwargs

        def compile(self, spec: FunctionSpec) -> ChiBundle:
            return recording.compile(spec)

    fake_local_mod = types.ModuleType(
        "chimera.function_synthesis.compilers.local"
    )
    fake_local_mod.LocalCompiler = _StubLocalCompiler
    monkeypatch.setitem(
        sys.modules, "chimera.function_synthesis.compilers.local", fake_local_mod
    )

    spec = _spec("local-default")
    slug = facade.compile(spec)

    assert seen["base_model"] == "Qwen/Qwen2-0.5B"
    assert len(recording.calls) == 1
    assert recording.calls[0].name == "local-default"
    assert slug.startswith("local-default-")
    # installed() sees it.
    assert slug in facade.installed()


def test_compile_respects_default_model_env(tmp_path, monkeypatch):
    """CHIMERA_FS_DEFAULT_COMPILE_MODEL overrides the hardcoded default."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    monkeypatch.setenv(
        "CHIMERA_FS_DEFAULT_COMPILE_MODEL", "tiny/override-model"
    )

    seen: dict[str, Any] = {}

    class _StubLocalCompiler:
        def __init__(self, base_model_name_or_path: str, **kwargs: Any) -> None:
            seen["base_model"] = base_model_name_or_path

        def compile(self, spec: FunctionSpec) -> ChiBundle:
            return _peft_bundle(spec)

    fake_local_mod = types.ModuleType(
        "chimera.function_synthesis.compilers.local"
    )
    fake_local_mod.LocalCompiler = _StubLocalCompiler
    monkeypatch.setitem(
        sys.modules, "chimera.function_synthesis.compilers.local", fake_local_mod
    )

    facade.compile(_spec("env-override"))
    assert seen["base_model"] == "tiny/override-model"


def test_compile_accepts_backend_instance(tmp_path, monkeypatch):
    """Passing a CompilerBackend instance bypasses the default construction."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    compiler = MockCompiler()
    spec = _spec("instance")
    slug = facade.compile(spec, compiler=compiler)
    assert slug in facade.installed()
    # The resolved bundle on disk should have been produced by MockCompiler.
    registry = ProgramRegistry.default()
    entry = registry.resolve(slug)
    loaded = ChiBundle.load(entry.bundle_path)
    assert loaded.metadata["compiler_backend"] == "mock"


def test_compile_accepts_string_alias(tmp_path, monkeypatch):
    """compiler='mock' resolves to MockCompiler without touching LocalCompiler."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))

    class _ExplodingLocal:
        def __init__(self, *a: Any, **kw: Any) -> None:
            raise AssertionError("LocalCompiler should not be used for alias='mock'")

    fake_local_mod = types.ModuleType(
        "chimera.function_synthesis.compilers.local"
    )
    fake_local_mod.LocalCompiler = _ExplodingLocal
    monkeypatch.setitem(
        sys.modules, "chimera.function_synthesis.compilers.local", fake_local_mod
    )

    slug = facade.compile(_spec("alias-mock"), compiler="mock")
    entry = ProgramRegistry.default().resolve(slug)
    loaded = ChiBundle.load(entry.bundle_path)
    assert loaded.metadata["compiler_backend"] == "mock"


def test_compile_rejects_unknown_string_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="unknown compiler alias"):
        facade.compile(_spec(), compiler="bogus")


def test_compile_rejects_bad_type(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    with pytest.raises(TypeError, match="CompilerBackend"):
        facade.compile(_spec(), compiler=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# load() auto-detection
# ---------------------------------------------------------------------------


def _install_peft_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    spec = _spec("peft-fn")
    bundle = _peft_bundle(spec)
    return ProgramRegistry.default().install(spec=spec, bundle=bundle)


def _install_gguf_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    spec = _spec("gguf-fn")
    bundle = _gguf_bundle(spec)
    return ProgramRegistry.default().install(spec=spec, bundle=bundle)


def test_load_auto_detects_transformers_for_peft_bundle(tmp_path, monkeypatch):
    """peft bundles load via TransformersBackend (mocked here)."""
    slug = _install_peft_bundle(tmp_path, monkeypatch)

    constructed: list[str] = []

    class _StubTransformersBackend(_RecordingRuntime):
        def __init__(self, base_model_name_or_path: str, **kwargs: Any) -> None:
            super().__init__("tf")
            constructed.append(base_model_name_or_path)

    fake_mod = types.ModuleType(
        "chimera.function_synthesis.backends.transformers"
    )
    fake_mod.TransformersBackend = _StubTransformersBackend
    monkeypatch.setitem(
        sys.modules,
        "chimera.function_synthesis.backends.transformers",
        fake_mod,
    )

    fn = facade.load(slug)
    assert isinstance(fn, CompiledFunction)
    assert constructed == ["base-hf"]
    assert fn("hi") == "tf:hi"


def test_load_auto_detects_llama_cpp_for_gguf_bundle(tmp_path, monkeypatch):
    """gguf-lora bundles load via LlamaCppBackend (mocked here)."""
    slug = _install_gguf_bundle(tmp_path, monkeypatch)

    constructed: list[str] = []

    class _StubLlamaCppBackend(_RecordingRuntime):
        def __init__(self, *, base_model_path: Any, **kwargs: Any) -> None:
            super().__init__("gguf")
            constructed.append(str(base_model_path))

    fake_mod = types.ModuleType(
        "chimera.function_synthesis.backends.llama_cpp"
    )
    fake_mod.LlamaCppBackend = _StubLlamaCppBackend
    monkeypatch.setitem(
        sys.modules,
        "chimera.function_synthesis.backends.llama_cpp",
        fake_mod,
    )

    fn = facade.load(slug)
    assert isinstance(fn, CompiledFunction)
    assert constructed == ["some/model.gguf"]
    assert fn("ping") == "gguf:ping"


def test_load_missing_optional_dep_raises_friendly(tmp_path, monkeypatch):
    """A missing transformers backend surfaces an install hint."""
    slug = _install_peft_bundle(tmp_path, monkeypatch)

    # Poison the import path: make importing the backend module raise
    # ``ImportError`` the same way a missing optional dep would.
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "chimera.function_synthesis.backends.transformers":
            raise ImportError("No module named 'transformers'")
        return original_import(name, *args, **kwargs)

    # Ensure the target module is not already cached.
    monkeypatch.delitem(
        sys.modules,
        "chimera.function_synthesis.backends.transformers",
        raising=False,
    )
    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(ImportError) as excinfo:
        facade.load(slug)
    msg = str(excinfo.value)
    assert "function_synthesis_transformers" in msg
    assert "pip install" in msg


def test_load_accepts_path_directly(tmp_path, monkeypatch):
    """Passing a .chi path bypasses the registry and loads the file."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    bundle = _peft_bundle()
    chi_path = tmp_path / "direct.chi"
    bundle.save(chi_path)

    class _StubTransformersBackend(_RecordingRuntime):
        def __init__(self, base_model_name_or_path: str, **kwargs: Any) -> None:
            super().__init__("tf")

    fake_mod = types.ModuleType(
        "chimera.function_synthesis.backends.transformers"
    )
    fake_mod.TransformersBackend = _StubTransformersBackend
    monkeypatch.setitem(
        sys.modules,
        "chimera.function_synthesis.backends.transformers",
        fake_mod,
    )

    fn = facade.load(chi_path)
    assert isinstance(fn, CompiledFunction)
    assert fn("x") == "tf:x"


def test_load_path_missing_raises_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    missing = tmp_path / "nope.chi"
    with pytest.raises(FileNotFoundError):
        facade.load(missing)


def test_load_accepts_runtime_instance(tmp_path, monkeypatch):
    """Passing a RuntimeBackend instance bypasses auto-detection entirely."""
    slug = _install_peft_bundle(tmp_path, monkeypatch)
    runtime = _RecordingRuntime("custom")
    fn = facade.load(slug, backend=runtime)
    assert runtime.loaded is not None
    assert fn("yo") == "custom:yo"


def test_load_rejects_unknown_backend_alias(tmp_path, monkeypatch):
    slug = _install_peft_bundle(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unknown backend alias"):
        facade.load(slug, backend="bogus")


def test_load_rejects_bad_backend_type(tmp_path, monkeypatch):
    slug = _install_peft_bundle(tmp_path, monkeypatch)
    with pytest.raises(TypeError, match="RuntimeBackend"):
        facade.load(slug, backend=42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# installed() / uninstall()
# ---------------------------------------------------------------------------


def test_installed_returns_registry_slugs(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    compiler = MockCompiler()
    slugs = [
        facade.compile(_spec(f"fn-{i}"), compiler=compiler) for i in range(3)
    ]
    assert sorted(facade.installed()) == sorted(slugs)


def test_uninstall_removes_from_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    slug = facade.compile(_spec("removable"), compiler=MockCompiler())
    assert slug in facade.installed()
    facade.uninstall(slug)
    assert facade.installed() == []


def test_two_line_compile_and_invoke(tmp_path, monkeypatch):
    """The headline UX: fs.compile(spec) + fs.load(slug)(...) returns a string."""
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    runtime = _RecordingRuntime("two-line")

    slug = facade.compile(_spec("headline"), compiler=MockCompiler())
    fn = facade.load(slug, backend=runtime)
    assert fn("hello") == "two-line:hello"
