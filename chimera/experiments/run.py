"""The experiment toolkit: run something, keep the evidence, resume the rest.

Chimera had no API for *"run an experiment and keep the evidence,"* so a
benchmark push was driven by five one-off scripts that each hand-rolled a run
directory, a progress file, resume logic, and ``.env`` loading. They wrote a
``pb-runs/`` tree beside themselves that reached 336 MB and went unnoticed for
a month (spec: ``docs/specs/storage-and-experiments.md``, Part 4). The scripts
were not the disease — the missing API was. This module is the paved road.

Three properties are load-bearing:

**Containment.** Everything a run writes lands under
``store_path("experiment-runs") / <name> / <stamp>/``. Run names are validated
and every relative path is resolved through :meth:`Run.path`, which rejects
absolutes, ``..`` traversal, and symlinks that leave the run directory. A
toolkit run is *structurally* unable to write outside its store, which is what
lets ``chimera gc`` reclaim experiment output without a second retention
mechanism and what keeps run output off the repo root.

**Durability.** :meth:`Run.jsonl` appends one JSON line and flushes it to the
operating system before returning. A run killed mid-loop keeps every row it
wrote, and :meth:`Run.seen` skips the truncated tail a hard kill can leave, so
the next :func:`resume` continues where the crash landed instead of re-running
completed work.

**Provenance.** ``manifest.json`` records the git SHA and dirty flag at start,
answering *which code produced this number* — the other half of the receipts
discipline in ``docs/playbooks/13-live-bench-runs.md``. ``result.json`` is
shaped like a bench receipt cell and is validated against the same invariants
``scripts/render_observatory.py`` enforces, so a curated result can be copied
into ``data/`` and cannot fail that gate later. The copy is always a deliberate
human act — ``data/`` stays curated, and nothing here writes to it.

Typical use::

    from chimera.experiments import start

    run = start("pb-sweep", config={"model": "glm-5.2", "limit": 10}, resume=True)
    done = run.seen("progress.jsonl", key="task")
    for task in tasks:
        if task.id in done:
            continue
        run.jsonl("progress.jsonl", {"task": task.id, "ok": ok, "cost": cost})
    run.finish({"passed": n, "total": t, "cost_usd": cost})

Stdlib only, per the zero-dependency-core rule.
"""
from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterator, Mapping

from chimera.config.paths import store_path

__all__ = [
    "ExperimentError",
    "NoSuchRun",
    "OutsideRun",
    "Run",
    "RunInfo",
    "git_provenance",
    "iter_runs",
    "list_runs",
    "load_run",
    "resume",
    "runs_root",
    "start",
]

#: Registry key for the store every run lives under. Declared in
#: ``chimera/config/paths.py``; relocating it is a config change, not a code
#: change.
STORE = "experiment-runs"

#: The manifest filename, written at :func:`start` and rewritten on finish.
MANIFEST = "manifest.json"

#: The receipt filename, written by :meth:`Run.finish`.
RESULT = "result.json"

#: ``manifest.json`` status while a run is in flight. A run found in this
#: state with no live writer was interrupted — see :attr:`RunInfo.interrupted`.
STATUS_RUNNING = "running"

#: Terminal status set by :meth:`Run.finish`.
STATUS_COMPLETED = "completed"

#: Terminal status set by :meth:`Run.fail`.
STATUS_FAILED = "failed"

#: Characters a run or stamp name may contain. Deliberately narrow: these
#: names become directory names, so anything that could traverse or escape is
#: rejected rather than sanitised (a silently rewritten name is a name you
#: cannot find again).
_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

#: Filenames at the run root the toolkit owns. Callers may read them; opening
#: one as a JSONL ledger would destroy the run's own record, so it is refused.
_RESERVED = frozenset({MANIFEST, RESULT})

_GIT_TIMEOUT = 10.0


class ExperimentError(RuntimeError):
    """Base class for every error this module raises."""


class OutsideRun(ExperimentError):
    """Raised when a path would resolve outside its run directory.

    The toolkit's containment guarantee is enforced here rather than
    documented: absolute paths, ``..`` traversal, and symlinks pointing out of
    the run directory all raise instead of writing.
    """


class NoSuchRun(ExperimentError):
    """Raised when a named run or stamp does not exist on disk."""


