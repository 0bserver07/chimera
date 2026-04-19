"""Live smoke tests for :class:`LocalCompiler`.

Opt-in via the ``live`` pytest marker.  Skips cleanly when:

* ``CHIMERA_FS_LIVE_COMPILER_MODEL`` is unset, or
* any of the optional deps (transformers/peft/torch/datasets) are missing.

Env vars:

* ``CHIMERA_FS_LIVE_COMPILER_MODEL`` -- a small HF causal-LM that is cheap to
  fine-tune on CPU (e.g. ``"Qwen/Qwen2-0.5B"``).

The default knobs are aggressive: ``num_train_epochs=1`` and ``lora_r=2`` so
the whole run completes in a few minutes on CPU.

Run with::

    CHIMERA_FS_LIVE_COMPILER_MODEL=Qwen/Qwen2-0.5B \\
        uv run pytest -m live tests/function_synthesis/test_live_local_compiler.py -v
"""
from __future__ import annotations

import importlib.util
import os

import pytest

from chimera.function_synthesis.bundle import ADAPTER_FORMAT_PEFT, ChiBundle
from chimera.function_synthesis.spec import FunctionSpec

pytestmark = pytest.mark.live


def _require_compile_stack() -> None:
    for mod in ("transformers", "peft", "torch", "datasets"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} not installed; skipping live compiler test")


@pytest.fixture
def live_compiler_model() -> str:
    name = os.environ.get("CHIMERA_FS_LIVE_COMPILER_MODEL")
    if not name:
        pytest.skip(
            "set CHIMERA_FS_LIVE_COMPILER_MODEL to a small HF causal-LM "
            "(e.g. 'Qwen/Qwen2-0.5B')"
        )
    _require_compile_stack()
    return name


@pytest.fixture
def tiny_spec() -> FunctionSpec:
    return FunctionSpec(
        name="upper-echo",
        description="Uppercase the input.",
        examples=[
            {"input": "hello", "output": "HELLO"},
            {"input": "world", "output": "WORLD"},
            {"input": "chimera", "output": "CHIMERA"},
            {"input": "live", "output": "LIVE"},
        ],
    )


def test_live_local_compiler_produces_peft_bundle(tmp_path, live_compiler_model, tiny_spec):
    """Compile a tiny LoRA and verify the bundle shape."""
    from chimera.function_synthesis.compilers.local import LocalCompiler

    compiler = LocalCompiler(
        live_compiler_model,
        num_train_epochs=1,
        lora_r=2,
        lora_alpha=4,
        output_dir=tmp_path / "train",
    )
    bundle = compiler.compile(tiny_spec)

    assert isinstance(bundle, ChiBundle)
    assert bundle.adapter_format == ADAPTER_FORMAT_PEFT
    assert bundle.adapter_peft_files, "compiler produced no adapter files"
    # PEFT always writes an adapter_config.json alongside the weight shard.
    assert "adapter_config.json" in bundle.adapter_peft_files
    # Metadata should reflect the knobs we passed in.
    assert bundle.metadata["compiler_backend"] == "local"
    assert bundle.metadata["base_model"] == live_compiler_model
    assert bundle.metadata["num_examples"] == len(tiny_spec.examples)
    assert bundle.metadata["lora_r"] == 2
    assert bundle.metadata["num_train_epochs"] == 1


def test_live_local_compiler_save_reload_roundtrip(
    tmp_path, live_compiler_model, tiny_spec
):
    """Save the compiled bundle to disk and reload it; bytes must match."""
    from chimera.function_synthesis.compilers.local import LocalCompiler

    compiler = LocalCompiler(
        live_compiler_model,
        num_train_epochs=1,
        lora_r=2,
        lora_alpha=4,
        output_dir=tmp_path / "train",
    )
    bundle = compiler.compile(tiny_spec)

    chi_path = tmp_path / "upper-echo.chi"
    bundle.save(chi_path)
    loaded = ChiBundle.load(chi_path)

    assert loaded.spec.name == bundle.spec.name
    assert loaded.adapter_format == ADAPTER_FORMAT_PEFT
    assert loaded.adapter_peft_files == bundle.adapter_peft_files
    assert loaded.base_model == bundle.base_model
    assert loaded.prompts == bundle.prompts


@pytest.mark.xfail(
    reason=(
        "Tiny base models fine-tuned on 3-5 examples rarely produce meaningful "
        "output for arbitrary prompts; this roundtrip exercises the wiring, "
        "not quality."
    ),
    strict=False,
)
def test_live_local_compiler_then_transformers_invoke(
    tmp_path, live_compiler_model, tiny_spec
):
    """Compile + load via :class:`TransformersBackend` + invoke()."""
    from chimera.function_synthesis.backends.transformers import TransformersBackend
    from chimera.function_synthesis.compilers.local import LocalCompiler

    compiler = LocalCompiler(
        live_compiler_model,
        num_train_epochs=1,
        lora_r=2,
        lora_alpha=4,
        output_dir=tmp_path / "train",
    )
    bundle = compiler.compile(tiny_spec)

    backend = TransformersBackend(live_compiler_model, device="cpu")
    try:
        backend.load(bundle)
        out = backend.invoke("hello", max_tokens=8)
        assert isinstance(out, str)
        assert len(out.strip()) > 0
    finally:
        backend.close()
