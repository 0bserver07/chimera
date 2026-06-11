"""Tests for ATIF v1.7 emission, validation, and reading."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.atif import ATIFEmitter, ATIFReader, ATIFValidator
from chimera.events.base import EventBus
from chimera.events.types import (
    AgentEndEvent,
    CompactionEvent,
    ModelResponseEvent,
    StepCostEvent,
    StepEvent,
    ToolCallEvent,
    ToolResultEvent,
)

PIER_SRC = Path(__file__).resolve().parents[2] / "data" / "vendor" / "pier" / "src"


def _emit_scripted_run(path: Path) -> dict[str, Any]:
    """Publish a representative two-turn run and return the trajectory."""
    bus = EventBus()
    emitter = ATIFEmitter(
        path,
        agent_name="chimera-react",
        agent_version="0.7.0",
        model_name="glm-5",
        session_id="run-1",
    )
    emitter.attach(bus)
    emitter.record_user_message("Fix the bug.")

    bus.publish(ModelResponseEvent(model="glm-5", content_length=10,
                                   tool_calls_count=1, input_tokens=120, output_tokens=30))
    bus.publish(StepEvent(step_number=1, content="Let me read the file."))
    bus.publish(ToolCallEvent(tool_name="read", arguments={"path": "a.py"}, call_id="c1"))
    bus.publish(ToolResultEvent(call_id="c1", output="print('hi')", success=True))
    bus.publish(StepCostEvent(step_index=1, cost=0.001))

    bus.publish(CompactionEvent(messages_before=10, messages_after=4))

    bus.publish(ModelResponseEvent(model="glm-5", content_length=5,
                                   tool_calls_count=0, input_tokens=200, output_tokens=8))
    bus.publish(StepEvent(step_number=2, content="Done: fixed."))
    bus.publish(AgentEndEvent(steps=2, success=True, total_cost=0.002))

    return json.loads(path.read_text(encoding="utf-8"))


class TestEmitter:
    def test_one_step_per_api_turn(self, tmp_path: Path) -> None:
        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        assert traj["schema_version"] == "ATIF-v1.7"
        steps = traj["steps"]
        assert [s["source"] for s in steps] == ["user", "agent", "agent"]
        assert steps[0]["message"] == "Fix the bug."
        assert steps[1]["message"] == "Let me read the file."
        assert steps[1]["llm_call_count"] == 1
        assert steps[2]["message"] == "Done: fixed."

    def test_tool_calls_and_observation_attach_to_their_turn(self, tmp_path: Path) -> None:
        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        agent_step = traj["steps"][1]
        assert agent_step["tool_calls"] == [
            {"tool_call_id": "c1", "function_name": "read", "arguments": {"path": "a.py"}}
        ]
        assert agent_step["observation"]["results"] == [
            {"content": "print('hi')", "source_call_id": "c1"}
        ]
        assert "tool_calls" not in traj["steps"][2]

    def test_metrics_and_final_aggregates(self, tmp_path: Path) -> None:
        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        assert traj["steps"][1]["metrics"] == {"prompt_tokens": 120, "completion_tokens": 30}
        fm = traj["final_metrics"]
        assert fm["total_steps"] == 3
        assert fm["total_prompt_tokens"] == 320
        assert fm["total_completion_tokens"] == 38
        assert fm["total_cost_usd"] == pytest.approx(0.001)
        assert fm["extra"]["peak_context_tokens"] == 200
        assert fm["extra"]["summarization_count"] == 1

    def test_agent_and_session_metadata(self, tmp_path: Path) -> None:
        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        assert traj["agent"] == {
            "name": "chimera-react", "version": "0.7.0", "model_name": "glm-5",
        }
        assert traj["session_id"] == "run-1"

    def test_close_is_idempotent_and_detaches(self, tmp_path: Path) -> None:
        bus = EventBus()
        emitter = ATIFEmitter(tmp_path / "x.atif.json", agent_name="a", agent_version="1")
        emitter.attach(bus)
        emitter.record_user_message("hi")
        first = emitter.close()
        bus.publish(StepEvent(step_number=1, content="after close"))
        assert emitter.close() == first
        traj = json.loads(first.read_text(encoding="utf-8"))
        assert len(traj["steps"]) == 1  # nothing recorded after close

    def test_timestamps_are_monotonic_iso(self, tmp_path: Path) -> None:
        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        assert ATIFValidator().check(traj).valid

    def test_consecutive_step_events_become_separate_turns(self, tmp_path: Path) -> None:
        # Loops that don't publish ModelResponseEvent still get one ATIF
        # step per StepEvent rather than collapsing into one.
        bus = EventBus()
        emitter = ATIFEmitter(tmp_path / "x.atif.json", agent_name="a", agent_version="1")
        emitter.attach(bus)
        emitter.record_user_message("go")
        bus.publish(StepEvent(step_number=1, content="planning"))
        bus.publish(ToolCallEvent(tool_name="read", arguments={}, call_id="c1"))
        bus.publish(StepEvent(step_number=2, content="executing"))
        bus.publish(StepEvent(step_number=3, content="done"))
        path = emitter.close()
        traj = json.loads(path.read_text(encoding="utf-8"))
        agent_steps = [s for s in traj["steps"] if s["source"] == "agent"]
        assert [s["message"] for s in agent_steps] == ["planning", "executing", "done"]
        assert agent_steps[0]["tool_calls"][0]["tool_call_id"] == "c1"
        assert ATIFValidator().check(traj).valid


class TestValidator:
    def _valid(self, tmp_path: Path) -> dict[str, Any]:
        return _emit_scripted_run(tmp_path / "run.atif.json")

    def test_emitter_output_is_valid(self, tmp_path: Path) -> None:
        result = ATIFValidator().check(self._valid(tmp_path))
        assert result.valid, result.errors

    @pytest.mark.parametrize(
        ("mutate", "fragment"),
        [
            (lambda t: t.__setitem__("schema_version", "ATIF-v9"), "schema_version"),
            (lambda t: t.pop("agent"), "agent: required"),
            (lambda t: t["agent"].pop("version"), "agent.version"),
            (lambda t: t.__setitem__("steps", []), "steps: required"),
            (lambda t: t["steps"][1].__setitem__("step_id", 99), "expected ordinal"),
            (lambda t: t["steps"][0].__setitem__("source", "robot"), "source: must be one of"),
            (lambda t: t["steps"][0].pop("message"), "message: required"),
            (lambda t: t["steps"][0].__setitem__("metrics", {"prompt_tokens": 1}),
             "only applicable when source is 'agent'"),
            (lambda t: t["steps"][1].__setitem__("llm_call_count", 0),
             "must be absent when llm_call_count is 0"),
            (lambda t: t["steps"][1]["tool_calls"][0].pop("function_name"),
             "function_name: required"),
            (lambda t: t["steps"][1].__setitem__("observation", {}),
             "observation.results: required"),
            (lambda t: t["steps"][2].__setitem__("timestamp", "not-a-time"),
             "invalid ISO 8601"),
            (lambda t: t["steps"][2].__setitem__("timestamp", "2000-01-01T00:00:00+00:00"),
             "not monotonically non-decreasing"),
        ],
        ids=[
            "bad-version", "missing-agent", "missing-agent-version", "empty-steps",
            "bad-step-id", "bad-source", "missing-message", "agent-only-on-user",
            "llm0-with-metrics", "toolcall-missing-field", "observation-no-results",
            "bad-timestamp", "non-monotonic-timestamp",
        ],
    )
    def test_each_rule_is_enforced(self, tmp_path: Path, mutate, fragment) -> None:
        traj = self._valid(tmp_path)
        mutate(traj)
        result = ATIFValidator().check(traj)
        assert not result.valid
        assert any(fragment in e for e in result.errors), result.errors


class TestReader:
    def test_round_trip_load(self, tmp_path: Path) -> None:
        path = tmp_path / "run.atif.json"
        emitted = _emit_scripted_run(path)
        loaded = ATIFReader().load(path)  # validate=True by default
        assert loaded == emitted

    def test_load_rejects_invalid(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.atif.json"
        traj = _emit_scripted_run(tmp_path / "ok.atif.json")
        traj["steps"][0]["source"] = "robot"
        path.write_text(json.dumps(traj), encoding="utf-8")
        with pytest.raises(ValueError, match="invalid ATIF trajectory"):
            ATIFReader().load(path)
        assert not ATIFReader().validate(path).valid

    def test_load_rejects_bad_json(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            ATIFReader().load(path)

    def test_to_events_reconstruction(self, tmp_path: Path) -> None:
        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        events = ATIFReader().to_events(traj)
        types = [e.type for e in events]
        assert types == ["step", "tool_call", "tool_result", "step", "compaction"]
        step1 = events[0]
        assert step1.content == "Let me read the file."
        tool_call = events[1]
        assert tool_call.tool_name == "read"
        assert tool_call.arguments == {"path": "a.py"}
        tool_result = events[2]
        assert tool_result.call_id == "c1"


@pytest.mark.skipif(
    not PIER_SRC.is_dir(),
    reason="Pier source clone not present under data/vendor/pier",
)
class TestPierInterop:
    """Validate Chimera-emitted trajectories against Pier's own models."""

    def test_pier_pydantic_models_accept_our_trajectory(self, tmp_path: Path) -> None:
        pydantic = pytest.importorskip("pydantic")
        del pydantic
        import sys
        import types

        if "pier" not in sys.modules:
            pier_pkg = types.ModuleType("pier")
            pier_pkg.__path__ = [str(PIER_SRC / "pier")]
            sys.modules["pier"] = pier_pkg
        from pier.models.trajectories.trajectory import Trajectory

        traj = _emit_scripted_run(tmp_path / "run.atif.json")
        parsed = Trajectory.model_validate(traj)
        assert parsed.schema_version == "ATIF-v1.7"
        assert len(parsed.steps) == 3
        assert parsed.steps[1].tool_calls[0].function_name == "read"
        assert parsed.final_metrics.extra["peak_context_tokens"] == 200