def _utc_now() -> datetime:
    """Return the current UTC time (patchable in one place for tests)."""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    """Format a datetime as ``2026-07-27T14:03:11Z``."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_for(moment: datetime) -> str:
    """Format a datetime as a directory-safe UTC stamp.

    ``2026-07-27T14-03-11`` — colons are not portable in path names, and UTC
    keeps lexical order equal to chronological order across machines and
    daylight-saving boundaries.
    """
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _validate_name(value: str, *, what: str) -> str:
    """Return *value* if it is safe to use as a single path component.

    Args:
        value: The candidate name.
        what: Noun for the error message (``"run name"`` / ``"stamp"``).

    Returns:
        The name unchanged.

    Raises:
        ValueError: If the name is empty, is ``.`` or ``..``, starts with a
            dot, or contains anything outside ``[A-Za-z0-9._-]``. Path
            separators are rejected by that rule, so a name can never widen
            the run directory's reach.
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")
    if value in (".", ".."):
        raise ValueError(f"{what} may not be {value!r}")
    # Character set before the leading-dot rule, so "../x" reports the "/" —
    # the actual reason it is dangerous — rather than a confusing dot message.
    bad = sorted({ch for ch in value if ch not in _NAME_OK})
    if bad:
        joined = "".join(bad)
        raise ValueError(
            f"{what} {value!r} contains disallowed characters {joined!r}; "
            "use letters, digits, dot, dash, underscore"
        )
    if value.startswith("."):
        raise ValueError(f"{what} may not start with a dot: {value!r}")
    return value


def _git(args: list[str], cwd: Path) -> str | None:
    """Run one read-only git command, returning stripped stdout or ``None``.

    Every failure mode — git absent, not a repository, a timeout, a non-zero
    exit — maps to ``None``. Recording provenance must never be what stops an
    experiment from starting.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def git_provenance(cwd: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Describe the working tree that is about to produce a number.

    Args:
        cwd: Directory to inspect (default: the current working directory).

    Returns:
        ``{"sha", "branch", "dirty"}``. ``sha``/``branch`` are ``None`` outside
        a repository; ``dirty`` is ``None`` when it could not be determined and
        ``True`` when the tree has uncommitted changes — the flag that says a
        result is not reproducible from the SHA alone.
    """
    where = Path(cwd) if cwd is not None else Path.cwd()
    sha = _git(["rev-parse", "HEAD"], where)
    if sha is None:
        return {"sha": None, "branch": None, "dirty": None}
    status = _git(["status", "--porcelain"], where)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], where)
    return {
        "sha": sha,
        "branch": branch or None,
        "dirty": None if status is None else bool(status.strip()),
    }


