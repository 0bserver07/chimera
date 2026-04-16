# Function Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `chimera/function_synthesis/` module that compiles natural-language specs into callable neural function artifacts (`.chi` bundles), runs them locally, and exposes them to agents as tools and to synthesis as a strategy.

**Architecture:** Spec → `CompilerBackend` (remote HTTP by default) → `.chi` bundle (ZIP containing GGUF LoRA adapter + manifest + prompt templates). A `CompiledFunction` runtime loads the bundle over a base GGUF model via `llama-cpp-python` and exposes it as a Python callable. Agents invoke compiled functions through `CompiledFunctionTool`; synthesis pipelines produce them via `FunctionSynthesisStrategy`. Compiler is pluggable — this plan ships `RemoteCompiler` only; `LocalLoRACompiler` is deferred to a follow-up plan.

**Tech Stack:** Python 3.11+, stdlib-only core, `llama-cpp-python` as an optional extra (`chimera[function_synthesis]`), `httpx` reused from the existing `remote` extra, hatchling build. Zero core dependency impact.

---

## File Structure

```
chimera/function_synthesis/
  __init__.py                  # Public re-exports
  spec.py                      # FunctionSpec (dataclass)
  bundle.py                    # ChiBundle (load/save/validate .chi ZIPs)
  runtime.py                   # CompiledFunction (backend-agnostic callable)
  compiler.py                  # CompilerBackend ABC
  backends/
    __init__.py
    llama_cpp.py               # LlamaCppBackend (optional dep)
  compilers/
    __init__.py
    remote.py                  # RemoteCompiler (HTTP client)
  strategies/
    __init__.py
    synthesis.py               # FunctionSynthesisStrategy

chimera/tools/compiled_function_tool.py   # Tool wrapper for agents

tests/function_synthesis/
  __init__.py
  test_spec.py
  test_bundle.py
  test_runtime.py
  test_compiler.py
  test_remote_compiler.py
  test_synthesis_strategy.py
tests/tools/test_compiled_function_tool.py
```

### Bundle format (`.chi`)

A `.chi` file is a ZIP archive with:

- `manifest.json` — `{"schema_version": 1, "name": str, "description": str, "base_model": str, "adapter_format": "gguf-lora", "created_at": str, "chimera_version": str}`
- `adapter.gguf` — LoRA adapter (Q4_0 by default) produced by compiler
- `prompts.json` — `{"system": str, "user_template": str, "stop": list[str]}`
- `spec.json` — serialized `FunctionSpec` used to compile
- `metadata.json` — free-form `{"compiler_backend": str, "base_model_sha256": str, ...}`

Runtime validates `schema_version == 1` and adapter presence.

---

## Task 1: FunctionSpec dataclass

**Files:**
- Create: `chimera/function_synthesis/__init__.py`
- Create: `chimera/function_synthesis/spec.py`
- Create: `tests/function_synthesis/__init__.py`
- Create: `tests/function_synthesis/test_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_spec.py
from __future__ import annotations

import json

from chimera.function_synthesis.spec import FunctionSpec


def test_spec_requires_name_and_description():
    spec = FunctionSpec(name="classify", description="Classify sentiment as pos/neg.")
    assert spec.name == "classify"
    assert spec.description == "Classify sentiment as pos/neg."
    assert spec.examples == []


def test_spec_with_examples_round_trips_json():
    spec = FunctionSpec(
        name="extract_email",
        description="Extract the first email from text.",
        examples=[{"input": "ping a@b.com", "output": "a@b.com"}],
    )
    blob = spec.to_json()
    restored = FunctionSpec.from_json(blob)
    assert restored == spec
    assert json.loads(blob)["name"] == "extract_email"


def test_spec_rejects_empty_name():
    import pytest

    with pytest.raises(ValueError, match="name must be non-empty"):
        FunctionSpec(name="", description="x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chimera.function_synthesis'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/__init__.py
"""Function synthesis: compile specs into callable neural artifacts."""
from __future__ import annotations

from chimera.function_synthesis.spec import FunctionSpec

__all__ = ["FunctionSpec"]
```

```python
# chimera/function_synthesis/spec.py
"""FunctionSpec: the 'what to compile' description for function synthesis."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class FunctionSpec:
    """Specification for a neural function to be synthesized.

    A FunctionSpec is consumed by a :class:`CompilerBackend` to produce a
    ``.chi`` bundle that can be loaded as a :class:`CompiledFunction`.

    Attributes:
        name: Short identifier (used in bundle filenames).
        description: Natural-language description of what the function does.
        examples: Optional input/output examples to ground compilation.
        input_schema: Optional JSON-schema-like dict describing input shape.
        output_schema: Optional JSON-schema-like dict describing output shape.
    """

    name: str
    description: str
    examples: list[dict[str, str]] = field(default_factory=list)
    input_schema: dict | None = None
    output_schema: dict | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self.description:
            raise ValueError("description must be non-empty")

    def to_json(self) -> str:
        """Serialize to a JSON string for inclusion in ``.chi`` bundles."""
        return json.dumps(
            {
                "name": self.name,
                "description": self.description,
                "examples": self.examples,
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, blob: str) -> FunctionSpec:
        """Deserialize from a JSON string produced by :meth:`to_json`."""
        data = json.loads(blob)
        return cls(
            name=data["name"],
            description=data["description"],
            examples=data.get("examples", []),
            input_schema=data.get("input_schema"),
            output_schema=data.get("output_schema"),
        )
```

