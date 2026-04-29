"""Tests for the otter HTTP server's ``/runs`` + ``/runs/cost`` routes.

Wave-4 (L6) lifts the M4 ``chimera mink runs cost`` aggregation onto
:class:`chimera.otter.server.OtterServer` so a TUI / IDE / web client
driving over HTTP can pull cost rollups for runs persisted under both
``~/.chimera/eventlog/mink-*`` and ``~/.chimera/eventlog/otter-*``
without shelling out to the CLI.

These tests use ``tmp_path`` with synthetic ``summary.json`` fixtures so
the routes never touch the developer's real ``~/.chimera/eventlog``. The
test driver speaks plain :mod:`urllib.request` to keep parity with the
rest of ``tests/otter/test_server*.py``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

from chimera.otter.server import OtterServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_summary(
    eventlog_root: Path,
    run_id: str,
    *,
    model: str,
    cost_usd: float,
    total_tokens: int,
    success: bool = True,
    started_at: str = "2026-04-25T12:00:00Z",
    ended_at: str = "2026-04-25T12:01:00Z",
    steps: int = 3,
    tool_calls_total: int = 2,
    prompt: str = "do the thing",
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a synthetic ``<run_id>/summary.json`` under ``eventlog_root``.

    Returns the run directory so callers can drop additional fixture
    files into it if a future test wants to exercise the ``event-*.json``
    walk too. We only write ``summary.json`` here because every public
    surface under test (`/runs`, `/runs/cost`) reads from that file
    only — matching :func:`chimera.mink.runs.iter_runs`.
    """
    run_dir = eventlog_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "model": model,
        "prompt": prompt,
        "cwd": "/tmp",
        "permission_mode": "auto",
        "steps": steps,
        "tool_calls_total": tool_calls_total,
        "success": success,
        "cost_usd": cost_usd,
        "total_tokens": total_tokens,
    }
    if extra:
        payload.update(extra)
    (run_dir / "summary.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


@pytest.fixture()
def eventlog_root(tmp_path: Path) -> Path:
    """Lay down a small synthetic corpus mixing ``mink-*`` and ``otter-*``.

    The four fixtures cover:

    * ``mink-...glm`` — a successful glm-5 run.
    * ``mink-...claude`` — a failed claude run (older).
    * ``otter-...glm`` — a successful glm-5.1 otter run with token-breakdown.
    * ``otter-...claude`` — a successful claude otter run.

    A stray ``foo-...`` directory and an empty directory are also dropped
    in to verify the walk only matches the two expected prefixes and
    skips dirs that don't carry a parseable ``summary.json``.
    """
    root = tmp_path / "eventlog"
    root.mkdir()

    # Newest first by run-id descending — match the production ordering.
    _write_summary(
        root,
        "otter-20260425T120300-aaaa1111",
        model="glm-5.1:cloud",
        cost_usd=0.07,
        total_tokens=1200,
        started_at="2026-04-25T12:03:00Z",
        ended_at="2026-04-25T12:03:30Z",
        steps=4,
        tool_calls_total=3,
        extra={
            "input_tokens": 800,
            "output_tokens": 350,
            "cache_read_input_tokens": 50,
        },
    )
    _write_summary(
        root,
        "otter-20260425T120200-bbbb2222",
        model="claude-sonnet-4-6",
        cost_usd=0.12,
        total_tokens=2000,
        started_at="2026-04-25T12:02:00Z",
        ended_at="2026-04-25T12:02:45Z",
    )
    _write_summary(
        root,
        "mink-20260425T120100-cccc3333",
        model="glm-5.1:cloud",
        cost_usd=0.03,
        total_tokens=600,
        started_at="2026-04-25T12:01:00Z",
        ended_at="2026-04-25T12:01:15Z",
        steps=2,
    )
    _write_summary(
        root,
        "mink-20260424T080000-dddd4444",
        model="claude-sonnet-4-6",
        cost_usd=0.0,
        total_tokens=0,
        success=False,
        started_at="2026-04-24T08:00:00Z",
        ended_at="2026-04-24T08:00:30Z",
        extra={"error": "simulated failure"},
    )

    # Decoy directories the walk must skip.
    (root / "foo-not-a-run").mkdir()
    (root / "mink-empty-no-summary").mkdir()

    return root


@pytest.fixture()
def server(eventlog_root: Path) -> Iterator[OtterServer]:
    """Spin up :class:`OtterServer` pointed at the synthetic eventlog root."""
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        eventlog_root=eventlog_root,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


@pytest.fixture()
def auth_server(eventlog_root: Path) -> Iterator[OtterServer]:
    """Same fixture, but with bearer auth on for the auth-flow checks."""
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        auth_token="runs-secret",
        eventlog_root=eventlog_root,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url(srv: OtterServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Stdlib-only GET / POST helper that returns ``(status, json_body)``."""
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}
    raw = resp.read()
    return resp.status, json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# GET /runs
# ---------------------------------------------------------------------------


def test_runs_list_returns_both_prefixes(server: OtterServer) -> None:
    """``/runs`` walks ``mink-*`` AND ``otter-*`` directories, newest first."""
    status, body = _http_json("GET", f"{_base_url(server)}/runs")
    assert status == 200
    assert body["total_runs"] == 4
    run_ids = [r["run_id"] for r in body["runs"]]
    # Lexical-desc on ``<prefix>-<UTC>-<uuid>`` keys — otter > mink for
    # the same UTC so otter rows lead, then the older mink rows.
    assert run_ids == [
        "otter-20260425T120300-aaaa1111",
        "otter-20260425T120200-bbbb2222",
        "mink-20260425T120100-cccc3333",
        "mink-20260424T080000-dddd4444",
    ]
    # Each row carries a ``source`` so clients can tell the corpora apart.
    sources = {r["run_id"]: r["source"] for r in body["runs"]}
    assert sources["otter-20260425T120300-aaaa1111"] == "otter"
    assert sources["mink-20260425T120100-cccc3333"] == "mink"


def test_runs_list_honors_model_filter(server: OtterServer) -> None:
    """``model=glm`` substring-filters the corpus (case-insensitive)."""
    status, body = _http_json(
        "GET", f"{_base_url(server)}/runs?model=glm-5.1:cloud"
    )
    assert status == 200
    run_ids = [r["run_id"] for r in body["runs"]]
    assert run_ids == [
        "otter-20260425T120300-aaaa1111",
        "mink-20260425T120100-cccc3333",
    ]


def test_runs_list_honors_limit(server: OtterServer) -> None:
    """``limit=2`` caps the row count to the two newest entries."""
    status, body = _http_json("GET", f"{_base_url(server)}/runs?limit=2")
    assert status == 200
    assert body["total_runs"] == 2
    run_ids = [r["run_id"] for r in body["runs"]]
    assert run_ids == [
        "otter-20260425T120300-aaaa1111",
        "otter-20260425T120200-bbbb2222",
    ]


def test_runs_list_rejects_bad_limit(server: OtterServer) -> None:
    """Non-integer ``limit`` -> 400 (rather than a 500 from int())."""
    status, body = _http_json("GET", f"{_base_url(server)}/runs?limit=nope")
    assert status == 400
    assert body["error"] == "invalid_query"
    assert "limit" in body["detail"]


def test_runs_list_rejects_bad_since(server: OtterServer) -> None:
    """Malformed ``since`` -> 400 with the parser's diagnostic surfaced."""
    status, body = _http_json(
        "GET", f"{_base_url(server)}/runs?since=not-a-date"
    )
    assert status == 400
    assert body["error"] == "invalid_query"


def test_runs_list_with_empty_root(tmp_path: Path) -> None:
    """A non-existent root yields ``{total_runs: 0, runs: []}`` (not 500)."""
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        eventlog_root=tmp_path / "does-not-exist",
    )
    srv.start(blocking=False)
    try:
        status, body = _http_json("GET", f"{_base_url(srv)}/runs")
    finally:
        srv.shutdown()
    assert status == 200
    assert body == {"total_runs": 0, "runs": []}


# ---------------------------------------------------------------------------
# GET /runs/cost
# ---------------------------------------------------------------------------


def test_runs_cost_aggregates_across_prefixes(server: OtterServer) -> None:
    """Top-level totals span every ``mink-*`` + ``otter-*`` run."""
    status, body = _http_json("GET", f"{_base_url(server)}/runs/cost")
    assert status == 200
    assert body["total_runs"] == 4
    # 0.07 + 0.12 + 0.03 + 0.0 == 0.22 (allow float fuzz).
    assert body["total_cost"] == pytest.approx(0.22, rel=1e-6)
    # 1200 + 2000 + 600 + 0 == 3800
    assert body["total_tokens"] == 3800
    # Per-model bucket carries runs / cost / tokens.
    assert set(body["by_model"].keys()) == {"glm-5.1:cloud", "claude-sonnet-4-6"}
    glm = body["by_model"]["glm-5.1:cloud"]
    assert glm["runs"] == 2
    assert glm["cost_usd"] == pytest.approx(0.10, rel=1e-6)
    assert glm["tokens"] == 1800
    # ``by_run`` carries one row per filtered record with full token detail.
    by_run = {row["run_id"]: row for row in body["by_run"]}
    otter_glm = by_run["otter-20260425T120300-aaaa1111"]
    assert otter_glm["input_tokens"] == 800
    assert otter_glm["output_tokens"] == 350
    assert otter_glm["cache_tokens"] == 50
    assert otter_glm["source"] == "otter"


def test_runs_cost_model_filter_narrows_totals(server: OtterServer) -> None:
    """Filtering by model collapses the totals to that bucket only."""
    status, body = _http_json(
        "GET", f"{_base_url(server)}/runs/cost?model=glm-5.1:cloud"
    )
    assert status == 200
    assert body["total_runs"] == 2
    assert body["total_cost"] == pytest.approx(0.10, rel=1e-6)
    assert set(body["by_model"].keys()) == {"glm-5.1:cloud"}
    assert body["filters"]["model"] == "glm-5.1:cloud"


def test_runs_cost_since_filter_drops_old_rows(server: OtterServer) -> None:
    """``since=2026-04-25`` drops the 04-24 mink failure."""
    status, body = _http_json(
        "GET", f"{_base_url(server)}/runs/cost?since=2026-04-25"
    )
    assert status == 200
    assert body["total_runs"] == 3
    run_ids = [row["run_id"] for row in body["by_run"]]
    assert "mink-20260424T080000-dddd4444" not in run_ids
    assert body["filters"]["since"] == "2026-04-25"


def test_runs_cost_limit_caps_rows(server: OtterServer) -> None:
    """``limit=1`` keeps only the newest record across both corpora."""
    status, body = _http_json(
        "GET", f"{_base_url(server)}/runs/cost?limit=1"
    )
    assert status == 200
    assert body["total_runs"] == 1
    assert body["by_run"][0]["run_id"] == "otter-20260425T120300-aaaa1111"


def test_runs_cost_totals_block_matches_mink_schema(server: OtterServer) -> None:
    """The ``totals`` block is a strict superset of ``mink runs cost --json``."""
    status, body = _http_json("GET", f"{_base_url(server)}/runs/cost")
    assert status == 200
    totals = body["totals"]
    # Schema parity with ``chimera.mink.cost.format_json``.
    assert totals["runs"] == 4
    assert totals["successful_runs"] == 3
    assert totals["failed_runs"] == 1
    assert "p50_cost_usd" in totals
    assert "p95_cost_usd" in totals
    assert "avg_cost_usd" in totals


def test_runs_cost_combined_query(server: OtterServer) -> None:
    """``since`` + ``model`` + ``limit`` compose without surprises."""
    status, body = _http_json(
        "GET",
        f"{_base_url(server)}/runs/cost"
        "?since=2026-04-25&model=glm-5.1:cloud&limit=50",
    )
    assert status == 200
    assert body["total_runs"] == 2
    assert body["filters"]["since"] == "2026-04-25"
    assert body["filters"]["model"] == "glm-5.1:cloud"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_runs_routes_require_auth(auth_server: OtterServer) -> None:
    """Both routes 401 without the bearer."""
    status, body = _http_json("GET", f"{_base_url(auth_server)}/runs")
    assert status == 401
    assert body == {"error": "unauthorized"}

    status, body = _http_json("GET", f"{_base_url(auth_server)}/runs/cost")
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_runs_routes_accept_valid_bearer(auth_server: OtterServer) -> None:
    """With the right bearer, both routes return 200 and the synthetic data."""
    headers = {"Authorization": "Bearer runs-secret"}
    status, body = _http_json(
        "GET", f"{_base_url(auth_server)}/runs", headers=headers
    )
    assert status == 200
    assert body["total_runs"] == 4

    status, body = _http_json(
        "GET", f"{_base_url(auth_server)}/runs/cost", headers=headers
    )
    assert status == 200
    assert body["total_runs"] == 4
