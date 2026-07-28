"""Tests for ``chimera doctor`` setup-diagnostics command.

Each probe is exercised with injected fakes (no network, no subprocess)
plus a final end-to-end check that runs ``chimera doctor --format json``
and parses the result.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import subprocess
import sys
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from chimera.cli import doctor


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def test_check_api_keys_all_set() -> None:
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-xxx",
        "OPENAI_API_KEY": "sk-openai-xxx",
        "OPENROUTER_API_KEY": "sk-or-xxx",
        "XAI_API_KEY": "xai-xxx",
        "MOONSHOT_API_KEY": "ms-xxx",
    }
    checks = doctor.check_api_keys(env=env)
    assert len(checks) == 5
    assert all(c.status == doctor.OK for c in checks)
    assert any("ANTHROPIC_API_KEY" in c.name for c in checks)


def test_check_api_keys_missing() -> None:
    checks = doctor.check_api_keys(env={})
    assert len(checks) == 5
    assert all(c.status == doctor.WARN for c in checks)
    assert all("not set" in c.detail for c in checks)
    assert all(c.hint for c in checks)


def test_check_api_keys_partial() -> None:
    env = {"ANTHROPIC_API_KEY": "abc"}
    checks = doctor.check_api_keys(env=env)
    by_name = {c.name: c for c in checks}
    assert by_name["env.ANTHROPIC_API_KEY"].status == doctor.OK
    assert by_name["env.OPENAI_API_KEY"].status == doctor.WARN


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def _make_opener(payload: bytes | Exception):
    def _opener(url: str, timeout: float) -> bytes:
        if isinstance(payload, Exception):
            raise payload
        return payload

    return _opener


def test_check_ollama_reachable() -> None:
    payload = json.dumps(
        {"models": [{"name": "qwen3:8b"}, {"name": "llama3:8b"}]}
    ).encode()
    check = doctor.check_ollama(opener=_make_opener(payload))
    assert check.status == doctor.OK
    assert "2 model" in check.detail
    assert "qwen3:8b" in check.detail


def test_check_ollama_unreachable() -> None:
    err = urllib.error.URLError("connection refused")
    check = doctor.check_ollama(opener=_make_opener(err))
    assert check.status == doctor.WARN
    assert "unreachable" in check.detail
    assert check.hint


def test_check_ollama_timeout() -> None:
    check = doctor.check_ollama(opener=_make_opener(socket.timeout()))
    assert check.status == doctor.WARN
    assert "unreachable" in check.detail


def test_check_ollama_no_models() -> None:
    payload = json.dumps({"models": []}).encode()
    check = doctor.check_ollama(opener=_make_opener(payload))
    assert check.status == doctor.OK
    assert "no models pulled" in check.detail


# ---------------------------------------------------------------------------
# llama.cpp / vLLM / SGLang share the same probe
# ---------------------------------------------------------------------------


def test_check_llamacpp_reachable() -> None:
    payload = json.dumps(
        {"data": [{"id": "qwen2.5-coder-7b"}]}
    ).encode()
    check = doctor.check_llamacpp(opener=_make_opener(payload))
    assert check.status == doctor.OK
    assert "qwen2.5-coder-7b" in check.detail


def test_check_vllm_unreachable() -> None:
    check = doctor.check_vllm(opener=_make_opener(ConnectionRefusedError()))
    assert check.status == doctor.WARN


def test_check_sglang_reachable_no_models() -> None:
    payload = json.dumps({"data": []}).encode()
    check = doctor.check_sglang(opener=_make_opener(payload))
    assert check.status == doctor.OK
    assert "no models" in check.detail


def test_check_llamacpp_non_json() -> None:
    check = doctor.check_llamacpp(opener=_make_opener(b"not json"))
    assert check.status == doctor.WARN
    assert "non-JSON" in check.detail


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


def _fake_completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["docker", "info"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_check_docker_running() -> None:
    def runner(_cmd: list[str]) -> Any:
        return _fake_completed(0, "Server Version: 24.0.0\n")

    check = doctor.check_docker(runner=runner)
    assert check.status == doctor.OK


def test_check_docker_missing_cli() -> None:
    def runner(_cmd: list[str]) -> Any:
        raise FileNotFoundError("no docker")

    check = doctor.check_docker(runner=runner)
    assert check.status == doctor.WARN
    assert "not installed" in check.detail


def test_check_docker_daemon_down() -> None:
    def runner(_cmd: list[str]) -> Any:
        return _fake_completed(1, "", "Cannot connect to the Docker daemon")

    check = doctor.check_docker(runner=runner)
    assert check.status == doctor.WARN
    assert "Cannot connect" in check.detail


def test_check_docker_timeout() -> None:
    def runner(_cmd: list[str]) -> Any:
        raise subprocess.TimeoutExpired(cmd=_cmd, timeout=2.0)

    check = doctor.check_docker(runner=runner)
    assert check.status == doctor.WARN
    assert "timed out" in check.detail


# ---------------------------------------------------------------------------
# Optional extras
# ---------------------------------------------------------------------------


def test_check_optional_extras_some_missing() -> None:
    installed = {"rich"}

    def importer(name: str) -> Any:
        if name in installed:
            return object()
        raise ImportError(name)

    checks = doctor.check_optional_extras(importer=importer)
    by_name = {c.name: c for c in checks}
    assert by_name["extra.rich"].status == doctor.OK
    assert by_name["extra.textual"].status == doctor.WARN
    assert by_name["extra.asyncssh"].status == doctor.WARN
    assert by_name["extra.modal"].status == doctor.WARN


def test_check_optional_extras_all_installed() -> None:
    def importer(_name: str) -> Any:
        return object()

    checks = doctor.check_optional_extras(importer=importer)
    assert all(c.status == doctor.OK for c in checks)


# ---------------------------------------------------------------------------
# CLI versions
# ---------------------------------------------------------------------------


def test_check_cli_versions_all_ok() -> None:
    def runner(cmd: list[str]) -> Any:
        return _fake_completed(0, f"{cmd[-2]} 0.7.0\n")

    checks = doctor.check_cli_versions(runner=runner)
    assert len(checks) == 7
    assert all(c.status == doctor.OK for c in checks)
    assert all("0.7.0" in c.detail for c in checks)


def test_check_cli_versions_one_missing() -> None:
    def runner(cmd: list[str]) -> Any:
        if "ferret" in cmd:
            return _fake_completed(2, "", "scaffold not yet built")
        return _fake_completed(0, "version 0.7.0\n")

    checks = doctor.check_cli_versions(runner=runner)
    by_name = {c.name: c for c in checks}
    assert by_name["cli.ferret"].status == doctor.WARN
    assert by_name["cli.mink"].status == doctor.OK


def test_check_cli_versions_timeout() -> None:
    def runner(cmd: list[str]) -> Any:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5.0)

    checks = doctor.check_cli_versions(runner=runner)
    assert all(c.status == doctor.WARN for c in checks)


# ---------------------------------------------------------------------------
# Eventlog dir
# ---------------------------------------------------------------------------


def test_check_eventlog_dir_creates(tmp_path: Path) -> None:
    check = doctor.check_eventlog_dir(home=tmp_path)
    assert check.status == doctor.OK
    assert (tmp_path / ".chimera" / "eventlog").exists()


def test_check_eventlog_dir_already_exists(tmp_path: Path) -> None:
    (tmp_path / ".chimera" / "eventlog").mkdir(parents=True)
    check = doctor.check_eventlog_dir(home=tmp_path)
    assert check.status == doctor.OK


# ---------------------------------------------------------------------------
# Plugin index
# ---------------------------------------------------------------------------


def test_check_plugin_index_env_override() -> None:
    env = {"CHIMERA_PLUGIN_INDEX": "https://example.com/plugins.json"}
    check = doctor.check_plugin_index(env=env)
    assert check.status == doctor.OK
    assert "example.com" in check.detail


def test_check_plugin_index_no_config() -> None:
    """With no env override and no built-in default URL, doctor surfaces a
    WARN with a hint pointing users at the three configuration paths."""
    check = doctor.check_plugin_index(env={}, opener=_make_opener(b"{}"))
    assert check.status == doctor.WARN
    assert "no plugin index" in check.detail.lower()
    assert check.hint is not None
    assert "CHIMERA_PLUGIN_INDEX" in check.hint or "plugin_index" in check.hint


def test_check_plugin_index_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a deployment monkey-patches DEFAULT_INDEX_URL to a local path that
    exists, doctor reports OK without any HTTP probe."""
    import tempfile
    import chimera.plugins.marketplace as mp_mod

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False
    ) as fh:
        fh.write(b'{"plugins": []}')
        local_path = fh.name
    try:
        monkeypatch.setattr(mp_mod, "DEFAULT_INDEX_URL", local_path)
        check = doctor.check_plugin_index(env={})
        assert check.status == doctor.OK
        assert "local index" in check.detail
    finally:
        Path(local_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_format_text_no_color() -> None:
    checks = [
        doctor.Check(name="env.X", status=doctor.OK, detail="set"),
        doctor.Check(
            name="daemon.y",
            status=doctor.WARN,
            detail="unreachable",
            hint="start it",
        ),
    ]
    out = doctor.format_text(checks, color=False)
    assert "chimera doctor:" in out
    assert "env.X" in out
    assert "ok" in out
    assert "warn" in out
    assert "hint: start it" in out
    # No ANSI escapes when color is off.
    assert "\033[" not in out


def test_format_text_with_color() -> None:
    checks = [doctor.Check(name="env.X", status=doctor.OK, detail="set")]
    out = doctor.format_text(checks, color=True)
    assert "\033[32m" in out  # green
    assert "\033[0m" in out


def test_format_json_round_trip() -> None:
    checks = [
        doctor.Check(name="a", status=doctor.OK, detail="d"),
        doctor.Check(name="b", status=doctor.WARN, detail="d", hint="h"),
        doctor.Check(name="c", status=doctor.FAIL, detail="d"),
    ]
    out = doctor.format_json(checks)
    parsed = json.loads(out)
    assert parsed["summary"] == {"ok": 1, "warn": 1, "fail": 1}
    assert len(parsed["checks"]) == 3
    assert parsed["checks"][1]["hint"] == "h"


# ---------------------------------------------------------------------------
# End-to-end: parse `chimera doctor --format json` output
# ---------------------------------------------------------------------------


def test_doctor_run_json_end_to_end(monkeypatch, tmp_path: Path) -> None:
    """Run the ``run()`` entry point with stubbed network + subprocess."""
    fake_home = tmp_path

    # Stub urlopen so all HTTP probes fail fast.
    def fake_urlopen(*_args: Any, **_kwargs: Any) -> Any:
        raise urllib.error.URLError("no network in test")

    monkeypatch.setattr(
        "chimera.cli.doctor.urllib.request.urlopen", fake_urlopen
    )
    # Stub subprocess.run so docker + cli probes don't actually shell out.
    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        return _fake_completed(0, "stub 0.0.1\n")

    monkeypatch.setattr("chimera.cli.doctor.subprocess.run", fake_run)
    # Override $HOME so eventlog probe is hermetic.
    monkeypatch.setenv("HOME", str(fake_home))
    # Avoid leaking real env into key probes.
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "XAI_API_KEY",
        "MOONSHOT_API_KEY",
        "CHIMERA_PLUGIN_INDEX",
    ):
        monkeypatch.delenv(key, raising=False)

    args = argparse.Namespace(format="json", no_color=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = doctor.run(args)
    out = buf.getvalue()
    parsed = json.loads(out)
    assert "checks" in parsed
    assert "summary" in parsed
    # No FAILs expected -> exit 0
    assert rc == 0
    names = {c["name"] for c in parsed["checks"]}
    assert "env.ANTHROPIC_API_KEY" in names
    assert "daemon.ollama" in names
    assert "daemon.llamacpp" in names
    assert "daemon.vllm" in names
    assert "daemon.sglang" in names
    assert "daemon.docker" in names
    assert "eventlog.dir" in names
    assert "plugin.index" in names
    assert "cli.mink" in names


def test_doctor_run_text_end_to_end(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "chimera.cli.doctor.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(
            urllib.error.URLError("no network")
        ),
    )
    monkeypatch.setattr(
        "chimera.cli.doctor.subprocess.run",
        lambda *a, **k: _fake_completed(0, "stub 0.0.1\n"),
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    args = argparse.Namespace(format="text", no_color=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = doctor.run(args)
    out = buf.getvalue()
    assert rc == 0
    assert "chimera doctor:" in out
    assert "summary:" in out


def test_doctor_subcommand_registered() -> None:
    """``chimera doctor`` should be a top-level subcommand."""
    from chimera.cli.main import build_parser

    parser = build_parser()
    # Argparse stores subparser actions in ``_subparsers``.
    sub_actions = [
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    ]
    assert sub_actions, "expected a subparsers action on the top-level parser"
    choices = sub_actions[0].choices
    assert "doctor" in choices


def test_doctor_main_invocation_smoke(monkeypatch, tmp_path: Path) -> None:
    """Invoke ``main(['doctor', '--format', 'json'])`` end-to-end."""
    monkeypatch.setattr(
        "chimera.cli.doctor.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(
            urllib.error.URLError("no network")
        ),
    )
    monkeypatch.setattr(
        "chimera.cli.doctor.subprocess.run",
        lambda *a, **k: _fake_completed(0, "stub 0.0.1\n"),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    from chimera.cli.main import main

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["doctor", "--format", "json"])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert "checks" in parsed


# ---------------------------------------------------------------------------
# Storage section (M2 of docs/specs/storage-and-experiments.md)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _storage_home(tmp_path, monkeypatch):
    """A hermetic storage root + project root for the storage probe."""
    for var in (
        "CHIMERA_HOME",
        "CHIMERA_CONFIG_HOME",
        "CHIMERA_DATASETS_DIR",
        "CHIMERA_FS_HOME",
        "CHIMERA_CRON_DIR",
        "CHIMERA_TEAMS_HOME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project)
    home = tmp_path / "home" / ".chimera"
    home.mkdir(parents=True, exist_ok=True)
    return home, project


def test_check_storage_emits_one_row_per_registry_store(_storage_home) -> None:
    """Both scopes, present or absent — the inventory must be complete.

    An absent store is reported as absent rather than omitted: only a report
    that distinguishes "declared and empty" from "not declared at all" can be
    trusted when it calls something an orphan.
    """
    from chimera.config.paths import all_stores

    _home, project = _storage_home
    checks = doctor.check_storage(project)
    names = {c.name for c in checks}
    for store in all_stores():
        assert f"storage.{store.name}" in names
    assert "storage.root" in names
    assert all(c.section == doctor.STORAGE_SECTION for c in checks)


def test_check_storage_reports_size_entries_ages_and_retention(
    _storage_home,
) -> None:
    home, project = _storage_home
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "a.jsonl").write_text("x" * 1200, encoding="utf-8")
    (project / ".chimera").mkdir(parents=True)
    (project / ".chimera" / "config.toml").write_text(
        "[storage.sessions]\nretain = 3\n", encoding="utf-8"
    )

    row = next(
        c for c in doctor.check_storage(project) if c.name == "storage.sessions"
    )
    assert "1.2 kB" in row.detail
    assert "1 entry" in row.detail
    assert "newest" in row.detail and "oldest" in row.detail
    assert "retain=3" in row.detail
    assert row.data is not None
    assert row.data["size_bytes"] == 1200
    assert row.data["retention"]["retain"] == 3


def test_doctor_reports_a_checkpoints_position_orphan(_storage_home) -> None:
    """The spec's original blind spot, pinned at the CLI boundary.

    ``<workdir>/.chimera_checkpoints`` sits *beside* ``<proj>/.chimera``. A
    scan of the two scope roots — which is what the spec first asked for —
    would print nothing here, which is precisely how a 2.0 GB tree went
    unreported for four months.
    """
    _home, project = _storage_home
    (project / ".chimera").mkdir(parents=True)
    blob = project / ".chimera_checkpoints" / "0" / ".venv"
    blob.mkdir(parents=True)
    (blob / "lib.bin").write_bytes(b"x" * 8192)

    checks = doctor.check_storage(project)
    orphans = [c for c in checks if c.name.startswith("storage.orphan.")]
    assert len(orphans) == 1
    assert orphans[0].status == doctor.WARN
    assert ".chimera_checkpoints" in orphans[0].detail
    assert "8.2 kB" in orphans[0].detail
    assert orphans[0].hint


def test_doctor_says_so_when_nothing_is_orphaned(_storage_home) -> None:
    _home, project = _storage_home
    checks = doctor.check_storage(project)
    row = next(c for c in checks if c.name == "storage.orphans")
    assert row.status == doctor.OK
    assert "none" in row.detail


def test_an_orphan_warns_but_does_not_fail_the_run(_storage_home) -> None:
    """An orphan is a finding to act on, not a broken install."""
    home, project = _storage_home
    (home / "mystery").mkdir(parents=True)
    checks = doctor.check_storage(project)
    assert any(c.status == doctor.WARN for c in checks)
    assert not any(c.status == doctor.FAIL for c in checks)


def test_section_storage_skips_the_network_and_subprocess_probes(
    _storage_home,
) -> None:
    """What makes the storage view cheap enough to script against."""
    _home, project = _storage_home
    checks = doctor.collect_checks("storage", project)
    assert checks
    assert all(c.name.startswith("storage.") for c in checks)


def test_section_providers_omits_storage(monkeypatch) -> None:
    monkeypatch.setattr(
        "chimera.cli.doctor.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("no net")),
    )
    monkeypatch.setattr(
        "chimera.cli.doctor.subprocess.run",
        lambda *a, **k: _fake_completed(0, "stub 0.0.1\n"),
    )
    checks = doctor.collect_checks("providers")
    assert not any(c.name.startswith("storage.") for c in checks)


def test_json_flag_is_shorthand_for_format_json(_storage_home) -> None:
    _home, project = _storage_home
    args = argparse.Namespace(
        format="text", json=True, section="storage", project=str(project),
        no_color=True,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = doctor.run(args)
    assert rc == 0
    payload = json.loads(buf.getvalue())
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["storage.root"]["section"] == "storage"
    assert "data" in by_name["storage.sessions"]


def test_json_shape_is_unchanged_for_pre_existing_probes() -> None:
    """Adding ``section``/``data`` must not reshape rows that carry neither."""
    payload = doctor.Check(name="env.X", status=doctor.OK, detail="set").to_dict()
    assert payload == {
        "name": "env.X",
        "status": doctor.OK,
        "detail": "set",
        "hint": "",
    }


def test_format_text_prints_a_heading_per_section() -> None:
    checks = [
        doctor.Check(name="env.X", status=doctor.OK, detail="set"),
        doctor.Check(
            name="storage.root",
            status=doctor.OK,
            detail="/tmp/x",
            section=doctor.STORAGE_SECTION,
        ),
    ]
    out = doctor.format_text(checks, color=False)
    assert "[storage]" in out
    # The unsectioned group keeps its original, heading-free shape.
    assert out.index("env.X") < out.index("[storage]")


def test_doctor_storage_via_main(_storage_home) -> None:
    _home, project = _storage_home
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main_module_main(["doctor", "--section", "storage", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert any(c["name"] == "storage.root" for c in payload["checks"])


def main_module_main(argv: list[str]) -> int:
    from chimera.cli.main import main

    return main(argv)


# Ensure stdlib-only-ness: no network/subprocess actually invoked here.
assert sys.version_info >= (3, 11)
