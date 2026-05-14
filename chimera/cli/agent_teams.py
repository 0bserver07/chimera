"""Experimental agent-team coordination (gated by CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1).

Provides a shared on-disk task list plus a per-teammate mailbox, with
file-locked claim semantics so multiple teammates can race to claim the
same task without duplicating work.

State layout (under ``~/.chimera/teams/<team-name>/``)::

    config.json              team metadata (name, default_model, members[])
    task_list.jsonl          append-only task queue (claim/release rewrite in-place)
    mailbox/<agent_id>.jsonl per-agent direct-message inbox

This module is intentionally stdlib-only (``fcntl``, ``os``, ``json``,
``pathlib``, ``secrets``).
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "ENV_FLAG",
    "Team",
    "TeamMailbox",
    "create_team",
    "destroy_team",
    "is_enabled",
    "join_team",
    "leave_team",
    "list_teams",
    "teams_root",
]

ENV_FLAG = "CHIMERA_EXPERIMENTAL_AGENT_TEAMS"


def is_enabled() -> bool:
    """Return True iff the experimental agent-teams flag is set to a truthy value."""
    return os.environ.get(ENV_FLAG, "").strip() in ("1", "true", "True", "yes", "on")


def teams_root() -> Path:
    """Root dir for all team state. Override via ``CHIMERA_TEAMS_HOME``."""
    override = os.environ.get("CHIMERA_TEAMS_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".chimera" / "teams"


@contextmanager
def _flock(path: Path, exclusive: bool = True) -> Iterator[Any]:
    """Cross-platform file lock context manager.

    Falls back to a no-op lock on platforms without ``fcntl`` (e.g. Windows
    without msvcrt support). The lock is held on a sidecar ``.lock`` file so
    we can lock around files that may be replaced atomically.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except ImportError:  # pragma: no cover - windows path
            try:
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            except ImportError:
                pass
        yield fd
    finally:
        try:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover
                try:
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                except ImportError:
                    pass
        finally:
            os.close(fd)


