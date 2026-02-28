# chimera/providers/cost.py
"""Token cost calculation for LLM providers."""
from __future__ import annotations

import threading

# Pricing: model_prefix -> (input_cost_per_million, output_cost_per_million)
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-3.5": (0.80, 4.0),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "o1": (15.0, 60.0),
    "o3-mini": (1.10, 4.40),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # GLM
    "glm-5": (2.0, 8.0),
    "glm-4-plus": (1.0, 4.0),
    "glm-4-flash": (0.04, 0.04),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}

_pricing_lock = threading.Lock()


def calculate_cost(model: str, usage: dict[str, int]) -> float:
    """Calculate the dollar cost of an API call.

    Args:
        model: Model identifier (e.g. "claude-sonnet-4-20250514").
        usage: Dict with "input_tokens" and "output_tokens" keys.

    Returns:
        Cost in USD. Returns 0.0 for unknown models.
    """
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    # Match longest prefix first (gpt-4o-mini before gpt-4o)
    for prefix in sorted(PRICING, key=len, reverse=True):
        if model.startswith(prefix):
            input_price, output_price = PRICING[prefix]
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return 0.0


def register_model_cost(
    model_prefix: str,
    input_cost_per_mtok: float,
    output_cost_per_mtok: float,
) -> None:
    """Register or override pricing for a model prefix.

    Args:
        model_prefix: Model name prefix to match (e.g. "my-model").
        input_cost_per_mtok: Cost per million input tokens in USD.
        output_cost_per_mtok: Cost per million output tokens in USD.
    """
    with _pricing_lock:
        PRICING[model_prefix] = (input_cost_per_mtok, output_cost_per_mtok)
