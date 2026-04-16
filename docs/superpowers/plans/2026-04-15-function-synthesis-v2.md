# Function Synthesis v2 — User-Facing Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `chimera/function_synthesis/` usable by a real developer end-to-end: they install Chimera, run one CLI command, get a working compiled function. Closes the capability gap between the v1 infrastructure slice (shipped 2026-04-14) and a shippable subsystem.

**Architecture:**
- **Cache layer** — `chimera/function_synthesis/cache.py` manages a per-user on-disk store under `~/.chimera/function_synthesis/{models,bundles}/` with a JSON index for slug lookup.
- **Registry** — `chimera/function_synthesis/registry.py` handles slug generation, bundle installation, listing, and removal. Wraps the cache in a higher-level API.
- **Cold-start cache** — `chimera/function_synthesis/prefix_cache.py` saves llama.cpp post-prompt state to disk so subsequent calls skip the system-prompt prefill.
- **CLI** — adds `chimera fs` subcommand group to `chimera/cli/main.py` with `compile`, `run`, `list`, `rm`, `info` verbs.
- **Offline mode** — `CHIMERA_FS_OFFLINE=1` env var short-circuits network calls; cache misses raise a clear error.
- **E2E test** — opt-in `pytest -m live` run that compiles a real spec against a small real model.
- **Docs** — `docs/function-synthesis.md` quickstart + `examples/function_synthesis_quickstart.py` runnable script.

**Tech Stack:** Python 3.11+, stdlib-only where possible. Optional extras: `huggingface_hub>=0.25` (gated by a new `function_synthesis_hub` extra, or rolled into the existing `function_synthesis` extra). No new runtime deps in core.

**Key design decisions (override before execution if you disagree):**
1. **Storage root:** `~/.chimera/function_synthesis/` — mirrors existing `~/.chimera/sessions/`. Overridable via `CHIMERA_FS_HOME`.
2. **Base-model transport:** Hugging Face Hub (via `huggingface_hub` snapshot download). Gated by extra. Alternative transport (raw URL in metadata) supported as a fallback without the extra.
3. **CLI namespace:** `chimera fs compile|run|list|rm|info` — keeps new verbs out of the top-level namespace.
4. **Slug format:** `<spec.name>-<sha256(spec.to_json())[:8]>` — deterministic, collision-resistant, human-ish.
5. **E2E model:** smallest chat-tuned GGUF available (TinyLlama Q4 or similar, <700MB). Live tests are opt-in (`-m live`), skipped in default pytest runs and CI.
6. **Compile service contract:** documented, not implemented. A `docs/function-synthesis-compile-protocol.md` spec is in scope; a reference server is not. A minimal `MockCompiler` fixture is added for testing.

---

## File Structure

```
chimera/function_synthesis/
  cache.py                     # CacheDirs, BaseModelCache, BundleCache (NEW)
  registry.py                  # ProgramRegistry, slug generation (NEW)
  prefix_cache.py              # PrefixCache for cold-start state (NEW)
  errors.py                    # OfflineError, CacheMissError (NEW)
  backends/
    llama_cpp.py               # MODIFY: wire PrefixCache
  compilers/
    mock.py                    # MockCompiler for tests + examples (NEW)

chimera/cli/
  main.py                      # MODIFY: register `fs` subcommand group
  fs.py                        # `chimera fs` subcommand implementations (NEW)

docs/
  function-synthesis.md        # Quickstart + API tour (NEW)
  function-synthesis-compile-protocol.md  # HTTP contract spec (NEW)

examples/
  function_synthesis_quickstart.py  # Runnable end-to-end example (NEW)

tests/function_synthesis/
  test_cache.py                # (NEW)
  test_registry.py             # (NEW)
  test_prefix_cache.py         # (NEW)
  test_errors.py               # (NEW)
  test_mock_compiler.py        # (NEW)
  test_live_e2e.py             # @pytest.mark.live, opt-in (NEW)
tests/cli/
  test_fs_cli.py               # (NEW)
```

---

## Milestone 1 — Cache Layer (Tasks 1-4)

### Task 1: CacheDirs (storage root resolution)

**Files:**
- Create: `chimera/function_synthesis/cache.py`
- Create: `tests/function_synthesis/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_cache.py::test_default_home_under_dot_chimera -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/cache.py
"""On-disk cache for base models and compiled bundles.

Layout::

    $CHIMERA_FS_HOME/  (default: ~/.chimera/function_synthesis/)
      models/         # base model files (GGUF)
      bundles/        # installed .chi bundles, one per slug
      index.json      # slug -> bundle path + metadata
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOME_ENV = "CHIMERA_FS_HOME"
_DEFAULT_SUBPATH = ".chimera/function_synthesis"


@dataclass(frozen=True)
class CacheDirs:
    """Resolves and owns the on-disk cache layout."""

    root: Path

    @classmethod
    def default(cls) -> CacheDirs:
        env = os.environ.get(DEFAULT_HOME_ENV)
        if env:
            return cls(root=Path(env))
        home = Path(os.environ.get("HOME") or Path.home())
        return cls(root=home / _DEFAULT_SUBPATH)

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def bundles(self) -> Path:
        return self.root / "bundles"

    @property
    def index_file(self) -> Path:
        return self.root / "index.json"

    def ensure(self) -> None:
        """Create all cache subdirectories if missing."""
        self.models.mkdir(parents=True, exist_ok=True)
        self.bundles.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_cache.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/cache.py tests/function_synthesis/test_cache.py
git commit -m "feat(function_synthesis): add CacheDirs for on-disk cache layout"
```