def runs_root(project: str | os.PathLike[str] | None = None) -> Path:
    """Return the directory every run lives under.

    Args:
        project: Forwarded to :func:`chimera.config.paths.store_path`; the
            ``experiment-runs`` store is user-scoped, so this is accepted for
            symmetry and currently has no effect.

    Returns:
        ``<storage root>/experiment-runs`` — not created.
    """
    return store_path(STORE, project)


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON via a temp file and ``os.replace``.

    A manifest is rewritten while a run finishes; a crash mid-write must never
    leave a truncated manifest, because an unparseable manifest is a run
    nothing can report on or resume.
    """
    tmp = path.with_name(f"{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, returning ``None`` when absent or unparseable."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _pid_alive(pid: int) -> bool:
    """Whether a PID exists on this host (best-effort, never raises).

    Non-positive PIDs are rejected before ``os.kill`` sees them: POSIX reads
    ``0`` as *this process group* and ``-1`` as *every process*, so a corrupt
    or hand-edited manifest could otherwise aim a liveness probe at the whole
    machine. There is no such process, so the answer is ``False``.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _dir_size(path: Path) -> int:
    """Total size in bytes of every regular file under *path*."""
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _normalize_cell(raw: Mapping[str, Any], *, fallback: str) -> dict[str, Any]:
    """Fill and validate one bench-receipt cell.

    The output matches what ``scripts/render_observatory.py`` reads out of
    ``data/*.json``: ``agent_id``, ``benchmark``, ``total``, ``passed``,
    ``pass_rate``, ``cost_usd``, ``status``, plus whatever else the caller
    recorded. Missing derivable fields are computed; supplied fields are
    checked rather than overwritten, because silently "fixing" an inconsistent
    number is how a fabricated result gets published.

    Args:
        raw: The caller's summary (or one entry of its ``cells`` list).
        fallback: Value used for ``agent_id``/``benchmark`` when the caller
            named neither — the run name, so a receipt is never anonymous.

    Returns:
        The normalized cell.

    Raises:
        ValueError: If ``passed`` exceeds ``total``, a supplied ``pass_rate``
            disagrees with ``passed / total``, a ``status_counts`` tally does
            not sum to ``total``, or ``status`` is ``"error"`` alongside
            passes. These are exactly the invariants the observatory renderer
            refuses to publish, enforced at write time so a copied receipt
            cannot fail that gate months later.
    """
    cell: dict[str, Any] = dict(raw)
    alias_agent = cell.pop("agent", None)
    alias_bench = cell.pop("bench", None)
    alias_cost = cell.pop("cost", None)
    if not cell.get("agent_id"):
        cell["agent_id"] = alias_agent or fallback
    if not cell.get("benchmark"):
        cell["benchmark"] = alias_bench or fallback

    total = int(cell.get("total", 0) or 0)
    passed = int(cell.get("passed", 0) or 0)
    cell["total"] = total
    cell["passed"] = passed

    if passed > total:
        raise ValueError(
            f"passed={passed} > total={total}: a run cannot pass more tasks "
            "than it graded"
        )

    expect = (passed / total) if total else 0.0
    if "pass_rate" in cell and cell["pass_rate"] is not None:
        given = float(cell["pass_rate"])
        if not math.isclose(given, expect, rel_tol=1e-6, abs_tol=1e-9):
            raise ValueError(
                f"pass_rate={given} disagrees with passed/total={passed}/{total}"
            )
        cell["pass_rate"] = given
    else:
        cell["pass_rate"] = expect

    if cell.get("cost_usd") is None:
        cell["cost_usd"] = float(alias_cost or 0.0)
    else:
        cell["cost_usd"] = float(cell["cost_usd"])

    status = str(cell.get("status") or STATUS_COMPLETED)
    cell["status"] = status
    if status == "error" and passed > 0:
        raise ValueError(
            f"status='error' with passed={passed}: an errored run cannot have "
            "produced graded passes"
        )

    counts = cell.get("status_counts")
    if counts:
        tally = sum(int(v) for v in dict(counts).values())
        if tally != total:
            raise ValueError(
                f"status_counts sums to {tally} but total={total}"
            )
    return cell


