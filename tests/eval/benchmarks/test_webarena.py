"""Tests for the WebArena benchmark adapter.

Covers:
    - dataset-absent skip path
    - dataset resolution (env var + default)
    - task loading + normalisation (JSON + JSONL)
    - load/score round-trip with a synthetic 2-task dataset
    - string_match scoring (simple + compound ``reference_answers``)
    - url_match scoring
    - combined eval-types AND semantics
    - unsupported eval types fail closed
    - upstream env evaluator escape hatch
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.eval.benchmarks.webarena import (
    ENV_DATASET_PATH,
    EVAL_TYPE_PROGRAM_HTML,
    EVAL_TYPE_STRING_MATCH,
    EVAL_TYPE_URL_MATCH,
    WebArena,
    _format_prompt,
    _split_agent_output,
    _urls_equivalent,
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
        assert "~" not in str(path)

    def test_dataset_available_missing(self, tmp_path):
        assert dataset_available(tmp_path / "missing") is False

    def test_dataset_available_empty_dir(self, tmp_path):
        assert dataset_available(tmp_path) is False

    def test_dataset_available_with_json(self, tmp_path):
        (tmp_path / "tasks.json").write_text("[]")
        assert dataset_available(tmp_path) is True

    def test_dataset_available_with_jsonl(self, tmp_path):
        (tmp_path / "tasks.jsonl").write_text("")
        assert dataset_available(tmp_path) is True

    def test_dataset_available_with_file_path(self, tmp_path):
        f = tmp_path / "tasks.json"
        f.write_text("[]")
        assert dataset_available(f) is True


# ----------------------------------------------------------------------
# Synthetic 2-task fixture (we DO NOT vendor upstream data)
# ----------------------------------------------------------------------


@pytest.fixture
def synthetic_dataset(tmp_path: Path) -> Path:
    """Two-task dataset matching the WebArena task shape.

    Synthetic only — no upstream content. One task is scored by
    ``string_match``, the other by ``url_match``.
    """
    tasks = [
        {
            "task_id": 1,
            "intent": "What is the most expensive product on the storefront?",
            "start_url": "http://shop.example.test",
            "sites": ["shopping"],
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answer": "Widget Pro Max",
        },
        {
            "task_id": 2,
            "intent": "Open the GitLab repository for chimera.",
            "start_url": "http://gitlab.example.test",
            "sites": ["gitlab"],
            "eval_types": [EVAL_TYPE_URL_MATCH],
            "reference_url": "http://gitlab.example.test/0bserver07/chimera",
        },
    ]
    (tmp_path / "test.json").write_text(json.dumps(tasks))
    return tmp_path


# ----------------------------------------------------------------------
# Skip path + loading + round-trip
# ----------------------------------------------------------------------


class TestLoadingAndSkip:
    def test_dataset_absent_returns_empty(self, tmp_path):
        bench = WebArena(dataset_path=str(tmp_path / "missing"))
        assert bench.tasks() == []

    def test_setup_hint_mentions_env_var(self, tmp_path):
        from chimera.eval.benchmarks.webarena import _SETUP_HINT

        msg = _SETUP_HINT.format(path=tmp_path / "missing")
        assert "WebArena dataset not found" in msg
        assert "CHIMERA_WEBARENA_PATH" in msg

    def test_loads_synthetic_directory(self, synthetic_dataset):
        bench = WebArena(dataset_path=str(synthetic_dataset))
        tasks = bench.tasks()
        assert len(tasks) == 2
        assert tasks[0]["id"] == "webarena-1"
        assert "shop.example.test" in tasks[0]["prompt"]
        assert tasks[0]["eval_types"] == [EVAL_TYPE_STRING_MATCH]

    def test_loads_synthetic_single_file(self, synthetic_dataset):
        f = synthetic_dataset / "test.json"
        bench = WebArena(dataset_path=str(f))
        assert len(bench.tasks()) == 2

    def test_jsonl_loader(self, tmp_path):
        recs = [
            {"task_id": 10, "intent": "x", "eval_types": [EVAL_TYPE_STRING_MATCH],
             "reference_answer": "ok"},
            {"task_id": 11, "intent": "y", "eval_types": [EVAL_TYPE_STRING_MATCH],
             "reference_answer": "ok"},
        ]
        (tmp_path / "tasks.jsonl").write_text(
            "\n".join(json.dumps(r) for r in recs) + "\n"
        )
        bench = WebArena(dataset_path=str(tmp_path))
        assert len(bench.tasks()) == 2

    def test_handles_wrapped_dict(self, tmp_path):
        (tmp_path / "wrapped.json").write_text(
            json.dumps({"tasks": [{"task_id": 1, "intent": "hi"}]})
        )
        bench = WebArena(dataset_path=str(tmp_path))
        tasks = bench.tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "webarena-1"

    def test_handles_corrupt_file(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json")
        bench = WebArena(dataset_path=str(tmp_path))
        assert bench.tasks() == []

    def test_limit_applied(self, synthetic_dataset):
        bench = WebArena(dataset_path=str(synthetic_dataset), limit=1)
        assert len(bench.tasks()) == 1

    def test_site_filter(self, synthetic_dataset):
        bench = WebArena(dataset_path=str(synthetic_dataset), sites=("gitlab",))
        tasks = bench.tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "webarena-2"

    def test_name(self):
        assert WebArena().name() == "webarena"

    def test_round_trip_load_and_score(self, synthetic_dataset):
        """Load 2 tasks, score correct answers, all should pass."""
        bench = WebArena(dataset_path=str(synthetic_dataset))
        tasks = bench.tasks()
        assert len(tasks) == 2

        # Task 1: string_match
        t1 = next(t for t in tasks if t["id"] == "webarena-1")
        assert bench.evaluate(t1, "Widget Pro Max", None) is True
        assert bench.evaluate(t1, "Some Other Thing", None) is False

        # Task 2: url_match — agent declares its final URL.
        t2 = next(t for t in tasks if t["id"] == "webarena-2")
        out_ok = "URL: http://gitlab.example.test/0bserver07/chimera"
        out_bad = "URL: http://gitlab.example.test/someone/else"
        assert bench.evaluate(t2, out_ok, None) is True
        assert bench.evaluate(t2, out_bad, None) is False


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


class TestStringMatch:
    def test_simple_reference_answer(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answer": "42",
        }
        assert bench.evaluate(task, "42", None) is True
        assert bench.evaluate(task, "  42 ", None) is True
        assert bench.evaluate(task, "43", None) is False

    def test_reference_answer_list_any(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answer": ["alpha", "beta"],
        }
        assert bench.evaluate(task, "Beta", None) is True
        assert bench.evaluate(task, "gamma", None) is False

    def test_compound_must_include(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answers": {"must_include": ["error", "404"]},
        }
        assert bench.evaluate(task, "Got error 404 on page", None) is True
        assert bench.evaluate(task, "Got error", None) is False

    def test_compound_exact_match(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answers": {"exact_match": "yes"},
        }
        assert bench.evaluate(task, "Yes", None) is True
        assert bench.evaluate(task, "yes please", None) is False

    def test_compound_fuzzy_match(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answers": {"fuzzy_match": "san francisco"},
        }
        assert bench.evaluate(task, "San Francisco, CA", None) is True
        assert bench.evaluate(task, "New York", None) is False

    def test_empty_compound_fails(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH],
            "reference_answers": {},
        }
        assert bench.evaluate(task, "anything", None) is False

    def test_no_reference_fails(self):
        bench = WebArena()
        task = {"eval_types": [EVAL_TYPE_STRING_MATCH]}
        assert bench.evaluate(task, "anything", None) is False


class TestUrlMatch:
    def test_url_path_match_ignoring_query(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_URL_MATCH],
            "reference_url": "http://x.test/orders",
        }
        out = "URL: http://x.test/orders?ref=1"
        assert bench.evaluate(task, out, None) is True

    def test_url_mismatch(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_URL_MATCH],
            "reference_url": "http://x.test/orders",
        }
        assert bench.evaluate(task, "URL: http://x.test/cart", None) is False

    def test_url_missing_in_output_fails(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_URL_MATCH],
            "reference_url": "http://x.test/orders",
        }
        assert bench.evaluate(task, "I navigated there.", None) is False

    def test_json_envelope(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_URL_MATCH],
            "reference_url": "http://x.test/orders",
        }
        out = json.dumps({"answer": "done", "url": "http://x.test/orders"})
        assert bench.evaluate(task, out, None) is True


class TestCombinedEvalTypes:
    def test_all_must_pass(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH, EVAL_TYPE_URL_MATCH],
            "reference_answer": "ok",
            "reference_url": "http://x.test/done",
        }
        good = "ANSWER: ok\nURL: http://x.test/done"
        bad_url = "ANSWER: ok\nURL: http://x.test/wrong"
        bad_ans = "ANSWER: nope\nURL: http://x.test/done"
        assert bench.evaluate(task, good, None) is True
        assert bench.evaluate(task, bad_url, None) is False
        assert bench.evaluate(task, bad_ans, None) is False


class TestUnsupportedEvalTypes:
    def test_program_html_fails_closed(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_PROGRAM_HTML],
            "reference_answer": "ignored",
        }
        # We deliberately fail when only a deferred eval type is declared
        # so a stub never falsely scores.
        assert bench.evaluate(task, "anything", None) is False

    def test_mixed_with_deferred_fails(self):
        bench = WebArena()
        task = {
            "eval_types": [EVAL_TYPE_STRING_MATCH, EVAL_TYPE_PROGRAM_HTML],
            "reference_answer": "ok",
        }
        assert bench.evaluate(task, "ok", None) is False

    def test_missing_eval_types_fails(self):
        bench = WebArena()
        assert bench.evaluate({}, "anything", None) is False


class TestEnvEscapeHatch:
    def test_env_evaluator_preferred(self):
        bench = WebArena()

        class Env:
            def evaluate_task(self, task, output):
                return True

        task = {"eval_types": [EVAL_TYPE_PROGRAM_HTML]}
        assert bench.evaluate(task, "noop", Env()) is True

    def test_env_evaluator_exception_returns_false(self):
        bench = WebArena()

        class Env:
            def evaluate_task(self, task, output):
                raise RuntimeError("boom")

        task = {"eval_types": [EVAL_TYPE_STRING_MATCH], "reference_answer": "x"}
        assert bench.evaluate(task, "x", Env()) is False


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


class TestSplitOutput:
    def test_json_envelope(self):
        out = json.dumps({"answer": "hi", "url": "http://x.test/"})
        ans, url = _split_agent_output(out)
        assert ans == "hi"
        assert url == "http://x.test/"

    def test_named_lines(self):
        ans, url = _split_agent_output("ANSWER: yo\nURL: http://x.test/p")
        assert ans == "yo"
        assert url == "http://x.test/p"

    def test_plain_text(self):
        ans, url = _split_agent_output("just a string")
        assert ans == "just a string"
        assert url is None

    def test_url_only(self):
        ans, url = _split_agent_output("URL: http://x.test/p")
        assert url == "http://x.test/p"
        # The full output is the answer when no ANSWER: line is present.
        assert "URL:" in ans


class TestUrlsEquivalent:
    def test_equal_with_query_diff(self):
        assert _urls_equivalent("http://x.test/p", "http://x.test/p?q=1") is True

    def test_trailing_slash_ignored(self):
        assert _urls_equivalent("http://x.test/p/", "http://x.test/p") is True

    def test_different_host(self):
        assert _urls_equivalent("http://x.test/p", "http://y.test/p") is False

    def test_scheme_optional(self):
        assert _urls_equivalent("//x.test/p", "http://x.test/p") is True


class TestFormatPrompt:
    def test_includes_intent_and_url(self):
        prompt = _format_prompt(
            {
                "intent": "Find the price.",
                "start_url": "http://shop.test",
                "sites": ["shopping"],
            }
        )
        assert "Find the price." in prompt
        assert "http://shop.test" in prompt
        assert "shopping" in prompt

    def test_handles_missing_fields(self):
        prompt = _format_prompt({})
        # We always include the response convention reminder.
        assert "URL:" in prompt


# ----------------------------------------------------------------------
# Module / registration
# ----------------------------------------------------------------------


class TestRegistration:
    def test_module_loads(self):
        import importlib

        mod = importlib.import_module("chimera.eval.benchmarks.webarena")
        assert hasattr(mod, "WebArena")

    def test_exported_from_package(self):
        from chimera.eval.benchmarks import WebArena as Exported

        assert Exported is WebArena
