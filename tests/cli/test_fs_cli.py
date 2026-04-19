# tests/cli/test_fs_cli.py
from __future__ import annotations

import json
import os
import subprocess
import sys

def _run(args: list[str], env: dict, cwd: str | None = None):
    return subprocess.run(
        [sys.executable, "-m", "chimera", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _env(tmp_path) -> dict:
    return {**os.environ, "CHIMERA_FS_HOME": str(tmp_path), "CHIMERA_FS_OFFLINE": "1"}


def _write_spec(tmp_path, name: str = "classify", desc: str = "classify sentiment"):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"name": name, "description": desc}))
    return spec_file


def test_fs_compile_writes_bundle_to_registry(tmp_path):
    env = _env(tmp_path)
    spec_file = _write_spec(tmp_path)
    result = _run(
        ["fs", "compile", str(spec_file), "--compiler", "mock"],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "classify-" in result.stdout  # slug printed
    assert (tmp_path / "bundles").exists()
    assert list((tmp_path / "bundles").glob("*.chi"))


def test_fs_list_shows_installed(tmp_path):
    env = _env(tmp_path)
    spec = _write_spec(tmp_path, name="a", desc="x")
    _run(["fs", "compile", str(spec), "--compiler", "mock"], env=env)
    result = _run(["fs", "list"], env=env)
    assert result.returncode == 0
    assert "a-" in result.stdout


def test_fs_rm_removes_entry(tmp_path):
    env = _env(tmp_path)
    spec = _write_spec(tmp_path, name="a", desc="x")
    compiled = _run(["fs", "compile", str(spec), "--compiler", "mock"], env=env)
    slug = compiled.stdout.strip()
    _run(["fs", "rm", slug], env=env)
    listed = _run(["fs", "list"], env=env)
    assert slug not in listed.stdout


def test_fs_info_returns_json(tmp_path):
    env = _env(tmp_path)
    spec = _write_spec(tmp_path, name="a", desc="x")
    compiled = _run(["fs", "compile", str(spec), "--compiler", "mock"], env=env)
    slug = compiled.stdout.strip()
    result = _run(["fs", "info", slug], env=env)
    payload = json.loads(result.stdout)
    assert payload["slug"] == slug
    assert payload["spec"]["name"] == "a"


# -----------------------------------------------------------------------------
# import-peft subcommand
# -----------------------------------------------------------------------------


def _write_fake_peft(tmp_path, *, base_model: str = "qwen/qwen2.5-4b-instruct"):
    peft_dir = tmp_path / "adapter"
    peft_dir.mkdir()
    (peft_dir / "adapter_config.json").write_text(
        json.dumps(
            {
                "base_model_name_or_path": base_model,
                "peft_type": "LORA",
                "r": 8,
                "lora_alpha": 16,
                "target_modules": ["q_proj"],
            },
            sort_keys=True,
        )
    )
    (peft_dir / "adapter_model.safetensors").write_bytes(b"FAKE_WEIGHTS_BYTES" * 16)
    return peft_dir


def test_fs_import_peft_prints_slug_and_writes_bundle(tmp_path):
    env = _env(tmp_path)
    peft_dir = _write_fake_peft(tmp_path)
    spec = _write_spec(tmp_path, name="sentiment", desc="classify sentiment")
    result = _run(
        ["fs", "import-peft", str(peft_dir), str(spec)],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    slug = result.stdout.strip()
    assert slug.startswith("sentiment-")
    bundles = list((tmp_path / "bundles").glob("*.chi"))
    assert len(bundles) == 1
    assert bundles[0].stem == slug


def test_fs_import_peft_registers_in_index(tmp_path):
    env = _env(tmp_path)
    peft_dir = _write_fake_peft(tmp_path)
    spec = _write_spec(tmp_path, name="sent2", desc="classify")
    compiled = _run(
        ["fs", "import-peft", str(peft_dir), str(spec)], env=env
    )
    slug = compiled.stdout.strip()
    # `fs list` should surface the slug.
    listed = _run(["fs", "list"], env=env)
    assert slug in listed.stdout


def test_fs_import_peft_info_reports_peft_metadata(tmp_path):
    env = _env(tmp_path)
    peft_dir = _write_fake_peft(tmp_path)
    spec = _write_spec(tmp_path, name="peftfn", desc="demo")
    compiled = _run(["fs", "import-peft", str(peft_dir), str(spec)], env=env)
    slug = compiled.stdout.strip()
    info = _run(["fs", "info", slug], env=env)
    payload = json.loads(info.stdout)
    assert payload["metadata"]["adapter_source"] == "peft"
    assert payload["metadata"]["peft_weights_filename"] == "adapter_model.safetensors"
    assert "peft_config_hash" in payload["metadata"]


def test_fs_import_peft_out_overrides_slug(tmp_path):
    env = _env(tmp_path)
    peft_dir = _write_fake_peft(tmp_path)
    spec = _write_spec(tmp_path, name="x", desc="y")
    result = _run(
        ["fs", "import-peft", str(peft_dir), str(spec), "--out", "custom-slug"],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "custom-slug"
    assert (tmp_path / "bundles" / "custom-slug.chi").exists()


def test_fs_import_peft_base_model_override(tmp_path):
    env = _env(tmp_path)
    peft_dir = _write_fake_peft(tmp_path, base_model="orig/model")
    spec = _write_spec(tmp_path, name="bm", desc="y")
    result = _run(
        [
            "fs",
            "import-peft",
            str(peft_dir),
            str(spec),
            "--base-model",
            "other/base",
        ],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    slug = result.stdout.strip()
    info = _run(["fs", "info", slug], env=env)
    payload = json.loads(info.stdout)
    # Verify via archive manifest too.
    import zipfile as _zip

    with _zip.ZipFile(payload["bundle_path"]) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["base_model"] == "other/base"
    assert manifest["adapter_format"] == "peft"


def test_fs_import_peft_round_trip_weights_via_cli(tmp_path):
    env = _env(tmp_path)
    peft_dir = _write_fake_peft(tmp_path)
    spec = _write_spec(tmp_path, name="rt", desc="roundtrip")
    compiled = _run(["fs", "import-peft", str(peft_dir), str(spec)], env=env)
    slug = compiled.stdout.strip()
    chi_path = tmp_path / "bundles" / f"{slug}.chi"

    import zipfile as _zip

    original_weights = (peft_dir / "adapter_model.safetensors").read_bytes()
    with _zip.ZipFile(chi_path) as zf:
        restored = zf.read("adapter_peft/adapter_model.safetensors")
    assert restored == original_weights


def test_fs_import_peft_missing_dir_fails_cleanly(tmp_path):
    env = _env(tmp_path)
    spec = _write_spec(tmp_path, name="bad", desc="d")
    result = _run(
        ["fs", "import-peft", str(tmp_path / "does-not-exist"), str(spec)],
        env=env,
    )
    assert result.returncode != 0
    # Error from PeftImportError should propagate.
    assert "not a directory" in (result.stderr + result.stdout)
