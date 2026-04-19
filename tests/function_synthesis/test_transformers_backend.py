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
    loaded_base_model: _FakeCausalLM | None = None
    # Full ``(adapter_dir, {"adapter_config.json": b"...", ...})`` snapshots
    # taken at the moment ``from_pretrained`` is invoked. Lets tests assert
    # that the adapter files existed on disk *at the call site*, not just
    # that the dir still existed at test-time.
    calls: list[tuple[str, dict[str, bytes]]] = []

    @classmethod
    def from_pretrained(cls, base_model: _FakeCausalLM, adapter_dir: str) -> _FakePeftModel:
        instance = cls()
        instance.base_model = base_model
        cls.loaded_adapter_dir = adapter_dir
        cls.loaded_base_model = base_model
        adapter_path = Path(adapter_dir)
        snapshot: dict[str, bytes] = {}
        if adapter_path.is_dir():
            for child in sorted(adapter_path.rglob("*")):
                if child.is_file():
                    snapshot[str(child.relative_to(adapter_path))] = child.read_bytes()
        cls.calls.append((adapter_dir, snapshot))
        return instance


class _FakeAutoModel:
    last_kwargs: dict[str, Any] = {}
    last_positional: tuple[Any, ...] = ()
    # Each entry: (args, kwargs). Lets tests assert the full call signature
    # including the first positional arg (base model name).
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    @classmethod
    def from_pretrained(cls, *args: Any, **kwargs: Any) -> _FakeCausalLM:
        cls.last_positional = args
        cls.last_kwargs = {"name": args[0] if args else None, **kwargs}
        cls.calls.append((args, dict(kwargs)))
        return _FakeCausalLM()


class _FakeAutoTokenizer:
    last_name: str = ""

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