@dataclass
class Run:
    """One experiment run and everything it is allowed to write.

    Construct through :func:`start` or :func:`resume` rather than directly;
    those set up the directory and manifest. Every write method resolves its
    path through :meth:`path`, so the containment guarantee holds for the whole
    surface, not just the parts a caller remembers to be careful with.

    Attributes:
        name: The experiment name (one directory under the store).
        stamp: The run's UTC stamp (one directory under the name).
        dir: The run directory. Everything this run writes lives here.
        config: The caller's configuration, as recorded in the manifest.
    """

    name: str
    stamp: str
    dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    _handles: dict[Path, IO[str]] = field(default_factory=dict, repr=False)
    _closed: bool = field(default=False, repr=False)

    # -- paths ------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        """Path to this run's ``manifest.json``."""
        return self.dir / MANIFEST

    @property
    def result_path(self) -> Path:
        """Path to this run's ``result.json`` (may not exist yet)."""
        return self.dir / RESULT

    def path(self, rel: str | os.PathLike[str], *, parents: bool = True) -> Path:
        """Resolve a run-relative path, refusing anything outside the run.

        Args:
            rel: A path relative to :attr:`dir`, e.g. ``"ws/task-1/out.txt"``.
            parents: Create the parent directory (default). Turn this off when
                you only want the resolved path and no filesystem effect.

        Returns:
            The absolute path inside the run directory.

        Raises:
            OutsideRun: If *rel* is absolute, traverses out with ``..``, or
                resolves through a symlink that leaves the run directory. The
                check compares real paths, so a symlink planted inside the run
                cannot be used as an exit.
        """
        candidate = Path(rel)
        if candidate.is_absolute():
            raise OutsideRun(
                f"{rel!r} is absolute; run paths are relative to {self.dir}"
            )
        if not str(candidate).strip():
            raise OutsideRun("empty path")
        target = self.dir / candidate
        root_real = os.path.realpath(self.dir)
        target_real = os.path.realpath(target)
        if target_real != root_real and not target_real.startswith(root_real + os.sep):
            raise OutsideRun(
                f"{rel!r} resolves to {target_real}, outside the run directory "
                f"{root_real}"
            )
        if parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def subdir(self, rel: str | os.PathLike[str]) -> Path:
        """Create and return a directory inside the run.

        Args:
            rel: Run-relative directory path, e.g. ``"ws/task-1"``.

        Returns:
            The created directory.

        Raises:
            OutsideRun: Under the same rules as :meth:`path`.
        """
        target = self.path(rel, parents=False)
        target.mkdir(parents=True, exist_ok=True)
        return target

    # -- writing ----------------------------------------------------------
    def _handle(self, rel: str) -> IO[str]:
        """Return the cached append handle for *rel*, opening it on demand."""
        if self._closed:
            raise ExperimentError(f"run {self.name}/{self.stamp} is closed")
        target = self.path(rel)
        if target.name in _RESERVED and target.parent == self.dir:
            raise ExperimentError(
                f"{rel!r} is written by the toolkit; pick another filename"
            )
        handle = self._handles.get(target)
        if handle is None:
            handle = open(target, "a", encoding="utf-8")
            self._handles[target] = handle
        return handle

    def jsonl(self, file: str, record: Mapping[str, Any]) -> None:
        """Append one JSON record to a run-relative JSONL file and flush it.

        The flush is the point: a run killed on the next line keeps this row.
        Rows are the resume ledger — write one per unit of work, keyed by
        whatever :meth:`seen` will look for.

        Args:
            file: Run-relative filename, e.g. ``"progress.jsonl"``.
            record: A JSON-serializable mapping. Values that are not natively
                serializable are recorded via ``str()`` rather than raising,
                so a stray object cannot destroy a long run's ledger.

        Raises:
            OutsideRun: If *file* escapes the run directory.
            ExperimentError: If the run is closed, or *file* is a name the
                toolkit owns.
        """
        handle = self._handle(file)
        handle.write(json.dumps(dict(record), default=str) + "\n")
        handle.flush()

    def write_text(self, file: str, text: str) -> Path:
        """Write (replacing) a text file inside the run.

        Args:
            file: Run-relative filename.
            text: File contents.

        Returns:
            The path written.
        """
        target = self.path(file)
        target.write_text(text, encoding="utf-8")
        return target

    def write_json(self, file: str, payload: Any) -> Path:
        """Write (replacing) a JSON file inside the run, atomically.

        Args:
            file: Run-relative filename.
            payload: Any JSON-serializable object.

        Returns:
            The path written.
        """
        target = self.path(file)
        _atomic_write_json(target, payload)
        return target

    # -- reading ----------------------------------------------------------
    def rows(self, file: str) -> list[dict[str, Any]]:
        """Read every parseable JSON object from a run-relative JSONL file.

        A hard kill can leave a partial final line. Unparseable lines are
        skipped rather than raised on: the whole point of the ledger is that it
        survives the crash that produced it.

        Args:
            file: Run-relative filename.

        Returns:
            The records, in file order. Empty when the file does not exist.
        """
        target = self.path(file, parents=False)
        if not target.is_file():
            return []
        out: list[dict[str, Any]] = []
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue  # truncated tail from a kill — expected, not fatal
                if isinstance(parsed, dict):
                    out.append(parsed)
        return out

    def seen(self, file: str, key: str = "id") -> set[Any]:
        """Return the values of *key* already recorded in a JSONL ledger.

        This is the resume primitive every hand-rolled driver rewrote, usually
        without the truncated-line handling::

            done = run.seen("progress.jsonl", key="task")
            for task in tasks:
                if task.id in done:
                    continue

        Args:
            file: Run-relative JSONL filename.
            key: The record field identifying a unit of work.

        Returns:
            The set of values seen. Rows lacking *key*, and values that cannot
            live in a set, are ignored.
        """
        out: set[Any] = set()
        for row in self.rows(file):
            if key not in row:
                continue
            try:
                out.add(row[key])
            except TypeError:
                continue  # unhashable value — cannot be a resume key
        return out

    def manifest(self) -> dict[str, Any]:
        """Read this run's manifest.

        Returns:
            The parsed manifest, or an empty dict if it is missing or corrupt.
        """
        return _read_json(self.manifest_path) or {}

    @property
    def status(self) -> str:
        """The manifest's current status string."""
        return str(self.manifest().get("status") or STATUS_RUNNING)

    # -- lifecycle --------------------------------------------------------
    def _set_status(self, status: str, **extra: Any) -> dict[str, Any]:
        """Rewrite the manifest with a new status and extra fields."""
        manifest = self.manifest()
        manifest["status"] = status
        manifest.update(extra)
        _atomic_write_json(self.manifest_path, manifest)
        return manifest

    def finish(self, summary: Mapping[str, Any] | None = None) -> Path:
        """Record the result and mark the run completed.

        Writes ``result.json`` shaped like a bench receipt — ``run_id``,
        ``name``, ``stamp``, ``model``, ``git``, and a ``cells`` list — and
        flips the manifest to ``completed``. A run that never reaches this call
        keeps ``status="running"``, which is how an interrupted run is told
        apart from a finished one.

        Pass either one cell's fields (``{"passed": n, "total": t,
        "cost_usd": c}``) or an explicit ``{"cells": [...]}`` list for a
        multi-cell run. Either way the cells are validated against the
        observatory's integrity invariants before anything is written, so a
        receipt copied into ``data/`` later cannot fail that gate. Copying is
        always deliberate: nothing here writes to ``data/``.

        Args:
            summary: The run's headline numbers.

        Returns:
            The path to ``result.json``.

        Raises:
            ValueError: If the summary violates a receipt invariant (see
                :func:`_normalize_cell`). Nothing is written in that case and
                the run stays open, so the caller can correct and retry.
        """
        payload = dict(summary or {})
        raw_cells = payload.pop("cells", None)
        if raw_cells is None:
            cells = [_normalize_cell(payload, fallback=self.name)] if payload else []
        else:
            cells = [_normalize_cell(c, fallback=self.name) for c in raw_cells]

        manifest = self.manifest()
        ended = _utc_now()
        receipt: dict[str, Any] = {
            "run_id": f"{self.name}/{self.stamp}",
            "name": self.name,
            "stamp": self.stamp,
            "model": self.config.get("model"),
            "started_at": manifest.get("started_at"),
            "ended_at": _iso(ended),
            "git": manifest.get("git"),
            "config": self.config,
            "cells": cells,
        }
        _atomic_write_json(self.result_path, receipt)
        self._set_status(STATUS_COMPLETED, ended_at=receipt["ended_at"])
        self.close()
        return self.result_path

    def fail(self, reason: str) -> dict[str, Any]:
        """Mark the run failed with a stated reason.

        Distinct from an interruption on purpose: ``failed`` means the run
        decided it could not continue, while ``running`` on a dead process
        means nobody got to decide. :func:`resume` only picks up the latter.

        Args:
            reason: Why the run stopped.

        Returns:
            The rewritten manifest.
        """
        manifest = self._set_status(
            STATUS_FAILED, ended_at=_iso(_utc_now()), error=str(reason)
        )
        self.close()
        return manifest

    def close(self) -> None:
        """Flush and close every open ledger handle. Idempotent."""
        for handle in self._handles.values():
            try:
                handle.flush()
                handle.close()
            except OSError:
                continue
        self._handles.clear()
        self._closed = True

    def __enter__(self) -> Run:
        """Enter a ``with`` block; the run is already started."""
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close ledger handles, and record a failure if the body raised.

        A body that raised leaves ``status="failed"`` with the exception text.
        A body that returned without calling :meth:`finish` leaves
        ``status="running"`` — an interrupted run, resumable, which is the
        honest record of what happened.
        """
        if exc_type is not None and self.status == STATUS_RUNNING:
            self.fail(f"{exc_type.__name__}: {exc}")
        else:
            self.close()


@dataclass(frozen=True)
class RunInfo:
    """A run as seen from outside — what ``list`` and ``show`` report.

    Attributes:
        name: The experiment name.
        stamp: The run stamp.
        dir: The run directory.
        manifest: The parsed ``manifest.json`` (empty if missing/corrupt).
        result: The parsed ``result.json``, or ``None`` if the run never
            finished.
    """

    name: str
    stamp: str
    dir: Path
    manifest: dict[str, Any]
    result: dict[str, Any] | None

    @property
    def status(self) -> str:
        """The manifest's status verbatim (``running`` for an unfinished run)."""
        return str(self.manifest.get("status") or STATUS_RUNNING)

    @property
    def interrupted(self) -> bool:
        """Whether this run says ``running`` but nothing is running it.

        Decided from the recorded host and PID: a run started on another host
        cannot be checked from here and is reported interrupted, as is one
        whose PID is gone. PIDs are recycled, so a *live* verdict is the weaker
        one — it can only be wrong in the direction of leaving a dead run
        listed as running, never of declaring a live run dead.
        """
        if self.status != STATUS_RUNNING:
            return False
        pid = self.manifest.get("pid")
        host = self.manifest.get("host")
        if not isinstance(pid, int) or host != socket.gethostname():
            return True
        return not _pid_alive(pid)

    @property
    def started_at(self) -> str:
        """ISO-8601 UTC start time, or ``""`` when the manifest lacks one."""
        return str(self.manifest.get("started_at") or "")

    def size_bytes(self) -> int:
        """Total on-disk size of the run directory."""
        return _dir_size(self.dir)

    def open(self) -> Run:
        """Reattach a writable :class:`Run` to this directory.

        Returns:
            A :class:`Run` bound to the same directory and config. Ledger
            writes append to the existing files.
        """
        config = self.manifest.get("config")
        return Run(
            name=self.name,
            stamp=self.stamp,
            dir=self.dir,
            config=dict(config) if isinstance(config, dict) else {},
        )


