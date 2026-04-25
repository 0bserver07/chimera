"""``chimera mink runs cost`` — aggregate cost across persisted mink runs.

Walks ``~/.chimera/eventlog/mink-*/summary.json`` files (the same corpus
``runs list/show/share`` uses) and emits cost rollups in human-readable,
JSON, or CSV form. Schema fields read are exactly the keys
:func:`chimera.mink.cli._write_summary_json` writes today: ``run_id``,
``started_at``, ``ended_at``, ``model``, ``prompt``, ``cwd``,
``permission_mode``, ``steps``, ``tool_calls_total``, ``success``,
``cost_usd``, ``total_tokens``, ``error``. No fields are invented; if a
future run schema gains ``input_tokens`` / ``cache_*_tokens`` we surface
them when present and fall back to zero when absent.

Stdlib only (csv, datetime, io, json, statistics) so this module ships
with the zero-dep core. Rich is used opportunistically for the table
view when installed; ``--format text`` always works without it.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from chimera.mink.runs import RunRecord, default_eventlog_root, iter_runs

__all__ = [
    "CostRow",
    "CostSummary",
    "parse_since",
    "filter_records",
    "compute_summary",
    "format_text",
    "format_json",
    "format_csv",
    "run_cost",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CostRow:
    """One row in the per-run cost table.

    Attributes:
        run_id: Directory name (``mink-<utc>-<uuid>``).
        started_at: ISO-8601 UTC start timestamp from ``summary.json``.
        model: Provider model name actually used.
        cost_usd: Run cost in USD (``0.0`` when unknown).
        total_tokens: Total tokens reported (``0`` when unknown).
        input_tokens: Input tokens when the run schema reports them, else 0.
        output_tokens: Output tokens when the run schema reports them, else 0.
        cache_tokens: Cached / cache-read tokens when reported, else 0.
        success: Whether the loop reported ``success=True``.
        steps: Number of ReAct steps executed.
    """

    run_id: str
    started_at: str
    model: str
    cost_usd: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_tokens: int
    success: bool
    steps: int


@dataclass
class CostSummary:
    """Aggregate cost rollup for a filtered set of runs.

    Attributes:
        total_runs: Count of runs included after filtering.
        successful_runs: Count where ``success=True``.
        failed_runs: ``total_runs - successful_runs``.
        total_cost_usd: Sum of ``cost_usd``.
        total_tokens: Sum of ``total_tokens``.
        total_input_tokens: Sum of ``input_tokens`` (zero unless schema reports them).
        total_output_tokens: Sum of ``output_tokens``.
        total_cache_tokens: Sum of ``cache_tokens``.
        avg_cost_usd: Mean cost per run; ``0.0`` for empty input.
        p50_cost_usd: Median cost per run; ``0.0`` for empty input.
        p95_cost_usd: 95th-percentile cost per run; ``0.0`` for empty input.
        by_model: Breakdown ``{model: {runs, cost_usd, tokens}}``.
        rows: Per-run :class:`CostRow` list (newest first).
        since: Echo of the parsed ``--since`` window (None when unset).
        model_filter: Echo of the ``--model`` filter (None when ``"all"``).
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
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    rows: list[CostRow] = field(default_factory=list)
    since: str | None = None
    model_filter: str | None = None


# ---------------------------------------------------------------------------
# --since parsing
# ---------------------------------------------------------------------------


_SHORTHAND_RE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)