```python
# tests/function_synthesis/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_spec.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/__init__.py chimera/function_synthesis/spec.py tests/function_synthesis/__init__.py tests/function_synthesis/test_spec.py
git commit -m "feat(function_synthesis): add FunctionSpec dataclass"
```

---

## Task 2: ChiBundle format (save/load/validate)

**Files:**
- Create: `chimera/function_synthesis/bundle.py`
- Create: `tests/function_synthesis/test_bundle.py`
- Modify: `chimera/function_synthesis/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_bundle.py
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.spec import FunctionSpec


def _make_bundle(tmp_path: Path) -> Path:
    spec = FunctionSpec(name="classify", description="classify sentiment")
    bundle = ChiBundle(
        spec=spec,
        adapter_bytes=b"FAKE_GGUF_BYTES",
        prompts={"system": "You classify.", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "test", "base_model_sha256": "deadbeef"},
        base_model="qwen3-4b-instruct-q4_0",
    )
    dst = tmp_path / "classify.chi"
    bundle.save(dst)
    return dst


def test_bundle_round_trip(tmp_path):
    path = _make_bundle(tmp_path)
    loaded = ChiBundle.load(path)
    assert loaded.spec.name == "classify"
    assert loaded.adapter_bytes == b"FAKE_GGUF_BYTES"
    assert loaded.prompts["system"] == "You classify."
    assert loaded.base_model == "qwen3-4b-instruct-q4_0"


def test_bundle_is_a_zipfile(tmp_path):
    path = _make_bundle(tmp_path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert {"manifest.json", "adapter.gguf", "prompts.json", "spec.json", "metadata.json"} <= names


def test_bundle_rejects_unknown_schema(tmp_path):
    path = tmp_path / "bad.chi"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", '{"schema_version": 999, "name": "x", "description": "y", "base_model": "z", "adapter_format": "gguf-lora", "created_at": "", "chimera_version": ""}')
        zf.writestr("adapter.gguf", b"")
        zf.writestr("prompts.json", "{}")
        zf.writestr("spec.json", '{"name": "x", "description": "y", "examples": [], "input_schema": null, "output_schema": null}')
        zf.writestr("metadata.json", "{}")
    with pytest.raises(ChiBundleError, match="schema_version"):
        ChiBundle.load(path)


def test_bundle_rejects_missing_adapter(tmp_path):
    path = tmp_path / "noadapter.chi"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", '{"schema_version": 1, "name": "x", "description": "y", "base_model": "z", "adapter_format": "gguf-lora", "created_at": "", "chimera_version": ""}')
        zf.writestr("prompts.json", "{}")
        zf.writestr("spec.json", '{"name": "x", "description": "y", "examples": [], "input_schema": null, "output_schema": null}')
        zf.writestr("metadata.json", "{}")
    with pytest.raises(ChiBundleError, match="adapter"):
        ChiBundle.load(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_bundle.py -v`
