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
