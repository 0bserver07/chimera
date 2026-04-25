"""Tests for chimera.tools.cron_tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.tools.cron_tools import CronCreateTool, CronDeleteTool, CronListTool


@pytest.fixture
def cron_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the cron job store under ``tmp_path``."""
    d = tmp_path / "cron"
    monkeypatch.setenv("CHIMERA_CRON_DIR", str(d))
    return d


def test_create_persists_job(cron_dir: Path) -> None:
    tool = CronCreateTool()
    res = tool.execute(
        {"name": "nightly", "schedule": "0 2 * * *", "command": "echo hi"},
        env=None,
    )
    assert res.success, res.error
    jobs_file = cron_dir / "jobs.json"
    assert jobs_file.exists()
    jobs = json.loads(jobs_file.read_text())
    assert len(jobs) == 1
    assert jobs[0]["name"] == "nightly"
    assert jobs[0]["registered"] is False


def test_create_rejects_duplicate(cron_dir: Path) -> None:
    tool = CronCreateTool()
    base = {"name": "j", "schedule": "* * * * *", "command": "true"}
    assert tool.execute(base, env=None).success
    res2 = tool.execute(base, env=None)
    assert not res2.success
    assert "already exists" in (res2.error or "")


def test_list_reads_persisted(cron_dir: Path) -> None:
    create = CronCreateTool()
    create.execute({"name": "a", "schedule": "* * * * *", "command": "x"}, env=None)
    create.execute({"name": "b", "schedule": "0 0 * * *", "command": "y"}, env=None)
    res = CronListTool().execute({}, env=None)
    assert res.success
    parsed = json.loads(res.output)
    names = sorted(j["name"] for j in parsed)
    assert names == ["a", "b"]


def test_list_empty_when_no_file(cron_dir: Path) -> None:
    res = CronListTool().execute({}, env=None)
    assert res.success
    assert json.loads(res.output) == []


def test_delete_removes_entry(cron_dir: Path) -> None:
    CronCreateTool().execute(
        {"name": "doomed", "schedule": "* * * * *", "command": "true"},
        env=None,
    )
    res = CronDeleteTool().execute({"name": "doomed"}, env=None)
    assert res.success
    jobs = json.loads((cron_dir / "jobs.json").read_text())
    assert jobs == []


def test_delete_unknown_errors(cron_dir: Path) -> None:
    res = CronDeleteTool().execute({"name": "ghost"}, env=None)
    assert not res.success
    assert "no job" in (res.error or "")
