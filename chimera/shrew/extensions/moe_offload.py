"""MoE-aware context-window sizing.

The MoE-offload trick documented in the upstream small-model coding
agent project: for Mixture-of-Experts models (e.g. Qwen3.6-35B-A3B),
keep the **experts in system RAM** and only the **attention layers
plus KV cache on GPU**. A 22 GB quantised MoE thereby runs on an
8 GB consumer laptop GPU.

The relevant llama.cpp incantation is::

    llama-server -m model.gguf \\
       --host 127.0.0.1 --port 8888 --jinja \\
       -c 16384 -ngl 99 --n-cpu-moe 999 --flash-attn on

Once the model weights themselves are out of the GPU VRAM budget,
the *only* GPU consumer that scales with usage is the **KV cache**.
That makes the practical context-window choice a function of:

  * the model's KV-bytes-per-token cost (architecture-dependent, but
    well-approximated as ``2 * n_kv_heads * head_dim * n_layers *
    bytes_per_element``);
  * the available VRAM after attention weights are loaded;
  * a safety reserve for activation buffers and CUDA workspace.

This module ships:

* :class:`MoEModelProfile` — per-model record containing the bits
  needed to compute a context window.
* :data:`MOE_MODEL_CATALOG` — default catalogue keyed by canonical
  shrew/llama.cpp model id.
* :func:`compute_optimal_context_window` — public function used by
  the shrew CLI to pin a context-window flag at launch time.

Stdlib-only. Returns are deterministic (no probing of the running
GPU) so callers can pre-compute settings during arg-parsing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "MOE_MODEL_CATALOG",
    "MoEModelProfile",
    "compute_optimal_context_window",
]


# ---------------------------------------------------------------------------
# Per-model profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoEModelProfile:
    """Static facts about an MoE-aware model used for context sizing.

    Attributes:
        model_id: Canonical shrew/llama.cpp model id (e.g.
            ``"qwen3.6-35b-a3b"``). Comparison is case-insensitive
            via the loader, but stored verbatim.
        params_b: Total parameter count, billions (the ``35`` in
            "35B-A3B"). Used as a tag for size-based heuristics.
        active_b: Active parameter count per token, billions (the
            ``A3B``). MoE inference cost scales with this.
        is_moe: True when the architecture is mixture-of-experts.
            False for dense models that nonetheless ship as
            "small-fit" defaults.
        kv_bytes_per_token: Estimated KV-cache bytes per token at
            FP16. Computed offline from architecture metadata; we
            keep it as a stored constant so the helper is pure.
        attention_vram_gb: Estimated VRAM cost of the attention
            blocks plus a small constant overhead, in gigabytes.
            Subtracted from the user's ``vram_gb`` budget before we
            distribute the remainder to the KV cache.
        max_context: Architectural cap on context window (e.g.
            32_768 for Qwen3 family). The optimiser never returns
            more than this regardless of available VRAM.
        notes: Free-form provenance string for documentation.
    """

    model_id: str
    params_b: float
    active_b: float
    is_moe: bool
    kv_bytes_per_token: int
    attention_vram_gb: float
    max_context: int
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Numbers chosen to match the upstream small-coder catalogue for the
# laptop-VRAM target (RTX 5070 Laptop, 8 GB) — see the project README
# in research/shrew/SPEC.md and the upstream architecture doc. Where
# we estimate, we round conservatively (favouring smaller windows
# over OOMs).
_QWEN_KV_PER_TOKEN: Final[int] = 96 * 1024  # ~96 KB / token at FP16
_QWEN_KV_9B_PER_TOKEN: Final[int] = 64 * 1024  # 9B is denser/cheaper

MOE_MODEL_CATALOG: Final[dict[str, MoEModelProfile]] = {
    "qwen3.6-35b-a3b": MoEModelProfile(
        model_id="qwen3.6-35b-a3b",
        params_b=35.0,
        active_b=3.0,
        is_moe=True,
        kv_bytes_per_token=_QWEN_KV_PER_TOKEN,
        attention_vram_gb=2.5,
        max_context=32_768,
        notes="Default shrew model; experts in RAM, attention on GPU.",
        aliases=("qwen3.6-35b", "qwen-35b-a3b"),
    ),
    "qwen3.5-9b": MoEModelProfile(
        model_id="qwen3.5-9b",
        params_b=9.7,
        active_b=9.7,
        is_moe=False,
        kv_bytes_per_token=_QWEN_KV_9B_PER_TOKEN,
        attention_vram_gb=5.3,
        max_context=32_768,
        notes="Dense 9.7B; the paper's model. 5.3 GB on GPU at Q4_K_M.",
        aliases=("qwen3.5", "qwen-9b"),
    ),
    "qwen3.5": MoEModelProfile(
        model_id="qwen3.5",
        params_b=9.7,
        active_b=9.7,
        is_moe=False,
        kv_bytes_per_token=_QWEN_KV_9B_PER_TOKEN,
        attention_vram_gb=5.3,
        max_context=32_768,
        notes="Ollama alias for qwen3.5-9b.",
    ),
    "deepseek-coder-v2-lite-16b": MoEModelProfile(
        model_id="deepseek-coder-v2-lite-16b",
        params_b=16.0,
        active_b=2.4,
        is_moe=True,
        kv_bytes_per_token=_QWEN_KV_PER_TOKEN,
        attention_vram_gb=2.0,
        max_context=16_384,
        notes="MoE coder (16B, A2.4B). Conservative defaults.",
        aliases=("deepseek-coder-lite", "deepseek-v2-lite"),
    ),
}
"""Default catalogue of MoE-aware (and small-dense) models."""

#: Minimum context window we will ever return — guarantees the
#: shrew CLI can run *something* even on tiny VRAM machines.
_MIN_CONTEXT: Final[int] = 4_096

#: VRAM reserve carved out for CUDA workspace, activations, and
#: anything else that grows with batch size. Keeps us off the OOM
#: cliff at small VRAM budgets.
_VRAM_RESERVE_GB: Final[float] = 0.8

#: Fraction of remaining VRAM (after attention + reserve) we are
#: willing to dedicate to the KV cache. The remainder is left as
#: headroom for fragmentation and unexpected bumps.
_KV_BUDGET_FRACTION: Final[float] = 0.85


def _resolve_profile(model_id: str) -> MoEModelProfile | None:
    """Return the catalogue entry for ``model_id`` or ``None``.

    Lookup is case-insensitive and tolerant of the
    ``"<provider>/<id>"`` shape the shrew CLI uses (e.g.
    ``"llamacpp/qwen3.6-35b-a3b"``).
    """
    if not model_id:
        return None
    lookup = model_id.strip().lower()
    if "/" in lookup:
        lookup = lookup.split("/", 1)[1]
    direct = MOE_MODEL_CATALOG.get(lookup)
    if direct is not None:
        return direct
    for profile in MOE_MODEL_CATALOG.values():
        if lookup in {alias.lower() for alias in profile.aliases}:
            return profile
    return None


def _round_to_power_of_two(n: int) -> int:
    """Snap to the largest power-of-two ``<= n`` (clamped at 4096).

    llama.cpp performs better with power-of-two context sizes; this
    matches the upstream small-coder convention.
    """
    if n <= _MIN_CONTEXT:
        return _MIN_CONTEXT
    out = _MIN_CONTEXT
    while out * 2 <= n:
        out *= 2
    return out


def compute_optimal_context_window(model_id: str, vram_gb: int) -> int:
    """Compute a safe ``-c`` value for ``model_id`` on ``vram_gb`` of GPU.

    For MoE-aware models in :data:`MOE_MODEL_CATALOG`, the result
    accounts for experts living in system RAM (only attention +
    KV-cache consume VRAM). For models we don't know about, we
    return a conservative default so callers never blow up.

    Args:
        model_id: A model identifier — bare or
            ``"<provider>/<id>"``. Case-insensitive.
        vram_gb: Total GPU VRAM in gigabytes (whole numbers; the
            laptop-class targets are 6/8/12/16/24).

    Returns:
        A context window (in tokens) snapped to a power of two,
        bounded by the model's architectural maximum and a 4096
        floor. Never returns 0.
    """
    if vram_gb <= 0:
        return _MIN_CONTEXT

    profile = _resolve_profile(model_id)
    if profile is None:
        # Unknown model: be conservative. On 8 GB VRAM, return 8K;
        # scale linearly thereafter, capped at 32K.
        scaled = max(_MIN_CONTEXT, int(vram_gb * 1024))
        return min(_round_to_power_of_two(scaled), 32_768)

    free_gb = float(vram_gb) - profile.attention_vram_gb - _VRAM_RESERVE_GB
    if free_gb <= 0.0:
        return _MIN_CONTEXT

    kv_budget_bytes = int(free_gb * (1024**3) * _KV_BUDGET_FRACTION)
    tokens_for_kv = kv_budget_bytes // max(profile.kv_bytes_per_token, 1)
    snapped = _round_to_power_of_two(int(tokens_for_kv))
    return max(_MIN_CONTEXT, min(snapped, profile.max_context))
