from __future__ import annotations

import os
import re
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any, Callable

from chimera.config.ignore import NOT_SOURCE_DIRS
from chimera.config.paths import STATE_DIRNAME, store_path, store_retention
from chimera.env.base import Environment
from chimera.env.session import SessionMixin
from chimera.types import CommandResult, TestResult

#: Where checkpoints lived before M3: ``<workdir>/.chimera_checkpoints``, a
#: sibling of the project state dir rather than a child, which is why no
#: registry-scoped scan could see it. Still read (see :meth:`restore`) — the
#: standing rule is archive or relocate, never strand.
LEGACY_CHECKPOINT_DIRNAME = ".chimera_checkpoints"

#: Chimera's own state directories inside a workspace. Never copied into a
#: checkpoint or a clone: the new checkpoint store lives under ``.chimera``, so
#: copying it would nest every checkpoint inside the next one.
WORKSPACE_STATE_DIRS: frozenset[str] = frozenset({STATE_DIRNAME, LEGACY_CHECKPOINT_DIRNAME})

#: What a checkpoint refuses to copy. A checkpoint is a snapshot of *source*;
#: a virtualenv, a ``node_modules``, or a build tree is reproducible bulk that
#: turned one real checkpoint into 2.0 GB (spec:
#: ``docs/specs/storage-and-experiments.md``). Excluded symmetrically:
#: :meth:`LocalEnvironment.restore` neither restores these nor deletes them, so
#: a restore leaves a working ``.venv`` exactly where it was.
CHECKPOINT_EXCLUDED_DIRS: frozenset[str] = NOT_SOURCE_DIRS | WORKSPACE_STATE_DIRS

#: Warn when a single checkpoint exceeds this many bytes. Not a limit — the
#: checkpoint is still written — but a large checkpoint now announces itself
#: instead of accumulating in silence for four months.
CHECKPOINT_SIZE_WARN_BYTES = 256 * 1024 * 1024


class LargeCheckpointWarning(UserWarning):
    """A checkpoint exceeded :data:`CHECKPOINT_SIZE_WARN_BYTES`.

    Its own category so a caller that genuinely checkpoints large trees can
    silence exactly this and nothing else.
    """


def _ignore_dirs(exclude: frozenset[str]) -> Callable[[Any, list[str]], set[str]]:
    """Build a :func:`shutil.copytree` ``ignore`` callable for *exclude*.

    Applied at every level of the recursion, which is what makes a nested
    ``site/node_modules`` as excluded as a top-level one. Deliberately not
    :func:`shutil.ignore_patterns`: that matches names by glob without regard to
    type, so a *file* named ``build`` would be dropped along with build
    directories.

    Args:
        exclude: Directory names to skip.

    Returns:
        A callable of ``(directory, names)`` returning the names to skip.
    """

    def _ignore(directory: Any, names: list[str]) -> set[str]:
        base = Path(directory)
        return {n for n in names if n in exclude and (base / n).is_dir()}

    return _ignore


def _tree_size(root: Path) -> int:
    """Return the total size in bytes of the files under *root*.

    Symlinks are counted at zero rather than followed: a link into a virtualenv
    must not be measured as if the checkpoint contained it, and following links
    could otherwise walk in a circle.
    """
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


