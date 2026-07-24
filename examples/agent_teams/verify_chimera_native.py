"""Live end-to-end check: a real Chimera agent as a teammate (issue #151).

Sibling of ``verify_integration.py``, which proves the runner + MCP
server against a protocol-faithful *mock* agent on every PR. This one
drives a **real model** through ``chimera code -p``, so it is opt-in:
it needs a configured provider and it spends tokens.

What it proves, end to end:

* ``chimera code -p`` loads the ``chimera-team`` MCP server from the
  project's ``.mcp.json`` and sees the ``team_*`` tools.
* A real model, given the teammate prompt, claims a task, does the
  work, and completes it — the coordination loop closes without a
  human.
* The lead's team policy reaches the teammate and constrains it: run
  with ``--policy read-only`` and the teammate's write is *blocked*,
  with the denial recorded in the team audit.

Run::

    set -a; source .env; set +a
    python examples/agent_teams/verify_chimera_native.py \\
        --model 'glm-5.2[1m]'

    # and the negative case — the posture must actually bite
    python examples/agent_teams/verify_chimera_native.py \\
        --model 'glm-5.2[1m]' --policy read-only --expect-blocked

Exits 0 on PASS, 1 on FAIL. Cleans up its temp dirs either way.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
TEAM_NAME = "verify-native"
TARGET_FILE = "team_hello.txt"
TARGET_TEXT = "hello from the team"
TASK = (
    f"Create a file named {TARGET_FILE} in the current working directory "
    f"whose entire contents are exactly this one line: {TARGET_TEXT}"
)


def _venv_bin(name: str) -> str:
    """Prefer the repo venv's console script, fall back to PATH."""
    candidate = REPO_ROOT / ".venv" / "bin" / name
    return str(candidate) if candidate.exists() else name


def _seed(teams_home: Path, policy: str | None) -> str:
    """Create the team, apply the policy, and add the one task."""
    sys.path.insert(0, str(REPO_ROOT))
    os.environ["CHIMERA_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    os.environ["CHIMERA_TEAMS_HOME"] = str(teams_home)
    from chimera.cli.agent_teams import Team

    team = Team(TEAM_NAME, root=teams_home)
    team.init(policy=policy)
    return team.add_task(TASK, created_by="lead")


def _write_mcp_config(workspace: Path, teams_home: Path, agent_id: str) -> None:
    """Drop the project ``.mcp.json`` that wires the team server in."""
    config = {
        "mcpServers": {
            "chimera-team": {
                "command": _venv_bin("chimera-team-mcp"),
                "env": {
                    "CHIMERA_EXPERIMENTAL_AGENT_TEAMS": "1",
                    "CHIMERA_TEAMS_HOME": str(teams_home),
                    "CHIMERA_TEAM": TEAM_NAME,
                    "CHIMERA_AGENT": agent_id,
                },
            },
        },
    }
    (workspace / ".mcp.json").write_text(json.dumps(config, indent=2))


def _run_teammate(
    workspace: Path,
    teams_home: Path,
    agent_id: str,
    model: str,
    policy: str | None,
    timeout: float,
) -> int:
    """Run one ``chimera-team-run`` pass against a real Chimera agent."""
    inner = (
        f"{shlex.quote(_venv_bin('chimera'))} code -p {{prompt}} "
        f"--model {shlex.quote(model)} --workdir {shlex.quote(str(workspace))}"
    )
    cmd = [
        _venv_bin("chimera-team-run"),
        "--team", TEAM_NAME,
        "--agent", agent_id,
        "--teams-home", str(teams_home),
        "--cmd", inner,
        "--idle-timeout", "20",
        "--task-timeout", str(timeout),
        "--poll-interval", "1",
        "--max-nudges", "0",
        "--policy-runtime", "chimera",
        "--workspace", str(workspace),
    ]
    if policy:
        cmd += ["--policy", policy]
    print("$ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    return subprocess.call(cmd, cwd=str(workspace))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", ""))
    parser.add_argument("--agent", default="chimera-1")
    parser.add_argument("--policy", default=None)
    parser.add_argument("--task-timeout", type=float, default=300.0)
    parser.add_argument(
        "--expect-blocked",
        action="store_true",
        help=(
            "Invert the assertion: the policy must PREVENT the write and "
            "record a denial in the team audit."
        ),
    )
    parser.add_argument("--keep", action="store_true", help="Keep temp dirs.")
    args = parser.parse_args(argv)

    if not args.model:
        print("FAIL: no model (pass --model or set ANTHROPIC_MODEL)")
        return 1

    teams_home = Path(tempfile.mkdtemp(prefix="chimera-verify-teams-"))
    workspace = Path(tempfile.mkdtemp(prefix="chimera-verify-ws-"))
    try:
        task_id = _seed(teams_home, args.policy)
        _write_mcp_config(workspace, teams_home, args.agent)
        print(f"team={TEAM_NAME} task={task_id} policy={args.policy or 'none'}")
        print(f"workspace={workspace}")

        rc = _run_teammate(
            workspace, teams_home, args.agent, args.model,
            args.policy, args.task_timeout,
        )
        print(f"runner exited rc={rc}")

        from chimera.cli.agent_teams import Team, TeamAudit

        team = Team(TEAM_NAME, root=teams_home)
        tasks = team.list_tasks()
        record = next((t for t in tasks if t["id"] == task_id), None)
        audit = TeamAudit(team).entries()
        target = workspace / TARGET_FILE

        print("\n--- team state ---")
        print(json.dumps(record, indent=2))
        print("--- audit ---")
        print(json.dumps(audit, indent=2))
        print(f"--- {TARGET_FILE} exists: {target.exists()} ---")
        if target.exists():
            print(target.read_text())

        if args.expect_blocked:
            denied = [e for e in audit if e.get("decision") == "denied"]
            if target.exists():
                print("FAIL: policy did not prevent the write")
                return 1
            if not denied:
                print("FAIL: no denial recorded in the team audit")
                return 1
            print(f"PASS: write blocked, {len(denied)} denial(s) audited")
            return 0

        if record is None or record.get("status") != "completed":
            print("FAIL: task was not completed")
            return 1
        if record.get("claimed_by") != args.agent:
            print(f"FAIL: task claimed by {record.get('claimed_by')!r}")
            return 1
        if not target.exists():
            print(f"FAIL: {TARGET_FILE} was not created")
            return 1
        if TARGET_TEXT not in target.read_text():
            print(f"FAIL: {TARGET_FILE} does not contain the requested text")
            return 1
        print("PASS: claimed, worked, completed — by a real model over MCP")
        return 0
    finally:
        if args.keep:
            print(f"kept: {teams_home} {workspace}")
        else:
            shutil.rmtree(teams_home, ignore_errors=True)
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
