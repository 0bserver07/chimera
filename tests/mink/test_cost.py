"""``chimera mink runs cost`` — aggregation correctness across fake summaries.

Builds a tmp_path eventlog with hand-rolled ``summary.json`` files, points
the cost helpers at it, and asserts the resulting :class:`CostSummary` /
text / JSON / CSV outputs are correct. Exercises:

* Total runs / cost / token aggregation (including extra schema fields
  the lightweight ``RunRecord`` doesn't carry — input / output / cache).
* p50 / p95 stability on small corpora.
* ``--since`` shorthand (``7d``, ``24h``) and absolute ISO dates.
* ``--model`` substring filter (case-insensitive) and ``"all"`` no-op.
* ``--limit N`` truncates the newest-first stream.
* ``--format json`` schema and ``--format csv`` columns.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.mink import cost as cost_mod
from chimera.mink import runs as runs_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _utc_iso(dt: datetime.datetime) -> str:
    """Render a UTC datetime as the ``Z``-suffixed ISO-8601 form summaries use."""
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_summary(
    root: Path,
    run_id: str,
    *,
    started_at: str,
    model: str = "stub-model",
    cost_usd: float = 0.001,
    success: bool = True,
    steps: int = 1,
    total_tokens: int = 0,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write one fake mink run dir with a ``summary.json``."""
    run_dir = root / run_id
    run_dir.mkdir()
    payload: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": started_at,
        "model": model,
        "prompt": f"prompt for {run_id}",
        "cwd": "/tmp",
        "permission_mode": "default",
        "steps": steps,
        "tool_calls_total": 0,
        "success": success,
        "cost_usd": cost_usd,
        "total_tokens": total_tokens,
        "error": None,
    }
    if extra:
        payload.update(extra)
    (run_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return run_dir


@pytest.fixture
def eventlog(tmp_path: Path) -> Path:
    """Empty eventlog root the cost helpers walk via ``eventlog_root=``."""
    root = tmp_path / "eventlog"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


class TestParseSince:
    def test_none_and_empty_return_none(self) -> None:
        assert cost_mod.parse_since(None) is None
        assert cost_mod.parse_since("") is None
        assert cost_mod.parse_since("   ") is None

    def test_shorthand_days(self) -> None:
        cutoff = cost_mod.parse_since("7d")
        assert cutoff is not None
        assert cutoff.tzinfo is not None
        delta = datetime.datetime.now(datetime.timezone.utc) - cutoff
        # Allow a generous slack so the assertion isn't flaky in CI.
        assert datetime.timedelta(days=6, hours=23) <= delta <= datetime.timedelta(days=7, hours=1)

    def test_shorthand_hours_and_minutes(self) -> None:
        h = cost_mod.parse_since("24h")
        m = cost_mod.parse_since("30m")
        assert h is not None and m is not None
        now = datetime.datetime.now(datetime.timezone.utc)
        assert datetime.timedelta(hours=23) <= now - h <= datetime.timedelta(hours=25)
        assert datetime.timedelta(minutes=29) <= now - m <= datetime.timedelta(minutes=31)

    def test_iso_date(self) -> None:
        cutoff = cost_mod.parse_since("2026-04-20")
        assert cutoff is not None
        assert cutoff.year == 2026 and cutoff.month == 4 and cutoff.day == 20
        assert cutoff.tzinfo is not None

    def test_iso_z_suffix(self) -> None:
        cutoff = cost_mod.parse_since("2026-04-20T12:00:00Z")
        assert cutoff is not None
        assert cutoff.hour == 12

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            cost_mod.parse_since("not-a-date")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestComputeSummary:
    def test_empty_eventlog(self, eventlog: Path) -> None:
        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog))
        assert summary.total_runs == 0
        assert summary.total_cost_usd == 0.0
        assert summary.avg_cost_usd == 0.0
        assert summary.p50_cost_usd == 0.0
        assert summary.p95_cost_usd == 0.0
        assert summary.by_model == {}
        assert summary.rows == []

    def test_basic_totals(self, eventlog: Path) -> None:
        # Three runs at different costs and models.
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260420T100000-aaaaaaaa",
            started_at=_utc_iso(now - datetime.timedelta(hours=2)),
            model="kimi-k2.6:cloud",
            cost_usd=0.01,
            total_tokens=1000,
        )
        _make_summary(
            eventlog,
            "mink-20260420T100100-bbbbbbbb",
            started_at=_utc_iso(now - datetime.timedelta(hours=1)),
            model="GLM-5",
            cost_usd=0.05,
            total_tokens=2500,
        )
        _make_summary(
            eventlog,
            "mink-20260420T100200-cccccccc",
            started_at=_utc_iso(now),
            model="GLM-5",
            cost_usd=0.04,
            total_tokens=2000,
            success=False,
        )

        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog))
        assert summary.total_runs == 3
        assert summary.successful_runs == 2
        assert summary.failed_runs == 1
        assert summary.total_cost_usd == pytest.approx(0.10)
        assert summary.total_tokens == 5500
        assert summary.avg_cost_usd == pytest.approx(0.10 / 3)
        # Median of [0.01, 0.04, 0.05] is 0.04.
        assert summary.p50_cost_usd == pytest.approx(0.04)
        # Nearest-rank p95 on a 3-row corpus = the largest value.
        assert summary.p95_cost_usd == pytest.approx(0.05)

        # Breakdown by model.
        assert set(summary.by_model.keys()) == {"kimi-k2.6:cloud", "GLM-5"}
        assert summary.by_model["GLM-5"]["runs"] == 2
        assert summary.by_model["GLM-5"]["cost_usd"] == pytest.approx(0.09)
        assert summary.by_model["kimi-k2.6:cloud"]["cost_usd"] == pytest.approx(0.01)

    def test_extra_token_fields_surface(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260420T100000-aaaaaaaa",
            started_at=_utc_iso(now),
            cost_usd=0.02,
            total_tokens=500,
            extra={
                "input_tokens": 300,
                "output_tokens": 150,
                "cache_read_input_tokens": 50,
            },
        )
        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog))
        assert summary.total_input_tokens == 300
        assert summary.total_output_tokens == 150
        assert summary.total_cache_tokens == 50
        assert summary.rows[0].input_tokens == 300
        assert summary.rows[0].cache_tokens == 50

    def test_since_shorthand_filters_old(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260101T000000-aaaaaaaa",
            started_at=_utc_iso(now - datetime.timedelta(days=10)),
            cost_usd=1.00,
        )
        _make_summary(
            eventlog,
            "mink-20260420T100100-bbbbbbbb",
            started_at=_utc_iso(now - datetime.timedelta(hours=1)),
            cost_usd=0.10,
        )
        cutoff = cost_mod.parse_since("7d")
        summary = cost_mod.compute_summary(
            runs_mod.iter_runs(eventlog),
            since=cutoff,
            since_label="7d",
        )
        assert summary.total_runs == 1
        assert summary.total_cost_usd == pytest.approx(0.10)
        assert summary.since == "7d"

    def test_since_iso_date_filters(self, eventlog: Path) -> None:
        _make_summary(
            eventlog,
            "mink-20260101T000000-aaaaaaaa",
            started_at="2026-01-01T00:00:00Z",
            cost_usd=1.00,
        )
        _make_summary(
            eventlog,
            "mink-20260501T000000-bbbbbbbb",
            started_at="2026-05-01T00:00:00Z",
            cost_usd=2.00,
        )
        cutoff = cost_mod.parse_since("2026-03-01")
        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog), since=cutoff)
        assert summary.total_runs == 1
        assert summary.rows[0].run_id.startswith("mink-20260501")

    def test_model_filter_substring_case_insensitive(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260420T100000-aaaaaaaa",
            started_at=_utc_iso(now),
            model="kimi-k2.6:cloud",
            cost_usd=0.01,
        )
        _make_summary(
            eventlog,
            "mink-20260420T100100-bbbbbbbb",
            started_at=_utc_iso(now),
            model="glm-5",
            cost_usd=0.02,
        )
        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog), model="GLM")
        assert summary.total_runs == 1
        assert summary.rows[0].model == "glm-5"
        assert summary.model_filter == "GLM"

    def test_model_all_keeps_everyone(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260420T100000-aaaaaaaa",
            started_at=_utc_iso(now),
            model="a",
        )
        _make_summary(
            eventlog,
            "mink-20260420T100100-bbbbbbbb",
            started_at=_utc_iso(now),
            model="b",
        )
        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog), model="all")
        assert summary.total_runs == 2
        assert summary.model_filter is None

    def test_limit_caps_after_filter(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        for i in range(5):
            _make_summary(
                eventlog,
                f"mink-2026042{i}T100000-aaaaaaaa",
                started_at=_utc_iso(now - datetime.timedelta(days=i)),
                cost_usd=0.01 * (i + 1),
            )
        summary = cost_mod.compute_summary(runs_mod.iter_runs(eventlog), limit=2)
        assert summary.total_runs == 2
        # iter_runs returns newest first lexically — so the first two run ids
        # are the highest-numbered (i=4, i=3).
        assert summary.rows[0].run_id.endswith("4T100000-aaaaaaaa")


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


class TestFormats:
    def _two_run_summary(self, eventlog: Path) -> cost_mod.CostSummary:
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260420T100000-aaaaaaaa",
            started_at=_utc_iso(now),
            model="GLM-5",
            cost_usd=0.05,
            total_tokens=2000,
        )
        _make_summary(
            eventlog,
            "mink-20260420T100100-bbbbbbbb",
            started_at=_utc_iso(now),
            model="GLM-5",
            cost_usd=0.03,
            total_tokens=1500,
            success=False,
        )
        return cost_mod.compute_summary(runs_mod.iter_runs(eventlog))

    def test_text_plain_renders(self, eventlog: Path) -> None:
        summary = self._two_run_summary(eventlog)
        text = cost_mod.format_text(summary, use_rich=False)
        assert "mink runs cost" in text
        assert "runs:" in text
        assert "$0.0800" in text  # total cost
        assert "GLM-5" in text

    def test_json_schema(self, eventlog: Path) -> None:
        summary = self._two_run_summary(eventlog)
        payload = json.loads(cost_mod.format_json(summary))
        assert set(payload.keys()) == {"totals", "filters", "by_model", "rows"}
        totals = payload["totals"]
        assert totals["runs"] == 2
        assert totals["successful_runs"] == 1
        assert totals["failed_runs"] == 1
        assert totals["cost_usd"] == pytest.approx(0.08)
        assert totals["tokens"] == 3500
        assert payload["filters"]["since"] is None
        assert payload["filters"]["model"] is None
        assert "GLM-5" in payload["by_model"]
        assert payload["by_model"]["GLM-5"]["runs"] == 2
        assert len(payload["rows"]) == 2
        assert {row["run_id"] for row in payload["rows"]} == {
            "mink-20260420T100000-aaaaaaaa",
            "mink-20260420T100100-bbbbbbbb",
        }

    def test_csv_columns_and_rows(self, eventlog: Path) -> None:
        summary = self._two_run_summary(eventlog)
        text = cost_mod.format_csv(summary)
        reader = csv.DictReader(io.StringIO(text))
        assert reader.fieldnames == [
            "run_id",
            "started_at",
            "model",
            "cost_usd",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "success",
            "steps",
        ]
        rows = list(reader)
        assert len(rows) == 2
        assert {r["run_id"] for r in rows} == {
            "mink-20260420T100000-aaaaaaaa",
            "mink-20260420T100100-bbbbbbbb",
        }
        # cost_usd column is formatted to 6 decimals.
        assert any(r["cost_usd"] == "0.050000" for r in rows)


