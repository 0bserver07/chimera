"""Pre-flight cost estimation for chimera CLIs.

Wave-11 task A8 — exposes a small helper used by the otter CLI (and, in a
follow-up wave, the other six CLIs) to answer two questions:

1. *How much will this turn cost me?* — printed by ``--estimate-cost``.
2. *Is this turn within my budget?* — gated by ``--max-cost FLOAT``.

The estimator is deliberately rough: token counts are estimated via the
chars-÷-4 rule of thumb (good to ~10-20% on English prose; less accurate
on dense code or non-Latin scripts). The dollar number is a useful
ceiling, not a bill. Pricing is sourced from
:data:`chimera.providers.cost.PRICING`, which uses *longest-prefix-match*
semantics so ``glm-5-air`` resolves through ``glm-5``.

Conventions chosen for this module (see report A8-W11-COST-ESTIMATE.md):

* Unknown models **raise** :class:`ModelNotPriced`. The CLI catches and
  prints a friendly message; programmatic callers can detect via
  ``isinstance``. Returning a zero-cost estimate would silently mask
  pricing-table gaps and let ``--max-cost`` pass when it shouldn't.
* The rule-of-thumb token estimate uses ``max(1, len(prompt) // 4)`` so
  a single-char prompt still counts as one token (avoids divide-by-zero
  surprises in downstream code).
* Cache-read / cache-write rates are not modelled here — the underlying
  PRICING table only stores ``(input_per_mtok, output_per_mtok)`` pairs
  today. Cache-aware estimation belongs in a follow-up once the table
  grows. The :class:`CostEstimate` dataclass keeps the door open by
  exposing only the fields PRICING actually feeds.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from chimera.providers.cost import PRICING

__all__ = [
    "CostEstimate",
    "ModelNotPriced",
    "estimate_cost",
    "format_estimate",
]


class ModelNotPriced(KeyError):
    """Raised when *model* has no entry in :data:`PRICING`.

    Subclasses :class:`KeyError` so callers using bare ``except KeyError``
    keep working, while ``except ModelNotPriced`` lets new code be
    explicit.
    """


@dataclass(frozen=True)
class CostEstimate:
    """Result of a pre-flight cost estimation.

    Attributes:
        model: Model identifier (the user's spelling, not the matched
            PRICING prefix).
        input_tokens: Estimated input-token count (chars-÷-4 of the
            prompt).
        output_tokens: Caller-supplied expected output token count.
        input_cost_usd: ``input_tokens × input_rate / 1e6``.
        output_cost_usd: ``output_tokens × output_rate / 1e6``.
        total_usd: ``input_cost_usd + output_cost_usd``.
    """

    model: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_usd: float


def _resolve_pricing(model: str) -> tuple[float, float]:
    """Return ``(input_per_mtok, output_per_mtok)`` for *model*.

    Uses the same longest-prefix-match semantics as
    :func:`chimera.providers.cost.calculate_cost` so the estimator and
    the post-hoc cost calculator agree.

    Raises:
        ModelNotPriced: When no prefix in :data:`PRICING` matches *model*.
    """
    for prefix in sorted(PRICING, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICING[prefix]
    raise ModelNotPriced(model)


def estimate_cost(
    model: str, prompt: str, expected_output_tokens: int = 2048,
) -> CostEstimate:
    """Estimate the dollar cost of a one-turn agent call.

    Args:
        model: Model identifier, e.g. ``"glm-5"`` or
            ``"claude-sonnet-4-6"``. Looked up in :data:`PRICING` via
            longest-prefix match.
        prompt: The user's prompt (or composed prompt + attachments).
            Token count is estimated as ``max(1, len(prompt) // 4)``.
        expected_output_tokens: Caller-supplied expected output token
            count. Defaults to ``2048``, a typical budget for a single
            agent reply with embedded tool calls. Tune higher for
            long-form generation, lower for terse Q&A.

    Returns:
        A :class:`CostEstimate` with both per-bucket and total USD.

    Raises:
        ModelNotPriced: When *model* has no PRICING entry. Callers should
            catch this and print a friendly message — silently zeroing
            would let ``--max-cost`` pass when the model is uncosted.
    """
    input_rate, output_rate = _resolve_pricing(model)
    input_tokens = max(1, len(prompt) // 4)
    out_tokens = max(0, int(expected_output_tokens))
    input_cost = input_tokens * input_rate / 1_000_000
    output_cost = out_tokens * output_rate / 1_000_000
    return CostEstimate(
        model=model,
        input_tokens=input_tokens,
        output_tokens=out_tokens,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        total_usd=input_cost + output_cost,
    )


def format_estimate(est: CostEstimate, output: str = "text") -> str:
    """Render *est* for human or machine consumption.

    Args:
        est: A :class:`CostEstimate` from :func:`estimate_cost`.
        output: ``"text"`` (single-line human-readable, default) or
            ``"json"`` (single-line JSON object with the dataclass
            fields).

    Returns:
        The rendered string. The text form looks like::

            Estimated cost: $0.0042 (123 input tokens, 2048 expected
            output tokens, glm-5)

        The json form is a compact ``json.dumps(asdict(est))`` so it
        round-trips through ``json.loads``.

    Raises:
        ValueError: When *output* isn't ``"text"`` or ``"json"``.
    """
    if output == "json":
        return json.dumps(asdict(est))
    if output == "text":
        return (
            f"Estimated cost: ${est.total_usd:.4f} "
            f"({est.input_tokens} input tokens, "
            f"{est.output_tokens} expected output tokens, {est.model})"
        )
    raise ValueError(
        f"unknown output format {output!r}; expected 'text' or 'json'"
    )
