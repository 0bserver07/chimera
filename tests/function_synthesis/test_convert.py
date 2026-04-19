"""Tests for chimera.function_synthesis.convert (PEFT -> ChiBundle)."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.convert import (
    PeftImportError,
    extract_peft_files,
    import_peft,
    save_peft_bundle,
)
from chimera.function_synthesis.spec import FunctionSpec


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _write_fake_peft_dir(
    root: Path,
    *,
    base_model: str = "qwen/qwen2.5-4b-instruct",
    weights_format: str = "safetensors",
    extras: dict[str, bytes] | None = None,
) -> Path:
    """Create a plausible PEFT directory with config + fake tensor blob."""
    peft_dir = root / "my_adapter"
    peft_dir.mkdir()
    config = {
        "base_model_name_or_path": base_model,
        "peft_type": "LORA",
        "task_type": "CAUSAL_LM",
        "r": 16,
        "lora_alpha": 32,
        "target_modules": ["q_proj", "v_proj"],
    }
    (peft_dir / "adapter_config.json").write_text(json.dumps(config, sort_keys=True))
    # Fake weight bytes — the converter is format-agnostic; we just need
    # something deterministic to hash.
    weight_bytes = b"\x00\xff" * 2048 + b"lora-weights-stub"
    if weights_format == "safetensors":
        (peft_dir / "adapter_model.safetensors").write_bytes(weight_bytes)
    elif weights_format == "bin":
        (peft_dir / "adapter_model.bin").write_bytes(weight_bytes)
    else:
        raise ValueError(f"unknown weights_format: {weights_format}")

    for name, data in (extras or {}).items():
        (peft_dir / name).write_bytes(data)

    return peft_dir


def _spec() -> FunctionSpec:
    return FunctionSpec(name="sentiment", description="classify sentiment")


def _prompts() -> dict:
    return {
        "system": "Classify the sentiment.",
        "user_template": "Text: {input}",
        "stop": [],
    }


# -----------------------------------------------------------------------------
# import_peft — happy path
# -----------------------------------------------------------------------------


def test_import_peft_safetensors(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path)
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())

    assert isinstance(bundle, ChiBundle)
    assert bundle.spec.name == "sentiment"
    assert bundle.base_model == "qwen/qwen2.5-4b-instruct"
    # adapter_bytes carries the raw weights file content.
    assert bundle.adapter_bytes == (peft_dir / "adapter_model.safetensors").read_bytes()
    # Metadata records the source.
    assert bundle.metadata["adapter_source"] == "peft"
    assert bundle.metadata["peft_weights_filename"] == "adapter_model.safetensors"
    assert bundle.metadata["peft_config"]["peft_type"] == "LORA"
    assert bundle.metadata["peft_config"]["r"] == 16


def test_import_peft_bin(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path, weights_format="bin")
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    assert bundle.metadata["peft_weights_filename"] == "adapter_model.bin"
    assert bundle.adapter_bytes == (peft_dir / "adapter_model.bin").read_bytes()


def test_import_peft_records_config_hash(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path)
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    expected = hashlib.sha256(
        (peft_dir / "adapter_config.json").read_bytes()
    ).hexdigest()
    assert bundle.metadata["peft_config_hash"] == expected


def test_import_peft_explicit_base_model_overrides_config(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path, base_model="original/model")
    bundle = import_peft(
        peft_dir,
        spec=_spec(),
        prompts=_prompts(),
        base_model="custom/base",
    )
    assert bundle.base_model == "custom/base"


def test_import_peft_metadata_extras_merge(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path)
    bundle = import_peft(
        peft_dir,
        spec=_spec(),
        prompts=_prompts(),
        metadata_extras={"author": "me", "tags": ["demo"]},
    )
    assert bundle.metadata["author"] == "me"
    assert bundle.metadata["tags"] == ["demo"]
    # Does not clobber adapter_source.
    assert bundle.metadata["adapter_source"] == "peft"


def test_import_peft_picks_up_optional_files(tmp_path):
    peft_dir = _write_fake_peft_dir(
        tmp_path,
        extras={
            "tokenizer_config.json": b'{"model_max_length": 2048}',
            "README.md": b"# My adapter\n",
        },
    )
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    assert "tokenizer_config.json" in bundle.metadata["peft_optional_files"]
    assert "README.md" in bundle.metadata["peft_optional_files"]


# -----------------------------------------------------------------------------
# import_peft — error paths
# -----------------------------------------------------------------------------


def test_import_peft_missing_dir(tmp_path):
    with pytest.raises(PeftImportError, match="is not a directory"):
        import_peft(tmp_path / "does-not-exist", spec=_spec(), prompts=_prompts())


def test_import_peft_missing_config(tmp_path):
    peft_dir = tmp_path / "bad"
    peft_dir.mkdir()
    (peft_dir / "adapter_model.safetensors").write_bytes(b"weights")
    with pytest.raises(PeftImportError, match="adapter_config.json"):
        import_peft(peft_dir, spec=_spec(), prompts=_prompts())


def test_import_peft_missing_weights(tmp_path):
    peft_dir = tmp_path / "bad"
    peft_dir.mkdir()
    (peft_dir / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "foo/bar"})
    )
    with pytest.raises(PeftImportError, match="no adapter weights"):
        import_peft(peft_dir, spec=_spec(), prompts=_prompts())


def test_import_peft_malformed_config(tmp_path):
    peft_dir = tmp_path / "bad"
    peft_dir.mkdir()
    (peft_dir / "adapter_config.json").write_text("not-json{{")
    (peft_dir / "adapter_model.safetensors").write_bytes(b"x")
    with pytest.raises(PeftImportError, match="malformed"):
        import_peft(peft_dir, spec=_spec(), prompts=_prompts())


def test_import_peft_no_base_model(tmp_path):
    peft_dir = tmp_path / "bad"
    peft_dir.mkdir()
    # Config without base_model_name_or_path.
    (peft_dir / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA"}))
    (peft_dir / "adapter_model.safetensors").write_bytes(b"x")
    with pytest.raises(PeftImportError, match="base_model"):
        import_peft(peft_dir, spec=_spec(), prompts=_prompts())


# -----------------------------------------------------------------------------
# save_peft_bundle + ChiBundle.load compatibility
# -----------------------------------------------------------------------------


def test_save_peft_bundle_round_trip_weights(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path)
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    out = tmp_path / "out.chi"
    save_peft_bundle(bundle, peft_dir, out)
    assert out.exists()

    # Restore PEFT files and verify byte equality.
    restore_dir = tmp_path / "restored"
    extract_peft_files(out, restore_dir)
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        original = (peft_dir / name).read_bytes()
        restored = (restore_dir / name).read_bytes()
        assert restored == original, f"{name} did not round-trip byte-for-byte"


def test_save_peft_bundle_manifest_adapter_format_peft(tmp_path):
    peft_dir = _write_fake_peft_dir(tmp_path)
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    out = tmp_path / "out.chi"
    save_peft_bundle(bundle, peft_dir, out)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["adapter_format"] == "peft"
    assert manifest["base_model"] == "qwen/qwen2.5-4b-instruct"
    assert manifest["peft_weights_filename"] == "adapter_model.safetensors"


def test_save_peft_bundle_still_loads_with_chibundle(tmp_path):
    """save_peft_bundle output must remain readable by ChiBundle.load."""
    peft_dir = _write_fake_peft_dir(tmp_path)
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    out = tmp_path / "out.chi"
    save_peft_bundle(bundle, peft_dir, out)

    loaded = ChiBundle.load(out)
    assert loaded.spec.name == "sentiment"
    assert loaded.metadata["adapter_source"] == "peft"
    assert loaded.base_model == "qwen/qwen2.5-4b-instruct"


def test_save_peft_bundle_ships_optional_files(tmp_path):
    peft_dir = _write_fake_peft_dir(
        tmp_path,
        extras={"tokenizer_config.json": b'{"x": 1}'},
    )
    bundle = import_peft(peft_dir, spec=_spec(), prompts=_prompts())
    out = tmp_path / "out.chi"
    save_peft_bundle(bundle, peft_dir, out)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "adapter_peft/tokenizer_config.json" in names


# -----------------------------------------------------------------------------
# extract_peft_files edge cases
# -----------------------------------------------------------------------------


def test_extract_peft_files_refuses_non_peft_archive(tmp_path):
    # A plain ChiBundle without PEFT members.
    bundle = ChiBundle(
        spec=_spec(),
        adapter_bytes=b"x",
        prompts=_prompts(),
    )
    out = tmp_path / "plain.chi"
    bundle.save(out)
    with pytest.raises(PeftImportError, match="no 'adapter_peft/' members"):
        extract_peft_files(out, tmp_path / "restored")
