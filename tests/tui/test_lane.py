"""Tests for lane telemetry folding (spec §6.5)."""
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")  # chimera.tui.render needs rich (tui extra)

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig, Liveness  # noqa: E402


def _lane(lid="A", model="glm-5.2"):
    cfg = LaneConfig(lane_id=lid, label=lid, model=model, preset="coding_agent")
    return Lane(cfg, driver=SimpleNamespace())


def _result(cost=0.0021, steps=3, reason="completed"):
    return SimpleNamespace(
        reason=reason, turn_count=steps, cost_usd=cost,
        usage={"input_tokens": 1200, "output_tokens": 340}, messages=[], duration_ms=1500.0,
    )


def test_telemetry_folds_result_event():
    lane = _lane()
    lane.mark_queued()
    assert lane.telemetry.liveness is Liveness.QUEUED
    assert lane.telemetry.busy
    lane.on_turn_begin()
    assert lane.telemetry.liveness is Liveness.RUNNING

    lane.record(LoopEvent(LoopEventType.assistant_chunk, "hi ", 0))
    lane.record(LoopEvent(LoopEventType.assistant_chunk, "there", 0))
    lane.record(LoopEvent(LoopEventType.assistant, SimpleNamespace(content="hi there"), 0))
    lane.record(LoopEvent(LoopEventType.result, _result(), 0))
    lane.on_turn_end(order=1)

    t = lane.telemetry
    assert t.cost == 0.0021
    assert t.steps == 3
    assert t.tokens_in == 1200 and t.tokens_out == 340 and t.tokens == 1540
    assert t.turns == 1
    assert t.terminal_reason == "completed"
    assert t.liveness is Liveness.DONE
    assert not t.busy
    assert t.finished_order == 1
    assert t.elapsed >= 0.0
    assert "hi there" in lane.transcript_text()


def test_error_event_marks_error_liveness():
    lane = _lane()
    lane.on_turn_begin()
    lane.record(LoopEvent(LoopEventType.error, "boom", 0))
    lane.on_turn_end(order=2)
    assert lane.telemetry.terminal_reason == "error"
    assert lane.telemetry.liveness is Liveness.ERROR


def test_alternate_usage_keys():
    lane = _lane()
    lane.record(LoopEvent(
        LoopEventType.result,
        SimpleNamespace(reason="completed", turn_count=1, cost_usd=0.0,
                        usage={"prompt_tokens": 50, "completion_tokens": 10}, messages=[]),
        0,
    ))
    assert lane.telemetry.tokens_in == 50 and lane.telemetry.tokens_out == 10


def test_reset_race_clears_markers():
    lane = _lane()
    lane.telemetry.finished_order = 3
    lane.telemetry.terminal_reason = "completed"
    lane.reset_race()
    assert lane.telemetry.finished_order is None
    assert lane.telemetry.terminal_reason is None


# -- budget (#170) ---------------------------------------------------------

def test_budget_reason_relabeled_to_steps():
    # The loop emits the raw enforcer dimension; the lane speaks "steps".
    lane = _lane()
    lane.record(LoopEvent(LoopEventType.result,
                          _result(reason="budget_exhausted:llm_calls"), 0))
    assert lane.telemetry.terminal_reason == "budget_exhausted:steps"


def test_budget_cost_and_wall_reasons_pass_through():
    for raw in ("budget_exhausted:cost", "budget_exhausted:wall_clock"):
        lane = _lane()
        lane.record(LoopEvent(LoopEventType.result, _result(reason=raw), 0))
        assert lane.telemetry.terminal_reason == raw


def test_lane_config_to_dict_omits_budget_when_unset():
    cfg = LaneConfig(lane_id="A", label="A", model="glm-5.2")
    assert "budget" not in cfg.to_dict()


def test_lane_config_to_dict_records_budget_when_set():
    from chimera.core.budget import BudgetSpec

    cfg = LaneConfig(lane_id="A", label="A", model="glm-5.2",
                     budget=BudgetSpec(max_cost_usd=0.10, max_llm_calls=20))
    assert cfg.to_dict()["budget"] == {"max_cost": 0.10, "max_steps": 20}
