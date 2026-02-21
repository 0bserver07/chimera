from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OverfitSignal:
    """Indicator of potential overfitting or benchmark gaming."""

    detected: bool
    reason: str
    confidence: float  # 0.0 to 1.0


def check_output_similarity(
    outputs: list[str], threshold: float = 0.9
) -> OverfitSignal:
    """Check if agent outputs are too similar across different tasks (copy-paste).

    A high ratio of duplicate outputs suggests the agent is producing
    templated responses rather than task-specific solutions.

    Args:
        outputs: List of agent outputs across tasks.
        threshold: Similarity ratio above which overfit is flagged.

    Returns:
        OverfitSignal indicating whether outputs are suspiciously similar.
    """
    if len(outputs) < 2:
        return OverfitSignal(detected=False, reason="", confidence=0.0)
    # Simple check: ratio of identical outputs
    unique = len(set(outputs))
    similarity = 1.0 - (unique / len(outputs))
    if similarity >= threshold:
        return OverfitSignal(
            detected=True,
            reason=f"Output similarity {similarity:.0%} exceeds threshold",
            confidence=similarity,
        )
    return OverfitSignal(detected=False, reason="", confidence=similarity)


def check_hardcoded_answers(
    output: str, test_values: list[str]
) -> OverfitSignal:
    """Check if agent output contains hardcoded test values instead of general logic.

    If the agent output contains most of the known test values verbatim,
    it may be hardcoding expected answers rather than generating a general solution.

    Args:
        output: The agent's generated output/code.
        test_values: Known test case values that a hardcoded solution would embed.

    Returns:
        OverfitSignal indicating whether hardcoding is suspected.
    """
    if not test_values:
        return OverfitSignal(detected=False, reason="", confidence=0.0)
    matches = sum(1 for v in test_values if v in output)
    ratio = matches / len(test_values)
    if ratio > 0.8:
        return OverfitSignal(
            detected=True,
            reason=f"Output contains {matches}/{len(test_values)} test values",
            confidence=ratio,
        )
    return OverfitSignal(detected=False, reason="", confidence=ratio)
