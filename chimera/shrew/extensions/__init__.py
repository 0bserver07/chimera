"""Shrew small-model-fit extensions.

This subpackage ships three small, opinionated helpers that adapt the
weasel/Chimera substrate to **small local models** (sub-13B parameter
counts, MoE architectures with limited VRAM, llama.cpp / Ollama
backends). Each module is self-contained, stdlib-only, and consumed
by :mod:`chimera.shrew.cli` via late-binding imports so it can land
out-of-order with the wave-5 CLI scaffold (S1).

Modules:

* :mod:`.moe_offload` — context-window sizing for MoE models that
  keep experts in system RAM and only attention/KV-cache on GPU.
  Encodes the small-VRAM-on-consumer-laptop pattern (8 GB VRAM
  hosts 22 GB MoE weights via offload).
* :mod:`.scaffold_fit` — system-prompt wrappers that add explicit
  step-by-step reasoning scaffolds for sub-13B models. Larger
  models pass through unchanged.
* :mod:`.tool_filter` — tool-list filter that removes tools
  empirically known to confuse very small models (web_fetch,
  browser, complex MCP tools) when the model is below 9B.

Public surface (what S1's ``cli.py`` imports late):

* :func:`compute_optimal_context_window`
* :func:`MOE_MODEL_CATALOG`
* :func:`wrap_for_small_model`
* :func:`filter_tools_for_model`
"""
from __future__ import annotations

from chimera.shrew.extensions.checkpoint import (
    CheckpointInfo,
    checkpoint_root,
    list_checkpoints,
    restore_file,
    snapshot_file,
)
from chimera.shrew.extensions.error_simplifier import (
    is_known_error,
    simplify_error,
)
from chimera.shrew.extensions.permission_gate import (
    DANGEROUS_PATTERNS,
    READ_ONLY_PREFIXES,
    Decision,
    GateMode,
    RiskLevel,
    classify_command,
    evaluate_command,
    resolve_mode,
)
from chimera.shrew.extensions.file_chunker import (
    Chunk,
    chunk_text,
    format_chunk_header,
)
from chimera.shrew.extensions.hint_injector import (
    Attempt,
    build_hint,
    inject_hint,
    should_inject_hint,
)
from chimera.shrew.extensions.moe_offload import (
    MOE_MODEL_CATALOG,
    MoEModelProfile,
    compute_optimal_context_window,
)
from chimera.shrew.extensions.output_truncation import (
    truncate_output,
)
from chimera.shrew.extensions.quiet_thinking import (
    has_thinking,
    strip_thinking,
)
from chimera.shrew.extensions.repeat_detection import (
    detect_short_loop,
    should_short_circuit,
)
from chimera.shrew.extensions.scaffold_fit import (
    SMALL_MODEL_THRESHOLD_B,
    wrap_for_small_model,
)
from chimera.shrew.extensions.tool_filter import (
    TINY_MODEL_THRESHOLD_B,
    TOOLS_TO_DROP_FOR_TINY,
    filter_tools_for_model,
    model_size_billions,
)
from chimera.shrew.extensions.turn_budgeter import (
    check_budget,
    estimate_tokens,
    format_budget_warning,
)

__all__ = [
    "DANGEROUS_PATTERNS",
    "MOE_MODEL_CATALOG",
    "READ_ONLY_PREFIXES",
    "SMALL_MODEL_THRESHOLD_B",
    "TINY_MODEL_THRESHOLD_B",
    "TOOLS_TO_DROP_FOR_TINY",
    "Attempt",
    "CheckpointInfo",
    "Chunk",
    "Decision",
    "GateMode",
    "MoEModelProfile",
    "RiskLevel",
    "build_hint",
    "check_budget",
    "checkpoint_root",
    "chunk_text",
    "classify_command",
    "compute_optimal_context_window",
    "detect_short_loop",
    "estimate_tokens",
    "evaluate_command",
    "filter_tools_for_model",
    "format_budget_warning",
    "format_chunk_header",
    "has_thinking",
    "inject_hint",
    "is_known_error",
    "list_checkpoints",
    "model_size_billions",
    "resolve_mode",
    "restore_file",
    "should_inject_hint",
    "should_short_circuit",
    "simplify_error",
    "snapshot_file",
    "strip_thinking",
    "truncate_output",
    "wrap_for_small_model",
]
