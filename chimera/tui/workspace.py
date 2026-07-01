"""Per-lane workspace isolation for the multiplexer (spec §6.2, R-ISO-1).

N coding agents racing the same task MUST NOT share a mutable tree, or their
file writes collide and the comparison is meaningless. Each lane gets its own
working directory here.

Strategies
----------
- ``worktree`` — a real ``git worktree`` on a fresh branch from the source's
  ``HEAD``. Fast (no file copy), gives a clean per-lane diff, and is the default
  for a git repo. Lanes start from the committed HEAD, not the dirty tree.
- ``copy`` — a full ``shutil.copytree`` of the source into a temp dir. Works for
  any directory; heavier. The automatic fallback for non-git sources.
- ``inplace`` — no isolation; every lane shares the source. Unsafe for
  file-writing agents; only for read-only / planning cohorts. Never the default.

``strategy="auto"`` (the default) picks ``worktree`` for a git repo, else
``copy``. Provisioned workspaces are torn down (worktree removed, copy deleted)
via :meth:`LaneWorkspace.cleanup` or the :class:`WorkspaceSet` context manager.
Capture any diffs *before* teardown — cleanup removes the tree.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "LaneWorkspace",
    "WorkspaceSet",
    "WorkspaceError",
    "provision_workspaces",
    "is_git_repo",
    "resolve_strategy",
    "apply_diff",
]

_SNAPSHOT_MAX_BYTES = 1_000_000
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


class WorkspaceError(RuntimeError):
    """Raised when a workspace cannot be provisioned or torn down."""


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def is_git_repo(path: Path) -> bool:
    """True if *path* is inside a git working tree."""
    try:
        out = _git(Path(path), "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def _git_head(path: Path) -> str | None:
    out = _git(path, "rev-parse", "HEAD")
    return out.stdout.strip() if out.returncode == 0 else None


def resolve_strategy(source: Path, strategy: str) -> str:
    """Resolve ``"auto"`` to a concrete strategy; validate an explicit one."""
    source = Path(source)
    if strategy == "auto":
        return "worktree" if is_git_repo(source) else "copy"
    if strategy == "worktree" and not is_git_repo(source):
        raise WorkspaceError(
            f"worktree isolation needs a git repo, but {source} is not one "
            f"(use --isolation copy)"
        )
    if strategy not in ("worktree", "copy", "inplace"):
        raise WorkspaceError(f"unknown isolation strategy: {strategy!r}")
    return strategy


def _snapshot(root: Path) -> dict[str, str]:
    """sha256 of every text-ish file under *root* (for a non-git diff)."""
    snap: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            if p.stat().st_size > _SNAPSHOT_MAX_BYTES:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        snap[str(rel)] = hashlib.sha256(data).hexdigest()
    return snap


@dataclass
class LaneWorkspace:
    """One lane's isolated working directory."""

    lane_id: str
    path: Path
    strategy: str
    source: Path
    base_commit: str | None = None
    branch: str | None = None
    _snapshot: dict[str, str] | None = field(default=None, repr=False)

    def diff(self) -> str:
        """Best-effort unified diff of the changes the lane produced.

        Git-backed workspaces (worktree, or a copy that still has ``.git``) get a
        real ``git diff`` against the base commit, including untracked files
        (via intent-to-add). Non-git copies fall back to a changed-files summary
        computed from a snapshot taken at provisioning time.
        """
        if (self.path / ".git").exists() or self.strategy == "worktree":
            base = self.base_commit or "HEAD"
            _git(self.path, "add", "-A", "-N")  # surface untracked in the diff
            out = _git(self.path, "diff", base)
            return out.stdout
        return self._snapshot_diff()

    def _snapshot_diff(self) -> str:
        before = self._snapshot or {}
        after = _snapshot(self.path)
        lines: list[str] = []
        for rel in sorted(set(before) | set(after)):
            b, a = before.get(rel), after.get(rel)
            if b == a:
                continue
            if b is None:
                lines.append(f"added:    {rel}")
            elif a is None:
                lines.append(f"removed:  {rel}")
            else:
                lines.append(f"modified: {rel}")
        return "\n".join(lines)

    def cleanup(self) -> None:
        """Remove this lane's workspace (worktree pruned, copy deleted)."""
        if self.strategy == "worktree":
            _git(self.source, "worktree", "remove", "--force", str(self.path))
            if self.branch:
                _git(self.source, "branch", "-D", self.branch)
        elif self.strategy == "copy":
            shutil.rmtree(self.path, ignore_errors=True)
        # inplace: the source is the user's tree — never touch it.