---

### Task 2: Errors module (OfflineError, CacheMissError)

**Files:**
- Create: `chimera/function_synthesis/errors.py`
- Create: `tests/function_synthesis/test_errors.py`
- Modify: `chimera/function_synthesis/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_errors.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Write minimal implementation**

```python
# chimera/function_synthesis/errors.py
"""Function-synthesis-specific exceptions."""
from __future__ import annotations


class CacheMissError(LookupError):
    """Raised when a required cache entry is missing and refresh is disallowed."""

    def __init__(self, *, kind: str, key: str) -> None:
        super().__init__(f"cache miss: {kind}={key!r}")
        self.kind = kind
        self.key = key


class OfflineError(RuntimeError):
    """Raised when an online operation is attempted while offline mode is active."""

    def __init__(self, *, operation: str) -> None:
        super().__init__(f"offline mode active; refusing to {operation}")
        self.operation = operation
```

- [ ] **Step 4: Update package __init__**

```python
# chimera/function_synthesis/__init__.py — add these two lines
from chimera.function_synthesis.errors import CacheMissError, OfflineError
```

And extend `__all__` to include `"CacheMissError"` and `"OfflineError"`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/function_synthesis/test_errors.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/errors.py chimera/function_synthesis/__init__.py tests/function_synthesis/test_errors.py
git commit -m "feat(function_synthesis): add CacheMissError and OfflineError"
```

---

### Task 3: BaseModelCache (download + locate base GGUFs)

**Files:**
- Modify: `chimera/function_synthesis/cache.py` — append `BaseModelCache` class.
- Modify: `tests/function_synthesis/test_cache.py` — append tests.
- Modify: `pyproject.toml` — extend the `function_synthesis` extra with `huggingface_hub>=0.25`.

- [ ] **Step 1: Write the failing test**

Append to `tests/function_synthesis/test_cache.py`:

```python
import sys
import types

from chimera.function_synthesis.cache import BaseModelCache
from chimera.function_synthesis.errors import CacheMissError, OfflineError


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

    fake.hf_hub_download = hf_hub_download
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/function_synthesis/test_cache.py -v`
Expected: the 4 new tests fail (ImportError for BaseModelCache).

- [ ] **Step 3: Extend `cache.py`**

Append to `chimera/function_synthesis/cache.py`:

```python
from chimera.function_synthesis.errors import CacheMissError, OfflineError


OFFLINE_ENV = "CHIMERA_FS_OFFLINE"


def _offline() -> bool:
    return os.environ.get(OFFLINE_ENV, "").lower() in {"1", "true", "yes"}


def _safe_segment(repo_id: str) -> str:
    # turn "org/repo" into "org--repo" (filesystem-safe, reversible-ish)
    return repo_id.replace("/", "--")


class BaseModelCache:
    """Resolves and downloads base GGUF model files to the local cache.

    The cache is content-addressed by ``(repo_id, filename)`` pairs and stored
    under ``<models>/<safe(repo_id)>/<filename>``.  Downloads are delegated to
    ``huggingface_hub`` (optional dep); passing ``CHIMERA_FS_OFFLINE=1`` forces
    a cache-only lookup that raises :class:`CacheMissError` on misses.
    """

    def __init__(self, dirs: CacheDirs) -> None:
        self.dirs = dirs
        dirs.ensure()

    def local_path(self, repo_id: str, filename: str) -> Path:
        return self.dirs.models / _safe_segment(repo_id) / filename

    def get(self, repo_id: str, filename: str) -> Path:
        target = self.local_path(repo_id, filename)
        if target.exists():
            return target
        if _offline():
            raise OfflineError(
                operation=f"download base model {repo_id!r}/{filename!r}"
            )
        try:
            import huggingface_hub  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "BaseModelCache requires huggingface_hub. "
                "Install with: pip install 'chimera[function_synthesis]'"
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved = huggingface_hub.hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=target.parent,
        )
        return Path(resolved)
```

- [ ] **Step 4: Update pyproject extra**

In `pyproject.toml`, change the `function_synthesis` extra:

```toml
function_synthesis = ["llama-cpp-python>=0.3.0", "huggingface_hub>=0.25"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/function_synthesis/test_cache.py -v`
Expected: 7 passed (3 from Task 1 + 4 from this task).

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/cache.py tests/function_synthesis/test_cache.py pyproject.toml
git commit -m "feat(function_synthesis): add BaseModelCache with HF hub download + offline mode"
```

---

### Task 4: BundleCache (install / locate / remove .chi files)

**Files:**
- Modify: `chimera/function_synthesis/cache.py` — append `BundleCache`.
- Modify: `tests/function_synthesis/test_cache.py` — append tests.

- [ ] **Step 1: Write the failing test**

Append:

```python
from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.cache import BundleCache
from chimera.function_synthesis.spec import FunctionSpec


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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/function_synthesis/test_cache.py -v`
Expected: 3 new failures (ImportError for BundleCache).

- [ ] **Step 3: Append `BundleCache` to `cache.py`**

```python
from chimera.function_synthesis.bundle import ChiBundle


