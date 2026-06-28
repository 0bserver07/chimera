"""wide-research fan-out runner — map inputs to subtasks, collect structured rows.

Mirrors the wide-research execution model: each ``inputs[i]`` becomes an
independent :class:`Subtask` whose prompt is ``spec.render(input)``, run by its
own agent (optionally in its own sandbox), returning a row that conforms to the
spec's ``output_schema``.  Concurrency is bounded by ``spec.parallelism``
(``0`` → a safe default cap).

The per-subtask execution is injected as a :data:`SubtaskExecutor` callable, so
the fan-out orchestration is fully testable without a live model or sandbox.
:func:`agent_executor` wires the default executor: one Chimera
:class:`~chimera.core.agent.Agent` per subtask, in an environment from the
universal env factory, with its final JSON object parsed via
:func:`extract_json_output`.

Example:
    ```python
    from chimera.wide_research import WideResearchSpec, WideResearchRunner

    spec = WideResearchSpec.from_toml_file("find_ceos.toml")
    runner = WideResearchRunner(spec)
    result = runner.run(lambda st: {"ceo": lookup(st.input)})
    for row in result.rows:
        print(row)
    ```
"""

from __future__ import annotations

import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.wide_research.spec import WideResearchSpec

# Chimera's safety cap when a spec asks for unbounded parallelism (0). The
# upstream tool spins one Modal sandbox per subtask; a local thread pool should
# not. Override per-run via ``WideResearchRunner(max_workers=...)``.
DEFAULT_MAX_WORKERS = 32


@dataclass(frozen=True)
class Subtask:
    """One unit of fan-out work: an input and its rendered prompt.

    Attributes:
        index: Position in ``spec.inputs`` (stable subtask id).
        input: The raw input string.
        prompt: ``spec.render(input)`` — the prompt handed to the executor.
    """

    index: int
    input: str
    prompt: str


@dataclass(frozen=True)
class SubtaskResult:
    """Outcome of one subtask.

    Attributes:
        index: Subtask id (matches :attr:`Subtask.index`).
        input: The raw input string.
        success: Whether a valid, schema-conforming output was produced.
        output: The structured output dict (empty on failure).
        error: Failure reason (empty on success).
        raw: Best-effort raw executor output, for debugging.
    """

    index: int
    input: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    raw: str = ""


