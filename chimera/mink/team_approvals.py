"""CLI interactive approval loop for proposed task plans."""
from __future__ import annotations

import sys
import time
from chimera.cli.agent_teams import Team

def run_approvals(team_name: str, poll_interval: float = 1.0) -> int:
    """Watch the team's task list for pending plans and prompt the lead interactively.

    Args:
        team_name: Name of the team to watch.
        poll_interval: Seconds to sleep between polls of the task list.

    Returns:
        Process exit code: ``0`` on clean exit (Ctrl+C / EOF), ``1`` if the
        team does not exist.
    """
    team = Team(team_name)
    if not team.exists():
        print(f"Error: team '{team_name}' does not exist.", file=sys.stderr)
        return 1

    title = f"Chimera Plan Approval Operator for team '{team_name}'"
    hint = "Watching for pending plans... (Ctrl+C to exit)"
    width = max(len(title), len(hint)) + 2
    print(f"\033[1;36m┌{'─' * width}┐\033[0m")
    print(f"\033[1;36m│ {title.ljust(width - 2)} │\033[0m")
    print(f"\033[1;36m│ {hint.ljust(width - 2)} │\033[0m")
    print(f"\033[1;36m└{'─' * width}┘\033[0m")

    try:
        while True:
            tasks = team.list_tasks(status_filter="all")
            pending_tasks = [t for t in tasks if t.get("plan_status") == "pending"]

            for task in pending_tasks:
                task_id = task["id"]
                claimed_by = task.get("claimed_by") or "unknown"
                desc = task.get("description") or ""
                plan = task.get("proposed_plan") or ""

                print(f"\n\033[1;33m[PENDING PLAN] Task ID: {task_id} claimed by agent: {claimed_by}\033[0m")
                print(f"\033[1;34mDescription:\033[0m {desc}")
                print("\033[1;34mProposed Plan:\033[0m")
                print("-" * 60)
                print(plan)
                print("-" * 60)

                while True:
                    try:
                        choice = input("\033[1;32mApprove or Reject? (a/r): \033[0m").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print("\nExiting approvals loop.")
                        return 0

                    if choice in ("a", "approve", "y", "yes"):
                        team.approve_plan(task_id, "approve")
                        print(f"\033[1;32m✓ Task {task_id} plan approved successfully.\033[0m")
                        break
                    elif choice in ("r", "reject", "n", "no"):
                        try:
                            feedback = input("\033[1;31mFeedback / Reason for rejection: \033[0m").strip()
                        except (KeyboardInterrupt, EOFError):
                            print("\nExiting approvals loop.")
                            return 0

                        team.approve_plan(task_id, "reject", feedback=feedback if feedback else None)
                        print(f"\033[1;31m✗ Task {task_id} plan rejected with feedback.\033[0m")
                        break
                    else:
                        print("Invalid choice. Please enter 'a' (approve) or 'r' (reject).")

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nExiting approvals loop.")
    return 0
