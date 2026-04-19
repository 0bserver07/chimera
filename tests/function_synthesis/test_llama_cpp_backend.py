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
        def __init__(self, *, model_path, lora_path=None, n_ctx=None, **kwargs):
            captured["model_path"] = model_path
            captured["lora_path"] = lora_path
            captured["n_ctx"] = n_ctx
            captured["init_kwargs"] = dict(kwargs)

        def create_chat_completion(self, messages, max_tokens, stop=None):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            captured["stop"] = stop
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

    from pathlib import Path

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base_path)
    backend.load(_bundle())
    out = backend.invoke("hello")

    assert out == "RESULT"
    assert captured["model_path"] == str(base_path)
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][0]["content"] == "sys"
    assert captured["messages"][1]["content"] == "U:hello"

    # Critical: the adapter bytes must be materialized to disk with the
    # correct content. A mock that only asserts "out == 'RESULT'" would
    # miss a backend that skipped writing the adapter (see the
    # empty-adapter-hangs-llama bug fixed in the commit adding
    # `test_llama_cpp_backend_empty_adapter_bytes_skips_lora`).
    assert captured["lora_path"] is not None, "lora_path must be set for non-empty adapter"
    lora_file = Path(captured["lora_path"])
    assert lora_file.exists(), f"lora tempfile missing: {lora_file}"
    assert lora_file.read_bytes() == b"ADAPTER", (
        f"lora tempfile content mismatch: got {lora_file.read_bytes()!r}"
    )

    # n_ctx must be forwarded (default 2048). Stop list must be passed.
    assert captured["n_ctx"] == 2048
    # bundle's stop is an empty list, so `stop=` is converted to None
    # (prompts.get("stop") or None → None).
    assert captured["stop"] is None
    assert captured["max_tokens"] == 256
    backend.close()


def test_llama_cpp_backend_forwards_n_ctx_and_n_threads(monkeypatch, tmp_path):
    """Non-default backend args must reach the Llama constructor."""
    captured: dict = {}
    _install_fake_llama_cpp(monkeypatch, captured)
    base_path = tmp_path / "base.gguf"
    base_path.write_bytes(b"BASE")

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=base_path, n_ctx=4096, n_threads=8)
    backend.load(_bundle())
    assert captured["n_ctx"] == 4096
    assert captured["init_kwargs"].get("n_threads") == 8
    backend.close()


def test_llama_cpp_backend_forwards_stop_sequences(monkeypatch, tmp_path):
    """Non-empty stop list in prompts must reach create_chat_completion."""
    captured: dict = {}
    _install_fake_llama_cpp(monkeypatch, captured)
    base_path = tmp_path / "base.gguf"
    base_path.write_bytes(b"BASE")

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    bundle = ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"ADAPTER",
        prompts={"system": "sys", "user_template": "{input}", "stop": ["\n\n", "</s>"]},
    )
    backend = LlamaCppBackend(base_model_path=base_path)
    backend.load(bundle)
    backend.invoke("hi", max_tokens=42)
    assert captured["stop"] == ["\n\n", "</s>"]
    assert captured["max_tokens"] == 42
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
