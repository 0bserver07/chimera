"""Tests for the optional ``stream()`` method on :class:`RuntimeBackend`.

These tests verify the default behavior defined on the ABC and the
delegation from :class:`CompiledFunction.stream` to the backend, without
depending on any real inference backend.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec


class _InvokeOnlyBackend(RuntimeBackend):
    """Backend that implements only the mandatory ABC methods."""

    def __init__(self) -> None:
        self.loaded: ChiBundle | None = None

    def load(self, bundle: ChiBundle) -> None:
        self.loaded = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        return f"one-shot:{user_input}"

    def close(self) -> None:
        self.loaded = None


class _StreamingBackend(RuntimeBackend):
    """Backend that overrides :meth:`stream` with a fake chunked output."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.calls: list[tuple[str, int]] = []

    def load(self, bundle: ChiBundle) -> None:
        self.loaded = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        return "".join(self._chunks)

    def stream(self, user_input: str, *, max_tokens: int = 256) -> Iterator[str]:
        self.calls.append((user_input, max_tokens))
        yield from self._chunks

    def close(self) -> None:
        pass


def _bundle_path(tmp_path: Path) -> Path:
    ChiBundle(
        spec=FunctionSpec(name="echo", description="echoes input"),
        adapter_bytes=b"FAKE",
        prompts={"system": "sys", "user_template": "{input}", "stop": []},
    ).save(tmp_path / "echo.chi")
    return tmp_path / "echo.chi"


def test_stream_default_raises_not_implemented(tmp_path):
    backend = _InvokeOnlyBackend()
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=backend)
    with pytest.raises(NotImplementedError, match="does not implement stream"):
        # Consume the iterator — exceptions from stream() may be raised on
        # call or on first ``next()`` depending on generator semantics;
        # wrapping in list() covers both.
        list(fn.stream("hello"))


def test_stream_default_error_mentions_backend_classname(tmp_path):
    backend = _InvokeOnlyBackend()
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=backend)
    with pytest.raises(NotImplementedError, match="_InvokeOnlyBackend"):
        list(fn.stream("hello"))


def test_streaming_backend_yields_chunks(tmp_path):
    backend = _StreamingBackend(chunks=["hello", " ", "world"])
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=backend)
    assert list(fn.stream("hi", max_tokens=7)) == ["hello", " ", "world"]
    assert backend.calls == [("hi", 7)]


def test_streaming_backend_multiple_chunks(tmp_path):
    backend = _StreamingBackend(chunks=["a", "b", "c", "d"])
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=backend)
    out = list(fn.stream("x"))
    assert len(out) == 4
    assert "".join(out) == "abcd"


def test_invoke_unaffected_by_streaming_override(tmp_path):
    backend = _StreamingBackend(chunks=["hi"])
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=backend)
    assert fn("anything") == "hi"


def test_llama_cpp_backend_stream_yields_deltas(monkeypatch, tmp_path):
    """LlamaCppBackend.stream() should yield non-empty delta contents.

    Uses a fake ``llama_cpp`` module so no real model is needed.
    """
    import sys
    import types

    captured: dict = {}
    fake_module = types.ModuleType("llama_cpp")

    class FakeLlama:
        def __init__(self, *, model_path, lora_path, **kwargs):
            captured["model_path"] = model_path
            captured["lora_path"] = lora_path

        def create_chat_completion(self, messages, max_tokens, stop=None, stream=False):
            captured["stream"] = stream
            captured["messages"] = messages
            if stream:
                yield {"choices": [{"delta": {"content": "hel"}}]}
                yield {"choices": [{"delta": {"content": "lo"}}]}
                yield {"choices": [{"delta": {}}]}  # empty delta, should be skipped
                yield {"choices": [{"delta": {"content": "!"}}]}
                return
            return {"choices": [{"message": {"content": "hello!"}}]}

    fake_module.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)

    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    base_path = tmp_path / "base.gguf"
    base_path.write_bytes(b"BASE")
    bundle = ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"ADAPTER",
        prompts={"system": "sys", "user_template": "U:{input}", "stop": []},
    )
    backend = LlamaCppBackend(base_model_path=base_path)
    backend.load(bundle)
    out = list(backend.stream("hi", max_tokens=8))
    assert out == ["hel", "lo", "!"]
    assert captured["stream"] is True
    assert captured["messages"][1]["content"] == "U:hi"
    backend.close()


def test_llama_cpp_backend_stream_raises_when_not_loaded(tmp_path):
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend

    backend = LlamaCppBackend(base_model_path=tmp_path / "missing.gguf")
    with pytest.raises(RuntimeError, match="not loaded"):
        list(backend.stream("hi"))