def _run_dirs(root: Path, name: str) -> list[Path]:
    """Return a name's run directories, oldest stamp first."""
    parent = root / name
    if not parent.is_dir():
        return []
    return sorted((p for p in parent.iterdir() if p.is_dir()), key=lambda p: p.name)


def _info_for(directory: Path, name: str) -> RunInfo:
    """Build a :class:`RunInfo` from a run directory."""
    return RunInfo(
        name=name,
        stamp=directory.name,
        dir=directory,
        manifest=_read_json(directory / MANIFEST) or {},
        result=_read_json(directory / RESULT),
    )


def start(
    name: str,
    config: Mapping[str, Any] | None = None,
    *,
    resume: bool = False,
    stamp: str | None = None,
    project: str | os.PathLike[str] | None = None,
) -> Run:
    """Begin an experiment run and write its manifest.

    Creates ``<store>/<name>/<stamp>/`` and a ``manifest.json`` recording the
    name, stamp, config, ``sys.argv``, working directory, git SHA and dirty
    flag, host, PID, Chimera version, and ``status="running"``. Nothing else
    is created — the run's own files are up to the caller.

    Args:
        name: The experiment name. Becomes one directory under the store, so
            it must be a plain ``[A-Za-z0-9._-]`` token.
        config: Whatever parameterised this run — model, limits, dataset,
            flags. Recorded verbatim in the manifest and echoed into
            ``result.json``, because *which settings produced this number* is
            half of a reproducible receipt.
        resume: When ``True``, reattach to this name's newest *interrupted*
            run instead of starting a new one, falling back to a new run when
            there is none. This is the one-call form of the resume loop; use
            :func:`resume` when a missing run should be an error.
        stamp: Override the generated UTC stamp. Mainly for tests and for
            pinning a run directory by hand.
        project: Forwarded to the path registry (see :func:`runs_root`).

    Returns:
        The open :class:`Run`.

    Raises:
        ValueError: If *name* or *stamp* is not a safe path component.
    """
    _validate_name(name, what="run name")
    root = runs_root(project)

    if resume:
        interrupted = [i for i in list_runs(name, project=project) if i.interrupted]
        if interrupted:
            reattached = interrupted[-1].open()
            reattached._set_status(
                STATUS_RUNNING,
                resumed_at=_iso(_utc_now()),
                pid=os.getpid(),
                host=socket.gethostname(),
            )
            return reattached

    started = _utc_now()
    if stamp is not None:
        _validate_name(stamp, what="stamp")
    chosen = stamp or _stamp_for(started)
    directory = root / name / chosen
    if stamp is None:
        suffix = 1
        while directory.exists():
            chosen = f"{_stamp_for(started)}-{suffix}"
            directory = root / name / chosen
            suffix += 1
    directory.mkdir(parents=True, exist_ok=True)

    try:
        from chimera import __version__ as chimera_version
    except Exception:  # noqa: BLE001 — provenance must not block a run
        chimera_version = "unknown"

    cwd = Path.cwd()
    manifest = {
        "name": name,
        "stamp": chosen,
        "status": STATUS_RUNNING,
        "started_at": _iso(started),
        "config": dict(config or {}),
        "argv": list(sys.argv),
        "cwd": str(cwd),
        "git": git_provenance(cwd),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "chimera_version": chimera_version,
    }
    _atomic_write_json(directory / MANIFEST, manifest)
    return Run(name=name, stamp=chosen, dir=directory, config=dict(config or {}))


