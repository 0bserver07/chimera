"""Tests for ``chimera.badger.parity`` — the harness-rewrite parity check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from chimera.badger import parity


def test_parity_schema_to_dict_round_trip() -> None:
    schema = parity.ParitySchema(
        tools=["bash", "read_file"],
        max_steps=25,
        slash_commands=["help", "parity"],
        model="claude-sonnet-4-6",
        rerun_on_failure=True,
    )
    data = schema.to_dict()
    assert data["tools"] == ["bash", "read_file"]
    assert data["max_steps"] == 25
    assert data["model"] == "claude-sonnet-4-6"
    assert data["rerun_on_failure"] is True


def test_load_schema_json(tmp_path: Path) -> None:
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text(json.dumps({
        "tools": ["bash", "read_file"],
        "max_steps": 25,
        "slash_commands": ["help"],
        "model": "claude-sonnet-4-6",
        "rerun_on_failure": False,
    }))
    schema = parity.load_schema(schema_path)
    assert schema.tools == ["bash", "read_file"]
    assert schema.max_steps == 25
    assert schema.slash_commands == ["help"]


def test_load_schema_yaml(tmp_path: Path) -> None:
    schema_path = tmp_path / "PARITY.yaml"
    schema_path.write_text(
        "model: claude-sonnet-4-6\n"
        "max_steps: 25\n"
        "rerun_on_failure: true\n"
        "tools:\n"
        "  - bash\n"
        "  - read_file\n"
        "slash_commands:\n"
        "  - help\n"
        "  - parity\n"
    )
    schema = parity.load_schema(schema_path)
    assert schema.model == "claude-sonnet-4-6"
    assert schema.max_steps == 25
    assert schema.rerun_on_failure is True
    assert "bash" in schema.tools
    assert "parity" in schema.slash_commands


def test_load_schema_md_fenced(tmp_path: Path) -> None:
    """PARITY.md ships as Markdown with a fenced JSON block."""
    schema_path = tmp_path / "PARITY.md"
    schema_path.write_text(
        "# Parity schema\n\n"
        "```\n"
        '{"tools": ["bash"], "max_steps": 25}\n'
        "```\n"
    )
    schema = parity.load_schema(schema_path)
    assert schema.tools == ["bash"]
    assert schema.max_steps == 25


def test_load_schema_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parity.load_schema(tmp_path / "missing.json")


def test_load_schema_invalid_json_raises(tmp_path: Path) -> None:
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text("{this is not json}")
    with pytest.raises(ValueError):
        parity.load_schema(schema_path)


def test_build_live_snapshot_uses_defaults() -> None:
    """Live snapshot mirrors the documented badger defaults."""
    live = parity.build_live_snapshot()
    assert live.max_steps == 25
    assert live.model == "claude-sonnet-4-6"
    assert live.rerun_on_failure is False
    assert "parity" in live.slash_commands
    assert "rerun" in live.slash_commands


def test_build_live_snapshot_accepts_overrides() -> None:
    live = parity.build_live_snapshot(
        tools=[],
        slash_commands=["help"],
        max_steps=10,
        model="local",
        rerun_on_failure=True,
    )
    assert live.tools == []
    assert live.slash_commands == ["help"]
    assert live.max_steps == 10
    assert live.model == "local"
    assert live.rerun_on_failure is True


def test_diff_schema_ok_when_match() -> None:
    expected = parity.ParitySchema(
        tools=["bash"],
        max_steps=25,
        slash_commands=["help"],
        model="claude-sonnet-4-6",
        rerun_on_failure=False,
    )
    live = parity.ParitySchema(
        tools=["bash", "read_file"],  # extra is fine
        max_steps=25,
        slash_commands=["help", "parity"],
        model="claude-sonnet-4-6",
        rerun_on_failure=False,
    )
    report = parity.diff_schema(expected, live)
    assert report.ok
    assert report.missing_tools == []
    assert "read_file" in report.extra_tools


def test_diff_schema_detects_missing_tool() -> None:
    expected = parity.ParitySchema(tools=["bash", "browser"])
    live = parity.ParitySchema(tools=["bash"])
    report = parity.diff_schema(expected, live)
    assert not report.ok
    assert report.missing_tools == ["browser"]


def test_diff_schema_detects_max_steps_mismatch() -> None:
    expected = parity.ParitySchema(max_steps=25)
    live = parity.ParitySchema(max_steps=50)
    report = parity.diff_schema(expected, live)
    assert not report.ok
    assert report.max_steps_mismatch == (25, 50)


def test_diff_schema_detects_model_mismatch() -> None:
    expected = parity.ParitySchema(model="claude-sonnet-4-6")
    live = parity.ParitySchema(model="gpt-4o")
    report = parity.diff_schema(expected, live)
    assert not report.ok
    assert report.model_mismatch == ("claude-sonnet-4-6", "gpt-4o")


def test_format_report_ok() -> None:
    report = parity.ParityReport()
    text = parity.format_report(report)
    assert "OK" in text


def test_format_report_failure_lists_misses() -> None:
    report = parity.ParityReport(
        missing_tools=["bash"],
        max_steps_mismatch=(25, 50),
    )
    text = parity.format_report(report)
    assert "FAIL" in text
    assert "bash" in text
    assert "max_steps mismatch" in text


# ---------------------------------------------------------------------------
# run_parity_check end-to-end (no LLM).
# ---------------------------------------------------------------------------


def test_run_parity_check_match_returns_zero(tmp_path: Path, capsys) -> None:
    """When the schema matches the live snapshot, rc=0."""
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text(json.dumps({
        "max_steps": 25,
        "model": "claude-sonnet-4-6",
        "rerun_on_failure": False,
    }))
    args = argparse.Namespace(
        parity_against=str(schema_path),
        cwd=str(tmp_path),
        output_format="text",
    )
    rc = parity.run_parity_check(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_run_parity_check_mismatch_returns_one(tmp_path: Path, capsys) -> None:
    """When the schema diverges from live, rc=1 with a diff report."""
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text(json.dumps({
        "max_steps": 999,
        "model": "claude-sonnet-4-6",
    }))
    args = argparse.Namespace(
        parity_against=str(schema_path),
        cwd=str(tmp_path),
        output_format="text",
    )
    rc = parity.run_parity_check(args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_run_parity_check_missing_schema_returns_two(tmp_path: Path, capsys) -> None:
    """When no schema is provided and none can be auto-resolved, rc=2."""
    args = argparse.Namespace(
        parity_against=None,
        cwd=str(tmp_path),
        output_format="text",
    )
    rc = parity.run_parity_check(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "no schema found" in err.lower()


def test_run_parity_check_json_output(tmp_path: Path, capsys) -> None:
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text(json.dumps({"max_steps": 25}))
    args = argparse.Namespace(
        parity_against=str(schema_path),
        cwd=str(tmp_path),
        output_format="json",
    )
    rc = parity.run_parity_check(args)
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["report"]["ok"] is True
    assert payload["expected"]["max_steps"] == 25
