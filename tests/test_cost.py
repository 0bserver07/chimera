# tests/test_cost.py
"""Tests for provider cost calculation."""
from __future__ import annotations

from chimera.providers.cost import calculate_cost, PRICING


class TestCalculateCost:
    def test_anthropic_sonnet(self):
        cost = calculate_cost("claude-sonnet-4-20250514", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        # sonnet: $3/M input, $15/M output
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_anthropic_opus(self):
        cost = calculate_cost("claude-opus-4-20250514", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        expected = (1000 * 15.0 + 500 * 75.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_anthropic_haiku(self):
        cost = calculate_cost("claude-haiku-3.5-20241022", {
            "input_tokens": 10000,
            "output_tokens": 2000,
        })
        expected = (10000 * 0.80 + 2000 * 4.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_openai_gpt4o(self):
        cost = calculate_cost("gpt-4o", {
            "input_tokens": 5000,
            "output_tokens": 1000,
        })
        expected = (5000 * 2.50 + 1000 * 10.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_openai_gpt4o_mini(self):
        cost = calculate_cost("gpt-4o-mini", {
            "input_tokens": 5000,
            "output_tokens": 1000,
        })
        expected = (5000 * 0.15 + 1000 * 0.60) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_unknown_model_returns_zero(self):
        cost = calculate_cost("unknown-model-v1", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        assert cost == 0.0

    def test_empty_usage(self):
        cost = calculate_cost("claude-sonnet-4-20250514", {})
        assert cost == 0.0

    def test_ollama_returns_zero(self):
        cost = calculate_cost("llama3.1:8b", {
            "input_tokens": 10000,
            "output_tokens": 5000,
        })
        assert cost == 0.0

    def test_pricing_dict_exists(self):
        assert isinstance(PRICING, dict)
        assert len(PRICING) > 0
