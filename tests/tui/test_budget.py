"""Unit tests for the TUI-facing budget surface (:mod:`chimera.tui.budget`, #170).

Pure and rich-free: the module only wraps :mod:`chimera.core.budget`, so these
run under the CI posture (no ``tui`` extra) with no ``importorskip``.
"""
from __future__ import annotations

import pytest

from chimera.core.budget import BudgetSpec
from chimera.tui.budget import (
    budget_from_dict,
    budget_to_dict,
    cohort_budget_from_config,
    cohort_terminal_reason,
    describe_budget,
    lane_budget_from_config,
    lane_dimension,
    parse_budget_spec,
    relabel_lane_reason,
)


class TestParseBudgetSpec:
    def test_empty_is_none(self) -> None:
        assert parse_budget_spec(None) is None
        assert parse_budget_spec("") is None
        assert parse_budget_spec("   ") is None

    def test_dollar_cost(self) -> None:
        assert parse_budget_spec("$0.10") == BudgetSpec(max_cost_usd=0.10)
        assert parse_budget_spec("0.25usd") == BudgetSpec(max_cost_usd=0.25)

    def test_bare_number_is_cost(self) -> None:
        assert parse_budget_spec("0.5") == BudgetSpec(max_cost_usd=0.5)

    def test_steps(self) -> None:
        assert parse_budget_spec("20steps") == BudgetSpec(max_llm_calls=20)
        assert parse_budget_spec("15st") == BudgetSpec(max_llm_calls=15)

    def test_wall_clock_seconds(self) -> None:
        assert parse_budget_spec("300s") == BudgetSpec(max_wall_clock_sec=300.0)
        assert parse_budget_spec("90sec") == BudgetSpec(max_wall_clock_sec=90.0)

    def test_tool_calls(self) -> None:
        assert parse_budget_spec("40tc") == BudgetSpec(max_tool_calls=40)

    def test_steps_never_read_as_seconds(self) -> None:
        # "20st"/"20steps" must not match the bare-"s" wall-clock unit.
        assert parse_budget_spec("20st").max_llm_calls == 20
        assert parse_budget_spec("20st").max_wall_clock_sec is None

    def test_combined_clauses(self) -> None:
        spec = parse_budget_spec("$0.10/20steps/300s")
        assert spec == BudgetSpec(
            max_cost_usd=0.10, max_llm_calls=20, max_wall_clock_sec=300.0
        )

    def test_non_positive_clause_disabled(self) -> None:
        assert parse_budget_spec("$0/20steps") == BudgetSpec(max_llm_calls=20)
        assert parse_budget_spec("$0") is None

    def test_bad_clause_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_budget_spec("abc")
        with pytest.raises(ValueError):
            parse_budget_spec("$notanumber")


class TestReasonVocabulary:
    def test_lane_dimension_maps_llm_calls_to_steps(self) -> None:
        assert lane_dimension("llm_calls") == "steps"
        assert lane_dimension("cost") == "cost"
        assert lane_dimension("wall_clock") == "wall_clock"
        assert lane_dimension("tool_calls") == "tool_calls"
        assert lane_dimension(None) is None

    def test_relabel_lane_reason(self) -> None:
        assert relabel_lane_reason("budget_exhausted:llm_calls") == "budget_exhausted:steps"
        assert relabel_lane_reason("budget_exhausted:cost") == "budget_exhausted:cost"
        assert relabel_lane_reason("budget_exhausted:wall_clock") == "budget_exhausted:wall_clock"

    def test_relabel_passes_through_non_budget_reasons(self) -> None:
        for reason in ("completed", "max_turns", "aborted_User cancelled", "error", None):
            assert relabel_lane_reason(reason) == reason

    def test_cohort_terminal_reason(self) -> None:
        assert cohort_terminal_reason("cost") == "cohort_budget:cost"
        assert cohort_terminal_reason("llm_calls") == "cohort_budget:steps"
        assert cohort_terminal_reason("wall_clock") == "cohort_budget:wall_clock"


class TestManifestCodec:
    def test_round_trip(self) -> None:
        spec = BudgetSpec(max_cost_usd=0.10, max_llm_calls=20, max_wall_clock_sec=300.0)
        data = budget_to_dict(spec)
        assert data == {"max_cost": 0.10, "max_steps": 20, "max_wall_clock": 300.0}
        assert budget_from_dict(data) == spec

    def test_none_and_unset_serialize_to_none(self) -> None:
        assert budget_to_dict(None) is None
        assert budget_to_dict(BudgetSpec()) is None
        assert budget_from_dict(None) is None
        assert budget_from_dict({}) is None

    def test_only_set_caps_written(self) -> None:
        assert budget_to_dict(BudgetSpec(max_cost_usd=0.5)) == {"max_cost": 0.5}


class TestConfig:
    def test_lane_budget_from_config(self) -> None:
        tui = {"budget": {"max-cost": 0.10, "max-steps": 20}}
        assert lane_budget_from_config(tui) == BudgetSpec(max_cost_usd=0.10, max_llm_calls=20)

    def test_lane_budget_underscore_keys(self) -> None:
        tui = {"budget": {"max_wall_clock": 300}}
        assert lane_budget_from_config(tui) == BudgetSpec(max_wall_clock_sec=300.0)

    def test_cohort_budget_from_nested_table(self) -> None:
        tui = {"budget": {"max-cost": 0.10, "cohort": {"max-cost": 2.0}}}
        assert cohort_budget_from_config(tui) == BudgetSpec(max_cost_usd=2.0)
        # The lane reader ignores the cohort subtable.
        assert lane_budget_from_config(tui) == BudgetSpec(max_cost_usd=0.10)

    def test_absent_or_empty_is_none(self) -> None:
        assert lane_budget_from_config(None) is None
        assert lane_budget_from_config({}) is None
        assert cohort_budget_from_config({"budget": {}}) is None

    def test_non_positive_values_disabled(self) -> None:
        assert lane_budget_from_config({"budget": {"max-cost": 0, "max-steps": -3}}) is None


class TestDescribeBudget:
    def test_none_is_no_budget(self) -> None:
        assert describe_budget(None) == "no budget"
        assert describe_budget(BudgetSpec()) == "no budget"

    def test_used_vs_cap(self) -> None:
        spec = BudgetSpec(max_cost_usd=0.10, max_llm_calls=20)
        out = describe_budget(spec, cost_used=0.04, steps_used=3)
        assert "cost $0.0400/$0.10" in out
        assert "steps 3/20" in out