def resume(
    name: str,
    stamp: str | None = None,
    *,
    project: str | os.PathLike[str] | None = None,
) -> Run:
    """Reattach to an existing run so its ledgers can be continued.

    Args:
        name: The experiment name.
        stamp: A specific run stamp. When omitted, the newest **interrupted**
            run is chosen — a completed run is not something to continue, and
            picking it silently would append rows to a published result.
        project: Forwarded to the path registry (see :func:`runs_root`).

    Returns:
        The reattached :class:`Run`, with ``status`` set back to ``running``
        and a ``resumed_at`` entry added to the manifest.

    Raises:
        NoSuchRun: If the name has no runs, the named stamp does not exist, or
            no interrupted run is available to continue.
        ValueError: If *name* or *stamp* is not a safe path component.
    """
    _validate_name(name, what="run name")
    infos = list_runs(name, project=project)
    if not infos:
        raise NoSuchRun(f"no runs recorded for experiment {name!r}")

    if stamp is not None:
        _validate_name(stamp, what="stamp")
        match = next((i for i in infos if i.stamp == stamp), None)
        if match is None:
            known = ", ".join(i.stamp for i in infos)
            raise NoSuchRun(f"no run {name}/{stamp}; recorded stamps: {known}")
    else:
        candidates = [i for i in infos if i.interrupted]
        if not candidates:
            raise NoSuchRun(
                f"no interrupted run for {name!r} to resume "
                f"({len(infos)} run(s) recorded, all finished or still live); "
                "pass an explicit stamp to reopen one anyway"
            )
        match = candidates[-1]

    run = match.open()
    run._set_status(
        STATUS_RUNNING,
        resumed_at=_iso(_utc_now()),
        pid=os.getpid(),
        host=socket.gethostname(),
    )
    return run


