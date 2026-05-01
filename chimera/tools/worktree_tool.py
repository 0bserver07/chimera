"""Git worktree enter/exit tools.

Thin wrappers over ``git worktree`` so an agent can isolate work onto a
parallel checkout without leaving the current branch dirty.  Mirrors the
CC ``EnterWorktree`` / ``ExitWorktree`` tool pair.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.hooks.emitter import get_global_emitter
from chimera.hooks.events import HookEvent
from chimera.types import ToolResult


def _emit_worktree_event(event: HookEvent, **kwargs: Any) -> None:
    """Best-effort hook emission. Worktree work must never fail on hooks."""
    try:
        emitter = get_global_emitter()
        if emitter.active:
            emitter.emit_sync(event, **kwargs)
    except Exception:
        pass


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand and capture text output."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )


def _is_git_repo(cwd: str | None = None) -> bool:
    proc = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


class EnterWorktreeTool(BaseTool):
    """Create (or attach to) a git worktree on a fresh branch.

    The worktree is placed at ``<repo_root>/../worktrees/<name>`` so it
    sits next to the main checkout without polluting it.
    """

    name = "enter_worktree"
    description = (
        "Create a new git worktree on a fresh branch. Returns the worktree path. "
        "Useful to isolate parallel work without disturbing the main checkout."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Branch name (also used as worktree directory leaf).",
            },
            "base_branch": {
                "type": "string",
                "description": "Branch/commit to base the new worktree on. Defaults to HEAD.",
            },
        },
        "required": ["name"],
    }
    is_destructive = False

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        name = args["name"]
        base = args.get("base_branch") or "HEAD"
        if not _is_git_repo():
            return ToolResult(output="", error="Not inside a git repository")

        # Anchor the worktree relative to the repo's superproject root so the
        # path is predictable regardless of the agent's current cwd.
        top = _git(["rev-parse", "--show-toplevel"]).stdout.strip()
        wt_root = Path(top).parent / "worktrees"
        wt_root.mkdir(parents=True, exist_ok=True)
        wt_path = wt_root / name

        proc = _git(["worktree", "add", str(wt_path), "-b", name, base])
        if proc.returncode != 0:
            return ToolResult(output="", error=f"git worktree add failed: {proc.stderr.strip()}")
        _emit_worktree_event(
            HookEvent.WORKTREE_CREATE,
            tool_name="enter_worktree",
            tool_input={"path": str(wt_path), "branch": name, "base": base},
        )
        return ToolResult(
            output=str(wt_path),
            metadata={"worktree_path": str(wt_path), "branch": name},
        )


class ExitWorktreeTool(BaseTool):
    """Tear down (or merge back) a git worktree.

    ``action`` semantics:
        ``remove``   -- ``git worktree remove`` (refuses if dirty).
        ``merge``    -- merge the worktree's branch into HEAD of the main
                        repo, then remove the worktree.
        ``abandon``  -- forcibly remove the worktree and delete its branch.
    """

    name = "exit_worktree"
    description = (
        "Remove, merge, or abandon a git worktree previously created via enter_worktree."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "worktree_path": {
                "type": "string",
                "description": "Filesystem path of the worktree to operate on.",
            },
            "action": {
                "type": "string",
                "enum": ["remove", "merge", "abandon"],
                "description": "Cleanup action.",
            },
        },
        "required": ["worktree_path", "action"],
    }
    is_destructive = True

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        wt = args["worktree_path"]
        action = args["action"]
        if not os.path.isdir(wt):
            return ToolResult(output="", error=f"Worktree path not found: {wt}")

        # Safety: refuse to drop a worktree with uncommitted changes unless the
        # caller explicitly chose 'abandon' (which is the documented escape hatch).
        if action != "abandon":
            status = _git(["status", "--porcelain"], cwd=wt)
            if status.stdout.strip():
                return ToolResult(
                    output="",
                    error=(
                        f"Worktree has uncommitted changes; refusing to {action}. "
                        "Commit/stash them, or use action='abandon'."
                    ),
                )

        if action == "remove":
            proc = _git(["worktree", "remove", wt])
            if proc.returncode != 0:
                return ToolResult(output="", error=f"remove failed: {proc.stderr.strip()}")
            _emit_worktree_event(
                HookEvent.WORKTREE_REMOVE,
                tool_name="exit_worktree",
                tool_input={"path": wt, "action": "remove"},
            )
            return ToolResult(output=f"Removed worktree {wt}")

        if action == "merge":
            branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt).stdout.strip()
            merge = _git(["merge", "--no-ff", branch])
            if merge.returncode != 0:
                return ToolResult(output="", error=f"merge failed: {merge.stderr.strip()}")
            rm = _git(["worktree", "remove", wt])
            if rm.returncode != 0:
                return ToolResult(
                    output=merge.stdout,
                    error=f"merged but cleanup failed: {rm.stderr.strip()}",
                )
            _emit_worktree_event(
                HookEvent.WORKTREE_REMOVE,
                tool_name="exit_worktree",
                tool_input={"path": wt, "action": "merge", "branch": branch},
            )
            return ToolResult(output=f"Merged {branch} and removed worktree")

        # action == "abandon"
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt).stdout.strip()
        rm = _git(["worktree", "remove", "--force", wt])
        if rm.returncode != 0:
            return ToolResult(output="", error=f"abandon remove failed: {rm.stderr.strip()}")
        if branch and branch != "HEAD":
            _git(["branch", "-D", branch])  # best-effort; some branches may not exist
        _emit_worktree_event(
            HookEvent.WORKTREE_REMOVE,
            tool_name="exit_worktree",
            tool_input={"path": wt, "action": "abandon", "branch": branch},
        )
        return ToolResult(output=f"Abandoned worktree {wt}")
