# chimera/providers/cost.py
"""Token cost calculation for LLM providers."""
from __future__ import annotations

import threading

# Pricing: model_prefix -> (input_cost_per_million, output_cost_per_million)
#
# Prefix-match semantics: ``calculate_cost`` matches the *longest* prefix
# first, so ``claude-opus-4-7`` overrides the generic ``claude-opus-4``
# entry while still falling back to it for ``claude-opus-4-1``.
#
# Only entries with publicly-published pricing are listed. New IDs whose
# pricing is unverified are intentionally omitted rather than fabricated.
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic — Opus family ($5 / $25 from 4.5 onward; $15 / $75 for 4.0 / 4.1).
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    # Anthropic — Sonnet 4 family ($3 / $15).
    "claude-sonnet-4-7": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    # Anthropic — Haiku 4.5 ($1 / $5, published Oct 2025).
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-3.5": (0.80, 4.0),
    # OpenAI — GPT-5 family (published Aug 2025).
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5": (1.25, 10.0),
    # OpenAI — GPT-4o family.
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    # OpenAI — reasoning models.
    "o1-mini": (1.10, 4.40),
    "o1": (15.0, 60.0),
    "o3-mini": (1.10, 4.40),
    "o3": (2.0, 8.0),
    # Google — Gemini 2.5 family (published 2025; uses ≤200K-token tier).
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    # Google — Gemini 2.0 / 1.5.
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # GLM. ``glm-5.1`` and ``glm-4.6`` are placeholders pending Zhipu's
    # public rate sheet (longer prefixes win in ``calculate_cost`` so a
    # future split is straightforward). 5.1 mirrors glm-5; 4.6 sits at
    # glm-4 tier. TODO: confirm at
    # https://docs.z.ai/api-reference/llm/chat-completion.
    "glm-5.1": (2.0, 8.0),
    "glm-5": (2.0, 8.0),
    "glm-4.6": (0.6, 2.2),
    "glm-4-plus": (1.0, 4.0),
    "glm-4-flash": (0.04, 0.04),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # DeepSeek-V3.1 terminus + V3 coder. Both billed at deepseek-chat
    # placeholder rates ($0.27 / $1.10) until V3.1/coder per-SKU rates
    # are published. Longer prefixes ensure these match before
    # ``deepseek-chat`` / ``deepseek-reasoner``. Source: DeepSeek pricing
    # page — refresh on next release.
    "deepseek-v3.1-terminus": (0.27, 1.10),
    "deepseek-coder-v3": (0.27, 1.10),
    # DeepSeek-V4 family — pricing TODO. DeepSeek had not published a V4
    # rate sheet at integration time, so we copy the deepseek-reasoner
    # numbers ($0.55 / $2.19 per Mtok) as a placeholder. Longer prefixes
    # (``deepseek-v4-pro``, ``deepseek-v4-pro:cloud``) are matched first by
    # ``calculate_cost`` so a future split is straightforward. Source:
    # DeepSeek API pricing page (https://api-docs.deepseek.com/quick_start/pricing)
    # — refresh once V4 SKUs ship.
    "deepseek-v4-pro:cloud": (0.55, 2.19),
    "deepseek-v4-pro": (0.55, 2.19),
    "deepseek-v4": (0.55, 2.19),
    # xAI / Grok — public pricing (verify against console.x.ai before billing).
    # Longer prefixes (grok-3-mini) are matched first by ``calculate_cost``.
    "grok-3-mini": (0.30, 0.50),
    "grok-3": (3.0, 15.0),
    "grok-4": (5.0, 25.0),
    # Kimi (Moonshot). Bare ids land on Moonshot's Anthropic-compat
    # endpoint (api.moonshot.ai/anthropic). $0.6 / $2.5 placeholder
    # until Moonshot publishes per-SKU rates for the 0905 preview and
    # k2.5 line. ``:cloud`` Kimi tags are served via the Ollama daemon
    # (free locally) and intentionally absent from this table.
    "kimi-k2-0905-preview": (0.6, 2.5),
    "kimi-k2.5": (0.6, 2.5),
    # Local-only / open-weight families. Listed at $0/$0 because Ollama's
    # ``/api/chat`` does not surface a price field — the cost is bandwidth
    # and electricity, not API metering. These entries exist so
    # ``calculate_cost`` returns a deterministic 0.0 instead of falling
    # through the prefix table.
    "qwen3-coder-30b": (0.0, 0.0),
    "qwen3-coder": (0.0, 0.0),
    "qwen3-32b": (0.0, 0.0),
    "gpt-oss-120b": (0.0, 0.0),
    "gpt-oss-20b": (0.0, 0.0),
    "mistral-codestral-2511": (0.0, 0.0),
    "gemma3-27b-instruct": (0.0, 0.0),
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


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int = 0,
) -> float:
    """Pre-flight cost estimation.

    Args:
        model: Model identifier.
        input_tokens: Expected input token count.
        output_tokens: Expected output token count.

    Returns:
        Estimated cost in USD. Returns 0.0 for unknown models.
    """
    return calculate_cost(model, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    })
