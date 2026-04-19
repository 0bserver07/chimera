from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.spec import FunctionSpec


def _make_bundle(tmp_path: Path) -> Path:
    spec = FunctionSpec(name="classify", description="classify sentiment")
    bundle = ChiBundle(
        spec=spec,
        adapter_bytes=b"FAKE_GGUF_BYTES",
        prompts={"system": "You classify.", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "test", "base_model_sha256": "deadbeef"},
        base_model="qwen3-4b-instruct-q4_0",
    )
    dst = tmp_path / "classify.chi"
    bundle.save(dst)
    return dst


def test_bundle_round_trip(tmp_path):
    path = _make_bundle(tmp_path)
    loaded = ChiBundle.load(path)
    assert loaded.spec.name == "classify"
    assert loaded.adapter_bytes == b"FAKE_GGUF_BYTES"
    assert loaded.prompts["system"] == "You classify."
    assert loaded.base_model == "qwen3-4b-instruct-q4_0"


def test_bundle_is_a_zipfile(tmp_path):
    path = _make_bundle(tmp_path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert {"manifest.json", "adapter.gguf", "prompts.json", "spec.json", "metadata.json"} <= names


def test_bundle_rejects_unknown_schema(tmp_path):
    path = tmp_path / "bad.chi"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", '{"schema_version": 999, "name": "x", "description": "y", "base_model": "z", "adapter_format": "gguf-lora", "created_at": "", "chimera_version": ""}')
        zf.writestr("adapter.gguf", b"")
        zf.writestr("prompts.json", "{}")
        zf.writestr("spec.json", '{"name": "x", "description": "y", "examples": [], "input_schema": null, "output_schema": null}')
        zf.writestr("metadata.json", "{}")
    with pytest.raises(ChiBundleError, match="schema_version"):
        ChiBundle.load(path)


def test_bundle_rejects_missing_adapter(tmp_path):
    path = tmp_path / "noadapter.chi"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", '{"schema_version": 1, "name": "x", "description": "y", "base_model": "z", "adapter_format": "gguf-lora", "created_at": "", "chimera_version": ""}')
        zf.writestr("prompts.json", "{}")
        zf.writestr("spec.json", '{"name": "x", "description": "y", "examples": [], "input_schema": null, "output_schema": null}')
        zf.writestr("metadata.json", "{}")
    with pytest.raises(ChiBundleError, match="adapter"):
        ChiBundle.load(path)


def test_peft_bundle_round_trip(tmp_path):
    """A ``adapter_format == 'peft'`` bundle packs files under ``adapter_peft/``."""
    spec = FunctionSpec(name="sentiment", description="classify sentiment")
    peft_files = {
        "adapter_config.json": b'{"r": 8, "lora_alpha": 16}',
        "adapter_model.safetensors": b"FAKE_SAFETENSORS_PAYLOAD",
        "tokenizer.json": b'{"fake": true}',
    }
    bundle = ChiBundle(
        spec=spec,
        prompts={"system": "s", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "local"},
        base_model="qwen3-4b-instruct",
        adapter_format="peft",
        adapter_peft_files=peft_files,
    )
    dst = tmp_path / "sentiment.chi"
    bundle.save(dst)

    # ZIP layout contains the adapter_peft/ subdirectory.
    with zipfile.ZipFile(dst) as zf:
        names = set(zf.namelist())
    assert "adapter_peft/adapter_config.json" in names
    assert "adapter_peft/adapter_model.safetensors" in names
    assert "adapter.gguf" not in names

    loaded = ChiBundle.load(dst)
    assert loaded.adapter_format == "peft"
    assert loaded.adapter_bytes == b""
    assert loaded.adapter_peft_files == peft_files


def test_peft_bundle_rejects_missing_adapter_dir(tmp_path):
    path = tmp_path / "nopeft.chi"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "manifest.json",
            '{"schema_version": 1, "name": "x", "description": "y", "base_model": "z", '
            '"adapter_format": "peft", "created_at": "", "chimera_version": ""}',
        )
        zf.writestr("prompts.json", "{}")
        zf.writestr(
            "spec.json",
            '{"name": "x", "description": "y", "examples": [], '
            '"input_schema": null, "output_schema": null}',
        )
        zf.writestr("metadata.json", "{}")
    with pytest.raises(ChiBundleError, match="adapter_peft"):
        ChiBundle.load(path)


def test_bundle_rejects_mixed_adapter_fields():
    spec = FunctionSpec(name="x", description="y")
    with pytest.raises(ChiBundleError, match="peft"):
        ChiBundle(
            spec=spec,
            adapter_bytes=b"gguf",
            adapter_format="peft",
            adapter_peft_files={"adapter_config.json": b"{}"},
        )
