"""End-to-end live test: compile -> install -> load -> call.

Requires a real base GGUF and is opt-in: ``pytest -m live``.
Set ``CHIMERA_FS_LIVE_BASE_MODEL`` to the path of a chat-tuned GGUF.

The second test chains :class:`LocalCompiler` (real PEFT fine-tune) ->
:class:`ProgramRegistry` install -> :class:`TransformersBackend` load ->
invoke().  It requires three env vars and skips cleanly if any are missing::

    CHIMERA_FS_LIVE_COMPILER_MODEL=Qwen/Qwen2-0.5B
    CHIMERA_FS_LIVE_TRANSFORMERS_MODEL=Qwen/Qwen2-0.5B
"""
from __future__ import annotations

import importlib.util
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


def _require_transformers_stack() -> None:
    for mod in ("transformers", "peft", "torch", "datasets"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} not installed; skipping live e2e transformers chain")


def test_end_to_end_local_compile_then_transformers_invoke(tmp_path, monkeypatch):
    """Full chain: LocalCompiler -> registry install -> TransformersBackend.

    Chains the three live suites: compile a PEFT bundle, install it via
    :class:`ProgramRegistry`, load it through :class:`TransformersBackend`,
    and invoke it once.  Skips cleanly when any of the following is missing:

    * ``CHIMERA_FS_LIVE_COMPILER_MODEL`` (compiler base model)
    * ``CHIMERA_FS_LIVE_TRANSFORMERS_MODEL`` (runtime base model)
    * the transformers/peft/torch/datasets deps
    """
    compiler_model = os.environ.get("CHIMERA_FS_LIVE_COMPILER_MODEL")
    runtime_model = os.environ.get("CHIMERA_FS_LIVE_TRANSFORMERS_MODEL")
    if not compiler_model:
        pytest.skip("set CHIMERA_FS_LIVE_COMPILER_MODEL for the full e2e chain")
    if not runtime_model:
        pytest.skip("set CHIMERA_FS_LIVE_TRANSFORMERS_MODEL for the full e2e chain")
    _require_transformers_stack()

    from chimera.function_synthesis.backends.transformers import TransformersBackend
    from chimera.function_synthesis.compilers.local import LocalCompiler

    monkeypatch.setenv("CHIMERA_FS_HOME", str(tmp_path))
    spec = FunctionSpec(
        name="upper-echo",
        description="Uppercase the input.",
        examples=[
            {"input": "hello", "output": "HELLO"},
            {"input": "chimera", "output": "CHIMERA"},
            {"input": "live", "output": "LIVE"},
        ],
    )
    compiler = LocalCompiler(
        compiler_model,
        num_train_epochs=1,
        lora_r=2,
        lora_alpha=4,
        output_dir=tmp_path / "train",
    )
    bundle = compiler.compile(spec)

    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    entry = registry.resolve(slug)

    backend = TransformersBackend(runtime_model, device="cpu")
    with CompiledFunction.from_path(entry.bundle_path, backend=backend) as fn:
        out = fn("hello", max_tokens=8)

    assert isinstance(out, str)