class BundleCache:
    """Stores compiled ``.chi`` bundles on disk, keyed by slug."""

    def __init__(self, dirs: CacheDirs) -> None:
        self.dirs = dirs
        dirs.ensure()

    def _path(self, slug: str) -> Path:
        return self.dirs.bundles / f"{slug}.chi"

    def install(self, *, slug: str, bundle: ChiBundle) -> Path:
        target = self._path(slug)
        bundle.save(target)
        return target

    def get(self, slug: str) -> Path:
        target = self._path(slug)
        if not target.exists():
            raise CacheMissError(kind="bundle", key=slug)
        return target

    def list(self) -> list[str]:
        if not self.dirs.bundles.exists():
            return []
        return sorted(p.stem for p in self.dirs.bundles.glob("*.chi"))

    def remove(self, slug: str) -> None:
        target = self._path(slug)
        if target.exists():
            target.unlink()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/function_synthesis/test_cache.py -v`
Expected: 10 passed total.

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/cache.py tests/function_synthesis/test_cache.py
git commit -m "feat(function_synthesis): add BundleCache for local .chi storage"
```

---

## Milestone 2 — Registry (Tasks 5-6)

### Task 5: Slug generation + ProgramIndex

**Files:**
- Create: `chimera/function_synthesis/registry.py`
- Create: `tests/function_synthesis/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_registry.py
from __future__ import annotations

import pytest

from chimera.function_synthesis.cache import CacheDirs, BundleCache
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
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/function_synthesis/test_registry.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `registry.py`**

```python
# chimera/function_synthesis/registry.py
"""Local program registry: slug -> installed bundle + metadata."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.cache import BundleCache, CacheDirs
from chimera.function_synthesis.errors import CacheMissError
from chimera.function_synthesis.spec import FunctionSpec


def slug_for(spec: FunctionSpec) -> str:
    """Return a deterministic slug ``<name>-<hash8>`` for the spec."""
    digest = hashlib.sha256(spec.to_json().encode()).hexdigest()[:8]
    return f"{spec.name}-{digest}"


@dataclass
class ProgramEntry:
    """An installed program."""

    slug: str
    bundle_path: Path
    spec: FunctionSpec
    metadata: dict = field(default_factory=dict)


class ProgramRegistry:
    """Local registry that installs bundles and resolves slugs to paths."""

    def __init__(self, dirs: CacheDirs) -> None:
        self.dirs = dirs
        self.dirs.ensure()
        self._bundles = BundleCache(dirs)

    @classmethod
    def default(cls) -> ProgramRegistry:
        return cls(CacheDirs.default())

    def _load_index(self) -> dict:
        if not self.dirs.index_file.exists():
            return {}
        return json.loads(self.dirs.index_file.read_text())

    def _save_index(self, index: dict) -> None:
        self.dirs.index_file.write_text(json.dumps(index, sort_keys=True, indent=2))

    def install(self, *, spec: FunctionSpec, bundle: ChiBundle) -> str:
        slug = slug_for(spec)
        path = self._bundles.install(slug=slug, bundle=bundle)
        index = self._load_index()
        index[slug] = {
            "bundle_path": str(path),
            "spec": json.loads(spec.to_json()),
            "metadata": bundle.metadata,
        }
        self._save_index(index)
        return slug

    def resolve(self, slug: str) -> ProgramEntry:
        index = self._load_index()
        if slug not in index:
            raise CacheMissError(kind="program", key=slug)
        entry = index[slug]
        spec = FunctionSpec.from_json(json.dumps(entry["spec"]))
        return ProgramEntry(
            slug=slug,
            bundle_path=Path(entry["bundle_path"]),
            spec=spec,
            metadata=entry.get("metadata", {}),
        )

    def list(self) -> list[ProgramEntry]:
        index = self._load_index()
        return [self.resolve(slug) for slug in sorted(index)]

    def remove(self, slug: str) -> None:
        index = self._load_index()
        if slug in index:
            self._bundles.remove(slug)
            del index[slug]
            self._save_index(index)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/function_synthesis/test_registry.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/registry.py tests/function_synthesis/test_registry.py
git commit -m "feat(function_synthesis): add ProgramRegistry with deterministic slugs"
```

---

### Task 6: MockCompiler (for tests, examples, and offline dev loop)

**Files:**
- Create: `chimera/function_synthesis/compilers/mock.py`
- Create: `tests/function_synthesis/test_mock_compiler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/function_synthesis/test_mock_compiler.py
from __future__ import annotations

from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.spec import FunctionSpec


def test_mock_compiler_emits_bundle_without_network():
    compiler = MockCompiler()
    spec = FunctionSpec(name="classify", description="classify sentiment")
    bundle = compiler.compile(spec)
    assert bundle.spec == spec
    assert bundle.adapter_bytes  # non-empty
    assert bundle.prompts["user_template"].strip() != ""
    assert bundle.metadata["compiler_backend"] == "mock"


def test_mock_compiler_uses_spec_description_in_system_prompt():
    spec = FunctionSpec(name="x", description="Extract the first email address.")
    bundle = MockCompiler().compile(spec)
    assert "Extract the first email address." in bundle.prompts["system"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/function_synthesis/test_mock_compiler.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `mock.py`**

```python
# chimera/function_synthesis/compilers/mock.py
"""MockCompiler: produces bundles without network or training.

