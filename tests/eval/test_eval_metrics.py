from __future__ import annotations

from dataclasses import dataclass

import pytest

from chimera.eval.metrics import avg_cost, avg_steps, pass_at_k, resolve_rate


@dataclass
class FakeResult:
    passed: bool
    cost: float
    steps: int


class TestPassAtK:
    def test_basic_calculation(self):
        # n=10 samples, c=3 correct, k=1
        result = pass_at_k(10, 3, 1)
        assert 0.0 < result < 1.0
        # With 3/10 correct, pass@1 should be around 0.3
        assert abs(result - 0.3) < 0.01

    def test_all_correct(self):
        # All 10 samples correct -> pass@k = 1.0
        assert pass_at_k(10, 10, 1) == 1.0
        assert pass_at_k(10, 10, 5) == 1.0

    def test_none_correct(self):
        # No correct samples -> pass@1 = 0.0
        assert pass_at_k(10, 0, 1) == 0.0

    def test_k_equals_n(self):
        # k == n, with at least 1 correct -> guaranteed pass
        assert pass_at_k(5, 1, 5) == 1.0

    def test_high_k_with_enough_correct(self):
        # n=10, c=8, k=3 -> n-c=2 < k=3 -> should return 1.0
        assert pass_at_k(10, 8, 3) == 1.0

    def test_n_less_than_k_raises(self):
        with pytest.raises(ValueError, match="must be >= k"):
            pass_at_k(3, 2, 5)


class TestAvgCost:
    def test_average_cost(self):
        results = [FakeResult(True, 0.10, 5), FakeResult(False, 0.20, 3)]
        assert avg_cost(results) == pytest.approx(0.15)

    def test_empty(self):
        assert avg_cost([]) == 0.0


class TestAvgSteps:
    def test_average_steps(self):
        results = [FakeResult(True, 0.1, 4), FakeResult(True, 0.1, 6)]
        assert avg_steps(results) == pytest.approx(5.0)

    def test_empty(self):
        assert avg_steps([]) == 0.0


class TestResolveRate:
    def test_resolve_rate(self):
        results = [
            FakeResult(True, 0.1, 1),
            FakeResult(False, 0.1, 1),
            FakeResult(True, 0.1, 1),
            FakeResult(True, 0.1, 1),
        ]
        assert resolve_rate(results) == pytest.approx(0.75)

    def test_empty(self):
        assert resolve_rate([]) == 0.0

    def test_all_pass(self):
        results = [FakeResult(True, 0.1, 1) for _ in range(5)]
        assert resolve_rate(results) == 1.0

    def test_none_pass(self):
        results = [FakeResult(False, 0.1, 1) for _ in range(5)]
        assert resolve_rate(results) == 0.0