Expected: FAIL with `ImportError: cannot import name 'ChiBundle' from 'chimera.function_synthesis.bundle'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/bundle.py
"""ChiBundle: the ``.chi`` file format for compiled neural functions.

A ``.chi`` file is a ZIP archive containing a GGUF LoRA adapter and metadata
describing the function it encodes.  See the architecture section of
``docs/superpowers/plans/2026-04-14-function-synthesis.md`` for the layout.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from chimera.function_synthesis.spec import FunctionSpec

SCHEMA_VERSION = 1
_REQUIRED_MEMBERS = {"manifest.json", "adapter.gguf", "prompts.json", "spec.json", "metadata.json"}


class ChiBundleError(ValueError):
    """Raised when a ``.chi`` file is malformed or unsupported."""


@dataclass
class ChiBundle:
    """In-memory representation of a ``.chi`` compiled-function bundle.

    Attributes:
        spec: The :class:`FunctionSpec` that was compiled.
        adapter_bytes: Raw GGUF LoRA adapter bytes.
        prompts: Dict with keys ``system``, ``user_template``, ``stop``.
        metadata: Free-form dict (compiler backend info, base model hash, ...).
        base_model: Identifier of the required base GGUF model.
    """

    spec: FunctionSpec
    adapter_bytes: bytes
    prompts: dict
    metadata: dict = field(default_factory=dict)
    base_model: str = "qwen3-4b-instruct-q4_0"

    def save(self, path: str | Path) -> None:
        """Write the bundle to ``path`` as a ``.chi`` ZIP archive."""
        path = Path(path)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "name": self.spec.name,
            "description": self.spec.description,
            "base_model": self.base_model,
            "adapter_format": "gguf-lora",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "chimera_version": _chimera_version(),
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            zf.writestr("adapter.gguf", self.adapter_bytes)
            zf.writestr("prompts.json", json.dumps(self.prompts, sort_keys=True))
            zf.writestr("spec.json", self.spec.to_json())
            zf.writestr("metadata.json", json.dumps(self.metadata, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> ChiBundle:
        """Load and validate a ``.chi`` bundle from ``path``."""
        path = Path(path)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            missing = _REQUIRED_MEMBERS - names
            if missing:
                raise ChiBundleError(f"bundle missing required members: {sorted(missing)}")
            manifest = json.loads(zf.read("manifest.json"))
            if manifest.get("schema_version") != SCHEMA_VERSION:
                raise ChiBundleError(
                    f"unsupported schema_version {manifest.get('schema_version')!r}"
                    f"; expected {SCHEMA_VERSION}"
                )
            adapter_bytes = zf.read("adapter.gguf")
            prompts = json.loads(zf.read("prompts.json"))
            spec = FunctionSpec.from_json(zf.read("spec.json").decode())
            metadata = json.loads(zf.read("metadata.json"))
        return cls(
            spec=spec,
            adapter_bytes=adapter_bytes,
            prompts=prompts,
            metadata=metadata,
            base_model=manifest["base_model"],
        )


def _chimera_version() -> str:
    try:
        from importlib.metadata import version

        return version("chimera")
    except Exception:
        return "unknown"
```

- [ ] **Step 4: Update package __init__**

```python
# chimera/function_synthesis/__init__.py
"""Function synthesis: compile specs into callable neural artifacts."""
from __future__ import annotations

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.spec import FunctionSpec

__all__ = ["ChiBundle", "ChiBundleError", "FunctionSpec"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_bundle.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/bundle.py chimera/function_synthesis/__init__.py tests/function_synthesis/test_bundle.py
git commit -m "feat(function_synthesis): add .chi bundle format (ChiBundle)"
```

---

## Task 3: Runtime backend ABC

**Files:**
- Create: `chimera/function_synthesis/runtime.py`
- Create: `tests/function_synthesis/test_runtime.py`
- Modify: `chimera/function_synthesis/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_runtime.py
from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec


class _FakeBackend(RuntimeBackend):
    def __init__(self) -> None:
        self.loaded: ChiBundle | None = None
        self.calls: list[str] = []

    def load(self, bundle: ChiBundle) -> None:
        self.loaded = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        self.calls.append(user_input)
        return f"echo:{user_input}"

    def close(self) -> None:
        self.loaded = None


def _bundle(tmp_path: Path) -> Path:
    spec = FunctionSpec(name="echo", description="echoes input")
    ChiBundle(
        spec=spec,
        adapter_bytes=b"FAKE",
        prompts={"system": "echo", "user_template": "{input}", "stop": []},
    ).save(tmp_path / "echo.chi")
    return tmp_path / "echo.chi"


def test_compiled_function_loads_and_calls(tmp_path):
    backend = _FakeBackend()
    fn = CompiledFunction.from_path(_bundle(tmp_path), backend=backend)
    assert fn.name == "echo"
    assert fn("hi") == "echo:hi"
    assert backend.calls == ["hi"]


def test_compiled_function_closes_backend(tmp_path):
    backend = _FakeBackend()
    fn = CompiledFunction.from_path(_bundle(tmp_path), backend=backend)
    fn.close()
    assert backend.loaded is None


def test_compiled_function_context_manager(tmp_path):
    backend = _FakeBackend()
    with CompiledFunction.from_path(_bundle(tmp_path), backend=backend) as fn:
        assert fn("x") == "echo:x"
    assert backend.loaded is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_runtime.py -v`