Use for unit tests, examples, and offline development.  The adapter bytes are
a deterministic placeholder — the resulting bundle cannot be invoked against
a real base model, but it round-trips through save/load and works with any
backend that doesn't validate adapter contents.
"""
from __future__ import annotations

import hashlib

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.spec import FunctionSpec


class MockCompiler(CompilerBackend):
    """Compiler that emits a deterministic, non-functional bundle."""

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        digest = hashlib.sha256(spec.to_json().encode()).digest()
        return ChiBundle(
            spec=spec,
            adapter_bytes=b"MOCK_ADAPTER:" + digest,
            prompts={
                "system": f"You are a compiled function. {spec.description}",
                "user_template": "{input}",
                "stop": [],
            },
            metadata={"compiler_backend": "mock", "deterministic": True},
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/function_synthesis/test_mock_compiler.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add chimera/function_synthesis/compilers/mock.py tests/function_synthesis/test_mock_compiler.py
git commit -m "feat(function_synthesis): add MockCompiler for tests + offline dev"
```

---

## Milestone 3 — CLI (Tasks 7-10)

### Task 7: `chimera fs` subcommand group + `compile` verb

**Files:**
- Create: `chimera/cli/fs.py`
- Modify: `chimera/cli/main.py` — register the `fs` subcommand group.
- Create: `tests/cli/test_fs_cli.py`

- [ ] **Step 1: Inspect main.py's existing subcommand pattern**

Run: `uv run python -c "import pathlib; print(pathlib.Path('chimera/cli/main.py').read_text()[:4000])"`

Note how existing subcommands (synthesize, eval, code) are registered. Replicate the pattern exactly — do not restructure main.py.

- [ ] **Step 2: Write the failing test**

```python
# tests/cli/test_fs_cli.py
from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _run(args: list[str], env: dict, cwd: str | None = None):
    return subprocess.run(
        [sys.executable, "-m", "chimera", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_fs_compile_writes_bundle_to_registry(tmp_path, monkeypatch):
    import os
    env = {**os.environ, "CHIMERA_FS_HOME": str(tmp_path), "CHIMERA_FS_OFFLINE": "1"}
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({
        "name": "classify",
        "description": "classify sentiment",
    }))
    result = _run(
        ["fs", "compile", str(spec_file), "--compiler", "mock"],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "classify-" in result.stdout  # slug printed
    assert (tmp_path / "bundles").exists()
    assert list((tmp_path / "bundles").glob("*.chi"))
```

- [ ] **Step 3: Run test to verify failure**

Run: `uv run pytest tests/cli/test_fs_cli.py::test_fs_compile_writes_bundle_to_registry -v`
Expected: non-zero exit (unknown subcommand "fs") or import error.

- [ ] **Step 4: Write `chimera/cli/fs.py`**

```python
# chimera/cli/fs.py
"""`chimera fs` subcommands: compile, run, list, rm, info."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.registry import ProgramRegistry
from chimera.function_synthesis.spec import FunctionSpec


def _load_spec(path: Path) -> FunctionSpec:
    text = path.read_text()
    # accept either raw JSON or the FunctionSpec.to_json() shape
    data = json.loads(text)
    return FunctionSpec(
        name=data["name"],
        description=data["description"],
        examples=data.get("examples", []),
        input_schema=data.get("input_schema"),
        output_schema=data.get("output_schema"),
    )


def _build_compiler(name: str, *, endpoint: str | None, api_key: str | None) -> CompilerBackend:
    if name == "mock":
        return MockCompiler()
    if name == "remote":
        if not endpoint:
            raise SystemExit("--endpoint required with --compiler remote")
        return RemoteCompiler(endpoint=endpoint, api_key=api_key)
    raise SystemExit(f"unknown --compiler {name!r}; expected 'mock' or 'remote'")


