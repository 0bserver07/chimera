"""Tests for :class:`TransformersBackend`.

These tests stub out ``transformers`` and ``peft`` so we never download a
real model. They verify:

* Optional-dep ``ImportError`` is surfaced with a friendly message.
* A ``gguf-lora`` bundle raises :class:`NotImplementedError` with a hint.
* A PEFT bundle flows through ``AutoModelForCausalLM.from_pretrained`` +
  ``PeftModel.from_pretrained``, and the adapter files land on disk.
* :meth:`invoke` feeds the formatted chat messages into the tokenizer
  and decodes the generated completion.
* :meth:`stream` yields multiple non-empty text chunks.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_PEFT,
    ChiBundle,
)
from chimera.function_synthesis.spec import FunctionSpec


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeEncoding(dict):
    """Dict-like object mimicking a ``BatchEncoding``.

    ``TransformersBackend`` calls ``.to(device)`` on the encoding and indexes
    into it; the dict + no-op ``to`` covers both code paths.
    """

    def to(self, _device: Any) -> _FakeEncoding:
        return self


class _FakeTensor:
    """Minimal stand-in for a 2-D LongTensor of token ids."""

    def __init__(self, ids: list[list[int]]) -> None:
        self._ids = ids

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self._ids), len(self._ids[0]) if self._ids else 0)

    def __getitem__(self, idx: int) -> list[int]:
        return self._ids[idx]


class _FakeTokenizer:
    pad_token_id = 0
    eos_token_id = 2

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.decoded: list[list[int]] = []
        self.template_messages: list[list[dict[str, str]]] | None = None

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        add_generation_prompt: bool,
        tokenize: bool,
    ) -> str:
        assert add_generation_prompt is True
        assert tokenize is False
        self.template_messages = messages
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    def __call__(self, rendered: str, *, return_tensors: str) -> _FakeEncoding:
        assert return_tensors == "pt"
        self.calls.append(rendered)
        # 5 prompt tokens; generate() will append new ones.
        return _FakeEncoding(
            input_ids=_FakeTensor([[10, 11, 12, 13, 14]]),
            attention_mask=_FakeTensor([[1, 1, 1, 1, 1]]),
        )

    def decode(self, ids: list[int], *, skip_special_tokens: bool) -> str:
        assert skip_special_tokens is True
        self.decoded.append(list(ids))
        return "HELLO_WORLD"


class _FakeCausalLM:
    device = "cpu"

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, Any]] = []
        self.to_calls: list[str] = []
        self.eval_called = False

    def to(self, device: str) -> _FakeCausalLM:
        self.to_calls.append(device)
        return self

    def eval(self) -> _FakeCausalLM:
        self.eval_called = True
        return self

    def generate(self, **kwargs: Any) -> _FakeTensor:
        self.generate_calls.append(kwargs)
        streamer = kwargs.get("streamer")
        if streamer is not None:
            # Streaming path: push chunks through the streamer and finish.
            streamer.put(["chunk-1 "])
            streamer.put(["chunk-2 "])
            streamer.put(["chunk-3"])
            streamer.end()
        # Return prompt_ids + 3 new tokens.
        return _FakeTensor([[10, 11, 12, 13, 14, 20, 21, 22]])


class _FakePeftModel(_FakeCausalLM):
    loaded_adapter_dir: str = ""

    @classmethod
    def from_pretrained(cls, base_model: _FakeCausalLM, adapter_dir: str) -> _FakePeftModel:
        instance = cls()
        instance.base_model = base_model
        cls.loaded_adapter_dir = adapter_dir
        return instance


class _FakeAutoModel:
    last_kwargs: dict[str, Any] = {}

    @classmethod
    def from_pretrained(cls, name_or_path: str, **kwargs: Any) -> _FakeCausalLM:
        cls.last_kwargs = {"name": name_or_path, **kwargs}
        return _FakeCausalLM()


class _FakeAutoTokenizer:
    @classmethod
    def from_pretrained(cls, name_or_path: str) -> _FakeTokenizer:
        cls.last_name = name_or_path
        return _FakeTokenizer()


class _FakeStreamer:
    """Minimal stand-in for ``transformers.TextIteratorStreamer``.

    Holds chunks in an internal list; :meth:`put` appends them and
    :meth:`end` closes the iterator. Iterating yields chunks in order.
    """

    def __init__(self, tokenizer: Any, *, skip_prompt: bool, skip_special_tokens: bool) -> None:
        assert skip_prompt is True
        assert skip_special_tokens is True
        self._tokenizer = tokenizer
        self._queue: list[str] = []
        self._closed = False

    def put(self, chunks: list[str]) -> None:
        self._queue.extend(chunks)

    def end(self) -> None:
        self._closed = True

    def __iter__(self) -> _FakeStreamer:
        return self

    def __next__(self) -> str:
        if self._queue:
            return self._queue.pop(0)
        if self._closed:
            raise StopIteration
        raise StopIteration


class _FakeTorch(types.SimpleNamespace):
    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    cuda = _Cuda()


# ---------------------------------------------------------------------------
# fake module installation
# ---------------------------------------------------------------------------


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoModelForCausalLM = _FakeAutoModel
    transformers_mod.AutoTokenizer = _FakeAutoTokenizer
    transformers_mod.TextIteratorStreamer = _FakeStreamer

    peft_mod = types.ModuleType("peft")
    peft_mod.PeftModel = _FakePeftModel

    torch_mod = _FakeTorch()

    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)
    monkeypatch.setitem(sys.modules, "peft", peft_mod)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def _peft_bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echoes input"),
        adapter_bytes=b"",
        prompts={
            "system": "You are helpful.",
            "user_template": "Q: {input}",
            "stop": [],
        },
        adapter_format=ADAPTER_FORMAT_PEFT,
        peft_files={
            "adapter_config.json": b'{"peft_type": "LORA"}',
            "adapter_model.safetensors": b"\x00\x01\x02WEIGHTS",
        },
    )


def _gguf_bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echoes input"),
        adapter_bytes=b"GGUF_BYTES",
        prompts={"system": "", "user_template": "{input}", "stop": []},
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_missing_dep_raises_friendly_importerror(monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "peft", None)
    monkeypatch.setitem(sys.modules, "torch", None)

    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("unused/model")
    with pytest.raises(ImportError, match="function_synthesis_transformers"):
        backend.load(_peft_bundle())


def test_gguf_bundle_raises_helpful_not_implemented(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("unused/model")
    with pytest.raises(NotImplementedError, match="LlamaCppBackend"):
        backend.load(_gguf_bundle())


def test_load_extracts_peft_adapter_and_wraps_model(monkeypatch, tmp_path):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    backend.load(_peft_bundle())

    assert _FakeAutoModel.last_kwargs["name"] == "my-org/base-model"
    adapter_dir = Path(_FakePeftModel.loaded_adapter_dir)
    assert adapter_dir.is_dir()
    assert (adapter_dir / "adapter_config.json").read_bytes() == b'{"peft_type": "LORA"}'
    assert (adapter_dir / "adapter_model.safetensors").read_bytes() == b"\x00\x01\x02WEIGHTS"

    backend.close()
    # close() should remove the extracted adapter dir.
    assert not adapter_dir.exists()


def test_invoke_builds_messages_and_decodes(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    backend.load(_peft_bundle())

    out = backend.invoke("hello?", max_tokens=16)
    assert out == "HELLO_WORLD"

    tokenizer = backend._tokenizer
    assert tokenizer.template_messages is not None
    assert tokenizer.template_messages[0] == {"role": "system", "content": "You are helpful."}
    assert tokenizer.template_messages[1] == {"role": "user", "content": "Q: hello?"}

    model = backend._model
    assert len(model.generate_calls) == 1
    gen_kwargs = model.generate_calls[0]
    assert gen_kwargs["max_new_tokens"] == 16
    assert gen_kwargs["do_sample"] is False

    # Only the *new* tokens should be decoded, not the prompt tokens.
    assert tokenizer.decoded == [[20, 21, 22]]


def test_stream_yields_multiple_chunks(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    backend.load(_peft_bundle())

    chunks = list(backend.stream("hello?", max_tokens=8))
    assert chunks == ["chunk-1 ", "chunk-2 ", "chunk-3"]
    assert len(chunks) > 1

    # streamer was attached to the generate call.
    model = backend._model
    assert "streamer" in model.generate_calls[-1]


def test_invoke_without_load_raises(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    with pytest.raises(RuntimeError, match="not loaded"):
        backend.invoke("hi")


def test_stream_without_load_raises(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    with pytest.raises(RuntimeError, match="not loaded"):
        list(backend.stream("hi"))


def test_unknown_adapter_format_raises(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    bundle = _peft_bundle()
    # Force-override the adapter_format bypassing __post_init__ validation
    # so we can exercise the runtime check in load().
    object.__setattr__(bundle, "adapter_format", "mystery-format")
    backend = TransformersBackend("my-org/base-model")
    with pytest.raises(ValueError, match="unknown adapter_format"):
        backend.load(bundle)