def list_runs(
    name: str | None = None,
    *,
    project: str | os.PathLike[str] | None = None,
) -> list[RunInfo]:
    """List recorded runs, oldest first.

    Args:
        name: Restrict to one experiment. ``None`` lists every experiment,
            ordered by name then stamp.
        project: Forwarded to the path registry (see :func:`runs_root`).

    Returns:
        The runs found. Empty when the store does not exist yet — an absent
        store is "no experiments have been run," not an error.
    """
    root = runs_root(project)
    if not root.is_dir():
        return []
    names = [name] if name is not None else sorted(
        p.name for p in root.iterdir() if p.is_dir()
    )
    out: list[RunInfo] = []
    for candidate in names:
        for directory in _run_dirs(root, candidate):
            out.append(_info_for(directory, candidate))
    return out


def load_run(
    ref: str,
    *,
    project: str | os.PathLike[str] | None = None,
) -> RunInfo:
    """Look up one run by ``"<name>"`` or ``"<name>/<stamp>"``.

    Args:
        ref: The experiment name, optionally with a stamp. A bare name
            resolves to that experiment's newest run.
        project: Forwarded to the path registry (see :func:`runs_root`).

    Returns:
        The matching :class:`RunInfo`.

    Raises:
        NoSuchRun: If nothing matches.
        ValueError: If the reference is not a safe path component pair.
    """
    name, _, stamp = ref.partition("/")
    _validate_name(name, what="run name")
    infos = list_runs(name, project=project)
    if not infos:
        raise NoSuchRun(f"no runs recorded for experiment {name!r}")
    if not stamp:
        return infos[-1]
    _validate_name(stamp, what="stamp")
    match = next((i for i in infos if i.stamp == stamp), None)
    if match is None:
        known = ", ".join(i.stamp for i in infos)
        raise NoSuchRun(f"no run {name}/{stamp}; recorded stamps: {known}")
    return match


def iter_runs(
    project: str | os.PathLike[str] | None = None,
) -> Iterator[RunInfo]:
    """Yield every recorded run (streaming form of :func:`list_runs`)."""
    yield from list_runs(None, project=project)
