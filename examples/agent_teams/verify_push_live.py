"""Live check: team mail reaches a running teammate mid-turn (issue #149).

``tests/mcp/test_team_push.py`` proves the wiring hermetically. This
script proves the thing that matters to a human: a **real model**, in
the middle of a multi-step turn, receives a message sent after the turn
started and *changes what it does because of it*.

The teammate here is an in-process :class:`~chimera.assembly.driver.AgentDriver`,
which satisfies the ``TeammateSink`` protocol as-is — the push path is
the existing steer seam, not a parallel channel.

Shape of the test: the agent is asked to create three files. Once it has
created the first one, the lead sends mail renaming the third. If the
push worked, ``gamma.txt`` exists and ``three.txt`` does not.

Run::

    set -a; source .env; set +a
    python examples/agent_teams/verify_push_live.py --model 'glm-5.2[1m]'

Exits 0 on PASS, 1 on FAIL. Opt-in: needs a configured provider and
spends tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TASK = (
    "Create three files in the current directory, one at a time, in this "
    "order: one.txt containing 'one', two.txt containing 'two', and "
    "three.txt containing 'three'. Create them one per step."
)
MAIL = (
    "Change of plan from the lead: do NOT create three.txt. Name the third "
    "file gamma.txt instead (same contents). If you already created "
    "three.txt, delete it."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", ""))
    parser.add_argument("--agent", default="chimera-1")
    parser.add_argument("--keep", action="store_true", help="Keep the temp dirs.")
    args = parser.parse_args(argv)

    if not args.model:
        print("FAIL: no model (pass --model or set ANTHROPIC_MODEL)")
        return 1

    sys.path.insert(0, str(REPO_ROOT))
    from chimera.assembly.driver import AgentDriver
    from chimera.cli.agent_teams import Team, TeamMailbox
    from chimera.mcp_servers.team_push import MailboxWatcher

    teams_home = Path(tempfile.mkdtemp(prefix="chimera-push-teams-"))
    workspace = Path(tempfile.mkdtemp(prefix="chimera-push-ws-"))
    try:
        team = Team("verify-push", root=teams_home)
        team.init()
        team.add_member(args.agent)
        mailbox = TeamMailbox(team, args.agent)

        driver = AgentDriver(
            model=args.model, project_dir=str(workspace), preset="coding_agent",
        )
        # AgentDriver IS a TeammateSink — no adapter, no second channel.
        watcher = MailboxWatcher(mailbox, driver, interval=0.2, debounce=0.05)

        sent = False

        async def _run() -> None:
            nonlocal sent
            async for event in driver.send(TASK):
                name = getattr(event.type, "value", str(event.type))
                if name in ("tool_use", "tool_result"):
                    print(f"  · {name}: {str(event.data)[:100]}", flush=True)
                # Send the mail as soon as the first file exists — that is
                # unambiguously mid-turn.
                if not sent and (workspace / "one.txt").exists():
                    sent = True
                    mailbox.send("lead", MAIL)
                    print("  → lead sent mail (mid-turn)", flush=True)

        print(f"workspace={workspace}")
        with watcher:
            asyncio.run(_run())

        created = sorted(p.name for p in workspace.iterdir() if p.is_file())
        print(f"\nmail sent mid-turn: {sent}")
        print(f"messages pushed:    {watcher.delivered}")
        print(f"files created:      {created}")

        if not sent:
            print("FAIL: the agent never created one.txt, so no mid-turn point existed")
            return 1
        if watcher.delivered < 1:
            print("FAIL: the watcher never delivered the message")
            return 1
        if mailbox.recv(drain=False):
            print("FAIL: mail was left undelivered in the mailbox")
            return 1
        if "gamma.txt" not in created:
            print("FAIL: the agent did not act on the pushed message")
            return 1
        if "three.txt" in created:
            print("FAIL: three.txt still exists — the redirection did not take")
            return 1
        print("PASS: mid-turn mail reached a real model and changed its behavior")
        return 0
    finally:
        if args.keep:
            print(f"kept: {teams_home} {workspace}")
        else:
            shutil.rmtree(teams_home, ignore_errors=True)
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
