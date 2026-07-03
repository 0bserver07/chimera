"""Agent runners — the many-to-many arm of the eval layer.

One :class:`~chimera.eval.runners.base.AgentRunner` contract that any agent —
Chimera-internal or external — satisfies, so a benchmark cell can measure it.
See ``docs/specs/agent-benchmark-matrix.md`` and
``docs/reference/capability-matrix.md``.
"""

from __future__ import annotations

from chimera.eval.runners.base import AgentRunner, AgentRunResult
from chimera.eval.runners.in_process import InProcessRunner

__all__ = ["AgentRunner", "AgentRunResult", "InProcessRunner"]
