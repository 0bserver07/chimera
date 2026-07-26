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
    # GLM. ``glm-5.2``/``glm-5.1`` and ``glm-4.6`` are placeholders pending
    # Zhipu's public rate sheet (longer prefixes win in ``calculate_cost``
    # so a future split is straightforward). 5.2/5.1 mirror glm-5; 4.6 sits
    # at glm-4 tier. NOTE: when served via the Ollama-Cloud bridge
    # (``glm-5.2:cloud``) true billing is Ollama's, not z.ai's — these rates
    # are an approximation. TODO: confirm at
    # https://docs.z.ai/api-reference/llm/chat-completion.
    "glm-5.2": (2.0, 8.0),
    "glm-5.1": (2.0, 8.0),
    "glm-5": (2.0, 8.0),
    "glm-4.6": (0.6, 2.2),
    "glm-4-plus": (1.0, 4.0),
    "glm-4-flash": (0.04, 0.04),
    # DeepSeek — first-party rates verified 2026-07-25 against
    # https://api-docs.deepseek.com/quick_start/pricing, corroborated by the
    # generated catalog (first-party ``deepseek-chat``/``deepseek-reasoner``
    # rows and the ``deepseek-ai/deepseek-v4-*`` mirrors, which carry the
    # identical figures). These are cache-MISS input rates: DeepSeek publishes
    # a ~50x cheaper cache-hit input rate that this two-number table cannot
    # express, so cache-heavy workloads are over-billed here, never under.
    #
    # ``deepseek-chat`` and ``deepseek-reasoner`` are deprecated aliases for the
    # non-thinking and thinking modes of ``deepseek-v4-flash`` (deprecation
    # 2026-07-24 15:59 UTC), so all three share one rate.
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.14, 0.28),
    "deepseek-v4-flash": (0.14, 0.28),
    # V3-era SKUs, deliberately NOT re-based onto the V4 rates above:
    # $0.27 / $1.10 was V3.1's own published rate and these ids bill as their
    # own generation. Longer prefixes ensure they match before
    # ``deepseek-chat`` / ``deepseek-reasoner``. No upstream row exists for
    # either, so they stay in PRICING_OVERRIDES.
    "deepseek-v3.1-terminus": (0.27, 1.10),
    "deepseek-coder-v3": (0.27, 1.10),
    # V4 Pro. ``:cloud`` is served through the Ollama-Cloud bridge, where true
    # billing is Ollama's and not DeepSeek's — approximated at the first-party
    # rate, which is the (now accurate) reason it stays in PRICING_OVERRIDES.
    "deepseek-v4-pro:cloud": (0.435, 0.87),
    "deepseek-v4-pro": (0.435, 0.87),
    # Bare ``deepseek-v4`` is not a real SKU — it is the catch-all for a future
    # V4 id that is neither ``-flash`` nor ``-pro`` (both matched above by
    # longer prefix). Deliberately pinned to the DEARER of the two known tiers
    # so an unrecognized SKU over-bills a budget rather than under-billing it.
    "deepseek-v4": (0.435, 0.87),
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

# Prefixes in :data:`PRICING` whose hand rate DELIBERATELY diverges from the
# public models.dev figure and must never be "corrected" toward upstream. Each
# is a conscious override, for one of three reasons documented inline above:
#
#   * a **placeholder** pending a vendor's public rate sheet (the GLM family,
#     the DeepSeek V3-era SKUs, the Kimi preview line) — these are additionally
#     listed in :data:`PRICING_PLACEHOLDERS` so the auditor can tell a
#     *temporary* reason from a permanent one;
#   * a **cross-endpoint billing nuance** upstream can't express (a model whose
#     true billing depends on which endpoint or local bridge served it);
#   * a **local / open-weight** family billed at ``$0`` because the serving
#     daemon surfaces no price field.
#
# This is the marker convention consumed by the dev-only reconciler
# ``scripts/audit_model_pricing.py``: it skips these prefixes so an intentional
# divergence is never reported as drift, while still flagging a hand rate that
# has silently gone stale. Membership does **not** affect runtime resolution —
# :func:`get_model_pricing` always prefers the hand table regardless; hand
# corrections always win. Removing a prefix here re-arms the audit for it.
PRICING_OVERRIDES: frozenset[str] = frozenset({
    # GLM — placeholders + z.ai-vs-Ollama-bridge billing nuance. ``glm-4.6`` is
    # deliberately absent: z.ai now publishes it first-party at exactly the hand
    # rate, so the placeholder expired and the entry is armed for audit.
    "glm-5.2", "glm-5.1", "glm-5", "glm-4-plus", "glm-4-flash",
    # DeepSeek V3-era SKUs — no upstream row publishes them, and the rates are
    # V3.1's own generation rate rather than a stand-in for a newer SKU.
    "deepseek-v3.1-terminus", "deepseek-coder-v3",
    # ``:cloud`` bills through the Ollama-Cloud bridge, not DeepSeek; bare
    # ``deepseek-v4`` is a deliberate over-billing catch-all for unknown SKUs.
    # ``deepseek-v4-pro`` and ``deepseek-v4-flash`` are deliberately NOT here —
    # both now carry verified first-party rates and must stay armed for audit.
    "deepseek-v4-pro:cloud", "deepseek-v4",
    # Kimi (Moonshot) — $0.6/$2.5 placeholder pending per-SKU rates.
    # ``kimi-k2-0905-preview`` is deliberately absent: Moonshot now publishes it
    # first-party at exactly the hand rate, so it is armed for audit.
    "kimi-k2.5",
    # Local / open-weight — billed $0 (no price field on the serving daemon).
    "qwen3-coder-30b", "qwen3-coder", "qwen3-32b",
    "gpt-oss-120b", "gpt-oss-20b", "mistral-codestral-2511", "gemma3-27b-instruct",
})