def _reset_fake_state() -> None:
    """Reset class-level state on the fakes between tests."""
    _FakeAutoModel.last_kwargs = {}
    _FakeAutoModel.last_positional = ()
    _FakeAutoModel.calls = []
    _FakePeftModel.loaded_adapter_dir = ""
    _FakePeftModel.loaded_base_model = None
    _FakePeftModel.calls = []
    _FakeAutoTokenizer.last_name = ""


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_fake_state()
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
        adapter_peft_files={
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

    # AutoModelForCausalLM.from_pretrained must receive the base model id
    # as its *first positional* arg (transformers signature), not as a
    # ``name=`` kwarg.
    assert len(_FakeAutoModel.calls) == 1
    args, kwargs = _FakeAutoModel.calls[0]
    assert args[0] == "my-org/base-model"
    assert _FakeAutoModel.last_kwargs["name"] == "my-org/base-model"
    # No dtype was specified → torch_dtype must NOT be forwarded. This
    # catches regressions that always-pass a default dtype.
    assert "torch_dtype" not in kwargs

    adapter_dir = Path(_FakePeftModel.loaded_adapter_dir)
    assert adapter_dir.is_dir()
    assert (adapter_dir / "adapter_config.json").read_bytes() == b'{"peft_type": "LORA"}'
    assert (adapter_dir / "adapter_model.safetensors").read_bytes() == b"\x00\x01\x02WEIGHTS"

    # Critical: PeftModel.from_pretrained must see the adapter files
    # on disk at the moment it is called — a backend that skipped
    # _extract_peft_adapter would leave ``calls[0][1]`` empty even if
    # the dir existed later.
    assert len(_FakePeftModel.calls) == 1
    captured_dir, snapshot = _FakePeftModel.calls[0]
    assert captured_dir == str(adapter_dir)
    assert snapshot == {
        "adapter_config.json": b'{"peft_type": "LORA"}',
        "adapter_model.safetensors": b"\x00\x01\x02WEIGHTS",
    }
    # ...and the base_model passed to peft must be the exact object
    # returned by AutoModelForCausalLM.from_pretrained (not None / a
    # fresh instance).
    assert _FakePeftModel.loaded_base_model is not None
    assert isinstance(_FakePeftModel.loaded_base_model, _FakeCausalLM)

    backend.close()
    # close() should remove the extracted adapter dir.
    assert not adapter_dir.exists()


def test_invoke_builds_messages_and_decodes(monkeypatch):
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    backend.load(_peft_bundle())

    # Even though invoke() returns the mock-decoded string, we must also
    # assert the adapter files landed on disk — a backend that skipped
    # _extract_peft_adapter entirely would still return "HELLO_WORLD".
    adapter_dir = Path(_FakePeftModel.loaded_adapter_dir)
    assert adapter_dir.is_dir()
    assert (adapter_dir / "adapter_config.json").read_bytes() == b'{"peft_type": "LORA"}'
    assert (adapter_dir / "adapter_model.safetensors").read_bytes() == b"\x00\x01\x02WEIGHTS"

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
    # input_ids and attention_mask must be forwarded (not dropped).
    assert "input_ids" in gen_kwargs
    assert "attention_mask" in gen_kwargs
    # pad_token_id must be forwarded (never None). Backend uses
    # `pad_token_id or eos_token_id`, so with pad_token_id==0 (falsy) it
    # falls back to eos_token_id==2. Either way: must be non-None.
    assert gen_kwargs["pad_token_id"] == 2
    assert gen_kwargs["pad_token_id"] is not None
    # No streamer on the one-shot invoke path.
    assert "streamer" not in gen_kwargs

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
    gen_kwargs = model.generate_calls[-1]
    assert "streamer" in gen_kwargs
    # The streamer must be a TextIteratorStreamer bound to *our* tokenizer —
    # otherwise the stream would decode with a stray tokenizer.
    streamer = gen_kwargs["streamer"]
    assert isinstance(streamer, _FakeStreamer)
    assert streamer._tokenizer is backend._tokenizer
    # max_new_tokens must also be forwarded on the streaming path.
    assert gen_kwargs["max_new_tokens"] == 8
    assert gen_kwargs["do_sample"] is False


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


def test_load_forwards_dtype_to_auto_model(monkeypatch):
    """``dtype=`` on the backend must reach ``AutoModelForCausalLM.from_pretrained``.

    A silent drop of the dtype would pass every other test (the fake
    still returns a working model) but would make the user get fp32 at
    runtime instead of the dtype they asked for.
    """
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    sentinel_dtype = "bfloat16"  # backend is dtype-agnostic; forwards as-is.
    backend = TransformersBackend("my-org/base-model", dtype=sentinel_dtype)
    backend.load(_peft_bundle())

    assert len(_FakeAutoModel.calls) == 1
    _, kwargs = _FakeAutoModel.calls[0]
    assert kwargs.get("torch_dtype") == sentinel_dtype
    backend.close()


def test_load_adapter_files_land_on_disk_with_correct_bytes(monkeypatch):
    """Regression: each PEFT file in the bundle must land on disk verbatim.

    Equivalent to the llama_cpp lora-on-disk assertion in commit eff239b.
    Uses 3 small files with distinct byte payloads so a backend that
    accidentally concatenated / truncated / dropped bytes would fail.
    """
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    payloads = {
        "adapter_config.json": b'{"peft_type":"LORA","r":8}',
        "adapter_model.safetensors": b"\x00\x01\x02\x03WEIGHTS\xff\xfe",
        "README.md": b"# fake adapter\n",
    }
    bundle = ChiBundle(
        spec=FunctionSpec(name="echo", description="echo"),
        adapter_bytes=b"",
        prompts={"system": "sys", "user_template": "{input}", "stop": []},
        adapter_format=ADAPTER_FORMAT_PEFT,
        adapter_peft_files=payloads,
    )
    backend = TransformersBackend("my-org/base-model")
    backend.load(bundle)

    adapter_dir = Path(_FakePeftModel.loaded_adapter_dir)
    assert adapter_dir.is_dir()
    for rel, expected in payloads.items():
        on_disk = (adapter_dir / rel).read_bytes()
        assert on_disk == expected, (
            f"bytes for {rel} on disk {on_disk!r} != bundle {expected!r}"
        )

    # Snapshot captured at the peft.from_pretrained call site must match
    # byte-for-byte: this catches a backend that wrote the files AFTER
    # handing the directory to peft.
    _captured_dir, snapshot = _FakePeftModel.calls[-1]
    assert snapshot == payloads

    backend.close()
    assert not adapter_dir.exists()


def test_invoke_forwards_max_new_tokens_verbatim(monkeypatch):
    """``max_tokens`` on invoke() must forward as ``max_new_tokens``."""
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    backend.load(_peft_bundle())
    backend.invoke("hi", max_tokens=1)
    backend.invoke("hi", max_tokens=512)

    calls = backend._model.generate_calls
    assert len(calls) == 2
    assert calls[0]["max_new_tokens"] == 1
    assert calls[1]["max_new_tokens"] == 512
    backend.close()


def test_stream_creates_streamer_with_correct_kwargs(monkeypatch):
    """TextIteratorStreamer must be created with skip_prompt + skip_special_tokens."""
    _install_fakes(monkeypatch)
    from chimera.function_synthesis.backends.transformers import TransformersBackend

    backend = TransformersBackend("my-org/base-model")
    backend.load(_peft_bundle())

    # Drain the iterator so the generate thread completes.
    chunks = list(backend.stream("hi", max_tokens=4))
    assert chunks  # non-empty stream

    streamer = backend._model.generate_calls[-1]["streamer"]
    # The streamer's __init__ asserts skip_prompt and skip_special_tokens
    # are True (see the _FakeStreamer class), so reaching this line at
    # all confirms those flags. Double-check it is actually our fake:
    assert isinstance(streamer, _FakeStreamer)
    backend.close()
