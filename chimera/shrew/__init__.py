"""Shrew — Chimera coding-agent CLI tuned for small local models.

Shrew is the fifth Chimera coding-agent CLI, paralleling :mod:`chimera.mink`,
:mod:`chimera.otter`, :mod:`chimera.ferret`, and :mod:`chimera.weasel`. Where
the others are model-agnostic, shrew is **explicitly tuned for small local
models** (Qwen3.5-9B, Qwen3.6-35B-A3B MoE, llama.cpp / Ollama backends) and
ships a curated set of extensions + skill markdown files + a benchmark harness
that target the scaffold-model fit problem head-on.

Built on top of weasel (Chimera's minimal-harness CLI) so improvements to the
substrate flow through automatically.

Distinguishing surfaces:

* **Small-local-model defaults** — ``llama.cpp`` and ``Ollama`` first; cloud
  models work but aren't the default.
* **Curated skill markdown set** — knowledge / protocols / tools, mounted
  into the agent's context for scaffold-model-fit gains.
* **Benchmark harness** — Aider Polyglot, GAIA, terminal-bench, harbor;
  optimised for the kind of comparisons small-model research wants.
* **MoE-aware ergonomics** — context-window sizing, expert offload hints,
  attention-on-GPU / experts-in-RAM helpers.

See ``research/shrew/`` for per-agent reports and the design spec.
"""

from __future__ import annotations

# WHY: re-exports kept lightweight so ``import chimera.shrew`` stays cheap
# (no provider / agent imports). Submodules import lazily inside their own
# call sites — see :mod:`chimera.shrew.cli` for the late-binding pattern.

__all__: list[str] = [
    "cli",
    "repl",
    "sessions",
]
