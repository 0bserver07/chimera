"""Smoke tests for the NoCha long-context scaffold."""
from __future__ import annotations

import json

import pytest

from chimera.eval.benchmarks.nocha import NoCha, NoChaInstance


class TestImportAndConstruction:
    def test_imports_via_package(self):
        from chimera.eval.benchmarks import NoCha as NoChaFromPkg

        assert NoChaFromPkg is NoCha

    def test_default_construction(self):
        bench = NoCha()
        assert bench.name() == "nocha"
        assert bench.tasks() == []


class TestLoading:
    @pytest.fixture
    def dataset(self, tmp_path):
        items = [
            {
                "instance_id": "n1",
                "document": "DOC1",
                "true_claim": "T1",
                "false_claim": "F1",
                "token_count": 60_000,
                "domain": "code",
            },
            {
                "instance_id": "n2",
                "document": "DOC2",
                "true_claim": "T2",
                "false_claim": "F2",
                "token_count": 10_000,
                "domain": "code",
            },
            {
                "instance_id": "n3",
                "document": "DOC3",
                "true_claim": "T3",
                "false_claim": "F3",
                "token_count": 75_000,
                "domain": "book",
            },
        ]
        path = tmp_path / "nocha.json"
        path.write_text(json.dumps(items))
        return str(path)

    def test_load_all(self, dataset):
        bench = NoCha(dataset_path=dataset)
        assert len(bench.tasks()) == 3

    def test_filter_by_domain(self, dataset):
        bench = NoCha(dataset_path=dataset, domain="code")
        ids = {t["id"] for t in bench.tasks()}
        assert ids == {"n1", "n2"}

    def test_filter_by_min_tokens(self, dataset):
        bench = NoCha(dataset_path=dataset, min_tokens=50_000)
        ids = {t["id"] for t in bench.tasks()}
        assert ids == {"n1", "n3"}

    def test_filter_by_max_tokens(self, dataset):
        bench = NoCha(dataset_path=dataset, max_tokens=20_000)
        ids = {t["id"] for t in bench.tasks()}
        assert ids == {"n2"}

    def test_long_context_share(self, dataset):
        bench = NoCha(dataset_path=dataset)
        # 2/3 instances >= 50k tokens
        assert bench.long_context_share(threshold=50_000) == pytest.approx(2 / 3)

    def test_name_with_domain(self):
        bench = NoCha(domain="code")
        assert bench.name() == "nocha-code"

    def test_missing_dataset_raises(self):
        with pytest.raises(FileNotFoundError):
            NoCha(dataset_path="/no/such/file.json")


class TestEvaluate:
    @pytest.fixture
    def task(self):
        return {
            "id": "n1",
            "true_claim": "x",
            "false_claim": "y",
        }

    @pytest.mark.parametrize(
        "answer, expected",
        [
            ("A", True),
            ("a", True),
            ("B", False),
            ("Answer: A", True),
            ("Answer: B.", False),
            ("I think A.", True),
            ("", False),
            ("neither", False),
        ],
    )
    def test_parse_choice(self, task, answer, expected):
        assert NoCha().evaluate(task, answer) is expected


class TestShape:
    def test_to_dict_includes_prompt(self):
        inst = NoChaInstance(
            instance_id="n",
            document="D",
            true_claim="T",
            false_claim="F",
            token_count=42,
        )
        d = inst.to_dict()
        assert d["id"] == "n"
        assert "CLAIM A: T" in d["prompt"]
        assert "CLAIM B: F" in d["prompt"]

    def test_add_instance(self):
        bench = NoCha()
        bench.add_instance(
            NoChaInstance(
                instance_id="n",
                document="d",
                true_claim="t",
                false_claim="f",
            )
        )
        assert len(bench.tasks()) == 1