Expected: FAIL with `ImportError: cannot import name 'CompiledFunction' from 'chimera.function_synthesis.runtime'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/runtime.py
"""CompiledFunction: callable wrapper around a loaded ``.chi`` bundle.

The runtime is backend-agnostic: :class:`RuntimeBackend` is an ABC, and
``chimera.function_synthesis.backends.llama_cpp`` provides the reference
implementation using ``llama-cpp-python``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle


class RuntimeBackend(ABC):
    """Abstract inference backend for compiled functions.

    Implementations must be able to load a :class:`ChiBundle` and run
    inference against it.  Backends are responsible for loading the base
    model and attaching the adapter contained in the bundle.
    """

    @abstractmethod
    def load(self, bundle: ChiBundle) -> None:
        """Load the bundle into the backend, preparing it for inference."""

    @abstractmethod
    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        """Run the loaded function against ``user_input`` and return text."""

    @abstractmethod
    def close(self) -> None:
        """Release any resources held by the backend."""


class CompiledFunction:
    """A loaded ``.chi`` bundle you can call like a Python function."""

    def __init__(self, bundle: ChiBundle, backend: RuntimeBackend) -> None:
        self._bundle = bundle
        self._backend = backend
        backend.load(bundle)

    @classmethod
    def from_path(cls, path: str | Path, *, backend: RuntimeBackend) -> CompiledFunction:
        """Load a ``.chi`` bundle from ``path`` and bind it to ``backend``."""
        return cls(ChiBundle.load(path), backend)

    @property
    def name(self) -> str:
        return self._bundle.spec.name

    @property
    def spec(self) -> Any:
        return self._bundle.spec

    def __call__(self, user_input: str, *, max_tokens: int = 256) -> str:
        return self._backend.invoke(user_input, max_tokens=max_tokens)

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> CompiledFunction:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
```

- [ ] **Step 4: Update package __init__**

```python
# chimera/function_synthesis/__init__.py
"""Function synthesis: compile specs into callable neural artifacts."""
from __future__ import annotations

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec

__all__ = [
    "ChiBundle",
    "ChiBundleError",
    "CompiledFunction",
    "FunctionSpec",
    "RuntimeBackend",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_runtime.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/runtime.py chimera/function_synthesis/__init__.py tests/function_synthesis/test_runtime.py
git commit -m "feat(function_synthesis): add CompiledFunction runtime + RuntimeBackend ABC"
```

---

## Task 4: LlamaCppBackend (optional dependency)

**Files:**
- Create: `chimera/function_synthesis/backends/__init__.py`
- Create: `chimera/function_synthesis/backends/llama_cpp.py`
- Create: `tests/function_synthesis/test_llama_cpp_backend.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_llama_cpp_backend.py
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.spec import FunctionSpec


def _install_fake_llama_cpp(monkeypatch, captured: dict):
    fake_module = types.ModuleType("llama_cpp")

    class FakeLlama:
        def __init__(self, *, model_path, lora_path, **kwargs):
            captured["model_path"] = model_path
            captured["lora_path"] = lora_path

        def create_chat_completion(self, messages, max_tokens, stop=None):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            return {"choices": [{"message": {"content": "RESULT"}}]}

    fake_module.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)


def _bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"ADAPTER",
        prompts={"system": "sys", "user_template": "U:{input}", "stop": []},
    )


def test_llama_cpp_backend_loads_and_invokes(monkeypatch, tmp_path):
    captured: dict = {}
    _install_fake_llama_cpp(monkeypatch, captured)

    base_path = tmp_path / "base.gguf"
    base_path.write_bytes(b"BASE")

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base_path)
    backend.load(_bundle())
    out = backend.invoke("hello")

    assert out == "RESULT"
    assert captured["model_path"] == str(base_path)
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][0]["content"] == "sys"
    assert captured["messages"][1]["content"] == "U:hello"
    backend.close()


def test_llama_cpp_backend_missing_dep_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=tmp_path / "base.gguf")
    with pytest.raises(ImportError, match="llama-cpp-python"):
        backend.load(_bundle())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_llama_cpp_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chimera.function_synthesis.backends'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/backends/__init__.py
"""Runtime backends for compiled neural functions."""
```

```python
# chimera/function_synthesis/backends/llama_cpp.py
"""llama.cpp runtime backend (optional dependency: llama-cpp-python)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import RuntimeBackend


class LlamaCppBackend(RuntimeBackend):
    """Runs compiled functions via ``llama-cpp-python``.

    The base GGUF model is loaded once; each :meth:`load` swaps the LoRA
    adapter carried inside the bundle.

    Args:
        base_model_path: Path to the base GGUF model file.
        n_ctx: Context window size.
        n_threads: CPU threads (None = library default).
    """

    def __init__(
        self,
        *,
        base_model_path: str | Path,
        n_ctx: int = 2048,
        n_threads: int | None = None,
    ) -> None:
        self._base_model_path = Path(base_model_path)
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._llm = None
        self._bundle: ChiBundle | None = None
        self._adapter_tmp: Path | None = None

    def load(self, bundle: ChiBundle) -> None:
        try:
            import llama_cpp  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "LlamaCppBackend requires llama-cpp-python. "
                "Install with: pip install 'chimera[function_synthesis]'"
            ) from exc

        # llama.cpp reads the adapter from disk; extract it to a tempfile.
        tmp = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
        tmp.write(bundle.adapter_bytes)
        tmp.close()
        self._adapter_tmp = Path(tmp.name)

        kwargs: dict = {
            "model_path": str(self._base_model_path),
            "lora_path": str(self._adapter_tmp),
            "n_ctx": self._n_ctx,
        }
        if self._n_threads is not None:
            kwargs["n_threads"] = self._n_threads

        self._llm = llama_cpp.Llama(**kwargs)
        self._bundle = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        if self._llm is None or self._bundle is None:
            raise RuntimeError("backend not loaded; call load() first")
        prompts = self._bundle.prompts
        user_msg = prompts.get("user_template", "{input}").format(input=user_input)
        messages = [
            {"role": "system", "content": prompts.get("system", "")},
            {"role": "user", "content": user_msg},
        ]
        result = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            stop=prompts.get("stop") or None,
        )
        return result["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._llm = None
        self._bundle = None
        if self._adapter_tmp is not None and self._adapter_tmp.exists():
            try:
                self._adapter_tmp.unlink()
            except OSError:
                pass
        self._adapter_tmp = None
```

