"""Tests for :class:`OnnxBackend`.

These tests stub out ``optimum.onnxruntime``, ``transformers``, and
``peft`` so we never download a real model or touch real ONNX Runtime.

They verify:

* The module imports cleanly with no optional deps installed, and
  :meth:`OnnxBackend.load` raises a friendly :class:`ImportError` when
  any of :mod:`optimum`, :mod:`onnxruntime`, or :mod:`transformers` is
  missing.
* A ``gguf-lora`` bundle raises :class:`NotImplementedError` pointing at
  :class:`LlamaCppBackend`.
* ``load(onnx_bundle)`` extracts ``adapter_onnx_files`` to disk and
  passes the directory to ``ORTModelForCausalLM.from_pretrained``.
* ``load(peft_bundle)`` flows through ``merge_and_unload`` → export →
  ``ORTModelForCausalLM.from_pretrained(..., export=True)``.
* :meth:`invoke` feeds rendered chat messages into the tokenizer and
  decodes the generated completion.
* :meth:`stream` iterates non-empty streamer chunks.
* :meth:`close` clears session refs and removes extracted temp dirs.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_ONNX,
    ADAPTER_FORMAT_PEFT,
    ChiBundle,
)
from chimera.function_synthesis.spec import FunctionSpec


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeEncoding(dict):
    def to(self, _device: Any) -> _FakeEncoding:  # pragma: no cover - unused
        return self


class _FakeTensor:
    def __init__(self, ids: list[list[int]]) -> None:
        self._ids = ids

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self._ids), len(self._ids[0]) if self._ids else 0)

    def __getitem__(self, idx: int) -> list[int]:
        return self._ids[idx]


class _FakeTokenizer:
    pad_token_id: int | None = 0
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
        return _FakeEncoding(
            input_ids=_FakeTensor([[10, 11, 12, 13, 14]]),
            attention_mask=_FakeTensor([[1, 1, 1, 1, 1]]),
        )

    def decode(self, ids: list[int], *, skip_special_tokens: bool) -> str:
        assert skip_special_tokens is True
        self.decoded.append(list(ids))
        return "HELLO_ONNX"

    def save_pretrained(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "tokenizer.json").write_bytes(b'{"saved": true}')


class _FakeAutoTokenizer:
    loaded_from: list[str] = []

    @classmethod
    def from_pretrained(cls, name_or_path: str) -> _FakeTokenizer:
        cls.loaded_from.append(name_or_path)
        return _FakeTokenizer()


class _FakeORTModel:
    """Minimal stand-in for ``ORTModelForCausalLM``."""

    last_kwargs: dict[str, Any] = {}
    last_model_path: str = ""
    # Full (positional_args, kwargs, snapshot-of-model-dir) list so tests
    # can assert ORTModelForCausalLM.from_pretrained was handed a real
    # directory with expected contents *at the moment of the call*.
    calls: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, bytes]]] = []

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, Any]] = []

    @classmethod
    def from_pretrained(cls, model_path: str, **kwargs: Any) -> _FakeORTModel:
        cls.last_model_path = model_path
        cls.last_kwargs = dict(kwargs)
        # Snapshot the directory passed in so tests can verify *what was
        # on disk when ORT saw it*, not what exists at test-time.
        snapshot: dict[str, bytes] = {}
        mp = Path(model_path)
        if mp.is_dir():
            for child in sorted(mp.rglob("*")):
                if child.is_file():
                    snapshot[str(child.relative_to(mp))] = child.read_bytes()
        cls.calls.append(((model_path,), dict(kwargs), snapshot))
        return cls()

    def generate(self, **kwargs: Any) -> _FakeTensor:
        self.generate_calls.append(kwargs)
        streamer = kwargs.get("streamer")
        if streamer is not None:
            streamer.put(["onnx-1 "])
            streamer.put(["onnx-2 "])
            streamer.put(["onnx-3"])
            streamer.end()
        # prompt was 5 tokens → return prompt + 3 new.
        return _FakeTensor([[10, 11, 12, 13, 14, 30, 31, 32]])


class _FakeMergedModel:
    # Track every save_pretrained call (path, snapshot-written-here) so
    # tests can assert the merged model is persisted to a real dir
    # *before* being handed to ORT for export.
    save_calls: list[str] = []

    def save_pretrained(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "pytorch_model.bin").write_bytes(b"MERGED_WEIGHTS")
        (Path(path) / "config.json").write_bytes(b'{"model_type": "fake"}')
        type(self).save_calls.append(path)


class _FakePeftWrapped:
    merge_and_unload_calls: list[_FakePeftWrapped] = []

    def __init__(self, base: Any, adapter_dir: str) -> None:
        self.base = base
        self.adapter_dir = adapter_dir
        self.merged = False

    def merge_and_unload(self) -> _FakeMergedModel:
        self.merged = True
        type(self).merge_and_unload_calls.append(self)
        return _FakeMergedModel()


class _FakePeftModel:
    last_adapter_dir: str = ""
    last_base: Any = None
    # (adapter_dir, snapshot-of-dir-at-call-time) — proves the peft files
    # were on disk at the moment peft.from_pretrained was invoked.
    calls: list[tuple[str, dict[str, bytes]]] = []

    @classmethod
    def from_pretrained(cls, base: Any, adapter_dir: str) -> _FakePeftWrapped:
        cls.last_adapter_dir = adapter_dir
        cls.last_base = base
        adapter_path = Path(adapter_dir)
        snapshot: dict[str, bytes] = {}
        if adapter_path.is_dir():
            for child in sorted(adapter_path.rglob("*")):
                if child.is_file():
                    snapshot[str(child.relative_to(adapter_path))] = child.read_bytes()
        cls.calls.append((adapter_dir, snapshot))
        return _FakePeftWrapped(base, adapter_dir)


class _FakeAutoModelForCausalLM:
    last_name: str = ""
    calls: list[str] = []

    @classmethod
    def from_pretrained(cls, name_or_path: str) -> object:
        cls.last_name = name_or_path
        cls.calls.append(name_or_path)
        return object()


class _FakeStreamer:
    # Record every instantiation so tests can assert the streamer was
    # built with the right tokenizer and flags.
    init_calls: list[tuple[Any, dict[str, Any]]] = []

    def __init__(
        self, tokenizer: Any, *, skip_prompt: bool, skip_special_tokens: bool
    ) -> None:
        assert skip_prompt is True
        assert skip_special_tokens is True
        self._tokenizer = tokenizer
        self.skip_prompt = skip_prompt
        self.skip_special_tokens = skip_special_tokens
        self._queue: list[str] = []
        self._closed = False
        type(self).init_calls.append(
            (tokenizer, {"skip_prompt": skip_prompt, "skip_special_tokens": skip_special_tokens})
        )

    def put(self, chunks: list[str]) -> None:
        self._queue.extend(chunks)

    def end(self) -> None:
        self._closed = True

    def __iter__(self) -> _FakeStreamer:
        return self

    def __next__(self) -> str:
        if self._queue:
            return self._queue.pop(0)
        raise StopIteration


# ---------------------------------------------------------------------------
# fake module installation
# ---------------------------------------------------------------------------


def _install_onnx_only_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install minimal fakes for the 'onnx' adapter_format code path."""
    optimum_ort = types.ModuleType("optimum.onnxruntime")
    optimum_ort.ORTModelForCausalLM = _FakeORTModel
    optimum_pkg = types.ModuleType("optimum")
    optimum_pkg.onnxruntime = optimum_ort

    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoTokenizer = _FakeAutoTokenizer
    transformers_mod.TextIteratorStreamer = _FakeStreamer
    # AutoModelForCausalLM intentionally omitted so we catch accidental PEFT paths.

    monkeypatch.setitem(sys.modules, "optimum", optimum_pkg)
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime", optimum_ort)
    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)


