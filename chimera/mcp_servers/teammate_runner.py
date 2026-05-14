#!/usr/bin/env python3
"""``chimera-team-run`` — drive any MCP-capable coding agent as a teammate.

The runner is a small polling loop that does one job:

* Watch the team's task list on disk.
* When an open task appears, spawn the configured external coding agent
  command with a teammate prompt that tells it exactly which team_*
  tools to call.
* When the subprocess exits, check the team state, and loop.
* Exit cleanly after the team has been idle for ``--idle-timeout`` seconds.

The runner is intentionally **agent-agnostic**: you supply a command
template (``--cmd``) with two placeholders:

* ``{prompt}`` — the teammate workflow prompt (single line, escaped)
* ``{prompt_file}`` — a path to a tempfile containing the multi-line prompt
  (handy for agents that take a prompt file rather than a CLI arg)

The runner exports ``CHIMERA_TEAM``, ``CHIMERA_AGENT``, and
``CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1`` into the subprocess environment.
The chimera-team MCP server's env-var fallback picks these up, so your
``mcp.json`` doesn't need per-team config.

Sample::

    # one-time: add to ~/.codex/mcp.json (Codex CLI)
    {
      "mcpServers": {
        "chimera-team": {
          "command": "chimera-team-mcp",
          "env": {"CHIMERA_EXPERIMENTAL_AGENT_TEAMS": "1"}
        }
      }
    }

    # per-session: run the runner
    chimera-team-run --team review-pr --agent codex-1 \\
        --cmd 'codex exec --prompt-file {prompt_file}'

    chimera-team-run --team review-pr --agent opencode-1 \\
        --cmd 'opencode run "{prompt}"'
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TextIO

from chimera.cli.agent_teams import ENV_FLAG, Team, TeamMailbox

__all__ = ["TEAMMATE_PROMPT", "run_loop", "main"]


TEAMMATE_PROMPT = """\
You are teammate '{agent_id}' in team '{team}'.

You have access to a `chimera-team` MCP server with the following tools:
  team_recv_messages  — drain your mailbox (call FIRST)
  team_claim_task     — claim the next open task ({{}} = auto, or pass task_id)
  team_complete_task  — mark a claimed task complete
  team_release_task   — release a claimed task back to the pool
  team_send_message   — DM another teammate
  team_list_members   — see who else is on the team
  team_list_tasks     — list tasks (filter: open|claimed|completed|mine)
  team_status         — team summary

Workflow for THIS run — work exactly ONE task and STOP:

  1. Call `team_recv_messages` first to drain any messages waiting for you.
  2. Call `team_claim_task` (no args) to atomically claim the next open task.
     If the result is `{{"claimed": false, ...}}`, exit immediately —
     there is no work for you.
  3. Read the claimed task's description carefully and do the work using
     your normal tools (file editing, bash, etc.).
  4. When the work is genuinely done, call `team_complete_task` with the
     task_id and a brief result summary describing what changed.
  5. Optionally call `team_send_message` to inform another teammate of
     anything they need to know.
  6. Stop. Do not pick up another task — the runner will spawn a fresh
     session for the next one.

Important:
* Only call `team_complete_task` when the work is genuinely done. If you
  can't finish, call `team_release_task` so another teammate can pick it up.