def cmd_compile(args: argparse.Namespace) -> int:
    spec = _load_spec(Path(args.spec))
    compiler = _build_compiler(args.compiler, endpoint=args.endpoint, api_key=args.api_key)
    bundle = compiler.compile(spec)
    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    print(slug)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    registry = ProgramRegistry.default()
    entry = registry.resolve(args.slug)
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
    from chimera.function_synthesis.runtime import CompiledFunction
    backend = LlamaCppBackend(base_model_path=args.base_model)
    with CompiledFunction.from_path(entry.bundle_path, backend=backend) as fn:
        print(fn(args.input, max_tokens=args.max_tokens))
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    registry = ProgramRegistry.default()
    for entry in registry.list():
        print(f"{entry.slug}\t{entry.spec.name}\t{entry.spec.description}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    ProgramRegistry.default().remove(args.slug)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    entry = ProgramRegistry.default().resolve(args.slug)
    payload = {
        "slug": entry.slug,
        "bundle_path": str(entry.bundle_path),
        "spec": json.loads(entry.spec.to_json()),
        "metadata": entry.metadata,
    }
    print(json.dumps(payload, indent=2))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    fs = subparsers.add_parser("fs", help="function-synthesis operations")
    fs_sub = fs.add_subparsers(dest="fs_cmd", required=True)

    p_compile = fs_sub.add_parser("compile", help="compile a FunctionSpec into a .chi bundle")
    p_compile.add_argument("spec", help="path to a spec JSON file")
    p_compile.add_argument("--compiler", default="mock", choices=["mock", "remote"])
    p_compile.add_argument("--endpoint", default=None)
    p_compile.add_argument("--api-key", default=None)
    p_compile.set_defaults(func=cmd_compile)

    p_run = fs_sub.add_parser("run", help="invoke an installed program")
    p_run.add_argument("slug")
    p_run.add_argument("input")
    p_run.add_argument("--base-model", required=True, help="path to base GGUF")
    p_run.add_argument("--max-tokens", type=int, default=256)
    p_run.set_defaults(func=cmd_run)

    p_list = fs_sub.add_parser("list", help="list installed programs")
    p_list.set_defaults(func=cmd_list)

    p_rm = fs_sub.add_parser("rm", help="remove an installed program")
    p_rm.add_argument("slug")
    p_rm.set_defaults(func=cmd_rm)

    p_info = fs_sub.add_parser("info", help="show details for a slug")
    p_info.add_argument("slug")
    p_info.set_defaults(func=cmd_info)
```

- [ ] **Step 5: Wire into `main.py`**

Open `chimera/cli/main.py`. Find the section where subcommands are registered (near other `subparsers.add_parser(...)` calls). Add:

```python
from chimera.cli import fs as _fs_cli  # near other CLI module imports

# inside the function that builds subparsers, alongside other register calls:
_fs_cli.register(subparsers)
```

Ensure the top-level dispatch calls `args.func(args)` for subcommands that set a `func` default (the existing pattern does this for most subcommands; if `fs` requires a custom dispatch, wire it to match).

- [ ] **Step 6: Run the test**

Run: `uv run pytest tests/cli/test_fs_cli.py::test_fs_compile_writes_bundle_to_registry -v`
Expected: 1 passed.

If main.py uses a different subcommand style than `args.func(args)`, adapt `register()` accordingly — keep `cmd_*` handlers stable.

- [ ] **Step 7: Commit**

```bash
git add chimera/cli/fs.py chimera/cli/main.py tests/cli/test_fs_cli.py
git commit -m "feat(cli): add 'chimera fs compile' subcommand"
```

---

### Task 8: `chimera fs list`, `rm`, `info` end-to-end tests

**Files:**
- Modify: `tests/cli/test_fs_cli.py` — append tests for the remaining verbs.

- [ ] **Step 1: Append tests**

```python
def test_fs_list_shows_installed(tmp_path):
    import os
    env = {**os.environ, "CHIMERA_FS_HOME": str(tmp_path), "CHIMERA_FS_OFFLINE": "1"}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"name": "a", "description": "x"}))
    _run(["fs", "compile", str(spec), "--compiler", "mock"], env=env)
    result = _run(["fs", "list"], env=env)
    assert result.returncode == 0
    assert "a-" in result.stdout


def test_fs_rm_removes_entry(tmp_path):
    import os
    env = {**os.environ, "CHIMERA_FS_HOME": str(tmp_path), "CHIMERA_FS_OFFLINE": "1"}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"name": "a", "description": "x"}))
    compiled = _run(["fs", "compile", str(spec), "--compiler", "mock"], env=env)
    slug = compiled.stdout.strip()
    _run(["fs", "rm", slug], env=env)
    listed = _run(["fs", "list"], env=env)
    assert slug not in listed.stdout


def test_fs_info_returns_json(tmp_path):
    import os
    env = {**os.environ, "CHIMERA_FS_HOME": str(tmp_path), "CHIMERA_FS_OFFLINE": "1"}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"name": "a", "description": "x"}))
    compiled = _run(["fs", "compile", str(spec), "--compiler", "mock"], env=env)
    slug = compiled.stdout.strip()
    result = _run(["fs", "info", slug], env=env)
    payload = json.loads(result.stdout)
    assert payload["slug"] == slug
    assert payload["spec"]["name"] == "a"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/cli/test_fs_cli.py -v`
Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/cli/test_fs_cli.py
git commit -m "test(cli): cover 'chimera fs list/rm/info' paths"
```

---

## Milestone 4 — Cold-Start Cache (Task 9)

### Task 9: PrefixCache for llama.cpp state

**Files:**
- Create: `chimera/function_synthesis/prefix_cache.py`
- Create: `tests/function_synthesis/test_prefix_cache.py`
- Modify: `chimera/function_synthesis/backends/llama_cpp.py` — use the cache if enabled.

**Note:** The cache is keyed by `(base_model_sha, bundle_slug, system_prompt_sha)`. When present, we call `Llama.load_state(...)` instead of re-prefilling the system prompt. When absent, we prefill and then `Llama.save_state(...)` to disk. All behavior gated by `PrefixCache.enabled` so the existing llama.cpp tests still pass.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/function_synthesis/test_prefix_cache.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `prefix_cache.py`**

```python
# chimera/function_synthesis/prefix_cache.py
"""Disk-backed cache for llama.cpp post-prefill state (cold-start elimination)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrefixCache:
    """Key-value store for serialized backend state.

    Keys are ``sha256(base_model_sha || slug || system_prompt)``.  Values are
    opaque bytes produced by the backend (e.g., llama.cpp ``save_state``
    output).  When :attr:`enabled` is False, ``load`` always returns None and
    ``store`` is a no-op, preserving existing behavior.
    """

    root: Path
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def key(self, *, base_model_sha: str, slug: str, system_prompt: str) -> str:
        h = hashlib.sha256()
        h.update(base_model_sha.encode())
        h.update(b"\x00")
        h.update(slug.encode())
        h.update(b"\x00")
        h.update(system_prompt.encode())
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.state"

    def load(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def store(self, key: str, state: bytes) -> None:
        if not self.enabled:
            return
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_bytes(state)
        os.replace(tmp, self._path(key))
```

