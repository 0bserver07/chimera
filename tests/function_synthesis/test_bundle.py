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