- [ ] **Step 4: Add optional extra in pyproject.toml**

In `pyproject.toml` under `[project.optional-dependencies]`, add:

```toml
function_synthesis = ["llama-cpp-python>=0.3.0"]
```

(Keep existing extras untouched. If the section already has a trailing entry, add the new key alphabetically or at the end, matching the file's existing style.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_llama_cpp_backend.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/backends/__init__.py chimera/function_synthesis/backends/llama_cpp.py tests/function_synthesis/test_llama_cpp_backend.py pyproject.toml
git commit -m "feat(function_synthesis): add LlamaCppBackend (optional llama-cpp-python)"
```

---

## Task 5: CompilerBackend ABC

**Files:**
- Create: `chimera/function_synthesis/compiler.py`
- Create: `tests/function_synthesis/test_compiler.py`
- Modify: `chimera/function_synthesis/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_compiler.py
from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.spec import FunctionSpec


class _DummyCompiler(CompilerBackend):
    def compile(self, spec: FunctionSpec) -> ChiBundle:
        return ChiBundle(
            spec=spec,
            adapter_bytes=b"DUMMY",
            prompts={"system": "", "user_template": "{input}", "stop": []},
            metadata={"compiler_backend": "dummy"},
        )


def test_compiler_backend_compile_and_save(tmp_path):
    compiler = _DummyCompiler()
    spec = FunctionSpec(name="echo", description="echo")
    bundle = compiler.compile(spec)
    out = tmp_path / "echo.chi"
    bundle.save(out)
    assert ChiBundle.load(out).metadata["compiler_backend"] == "dummy"


def test_compiler_error_is_value_error_subclass():
    assert issubclass(CompilerError, ValueError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_compiler.py -v`
Expected: FAIL with `ImportError: cannot import name 'CompilerBackend'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/compiler.py
"""CompilerBackend: the abstract interface for producing ``.chi`` bundles.

Concrete compilers (see ``chimera.function_synthesis.compilers``) take a
:class:`FunctionSpec` and return a :class:`ChiBundle`.  The ABC keeps the
synthesis strategy layer independent of any specific compilation backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.spec import FunctionSpec


class CompilerError(ValueError):
    """Raised when a compiler backend cannot produce a bundle."""


class CompilerBackend(ABC):
    """Abstract compiler that turns a :class:`FunctionSpec` into a bundle."""

    @abstractmethod
    def compile(self, spec: FunctionSpec) -> ChiBundle:
        """Compile ``spec`` into a :class:`ChiBundle`."""
```

- [ ] **Step 4: Update package __init__**

```python
# chimera/function_synthesis/__init__.py
"""Function synthesis: compile specs into callable neural artifacts."""
from __future__ import annotations

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec

__all__ = [
    "ChiBundle",
    "ChiBundleError",
    "CompiledFunction",
    "CompilerBackend",
    "CompilerError",
    "FunctionSpec",
    "RuntimeBackend",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_compiler.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/compiler.py chimera/function_synthesis/__init__.py tests/function_synthesis/test_compiler.py
git commit -m "feat(function_synthesis): add CompilerBackend ABC"
```

---

## Task 6: RemoteCompiler (HTTP client)

**Files:**
- Create: `chimera/function_synthesis/compilers/__init__.py`
- Create: `chimera/function_synthesis/compilers/remote.py`
- Create: `tests/function_synthesis/test_remote_compiler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_remote_compiler.py
from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerError
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.spec import FunctionSpec


def _zip_bytes() -> bytes:
    spec = FunctionSpec(name="echo", description="echo")
    bundle = ChiBundle(
        spec=spec,
        adapter_bytes=b"ADAPTER",
        prompts={"system": "", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "remote"},
    )
    buf = io.BytesIO()
    # mirror ChiBundle.save but write to a buffer
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.chi"
        bundle.save(path)
        return path.read_bytes()


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def test_remote_compiler_posts_spec_and_returns_bundle(monkeypatch):
    payload = _zip_bytes()
    fake_post = MagicMock(return_value=_FakeResponse(200, content=payload))

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers, timeout):
            return fake_post(url, json=json, headers=headers, timeout=timeout)

    import chimera.function_synthesis.compilers.remote as mod
    monkeypatch.setattr(mod, "_Client", _FakeClient)

    compiler = RemoteCompiler(endpoint="https://example.test/compile", api_key="secret")
    bundle = compiler.compile(FunctionSpec(name="echo", description="echo"))

    assert bundle.spec.name == "echo"
    assert bundle.metadata["compiler_backend"] == "remote"
    fake_post.assert_called_once()
    _, kwargs = fake_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"]["spec"]["name"] == "echo"


def test_remote_compiler_raises_compiler_error_on_http_failure(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers, timeout):
            return _FakeResponse(500, text="boom")

    import chimera.function_synthesis.compilers.remote as mod
    monkeypatch.setattr(mod, "_Client", _FakeClient)

    compiler = RemoteCompiler(endpoint="https://example.test/compile")
    with pytest.raises(CompilerError, match="500"):
        compiler.compile(FunctionSpec(name="x", description="y"))


def test_remote_compiler_requires_httpx_when_missing(monkeypatch):
    import chimera.function_synthesis.compilers.remote as mod
    monkeypatch.setattr(mod, "_Client", None)
    compiler = RemoteCompiler(endpoint="https://example.test/compile")
    with pytest.raises(ImportError, match="httpx"):
        compiler.compile(FunctionSpec(name="x", description="y"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_remote_compiler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chimera.function_synthesis.compilers'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/compilers/__init__.py
"""Concrete compiler backends (see also chimera.function_synthesis.compiler)."""
```

```python
# chimera/function_synthesis/compilers/remote.py
"""RemoteCompiler: HTTP client that delegates compilation to an external service.

The service contract is intentionally small:

- POST ``{endpoint}`` with JSON ``{"spec": <FunctionSpec.to_json parsed>}``
- Optional ``Authorization: Bearer <api_key>`` header
- Response: raw ``.chi`` bundle bytes (``application/zip``)

This keeps chimera free of training infrastructure while letting users plug
in any compatible backend (self-hosted or third-party).
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.spec import FunctionSpec

try:  # pragma: no cover - import guard
    import httpx as _httpx  # type: ignore[import-not-found]

    _Client = _httpx.Client
except ImportError:
    _Client = None  # type: ignore[assignment]


class RemoteCompiler(CompilerBackend):
    """Compile function specs by POSTing them to an external HTTP service.

    Args:
        endpoint: Full URL of the compile endpoint.
        api_key: Optional bearer token.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        if _Client is None:
            raise ImportError(
                "RemoteCompiler requires httpx. Install with: pip install 'chimera[remote]'"
            )
        headers: dict[str, str] = {"Accept": "application/zip"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"spec": json.loads(spec.to_json())}
        with _Client() as client:
            response = client.post(
                self._endpoint,
                json=body,
                headers=headers,
                timeout=self._timeout,
            )
        if response.status_code >= 400:
            raise CompilerError(
                f"remote compile failed: HTTP {response.status_code}: {getattr(response, 'text', '')}"
            )
        return _bytes_to_bundle(response.content)


def _bytes_to_bundle(data: bytes) -> ChiBundle:
    """Write ``data`` to a tempfile and load it as a :class:`ChiBundle`."""
    tmp = tempfile.NamedTemporaryFile(suffix=".chi", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return ChiBundle.load(Path(tmp.name))
    finally:
        try:
            Path(tmp.name).unlink()
        except OSError:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_remote_compiler.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/compilers/__init__.py chimera/function_synthesis/compilers/remote.py tests/function_synthesis/test_remote_compiler.py
git commit -m "feat(function_synthesis): add RemoteCompiler HTTP client"
```

---

## Task 7: CompiledFunctionTool (agent-side wrapper)

**Files:**
- Create: `chimera/tools/compiled_function_tool.py`
- Create: `tests/tools/test_compiled_function_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_compiled_function_tool.py
from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec
from chimera.tools.compiled_function_tool import CompiledFunctionTool


class _StubBackend(RuntimeBackend):
    def load(self, bundle):
        self.bundle = bundle

    def invoke(self, user_input, *, max_tokens=256):
        return f"OUT[{self.bundle.spec.name}]:{user_input}"

    def close(self):
        pass


def _bundle_path(tmp_path: Path) -> Path:
    ChiBundle(
        spec=FunctionSpec(name="sentiment", description="classify pos/neg"),
        adapter_bytes=b"A",
        prompts={"system": "", "user_template": "{input}", "stop": []},
    ).save(tmp_path / "sentiment.chi")
    return tmp_path / "sentiment.chi"


def test_tool_exposes_function_name_and_description(tmp_path):
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=_StubBackend())
    tool = CompiledFunctionTool(fn)
    assert tool.name == "sentiment"
    assert "classify pos/neg" in tool.description


def test_tool_call_returns_function_output(tmp_path):
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=_StubBackend())
    tool = CompiledFunctionTool(fn)
    assert tool.execute(user_input="great movie") == "OUT[sentiment]:great movie"


def test_tool_name_override(tmp_path):
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=_StubBackend())
    tool = CompiledFunctionTool(fn, name="classify_sentiment")
    assert tool.name == "classify_sentiment"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/test_compiled_function_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chimera.tools.compiled_function_tool'`

- [ ] **Step 3: Inspect BaseTool shape before implementing**

Run: `uv run python -c "from chimera.core.tool import BaseTool; import inspect; print(inspect.getsource(BaseTool))"`

Use the observed signatures (name, description, input schema, execute) when writing the tool class below. If `BaseTool` uses different attribute names than assumed here, update the implementation in Step 4 accordingly — keep the test interface (`tool.name`, `tool.description`, `tool.execute(user_input=...)`) stable.

- [ ] **Step 4: Write minimal implementation**

```python
# chimera/tools/compiled_function_tool.py
"""CompiledFunctionTool: expose a :class:`CompiledFunction` as an agent tool."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool

if TYPE_CHECKING:
    from chimera.function_synthesis.runtime import CompiledFunction


class CompiledFunctionTool(BaseTool):
    """Wraps a loaded compiled function so agents can call it as a tool.

    Uses the function's name/description by default.  Agents call the tool
    with a single ``user_input`` string; the compiled function's output is
    returned verbatim.
    """

    def __init__(
        self,
        function: CompiledFunction,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        self._function = function
        self._name = name or function.name
        self._description = description or f"Compiled neural function: {function.spec.description}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_input": {"type": "string", "description": "Input passed to the compiled function."},
            },
            "required": ["user_input"],
        }

    def execute(self, user_input: str, **_kwargs: Any) -> str:
        return self._function(user_input)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/tools/test_compiled_function_tool.py -v`
Expected: 3 passed

If `BaseTool` rejects this shape (e.g., requires a different `execute` signature, or uses `get_schema()` instead of `input_schema`), update only the attribute/method shape — not the behavior — and re-run.

- [ ] **Step 6: Commit**

```bash
git add chimera/tools/compiled_function_tool.py tests/tools/test_compiled_function_tool.py
git commit -m "feat(tools): add CompiledFunctionTool wrapper for compiled neural functions"
```

---

## Task 8: FunctionSynthesisStrategy

**Files:**
- Create: `chimera/function_synthesis/strategies/__init__.py`
- Create: `chimera/function_synthesis/strategies/synthesis.py`
- Create: `tests/function_synthesis/test_synthesis_strategy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_synthesis_strategy.py
from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.spec import FunctionSpec
from chimera.function_synthesis.strategies.synthesis import FunctionSynthesisStrategy


class _RecordingCompiler(CompilerBackend):
    def __init__(self) -> None:
        self.calls: list[FunctionSpec] = []

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        self.calls.append(spec)
        return ChiBundle(
            spec=spec,
            adapter_bytes=b"A",
            prompts={"system": "", "user_template": "{input}", "stop": []},
            metadata={"compiler_backend": "recording"},
        )


def test_strategy_compiles_spec_and_saves_bundle(tmp_path):
    compiler = _RecordingCompiler()
    strategy = FunctionSynthesisStrategy(compiler=compiler, output_dir=tmp_path)
    spec = FunctionSpec(name="cls", description="classify")
    result = strategy.run(spec)

    assert result.bundle_path == tmp_path / "cls.chi"
    assert result.bundle_path.exists()
    loaded = ChiBundle.load(result.bundle_path)
    assert loaded.metadata["compiler_backend"] == "recording"
    assert compiler.calls == [spec]


def test_strategy_overwrites_existing_bundle(tmp_path):
    target = tmp_path / "cls.chi"
    target.write_bytes(b"stale")
    strategy = FunctionSynthesisStrategy(compiler=_RecordingCompiler(), output_dir=tmp_path)
    strategy.run(FunctionSpec(name="cls", description="classify"))
    assert ChiBundle.load(target).spec.name == "cls"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_synthesis_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chimera.function_synthesis.strategies'`

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/strategies/__init__.py
"""Synthesis strategies that produce function artifacts (.chi bundles)."""
from __future__ import annotations

from chimera.function_synthesis.strategies.synthesis import (
    FunctionSynthesisResult,
    FunctionSynthesisStrategy,
)

__all__ = ["FunctionSynthesisResult", "FunctionSynthesisStrategy"]
```

```python
# chimera/function_synthesis/strategies/synthesis.py
"""FunctionSynthesisStrategy: compile a FunctionSpec into a .chi bundle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.spec import FunctionSpec


@dataclass
class FunctionSynthesisResult:
    """Result of running :class:`FunctionSynthesisStrategy`."""

    spec: FunctionSpec
    bundle_path: Path


class FunctionSynthesisStrategy:
    """Produce a ``.chi`` bundle from a :class:`FunctionSpec`.

    Unlike code-synthesis strategies (TestConvergence, CEGIS, ...), the
    output here is a neural artifact rather than source.  The strategy is
    a thin orchestration layer: it delegates compilation to the injected
    :class:`CompilerBackend` and writes the bundle to ``output_dir``.

    Args:
        compiler: A concrete :class:`CompilerBackend` (e.g. RemoteCompiler).
        output_dir: Directory to write the resulting ``<name>.chi`` file.
    """

    def __init__(
        self,
        *,
        compiler: CompilerBackend,
        output_dir: str | Path,
    ) -> None:
        self._compiler = compiler
        self._output_dir = Path(output_dir)

    def run(self, spec: FunctionSpec) -> FunctionSynthesisResult:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        bundle = self._compiler.compile(spec)
        dst = self._output_dir / f"{spec.name}.chi"
        bundle.save(dst)
        return FunctionSynthesisResult(spec=spec, bundle_path=dst)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_synthesis_strategy.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/strategies/__init__.py chimera/function_synthesis/strategies/synthesis.py tests/function_synthesis/test_synthesis_strategy.py
git commit -m "feat(function_synthesis): add FunctionSynthesisStrategy"
```

---

## Task 9: Full-suite regression + lint + type check

**Files:** none (verification task).

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest`
Expected: all previously-passing tests still pass; ~12 new tests pass; 0 failures.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check chimera/function_synthesis/ chimera/tools/compiled_function_tool.py tests/function_synthesis/ tests/tools/test_compiled_function_tool.py`
Expected: no issues.

- [ ] **Step 3: Run mypy on the new module**

Run: `uv run mypy chimera/function_synthesis/`
Expected: no errors. (If mypy flags the optional `llama_cpp` / `httpx` imports, the `# type: ignore[import-not-found]` comments already in the plan suppress them.)

- [ ] **Step 4: Record final status**

Run: `uv run pytest --collect-only -q | tail -5`
Record the total test count in your commit message body.

- [ ] **Step 5: Commit (chore, if anything changed; otherwise skip)**

Only if lint/mypy required any minor fixups:

```bash
git add -u chimera/function_synthesis/ chimera/tools/compiled_function_tool.py
git commit -m "chore(function_synthesis): lint/type fixups"
```

---

## Deferred (not in this plan)

- **LocalLoRACompiler** — pure-Python PEFT-based compiler for users without a remote service. Needs a separate plan: depends on `transformers` + `peft` + `bitsandbytes`, a training recipe, and a conversion step from PEFT LoRA → GGUF Q4_0.
- **CLI integration** — `chimera compile <spec.yaml>` subcommand. Small follow-up once the API surface stabilizes.
- **Agent preset** — a `FunctionSmith` preset that routes a natural-language request through `FunctionSynthesisStrategy` and returns a loaded `CompiledFunction`.
- **Streaming invoke** — `CompiledFunction.stream(...)` for token-by-token output from llama.cpp.
- **Adapter caching** — shared adapter directory so multiple `CompiledFunction` instances reuse extracted GGUF files.

---

## Self-Review Notes

- Spec coverage: every decision from the scoping conversation is in a task — module name (Tasks 1–8), `.chi` format (Task 2), pluggable `CompilerBackend` (Task 5), `RemoteCompiler` (Task 6), `CompiledFunctionTool` (Task 7), `FunctionSynthesisStrategy` (Task 8), `LocalLoRACompiler` deferred (see Deferred section).
- Placeholders: none — every step has exact code, exact commands, exact expected output.
- Type consistency: `FunctionSpec`, `ChiBundle`, `CompiledFunction`, `RuntimeBackend`, `CompilerBackend`, `CompilerError`, `ChiBundleError`, `LlamaCppBackend`, `RemoteCompiler`, `CompiledFunctionTool`, `FunctionSynthesisStrategy`, `FunctionSynthesisResult` — names used consistently across all tasks. `CompiledFunction.__call__(user_input, *, max_tokens)` matches `RuntimeBackend.invoke` matches tool `execute(user_input=...)`.
- Core-dependency rule: new runtime (`llama-cpp-python`) behind optional extra; `httpx` reuses existing extra. Zero net impact on the zero-dep core.