def _install_full_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fakes for both 'onnx' and 'peft' adapter_format code paths."""
    optimum_ort = types.ModuleType("optimum.onnxruntime")
    optimum_ort.ORTModelForCausalLM = _FakeORTModel
    optimum_pkg = types.ModuleType("optimum")
    optimum_pkg.onnxruntime = optimum_ort

    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoTokenizer = _FakeAutoTokenizer
    transformers_mod.AutoModelForCausalLM = _FakeAutoModelForCausalLM
    transformers_mod.TextIteratorStreamer = _FakeStreamer

    peft_mod = types.ModuleType("peft")
    peft_mod.PeftModel = _FakePeftModel

    monkeypatch.setitem(sys.modules, "optimum", optimum_pkg)
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime", optimum_ort)
    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)
    monkeypatch.setitem(sys.modules, "peft", peft_mod)


def _reset_fake_state() -> None:
    """Reset class-level state on the fakes between tests."""
    _FakeAutoTokenizer.loaded_from = []
    _FakeORTModel.last_kwargs = {}
    _FakeORTModel.last_model_path = ""
    _FakeORTModel.calls = []
    _FakePeftModel.last_adapter_dir = ""
    _FakePeftModel.last_base = None
    _FakePeftModel.calls = []
    _FakePeftWrapped.merge_and_unload_calls = []
    _FakeMergedModel.save_calls = []
    _FakeAutoModelForCausalLM.last_name = ""
    _FakeAutoModelForCausalLM.calls = []
    _FakeStreamer.init_calls = []


# ---------------------------------------------------------------------------
# bundle builders
# ---------------------------------------------------------------------------


def _onnx_bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echoes input"),
        prompts={
            "system": "You are helpful.",
            "user_template": "Q: {input}",
            "stop": [],
        },
        adapter_format=ADAPTER_FORMAT_ONNX,
        adapter_onnx_files={
            "model.onnx": b"\x08ONNX_MODEL",
            "config.json": b'{"model_type": "fake"}',
            "tokenizer.json": b'{"fake": true}',
        },
    )


def _peft_bundle() -> ChiBundle:
    return ChiBundle(
        spec=FunctionSpec(name="echo", description="echoes input"),
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
# tests: imports / deferred deps
# ---------------------------------------------------------------------------


def test_module_imports_with_no_optional_deps(monkeypatch):
    """Importing the backend module without optimum/onnxruntime must succeed."""
    monkeypatch.setitem(sys.modules, "optimum", None)
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime", None)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    # Force a fresh import of the backend module under the blocked state.
    monkeypatch.delitem(sys.modules, "chimera.function_synthesis.backends.onnx", raising=False)

    import chimera.function_synthesis.backends.onnx as onnx_mod

    assert hasattr(onnx_mod, "OnnxBackend")
    # Construction must also succeed without heavy deps.
    backend = onnx_mod.OnnxBackend(base_model="unused/model")
    assert backend is not None


def test_missing_optimum_raises_friendly_importerror(monkeypatch):
    monkeypatch.setitem(sys.modules, "optimum", None)
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime", None)
    monkeypatch.setitem(sys.modules, "transformers", None)
    _reset_fake_state()

    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="unused/model")
    with pytest.raises(ImportError, match="function_synthesis_onnx"):
        backend.load(_onnx_bundle())


def test_gguf_bundle_raises_helpful_not_implemented(monkeypatch):
    _install_full_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="unused/model")
    with pytest.raises(NotImplementedError, match="LlamaCppBackend"):
        backend.load(_gguf_bundle())


def test_unknown_adapter_format_raises(monkeypatch):
    _install_full_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    bundle = _onnx_bundle()
    # Force-override the adapter_format bypassing __post_init__ validation.
    object.__setattr__(bundle, "adapter_format", "mystery-format")
    backend = OnnxBackend(base_model="unused/model")
    with pytest.raises(ValueError, match="unknown adapter_format"):
        backend.load(bundle)


# ---------------------------------------------------------------------------
# tests: load()
# ---------------------------------------------------------------------------


def test_load_onnx_bundle_extracts_adapter_and_passes_providers(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(
        base_model="my-org/base-model",
        providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
    )
    backend.load(_onnx_bundle())

    adapter_dir = Path(_FakeORTModel.last_model_path)
    assert adapter_dir.is_dir()
    assert (adapter_dir / "model.onnx").read_bytes() == b"\x08ONNX_MODEL"
    assert (adapter_dir / "config.json").read_bytes() == b'{"model_type": "fake"}'
    assert (adapter_dir / "tokenizer.json").read_bytes() == b'{"fake": true}'

    # Providers are forwarded and export is OFF for pre-exported onnx bundles.
    assert _FakeORTModel.last_kwargs["provider"] == "CoreMLExecutionProvider"
    assert _FakeORTModel.last_kwargs["providers"] == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert "export" not in _FakeORTModel.last_kwargs

    # ORTModelForCausalLM.from_pretrained must see the adapter files
    # *at the moment of the call*, not merely at test-time. A backend
    # that wrote them AFTER the ORT call would fail this snapshot check
    # while still passing the disk assertions above.
    assert len(_FakeORTModel.calls) == 1
    (pos_args, kwargs, snapshot) = _FakeORTModel.calls[0]
    assert pos_args == (str(adapter_dir),)
    assert snapshot == {
        "model.onnx": b"\x08ONNX_MODEL",
        "config.json": b'{"model_type": "fake"}',
        "tokenizer.json": b'{"fake": true}',
    }
    # Defensive: provider list is forwarded as a *list*, not a tuple or str.
    assert isinstance(kwargs["providers"], list)
    assert kwargs["providers"] == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]

    backend.close()
    # close() scrubs the extracted adapter dir.
    assert not adapter_dir.exists()


def test_load_onnx_default_providers_is_cpu(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    backend.load(_onnx_bundle())
    assert _FakeORTModel.last_kwargs["providers"] == ["CPUExecutionProvider"]
    assert _FakeORTModel.last_kwargs["provider"] == "CPUExecutionProvider"
    backend.close()


def test_load_peft_bundle_merges_and_exports(monkeypatch, tmp_path):
    _install_full_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(
        base_model="my-org/base-model",
        cache_dir=tmp_path / "cache",
    )
    backend.load(_peft_bundle())

    # PEFT adapter was materialized and handed to peft.
    peft_adapter_dir = Path(_FakePeftModel.last_adapter_dir)
    assert peft_adapter_dir.is_dir()
    assert (peft_adapter_dir / "adapter_config.json").exists()

    # peft.from_pretrained must see the adapter files on disk *at call time*.
    assert len(_FakePeftModel.calls) == 1
    peft_call_dir, peft_snapshot = _FakePeftModel.calls[0]
    assert peft_call_dir == str(peft_adapter_dir)
    assert peft_snapshot == {
        "adapter_config.json": b'{"peft_type": "LORA"}',
        "adapter_model.safetensors": b"\x00\x01\x02WEIGHTS",
    }

    # Base model name was loaded by AutoModelForCausalLM — exactly once.
    assert _FakeAutoModelForCausalLM.last_name == "my-org/base-model"
    assert _FakeAutoModelForCausalLM.calls == ["my-org/base-model"]
    # And the object it returned is the base handed to peft.from_pretrained
    # (not None / a stray sentinel). This catches a backend that reloads
    # the base model twice by mistake.
    assert _FakePeftModel.last_base is not None

    # merge_and_unload must have been called on the peft-wrapped model
    # exactly once. A backend that skipped merging would still succeed
    # in the "pytorch_model.bin exists" assertion because the fake's
    # save_pretrained also writes it.
    assert len(_FakePeftWrapped.merge_and_unload_calls) == 1
    merged_on = _FakePeftWrapped.merge_and_unload_calls[0]
    assert merged_on.adapter_dir == str(peft_adapter_dir)
    assert merged_on.merged is True

    # save_pretrained must have been invoked on the merged model with a
    # real directory path (so ORT can read it).
    assert len(_FakeMergedModel.save_calls) == 1
    merged_save_path = Path(_FakeMergedModel.save_calls[0])
    assert merged_save_path.is_dir()

    # The merged model directory was handed to ORTModelForCausalLM with export=True.
    exported_dir = Path(_FakeORTModel.last_model_path)
    assert exported_dir.is_dir()
    assert (exported_dir / "pytorch_model.bin").exists()
    assert _FakeORTModel.last_kwargs["export"] is True
    assert _FakeORTModel.last_kwargs["cache_dir"] == str(tmp_path / "cache")
    # The default providers list must still ride along even on the peft path.
    assert _FakeORTModel.last_kwargs["providers"] == ["CPUExecutionProvider"]
    assert _FakeORTModel.last_kwargs["provider"] == "CPUExecutionProvider"
    # And the exported dir handed to ORT must contain the merged + tokenizer
    # files at call time (snapshot).
    assert len(_FakeORTModel.calls) == 1
    (_pos, _kw, ort_snapshot) = _FakeORTModel.calls[0]
    assert "pytorch_model.bin" in ort_snapshot
    assert ort_snapshot["pytorch_model.bin"] == b"MERGED_WEIGHTS"
    assert ort_snapshot["config.json"] == b'{"model_type": "fake"}'
    # Tokenizer was saved alongside (via _FakeTokenizer.save_pretrained).
    assert "tokenizer.json" in ort_snapshot

    backend.close()
    assert not peft_adapter_dir.exists()
    assert not exported_dir.exists()


# ---------------------------------------------------------------------------
# tests: invoke / stream / close
# ---------------------------------------------------------------------------


def test_invoke_builds_messages_and_decodes(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    backend.load(_onnx_bundle())

    # Asserting only ``out == "HELLO_ONNX"`` would pass even if the
    # backend never materialized the adapter. Assert the adapter bytes
    # landed on disk alongside the decoded string.
    adapter_dir = Path(_FakeORTModel.last_model_path)
    assert adapter_dir.is_dir()
    assert (adapter_dir / "model.onnx").read_bytes() == b"\x08ONNX_MODEL"

    out = backend.invoke("hello?", max_tokens=16)
    assert out == "HELLO_ONNX"

    tokenizer = backend._tokenizer
    assert tokenizer.template_messages is not None
    assert tokenizer.template_messages[0] == {
        "role": "system",
        "content": "You are helpful.",
    }
    assert tokenizer.template_messages[1] == {"role": "user", "content": "Q: hello?"}

    model = backend._model
    assert len(model.generate_calls) == 1
    gen_kwargs = model.generate_calls[0]
    assert gen_kwargs["max_new_tokens"] == 16
    assert gen_kwargs["do_sample"] is False
    # input_ids + attention_mask must be forwarded (not dropped).
    assert "input_ids" in gen_kwargs
    assert "attention_mask" in gen_kwargs
    # pad_token_id must be non-None (backend falls back to eos when pad
    # is None or falsy).
    assert gen_kwargs["pad_token_id"] is not None
    # No streamer on the one-shot invoke path.
    assert "streamer" not in gen_kwargs
    # Only the *new* tokens should be decoded, not the prompt tokens.
    assert tokenizer.decoded == [[30, 31, 32]]

    backend.close()


def test_stream_yields_multiple_chunks(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    backend.load(_onnx_bundle())

    chunks = list(backend.stream("hello?", max_tokens=8))
    assert chunks == ["onnx-1 ", "onnx-2 ", "onnx-3"]
    assert len(chunks) > 1

    model = backend._model
    gen_kwargs = model.generate_calls[-1]
    assert "streamer" in gen_kwargs
    streamer = gen_kwargs["streamer"]
    # TextIteratorStreamer must be constructed with *our* tokenizer and
    # the right flags — a silent default would cause mis-decoded chunks.
    assert isinstance(streamer, _FakeStreamer)
    assert streamer._tokenizer is backend._tokenizer
    assert streamer.skip_prompt is True
    assert streamer.skip_special_tokens is True
    assert len(_FakeStreamer.init_calls) == 1
    init_tok, init_flags = _FakeStreamer.init_calls[0]
    assert init_tok is backend._tokenizer
    assert init_flags == {"skip_prompt": True, "skip_special_tokens": True}
    # max_new_tokens must also be forwarded on the streaming path.
    assert gen_kwargs["max_new_tokens"] == 8
    assert gen_kwargs["do_sample"] is False

    backend.close()


def test_invoke_without_load_raises(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    with pytest.raises(RuntimeError, match="not loaded"):
        backend.invoke("hi")


def test_stream_without_load_raises(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    with pytest.raises(RuntimeError, match="not loaded"):
        list(backend.stream("hi"))


def test_close_clears_session_and_tempdirs(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    backend.load(_onnx_bundle())
    adapter_dir = backend._adapter_dir
    assert backend._model is not None
    assert backend._tokenizer is not None
    assert backend._bundle is not None
    assert adapter_dir is not None
    assert adapter_dir.exists()

    backend.close()

    assert backend._model is None
    assert backend._tokenizer is None
    assert backend._bundle is None
    assert backend._adapter_dir is None
    assert backend._exported_dir is None
    assert not adapter_dir.exists()


def test_close_is_safe_when_never_loaded(monkeypatch):
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    # Should not raise.
    backend.close()
    assert backend._model is None


# ---------------------------------------------------------------------------
# tests: regression / hardening (disk + kwargs parity)
# ---------------------------------------------------------------------------


def test_load_onnx_bundle_files_land_on_disk_with_correct_bytes(monkeypatch):
    """Regression: every file in ``adapter_onnx_files`` must land on disk verbatim.

    Equivalent to the llama_cpp lora-on-disk assertion in commit eff239b.
    Uses 3 small files with distinct payloads (incl. NUL and high bytes)
    so that a backend that concatenated / truncated / dropped any byte
    would fail.
    """
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    payloads = {
        "model.onnx": b"\x08ONNX_MODEL_PAYLOAD\x00\x01",
        "config.json": b'{"model_type":"fake","hidden":64}',
        "tokenizer.json": b"{\xff\x00fake-tok}",
    }
    bundle = ChiBundle(
        spec=FunctionSpec(name="echo", description="echoes input"),
        prompts={"system": "", "user_template": "{input}", "stop": []},
        adapter_format=ADAPTER_FORMAT_ONNX,
        adapter_onnx_files=payloads,
    )
    backend = OnnxBackend(base_model="my-org/base-model")
    backend.load(bundle)

    adapter_dir = Path(_FakeORTModel.last_model_path)
    assert adapter_dir.is_dir()
    for rel, expected in payloads.items():
        got = (adapter_dir / rel).read_bytes()
        assert got == expected, (
            f"byte mismatch for {rel}: got {got!r}, expected {expected!r}"
        )

    # Snapshot captured at ORTModelForCausalLM.from_pretrained call
    # time must also match byte-for-byte. This catches a regression
    # where the backend writes files AFTER handing the dir to ORT.
    assert len(_FakeORTModel.calls) == 1
    _pos, _kw, snapshot = _FakeORTModel.calls[0]
    assert snapshot == payloads

    backend.close()


def test_load_onnx_providers_exact_list_coreml(monkeypatch):
    """providers= must reach ORT as the exact list the user gave (order preserved)."""
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    user_providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    backend = OnnxBackend(
        base_model="my-org/base-model",
        providers=user_providers,
    )
    backend.load(_onnx_bundle())

    assert _FakeORTModel.last_kwargs["providers"] == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]
    # Backend must *not* mutate the caller's list.
    assert user_providers == ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    # First provider must also be forwarded via the legacy ``provider=`` kwarg.
    assert _FakeORTModel.last_kwargs["provider"] == "CoreMLExecutionProvider"
    backend.close()


def test_load_peft_bundle_ort_call_uses_merged_dir_not_base(monkeypatch, tmp_path):
    """Regression: ORT.from_pretrained must be called with the *merged* dir,
    never directly with the HF base model id, on the peft path."""
    _install_full_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(
        base_model="my-org/base-model",
        cache_dir=tmp_path / "cache",
    )
    backend.load(_peft_bundle())

    # ORT must NOT be called with the raw base model id — a backend that
    # skipped the merge+export would pass export=True and a base id and
    # kinda-sorta work, but we explicitly forbid that path.
    assert _FakeORTModel.last_model_path != "my-org/base-model"
    # Instead it must be called with the merged dir written by
    # _FakeMergedModel.save_pretrained.
    assert _FakeORTModel.last_model_path in _FakeMergedModel.save_calls
    # export=True on the peft path (so optimum exports on load), and
    # cache_dir points at the user's chosen cache.
    assert _FakeORTModel.last_kwargs["export"] is True
    assert _FakeORTModel.last_kwargs["cache_dir"] == str(tmp_path / "cache")
    backend.close()


def test_invoke_forwards_max_new_tokens_verbatim(monkeypatch):
    """``max_tokens`` on invoke() must forward as ``max_new_tokens``."""
    _install_onnx_only_fakes(monkeypatch)
    _reset_fake_state()
    from chimera.function_synthesis.backends.onnx import OnnxBackend

    backend = OnnxBackend(base_model="my-org/base-model")
    backend.load(_onnx_bundle())
    backend.invoke("hi", max_tokens=1)
    backend.invoke("hi", max_tokens=512)

    calls = backend._model.generate_calls
    assert len(calls) == 2
    assert calls[0]["max_new_tokens"] == 1
    assert calls[1]["max_new_tokens"] == 512
    backend.close()