# The subset of :data:`PRICING_OVERRIDES` whose reason is **temporary** — a
# stand-in rate held only until the vendor publishes a real one. The distinction
# matters because an override silences the auditor: a *permanent* override (a
# local model billed ``$0``, a cross-endpoint billing nuance) should stay silent
# forever, but a placeholder must stop being silent the moment upstream
# publishes. ``scripts/audit_model_pricing.py`` reports any prefix listed here
# that has since gained a first-party upstream record as a **stale placeholder**
# and exits non-zero, so the marker cannot quietly become permanent.
#
# This exists because it already failed once: ``deepseek-v4-pro`` was pinned to
# a copy of the ``deepseek-reasoner`` rate ("DeepSeek had not published a V4
# rate sheet at integration time") and listed as an override. DeepSeek then
# published $0.435 / $0.87 — but the override kept the auditor quiet, so the
# table went on billing $0.55 / $2.19 (26% high on input, 152% high on output)
# through a release, and the drift was found by hand rather than by the tool
# built to find exactly this.
PRICING_PLACEHOLDERS: frozenset[str] = frozenset({
    # GLM — pending Zhipu's own rate sheet. Only resellers list the 5.x line
    # today (``alibaba-cn`` at $1.10/$3.851 for glm-5.2), which is a markup on
    # someone else's model and must never be copied in as a "correction".
    "glm-5.2", "glm-5.1", "glm-5", "glm-4-plus", "glm-4-flash",
    # DeepSeek V3-era — no first-party row publishes these ids today.
    "deepseek-v3.1-terminus", "deepseek-coder-v3",
    # Kimi (Moonshot) — k2.5 is reseller-listed only, still pending Moonshot's
    # own per-SKU rate.
    "kimi-k2.5",
})

_pricing_lock = threading.Lock()

# --- Generated-catalog fallback -------------------------------------------
# The hand-maintained PRICING table above is the source of truth for the
# models Chimera actively bills — its z.ai-vs-ollama billing nuances can't be
# auto-derived and must win. For every other model we fall back to the
# generated models.dev catalog (``chimera/providers/model_catalog.py``,
# refreshed by ``scripts/generate_model_catalog.py``). The catalog is loaded
# lazily and cached, so the common path — a hand-dict hit — never pays its
# import cost.
_catalog_lock = threading.Lock()
_catalog: dict[str, dict[str, float | int | str | None]] | None = None
_catalog_keys: list[str] | None = None


def _ensure_catalog() -> tuple[dict[str, dict[str, float | int | str | None]], list[str]]:
    """Load and cache the generated catalog (keys sorted longest-first)."""
    global _catalog, _catalog_keys
    if _catalog is None or _catalog_keys is None:
        with _catalog_lock:
            if _catalog is None or _catalog_keys is None:
                from chimera.providers.model_catalog import MODEL_CATALOG

                _catalog = MODEL_CATALOG
                _catalog_keys = sorted(MODEL_CATALOG, key=len, reverse=True)
    return _catalog, _catalog_keys


def _catalog_pricing(model: str) -> tuple[float, float] | None:
    """Resolve *model* against the generated catalog via longest-prefix match.

    Mirrors :func:`calculate_cost`'s prefix semantics so a dated/suffixed id
    (e.g. ``gpt-4-turbo-2024-04-09``) resolves through its base entry
    (``gpt-4-turbo``). A missing/omitted output rate is treated as ``0.0``
    (e.g. embedding models that bill input only).

    Returns:
        The ``(input, output)`` dollars-per-million pair, or ``None`` when no
        catalog key prefixes *model*.
    """
    catalog, keys = _ensure_catalog()
    for key in keys:
        if model.startswith(key):
            record = catalog[key]
            input_price = record.get("input")
            if isinstance(input_price, (int, float)) and not isinstance(input_price, bool):
                output_price = record.get("output")
                out = (
                    float(output_price)
                    if isinstance(output_price, (int, float))
                    and not isinstance(output_price, bool)
                    else 0.0
                )
                return float(input_price), out
    return None


def get_model_pricing(model: str) -> tuple[float, float] | None:
    """Resolve ``(input_per_mtok, output_per_mtok)`` for *model*, or ``None``.

    The hand-maintained :data:`PRICING` table is consulted first (longest
    prefix wins), so explicit overrides — including the z.ai-vs-ollama billing
    nuances documented there — always take precedence. Only when no hand entry
    matches does the generated models.dev catalog provide a fallback rate.

    Args:
        model: Model identifier, e.g. ``"claude-sonnet-4-5-20250929"``.

    Returns:
        The ``(input, output)`` dollars-per-million-tokens pair, or ``None``
        when neither the hand table nor the generated catalog knows *model*.
    """
    # Match longest prefix first (gpt-4o-mini before gpt-4o).
    for prefix in sorted(PRICING, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICING[prefix]
    return _catalog_pricing(model)


def calculate_cost(model: str, usage: dict[str, int]) -> float:
    """Calculate the dollar cost of an API call.

    Args:
        model: Model identifier (e.g. "claude-sonnet-4-20250514").
        usage: Dict with "input_tokens" and "output_tokens" keys.

    Returns:
        Cost in USD. Pricing resolves through :func:`get_model_pricing` (hand
        table first, then the generated models.dev catalog). Returns 0.0 for
        models neither source knows.
    """
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    pricing = get_model_pricing(model)
    if pricing is None:
        return 0.0
    input_price, output_price = pricing
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


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
