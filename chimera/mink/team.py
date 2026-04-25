"""``chimera team`` subcommand — manage experimental agent teams.

Wired into ``chimera/cli/main.py`` as a top-level subcommand. The same
``register`` and ``run`` entry points can be re-attached underneath the
``mink`` parent subparser later without changes.

Subcommands::

    chimera team create <name>            create a team
    chimera team join <name> <agent_id>   join an existing team
    chimera team task add <name> "<desc>" add a task
    chimera team task list <name>         list tasks
    chimera team status <name>            show team config + counts
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from chimera.cli.agent_teams import (
    ENV_FLAG,
    Team,
    create_team,
    is_enabled,
    join_team,
)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Attach the ``team`` subcommand to a parent argparse subparsers object."""
    team_parser = subparsers.add_parser("team", help="Manage experimental agent teams")
    team_sub = team_parser.add_subparsers(dest="team_action", required=False)

    p_create = team_sub.add_parser("create", help="Create a new team")
    p_create.add_argument("name")
    p_create.add_argument("--model", default="kimi-k2.6")

    p_join = team_sub.add_parser("join", help="Join an existing team")
    p_join.add_argument("name")
    p_join.add_argument("agent_id")

    p_task = team_sub.add_parser("task", help="Task list operations")
    task_sub = p_task.add_subparsers(dest="task_action", required=True)

    p_task_add = task_sub.add_parser("add", help="Append a task")
    p_task_add.add_argument("name")
    p_task_add.add_argument("description")
    p_task_add.add_argument("--by", default="lead")

    p_task_list = task_sub.add_parser("list", help="List tasks")
    p_task_list.add_argument("name")

    p_status = team_sub.add_parser("status", help="Show team summary")
    p_status.add_argument("name")

    team_parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if not is_enabled():
        print(
            f"agent teams disabled: set {ENV_FLAG}=1 to enable.",
            file=sys.stderr,
        )
        return 2

    action = getattr(args, "team_action", None)
    if action == "create":
        team = create_team(args.name, default_model=args.model)
        print(f"created team '{team.name}' at {team.dir}")
        return 0
    if action == "join":
        team = join_team(args.name, args.agent_id)
        print(f"agent '{args.agent_id}' joined team '{team.name}'")
        return 0
    if action == "task":
        team = Team(args.name)
        if not team.exists():
            print(f"team '{args.name}' does not exist", file=sys.stderr)
            return 1
        if args.task_action == "add":
            tid = team.add_task(args.description, created_by=args.by)
            print(tid)
            return 0
        if args.task_action == "list":
            for rec in team.list_tasks():
                print(json.dumps(rec))
            return 0
    if action == "status":
        team = Team(args.name)
        if not team.exists():
            print(f"team '{args.name}' does not exist", file=sys.stderr)
            return 1
        cfg = team.load_config()
        tasks = team.list_tasks()
        open_tasks = sum(1 for t in tasks if t.get("status") == "open")
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        print(json.dumps({
            "name": cfg.get("name"),
            "default_model": cfg.get("default_model"),
            "members": cfg.get("members", []),
            "tasks_total": len(tasks),
            "tasks_open": open_tasks,
            "tasks_completed": completed,
        }, indent=2))
        return 0

    print("usage: chimera team {create|join|task|status} ...", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point (useful for tests / direct invocation)."""
    parser = argparse.ArgumentParser(prog="chimera-team")
    sub = parser.add_subparsers(dest="command")
    register(sub)
    args = parser.parse_args(argv)
    if args.command != "team":
        parser.print_help()
        return 1
    return run(args)


__all__ = ["ENV_FLAG", "Team", "create_team", "is_enabled", "join_team", "main", "register", "run"]