def parse_since(value: str | None) -> datetime.datetime | None:
    """Parse a ``--since`` argument into a UTC ``datetime`` cutoff.

    Accepts:

    * ``None`` / empty string → ``None`` (no cutoff).
    * ``"7d"`` / ``"24h"`` / ``"30m"`` shorthand (case-insensitive).
    * Absolute ISO-8601 dates like ``"2026-04-20"`` or
      ``"2026-04-20T12:00:00Z"`` — anything :func:`datetime.fromisoformat`
      accepts after stripping a trailing ``Z``.

    Args:
        value: Raw CLI string or ``None``.

    Returns:
        A timezone-aware UTC :class:`datetime.datetime` or ``None``.

    Raises:
        ValueError: When ``value`` is non-empty but matches neither form.
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    m = _SHORTHAND_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        deltas = {
            "d": datetime.timedelta(days=n),
            "h": datetime.timedelta(hours=n),
            "m": datetime.timedelta(minutes=n),
        }
        return datetime.datetime.now(datetime.timezone.utc) - deltas[unit]
    iso = s[:-1] if s.endswith("Z") else s
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except ValueError as exc:  # pragma: no cover - re-raised below
        raise ValueError(
            f"--since {value!r} is neither shorthand (e.g. '7d', '24h', '30m') "
            "nor an ISO-8601 date"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _parse_started_at(iso: str) -> datetime.datetime | None:
    """Best-effort parse of a ``summary.json`` ``started_at`` to UTC datetime."""
    if not iso:
        return None
    raw = iso[:-1] if iso.endswith("Z") else iso
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Filtering and aggregation
# ---------------------------------------------------------------------------


def _row_from_record(rec: RunRecord) -> CostRow:
    """Promote a :class:`RunRecord` to a :class:`CostRow`.

    Re-reads ``summary.json`` once to pick up optional token-breakdown
    fields the lightweight :class:`RunRecord` does not carry. Missing
    fields default to ``0`` so we never invent data.
    """
    summary_path = rec.path / "summary.json"
    extra: dict[str, Any] = {}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            extra = data
    except (OSError, json.JSONDecodeError):
        extra = {}

    def _int_field(*keys: str) -> int:
        for k in keys:
            v = extra.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return 0

    total_tokens = _int_field("total_tokens", "tokens_total")
    input_tokens = _int_field("input_tokens", "prompt_tokens", "tokens_input")
    output_tokens = _int_field("output_tokens", "completion_tokens", "tokens_output")
    cache_tokens = _int_field(
        "cache_tokens",
        "cache_read_input_tokens",
        "cache_read_tokens",
        "cached_tokens",
    )

    return CostRow(
        run_id=rec.run_id,
        started_at=rec.started_at,
        model=rec.model,
        cost_usd=rec.cost_usd,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        success=rec.success,
        steps=rec.steps,
    )


def filter_records(
    records: Iterable[RunRecord],
    *,
    since: datetime.datetime | None = None,
    model: str | None = None,
    limit: int | None = None,
) -> list[RunRecord]:
    """Apply ``--since`` / ``--model`` / ``--limit`` to a record stream.

    Args:
        records: Newest-first iterable of :class:`RunRecord`.
        since: Drop runs whose ``started_at`` is older than this UTC
            datetime. Records with unparseable timestamps are always kept
            (we'd rather over-report than silently lose data).
        model: When set, keep only runs whose model name contains this
            substring (case-insensitive). ``"all"`` and ``None`` skip the
            filter entirely.
        limit: When > 0, cap to the N most-recent records *after* the
            other filters.

    Returns:
        A list of :class:`RunRecord` in the same order as ``records``.
    """
    out: list[RunRecord] = []
    model_norm = (model or "").strip().lower()
    for r in records:
        if since is not None:
            ts = _parse_started_at(r.started_at)
            if ts is not None and ts < since:
                continue
        if model_norm and model_norm != "all":
            if model_norm not in (r.model or "").lower():
                continue
        out.append(r)
    if limit is not None and limit > 0:
        out = out[:limit]
    return out


def _percentile(values: Sequence[float], pct: float) -> float:
    """Inclusive nearest-rank percentile; ``0.0`` for empty input.

    Uses ``statistics.quantiles`` when there are enough samples and falls
    back to the largest value for ``p95`` of tiny lists so the output is
    stable on small corpora (the common case for early adopters).
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_v = sorted(values)
    if pct <= 0:
        return float(sorted_v[0])
    if pct >= 100:
        return float(sorted_v[-1])
    # Nearest-rank: rank = ceil(pct/100 * N).
    rank = max(1, int(round(pct / 100.0 * len(sorted_v))))
    rank = min(rank, len(sorted_v))
    return float(sorted_v[rank - 1])


def compute_summary(
    records: Iterable[RunRecord],
    *,
    since: datetime.datetime | None = None,
    since_label: str | None = None,
    model: str | None = None,
    limit: int | None = None,
) -> CostSummary:
    """Build a :class:`CostSummary` from a record stream.

    Filtering happens here so callers can pass the raw ``iter_runs()``
    generator and let this function consolidate the policy.
    """
    filtered = filter_records(records, since=since, model=model, limit=limit)
    rows = [_row_from_record(r) for r in filtered]

    costs = [row.cost_usd for row in rows]
    total_cost = float(sum(costs))
    total_runs = len(rows)
    successful = sum(1 for row in rows if row.success)
    avg_cost = (total_cost / total_runs) if total_runs else 0.0
    p50 = float(statistics.median(costs)) if costs else 0.0
    p95 = _percentile(costs, 95)

    by_model: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = by_model.setdefault(
            row.model or "(unknown)",
            {"runs": 0.0, "cost_usd": 0.0, "tokens": 0.0},
        )
        bucket["runs"] += 1
        bucket["cost_usd"] += row.cost_usd
        bucket["tokens"] += row.total_tokens

    model_filter: str | None
    if model is None or model.strip().lower() in {"", "all"}:
        model_filter = None
    else:
        model_filter = model

    return CostSummary(
        total_runs=total_runs,
        successful_runs=successful,
        failed_runs=total_runs - successful,
        total_cost_usd=total_cost,
        total_tokens=sum(row.total_tokens for row in rows),
        total_input_tokens=sum(row.input_tokens for row in rows),
        total_output_tokens=sum(row.output_tokens for row in rows),
        total_cache_tokens=sum(row.cache_tokens for row in rows),
        avg_cost_usd=avg_cost,
        p50_cost_usd=p50,
        p95_cost_usd=p95,
        by_model=by_model,
        rows=rows,
        since=since_label,
        model_filter=model_filter,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_money(amount: float) -> str:
    """Format USD with 4-decimal precision (matches ``runs list`` style)."""
    return f"${amount:.4f}"


def _try_rich_table(summary: CostSummary) -> str | None:
    """Render with rich when installed; return ``None`` to fall back to plain.

    Kept opportunistic — rich is an optional extra and the plain renderer
    must remain the primary path so this command works in zero-dep envs.
    """
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        return None

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)

    overview = Table(title="mink runs cost")
    overview.add_column("metric", style="bold")
    overview.add_column("value", justify="right")
    overview.add_row("runs", str(summary.total_runs))
    overview.add_row(
        "success / fail",
        f"{summary.successful_runs} / {summary.failed_runs}",
    )
    overview.add_row("total cost", _format_money(summary.total_cost_usd))
    overview.add_row("avg cost / run", _format_money(summary.avg_cost_usd))
    overview.add_row("p50 cost", _format_money(summary.p50_cost_usd))
    overview.add_row("p95 cost", _format_money(summary.p95_cost_usd))
    overview.add_row("total tokens", str(summary.total_tokens))
    if summary.total_input_tokens or summary.total_output_tokens or summary.total_cache_tokens:
        overview.add_row(
            "input / output / cache tokens",
            f"{summary.total_input_tokens} / {summary.total_output_tokens} / {summary.total_cache_tokens}",
        )
    if summary.since:
        overview.add_row("since", summary.since)
    if summary.model_filter:
        overview.add_row("model filter", summary.model_filter)
    console.print(overview)

    if summary.by_model:
        per_model = Table(title="by model")
        per_model.add_column("model")
        per_model.add_column("runs", justify="right")
        per_model.add_column("cost", justify="right")
        per_model.add_column("tokens", justify="right")
        for name in sorted(summary.by_model, key=lambda k: -summary.by_model[k]["cost_usd"]):
            bucket = summary.by_model[name]
            per_model.add_row(
                name,
                str(int(bucket["runs"])),
                _format_money(bucket["cost_usd"]),
                str(int(bucket["tokens"])),
            )
        console.print(per_model)

    return buf.getvalue().rstrip("\n")


def _format_text_plain(summary: CostSummary) -> str:
    """Plain ASCII table — works without rich and on dumb terminals."""
    lines: list[str] = []
    lines.append("mink runs cost")
    lines.append("=" * 40)
    lines.append(f"  runs:                  {summary.total_runs}")
    lines.append(f"  success / fail:        {summary.successful_runs} / {summary.failed_runs}")
    lines.append(f"  total cost:            {_format_money(summary.total_cost_usd)}")
    lines.append(f"  avg cost / run:        {_format_money(summary.avg_cost_usd)}")
    lines.append(f"  p50 cost:              {_format_money(summary.p50_cost_usd)}")
    lines.append(f"  p95 cost:              {_format_money(summary.p95_cost_usd)}")
    lines.append(f"  total tokens:          {summary.total_tokens}")
    if summary.total_input_tokens or summary.total_output_tokens or summary.total_cache_tokens:
        lines.append(
            f"  input/output/cache:    {summary.total_input_tokens} / "
            f"{summary.total_output_tokens} / {summary.total_cache_tokens}"
        )
    if summary.since:
        lines.append(f"  since:                 {summary.since}")
    if summary.model_filter:
        lines.append(f"  model filter:          {summary.model_filter}")
    if summary.by_model:
        lines.append("")
        lines.append("by model:")
        lines.append(
            "  " + "MODEL".ljust(28) + "RUNS".rjust(6) + "  " + "COST".rjust(10) + "  " + "TOKENS".rjust(10)
        )
        for name in sorted(summary.by_model, key=lambda k: -summary.by_model[k]["cost_usd"]):
            bucket = summary.by_model[name]
            lines.append(
                "  "
                + name.ljust(28)[:28]
                + str(int(bucket["runs"])).rjust(6)
                + "  "
                + _format_money(bucket["cost_usd"]).rjust(10)
                + "  "
                + str(int(bucket["tokens"])).rjust(10)
            )
    return "\n".join(lines)


def format_text(summary: CostSummary, *, use_rich: bool = True) -> str:
    """Render ``summary`` as a human-readable table.

    Args:
        summary: The aggregated :class:`CostSummary`.
        use_rich: When True (default) try rich first and fall back to the
            plain ASCII renderer if rich is not installed. Pass ``False``
            to force the plain renderer (used by tests for stable output).
    """
    if use_rich:
        rendered = _try_rich_table(summary)
        if rendered is not None:
            return rendered
    return _format_text_plain(summary)


def format_json(summary: CostSummary) -> str:
    """Render ``summary`` as a JSON object.

    Schema is stable: ``totals`` block + ``by_model`` map + ``rows``
    array. Per-run rows include the same keys as :class:`CostRow`.
    """
    payload = {
        "totals": {
            "runs": summary.total_runs,
            "successful_runs": summary.successful_runs,
            "failed_runs": summary.failed_runs,
            "cost_usd": summary.total_cost_usd,
            "tokens": summary.total_tokens,
            "input_tokens": summary.total_input_tokens,
            "output_tokens": summary.total_output_tokens,
            "cache_tokens": summary.total_cache_tokens,
            "avg_cost_usd": summary.avg_cost_usd,
            "p50_cost_usd": summary.p50_cost_usd,
            "p95_cost_usd": summary.p95_cost_usd,
        },
        "filters": {
            "since": summary.since,
            "model": summary.model_filter,
        },
        "by_model": {
            name: {
                "runs": int(bucket["runs"]),
                "cost_usd": bucket["cost_usd"],
                "tokens": int(bucket["tokens"]),
            }
            for name, bucket in summary.by_model.items()
        },
        "rows": [
            {
                "run_id": row.run_id,
                "started_at": row.started_at,
                "model": row.model,
                "cost_usd": row.cost_usd,
                "total_tokens": row.total_tokens,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cache_tokens": row.cache_tokens,
                "success": row.success,
                "steps": row.steps,
            }
            for row in summary.rows
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def format_csv(summary: CostSummary) -> str:
    """Render the per-run rows as CSV (no totals block).

    Columns match :class:`CostRow` fields so spreadsheet pivots can
    derive totals locally without re-parsing JSON.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
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
    )
    for row in summary.rows:
        writer.writerow(
            [
                row.run_id,
                row.started_at,
                row.model,
                f"{row.cost_usd:.6f}",
                row.total_tokens,
                row.input_tokens,
                row.output_tokens,
                row.cache_tokens,
                "true" if row.success else "false",
                row.steps,
            ]
        )
    return buf.getvalue().rstrip("\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run_cost(
    *,
    since: str | None = None,
    model: str | None = None,
    fmt: str = "text",
    limit: int | None = None,
    eventlog_root: Path | None = None,
    use_rich: bool = True,
) -> tuple[int, str]:
    """Top-level helper for ``chimera mink runs cost``.

    Args:
        since: Raw ``--since`` value (shorthand or ISO date). ``None``
            disables the filter.
        model: ``--model`` filter; case-insensitive substring match.
            ``"all"`` and ``None`` mean "all models".
        fmt: One of ``"text"``, ``"json"``, ``"csv"``. Unknown values
            map to a usage-error tuple ``(2, message)``.
        limit: ``--limit`` cap on the number of rows considered (newest
            first). ``None`` / ``0`` means "no cap".
        eventlog_root: Override ``~/.chimera/eventlog`` (used by tests).
        use_rich: Forwarded to :func:`format_text`.

    Returns:
        ``(exit_code, output_string)``. The caller is responsible for
        printing ``output_string`` and returning ``exit_code``.
    """
    try:
        cutoff = parse_since(since)
    except ValueError as exc:
        return 2, f"error: {exc}"

    if fmt not in {"text", "json", "csv"}:
        return 2, f"error: unknown --format {fmt!r} (supported: text, json, csv)"

    root = eventlog_root or default_eventlog_root()
    summary = compute_summary(
        iter_runs(root),
        since=cutoff,
        since_label=since,
        model=model,
        limit=limit,
    )

    if fmt == "json":
        return 0, format_json(summary)
    if fmt == "csv":
        return 0, format_csv(summary)
    return 0, format_text(summary, use_rich=use_rich)
