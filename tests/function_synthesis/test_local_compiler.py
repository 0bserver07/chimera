"""Offline tests for :class:`LocalCompiler`.

The entire HuggingFace stack (transformers, peft, datasets, torch) is mocked
so these tests never download anything and finish in well under a second.
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.function_synthesis.bundle import ADAPTER_FORMAT_PEFT, ChiBundle
from chimera.function_synthesis.compiler import CompilerError
from chimera.function_synthesis.compilers import local as local_mod
from chimera.function_synthesis.compilers.local import LocalCompiler
from chimera.function_synthesis.spec import FunctionSpec


def _make_spec(*, with_examples: bool = True) -> FunctionSpec:
    examples = (
        [
            {"input": "Love it!", "output": "positive"},
            {"input": "Hated it.", "output": "negative"},
        ]
        if with_examples
        else []
    )
    return FunctionSpec(
        name="sentiment",
        description="Classify sentiment as positive or negative.",
        examples=examples,
    )


def _mock_deps(saved_files: dict[str, bytes] | None = None) -> dict[str, Any]:
    """Build a mock ``_import_deps`` return value.

    ``peft_model.save_pretrained(dir)`` writes every entry in ``saved_files``
    into the directory passed to it, so that the compiler's subsequent
    directory read picks them up.
    """
    files = (
        saved_files
        if saved_files is not None
        else {
            "adapter_config.json": b'{"r": 8, "lora_alpha": 16}',
            "adapter_model.safetensors": b"FAKE_SAFETENSORS",
        }
    )

    # Dataset: mimic the .from_list(...).map(...) chain.
    class FakeDataset:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self.rows = rows

        @classmethod
        def from_list(cls, rows: list[dict[str, str]]) -> FakeDataset:
            return cls(rows)

        def map(self, fn: Any) -> FakeDataset:  # pragma: no cover - trivial
            # Not strictly required — the mocked trainer ignores the dataset —
            # but we still exercise the real example rendering.
            self.rows = [fn(r) for r in self.rows]
            return self

    # Tokenizer: mimic HF __call__ producing input_ids, returning a dict.
    tokenizer = MagicMock()
    tokenizer.pad_token = None
    tokenizer.eos_token = "<eos>"
    tokenizer.return_value = {"input_ids": [1, 2, 3], "attention_mask": [1, 1, 1]}

    auto_tokenizer = MagicMock()
    auto_tokenizer.from_pretrained = MagicMock(return_value=tokenizer)

    model = MagicMock()
    auto_model = MagicMock()
    auto_model.from_pretrained = MagicMock(return_value=model)

    # peft_model.save_pretrained writes the fake adapter files.
    peft_model = MagicMock()

    def _save_pretrained(target_dir: str) -> None:
        from pathlib import Path as _P

        for name, data in files.items():
            dst = _P(target_dir) / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)

    peft_model.save_pretrained = MagicMock(side_effect=_save_pretrained)

    get_peft_model = MagicMock(return_value=peft_model)
    lora_config_cls = MagicMock(return_value=MagicMock())

    trainer = MagicMock()
    trainer.train = MagicMock(return_value=None)
    trainer_cls = MagicMock(return_value=trainer)
    training_args_cls = MagicMock(return_value=MagicMock())

    return {
        "AutoModelForCausalLM": auto_model,
        "AutoTokenizer": auto_tokenizer,
        "Trainer": trainer_cls,
        "TrainingArguments": training_args_cls,
        "LoraConfig": lora_config_cls,
        "get_peft_model": get_peft_model,
        "Dataset": FakeDataset,
        "_trainer_instance": trainer,
        "_peft_model": peft_model,
    }


# ---------------------------------------------------------------------------
# compile() behavior with mocked deps
# ---------------------------------------------------------------------------


def test_compile_with_empty_examples_raises_compiler_error():
    compiler = LocalCompiler("some/base-model")
    spec = _make_spec(with_examples=False)
    with pytest.raises(CompilerError, match="examples"):
        compiler.compile(spec)


def test_compile_returns_peft_bundle_with_expected_metadata(monkeypatch):
    deps = _mock_deps()
    monkeypatch.setattr(local_mod, "_import_deps", lambda: deps)

    compiler = LocalCompiler(
        "fake/base-model",
        num_train_epochs=2,
        learning_rate=5e-5,
        lora_r=8,
        lora_alpha=16,
    )
    spec = _make_spec()
    bundle = compiler.compile(spec)

    assert isinstance(bundle, ChiBundle)
    assert bundle.adapter_format == ADAPTER_FORMAT_PEFT
    assert bundle.metadata["compiler_backend"] == "local"
    assert bundle.metadata["base_model"] == "fake/base-model"
    assert bundle.metadata["num_examples"] == len(spec.examples)
    assert bundle.metadata["lora_r"] == 8
    assert bundle.metadata["lora_alpha"] == 16
    assert "adapter_config.json" in bundle.adapter_peft_files
    assert bundle.adapter_peft_files["adapter_config.json"] == b'{"r": 8, "lora_alpha": 16}'

    # Confirm the training path actually invoked Trainer.train() — the mock
    # isn't hiding a short-circuit.
    assert deps["_trainer_instance"].train.call_count == 1
    assert deps["_peft_model"].save_pretrained.call_count == 1


def test_compile_bundle_round_trips_to_disk(monkeypatch, tmp_path):
    deps = _mock_deps()
    monkeypatch.setattr(local_mod, "_import_deps", lambda: deps)

    compiler = LocalCompiler("fake/base-model")
    bundle = compiler.compile(_make_spec())

    dst = tmp_path / "sentiment.chi"
    bundle.save(dst)
    loaded = ChiBundle.load(dst)
    assert loaded.adapter_format == ADAPTER_FORMAT_PEFT
    assert loaded.adapter_peft_files == bundle.adapter_peft_files
    assert loaded.metadata["compiler_backend"] == "local"


def test_compile_uses_user_template_on_examples(monkeypatch):
    """Prompt rendering should apply ``user_template.format(input=...)``."""
    deps = _mock_deps()
    monkeypatch.setattr(local_mod, "_import_deps", lambda: deps)

    compiler = LocalCompiler("fake/base-model")
    spec = _make_spec()
    compiler.compile(spec)

    # Inspect what was fed to AutoTokenizer.__call__ inside the tokenize fn.
    tokenizer = deps["AutoTokenizer"].from_pretrained.return_value
    calls = tokenizer.call_args_list
    texts = [call.args[0] for call in calls]
    assert any("Love it!" in t and "positive" in t for t in texts)
    assert any("Hated it." in t and "negative" in t for t in texts)


def test_lora_config_falls_back_when_all_linear_unsupported(monkeypatch):
    """Older peft releases reject target_modules='all-linear' — we retry."""
    deps = _mock_deps()

    calls = {"n": 0}

    def _lora_config(**kwargs: Any) -> MagicMock:
        calls["n"] += 1
        if calls["n"] == 1:
            assert kwargs.get("target_modules") == "all-linear"
            raise ValueError("unsupported target_modules")
        assert kwargs.get("target_modules") == ["q_proj", "v_proj"]
        return MagicMock()

    deps["LoraConfig"] = MagicMock(side_effect=_lora_config)
    monkeypatch.setattr(local_mod, "_import_deps", lambda: deps)

    compiler = LocalCompiler("fake/base-model")
    bundle = compiler.compile(_make_spec())
    assert bundle.adapter_format == ADAPTER_FORMAT_PEFT
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Optional-dep import gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["torch", "transformers", "peft", "datasets"],
)
def test_compile_raises_importerror_when_dep_missing(monkeypatch, missing):
    """``_import_deps`` surfaces a clear ImportError for each missing package."""
    # Block exactly one package by pretending it's not installed.
    real_import = __import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        # Block both the top-level package and its submodules
        # (e.g. ``from transformers import AutoTokenizer``).
        if name == missing or name.startswith(missing + "."):
            raise ImportError(f"No module named {missing!r}")
        return real_import(name, *args, **kwargs)

    # If other optional deps aren't installed in this env, stub them in so
    # only the one under test is missing.
    stubs: list[str] = []
    for pkg in ["torch", "transformers", "peft", "datasets"]:
        if pkg == missing:
            continue
        if pkg not in sys.modules:
            stub = types.ModuleType(pkg)
            if pkg == "transformers":
                stub.AutoModelForCausalLM = object  # type: ignore[attr-defined]
                stub.AutoTokenizer = object  # type: ignore[attr-defined]
                stub.Trainer = object  # type: ignore[attr-defined]
                stub.TrainingArguments = object  # type: ignore[attr-defined]
            elif pkg == "peft":
                stub.LoraConfig = object  # type: ignore[attr-defined]
                stub.get_peft_model = lambda *a, **k: None  # type: ignore[attr-defined]
            elif pkg == "datasets":
                stub.Dataset = object  # type: ignore[attr-defined]
            sys.modules[pkg] = stub
            stubs.append(pkg)

    # Ensure a fresh import of the missing pkg is attempted.
    for mod_name in list(sys.modules):
        if mod_name == missing or mod_name.startswith(missing + "."):
            sys.modules.pop(mod_name, None)

    monkeypatch.setattr("builtins.__import__", fake_import)
    try:
        compiler = LocalCompiler("fake/base-model")
        with pytest.raises(ImportError, match=missing):
            compiler.compile(_make_spec())
    finally:
        # Clean up the stubs we injected.
        for pkg in stubs:
            sys.modules.pop(pkg, None)
