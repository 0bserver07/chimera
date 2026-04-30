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

from chimera.shrew.extensions.moe_offload import (
    MOE_MODEL_CATALOG,
    MoEModelProfile,
    compute_optimal_context_window,
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

__all__ = [
    "MOE_MODEL_CATALOG",
    "MoEModelProfile",
    "SMALL_MODEL_THRESHOLD_B",
    "TINY_MODEL_THRESHOLD_B",
    "TOOLS_TO_DROP_FOR_TINY",
    "compute_optimal_context_window",
    "filter_tools_for_model",
    "model_size_billions",
    "wrap_for_small_model",
]