- [ ] **Step 4: Wire into `LlamaCppBackend` (additive, opt-in)**

Modify `chimera/function_synthesis/backends/llama_cpp.py`:

- Add optional `prefix_cache: PrefixCache | None = None` parameter to `__init__`.
- In `invoke`, before calling `create_chat_completion`: if `prefix_cache` is present and the backend supports `save_state`/`load_state`, attempt to load state keyed by `(base_model_sha, slug, system_prompt)`. On miss, run inference normally and then save state. On hit, skip the system-prompt prefill by restoring state first.

**Important constraint:** only call `save_state`/`load_state` if `hasattr(self._llm, "save_state")` — keep compatibility with the test fake in `test_llama_cpp_backend.py` (which doesn't implement those methods). Existing tests must continue to pass.

Here's a minimal patch for `invoke`:

```python
def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
    if self._llm is None or self._bundle is None:
        raise RuntimeError("backend not loaded; call load() first")
    prompts = self._bundle.prompts
    system = prompts.get("system", "")
    user_msg = prompts.get("user_template", "{input}").format(input=user_input)

    cache_key: str | None = None
    if (
        self._prefix_cache is not None
        and hasattr(self._llm, "save_state")
        and hasattr(self._llm, "load_state")
    ):
        from hashlib import sha256
        base_sha = sha256(self._base_model_path.read_bytes() if self._base_model_path.exists() else b"").hexdigest()
        slug = self._bundle.metadata.get("slug", self._bundle.spec.name)
        cache_key = self._prefix_cache.key(
            base_model_sha=base_sha, slug=slug, system_prompt=system,
        )
        cached = self._prefix_cache.load(cache_key)
        if cached is not None:
            self._llm.load_state(cached)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    result = self._llm.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        stop=prompts.get("stop") or None,
    )

    if cache_key is not None and self._prefix_cache.load(cache_key) is None:
        try:
            self._prefix_cache.store(cache_key, self._llm.save_state())
        except Exception:  # pragma: no cover — best-effort cache save
            pass

    return result["choices"][0]["message"]["content"]
```

Keep everything else untouched. Existing tests in `test_llama_cpp_backend.py` don't pass a `prefix_cache`, so the new branch is dormant.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/function_synthesis/ -v`
Expected: all 10+ previous tests still pass, plus 5 new PrefixCache tests = ~26 passed.

- [ ] **Step 6: Commit**

```bash
git add chimera/function_synthesis/prefix_cache.py chimera/function_synthesis/backends/llama_cpp.py tests/function_synthesis/test_prefix_cache.py
git commit -m "feat(function_synthesis): add PrefixCache for cold-start elimination"
```

---

## Milestone 5 — E2E + Docs (Tasks 10-13)

### Task 10: Live end-to-end test (opt-in)

**Files:**
- Create: `tests/function_synthesis/test_live_e2e.py`
- Modify: `pyproject.toml` — register the `live` marker.

This test is the proof that the whole system works. It is **not** run by default. It requires a real small GGUF on disk.

- [ ] **Step 1: Register the marker in pyproject.toml**

In the `[tool.pytest.ini_options]` section, under `markers = [...]`, add:

```toml
"live: opt-in tests that hit real models/networks (run with `pytest -m live`)",
```

- [ ] **Step 2: Write the live test**

```python
# tests/function_synthesis/test_live_e2e.py
"""End-to-end live test: compile -> install -> load -> call.

Requires a real base GGUF and is opt-in: ``pytest -m live``.
Set ``CHIMERA_FS_LIVE_BASE_MODEL`` to the path of a chat-tuned GGUF.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry
from chimera.function_synthesis.runtime import CompiledFunction
from chimera.function_synthesis.spec import FunctionSpec

pytestmark = pytest.mark.live


@pytest.fixture
def base_model_path() -> Path:
    path = os.environ.get("CHIMERA_FS_LIVE_BASE_MODEL")
    if not path or not Path(path).exists():
        pytest.skip("set CHIMERA_FS_LIVE_BASE_MODEL to a chat-tuned GGUF")
    return Path(path)


def test_end_to_end_compile_install_invoke(tmp_path, monkeypatch, base_model_path):
    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    spec = FunctionSpec(
        name="greet",
        description="Reply with a short friendly greeting.",
    )
    bundle = MockCompiler().compile(spec)
    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    entry = registry.resolve(slug)

    backend = LlamaCppBackend(base_model_path=base_model_path)
    with CompiledFunction.from_path(entry.bundle_path, backend=backend) as fn:
        out = fn("hi", max_tokens=16)

    assert isinstance(out, str)
    assert len(out.strip()) > 0
```

**Note:** `MockCompiler` produces a non-functional adapter — the real model will ignore it and respond with its own behavior to the system prompt. This is intentional: we're testing the plumbing (compile -> install -> load -> call), not the training pipeline. A follow-up plan will wire in a real compile service for behavior tests.

- [ ] **Step 3: Verify opt-in behavior**

Run: `uv run pytest tests/function_synthesis/test_live_e2e.py`
Expected: 1 skipped (or 1 deselected) because live marker is off by default.

Run: `uv run pytest tests/function_synthesis/test_live_e2e.py -m live`
Expected: 1 skipped because `CHIMERA_FS_LIVE_BASE_MODEL` is unset.

- [ ] **Step 4: Commit**

```bash
git add tests/function_synthesis/test_live_e2e.py pyproject.toml
git commit -m "test(function_synthesis): add opt-in e2e live test"
```

---

### Task 11: Compile-service protocol doc

**Files:**
- Create: `docs/function-synthesis-compile-protocol.md`

- [ ] **Step 1: Write the protocol document**

Create `docs/function-synthesis-compile-protocol.md`:

```markdown
# Function Synthesis Compile Protocol

The `RemoteCompiler` speaks a small HTTP contract. Implement this contract to
host your own compile service (self-hosted fine-tuning pipeline, third-party
adapter-training provider, etc.).

## Request

```
POST <endpoint>
Content-Type: application/json
Authorization: Bearer <api_key>   # optional
Accept: application/zip

{
  "spec": {
    "name": "classify",
    "description": "Classify sentiment as pos/neg.",
    "examples": [{"input": "...", "output": "..."}],
    "input_schema": null,
    "output_schema": null
  }
}
```

## Response

- **Status 200:** body is the raw `.chi` bundle (`application/zip`). See
  [.chi format](./function-synthesis.md#chi-bundle-format) for required
  members.
- **Status 4xx/5xx:** body is treated as an opaque error message and
  surfaced as `CompilerError` on the client.

## Bundle requirements

The service MUST return a valid `.chi` bundle:

- `manifest.json` with `schema_version: 1`, `name`, `description`,
  `base_model`, `adapter_format: "gguf-lora"`, `created_at`, `chimera_version`.
- `adapter.gguf` — LoRA adapter in GGUF format.
- `prompts.json` — `{"system": str, "user_template": str, "stop": list[str]}`.
- `spec.json` — the request `spec` JSON-serialized.
- `metadata.json` — free-form; at minimum SHOULD include
  `compiler_backend`, `base_model_sha256`.

## Reference clients

- `chimera.function_synthesis.compilers.remote.RemoteCompiler`
- `chimera fs compile --compiler remote --endpoint <URL> --api-key <KEY>`

## Reference servers

None bundled. See `chimera/function_synthesis/compilers/mock.py` for a
zero-network stub suitable for development.
```

- [ ] **Step 2: Commit**

```bash
git add docs/function-synthesis-compile-protocol.md
git commit -m "docs(function_synthesis): add compile-service HTTP protocol"
```

---

### Task 12: Quickstart doc + runnable example

**Files:**
- Create: `docs/function-synthesis.md`
- Create: `examples/function_synthesis_quickstart.py`

- [ ] **Step 1: Write the quickstart**

Create `docs/function-synthesis.md`:

```markdown
# Function Synthesis

Compile natural-language specs into callable neural artifacts (`.chi` bundles),
run them locally, and expose them as agent tools.

## Install

```bash
pip install 'chimera[function_synthesis]'
```

Pulls in `llama-cpp-python` (runtime) and `huggingface_hub` (base model cache).

## 60-second quickstart

```python
from chimera.function_synthesis import FunctionSpec
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry

spec = FunctionSpec(
    name="sentiment",
    description="Classify the text as 'positive' or 'negative'.",
)
bundle = MockCompiler().compile(spec)                     # no network, no training
slug = ProgramRegistry.default().install(spec=spec, bundle=bundle)
print(slug)   # -> "sentiment-ab12cd34"
```

## CLI

```bash
# compile a spec file into a bundle + install it
echo '{"name":"sentiment","description":"classify pos/neg"}' > spec.json
chimera fs compile spec.json --compiler mock

# list installed programs
chimera fs list

# invoke one (requires a base GGUF + llama-cpp-python)
chimera fs run sentiment-ab12cd34 "I loved it!" \
    --base-model ~/.chimera/function_synthesis/models/.../model.gguf

# inspect / remove
chimera fs info sentiment-ab12cd34
chimera fs rm sentiment-ab12cd34
```

## Using a real compile service

Point `RemoteCompiler` at any endpoint implementing the
[compile protocol](./function-synthesis-compile-protocol.md):

```python
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.strategies.synthesis import FunctionSynthesisStrategy

compiler = RemoteCompiler(endpoint="https://your-service/compile", api_key="...")
strategy = FunctionSynthesisStrategy(
    compiler=compiler,
    output_dir="./bundles",
)
result = strategy.run(spec)
print(result.bundle_path)
```

## Agent tool

Any loaded `CompiledFunction` can be exposed to a Chimera agent:

```python
from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
from chimera.function_synthesis.runtime import CompiledFunction
from chimera.tools.compiled_function_tool import CompiledFunctionTool

backend = LlamaCppBackend(base_model_path="...")
fn = CompiledFunction.from_path("sentiment-ab12cd34.chi", backend=backend)
tool = CompiledFunctionTool(fn)

# add `tool` to your agent's tool list — agents can now call the compiled
# function just like any other tool.
```

## Cache layout

```
$CHIMERA_FS_HOME/  (default: ~/.chimera/function_synthesis/)
  models/       # base GGUF files, downloaded on first use
  bundles/      # installed .chi bundles (one per slug)
  index.json    # slug -> bundle path + metadata
  prefix/       # cold-start state cache (see PrefixCache)
```

Set `CHIMERA_FS_HOME` to override. Set `CHIMERA_FS_OFFLINE=1` to refuse any
network call — cache misses raise `OfflineError` instead of downloading.

## `.chi` bundle format

A `.chi` file is a ZIP archive containing:

| Member           | Contents                                                    |
|------------------|-------------------------------------------------------------|
| `manifest.json`  | Schema version, name, base model ID, adapter format         |
| `adapter.gguf`   | LoRA adapter (Q4_0 by default)                              |
| `prompts.json`   | `{"system": str, "user_template": str, "stop": [str]}`      |
| `spec.json`      | Serialized `FunctionSpec`                                   |
| `metadata.json`  | Compiler backend info, base model hash, free-form fields    |

See `chimera/function_synthesis/bundle.py` for the loader.
```

- [ ] **Step 2: Write the runnable example**

Create `examples/function_synthesis_quickstart.py`:

```python
"""End-to-end quickstart for chimera.function_synthesis.

Runs fully offline: uses MockCompiler, no network, no GGUF required.
For the real-model path, see docs/function-synthesis.md.
"""
from __future__ import annotations

from chimera.function_synthesis import FunctionSpec
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry


def main() -> None:
    spec = FunctionSpec(
        name="greet",
        description="Reply with a one-sentence friendly greeting.",
    )

    print(f"[1/3] Compiling spec {spec.name!r}...")
    bundle = MockCompiler().compile(spec)
    print(f"      adapter_bytes: {len(bundle.adapter_bytes)} bytes")

    print("[2/3] Installing into local registry...")
    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    print(f"      slug: {slug}")

    print("[3/3] Resolving back from the registry...")
    entry = registry.resolve(slug)
    print(f"      bundle_path: {entry.bundle_path}")
    print(f"      spec.description: {entry.spec.description}")

    print(
        "\nDone. To invoke this program against a real base model, see "
        "`chimera fs run --base-model ...` or docs/function-synthesis.md."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-run the example**

Run: `uv run python examples/function_synthesis_quickstart.py`
Expected: three step lines and "Done." — no exceptions.

- [ ] **Step 4: Commit**

```bash
git add docs/function-synthesis.md examples/function_synthesis_quickstart.py
git commit -m "docs(function_synthesis): add quickstart + runnable example"
```

---

### Task 13: Full regression + lint + type check

**Files:** none (verification).

- [ ] **Step 1: Run the full suite minus live**

Run: `uv run pytest --ignore=tests/benchmarks`
Expected: all previously passing tests still pass; ~26 new tests added by this plan pass; the 7 pre-existing hook-subprocess failures remain (they are not regressions from this plan).

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check chimera/function_synthesis/ chimera/cli/fs.py tests/function_synthesis/ tests/cli/test_fs_cli.py examples/function_synthesis_quickstart.py`
Expected: no issues.

- [ ] **Step 3: Run mypy**

Run: `uv run mypy chimera/function_synthesis/ chimera/cli/fs.py`
Expected: no errors. Add `# type: ignore[import-not-found]` for optional imports (`huggingface_hub`, `llama_cpp`) if needed — keep the pattern used in existing backends.

- [ ] **Step 4: Record final counts**

Run: `uv run pytest --collect-only -q | tail -5`
Record the total test count in the commit body below.

- [ ] **Step 5: Commit (only if lint/mypy required fixups)**

```bash
git add -u chimera/function_synthesis/ chimera/cli/fs.py
git commit -m "chore(function_synthesis): lint/type fixups for v2"
```

---

## Deferred (follow-ups beyond this plan)

- **Reference compile service** — a bundled stub server that fine-tunes a LoRA adapter and returns a `.chi`. Needs training infrastructure, not in scope here.
- **Remote hub / program sharing** — a way to publish/download slugs from a shared registry. Local registry first; remote second.
- **Streaming invoke** — `CompiledFunction.stream(...)` yielding token-by-token output from llama.cpp.
- **Multi-adapter merge** — compose multiple `.chi` bundles into one callable (requires adapter-weight arithmetic).
- **Browser runtime** — a JS/WASM loader for `.chi` bundles. Requires a different runtime backend entirely.
- **Structured input/output validation** — enforce `input_schema` / `output_schema` at invoke time.

---

## Self-Review Notes

- **Spec coverage:** every gap from the v2 scoping conversation is a task here. Model cache (T3), bundle cache (T4), offline mode (T2+T3), slug registry (T5), MockCompiler (T6), CLI (T7+T8), cold-start cache (T9), e2e test (T10), compile-service contract (T11), quickstart + example (T12), regression (T13). No gaps unclaimed.
- **Placeholders:** none. Every step has exact file paths, exact code blocks, exact commands.
- **Type consistency:** `CacheDirs`, `BaseModelCache`, `BundleCache`, `ProgramRegistry`, `ProgramEntry`, `slug_for`, `MockCompiler`, `PrefixCache`, `OfflineError`, `CacheMissError` — all used consistently across tasks.
- **Additive only:** touches two existing files (`chimera/function_synthesis/__init__.py`, `chimera/cli/main.py`, `chimera/function_synthesis/backends/llama_cpp.py`) with additive changes only. No existing behavior changed; all existing tests unaffected.
- **Optional-deps rule:** new runtime deps (`huggingface_hub`) ride the existing `function_synthesis` extra. Zero new core dependencies.
