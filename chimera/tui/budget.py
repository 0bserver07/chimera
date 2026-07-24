"""Lane + cohort budgets for the multiplexer (issue #170).

A **budget** bounds a race: a lane (and the cohort as a whole) can carry a cap
on cost (USD), steps, or wall-clock seconds, and stop cleanly with an honest
terminal reason when it trips instead of burning tokens unbounded. The
enforcement machinery is reused wholesale from :mod:`chimera.core.budget`
(:class:`~chimera.core.budget.BudgetSpec` /
:class:`~chimera.core.budget.BudgetEnforcer`); this module is only the
TUI-facing surface around it — the user vocabulary, the config/CLI/manifest
codecs, and the terminal-reason strings.

Vocabulary bridge. The core enforcer counts ``llm_calls``; a lane calls one
reason-act cycle a **step** and shows ``N st`` in its pane, so the lane surface
speaks *steps*, *cost*, and *wall_clock*. The mapping is one-to-one:

===============  ======================  ===========================
lane vocabulary  :class:`BudgetSpec`     terminal reason token
===============  ======================  ===========================
``cost``         ``max_cost_usd``        ``budget_exhausted:cost``
``steps``        ``max_llm_calls``       ``budget_exhausted:steps``
``wall_clock``   ``max_wall_clock_sec``  ``budget_exhausted:wall_clock``
===============  ======================  ===========================

(A power user may also set ``tool_calls`` via config/spec; it maps to
``max_tool_calls`` and surfaces unchanged.) Cohort caps use the same tokens
under a ``cohort_budget:`` prefix.

Compact string grammar (CLI ``--lane-budget`` / ``--budget`` and the per-lane
``--models`` override): ``/``-joined clauses, each a number with a unit —
``$0.10`` or ``0.10usd`` (cost), ``20steps`` / ``20st`` (steps), ``300s`` /
``300sec`` (wall-clock seconds), ``40tc`` (tool calls). A bare number is cost
USD. Example: ``$0.10/20steps/300s``.

Stdlib only (imports just :mod:`chimera.core.budget`); nothing here needs the
``tui`` extra, so the CLI and cohort persistence use it without ``rich``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chimera.core.budget import BudgetSpec

__all__ = [
    "COHORT_REASON_PREFIX",
    "LANE_REASON_PREFIX",
    "budget_from_dict",
    "budget_to_dict",
    "cohort_budget_from_config",
    "cohort_terminal_reason",
    "describe_budget",
    "lane_budget_from_config",
    "lane_dimension",
    "parse_budget_spec",
    "relabel_lane_reason",
]

#: Reason-string prefixes (kept together so callers never hardcode them).
LANE_REASON_PREFIX = "budget_exhausted:"
COHORT_REASON_PREFIX = "cohort_budget:"

#: Core enforcer dimension -> lane vocabulary. Only ``llm_calls`` is renamed
#: (to ``steps``); ``cost`` / ``wall_clock`` / ``tool_calls`` pass through.
_ENFORCER_TO_LANE: dict[str, str] = {"llm_calls": "steps"}


# ---------------------------------------------------------------------------
# Value coercion (a non-positive or malformed cap disables that dimension)
# ---------------------------------------------------------------------------

def _coerce_float(value: Any) -> float | None:
    """Coerce a config/CLI value to a positive float, else ``None``."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _coerce_int(value: Any) -> int | None:
    """Coerce a config/CLI value to a positive int, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(float(value.strip()))
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


# ---------------------------------------------------------------------------
# Terminal-reason vocabulary
# ---------------------------------------------------------------------------

def lane_dimension(enforcer_dimension: str | None) -> str | None:
    """Translate a core enforcer dimension into the lane vocabulary.

    Args:
        enforcer_dimension: One of :data:`chimera.core.budget.BUDGET_DIMENSIONS`
            (or ``None``).

    Returns:
        The lane-facing token (``llm_calls`` -> ``steps``; others unchanged), or
        ``None`` when *enforcer_dimension* is ``None``.
    """
    if enforcer_dimension is None:
        return None
    return _ENFORCER_TO_LANE.get(enforcer_dimension, enforcer_dimension)


def relabel_lane_reason(reason: str | None) -> str | None:
    """Rewrite a loop ``budget_exhausted:<dim>`` reason into lane vocabulary.

    The core :class:`~chimera.core.agent_loop.AgentLoop` emits the raw enforcer
    dimension (``budget_exhausted:llm_calls``); a lane relabels it to
    ``budget_exhausted:steps`` as the reason enters its telemetry, so every
    downstream surface (pane header, status line, summary, manifest) speaks one
    vocabulary. Any non-budget reason passes through byte-identical.

    Args:
        reason: The terminal reason from a loop result, or ``None``.

    Returns:
        The relabeled reason (or the input unchanged).
    """
    if not reason or not reason.startswith(LANE_REASON_PREFIX):
        return reason
    dim = reason[len(LANE_REASON_PREFIX):]
    return f"{LANE_REASON_PREFIX}{lane_dimension(dim)}"


def cohort_terminal_reason(enforcer_dimension: str | None) -> str:
    """Build the ``cohort_budget:<dim>`` reason for a cohort-cancelled lane."""
    return f"{COHORT_REASON_PREFIX}{lane_dimension(enforcer_dimension) or 'budget'}"


# ---------------------------------------------------------------------------
# Compact string grammar
# ---------------------------------------------------------------------------

# Unit suffixes, longest-first within each dimension so "steps" wins over "st"
# and "seconds" over "s". Order across dimensions matters: cost and steps are
# tried before the bare-"s" wall-clock unit so "20st"/"20steps" never read as
# seconds.
_COST_SUFFIXES = ("usd", "$")
_STEP_SUFFIXES = ("steps", "step", "st")
_WALL_SUFFIXES = ("seconds", "secs", "sec", "s")
_TOOL_SUFFIXES = ("toolcalls", "tool_calls", "tc")


def _parse_clause(clause: str) -> tuple[str, float]:
    """Parse one compact clause into ``(lane_dimension, value)``.

    Raises:
        ValueError: On an empty clause or an unparseable number.
    """
    if clause.startswith("$"):
        return "cost", _require_number(clause[1:], clause)
    for suffix in _COST_SUFFIXES:
        if clause.endswith(suffix):
            return "cost", _require_number(clause[: -len(suffix)], clause)
    for suffix in _STEP_SUFFIXES:
        if clause.endswith(suffix):
            return "steps", _require_number(clause[: -len(suffix)], clause)
    for suffix in _WALL_SUFFIXES:
        if clause.endswith(suffix):
            return "wall_clock", _require_number(clause[: -len(suffix)], clause)
    for suffix in _TOOL_SUFFIXES:
        if clause.endswith(suffix):
            return "tool_calls", _require_number(clause[: -len(suffix)], clause)
    # Bare number -> cost USD (the friendliest default for the common case).
    return "cost", _require_number(clause, clause)


def _require_number(text: str, clause: str) -> float:
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"bad budget clause {clause!r}: expected a number with an optional "
            f"unit ($/usd, steps/st, s/sec, tc)"
        ) from exc


def parse_budget_spec(text: str | None) -> BudgetSpec | None:
    """Parse a compact budget string into a :class:`BudgetSpec`.

    Args:
        text: A ``/``-joined clause list (see the module docstring), or
            ``None`` / empty for no budget.

    Returns:
        The resolved spec, or ``None`` when *text* is empty or every clause is
        non-positive.

    Raises:
        ValueError: On an unparseable clause.
    """
    if not text or not text.strip():
        return None
    caps: dict[str, float] = {}
    for raw in text.split("/"):
        clause = raw.strip().lower().replace(" ", "")
        if not clause:
            continue
        dimension, value = _parse_clause(clause)
        if value > 0:  # a non-positive cap disables that dimension
            caps[dimension] = value
    spec = BudgetSpec(
        max_cost_usd=caps.get("cost"),
        max_llm_calls=int(caps["steps"]) if "steps" in caps else None,
        max_wall_clock_sec=caps.get("wall_clock"),
        max_tool_calls=int(caps["tool_calls"]) if "tool_calls" in caps else None,
    )
    return spec if spec.is_set else None


# ---------------------------------------------------------------------------
# Config (``[tui.budget]`` and ``[tui.budget.cohort]``)
# ---------------------------------------------------------------------------

def _spec_from_table(table: Mapping[str, Any] | None) -> BudgetSpec | None:
    """Build a spec from a config table (dash or underscore keys)."""
    if not isinstance(table, Mapping):
        return None

    def pick(*names: str) -> Any:
        for name in names:
            if name in table:
                return table[name]
        return None

    spec = BudgetSpec(
        max_cost_usd=_coerce_float(pick("max-cost", "max_cost", "cost")),
        max_llm_calls=_coerce_int(pick("max-steps", "max_steps", "steps")),
        max_wall_clock_sec=_coerce_float(
            pick("max-wall-clock", "max_wall_clock", "wall-clock", "wall_clock")
        ),
        max_tool_calls=_coerce_int(pick("max-tool-calls", "max_tool_calls", "tool_calls")),
    )
    return spec if spec.is_set else None


def lane_budget_from_config(tui: Mapping[str, Any] | None) -> BudgetSpec | None:
    """Resolve the per-lane default budget from a ``[tui.budget]`` table.

    The nested ``cohort`` subtable is ignored here (see
    :func:`cohort_budget_from_config`). Unset / non-positive / malformed values
    disable that dimension; an empty table yields ``None`` (no budget — the
    additive default).

    Args:
        tui: The merged ``tui`` config section (or ``None``).

    Returns:
        The per-lane :class:`BudgetSpec`, or ``None``.
    """
    budget = tui.get("budget") if isinstance(tui, Mapping) else None
    return _spec_from_table(budget)


def cohort_budget_from_config(tui: Mapping[str, Any] | None) -> BudgetSpec | None:
    """Resolve the cohort-aggregate budget from ``[tui.budget.cohort]``.

    Args:
        tui: The merged ``tui`` config section (or ``None``).

    Returns:
        The cohort :class:`BudgetSpec`, or ``None``.
    """
    budget = tui.get("budget") if isinstance(tui, Mapping) else None
    cohort = budget.get("cohort") if isinstance(budget, Mapping) else None
    return _spec_from_table(cohort)


# ---------------------------------------------------------------------------
# Manifest codec (rides the cohort manifest for resume/inspection)
# ---------------------------------------------------------------------------

def budget_to_dict(spec: BudgetSpec | None) -> dict[str, Any] | None:
    """Serialize a spec to a manifest dict in lane vocabulary, or ``None``.

    Only set caps are written, so the manifest stays terse. ``None`` in, ``None``
    out — a budget-less lane records no ``budget`` key.
    """
    if spec is None or not spec.is_set:
        return None
    out: dict[str, Any] = {}
    if spec.max_cost_usd is not None:
        out["max_cost"] = spec.max_cost_usd
    if spec.max_llm_calls is not None:
        out["max_steps"] = spec.max_llm_calls
    if spec.max_wall_clock_sec is not None:
        out["max_wall_clock"] = spec.max_wall_clock_sec
    if spec.max_tool_calls is not None:
        out["max_tool_calls"] = spec.max_tool_calls
    return out


def budget_from_dict(data: Mapping[str, Any] | None) -> BudgetSpec | None:
    """Rebuild a spec from a manifest dict (the inverse of :func:`budget_to_dict`)."""
    if not isinstance(data, Mapping) or not data:
        return None
    spec = BudgetSpec(
        max_cost_usd=_coerce_float(data.get("max_cost")),
        max_llm_calls=_coerce_int(data.get("max_steps")),
        max_wall_clock_sec=_coerce_float(data.get("max_wall_clock")),
        max_tool_calls=_coerce_int(data.get("max_tool_calls")),
    )
    return spec if spec.is_set else None


# ---------------------------------------------------------------------------
# Human description (the /budget inspector)
# ---------------------------------------------------------------------------

def describe_budget(
    spec: BudgetSpec | None,
    *,
    cost_used: float | None = None,
    steps_used: float | None = None,
    wall_used: float | None = None,
    tool_used: float | None = None,
) -> str:
    """A one-line ``used/cap`` description of a budget's set dimensions.

    Pure and rich-free, so the ``/budget`` inspector and tests share it. Used
    values default to ``0`` when unknown.

    Args:
        spec: The budget (``None`` / unset yields ``"no budget"``).
        cost_used: Dollars spent so far.
        steps_used: Steps taken so far.
        wall_used: Active seconds elapsed so far.
        tool_used: Tool calls made so far.

    Returns:
        e.g. ``"cost $0.0400/$0.10 · steps 3/20 · wall 12/300s"``.
    """
    if spec is None or not spec.is_set:
        return "no budget"
    parts: list[str] = []
    if spec.max_cost_usd is not None:
        parts.append(f"cost ${cost_used or 0.0:.4f}/${spec.max_cost_usd:.2f}")
    if spec.max_llm_calls is not None:
        parts.append(f"steps {int(steps_used or 0)}/{spec.max_llm_calls}")
    if spec.max_wall_clock_sec is not None:
        parts.append(f"wall {wall_used or 0.0:.0f}/{spec.max_wall_clock_sec:.0f}s")
    if spec.max_tool_calls is not None:
        parts.append(f"tools {int(tool_used or 0)}/{spec.max_tool_calls}")
    return " · ".join(parts)
