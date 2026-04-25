"""GitHub Actions workflow file validation.

These tests guard against regressions in the two workflow files we ship:
``ci.yml`` (push/PR matrix lane) and ``mink-live.yml`` (opt-in live lane
that drives the walking-skeleton against a real Ollama daemon).

WHY: a malformed workflow YAML, a missing top-level key, or an unpinned
``uses:`` reference would break CI silently — GitHub only surfaces the
failure when the workflow is actually triggered.  Catching it locally
prevents a round-trip through the Actions UI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
MINK_LIVE = WORKFLOWS_DIR / "mink-live.yml"
CI = WORKFLOWS_DIR / "ci.yml"


def _load(path: Path) -> dict:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{path} did not parse to a mapping"
    return data


def _has_on_key(data: dict) -> bool:
    # YAML 1.1 coerces the bare key ``on`` to the boolean ``True``.
    # PyYAML preserves that, so accept either form.
    return "on" in data or True in data


def test_mink_live_workflow_parses() -> None:
    """``mink-live.yml`` parses and exposes the expected top-level shape."""
    assert MINK_LIVE.exists(), f"missing workflow file: {MINK_LIVE}"
    data = _load(MINK_LIVE)
    assert "name" in data, "mink-live.yml missing top-level ``name``"
    assert _has_on_key(data), "mink-live.yml missing top-level ``on``"
    assert "jobs" in data, "mink-live.yml missing top-level ``jobs``"
    # Live lane must remain manually triggerable from the Actions UI.
    on_section = data.get("on", data.get(True))
    assert isinstance(on_section, dict), "``on:`` must be a mapping"
    assert "workflow_dispatch" in on_section, (
        "mink-live.yml must keep workflow_dispatch so it can be triggered manually"
    )
    # Sanity: every job must declare runs-on + steps.
    for job_name, job in data["jobs"].items():
        assert "runs-on" in job, f"job {job_name!r} missing runs-on"
        assert "steps" in job, f"job {job_name!r} missing steps"
        assert isinstance(job["steps"], list) and job["steps"], (
            f"job {job_name!r} must have at least one step"
        )


def test_ci_workflow_parses() -> None:
    """``ci.yml`` parses and exposes the expected top-level shape."""
    assert CI.exists(), f"missing workflow file: {CI}"
    data = _load(CI)
    assert "name" in data, "ci.yml missing top-level ``name``"
    assert _has_on_key(data), "ci.yml missing top-level ``on``"
    assert "jobs" in data, "ci.yml missing top-level ``jobs``"
    # Sanity: every job must declare runs-on + steps.
    for job_name, job in data["jobs"].items():
        assert "runs-on" in job, f"job {job_name!r} missing runs-on"
        assert "steps" in job, f"job {job_name!r} missing steps"
        assert isinstance(job["steps"], list) and job["steps"], (
            f"job {job_name!r} must have at least one step"
        )


# Bare ``@main`` / ``@master`` / ``@latest`` references are unpinned and
# therefore unsafe — a silent upstream change can break the build.
_UNPINNED = re.compile(r"^\s*-?\s*uses:\s*\S+@(main|master|latest)\s*$")
_USES_LINE = re.compile(r"^\s*-?\s*uses:\s*(\S+)\s*$")


@pytest.mark.parametrize("workflow", [MINK_LIVE, CI], ids=lambda p: p.name)
def test_action_versions_pinned(workflow: Path) -> None:
    """Every ``uses:`` reference must pin a tag/SHA — never ``@main``."""
    assert workflow.exists(), f"missing workflow file: {workflow}"
    offenders: list[tuple[int, str]] = []
    missing_at: list[tuple[int, str]] = []
    for lineno, line in enumerate(workflow.read_text().splitlines(), start=1):
        if _UNPINNED.match(line):
            offenders.append((lineno, line.strip()))
            continue
        m = _USES_LINE.match(line)
        if m and "@" not in m.group(1):
            missing_at.append((lineno, line.strip()))
    assert not offenders, (
        f"{workflow.name} uses unpinned action versions (main/master/latest): {offenders}"
    )
    assert not missing_at, (
        f"{workflow.name} has ``uses:`` references with no @<tag>: {missing_at}"
    )