class Team:
    """Persistent team coordinator backed by a directory under ``teams_root()``."""

    def __init__(self, name: str, root: Path | None = None) -> None:
        self.name = name
        self.dir = (root or teams_root()) / name
        self.config_path = self.dir / "config.json"
        self.task_path = self.dir / "task_list.jsonl"
        self.mailbox_dir = self.dir / "mailbox"

    # ---- lifecycle ---------------------------------------------------------

    def init(self, default_model: str = "kimi-k2.6") -> None:
        """Create the team directory and seed config/task/mailbox files."""
        self.mailbox_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self._save_config({
                "name": self.name,
                "default_model": default_model,
                "members": [],
                "created_at": time.time(),
            })
        if not self.task_path.exists():
            self.task_path.touch()

    def exists(self) -> bool:
        return self.config_path.exists()

    # ---- config ------------------------------------------------------------

    def load_config(self) -> dict[str, Any]:
        with _flock(self.config_path, exclusive=False):
            data: dict[str, Any] = json.loads(self.config_path.read_text() or "{}")
            return data

    def _save_config(self, cfg: dict[str, Any]) -> None:
        with _flock(self.config_path):
            tmp = self.config_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
            os.replace(tmp, self.config_path)

    def add_member(self, agent_id: str) -> bool:
        """Idempotently add ``agent_id`` to the member list. Returns True if added."""
        with _flock(self.config_path):
            cfg = json.loads(self.config_path.read_text() or "{}")
            members = list(cfg.get("members", []))
            added = agent_id not in members
            if added:
                members.append(agent_id)
                cfg["members"] = members
                tmp = self.config_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
                os.replace(tmp, self.config_path)
            mb = self.mailbox_dir / f"{agent_id}.jsonl"
            if not mb.exists():
                mb.touch()
            return added

    def remove_member(self, agent_id: str) -> bool:
        with _flock(self.config_path):
            cfg = json.loads(self.config_path.read_text() or "{}")
            members = [m for m in cfg.get("members", []) if m != agent_id]
            removed = len(members) != len(cfg.get("members", []))
            if removed:
                cfg["members"] = members
                tmp = self.config_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
                os.replace(tmp, self.config_path)
            return removed

    # ---- tasks -------------------------------------------------------------

    def add_task(
        self,
        description: str,
        created_by: str = "lead",
        depends_on: list[str] | None = None,
    ) -> str:
        """Append a new task to ``task_list.jsonl`` and return its id.

        Args:
            description: Free-form description of the work item.
            created_by: Agent id of the creator (defaults to ``"lead"``).
            depends_on: Optional list of task ids that must reach status
                ``"completed"`` before this task is claimable. Defaults to
                an empty list (no dependencies).

        Returns:
            The hex task id assigned to the new record.
        """
        task_id = secrets.token_hex(8)
        record = {
            "id": task_id,
            "description": description,
            "created_by": created_by,
            "created_at": time.time(),
            "claimed_by": None,
            "status": "open",
            "result": None,
            "depends_on": list(depends_on or []),
        }
        with _flock(self.task_path):
            with open(self.task_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        return task_id

    def _deps_satisfied(self, rec: dict[str, Any], tasks: list[dict[str, Any]]) -> bool:
        """Return True iff every dep id in ``rec`` exists and is completed.

        A missing dep id (no matching task) is treated as unsatisfied so the
        task stays blocked rather than silently unblocking on a typo.
        """
        deps = rec.get("depends_on") or []
        if not deps:
            return True
        by_id = {t["id"]: t for t in tasks}
        for dep_id in deps:
            dep = by_id.get(dep_id)
            if dep is None or dep.get("status") != "completed":
                return False
        return True

    def is_blocked(self, task_id: str) -> bool:
        """Return True iff ``task_id`` is open but its deps are unresolved."""
        with _flock(self.task_path, exclusive=False):
            tasks = self._read_tasks_unlocked()
            for rec in tasks:
                if rec["id"] == task_id:
                    if rec.get("status") != "open":
                        return False
                    return not self._deps_satisfied(rec, tasks)
            return False

    def _read_tasks_unlocked(self) -> list[dict[str, Any]]:
        if not self.task_path.exists():
            return []
        tasks: dict[str, dict[str, Any]] = {}
        for line in self.task_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            tasks[rec["id"]] = rec
        return list(tasks.values())

    def list_tasks(self, status_filter: str = "all") -> list[dict[str, Any]]:
        """Return tasks matching ``status_filter``.

        Args:
            status_filter: One of ``"all"`` (default), ``"open"`` (open and
                unblocked), ``"open_all"`` (all open including blocked),
                ``"blocked"`` (open with unresolved deps), ``"claimed"``,
                or ``"completed"``. Unknown values return all tasks.

        Returns:
            List of task record dicts matching the filter.
        """
        with _flock(self.task_path, exclusive=False):
            tasks = self._read_tasks_unlocked()

        if status_filter == "all":
            return tasks
        if status_filter == "open":
            return [
                t for t in tasks
                if t.get("status") == "open" and self._deps_satisfied(t, tasks)
            ]
        if status_filter == "open_all":
            return [t for t in tasks if t.get("status") == "open"]
        if status_filter == "blocked":
            return [
                t for t in tasks
                if t.get("status") == "open" and not self._deps_satisfied(t, tasks)
            ]
        if status_filter in {"claimed", "completed"}:
            return [t for t in tasks if t.get("status") == status_filter]
        return tasks

    def _rewrite_tasks(self, tasks: list[dict[str, Any]]) -> None:
        tmp = self.task_path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in tasks:
                f.write(json.dumps(rec) + "\n")
        os.replace(tmp, self.task_path)

    def claim_task(self, task_id: str, agent_id: str) -> bool:
        """Atomically claim a task. Returns True if this caller won the race.

        A task whose ``depends_on`` list still has incomplete entries is NOT
        claimable: this method returns ``False`` for such tasks even if no
        other agent has raced for them. Use :meth:`is_blocked` or the
        ``"blocked"`` filter of :meth:`list_tasks` to surface them.
        """
        with _flock(self.task_path):
            tasks = self._read_tasks_unlocked()
            won = False
            for rec in tasks:
                if (
                    rec["id"] == task_id
                    and rec.get("claimed_by") is None
                    and rec.get("status") == "open"
                    and self._deps_satisfied(rec, tasks)
                ):
                    rec["claimed_by"] = agent_id
                    rec["status"] = "claimed"
                    rec["claimed_at"] = time.time()
                    won = True
                    break
            if won:
                self._rewrite_tasks(tasks)
            return won

    def auto_claim_task(self, agent_id: str) -> str | None:
        """Atomically claim the first unblocked open task. Returns its id or None.

        Tasks are scanned in append order; any task with unresolved
        ``depends_on`` is skipped. Returns the claimed task id, or ``None``
        if no unblocked open task is available.
        """
        with _flock(self.task_path):
            tasks = self._read_tasks_unlocked()
            for rec in tasks:
                if (
                    rec.get("claimed_by") is None
                    and rec.get("status") == "open"
                    and self._deps_satisfied(rec, tasks)
                ):
                    rec["claimed_by"] = agent_id
                    rec["status"] = "claimed"
                    rec["claimed_at"] = time.time()
                    self._rewrite_tasks(tasks)
                    return str(rec["id"])
            return None

    def release_task(self, task_id: str, agent_id: str) -> bool:
        with _flock(self.task_path):
            tasks = self._read_tasks_unlocked()
            changed = False
            for rec in tasks:
                if rec["id"] == task_id and rec.get("claimed_by") == agent_id:
                    rec["claimed_by"] = None
                    rec["status"] = "open"
                    changed = True
                    break
            if changed:
                self._rewrite_tasks(tasks)
            return changed

    def complete_task(self, task_id: str, agent_id: str, result: str = "") -> bool:
        with _flock(self.task_path):
            tasks = self._read_tasks_unlocked()
            changed = False
            for rec in tasks:
                if rec["id"] == task_id and rec.get("claimed_by") == agent_id:
                    rec["status"] = "completed"
                    rec["result"] = result
                    rec["completed_at"] = time.time()
                    changed = True
                    break
            if changed:
                self._rewrite_tasks(tasks)
            return changed

    # ---- teardown ----------------------------------------------------------

    def destroy(self, force: bool = False) -> Path:
        """Delete the entire team directory.

        Args:
            force: When True, delete even if claimed-but-not-completed tasks
                exist. When False, refuse to delete and raise.

        Returns:
            The deleted directory path (for logging).

        Raises:
            ValueError: If any task is in status ``"claimed"`` and ``force``
                is False.
        """
        if not force and self.task_path.exists():
            with _flock(self.task_path, exclusive=False):
                tasks = self._read_tasks_unlocked()
            claimed = [t for t in tasks if t.get("status") == "claimed"]
            if claimed:
                raise ValueError(
                    f"team has {len(claimed)} claimed-but-not-completed tasks; "
                    "pass force=True to delete anyway"
                )
        shutil.rmtree(self.dir, ignore_errors=True)
        return self.dir


class TeamMailbox:
    """Per-agent message inbox stored as ``mailbox/<agent_id>.jsonl``."""

    def __init__(self, team: Team, agent_id: str) -> None:
        self.team = team
        self.agent_id = agent_id
        self.path = team.mailbox_dir / f"{agent_id}.jsonl"

    def send(self, sender: str, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"from": sender, "to": self.agent_id, "content": content, "ts": time.time()}
        with _flock(self.path):
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def recv(self, drain: bool = True) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with _flock(self.path):
            lines = self.path.read_text().splitlines()
            messages = [json.loads(ln) for ln in lines if ln.strip()]
            if drain:
                self.path.write_text("")
            return messages


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def create_team(name: str, members: list[str] | None = None, default_model: str = "kimi-k2.6") -> Team:
    """Create a team and pre-populate its initial member list."""
    team = Team(name)
    team.init(default_model=default_model)
    for agent_id in members or []:
        team.add_member(agent_id)
    return team


def join_team(name: str, agent_id: str) -> Team:
    team = Team(name)
    if not team.exists():
        team.init()
    team.add_member(agent_id)
    return team


def leave_team(name: str, agent_id: str) -> Team:
    team = Team(name)
    if team.exists():
        team.remove_member(agent_id)
    return team


def destroy_team(name: str, root: Path | None = None, force: bool = False) -> Path:
    """Delete a team directory and all of its state.

    Args:
        name: Team name (directory under ``teams_root()``).
        root: Optional override for the teams root dir; defaults to
            :func:`teams_root`.
        force: When True, delete even if claimed-but-not-completed tasks
            exist.

    Returns:
        The deleted directory path (whether or not it existed beforehand).

    Raises:
        ValueError: If any task is in status ``"claimed"`` and ``force`` is
            False.
    """
    return Team(name, root=root).destroy(force=force)


def list_teams(root: Path | None = None) -> list[dict[str, Any]]:
    """Enumerate every team directory under ``root``.

    Args:
        root: Optional override for the teams root dir; defaults to
            :func:`teams_root`.

    Returns:
        A list of dicts (sorted by ``name``) with keys ``name``,
        ``members`` (list[str]), ``tasks_total``, ``tasks_open``,
        ``tasks_claimed``, ``tasks_completed``, and ``dir`` (str path).
        Returns ``[]`` when the root directory does not exist.
    """
    base = root or teams_root()
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if not (child / "config.json").is_file():
            continue
        team = Team(child.name, root=base)
        cfg = team.load_config()
        tasks = team.list_tasks()
        out.append({
            "name": child.name,
            "members": list(cfg.get("members", [])),
            "tasks_total": len(tasks),
            "tasks_open": sum(1 for t in tasks if t.get("status") == "open"),
            "tasks_claimed": sum(1 for t in tasks if t.get("status") == "claimed"),
            "tasks_completed": sum(1 for t in tasks if t.get("status") == "completed"),
            "dir": str(child),
        })
    return out
