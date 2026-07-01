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
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tui.history_io import serialize_history
from chimera.tui.lane import Lane
from chimera.tui.routing import RoutingMode

if TYPE_CHECKING:
    from chimera.tui.workspace import WorkspaceSet

__all__ = ["CohortManifest", "Cohort", "default_cohort_root"]


def default_cohort_root() -> Path:
    """The default parent dir for persisted cohorts (``~/.chimera/cohorts``)."""
    return Path.home() / ".chimera" / "cohorts"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort_id": self.cohort_id,
            "task": self.task,
            "created_at": self.created_at,
            "source": self.source,
            "isolation": self.isolation,
            "routing": self.routing,
            "lanes": self.lanes,
        }

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
    ) -> None:
        self.lanes = lanes
        self.task = task
        self.source = str(source) if source is not None else ""
        self.isolation = isolation
        self.routing = routing
        self.workspaces = workspaces
        self.cohort_id = cohort_id or _new_cohort_id()
        self.created_at = _now_iso()

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

        (out / "manifest.json").write_text(
            json.dumps(self.manifest().to_dict(), indent=2), encoding="utf-8"
        )
        (out / "summary.json").write_text(
            json.dumps(self.summary_rows(), indent=2), encoding="utf-8"
        )
        for lane in self.lanes:
            (out / f"lane-{lane.id}.transcript.txt").write_text(
                lane.transcript_text(), encoding="utf-8"
            )
            # Faithful conversation history for resume (§13.2).
            history = serialize_history(getattr(lane.driver, "history", []) or [])
            (out / f"lane-{lane.id}.history.json").write_text(
                json.dumps(history, indent=2), encoding="utf-8"
            )
            if lane.workspace is not None:
                try:
                    diff = lane.workspace.diff()
                except Exception as exc:  # noqa: BLE001 - diff is best-effort
                    diff = f"(diff unavailable: {exc})"
                if diff:
                    (out / f"lane-{lane.id}.diff").write_text(diff, encoding="utf-8")
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
            lanes.append({**entry, "history": history, "diff": diff})
        return {"manifest": manifest, "cohort_dir": str(cohort_dir), "lanes": lanes}
