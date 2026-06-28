"""Chimera's wide-research adapter — fan out one sub-agent per input.

Integrates the Datacurve wide-research job format: a TOML spec (``brief`` +
``inputs[]`` + ``prompt_template`` + ``output_schema``) parsed by
:class:`WideResearchSpec` and fanned out by :class:`WideResearchRunner` — one
subtask per input, each rendering ``{{ input }}`` into the prompt and returning
a structured row that conforms to ``output_schema``.

The per-subtask executor is injectable (testable without a live model); the
default :func:`agent_executor` runs one Chimera agent per subtask inside an
environment from the universal env factory, giving the upstream
sandbox-per-subtask shape via ``env_provider="e2b"`` / ``"modal"``.
"""

from chimera.wide_research.runner import (
    DEFAULT_MAX_WORKERS,
    Subtask,
    SubtaskExecutor,
    SubtaskResult,
    WideResearchResult,
    WideResearchRunner,
    agent_executor,
    extract_json_output,
    results_to_csv,
    results_to_jsonl,
)
from chimera.wide_research.spec import OutputField, WideResearchSpec

__all__ = [
    "DEFAULT_MAX_WORKERS",
    "OutputField",
    "Subtask",
    "SubtaskExecutor",
    "SubtaskResult",
    "WideResearchResult",
    "WideResearchRunner",
    "WideResearchSpec",
    "agent_executor",
    "extract_json_output",
    "results_to_csv",
    "results_to_jsonl",
]