* Do not call `team_add_task` unless the lead asked you to. Stay in scope.
"""


def _run_with_timeout(
    cmd: str, env: dict[str, str], timeout: float, log: TextIO,
) -> int:
    """Run *cmd* (shell), enforcing *timeout* with SIGTERM-then-escalate.

    Returns the process exit code, or ``-1`` if it had to be terminated.
    """
    proc = subprocess.Popen(cmd, shell=True, env=env)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"chimera-team-run: external agent exceeded task_timeout="
            f"{timeout:.0f}s; sending SIGTERM.",
            file=log,
        )
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(
                "chimera-team-run: agent did not exit after SIGTERM; "
                "escalating to SIGKILL.",
                file=log,
            )
            proc.kill()
            proc.wait(timeout=5)
        return -1


def run_loop(
    team_name: str,
    agent_id: str,
    cmd_template: str,
    teams_root: Path | None = None,
    idle_timeout: float = 60.0,
    task_timeout: float = 600.0,
    poll_interval: float = 2.0,
    max_nudges: int = 1,
    log: TextIO = sys.stderr,
) -> int:
    """Poll-and-spawn loop.

    Args:
        team_name: Team to attach to (created if absent).
        agent_id: This teammate's id.
        cmd_template: Shell command with ``{prompt}`` or ``{prompt_file}``
            placeholders.
        teams_root: Override for ``~/.chimera/teams``.
        idle_timeout: Exit after this many seconds with no *progress*
            (no task transitioning to a new state). This covers both
            "no open tasks" and "agent keeps failing to make progress".
        task_timeout: Kill the external subprocess after this many seconds.
        poll_interval: How often (seconds) to check the task list.
        max_nudges: Number of consecutive no-progress nudges to send for a
            stuck claim before the runner force-releases the task. Defaults
            to 1 (one nudge, then release on the next stuck iteration).
        log: Stream to write status messages to.

    Returns:
        Exit code (0 on idle-timeout shutdown).
    """
    team = Team(team_name, root=teams_root)
    team.init()
    team.add_member(agent_id)
    prompt = TEAMMATE_PROMPT.format(agent_id=agent_id, team=team_name)
    mailbox = TeamMailbox(team, agent_id)

    base_env = {
        **os.environ,
        "CHIMERA_TEAM": team_name,
        "CHIMERA_AGENT": agent_id,
        ENV_FLAG: "1",
    }
    if teams_root is not None:
        base_env["CHIMERA_TEAMS_HOME"] = str(teams_root)

    def _my_completed() -> int:
        return sum(
            1 for t in team.list_tasks()
            if t.get("claimed_by") == agent_id and t.get("status") == "completed"
        )

    # Per-task counter of consecutive nudges we've sent for a stuck claim.
    # Resets to 0 (entry removed) when the task transitions out of "claimed".
    nudge_counts: dict[str, int] = {}
    # Tasks the runner has force-released back to the pool. We won't spawn
    # this agent again purely on the strength of a released-by-us task being
    # open again — that would create a claim/release cycle. The set is
    # cleared per-task when something else (another teammate or the lead)
    # transitions the task out of "open".
    released_by_runner: set[str] = set()

    def _handle_stuck_claims(current_tasks: list[dict[str, object]]) -> bool:
        """Nudge or force-release tasks this agent claimed but didn't finish.

        Sends at most one nudge per stuck task per call. Force-releases
        when the per-task nudge count has reached ``max_nudges``.

        Returns True iff the runner force-released at least one task.
        """
        # Reset nudge counters for any tracked task that has moved out of
        # the "claimed" state (completed or released). A task that becomes
        # unstuck (or is released and then re-claimed in a later round)
        # starts counting fresh.
        status_by_id = {t["id"]: t.get("status") for t in current_tasks}
        for tid in list(nudge_counts):
            if status_by_id.get(tid) != "claimed":
                del nudge_counts[tid]

        # Identify stuck claims: tasks this agent has claimed but not
        # completed. The agent exited without releasing them.
        stuck = [
            t for t in current_tasks
            if t.get("status") == "claimed" and t.get("claimed_by") == agent_id
        ]

        released_any = False
        for t in stuck:
            tid = str(t["id"])
            count = nudge_counts.get(tid, 0)
            description = str(t.get("description", ""))
            if count >= max_nudges:
                released = team.release_task(tid, agent_id)
                if released:
                    print(
                        f"chimera-team-run: task {tid} stuck in 'claimed' "
                        f"after {count} nudge(s); runner force-released it.",
                        file=log,
                    )
                    nudge_counts.pop(tid, None)
                    released_by_runner.add(tid)
                    released_any = True
            else:
                mailbox.send(
                    sender="chimera-team-run",
                    content=(
                        f"You claimed task {tid} ('{description[:80]}') but did "
                        f"not complete it. Either call team_complete_task or "
                        f"team_release_task."
                    ),
                )
                nudge_counts[tid] = count + 1
                print(
                    f"chimera-team-run: nudged {agent_id} about stuck task "
                    f"{tid} (nudge {count + 1}/{max_nudges}).",
                    file=log,
                )
        return released_any

    last_progress = time.time()

    while True:
        # Idle = no team-state progress for idle_timeout, regardless of
        # whether tasks remain open. A stuck agent that never completes
        # anything still drains to this exit rather than spinning forever.
        if time.time() - last_progress > idle_timeout:
            print(
                f"chimera-team-run: no progress for {idle_timeout:.0f}s "
                f"({_my_completed()} tasks completed by {agent_id}); exiting.",
                file=log,
            )
            return 0

        tasks = team.list_tasks()
        open_tasks = [t for t in tasks if t.get("status") == "open"]
        # Tasks we released earlier and that are now back in the pool —
        # we don't count those as "open work for me" to avoid an infinite
        # claim/release cycle with the same misbehaving agent.
        if released_by_runner:
            # Drop ids that have transitioned out of "open" (someone else
            # acted on them) so the next round can reconsider.
            status_by_id = {t["id"]: t.get("status") for t in tasks}
            for tid in list(released_by_runner):
                if status_by_id.get(tid) != "open":
                    released_by_runner.discard(tid)
        spawnable_open = [t for t in open_tasks if t["id"] not in released_by_runner]
        my_stuck = [
            t for t in tasks
            if t.get("status") == "claimed" and t.get("claimed_by") == agent_id
        ]

        # Spawn when there's fresh open work OR we have stuck claims of
        # ours (re-spawn so the agent can read its mailbox and act).
        if not spawnable_open and not my_stuck:
            time.sleep(poll_interval)
            continue

        # Snapshot task ids before the spawn — we use this to detect
        # whether the agent actually made progress.
        before = {t["id"]: t.get("status") for t in tasks}

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            cmd = cmd_template.replace("{prompt_file}", prompt_file).replace(
                "{prompt}", shlex.quote(prompt)
            )
            spawn_reason = (
                f"{len(spawnable_open)} open task(s)" if spawnable_open
                else f"{len(my_stuck)} stuck claim(s)"
            )
            print(
                f"chimera-team-run: {spawn_reason}; spawning external agent.",
                file=log,
            )
            rc = _run_with_timeout(cmd, base_env, task_timeout, log)
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

        after_tasks = team.list_tasks()
        runner_acted = _handle_stuck_claims(after_tasks)

        # Refresh task state after any runner-initiated releases so the
        # progress check below sees the runner's own action as a state
        # change for the *next* iteration.
        if runner_acted:
            after_tasks = team.list_tasks()
        after = {t["id"]: t.get("status") for t in after_tasks}

        progressed = before != after
        if progressed:
            last_progress = time.time()
            print(
                f"chimera-team-run: agent exited rc={rc}; team state changed.",
                file=log,
            )
        else:
            print(
                f"chimera-team-run: agent exited rc={rc} but team state "
                f"did not change. Sleeping {poll_interval:.0f}s before retry.",
                file=log,
            )
            time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chimera-team-run",
        description=(
            "Drive an MCP-capable coding agent (Codex, OpenCode, ...) as a "
            "Chimera teammate. Polls the team task list and spawns the "
            "configured external command per task."
        ),
    )
    parser.add_argument("--team", required=True, help="Team name.")
    parser.add_argument("--agent", required=True, help="This teammate's agent id.")
    parser.add_argument(
        "--cmd",
        required=True,
        help=(
            "Shell command template. Use {prompt} for an inline (shell-quoted) "
            "prompt or {prompt_file} for a tempfile path containing the prompt."
        ),
    )
    parser.add_argument(
        "--teams-home",
        default=None,
        help="Override teams home directory (default: $CHIMERA_TEAMS_HOME or ~/.chimera/teams).",
    )
    parser.add_argument("--idle-timeout", type=float, default=60.0)
    parser.add_argument("--task-timeout", type=float, default=600.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--max-nudges",
        type=int,
        default=1,
        help=(
            "Consecutive no-progress nudges to send for a stuck claim before "
            "the runner force-releases the task back to the pool (default: 1)."
        ),
    )
    args = parser.parse_args(argv)

    if "{prompt}" not in args.cmd and "{prompt_file}" not in args.cmd:
        print(
            "chimera-team-run: --cmd must contain {prompt} or {prompt_file}.",
            file=sys.stderr,
        )
        return 2

    root = Path(args.teams_home).expanduser() if args.teams_home else None
    return run_loop(
        team_name=args.team,
        agent_id=args.agent,
        cmd_template=args.cmd,
        teams_root=root,
        idle_timeout=args.idle_timeout,
        task_timeout=args.task_timeout,
        poll_interval=args.poll_interval,
        max_nudges=args.max_nudges,
    )


if __name__ == "__main__":
    sys.exit(main())
