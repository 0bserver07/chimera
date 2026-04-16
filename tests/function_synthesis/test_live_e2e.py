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
