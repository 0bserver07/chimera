"""Tests for the tau-bench adapter.

Covers:
    - dataset-absent skip path (the common case in CI)
    - task iteration / normalisation when a JSON dump is supplied
    - terminal-action match scoring logic
    - structural goal_state fallback
    - CLI module loads without side effects

Heavyweight imports (``asyncio`` runtime, agent stack) are guarded with
``pytest.importorskip`` / try-import skips so the suite stays green on
minimal environments.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip the whole module if asyncio fails to load — the CLI path imports
# the agent stack which in turn pulls asyncio.
pytest.importorskip("asyncio")

from chimera.eval.benchmarks.tau_bench import (  # noqa: E402
    ENV_DATASET_PATH,
    TauBench,
    _action_equal,
    _actions_match,
    _format_table,
    _terminal_action_name,
    dataset_available,
    default_dataset_path,
)


# ----------------------------------------------------------------------
# Dataset resolution / availability
# ----------------------------------------------------------------------


class TestDatasetResolution:
    def test_default_path_uses_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_DATASET_PATH, str(tmp_path))
        assert default_dataset_path() == tmp_path

    def test_default_path_fallback(self, monkeypatch):
        monkeypatch.delenv(ENV_DATASET_PATH, raising=False)
        path = default_dataset_path()
        # Path resolution should not raise and should expand ~.
        assert "~" not in str(path)

    def test_dataset_available_missing_dir(self, tmp_path):
        missing = tmp_path / "nope"
        assert dataset_available(missing) is False

    def test_dataset_available_empty_dir(self, tmp_path):
        assert dataset_available(tmp_path) is False

    def test_dataset_available_with_file(self, tmp_path):
        (tmp_path / "retail_train.json").write_text("[]")
        assert dataset_available(tmp_path) is True
        assert dataset_available(tmp_path, domain="retail") is True
        assert dataset_available(tmp_path, domain="airline") is False


# ----------------------------------------------------------------------
# Task loading + normalisation
# ----------------------------------------------------------------------


class TestTaskLoading:
    @pytest.fixture
    def retail_tasks_dir(self, tmp_path):
        tasks = [
            {
                "user_id": "u1",
                "instruction": "Cancel my last order.",
                "actions": [
                    {"name": "find_user", "arguments": {"id": "u1"}},
                    {"name": "cancel_order", "arguments": {"order_id": "o-99"}},
                ],
            },
            {
                "user_id": "u2",
                "instruction": "Refund order o-42.",
                "actions": [
                    {"name": "refund_order", "arguments": {"order_id": "o-42"}},
                ],
            },
        ]
        (tmp_path / "retail_train.json").write_text(json.dumps(tasks))
        return tmp_path

    def test_skip_path_when_dataset_absent(self, tmp_path):
        bench = TauBench(domain="airline", dataset_path=str(tmp_path / "missing"))
        # Loader returns [] cleanly; CLI / harness can pre-flight via
        # dataset_available() and emit a friendly skip.
        assert bench.tasks() == []

    def test_dataset_absent_skip_message(self, tmp_path, capsys):
        # Mirror the CLI skip path: the loader returns [], and the caller
        # must surface a friendly setup hint. We exercise the import +
        # call path here without invoking the agent stack.
        from chimera.eval.benchmarks.tau_bench import _SETUP_HINT

        msg = _SETUP_HINT.format(path=tmp_path / "missing")
        assert "tau-bench dataset not found" in msg
        assert "CHIMERA_TAU_BENCH_PATH" in msg

    def test_loads_tasks_from_directory(self, retail_tasks_dir):
        bench = TauBench(domain="retail", dataset_path=str(retail_tasks_dir))
        tasks = bench.tasks()
        assert len(tasks) == 2
        assert tasks[0]["id"] == "retail-0"
        assert tasks[0]["domain"] == "retail"
        # instruction -> prompt mapping
        assert tasks[0]["prompt"] == "Cancel my last order."

    def test_loads_tasks_from_single_file(self, retail_tasks_dir):
        f = retail_tasks_dir / "retail_train.json"
        bench = TauBench(domain="retail", dataset_path=str(f))
        assert len(bench.tasks()) == 2

    def test_limit_applied(self, retail_tasks_dir):
        bench = TauBench(
            domain="retail", dataset_path=str(retail_tasks_dir), limit=1
        )
        assert len(bench.tasks()) == 1

    def test_filters_by_domain(self, tmp_path):
        # Two domain files; loader should pick only the configured one.
        (tmp_path / "retail.json").write_text(json.dumps([{"instruction": "r"}]))
        (tmp_path / "airline.json").write_text(json.dumps([{"instruction": "a"}]))
        retail = TauBench(domain="retail", dataset_path=str(tmp_path)).tasks()
        airline = TauBench(domain="airline", dataset_path=str(tmp_path)).tasks()
        assert len(retail) == 1 and retail[0]["prompt"] == "r"
        assert len(airline) == 1 and airline[0]["prompt"] == "a"

    def test_handles_wrapped_dict(self, tmp_path):
        (tmp_path / "airline.json").write_text(
            json.dumps({"tasks": [{"instruction": "hi"}]})
        )
        bench = TauBench(domain="airline", dataset_path=str(tmp_path))
        assert len(bench.tasks()) == 1

    def test_handles_corrupt_file(self, tmp_path):
        (tmp_path / "airline.json").write_text("{not json")
        bench = TauBench(domain="airline", dataset_path=str(tmp_path))
        assert bench.tasks() == []

    def test_invalid_domain_raises(self):
        with pytest.raises(ValueError, match="Unknown tau-bench domain"):
            TauBench(domain="quantum")

    def test_invalid_num_trials_raises(self):
        with pytest.raises(ValueError, match="num_trials"):
            TauBench(domain="airline", num_trials=0)

    def test_name(self):
        assert TauBench(domain="retail").name() == "tau-bench:retail"


# ----------------------------------------------------------------------
# Evaluation / scoring
# ----------------------------------------------------------------------


class TestEvaluate:
    def test_empty_output_fails(self):
        bench = TauBench(domain="mock")
        assert bench.evaluate({"actions": [{"name": "x"}]}, "", None) is False

    def test_no_goal_no_actions_fails(self):
        bench = TauBench(domain="mock")
        assert bench.evaluate({}, "anything", None) is False

    def test_goal_state_match(self):
        bench = TauBench(domain="mock")
        task = {"goal_state": {"orders": []}}
        out = json.dumps({"final_state": {"orders": []}})
        assert bench.evaluate(task, out, None) is True

    def test_goal_state_mismatch(self):
        bench = TauBench(domain="mock")
        task = {"goal_state": {"orders": []}}
        out = json.dumps({"final_state": {"orders": ["o-1"]}})
        assert bench.evaluate(task, out, None) is False

    def test_terminal_action_json_match(self):
        bench = TauBench(domain="mock")
        task = {
            "actions": [
                {"name": "find_user", "arguments": {"id": "u1"}},
                {"name": "cancel_order", "arguments": {"order_id": "o-99"}},
            ]
        }
        out = json.dumps(
            {
                "actions": [
                    {"name": "find_user", "arguments": {"id": "u1"}},
                    {"name": "cancel_order", "arguments": {"order_id": "o-99"}},
                ]
            }
        )
        assert bench.evaluate(task, out, None) is True

    def test_terminal_action_argument_mismatch(self):
        bench = TauBench(domain="mock")
        task = {
            "actions": [
                {"name": "cancel_order", "arguments": {"order_id": "o-99"}},
            ]
        }
        out = json.dumps(
            {"actions": [{"name": "cancel_order", "arguments": {"order_id": "o-1"}}]}
        )
        assert bench.evaluate(task, out, None) is False

    def test_terminal_action_string_fallback(self):
        bench = TauBench(domain="mock")
        task = {"actions": [{"name": "cancel_order", "arguments": {"id": "x"}}]}
        # Plain text containing the terminal action name still scores.
        assert bench.evaluate(task, "I will call cancel_order(...)", None) is True

    def test_terminal_action_string_fallback_miss(self):
        bench = TauBench(domain="mock")
        task = {"actions": [{"name": "cancel_order"}]}
        assert bench.evaluate(task, "I'll think about it.", None) is False

    def test_env_evaluator_preferred(self):
        bench = TauBench(domain="mock")

        class Env:
            def evaluate_task(self, task, output):
                return True

        assert bench.evaluate({"actions": [{"name": "x"}]}, "noop", Env()) is True

    def test_env_evaluator_exception_returns_false(self):
        bench = TauBench(domain="mock")

        class Env:
            def evaluate_task(self, task, output):
                raise RuntimeError("boom")

        assert bench.evaluate({"actions": [{"name": "x"}]}, "noop", Env()) is False


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


class TestHelpers:
    def test_terminal_action_name_dict(self):
        assert _terminal_action_name([{"name": "a"}, {"name": "b"}]) == "b"

    def test_terminal_action_name_string(self):
        assert _terminal_action_name(["a", "b"]) == "b"

    def test_terminal_action_name_empty(self):
        assert _terminal_action_name([]) is None
        assert _terminal_action_name(None) is None

    def test_action_equal_dicts(self):
        a = {"name": "x", "arguments": {"id": 1}}
        b = {"name": "x", "arguments": {"id": 1}}
        assert _action_equal(a, b) is True

    def test_action_equal_dict_vs_string(self):
        assert _action_equal({"name": "x"}, "x") is True
        assert _action_equal("x", {"name": "x"}) is True
        assert _action_equal({"name": "x"}, "y") is False

    def test_actions_match_terminal_only(self):
        # tau-bench scores by terminal action: only the last entry must
        # match. Earlier divergence is allowed.
        agent = [{"name": "lookup"}, {"name": "cancel"}]
        expected = [{"name": "wholly_different"}, {"name": "cancel"}]
        assert _actions_match(agent, expected) is True

    def test_actions_match_empty(self):
        assert _actions_match([], [{"name": "x"}]) is False
        assert _actions_match([{"name": "x"}], []) is False


class TestFormatTable:
    def test_handles_empty(self):
        assert _format_table([], use_color=False) == "(no tasks)"

    def test_no_color_renders(self):
        out = _format_table(
            [("retail-0", "True", "ok"), ("retail-1", "False", "miss")],
            use_color=False,
        )
        assert "retail-0" in out
        assert "True" in out
        assert "\033[" not in out

    def test_color_emits_escapes(self):
        out = _format_table(
            [("retail-0", "True", "ok")],
            use_color=True,
        )
        assert "\033[32m" in out  # green for pass


# ----------------------------------------------------------------------
# Module is importable as a script entry point
# ----------------------------------------------------------------------


class TestCliModule:
    def test_module_loads_without_side_effects(self):
        import importlib

        mod = importlib.import_module("chimera.eval.benchmarks.tau_bench")
        assert hasattr(mod, "_run_cli")
        assert callable(mod._run_cli)

    def test_cli_emits_setup_hint_when_dataset_missing(
        self, tmp_path, capsys, monkeypatch
    ):
        from chimera.eval.benchmarks import tau_bench as mod

        monkeypatch.setenv(ENV_DATASET_PATH, str(tmp_path / "missing"))
        rc = mod._run_cli(["--domain", "airline", "--limit", "1", "--no-color"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "tau-bench dataset not found" in captured.out


# ----------------------------------------------------------------------
# Smoke: loader is import-clean even when path is the real default
# ----------------------------------------------------------------------


def test_default_dataset_path_does_not_raise(monkeypatch):
    monkeypatch.delenv(ENV_DATASET_PATH, raising=False)
    p = default_dataset_path()
    assert isinstance(p, Path)
