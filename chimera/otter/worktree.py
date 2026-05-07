"""``chimera otter worktree {create|list|remove}`` — git worktree isolation.

Wires :class:`chimera.env.git_env.GitEnvironment` (and the underlying
``git worktree`` subcommands) so a long-running otter session can branch
into an isolated working copy without disturbing the user's current
checkout.

Trademark hygiene: this module never names the upstream open-source
coding agent in user-visible source. The "worktree" concept is plain
git terminology.

Layout
------

Worktrees live under ``~/.chimera/worktrees/<name>/`` by default. Each
entry is a real ``git worktree add`` of the user's repo, so commits made
inside the worktree resolve against the same object database. The
manifest at ``~/.chimera/worktrees/index.json`` tracks every worktree
otter created so ``otter worktree list`` doesn't have to shell out to
``git worktree list`` for the cross-process view.

Subcommands
-----------

* ``otter worktree create <name> [--branch BRANCH] [--repo PATH]`` —
  create ``~/.chimera/worktrees/<name>`` from ``HEAD`` (or
  ``--branch``). Records to the manifest. Idempotent: re-running with
  the same name updates the manifest entry but won't re-add an existing
  worktree.
* ``otter worktree list [--json]`` — print the manifest.
* ``otter worktree remove <name> [--force]`` — ``git worktree remove``
  the directory and drop the manifest entry. ``--force`` skips the
  "uncommitted changes" guard.

All disk paths are honored via :func:`Path.home` so test fixtures can
override the manifest root with ``monkeypatch.setenv("HOME", ...)``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "WorktreeRecord",
    "default_worktree_root",
    "manifest_path",
    "load_manifest",
    "save_manifest",
    "create_worktree",
    "remove_worktree",
    "list_worktrees",
    "dispatch_worktree",
]


_DEFAULT_BRANCH_PREFIX = "otter/"


@dataclass
class WorktreeRecord:
    """One entry in the worktree manifest.

    Attributes:
        name: User-supplied label (matches the directory under the root).
        path: Absolute filesystem path to the worktree.
        branch: Branch checked out in the worktree.
        repo: Absolute path to the source repo the worktree was cut from.
        created_at: ISO-8601 UTC timestamp of creation.
    """

    name: str
    path: str
    branch: str
    repo: str
    created_at: str


def default_worktree_root() -> Path:
    """Return ``~/.chimera/worktrees/`` honoring the current ``Path.home()``."""
    return Path.home() / ".chimera" / "worktrees"


def manifest_path(root: Path | None = None) -> Path:
    """Return the manifest file path under ``root``."""
    return (root or default_worktree_root()) / "index.json"


def load_manifest(root: Path | None = None) -> list[WorktreeRecord]:
    """Read the manifest from disk; return ``[]`` when the file is missing.

    Malformed entries are silently dropped so a hand-edit can't poison
    every later command — the calling subcommand just sees a shorter list.
    """
    p = manifest_path(root)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[WorktreeRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                WorktreeRecord(
                    name=str(item["name"]),
                    path=str(item["path"]),
                    branch=str(item["branch"]),
                    repo=str(item["repo"]),
                    created_at=str(item.get("created_at", "")),
                )
            )
        except KeyError:
            continue
    return out


def save_manifest(records: list[WorktreeRecord], root: Path | None = None) -> None:
    """Write the manifest atomically. Creates the parent dir on demand."""
    target = manifest_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([asdict(r) for r in records], indent=2, sort_keys=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(target)


def _utc_iso8601() -> str:
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_repo(repo: str | Path | None) -> Path:
    """Return an absolute path; default to ``Path.cwd()`` when unset."""
    return (Path(repo) if repo else Path.cwd()).resolve()


def create_worktree(
    name: str,
    *,
    branch: str | None = None,
    repo: str | Path | None = None,
    root: Path | None = None,
) -> WorktreeRecord:
    """Create a git worktree under ``~/.chimera/worktrees/<name>``.

    Args:
        name: Friendly label and directory name.
        branch: Branch to check out. Defaults to ``otter/<name>``; an
            existing branch with that name is reused, otherwise it's
            created from ``HEAD``.
        repo: Source repository path. Defaults to ``cwd``.
        root: Override the worktree root (``~/.chimera/worktrees``).

    Returns:
        The recorded :class:`WorktreeRecord`.

    Raises:
        ValueError: When ``name`` is empty or contains path separators.
        FileNotFoundError: When ``repo`` is not a git repo.
        RuntimeError: When ``git worktree add`` fails.
    """
    if not name or "/" in name or ".." in name:
        raise ValueError(f"invalid worktree name: {name!r}")
    repo_path = _resolve_repo(repo)
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(
            f"{repo_path} is not a git repository (no .git dir/file)"
        )
    branch_name = branch or f"{_DEFAULT_BRANCH_PREFIX}{name}"
    target_root = root or default_worktree_root()
    target_root.mkdir(parents=True, exist_ok=True)
    worktree_path = target_root / name

    # Check whether the branch already exists; reuse if so, else create.
    rc = _git(repo_path, "rev-parse", "--verify", f"refs/heads/{branch_name}")
    if rc.returncode == 0:
        wt_args = ["worktree", "add", str(worktree_path), branch_name]
    else:
        wt_args = ["worktree", "add", "-b", branch_name, str(worktree_path)]

    if not worktree_path.exists():
        out = _git(repo_path, *wt_args)
        if out.returncode != 0:
            raise RuntimeError(
                f"git worktree add failed: {out.stderr.strip() or out.stdout.strip()}"
            )

    record = WorktreeRecord(
        name=name,
        path=str(worktree_path.resolve()),
        branch=branch_name,
        repo=str(repo_path),
        created_at=_utc_iso8601(),
    )
    records = [r for r in load_manifest(root) if r.name != name]
    records.append(record)
    save_manifest(records, root)
    return record


def remove_worktree(
    name: str,
    *,
    force: bool = False,
    root: Path | None = None,
) -> bool:
    """Remove a worktree directory and drop its manifest entry.

    Args:
        name: Manifest entry to remove.
        force: When ``True`` pass ``--force`` to ``git worktree remove``
            (allows removal even with uncommitted changes).
        root: Override the worktree root.

    Returns:
        ``True`` when the manifest entry was removed; ``False`` when
        ``name`` was not in the manifest.

    Raises:
        RuntimeError: When ``git worktree remove`` fails *and* the
            worktree still exists. A missing worktree is treated as a
            successful removal so a half-deleted entry can always be
            cleaned up from the manifest.
    """
    records = load_manifest(root)
    record = next((r for r in records if r.name == name), None)
    if record is None:
        return False
    wt_path = Path(record.path)
    if wt_path.exists():
        repo = Path(record.repo)
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(wt_path))
        out = _git(repo, *args)
        if out.returncode != 0 and wt_path.exists():
            raise RuntimeError(
                f"git worktree remove failed: {out.stderr.strip() or out.stdout.strip()}"
            )
    save_manifest([r for r in records if r.name != name], root)
    return True


def list_worktrees(root: Path | None = None) -> list[WorktreeRecord]:
    """Return every worktree currently tracked in the manifest."""
    return load_manifest(root)


# ---------------------------------------------------------------------------
# argparse dispatcher
# ---------------------------------------------------------------------------


def dispatch_worktree(args: argparse.Namespace) -> int:
    """Wire ``chimera otter worktree {create|list|remove}``.

    Reads ``args.sub_action`` and ``args.sub_target`` (the positional
    slots already shared with sessions/agents/share) plus the optional
    ``--worktree-branch`` / ``--worktree-repo`` / ``--worktree-force``
    flags wired by :func:`chimera.otter.cli.add_arguments`.
    """
    action = (getattr(args, "sub_action", None) or "list").lower()
    target = getattr(args, "sub_target", None)
    json_out = bool(getattr(args, "sessions_json", False) or getattr(args, "worktree_json", False))
    repo = getattr(args, "worktree_repo", None) or getattr(args, "cwd", None)
    branch = getattr(args, "worktree_branch", None)
    force = bool(getattr(args, "worktree_force", False))

    if action == "list":
        records = list_worktrees()
        if json_out:
            print(json.dumps([asdict(r) for r in records], indent=2, sort_keys=True))
            return 0
        if not records:
            print("no worktrees")
            return 0
        for r in records:
            print(f"{r.name}\t{r.branch}\t{r.path}")
        return 0

    if action == "create":
        if not target:
            print("error: 'worktree create' requires <NAME>", file=sys.stderr)
            return 2
        try:
            record = create_worktree(
                target, branch=branch, repo=repo,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if json_out:
            print(json.dumps(asdict(record), indent=2, sort_keys=True))
        else:
            print(f"created worktree {record.name} at {record.path}")
        return 0

    if action == "remove":
        if not target:
            print("error: 'worktree remove' requires <NAME>", file=sys.stderr)
            return 2
        try:
            removed = remove_worktree(target, force=force)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not removed:
            print(f"no such worktree: {target}", file=sys.stderr)
            return 1
        print(f"removed worktree {target}")
        return 0

    print(
        f"error: unknown 'worktree' action: {action!r} "
        "(supported: create, list, remove)",
        file=sys.stderr,
    )
    return 2


# Re-export of internals for tests.
def _reset_for_tests(root: Path | None = None) -> None:
    """Best-effort manifest wipe. Used by test fixtures."""
    p = manifest_path(root)
    if p.exists():
        p.unlink()


__all__.append("_reset_for_tests")
