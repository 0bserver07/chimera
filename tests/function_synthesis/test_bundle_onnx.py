"""Round-trip + validation tests for ``adapter_format == "onnx"`` bundles."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import (
    ADAPTER_FORMAT_ONNX,
    ChiBundle,
    ChiBundleError,
)
from chimera.function_synthesis.spec import FunctionSpec


def _onnx_files() -> dict[str, bytes]:
    return {
        "model.onnx": b"\x08\x07ONNX_MODEL_BYTES",
        "config.json": b'{"model_type": "qwen"}',
        "tokenizer.json": b'{"fake": true}',
    }


def test_onnx_bundle_round_trip(tmp_path: Path) -> None:
    """An ``adapter_format == 'onnx'`` bundle packs files under ``adapter_onnx/``."""
    spec = FunctionSpec(name="sentiment", description="classify sentiment")
    files = _onnx_files()
    bundle = ChiBundle(
        spec=spec,
        prompts={"system": "s", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "local"},
        base_model="qwen3-4b-instruct",
        adapter_format=ADAPTER_FORMAT_ONNX,
        adapter_onnx_files=files,
    )
    dst = tmp_path / "sentiment.chi"
    bundle.save(dst)

    # ZIP layout contains the adapter_onnx/ subdirectory.
    with zipfile.ZipFile(dst) as zf:
        names = set(zf.namelist())
    assert "adapter_onnx/model.onnx" in names
    assert "adapter_onnx/config.json" in names
    assert "adapter_onnx/tokenizer.json" in names
    assert "adapter.gguf" not in names
    assert not any(n.startswith("adapter_peft/") for n in names)

    loaded = ChiBundle.load(dst)
    assert loaded.adapter_format == "onnx"
    assert loaded.adapter_bytes == b""
    assert loaded.adapter_peft_files == {}
    assert loaded.adapter_onnx_files == files


def test_onnx_bundle_rejects_missing_adapter_dir(tmp_path: Path) -> None:
    path = tmp_path / "noonnx.chi"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "manifest.json",
            '{"schema_version": 1, "name": "x", "description": "y", '
            '"base_model": "z", "adapter_format": "onnx", "created_at": "", '
            '"chimera_version": ""}',
        )
        zf.writestr("prompts.json", "{}")
        zf.writestr(
            "spec.json",
            '{"name": "x", "description": "y", "examples": [], '
            '"input_schema": null, "output_schema": null}',
        )
        zf.writestr("metadata.json", "{}")
    with pytest.raises(ChiBundleError, match="adapter_onnx"):
        ChiBundle.load(path)


def test_onnx_bundle_rejects_mixed_gguf_bytes() -> None:
    spec = FunctionSpec(name="x", description="y")
    with pytest.raises(ChiBundleError, match="onnx"):
        ChiBundle(
            spec=spec,
            adapter_bytes=b"gguf",
            adapter_format=ADAPTER_FORMAT_ONNX,
            adapter_onnx_files={"model.onnx": b"x"},
        )


def test_onnx_bundle_rejects_mixed_peft_files() -> None:
    spec = FunctionSpec(name="x", description="y")
    with pytest.raises(ChiBundleError, match="onnx"):
        ChiBundle(
            spec=spec,
            adapter_format=ADAPTER_FORMAT_ONNX,
            adapter_onnx_files={"model.onnx": b"x"},
            adapter_peft_files={"adapter_config.json": b"{}"},
        )


def test_onnx_bundle_requires_at_least_one_file() -> None:
    spec = FunctionSpec(name="x", description="y")
    with pytest.raises(ChiBundleError, match="onnx bundle must include at least one"):
        ChiBundle(
            spec=spec,
            adapter_format=ADAPTER_FORMAT_ONNX,
            adapter_onnx_files={},
        )


def test_peft_bundle_rejects_onnx_files() -> None:
    """Cross-field validation: a peft bundle must not carry onnx files."""
    spec = FunctionSpec(name="x", description="y")
    with pytest.raises(ChiBundleError, match="peft bundle must not include adapter_onnx_files"):
        ChiBundle(
            spec=spec,
            adapter_format="peft",
            adapter_peft_files={"adapter_config.json": b"{}"},
            adapter_onnx_files={"model.onnx": b"x"},
        )


def test_gguf_bundle_rejects_onnx_files() -> None:
    """Cross-field validation: a gguf bundle must not carry onnx files."""
    spec = FunctionSpec(name="x", description="y")
    with pytest.raises(ChiBundleError, match="gguf-lora bundle must not include adapter_onnx_files"):
        ChiBundle(
            spec=spec,
            adapter_bytes=b"gguf",
            adapter_format="gguf-lora",
            adapter_onnx_files={"model.onnx": b"x"},
        )
