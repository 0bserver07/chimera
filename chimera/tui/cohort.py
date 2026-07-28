"""Cohort model + persistence/export for the multiplexer (spec §6.5, §6.8).

A **cohort** is the set of lanes launched together to race one task under
controlled variables — the comparison unit. This module binds the lanes, the
shared task, and their controlled variables into a manifest, and writes a
self-contained comparison artifact (manifest + per-lane transcripts + per-lane
produced diffs) that is portable and reproducible.

Persistence is UI-independent: it reads a lane's accumulated telemetry, its
plain-text transcript, and its workspace diff — never a widget. Capture happens
before workspace teardown, since cleanup removes the tree.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tui.budget import budget_to_dict
from chimera.tui.history_io import serialize_history
from chimera.tui.lane import Lane
from chimera.tui.routing import RoutingMode
from chimera.config.paths import store_path

if TYPE_CHECKING:
    from chimera.core.budget import BudgetSpec
    from chimera.tui.workspace import WorkspaceSet

__all__ = [
    "Cohort",
    "CohortManifest",
    "CohortRetention",
    "default_cohort_root",
    "load_cohort_retention",
    "prune_cohorts",
]


def default_cohort_root() -> Path:
    """The default parent dir for persisted cohorts (``~/.chimera/cohorts``)."""
    return store_path("cohorts")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp file in the same dir + rename).

    Adopts the durability discipline of the session event log
    (:mod:`chimera.sessions.eventlog.log`): a crash or a concurrent reader
    never observes a half-written ``manifest.json`` — the file is either its
    previous contents or the complete new contents, never a truncation, because
    :func:`os.replace` swaps it atomically within the filesystem. The temp file
    is hidden (dot-prefixed) so a mid-write ``iterdir`` scan does not mistake it
    for a cohort artifact.

    Args:
        path: Destination file.
        text: Full contents to write.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_cohort_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


@dataclass
class CohortManifest:
    """Reproducibility record binding lanes + task + controlled variables."""

    cohort_id: str
    task: str | None
    created_at: str
    source: str
    isolation: str
    routing: str
    lanes: list[dict[str, Any]] = field(default_factory=list)
    #: The cohort-aggregate budget (#170) in lane vocabulary, or ``None``. Only
    #: written when set, so an unbudgeted cohort's manifest is unchanged.
    budget: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "cohort_id": self.cohort_id,
            "task": self.task,
            "created_at": self.created_at,
            "source": self.source,
            "isolation": self.isolation,
            "routing": self.routing,
            "lanes": self.lanes,
        }
        if self.budget is not None:
            data["budget"] = self.budget
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CohortManifest:
        return cls(
            cohort_id=data["cohort_id"],
            task=data.get("task"),
            created_at=data.get("created_at", ""),
            source=data.get("source", ""),
            isolation=data.get("isolation", ""),
            routing=data.get("routing", ""),
            lanes=data.get("lanes", []),
            budget=data.get("budget"),
        )


class Cohort:
    """The lanes racing one task, plus their manifest and persistence."""

    def __init__(
        self,
        lanes: list[Lane],
        *,
        task: str | None = None,
        source: str | Path | None = None,
        isolation: str = "worktree",
        routing: RoutingMode = RoutingMode.BROADCAST,
        cohort_id: str | None = None,
        workspaces: WorkspaceSet | None = None,
        budget: BudgetSpec | None = None,
    ) -> None:
        self.lanes = lanes
        self.task = task
        self.source = str(source) if source is not None else ""
        self.isolation = isolation
        self.routing = routing
        self.workspaces = workspaces
        self.cohort_id = cohort_id or _new_cohort_id()
        self.created_at = _now_iso()
        #: The cohort-aggregate budget (#170): a cap on total $ / total steps /
        #: race wall-clock across all lanes. ``None`` = unbudgeted.
        self.budget = budget

    # -- live cohort state ----------------------------------------------
    @property
    def done_count(self) -> int:
        """Lanes that are not currently running/queued a turn."""
        return sum(1 for lane in self.lanes if not lane.telemetry.busy)

    @property
    def all_done(self) -> bool:
        return self.done_count == len(self.lanes)

    @property
    def total_cost(self) -> float:
        return sum(lane.telemetry.cost for lane in self.lanes)

    @property
    def total_steps(self) -> int:
        """Steps summed across lanes — the unit for a cohort step budget."""
        return sum(lane.telemetry.steps for lane in self.lanes)

    @property
    def first_finisher(self) -> Lane | None:
        finished = [ln for ln in self.lanes if ln.telemetry.finished_order is not None]
        if not finished:
            return None
        return min(finished, key=lambda ln: ln.telemetry.finished_order or 0)

    def lane(self, lane_id: str) -> Lane | None:
        for lane in self.lanes:
            if lane.id == lane_id:
                return lane
        return None

    def summary_rows(self) -> list[dict[str, Any]]:
        """Per-lane comparison rows, ranked by finish order then label."""
        def sort_key(lane: Lane) -> tuple[int, str]:
            order = lane.telemetry.finished_order
            return (order if order is not None else 1_000_000, lane.label)

        rows: list[dict[str, Any]] = []
        for lane in sorted(self.lanes, key=sort_key):
            t = lane.telemetry
            rows.append({
                "lane_id": lane.id,
                "label": lane.label,
                "model": lane.config.model,
                "preset": lane.config.preset,
                "liveness": t.liveness.value,
                "terminal_reason": t.terminal_reason,
                "finished_order": t.finished_order,
                "cost": round(t.cost, 6),
                "tokens_in": t.tokens_in,
                "tokens_out": t.tokens_out,
                "steps": t.steps,
                "turns": t.turns,
                "elapsed": round(t.elapsed, 3),
            })
        return rows

    # -- manifest & persistence -----------------------------------------
    def manifest(self) -> CohortManifest:
        by_id = self.summary_rows_by_id()
        lanes: list[dict[str, Any]] = []
        for lane in self.lanes:
            entry = dict(lane.config.to_dict())
            entry["telemetry"] = {
                k: v for k, v in by_id.get(lane.id, {}).items()
                if k not in ("lane_id", "label", "model", "preset")
            }
            ws = lane.workspace
            entry["workspace"] = {
                "path": str(ws.path) if ws else None,
                "strategy": ws.strategy if ws else None,
                "base_commit": ws.base_commit if ws else None,
                "branch": ws.branch if ws else None,
            }
            lanes.append(entry)
        return CohortManifest(
            cohort_id=self.cohort_id,
            task=self.task,
            created_at=self.created_at,
            source=self.source,
            isolation=self.isolation,
            routing=self.routing.value,
            lanes=lanes,
            budget=budget_to_dict(self.budget),
        )

    def summary_rows_by_id(self) -> dict[str, dict[str, Any]]:
        return {row["lane_id"]: row for row in self.summary_rows()}

    def persist(self, root: str | Path | None = None) -> Path:
        """Write a self-contained comparison artifact; return its directory.

        Layout under ``<root>/<cohort_id>/``:

        - ``manifest.json`` — task, controlled variables, per-lane telemetry.
        - ``summary.json`` — ranked cohort summary rows.
        - ``lane-<id>.transcript.txt`` — each lane's transcript.
        - ``lane-<id>.diff`` — each lane's produced changes (if a workspace).
        """
        base = Path(root) if root is not None else default_cohort_root()
        out = base / self.cohort_id
        out.mkdir(parents=True, exist_ok=True)

        # Atomic writes for the resume-critical artifacts (manifest, summary,
        # per-lane history + diff): ``list_saved`` / ``load_saved`` parse these,
        # so a truncated file would silently drop a cohort from the picker.
        _atomic_write_text(
            out / "manifest.json",
            json.dumps(self.manifest().to_dict(), indent=2),
        )
        _atomic_write_text(
            out / "summary.json", json.dumps(self.summary_rows(), indent=2)
        )
        for lane in self.lanes:
            _atomic_write_text(
                out / f"lane-{lane.id}.transcript.txt", lane.transcript_text()
            )
            # Faithful conversation history for resume (§13.2).
            history = serialize_history(getattr(lane.driver, "history", []) or [])
            _atomic_write_text(
                out / f"lane-{lane.id}.history.json", json.dumps(history, indent=2)
            )
            if lane.workspace is not None:
                try:
                    diff = lane.workspace.diff()
                except Exception as exc:  # noqa: BLE001 - diff is best-effort
                    diff = f"(diff unavailable: {exc})"
                if diff:
                    _atomic_write_text(out / f"lane-{lane.id}.diff", diff)
        return out

    def export(self, dest: str | Path, *, cohort_dir: Path | None = None) -> Path:
        """Zip the persisted artifact to *dest* (``.zip`` appended if absent)."""
        src = cohort_dir or self.persist()
        dest = Path(dest)
        stem = str(dest.with_suffix("")) if dest.suffix == ".zip" else str(dest)
        archive = shutil.make_archive(stem, "zip", root_dir=str(src))
        return Path(archive)

    # -- resume discovery / loading -------------------------------------
    @staticmethod
    def list_saved(root: str | Path | None = None) -> list[dict[str, Any]]:
        """List persisted cohorts (newest first) for resume discovery."""
        base = Path(root) if root is not None else default_cohort_root()
        if not base.exists():
            return []
        rows: list[dict[str, Any]] = []
        for entry in sorted(base.iterdir(), key=lambda p: p.name, reverse=True):
            manifest_path = entry / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append({
                "cohort_id": manifest.get("cohort_id", entry.name),
                "task": manifest.get("task"),
                "created_at": manifest.get("created_at"),
                "isolation": manifest.get("isolation"),
                "lanes": [
                    {"label": ln.get("label"), "model": ln.get("model")}
                    for ln in manifest.get("lanes", [])
                ],
                "dir": str(entry),
            })
        return rows

    @staticmethod
    def load_saved(cohort_id: str, root: str | Path | None = None) -> dict[str, Any]:
        """Load a persisted cohort's manifest + per-lane history/diff for resume.

        Returns ``{manifest, cohort_dir, lanes}`` where each lane merges its
        manifest entry with its saved ``history`` (rows) and ``diff`` (text).
        """
        base = Path(root) if root is not None else default_cohort_root()
        cohort_dir = base / cohort_id
        manifest_path = cohort_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"no saved cohort {cohort_id!r} under {base}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        lanes: list[dict[str, Any]] = []
        for entry in manifest.get("lanes", []):
            lane_id = entry.get("lane_id", "")
            hist_path = cohort_dir / f"lane-{lane_id}.history.json"
            diff_path = cohort_dir / f"lane-{lane_id}.diff"
            history = (
                json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.is_file() else []
            )
            diff = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""
            transcript_path = cohort_dir / f"lane-{lane_id}.transcript.txt"
            transcript = (
                transcript_path.read_text(encoding="utf-8")
                if transcript_path.is_file() else ""
            )
            lanes.append(
                {**entry, "history": history, "diff": diff, "transcript": transcript},
            )
        return {"manifest": manifest, "cohort_dir": str(cohort_dir), "lanes": lanes}


# ---------------------------------------------------------------------------
# Auto-pruning (issue #173) — keep the cohort store from growing without bound
# ---------------------------------------------------------------------------


def _positive_int(value: Any) -> int | None:
    """Coerce a config value to a positive int, else ``None`` (knob disabled)."""
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _positive_float(value: Any) -> float | None:
    """Coerce a config value to a positive float, else ``None`` (knob disabled)."""
    if isinstance(value, bool):
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


@dataclass(frozen=True)
class CohortRetention:
    """The cohort auto-pruning policy (issue #173).

    Both knobs are optional and **OFF by default**, so no configuration means
    no pruning — persisted cohorts accumulate exactly as before (the
    data-preserving default: nobody loses work they did not ask to discard).
    Configure under ``[storage.cohorts]`` in ``~/.chimera/config.toml``::

        [storage.cohorts]
        retain = 20            # keep only the newest 20 cohorts
        max-age-days = 30      # also drop cohorts older than 30 days

    ``[tui.cohorts]`` — the spelling that shipped first — is still read as a
    legacy alias when ``[storage.cohorts]`` is absent, so existing configs keep
    working untouched.

    Args:
        retain: Keep at most this many of the newest cohorts; ``None`` keeps
            any number.
        max_age_days: Drop cohorts older than this many days; ``None`` imposes
            no age limit.
    """

    retain: int | None = None
    max_age_days: float | None = None

    @property
    def active(self) -> bool:
        """Whether any knob is set (an inactive policy prunes nothing)."""
        return self.retain is not None or self.max_age_days is not None

    @classmethod
    def from_tui_config(cls, tui: Mapping[str, Any] | None) -> CohortRetention:
        """Parse a ``[tui.cohorts]`` table (dash or underscore keys).

        Unset, non-positive, or malformed values disable that knob — the safe
        default is "prune nothing" unless a valid positive limit is given.

        Args:
            tui: The merged ``tui`` config section (or ``None``).

        Returns:
            The resolved policy (inactive when no valid knob is present).
        """
        cohorts = tui.get("cohorts") if isinstance(tui, Mapping) else None
        if not isinstance(cohorts, Mapping):
            return cls()
        return cls(
            retain=_positive_int(cohorts.get("retain")),
            max_age_days=_positive_float(
                cohorts.get("max-age-days", cohorts.get("max_age_days"))
            ),
        )


def load_cohort_retention(
    project_dir: str | os.PathLike[str] | None = None,
) -> CohortRetention:
    """Resolve the cohort-retention policy from the unified config chain.

    Delegates to :func:`chimera.config.paths.store_retention` — the one
    retention reader every store shares — which reads ``[storage.cohorts]``
    across the standard scopes (XDG < user < project) and falls back to the
    legacy ``[tui.cohorts]`` table when the new spelling is absent. A global
    cap in ``~/.chimera/config.toml`` or a per-project override both apply.
    Any failure yields the inactive (prune-nothing) default — config discovery
    must never block a TUI launch.

    Args:
        project_dir: Project root for the project-scope lookup (default: cwd).

    Returns:
        The resolved :class:`CohortRetention`.
    """
    try:
        from chimera.config.paths import store_retention

        resolved = store_retention("cohorts", project_dir)
    except Exception:  # noqa: BLE001 — config discovery is best-effort.
        return CohortRetention()
    return CohortRetention(
        retain=resolved.retain, max_age_days=resolved.max_age_days
    )


def _cohort_age_days(manifest_path: Path, entry: Path, ref: datetime) -> float:
    """Age of a cohort in days: manifest ``created_at``, dir mtime as fallback."""
    created: datetime | None = None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = data.get("created_at")
        if isinstance(raw, str) and raw:
            created = datetime.fromisoformat(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        created = None
    if created is None:
        try:
            created = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0.0, (ref - created).total_seconds() / 86400.0)


def prune_cohorts(
    root: str | Path | None = None,
    retention: CohortRetention | None = None,
    *,
    exclude: Iterable[str] = (),
    now: datetime | None = None,
) -> list[str]:
    """Delete old persisted cohorts per *retention*; return the ids removed.

    Cohorts are ordered newest-first by id (ids are ``<UTC-stamp>-<rand>``, so
    lexical order is chronological). The newest ``retention.retain`` are always
    kept (a hard floor); among the older remainder, a cohort is removed when
    ``retention.max_age_days`` says it is too old — or, when only ``retain`` is
    set, simply because it is past the newest-N window. When only
    ``max_age_days`` is set, every cohort older than the limit is removed
    regardless of count.

    Ids in *exclude* are never removed — the caller passes the cohort it is
    about to run or resume, so a live cohort is untouchable. Only directories
    that carry a ``manifest.json`` are considered, so unrelated files under
    *root* are never deleted. Deletion is best-effort: a directory that vanishes
    or is locked by a concurrent instance is skipped, never fatal.

    A no-op returning ``[]`` when *retention* is ``None``/inactive, when *root*
    does not exist, or when nothing qualifies — the default, data-preserving
    behavior.

    Args:
        root: Cohort store root (default: ``~/.chimera/cohorts``).
        retention: The policy; ``None`` or inactive prunes nothing.
        exclude: Cohort ids that must survive (e.g. the one being resumed).
        now: Clock override for age comparisons (default: current UTC time).

    Returns:
        The cohort ids deleted, newest-to-oldest.
    """
    if retention is None or not retention.active:
        return []
    base = Path(root) if root is not None else default_cohort_root()
    if not base.is_dir():
        return []

    excluded = set(exclude)
    ref = now or datetime.now(timezone.utc)
    entries: list[tuple[str, Path, float]] = []
    for entry in base.iterdir():
        manifest_path = entry / "manifest.json"
        if not entry.is_dir() or not manifest_path.is_file():
            continue
        entries.append(
            (entry.name, entry, _cohort_age_days(manifest_path, entry, ref))
        )
    entries.sort(key=lambda item: item[0], reverse=True)  # newest id first

    keep = retention.retain
    max_age = retention.max_age_days
    removed: list[str] = []
    for position, (cohort_id, entry, age) in enumerate(entries):
        if cohort_id in excluded:
            continue
        if keep is not None and position < keep:
            continue  # always keep the newest N (the retain floor)
        # Past the retain window (or no retain set): delete when an age limit
        # says so, or when a retain limit alone applies to this overflow.
        if (max_age is not None and age > max_age) or (max_age is None and keep is not None):
            try:
                shutil.rmtree(entry)
            except OSError:
                continue  # best-effort: a concurrent reader/racer is not fatal
            removed.append(cohort_id)
    return removed