@dataclass(frozen=True)
class WideResearchResult:
    """The collected results of a whole run."""

    spec_name: str
    results: tuple[SubtaskResult, ...]

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Successful subtask outputs, each tagged with ``_index``/``_input``."""
        return [
            {"_index": r.index, "_input": r.input, **r.output}
            for r in self.results
            if r.success
        ]

    @property
    def failures(self) -> list[SubtaskResult]:
        """Subtasks that errored or produced a non-conforming output."""
        return [r for r in self.results if not r.success]

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)


# An executor turns a Subtask into its structured output dict. Raising signals
# subtask failure (captured as SubtaskResult.error).
SubtaskExecutor = Callable[[Subtask], dict[str, Any]]


class WideResearchRunner:
    """Fan-out runner: one subtask per ``spec.inputs`` element.

    Attributes:
        spec: The job to run.
        max_workers: Concurrency override; defaults to ``spec.parallelism`` or
            :data:`DEFAULT_MAX_WORKERS` when the spec leaves it unbounded.
    """

    def __init__(self, spec: WideResearchSpec, max_workers: int | None = None) -> None:
        self.spec = spec
        if max_workers is not None:
            self.max_workers = max(1, max_workers)
        elif spec.parallelism > 0:
            self.max_workers = spec.parallelism
        else:
            self.max_workers = min(max(1, len(spec.inputs)), DEFAULT_MAX_WORKERS)

    def subtasks(self) -> list[Subtask]:
        """Render every input into a :class:`Subtask`, preserving order."""
        return [
            Subtask(index=i, input=value, prompt=self.spec.render(value))
            for i, value in enumerate(self.spec.inputs)
        ]

    def run(self, executor: SubtaskExecutor) -> WideResearchResult:
        """Fan *executor* out across all subtasks and collect results in order.

        Each subtask runs in a worker thread (bounded by :attr:`max_workers`).
        An executor that raises, or returns an output missing a required field,
        yields a failed :class:`SubtaskResult` rather than aborting the batch.

        Args:
            executor: Maps a :class:`Subtask` to its structured output dict.

        Returns:
            A :class:`WideResearchResult` with one entry per input, in input
            order.
        """
        subtasks = self.subtasks()
        results: list[SubtaskResult | None] = [None] * len(subtasks)

        def _one(st: Subtask) -> SubtaskResult:
            try:
                output = executor(st)
            except Exception as exc:  # executor failure → failed subtask
                return SubtaskResult(
                    index=st.index, input=st.input, success=False, error=str(exc)
                )
            missing = self._missing_required(output)
            if missing:
                return SubtaskResult(
                    index=st.index,
                    input=st.input,
                    success=False,
                    output=output,
                    error=f"missing required field(s): {', '.join(missing)}",
                    raw=json.dumps(output),
                )
            return SubtaskResult(
                index=st.index, input=st.input, success=True, output=output
            )

        if not subtasks:
            return WideResearchResult(spec_name=self.spec.name, results=())

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for st, res in zip(subtasks, pool.map(_one, subtasks)):
                results[st.index] = res

        return WideResearchResult(
            spec_name=self.spec.name,
            results=tuple(r for r in results if r is not None),
        )

    def _missing_required(self, output: dict[str, Any]) -> list[str]:
        """Required schema fields absent or ``None`` in *output*."""
        return [
            name
            for name in self.spec.required_fields
            if output.get(name) is None
        ]


# ----------------------------------------------------------------------
# Output extraction + serialization
# ----------------------------------------------------------------------

def extract_json_output(text: str, fields: list[str] | None = None) -> dict[str, Any]:
    """Best-effort: pull the structured output JSON object from agent text.

    Agents that lack a native ``submit`` tool are asked to end with a JSON
    object of the schema fields; this recovers it by scanning for every
    balanced ``{...}`` that parses as a JSON object, so fenced code blocks and
    surrounding prose are tolerated and nested objects are handled correctly.

    Args:
        text: The agent's final output.
        fields: When multiple objects are present, prefer the last one
            containing at least one of these keys.

    Returns:
        The parsed object (the last one by default), or ``{}`` if none found.
    """
    decoder = json.JSONDecoder()
    objs: list[dict[str, Any]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            objs.append(obj)
            i = end
        else:
            i += 1
    if not objs:
        return {}
    if fields:
        for obj in reversed(objs):
            if any(k in obj for k in fields):
                return obj
    return objs[-1]


def results_to_jsonl(result: WideResearchResult) -> str:
    """Serialize successful rows to JSONL (one JSON object per line)."""
    return "".join(json.dumps(row) + "\n" for row in result.rows)


def results_to_csv(result: WideResearchResult, spec: WideResearchSpec) -> str:
    """Serialize successful rows to CSV with a stable, schema-ordered header.

    Columns are ``_index``, ``_input``, then each ``output_schema`` field in
    declaration order.

    Args:
        result: The run result.
        spec: The spec the run came from (supplies column order).

    Returns:
        CSV text (RFC 4180, ``\\r\\n`` line endings from :mod:`csv`).
    """
    header = ["_index", "_input", *(f.name for f in spec.output_schema)]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore")
    writer.writeheader()
    for row in result.rows:
        writer.writerow(row)
    return buf.getvalue()


def agent_executor(
    provider: Any,
    *,
    env_provider: str = "local",
    env_opts: dict[str, Any] | None = None,
    system: str | None = None,
) -> SubtaskExecutor:
    """Build the default agent-backed :data:`SubtaskExecutor`.

    Each subtask gets a fresh :class:`~chimera.core.agent.Agent` on *provider*,
    running inside an environment from
    :func:`chimera.env.factory.create_environment` (so ``env_provider="e2b"``
    or ``"modal"`` gives the sandbox-per-subtask shape).  The agent is asked to
    finish with a JSON object of the schema fields, recovered via
    :func:`extract_json_output`.

    Args:
        provider: A Chimera provider instance for the agent's model.
        env_provider: Env factory backend (``local``/``docker``/``e2b``/…).
        env_opts: Keyword options forwarded to ``create_environment``.
        system: Optional system-prompt override.

    Returns:
        A :data:`SubtaskExecutor` suitable for :meth:`WideResearchRunner.run`.
    """
    from chimera.core.agent import Agent
    from chimera.core.prompt import Prompt
    from chimera.env.factory import create_environment

    def _execute(st: Subtask) -> dict[str, Any]:
        env = create_environment(env_provider, **(env_opts or {}))
        try:
            env.setup()
            prompt = Prompt.from_string(system) if system else None
            agent = Agent(provider=provider, prompt=prompt)
            result = agent.run(st.prompt, env)
            return extract_json_output(result.output)
        finally:
            env.cleanup()

    return _execute
