"""Tests for cohort manifest + persistence/export (spec §6.8)."""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.cohort import Cohort, CohortManifest  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.routing import RoutingMode  # noqa: E402


def _lane(lid, model, workspace=None):
    cfg = LaneConfig(lane_id=lid, label=lid, model=model, preset="coding_agent")
    return Lane(cfg, driver=SimpleNamespace(), workspace=workspace)


def _finish(lane, cost, steps, order, reason="completed"):
    lane.on_turn_begin()
    lane.record(LoopEvent(
        LoopEventType.result,
        SimpleNamespace(reason=reason, turn_count=steps, cost_usd=cost,
                        usage={"input_tokens": 100, "output_tokens": 50}, messages=[]),
        0,
    ))
    lane.on_turn_end(order=order)


def test_summary_ranked_by_finish_order():
    a, b = _lane("A", "glm-5.2"), _lane("B", "glm-5.1")
    _finish(b, 0.002, 5, order=1)   # B finishes first
    _finish(a, 0.001, 2, order=2)
    co = Cohort([a, b], task="fix", routing=RoutingMode.BROADCAST)
    assert co.all_done
    assert co.first_finisher.id == "B"
    rows = co.summary_rows()
    assert [r["lane_id"] for r in rows] == ["B", "A"]
    assert co.total_cost == pytest.approx(0.003)


def test_persist_writes_self_contained_artifact(tmp_path):
    fake_ws = SimpleNamespace(
        path=tmp_path / "wsA", strategy="worktree", base_commit="abc123",
        branch="chimera-lane-A", diff=lambda: "diff --git a/f b/f\n+hello\n",
    )
    a = _lane("A", "glm-5.2", workspace=fake_ws)
    b = _lane("B", "glm-5.1", workspace=None)
    a.record(LoopEvent(LoopEventType.assistant, SimpleNamespace(content="hi from A"), 0))
    _finish(a, 0.0021, 3, order=1)
    _finish(b, 0.0, 1, order=2, reason="loop_detected")

    co = Cohort([a, b], task="fix the bug", source="/repo",
                isolation="worktree", routing=RoutingMode.BROADCAST)
    out = co.persist(root=tmp_path / "cohorts")

    assert (out / "manifest.json").exists()
    assert (out / "summary.json").exists()
    assert (out / "lane-A.transcript.txt").exists()
    assert (out / "lane-B.transcript.txt").exists()
    assert (out / "lane-A.diff").exists()      # A has a workspace
    assert not (out / "lane-B.diff").exists()  # B has none

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["task"] == "fix the bug"
    assert manifest["isolation"] == "worktree"
    assert manifest["routing"] == "broadcast"
    entry_a = next(l for l in manifest["lanes"] if l["lane_id"] == "A")
    assert entry_a["workspace"]["base_commit"] == "abc123"
    assert entry_a["telemetry"]["cost"] == 0.0021
    entry_b = next(l for l in manifest["lanes"] if l["lane_id"] == "B")
    assert entry_b["telemetry"]["terminal_reason"] == "loop_detected"
    assert "hi from A" in (out / "lane-A.transcript.txt").read_text()

    # manifest roundtrips
    m2 = CohortManifest.from_dict(manifest)
    assert m2.cohort_id == co.cohort_id


def test_export_zip(tmp_path):
    a = _lane("A", "glm-5.2")
    _finish(a, 0.001, 1, order=1)
    co = Cohort([a], task="x")
    cohort_dir = co.persist(root=tmp_path / "cohorts")
    archive = co.export(tmp_path / "out.zip", cohort_dir=cohort_dir)
    assert archive.exists() and archive.suffix == ".zip"
