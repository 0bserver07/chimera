# tests/test_cost.py
"""Tests for provider cost calculation."""
from __future__ import annotations

from chimera.providers.cost import calculate_cost, register_model_cost, PRICING


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


class TestRegisterModelCost:
    def test_register_new_model(self):
        key = "my-custom-model"
        assert key not in PRICING
        register_model_cost(key, 5.0, 10.0)
        try:
            cost = calculate_cost(key, {"input_tokens": 1_000_000, "output_tokens": 0})
            assert abs(cost - 5.0) < 1e-9
        finally:
            del PRICING[key]

    def test_override_existing_model(self):
        key = "gpt-4o"
        original = PRICING[key]
        register_model_cost(key, 1.0, 2.0)
        try:
            cost = calculate_cost(key, {"input_tokens": 1_000_000, "output_tokens": 0})
            assert abs(cost - 1.0) < 1e-9
        finally:
            PRICING[key] = original

    def test_glm5_pricing_exists(self):
        cost = calculate_cost("glm-5", {"input_tokens": 1_000_000, "output_tokens": 0})
        assert abs(cost - 2.0) < 1e-9

    def test_deepseek_pricing_exists(self):
        cost = calculate_cost("deepseek-chat", {"input_tokens": 1_000_000, "output_tokens": 0})
        assert abs(cost - 0.27) < 1e-9


class TestRefreshedCatalog:
    """Cover the 2025/2026 model IDs added in P1-CATALOG."""

    def test_claude_opus_4_7(self):
        cost = calculate_cost("claude-opus-4-7", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        # Opus 4.5+: $5 in / $25 out per 1M
        assert abs(cost - 30.0) < 1e-9

    def test_claude_opus_4_7_dated_id(self):
        # Real Anthropic IDs include date suffixes; longest-prefix match must win.
        cost = calculate_cost("claude-opus-4-7-20260315", {
            "input_tokens": 1_000_000, "output_tokens": 0,
        })
        assert abs(cost - 5.0) < 1e-9

    def test_claude_opus_4_1_keeps_legacy_rate(self):
        # 4.0 / 4.1 stay at the pre-4.5 rate; the 4.5+ repricing must not leak down.
        cost = calculate_cost("claude-opus-4-1", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        assert abs(cost - 90.0) < 1e-9

    def test_claude_haiku_4_5(self):
        cost = calculate_cost("claude-haiku-4-5", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        # Haiku 4.5: $1 in / $5 out per 1M
        assert abs(cost - 6.0) < 1e-9

    def test_claude_sonnet_4_5_dated_id(self):
        cost = calculate_cost("claude-sonnet-4-5-20250929", {
            "input_tokens": 1_000_000, "output_tokens": 0,
        })
        assert abs(cost - 3.0) < 1e-9

    def test_gpt_5(self):
        cost = calculate_cost("gpt-5", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        # GPT-5: $1.25 in / $10 out per 1M
        assert abs(cost - 11.25) < 1e-9

    def test_gpt_5_mini_longest_prefix(self):
        # gpt-5-mini must NOT collapse onto gpt-5 pricing.
        cost = calculate_cost("gpt-5-mini", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        assert abs(cost - 2.25) < 1e-9

    def test_gpt_5_nano_longest_prefix(self):
        cost = calculate_cost("gpt-5-nano", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        assert abs(cost - 0.45) < 1e-9

    def test_o3_distinct_from_o3_mini(self):
        # "o3" prefix must match a bare o3 id without colliding with o3-mini.
        cost = calculate_cost("o3", {
            "input_tokens": 1_000_000, "output_tokens": 0,
        })
        assert abs(cost - 2.0) < 1e-9

    def test_o3_mini_longest_prefix(self):
        cost = calculate_cost("o3-mini", {
            "input_tokens": 1_000_000, "output_tokens": 0,
        })
        assert abs(cost - 1.10) < 1e-9

    def test_gemini_2_5_pro(self):
        cost = calculate_cost("gemini-2.5-pro", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        # Gemini 2.5 Pro (≤200K tier): $1.25 in / $10 out per 1M
        assert abs(cost - 11.25) < 1e-9

    def test_gemini_2_5_flash(self):
        cost = calculate_cost("gemini-2.5-flash", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
        })
        # Gemini 2.5 Flash: $0.30 in / $2.50 out per 1M
        assert abs(cost - 2.80) < 1e-9
