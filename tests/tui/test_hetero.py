"""Tests for heterogeneous lanes — per-lane preset + loop posture (spec §13.3)."""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")

from chimera.assembly.coding_agent import LOOP_POSTURES  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.multiplex import parse_lane_specs  # noqa: E402


def test_parse_model_preset_loop_and_labels():
    specs = parse_lane_specs("glm-5.2:coding_agent:plan,glm-5.2:explore,glm-4.6")
    assert specs[0]["preset"] == "coding_agent" and specs[0]["loop"] == "plan"
    assert specs[0]["label"] == "glm-5.2·plan"
    assert specs[1]["preset"] == "explore" and specs[1]["loop"] == ""
    assert specs[1]["label"] == "glm-5.2·explore"
    assert specs[2]["preset"] == "coding_agent" and specs[2]["loop"] == ""
    assert specs[2]["label"] == "glm-4.6"


def test_parse_rejects_unknown_preset_and_loop():
    with pytest.raises(ValueError, match="unknown preset"):
        parse_lane_specs("glm-5.2:nope")
    with pytest.raises(ValueError, match="unknown loop"):
        parse_lane_specs("glm-5.2:coding_agent:bogus")


def test_duplicate_labels_disambiguated():
    specs = parse_lane_specs("glm-5.2,glm-5.2")
    assert {s["label"] for s in specs} == {"glm-5.2#1", "glm-5.2#2"}


def test_manifest_records_loop(tmp_path):
    cfg = LaneConfig("A", "glm-5.2·plan", "glm-5.2", preset="coding_agent", loop="plan")
    lane = Lane(cfg, driver=SimpleNamespace(history=[]), workspace=None)
    out = Cohort([lane], task="x", isolation="worktree").persist(root=tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["lanes"][0]["loop"] == "plan"
    assert manifest["lanes"][0]["preset"] == "coding_agent"


def test_loop_postures_available():
    assert {"plan", "tdd"} <= set(LOOP_POSTURES)
    assert all(isinstance(v, str) and v.strip() for v in LOOP_POSTURES.values())


def test_coding_agent_applies_loop_posture(tmp_path):
    try:
        from chimera.assembly.coding_agent import CodingAgent
        plain = CodingAgent(model="glm-5.2", project_dir=str(tmp_path / "a"))
        planned = CodingAgent(model="glm-5.2", project_dir=str(tmp_path / "b"), loop="plan")
    except Exception:  # noqa: BLE001 - needs a provider; skip if unavailable
        pytest.skip("CodingAgent could not be constructed in this environment")
    assert "plan-first" not in plain._system_prompt_text
    assert "plan-first" in planned._system_prompt_text
    assert planned._loop == "plan"
    # an unknown posture is ignored, not applied
    ignored = CodingAgent(model="glm-5.2", project_dir=str(tmp_path / "c"), loop="bogus")
    assert "plan-first" not in ignored._system_prompt_text