# ---------------------------------------------------------------------------
# run_cost top-level
# ---------------------------------------------------------------------------


class TestRunCost:
    def test_unknown_format_returns_usage_error(self, eventlog: Path) -> None:
        rc, out = cost_mod.run_cost(fmt="xml", eventlog_root=eventlog)
        assert rc == 2
        assert "format" in out.lower()

    def test_invalid_since_returns_usage_error(self, eventlog: Path) -> None:
        rc, out = cost_mod.run_cost(since="not-a-date", eventlog_root=eventlog)
        assert rc == 2
        assert "since" in out.lower() or "date" in out.lower()

    def test_text_output_empty_eventlog(self, eventlog: Path) -> None:
        rc, out = cost_mod.run_cost(eventlog_root=eventlog, use_rich=False)
        assert rc == 0
        assert "runs:" in out

    def test_json_output(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        _make_summary(
            eventlog,
            "mink-20260420T100000-aaaaaaaa",
            started_at=_utc_iso(now),
            cost_usd=0.07,
        )
        rc, out = cost_mod.run_cost(fmt="json", eventlog_root=eventlog)
        assert rc == 0
        payload = json.loads(out)
        assert payload["totals"]["runs"] == 1
        assert payload["totals"]["cost_usd"] == pytest.approx(0.07)

    def test_limit_zero_means_no_cap(self, eventlog: Path) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        for i in range(3):
            _make_summary(
                eventlog,
                f"mink-2026042{i}T100000-aaaaaaaa",
                started_at=_utc_iso(now),
            )
        rc, out = cost_mod.run_cost(fmt="json", limit=0, eventlog_root=eventlog)
        assert rc == 0
        payload = json.loads(out)
        assert payload["totals"]["runs"] == 3
