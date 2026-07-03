"""NativeHarnessRunner — grade a framework's OWN SWE-bench harness output.

Some external agents (SWE-agent, mini-SWE-agent, Agentless, AutoCodeRover,
Moatless, …) ship their own SWE-bench harness that writes a ``predictions``
file. Rather than re-drive their loop, this runner **shells out to that
external harness once**, then reads the predictions it wrote and maps each
``instance_id`` onto an :class:`~chimera.eval.runners.base.AgentRunResult`
(one per task) — so their scores are reproduced under our controls and graded
by the same column grader. See ``docs/specs/agent-benchmark-matrix.md`` (A4).

The harness command is invoked through an injectable ``runner``
(``subprocess.run``) so the mapping logic is unit-testable without any real
harness, subprocess, network, or LLM. These are honest scaffolds: live
execution requires the external framework installed, and real end-to-end
verification happens later with real infra.

This runner cannot attribute per-instance cost or wall-clock, so it never
fabricates them: ``cost_usd`` stays ``0.0`` with ``raw["cost"] == "unknown"``,
``wall_clock_sec`` stays ``0.0`` per cell, and the whole-batch duration is
recorded once in ``raw["batch_wall_clock_sec"]``.
"""

from __future__ import annotations

import glob as _glob
import json
import re
import shlex
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING, Any, Callable

from chimera.eval.runners.base import AgentRunResult

if TYPE_CHECKING:
    from chimera.env.base import Environment

_INSTANCE_KEYS = ("instance_id", "id", "task_id")
_PATCH_KEYS = ("model_patch", "prediction", "patch")

#: Placeholders substituted into ``harness_cmd`` and ``predictions_glob``.
_PLACEHOLDER = re.compile(r"\{(out_dir|subset)\}")


