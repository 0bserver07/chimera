"""Scaffold-model-fit prompt builders for small local models.

The upstream small-coder result (a 9.7B Qwen jumping from 19% to
45% on Aider Polyglot when wrapped in the right scaffolding) tells
us small models *do* know how to code — they just need a much more
explicit reasoning frame than frontier models.

This module exposes one function: :func:`wrap_for_small_model`.
Callers (notably :mod:`chimera.shrew.cli`) feed it the in-flight
system prompt and the active model size, and we return the same
prompt augmented with:

* a "think first, then act" preamble;
* an explicit step-by-step reasoning checklist;
* an output-shape reminder (one tool call per turn, no narration
  about tools you're about to call).

For models above :data:`SMALL_MODEL_THRESHOLD_B` we return the
prompt unmodified — the scaffold is empirically a no-op or even a
mild regression on frontier models, so we let them be.

Stdlib-only. Pure function. Output is deterministic.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "SMALL_MODEL_THRESHOLD_B",
    "wrap_for_small_model",
]


#: Models with **fewer** active parameters than this (in billions)
#: receive the small-model scaffold. Sub-13B is the "small" band
#: per the upstream paper and our own measurements; 13B+ models
#: pass through unchanged so the same shrew install can host both
#: without surprises.
SMALL_MODEL_THRESHOLD_B: Final[float] = 13.0


# ---------------------------------------------------------------------------
# Scaffold pieces
# ---------------------------------------------------------------------------

_SMALL_MODEL_PREAMBLE = (
    "<small-model-scaffold>\n"
    "You are running on a small local model. Before you act, think "
    "step-by-step in plain text. After thinking, emit exactly one "
    "tool call (or a final answer). Never narrate which tool you are "
    "about to use — just call it.\n"
    "</small-model-scaffold>\n"
)

_SMALL_MODEL_CHECKLIST = (
    "\n\n"
    "<reasoning-checklist>\n"
    "For every turn, walk through these steps in order:\n"
    "  1. Restate the user's most recent goal in one sentence.\n"
    "  2. List the concrete sub-tasks needed to satisfy that goal.\n"
    "  3. Identify the next single sub-task to attempt right now.\n"
    "  4. Decide on the tool best suited to that sub-task (or "
    "decide that the answer is ready and no tool is needed).\n"
    "  5. Emit exactly one tool call OR a final answer.\n"
    "If a previous tool call failed, address the failure before "
    "advancing the plan.\n"
    "</reasoning-checklist>\n"
)

_SMALL_MODEL_OUTPUT_RULES = (
    "\n"
    "<output-rules>\n"
    "  - One tool call per turn, no exceptions.\n"
    "  - Do not announce or explain the tool call you are about to "
    "make; just emit it.\n"
    "  - When editing files, prefer the edit/replace tools over "
    "rewriting whole files unless the file does not yet exist.\n"
    "  - Stop and ask the user only when truly blocked; never to "
    "confirm steps you can verify yourself.\n"
    "</output-rules>\n"
)


def _is_small(model_size_b: float) -> bool:
    """Return ``True`` when ``model_size_b`` is below the threshold.

    Defensive: negative or zero sizes are treated as "small" — the
    scaffold is harmless on tiny models, and an explicit zero almost
    certainly means "size unknown, assume worst case".
    """
    return model_size_b < SMALL_MODEL_THRESHOLD_B


def wrap_for_small_model(prompt: str, model_size_b: float) -> str:
    """Augment ``prompt`` with a small-model reasoning scaffold.

    For sub-:data:`SMALL_MODEL_THRESHOLD_B` models, the scaffold is
    prepended (preamble) and appended (checklist + output rules)
    around the caller's existing system prompt. The original prompt
    text is preserved verbatim in the middle so chimera presets
    (Build / Plan / Explore / General / Review) keep their
    personality.

    For larger models, the prompt is returned unchanged.

    Args:
        prompt: The current system-prompt body.
        model_size_b: Model size in billions of (active) parameters.
            For dense models, this is total params; for MoE, prefer
            the active-experts count (so qwen3.6-35b-a3b is ``3.0``).

    Returns:
        Either ``prompt`` unchanged (for large models) or a wrapped
        version with the small-model scaffold layered around it.
        Always returns a non-empty string when ``prompt`` is
        non-empty; idempotent if applied to already-wrapped output.
    """
    if not _is_small(model_size_b):
        return prompt

    # Idempotence: if a caller has already wrapped the prompt, the
    # scaffold tag will be present and we return as-is. This makes
    # it safe to apply the wrapper at multiple stages of the
    # pipeline (CLI args + per-turn injection) without duplication.
    if "<small-model-scaffold>" in prompt:
        return prompt

    body = prompt if prompt is not None else ""
    return (
        _SMALL_MODEL_PREAMBLE
        + body
        + _SMALL_MODEL_CHECKLIST
        + _SMALL_MODEL_OUTPUT_RULES
    )
