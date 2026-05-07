"""Smoke tests for the SWE-Lancer scaffold."""
from __future__ import annotations

import json

import pytest

from chimera.eval.benchmarks.swe_lancer import (
    TASK_CATEGORIES,
    SWELancer,
    SWELancerTask,
)


class TestImportAndConstruction:
    def test_imports_via_package(self):
        from chimera.eval.benchmarks import SWELancer as SWELancerFromPkg

        assert SWELancerFromPkg is SWELancer

    def test_default_construction(self):
        bench = SWELancer()
        assert bench.name() == "swe-lancer"
        assert bench.tasks() == []

    def test_unsupported_category_rejected(self):
        with pytest.raises(ValueError):
            SWELancer(category="ceo")

    def test_categories(self):
        assert TASK_CATEGORIES == frozenset({"ic_swe", "swe_manager"})


class TestLoading:
    @pytest.fixture
    def dataset(self, tmp_path):
        items = [
            {
                "task_id": "sl_1",
                "title": "Fix login button",
                "description": "Login fails on Safari.",
                "payout_usd": 250.0,
                "category": "ic_swe",
                "repo": "expensify/app",
                "base_commit": "abc123",
            },
            {
                "task_id": "sl_2",
                "title": "Pick best PR",
                "description": "Choose the right fix.",
                "payout_usd": 100.0,
                "category": "swe_manager",
                "choices": ["patch_a", "patch_b", "patch_c"],
                "correct_choice": 1,
            },
            {
                "task_id": "sl_3",
                "title": "Cheap task",
                "description": "Skip in min_payout filter.",
                "payout_usd": 5.0,
                "category": "ic_swe",
            },
            {
                "task_id": "sl_unknown",
                "title": "Unknown category",
                "description": "filtered",
                "category": "founder",
            },
        ]
        path = tmp_path / "swl.json"
        path.write_text(json.dumps(items))
        return str(path)

    def test_loads_known_categories(self, dataset):
        bench = SWELancer(dataset_path=dataset)
        ids = {t["id"] for t in bench.tasks()}
        assert ids == {"sl_1", "sl_2", "sl_3"}

    def test_min_payout_filter(self, dataset):
        bench = SWELancer(dataset_path=dataset, min_payout=50.0)
        ids = {t["id"] for t in bench.tasks()}
        assert ids == {"sl_1", "sl_2"}

    def test_category_filter(self, dataset):
        bench = SWELancer(dataset_path=dataset, category="swe_manager")
        assert {t["id"] for t in bench.tasks()} == {"sl_2"}

    def test_name_with_category(self):
        bench = SWELancer(category="ic_swe")
        assert bench.name() == "swe-lancer-ic_swe"


class TestEvaluate:
    def test_evaluate_raises_not_implemented(self):
        bench = SWELancer()
        with pytest.raises(NotImplementedError):
            bench.evaluate({"id": "x"}, "irrelevant", env=None)

    def test_grade_manager_choice_correct(self):
        bench = SWELancer()
        task = {
            "category": "swe_manager",
            "choices": ["a", "b", "c"],
            "correct_choice": 2,
        }
        assert bench.grade_manager_choice(task, 2) is True

    def test_grade_manager_choice_wrong(self):
        bench = SWELancer()
        task = {
            "category": "swe_manager",
            "choices": ["a", "b"],
            "correct_choice": 0,
        }
        assert bench.grade_manager_choice(task, 1) is False

    def test_grade_manager_choice_rejects_ic_task(self):
        bench = SWELancer()
        task = {"category": "ic_swe"}
        assert bench.grade_manager_choice(task, 0) is False

    def test_dollar_weighted_pass_rate(self):
        bench = SWELancer()
        bench.add_instance(
            SWELancerTask(
                task_id="a", title="", description="", payout_usd=300.0
            )
        )
        bench.add_instance(
            SWELancerTask(
                task_id="b", title="", description="", payout_usd=100.0
            )
        )
        rate = bench.dollar_weighted_pass_rate([("a", True), ("b", False)])
        assert rate == pytest.approx(0.75)

    def test_dollar_weighted_pass_rate_empty(self):
        bench = SWELancer()
        assert bench.dollar_weighted_pass_rate([]) == 0.0


class TestShape:
    def test_to_dict(self):
        t = SWELancerTask(
            task_id="x",
            title="y",
            description="z",
            payout_usd=10.0,
            category="ic_swe",
        )
        d = t.to_dict()
        assert d["id"] == "x" and d["payout_usd"] == 10.0

    def test_add_instance(self):
        bench = SWELancer()
        bench.add_instance(
            SWELancerTask(task_id="t", title="", description="")
        )
        assert len(bench.tasks()) == 1
