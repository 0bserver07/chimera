"""``chimera otter stats`` — usage stats aggregated from the eventlog.

Surfaces token totals (input / output / cache / total), per-model
breakdown, per-window cost rollup, and lifetime totals across both
``mink-*`` and ``otter-*`` eventlog directories. Composes the existing
:func:`chimera.mink.cost.compute_summary` aggregator so the on-the-wire
JSON shape is a strict superset of ``chimera mink runs cost --format
json``.

Trademark hygiene: this module never names the upstream open-source
coding agent in user-visible source.

Subcommand
----------

``otter stats [--since 7d] [--model NAME] [--format text|json]``
    Print usage stats. ``--since`` accepts the same shorthand as
    :func:`chimera.mink.cost.parse_since` (``7d``, ``24h``, ``30m``,
    ISO-8601 dates). ``--model`` filters by case-insensitive substring.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "StatsReport",
    "compute_stats",
    "format_stats_text",
    "format_stats_json",
    "dispatch_stats",
]


@dataclass
class StatsReport:
    """A flat view of a usage-stats rollup.

    All numeric fields are zero-defaulted so the JSON shape is stable
    when the eventlog is empty.
    """

    total_runs: int
    successful_runs: int
    failed_runs: int
    total_cost_usd: float
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_tokens: int
    avg_cost_usd: float
    p50_cost_usd: float
    p95_cost_usd: float
    by_model: dict[str, dict[str, float | int]]
    since: str | None
    model_filter: str | None


def _iter_combined(eventlog_root: Path | None = None) -> Iterator[Any]:
    """Yield ``RunRecord`` instances across ``mink-*`` and ``otter-*``.

    Mirrors :meth:`chimera.otter.server.OtterServer._iter_run_records`
    but as a free function so the ``stats`` subcommand can call it
    without instantiating an HTTP server.
    """
    from chimera.mink.runs import (
        _read_summary,
        _summary_to_record,
        default_eventlog_root,
    )

    root = eventlog_root or default_eventlog_root()
    if not root.exists():
        return
    candidates = [
        p
        for p in root.iterdir()
        if p.is_dir()
        and (p.name.startswith("mink-") or p.name.startswith("otter-"))
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    for run_dir in candidates:
        summary = _read_summary(run_dir)
        if summary is None:
            continue
        # Otter writes ``session_id`` rather than ``run_id``; bridge so
        # ``_summary_to_record`` keys off a stable id.
        if "run_id" not in summary and "session_id" in summary:
            summary = dict(summary)
            summary["run_id"] = summary["session_id"]
        yield _summary_to_record(run_dir, summary)


def compute_stats(
    *,
    since: str | None = None,
    model: str | None = None,
    eventlog_root: Path | None = None,
) -> StatsReport:
    """Compute the rollup; raises :class:`ValueError` for bad ``since``."""
    from chimera.mink.cost import compute_summary, parse_since

    cutoff = parse_since(since)
    summary = compute_summary(
        _iter_combined(eventlog_root=eventlog_root),
        since=cutoff,
        since_label=since,
        model=model,
    )
    by_model: dict[str, dict[str, float | int]] = {}
    for name, bucket in summary.by_model.items():
        by_model[name] = {
            "runs": int(bucket["runs"]),
            "cost_usd": float(bucket["cost_usd"]),
            "tokens": int(bucket["tokens"]),
        }
    return StatsReport(
        total_runs=summary.total_runs,
        successful_runs=summary.successful_runs,
        failed_runs=summary.failed_runs,
        total_cost_usd=summary.total_cost_usd,
        total_tokens=summary.total_tokens,
        total_input_tokens=summary.total_input_tokens,
        total_output_tokens=summary.total_output_tokens,
        total_cache_tokens=summary.total_cache_tokens,
        avg_cost_usd=summary.avg_cost_usd,
        p50_cost_usd=summary.p50_cost_usd,
        p95_cost_usd=summary.p95_cost_usd,
        by_model=by_model,
        since=summary.since,
        model_filter=summary.model_filter,
    )


def _money(amount: float) -> str:
    return f"${amount:.4f}"


def format_stats_text(report: StatsReport) -> str:
    """Render a :class:`StatsReport` as a plain text block."""
    lines: list[str] = []
    title = "otter stats"
    if report.since:
        title += f" — since {report.since}"
    if report.model_filter:
        title += f" — model={report.model_filter}"
    lines.append(title)
    lines.append("=" * len(title))
    lines.append(f"runs                {report.total_runs}")
    lines.append(
        f"  success / fail    {report.successful_runs} / {report.failed_runs}"
    )
    lines.append(f"total cost          {_money(report.total_cost_usd)}")
    lines.append(f"avg cost / run      {_money(report.avg_cost_usd)}")
    lines.append(f"p50 cost            {_money(report.p50_cost_usd)}")
    lines.append(f"p95 cost            {_money(report.p95_cost_usd)}")
    lines.append("")
    lines.append("tokens")
    lines.append(f"  input             {report.total_input_tokens}")
    lines.append(f"  output            {report.total_output_tokens}")
    lines.append(f"  cache             {report.total_cache_tokens}")
    lines.append(f"  total             {report.total_tokens}")
    if report.by_model:
        lines.append("")
        lines.append("by model")
        # Stable order: by total cost descending, then by name.
        sorted_models = sorted(
            report.by_model.items(),
            key=lambda kv: (-float(kv[1].get("cost_usd", 0)), kv[0]),
        )
        for name, bucket in sorted_models:
            runs = int(bucket.get("runs", 0))
            cost = float(bucket.get("cost_usd", 0.0))
            tokens = int(bucket.get("tokens", 0))
            lines.append(
                f"  {name:<24}{runs:>4} runs   "
                f"{_money(cost):>12}   {tokens:>10} tokens"
            )
    return "\n".join(lines)


def format_stats_json(report: StatsReport) -> str:
    """Serialize a :class:`StatsReport` as deterministic JSON."""
    payload: dict[str, Any] = {
        "total_runs": report.total_runs,
        "successful_runs": report.successful_runs,
        "failed_runs": report.failed_runs,
        "total_cost_usd": report.total_cost_usd,
        "total_tokens": report.total_tokens,
        "total_input_tokens": report.total_input_tokens,
        "total_output_tokens": report.total_output_tokens,
        "total_cache_tokens": report.total_cache_tokens,
        "avg_cost_usd": report.avg_cost_usd,
        "p50_cost_usd": report.p50_cost_usd,
        "p95_cost_usd": report.p95_cost_usd,
        "by_model": report.by_model,
        "since": report.since,
        "model_filter": report.model_filter,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def dispatch_stats(args: argparse.Namespace) -> int:
    """Wire ``chimera otter stats``.

    Reads ``--stats-since``, ``--stats-model``, ``--stats-format`` from
    the otter parser; falls back to ``--sessions-since`` /
    ``--sessions-model`` (already wired by O3) so the legacy flags also
    flow through here.
    """
    since = (
        getattr(args, "stats_since", None)
        or getattr(args, "sessions_since", None)
    )
    model = (
        getattr(args, "stats_model", None)
        or getattr(args, "sessions_model", None)
    )
    fmt = (
        getattr(args, "stats_format", None)
        or getattr(args, "output_format", None)
        or "text"
    ).lower()
    if fmt == "stream-json":
        # ``stats`` is a single rollup; treat stream-json as plain json.
        fmt = "json"
    try:
        report = compute_stats(since=since, model=model)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if fmt == "json":
        print(format_stats_json(report))
    else:
        print(format_stats_text(report))
    return 0