class LocalEnvironment(SessionMixin, Environment):
    """Local filesystem environment with file-copy checkpointing.

    Checkpoints are written under the ``project-checkpoints`` store the path
    registry declares — ``<workdir>/.chimera/checkpoints`` — and exclude
    :data:`CHECKPOINT_EXCLUDED_DIRS`. Checkpoints written before that move
    (``<workdir>/.chimera_checkpoints``) stay readable by :meth:`restore` and
    are never deleted; only new writes land in the registry location.
    """

    def __init__(
        self,
        workdir: str,
        test_cmd: str = "python -m pytest",
        timeout: int = 300,
        session: bool = False,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.test_cmd = test_cmd
        self.timeout = timeout
        self._checkpoint_dir: Path | None = None
        self._use_session = session

    @property
    def _legacy_checkpoint_dir(self) -> Path:
        """The pre-M3 checkpoint location, read-only from here on."""
        return self.workdir / LEGACY_CHECKPOINT_DIRNAME

    def _contain(self, path: str) -> Path:
        """Resolve ``path`` relative to workdir and ensure it stays inside.

        Blocks:
          * absolute paths outside workdir (``/etc/passwd`` replaces the
            left operand in ``Path(workdir) / "/etc/passwd"``),
          * ``..`` traversal (``../../id_rsa``),
          * symlinks pointing outside workdir (``resolve()`` follows them
            so ``relative_to`` catches the escape).

        Args:
            path: Path supplied by the caller (user or LLM). Absolute paths
                that resolve inside ``self.workdir`` are allowed.

        Returns:
            The resolved absolute ``Path`` guaranteed to live under
            ``self.workdir``.

        Raises:
            PermissionError: If the resolved path escapes ``self.workdir``.
        """
        candidate = (self.workdir / path).resolve()
        workdir_resolved = self.workdir.resolve()
        try:
            candidate.relative_to(workdir_resolved)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workdir: {path}") from exc
        return candidate

    def setup(self) -> None:
        """Create the workdir and resolve — but do not create — the store.

        ``setup`` used to ``mkdir`` the checkpoint directory unconditionally, so
        every environment ever constructed left a directory behind whether or
        not a checkpoint was taken. The path is resolved here through the
        registry; :meth:`checkpoint` creates it on first write.
        """
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_dir = store_path("project-checkpoints", self.workdir)
        if self._use_session:
            self.start_session()

    def cleanup(self) -> None:
        if self.has_session:
            self.end_session()

    def read_file(self, path: str) -> str:
        full = self._contain(path)
        if not full.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return full.read_text()

    def write_file(self, path: str, content: str) -> None:
        full = self._contain(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        roots = [d for d in (self._checkpoint_dir, self._legacy_checkpoint_dir) if d]
        results = []
        for p in self.workdir.glob(pattern):
            if p.is_file():
                if any(str(p).startswith(str(root)) for root in roots):
                    continue
                results.append(str(p.relative_to(self.workdir)))
        return sorted(results)

    def run_command(self, cmd: str, timeout: int | None = None, shell_name: str = "main") -> CommandResult:
        if self.has_session:
            return self.run_in_session(cmd, shell_name=shell_name, timeout=timeout or self.timeout)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.workdir),
                timeout=timeout or self.timeout,
            )
            return CommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Command timed out", exit_code=124)

    def run_tests(self) -> TestResult:
        result = self.run_command(self.test_cmd)
        return self._parse_test_output(result)

    def checkpoint(self) -> str:
        """Snapshot the workspace source into the ``project-checkpoints`` store.

        :data:`CHECKPOINT_EXCLUDED_DIRS` is skipped, so a workspace with a
        ``.venv`` or ``node_modules`` checkpoints its source and not its
        dependencies. Past :data:`CHECKPOINT_SIZE_WARN_BYTES` the result is
        still written, and warned about.

        Returns:
            The checkpoint ID — a decimal counter, allocated above every ID in
            *both* the registry store and the legacy directory so an ID can
            never mean two different snapshots.
        """
        assert self._checkpoint_dir is not None
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        existing = self._existing_ids(self._checkpoint_dir) | self._existing_ids(
            self._legacy_checkpoint_dir
        )
        cp_id = str(max(existing, default=-1) + 1)
        cp_dir = self._checkpoint_dir / cp_id

        self._copy_workspace(self.workdir, cp_dir, exclude=CHECKPOINT_EXCLUDED_DIRS)
        self._warn_if_large(cp_dir)
        self._apply_retention(keep_id=cp_id)
        return cp_id

    def restore(self, checkpoint_id: str) -> None:
        """Restore the workspace source from a checkpoint.

        Only what a checkpoint *captures* is replaced:
        :data:`CHECKPOINT_EXCLUDED_DIRS` is skipped when clearing the workspace
        as well as when copying back, so restoring never deletes a virtualenv,
        a ``node_modules``, a ``.git``, or the project's ``.chimera`` state that
        the checkpoint deliberately did not contain.

        Args:
            checkpoint_id: An ID from :meth:`checkpoint`. Checkpoints written to
                the pre-M3 ``.chimera_checkpoints`` directory are still found.

        Raises:
            ValueError: If no checkpoint with that ID exists in either location.
            PermissionError: If the workdir is ``/`` or ``$HOME``.
        """
        assert self._checkpoint_dir is not None
        cp_dir = self._resolve_checkpoint(checkpoint_id)

        # Refuse to wipe obviously-dangerous workdirs. ``restore`` issues
        # ``shutil.rmtree`` against every top-level entry, so a misconfigured
        # workdir at ``/`` or the user's home directory would be catastrophic.
        resolved = self.workdir.resolve()
        forbidden = {Path("/").resolve(), Path(os.path.expanduser("~")).resolve()}
        if resolved in forbidden:
            raise PermissionError(
                f"Refusing to restore: workdir {resolved} is root or HOME"
            )

        # Remove what the checkpoint owns; leave what it deliberately excluded.
        for item in self.workdir.iterdir():
            if item.is_dir():
                if item.name in CHECKPOINT_EXCLUDED_DIRS:
                    continue
                shutil.rmtree(item)
            else:
                item.unlink()

        self._copy_workspace(cp_dir, self.workdir, exclude=CHECKPOINT_EXCLUDED_DIRS)

    @staticmethod
    def _existing_ids(directory: Path) -> set[int]:
        """Return the numeric checkpoint IDs directly under *directory*."""
        if not directory.is_dir():
            return set()
        return {
            int(d.name) for d in directory.iterdir()
            if d.is_dir() and d.name.isdigit()
        }

    def _resolve_checkpoint(self, checkpoint_id: str) -> Path:
        """Find a checkpoint in the registry store, then the legacy directory.

        Raises:
            ValueError: If neither location holds it.
        """
        assert self._checkpoint_dir is not None
        for base in (self._checkpoint_dir, self._legacy_checkpoint_dir):
            candidate = base / checkpoint_id
            if candidate.exists():
                return candidate
        raise ValueError(f"Checkpoint {checkpoint_id} not found")

    def _warn_if_large(self, cp_dir: Path) -> None:
        """Emit :class:`LargeCheckpointWarning` if *cp_dir* is oversized."""
        size = _tree_size(cp_dir)
        if size <= CHECKPOINT_SIZE_WARN_BYTES:
            return
        warnings.warn(
            f"Checkpoint {cp_dir} is {size / 1_048_576:.0f} MB "
            f"(warn threshold {CHECKPOINT_SIZE_WARN_BYTES / 1_048_576:.0f} MB). "
            "Vendored and build directories are already excluded, so this is "
            "real workspace content. Configure `[storage.checkpoints] retain` "
            "to bound how many are kept.",
            LargeCheckpointWarning,
            stacklevel=3,
        )

    def _apply_retention(self, keep_id: str) -> None:
        """Drop the oldest checkpoints past a configured ``retain``.

        Retention is **opt-in**: with no ``[storage.checkpoints] retain`` in the
        config chain this returns immediately and nothing is ever removed, which
        is the project's standing rule (nobody loses work they did not ask to
        discard). Only numbered directories in the registry store are eligible —
        the legacy directory is never touched, and neither is the checkpoint
        just written.

        Args:
            keep_id: The ID created by this call, never a pruning candidate.
        """
        assert self._checkpoint_dir is not None
        retain = store_retention("project-checkpoints", self.workdir).retain
        if retain is None:
            return
        ids = sorted(self._existing_ids(self._checkpoint_dir), reverse=True)
        for stale in ids[retain:]:
            if str(stale) == keep_id:
                continue
            shutil.rmtree(self._checkpoint_dir / str(stale), ignore_errors=True)

    def clone(self) -> LocalEnvironment:
        """Create an independent copy of this environment.

        Returns:
            A new LocalEnvironment with the same files in a temporary directory.
        """
        import tempfile
        clone_dir = Path(tempfile.mkdtemp(prefix="chimera-clone-", dir=self.workdir.parent))
        self._copy_workspace(self.workdir, clone_dir)
        cloned = LocalEnvironment(workdir=str(clone_dir), test_cmd=self.test_cmd, timeout=self.timeout)
        cloned.setup()
        return cloned

    def _copy_workspace(
        self, src: Path, dst: Path, exclude: frozenset[str] | None = None
    ) -> None:
        """Copy a workspace tree, skipping excluded top-level *directories*.

        Args:
            src: Source tree.
            dst: Destination tree, created if absent.
            exclude: Directory names to skip. Defaults to
                :data:`WORKSPACE_STATE_DIRS` — Chimera's own state, which
                :meth:`clone` must not copy because the checkpoint store now
                lives inside it. Checkpointing passes the wider
                :data:`CHECKPOINT_EXCLUDED_DIRS`; cloning keeps the narrow set
                deliberately, since a clone has to remain a *working* copy and
                a workspace stripped of ``.git`` or ``.venv`` cannot run its
                own tests.

        Note:
            Exclusion is matched at **every** depth, not just the top level. The
            checkpoint that motivated this work held 759 MB of
            ``site/node_modules`` — nested one level down, which a top-level-only
            skip would have copied in full. Only directories are matched: a
            *file* named ``build`` is source and is always copied.
        """
        skip = WORKSPACE_STATE_DIRS if exclude is None else exclude
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.is_dir() and item.name in skip:
                continue
            dest_item = dst / item.name
            if item.is_dir():
                shutil.copytree(
                    item, dest_item, dirs_exist_ok=True, ignore=_ignore_dirs(skip)
                )
            else:
                shutil.copy2(item, dest_item)

    def _parse_test_output(self, result: CommandResult) -> TestResult:
        """Parse pytest output to extract pass/fail counts."""
        output = result.stdout + result.stderr
        passed = failed = errors = 0

        # Match pytest summary line: "X passed, Y failed, Z errors"
        match = re.search(r"(\d+) passed", output)
        if match:
            passed = int(match.group(1))
        match = re.search(r"(\d+) failed", output)
        if match:
            failed = int(match.group(1))
        match = re.search(r"(\d+) error", output)
        if match:
            errors = int(match.group(1))

        return TestResult(passed=passed, failed=failed, errors=errors, output=output)
