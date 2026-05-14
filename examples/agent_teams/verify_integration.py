"""Self-contained smoke test for the chimera-team MCP server + runner.

Spawns two chimera-team-run subprocesses against an isolated temp teams
home, each driving examples/agent_teams/fake_agent.py as the "external
coding agent." Verifies that:

  * All seeded tasks reach status=completed.
  * Both agents claimed at least one task (work was actually shared).
  * No task got double-claimed (the file-locked claim_task primitive holds
    under concurrent runners).

Run:
    python examples/agent_teams/verify_integration.py

Exits 0 on PASS, 1 on FAIL. Cleans up the temp teams home in either case.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
FAKE_AGENT = HERE / "fake_agent.py"
TEAM_NAME = "verify-team"
NUM_TASKS = 6
IDLE_TIMEOUT = 3.0
POLL_INTERVAL = 0.2
OVERALL_DEADLINE = 60.0


def _seed_team(teams_home: Path) -> None:
    """Create the team and add NUM_TASKS open tasks using the Team API directly."""
    sys.path.insert(0, str(REPO_ROOT))
    os.environ["CHIMERA_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    os.environ["CHIMERA_TEAMS_HOME"] = str(teams_home)
    from chimera.cli.agent_teams import Team
    team = Team(TEAM_NAME, root=teams_home)
    team.init()
    for i in range(NUM_TASKS):
        team.add_task(f"verify-task-{i}", created_by="lead")


def _spawn_runner(agent_id: str, teams_home: Path, log_path: Path) -> subprocess.Popen:
    """Start one chimera-team-run subprocess with the fake_agent as its external command."""
    py = shlex.quote(sys.executable)
    fake = shlex.quote(str(FAKE_AGENT))
    cmd = [
        sys.executable,
        "-m", "chimera.mcp_servers.teammate_runner",
        "--team", TEAM_NAME,
        "--agent", agent_id,
        "--teams-home", str(teams_home),
        "--idle-timeout", str(IDLE_TIMEOUT),
        "--poll-interval", str(POLL_INTERVAL),
        "--task-timeout", "30",
        # fake_agent ignores its prompt-file arg (identity comes from env),
        # but the runner requires either {prompt} or {prompt_file} in --cmd.
        "--cmd", f"{py} {fake} {{prompt_file}}",
    ]
    env = {
        **os.environ,
        "CHIMERA_EXPERIMENTAL_AGENT_TEAMS": "1",
        "CHIMERA_TEAMS_HOME": str(teams_home),
    }
    log = open(log_path, "w")
    return subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _verify(teams_home: Path) -> tuple[bool, list[str]]:
    """Return (passed, diagnostics)."""
    from chimera.cli.agent_teams import Team
    team = Team(TEAM_NAME, root=teams_home)
    tasks = team.list_tasks()
    diag: list[str] = []

    if len(tasks) != NUM_TASKS:
        diag.append(f"expected {NUM_TASKS} tasks, found {len(tasks)}")

    completed = [t for t in tasks if t.get("status") == "completed"]
    if len(completed) != NUM_TASKS:
        statuses = sorted({str(t.get("status")) for t in tasks})
        diag.append(
            f"expected all {NUM_TASKS} tasks completed, got {len(completed)} "
            f"(statuses present: {statuses})"
        )

    claim_counts: dict[str, int] = {}
    for t in tasks:
        cb = t.get("claimed_by")
        if cb is None:
            diag.append(f"task {t.get('id')} has no claimed_by (status={t.get('status')!r})")
            continue
        if not isinstance(cb, str):
            diag.append(f"task {t.get('id')} has non-string claimed_by={cb!r}")
            continue
        claim_counts[cb] = claim_counts.get(cb, 0) + 1

    if set(claim_counts) != {"agent-1", "agent-2"}:
        diag.append(
            f"expected both agent-1 and agent-2 to claim tasks, "
            f"got claim_counts={claim_counts}"
        )
    else:
        for agent in ("agent-1", "agent-2"):
            if claim_counts.get(agent, 0) < 1:
                diag.append(f"{agent} claimed 0 tasks; expected >=1")

    return (len(diag) == 0), diag


def main() -> int:
    teams_home = Path(tempfile.mkdtemp(prefix="chimera-team-verify-"))
    log_a = teams_home / "agent-1.log"
    log_b = teams_home / "agent-2.log"
    proc_a: subprocess.Popen | None = None
    proc_b: subprocess.Popen | None = None
    try:
        _seed_team(teams_home)

        proc_a = _spawn_runner("agent-1", teams_home, log_a)
        proc_b = _spawn_runner("agent-2", teams_home, log_b)

        deadline = time.time() + OVERALL_DEADLINE
        while time.time() < deadline:
            if proc_a.poll() is not None and proc_b.poll() is not None:
                break
            time.sleep(0.1)

        for name, proc, log_path in (
            ("agent-1", proc_a, log_a),
            ("agent-2", proc_b, log_b),
        ):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                print(
                    f"FAIL: runner for {name} did not exit within "
                    f"{OVERALL_DEADLINE:.0f}s; terminated. log={log_path}",
                    file=sys.stderr,
                )

        passed, diag = _verify(teams_home)
        if passed:
            print(
                f"PASS: chimera-team integration verified — all {NUM_TASKS} tasks "
                f"completed, both agent-1 and agent-2 claimed at least one task, "
                f"no double-claims."
            )
            return 0
        print("FAIL: chimera-team integration verification failed:", file=sys.stderr)
        for line in diag:
            print(f"  - {line}", file=sys.stderr)
        print(f"  log: agent-1 -> {log_a}", file=sys.stderr)
        print(f"  log: agent-2 -> {log_b}", file=sys.stderr)
        try:
            print("--- agent-1 log ---", file=sys.stderr)
            print(log_a.read_text(), file=sys.stderr)
            print("--- agent-2 log ---", file=sys.stderr)
            print(log_b.read_text(), file=sys.stderr)
        except OSError:
            pass
        return 1
    finally:
        for proc in (proc_a, proc_b):
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        shutil.rmtree(teams_home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