@dataclass
class WorkspaceSet:
    """A provisioned set of lane workspaces, cleaned up together."""

    root: Path
    workspaces: list[LaneWorkspace]
    strategy: str
    owns_root: bool = True

    def __iter__(self) -> Iterator[LaneWorkspace]:
        return iter(self.workspaces)

    def __len__(self) -> int:
        return len(self.workspaces)

    def __getitem__(self, index: int) -> LaneWorkspace:
        return self.workspaces[index]

    def cleanup_all(self) -> None:
        """Tear down every workspace, then the temp root if we own it."""
        for ws in self.workspaces:
            try:
                ws.cleanup()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
        if self.owns_root and self.strategy != "inplace":
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> WorkspaceSet:
        return self

    def __exit__(self, *args: object) -> None:
        self.cleanup_all()


def apply_diff(worktree: Path, diff_text: str) -> bool:
    """Apply a saved unified diff to *worktree* (best-effort).

    Used by cohort resume to restore a lane's produced changes on top of a
    freshly provisioned workspace. Returns ``True`` on success (an empty diff is
    a no-op success); ``False`` if the patch does not apply cleanly.
    """
    if not diff_text.strip():
        return True
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(worktree), input=diff_text, capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


def provision_workspaces(
    source: str | Path,
    lane_ids: list[str],
    strategy: str = "auto",
    root: str | Path | None = None,
    base_commit: str | None = None,
) -> WorkspaceSet:
    """Provision one isolated workspace per lane id.

    Args:
        source: The project directory to isolate from.
        lane_ids: One id per lane; used as the workspace subdirectory name.
        strategy: ``auto`` (default), ``worktree``, ``copy``, or ``inplace``.
        root: Parent dir for the workspaces; a temp dir is created if omitted.

    Returns:
        A :class:`WorkspaceSet`. On partial failure, any workspaces already
        created are rolled back before the error propagates.

    Raises:
        WorkspaceError: If a strategy is invalid for the source, or provisioning
            fails.
    """
    source = Path(source).resolve()
    strategy = resolve_strategy(source, strategy)
    if root is None:
        root_path = Path(tempfile.mkdtemp(prefix="chimera-mux-"))
        owns_root = True
    else:
        root_path = Path(root)
        owns_root = False
    root_path.mkdir(parents=True, exist_ok=True)
    base = base_commit or (_git_head(source) if is_git_repo(source) else None)

    created: list[LaneWorkspace] = []

    def _rollback() -> None:
        for ws in created:
            try:
                ws.cleanup()
            except Exception:  # noqa: BLE001
                pass
        if owns_root:
            shutil.rmtree(root_path, ignore_errors=True)

    for lane_id in lane_ids:
        dest = root_path / lane_id
        if strategy == "worktree":
            branch = f"chimera-lane-{lane_id}-{uuid.uuid4().hex[:6]}"
            out = _git(source, "worktree", "add", "-b", branch, str(dest), base or "HEAD")
            if out.returncode != 0:
                _rollback()
                raise WorkspaceError(
                    f"git worktree add failed for lane {lane_id!r}: "
                    f"{out.stderr.strip() or out.stdout.strip()}"
                )
            created.append(LaneWorkspace(lane_id, dest, "worktree", source, base, branch))
        elif strategy == "copy":
            try:
                shutil.copytree(source, dest, symlinks=True, dirs_exist_ok=True)
            except OSError as exc:
                _rollback()
                raise WorkspaceError(f"copy isolation failed for lane {lane_id!r}: {exc}") from exc
            snap = None if (dest / ".git").exists() else _snapshot(dest)
            created.append(LaneWorkspace(lane_id, dest, "copy", source, base, None, snap))
        else:  # inplace
            snap = None if is_git_repo(source) else _snapshot(source)
            created.append(LaneWorkspace(lane_id, source, "inplace", source, base, None, snap))

    return WorkspaceSet(root=root_path, workspaces=created, strategy=strategy, owns_root=owns_root)
