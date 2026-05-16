"""Validate that ``.github/workflows/publish.yml`` ships both publish paths.

The publish workflow uses Option B from W18-1 / issue #143: a single
``publish-pypi`` job that prefers PyPI Trusted Publishing (OIDC, no
secrets) and falls back to ``secrets.PYPI_API_TOKEN`` when the OIDC
step does not succeed.  These tests parse the YAML and assert both
branches are still wired correctly — a malformed condition or a
missing ``id-token: write`` permission would silently break the
release cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WF = REPO_ROOT / ".github" / "workflows" / "publish.yml"

_PYPI_ACTION_PREFIX = "pypa/gh-action-pypi-publish"


def _load(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{path} did not parse to a mapping"
    return data


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    assert PUBLISH_WF.exists(), f"missing workflow file: {PUBLISH_WF}"
    return _load(PUBLISH_WF)


@pytest.fixture(scope="module")
def publish_job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow missing top-level ``jobs`` mapping"
    assert "publish-pypi" in jobs, "workflow missing ``publish-pypi`` job"
    return jobs["publish-pypi"]


@pytest.fixture(scope="module")
def publish_steps(publish_job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = publish_job.get("steps")
    assert isinstance(steps, list) and steps, "``publish-pypi`` must have steps"
    return steps


def _pypi_publish_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the steps that invoke ``pypa/gh-action-pypi-publish``."""
    return [
        step
        for step in steps
        if isinstance(step.get("uses"), str)
        and step["uses"].startswith(_PYPI_ACTION_PREFIX)
    ]


def test_publish_workflow_parses(workflow: dict[str, Any]) -> None:
    """The workflow file must parse and declare the canonical top-level shape."""
    assert "name" in workflow, "publish.yml missing top-level ``name``"
    # YAML 1.1 coerces the bare key ``on`` to ``True``.
    assert "on" in workflow or True in workflow, "publish.yml missing ``on``"
    assert "jobs" in workflow, "publish.yml missing ``jobs``"


def test_publish_job_has_id_token_write(
    publish_job: dict[str, Any], workflow: dict[str, Any]
) -> None:
    """OIDC requires ``id-token: write`` at job (or workflow) scope."""
    job_perms = publish_job.get("permissions") or {}
    wf_perms = workflow.get("permissions") or {}
    assert isinstance(job_perms, dict), "job ``permissions`` must be a mapping"
    assert isinstance(wf_perms, dict), "workflow ``permissions`` must be a mapping"
    has_id_token = (
        job_perms.get("id-token") == "write"
        or wf_perms.get("id-token") == "write"
    )
    assert has_id_token, (
        "publish-pypi (or workflow) must declare ``permissions.id-token: write``"
        " for Trusted Publishing OIDC"
    )


def test_publish_job_has_oidc_step_without_password(
    publish_steps: list[dict[str, Any]],
) -> None:
    """First branch: pypi-publish action with NO ``password:`` field (OIDC)."""
    oidc_steps = [
        step
        for step in _pypi_publish_steps(publish_steps)
        if "password" not in (step.get("with") or {})
    ]
    assert oidc_steps, (
        "publish-pypi must include an OIDC step (pypa/gh-action-pypi-publish"
        " with no ``password:`` — the action will then use Trusted Publishing)"
    )


def test_publish_job_has_token_fallback_step(
    publish_steps: list[dict[str, Any]],
) -> None:
    """Second branch: pypi-publish action wired to ``secrets.PYPI_API_TOKEN``."""
    token_steps = [
        step
        for step in _pypi_publish_steps(publish_steps)
        if "PYPI_API_TOKEN" in str((step.get("with") or {}).get("password", ""))
    ]
    assert token_steps, (
        "publish-pypi must include a fallback step using"
        " ``password: ${{ secrets.PYPI_API_TOKEN }}``"
    )


def test_token_fallback_is_gated_on_oidc_outcome(
    publish_steps: list[dict[str, Any]],
) -> None:
    """The token step must only fire when the OIDC step did NOT succeed.

    Otherwise both steps would run on every tag and double-upload the
    same artifacts (``skip-existing: true`` would mask it, but the
    intent is to leave the token branch dormant once Trusted Publishing
    is registered).
    """
    token_steps = [
        step
        for step in _pypi_publish_steps(publish_steps)
        if "PYPI_API_TOKEN" in str((step.get("with") or {}).get("password", ""))
    ]
    assert token_steps, "token fallback step missing (see previous test)"
    for step in token_steps:
        condition = step.get("if")
        assert isinstance(condition, str) and condition.strip(), (
            "token fallback step must carry an ``if:`` gate referencing the"
            " OIDC step's outcome — found no condition"
        )
        # Must reference the OIDC step's outcome/conclusion.  We don't
        # care about the exact comparison form ("!= 'success'" vs
        # "== 'failure'" vs an OR chain), only that the gate exists.
        lowered = condition.lower()
        assert "steps." in lowered and (
            "outcome" in lowered or "conclusion" in lowered
        ), (
            "token fallback step's ``if:`` must reference"
            " ``steps.<oidc>.outcome`` / ``conclusion`` — got: "
            f"{condition!r}"
        )


def test_both_pypi_steps_use_skip_existing(
    publish_steps: list[dict[str, Any]],
) -> None:
    """Re-running on a previously-uploaded tag must be a no-op for both paths."""
    for step in _pypi_publish_steps(publish_steps):
        with_block = step.get("with") or {}
        assert with_block.get("skip-existing") is True, (
            "pypa/gh-action-pypi-publish step missing ``skip-existing: true``: "
            f"{step.get('name') or step.get('id') or step}"
        )
