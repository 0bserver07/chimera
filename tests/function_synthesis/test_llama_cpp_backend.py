# tests/function_synthesis/test_llama_cpp_backend.py
from __future__ import annotations

import sys
import types

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.spec import FunctionSpec


def _install_fake_llama_cpp(monkeypatch, captured: dict):
    fake_module = types.ModuleType("llama_cpp")

    class FakeLlama:
        def __init__(self, *, model_path, lora_path=None, **kwargs):
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


def test_llama_cpp_backend_empty_adapter_bytes_skips_lora(monkeypatch, tmp_path):
    """Regression: zero-byte adapter must NOT be written/passed as lora_path.

    llama.cpp treats lora_path as a real GGUF; an empty file fails with
    `failed to read magic`.  Base-only bundles (no LoRA) should just skip
    the lora_path entirely.
    """
    captured: dict = {}
    _install_fake_llama_cpp(monkeypatch, captured)

    base_path = tmp_path / "base.gguf"
    base_path.write_bytes(b"BASE")

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    bundle = ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"",  # empty — no adapter
        prompts={"system": "sys", "user_template": "{input}", "stop": []},
    )
    backend = LlamaCppBackend(base_model_path=base_path)
    backend.load(bundle)

    assert captured["lora_path"] is None, (
        "lora_path must be None (not a path to an empty file) when "
        "adapter_bytes is empty"
    )
    backend.close()


def test_llama_cpp_backend_missing_dep_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=tmp_path / "base.gguf")
    with pytest.raises(ImportError, match="llama-cpp-python"):
        backend.load(_bundle())