class NativeHarnessRunner:
    """Run an external SWE-bench harness once, then map its predictions.

    Args:
        id: Row label for the matrix.
        harness_cmd: Command template for the framework's own harness. Supports
            ``{out_dir}`` (a fresh temp output directory) and ``{subset}`` (a
            comma-joined list of the requested instance ids). Substituted, then
            ``shlex.split`` into an argv list.
        predictions_glob: Glob (also supporting ``{out_dir}`` / ``{subset}``)
            matching the predictions file(s) the harness writes — JSONL with an
            ``instance_id`` plus a ``model_patch`` / ``prediction`` / ``patch``
            per line (a whole-file JSON array/object is tolerated too).
        timeout: Optional wall-clock cap (seconds) for the harness process.
            ``None`` means no timeout.
        runner: The ``subprocess.run``-compatible callable. Injectable so tests
            pass a fake — no real harness is spawned.
    """

    def __init__(
        self,
        id: str,
        harness_cmd: str,
        predictions_glob: str,
        timeout: float | None = None,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        self.id = id
        self.harness_cmd = harness_cmd
        self.predictions_glob = predictions_glob
        self.timeout = timeout
        self.runner = runner

    def run_all(
        self,
        tasks: Any,
        env: Environment | None = None,
        budget: Any = None,
    ) -> dict[str, AgentRunResult]:
        """Run the harness over *tasks*, then map its predictions to results.

        Args:
            tasks: An iterable of benchmark tasks (dicts with an
                ``instance_id``/``id`` key, objects exposing one, or raw
                instance-id strings).
            env: Optional environment; recorded in ``raw`` but not used to place
                execution (the external harness manages its own sandbox).
            budget: Optional budget spec. This runner cannot honor a tool-call
                budget, so it is recorded in ``raw`` but not enforced.

        Returns:
            A dict mapping ``instance_id`` to an :class:`AgentRunResult`. Every
            instance found in the predictions maps to a ``completed`` cell with
            its patch; every requested instance *without* a prediction maps to
            an ``error`` cell (or ``timeout`` when the harness timed out).
        """
        requested = [iid for iid in (self._instance_id(t) for t in tasks) if iid]
        out_dir = tempfile.mkdtemp(prefix="chimera-native-harness-")
        subset = ",".join(requested)
        argv = shlex.split(self._render(self.harness_cmd, out_dir, subset))

        timed_out = False
        harness_error: str | None = None
        exit_code: int | None = None
        stderr = ""
        started = time.monotonic()
        try:
            proc = self.runner(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            exit_code = int(getattr(proc, "returncode", 0) or 0)
            stderr = self._as_text(getattr(proc, "stderr", ""))
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception as exc:  # noqa: BLE001 - missing binary, etc.
            harness_error = repr(exc)
        elapsed = time.monotonic() - started

        glob_pattern = self._render(self.predictions_glob, out_dir, subset)
        predictions = self._read_predictions(glob_pattern)

        batch_meta: dict[str, Any] = {
            "cost": "unknown",
            "batch_scope": True,
            "batch_wall_clock_sec": elapsed,
            "batch_size": len(requested) or len(predictions),
            "harness_exit_code": exit_code,
            "harness_stderr": stderr,
            "harness_argv": argv,
            "out_dir": out_dir,
            "predictions_glob": glob_pattern,
            "budget": None if budget is None else repr(budget),
            "env": None if env is None else type(env).__name__,
        }
        if harness_error is not None:
            batch_meta["harness_error"] = harness_error

        cells: dict[str, AgentRunResult] = {}
        for instance_id, prediction in predictions.items():
            cells[instance_id] = AgentRunResult(
                patch=prediction["patch"],
                status="completed",
                cost_usd=0.0,
                wall_clock_sec=0.0,
                raw={**batch_meta, "prediction": prediction["raw"]},
            )

        missing_status = "timeout" if timed_out else "error"
        for instance_id in requested:
            if instance_id not in cells:
                cells[instance_id] = AgentRunResult(
                    patch=None,
                    status=missing_status,
                    cost_usd=0.0,
                    wall_clock_sec=0.0,
                    raw={
                        **batch_meta,
                        "note": "no prediction emitted for instance_id",
                    },
                )
        return cells

    def run(
        self,
        task: Any,
        env: Environment | None = None,
        budget: Any = None,
    ) -> AgentRunResult:
        """Convenience: run the harness for a single task and return its cell.

        Args:
            task: One benchmark task carrying an instance id.
            env: Forwarded to :meth:`run_all`.
            budget: Forwarded to :meth:`run_all`.

        Returns:
            The :class:`AgentRunResult` for this task's instance id.

        Raises:
            ValueError: If no instance id can be derived from *task*
                (this runner is keyed on instance ids).
            RuntimeError: If :meth:`run_all` produced no cell for the id.
        """
        instance_id = self._instance_id(task)
        if not instance_id:
            raise ValueError(
                f"NativeHarnessRunner {self.id!r}: cannot derive an instance_id "
                f"from task {task!r}"
            )
        cells = self.run_all([task], env=env, budget=budget)
        if instance_id not in cells:
            raise RuntimeError(
                f"NativeHarnessRunner {self.id!r}: no cell produced for "
                f"instance_id={instance_id!r}"
            )
        return cells[instance_id]

    def _render(self, template: str, out_dir: str, subset: str) -> str:
        """Substitute ``{out_dir}`` / ``{subset}`` in a single regex pass."""
        mapping = {"out_dir": out_dir, "subset": subset}
        return _PLACEHOLDER.sub(lambda m: mapping[m.group(1)], template)

    def _instance_id(self, task: Any) -> str:
        """Best-effort extraction of a task's instance id (empty when absent)."""
        if isinstance(task, str):
            return task
        if isinstance(task, dict):
            for key in _INSTANCE_KEYS:
                val = task.get(key)
                if isinstance(val, str) and val:
                    return val
            return ""
        for attr in _INSTANCE_KEYS:
            val = getattr(task, attr, None)
            if isinstance(val, str) and val:
                return val
        return ""

    def _read_predictions(self, pattern: str) -> dict[str, dict[str, Any]]:
        """Read every predictions file matching *pattern*.

        Args:
            pattern: A concrete glob (placeholders already substituted).

        Returns:
            A dict mapping ``instance_id`` to ``{"patch": <str|None>,
            "raw": <record dict>}``. Later files/lines win on id collision.
        """
        out: dict[str, dict[str, Any]] = {}
        for path in sorted(_glob.glob(pattern)):
            for record in self._load_records(path):
                instance_id = self._record_instance_id(record)
                if not instance_id:
                    continue
                out[instance_id] = {
                    "patch": self._record_patch(record),
                    "raw": record,
                }
        return out

    @staticmethod
    def _record_instance_id(record: dict[str, Any]) -> str:
        for key in _INSTANCE_KEYS:
            val = record.get(key)
            if isinstance(val, str) and val:
                return val
        return ""

    @staticmethod
    def _record_patch(record: dict[str, Any]) -> str | None:
        for key in _PATCH_KEYS:
            val = record.get(key)
            if isinstance(val, str):
                return val
        return None

    @staticmethod
    def _load_records(path: str) -> list[dict[str, Any]]:
        """Parse one predictions file as JSONL, falling back to whole-file JSON."""
        try:
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            return []

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        records: list[dict[str, Any]] = []
        jsonl_ok = bool(lines)
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                jsonl_ok = False
                break
            if isinstance(obj, dict):
                records.append(obj)
            elif isinstance(obj, list):
                records.extend(item for item in obj if isinstance(item, dict))
        if jsonl_ok:
            return records

        try:
            whole = json.loads(content)
        except json.JSONDecodeError:
            return []
        if isinstance(whole, dict):
            return [whole]
        if isinstance(whole, list):
            return [item for item in whole if isinstance(item, dict)]
        return []

    @staticmethod
    def _as_text(value: Any) -> str:
        """Coerce subprocess stderr (str, bytes, or None) to ``str``."""
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return str(value)
