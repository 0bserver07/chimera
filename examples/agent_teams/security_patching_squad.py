# examples/agent_teams/security_patching_squad.py
"""A high-fidelity, runnable example demonstrating production-grade multi-agent
SecOps collaboration with the new interactive plan-approval workflow.

This standalone script simulates three distinct agents collaborating on hardening
a sandboxed codebase:
1. SecOps Triage Agent (triage-agent): Analyzes the target app's codebase, isolates
   critical security flaws, and schedules tasks in the locked team queue.
2. Security Patch Agent (patch-agent): Claims tasks, proposes secure refactoring plans
   for critical gates (requiring lead approval), externalizes secrets, and writes secure queries.
3. Security Auditor Agent (auditor-agent): Runs a ChecklistCritic review (with a
   canned mock-provider verdict) on the hardened code to demonstrate the audit step
   before notifying the lead that the code is safe.
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
import threading
from pathlib import Path
from typing import Any

# Chimera imports
from chimera.cli.agent_teams import Team, TeamMailbox
from chimera.mcp_servers.teammate_runner import run_loop, ACPClientLike
from chimera.providers.base import Provider, Response
from chimera.types import Message
from chimera.critic import ChecklistCritic, CriticConfig
from chimera.core.context import Context

# Use a print lock to ensure clean, non-interleaved logging to stdout
_print_lock = threading.Lock()

def safe_log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


class MockSecurityCriticProvider(Provider):
    """A mock LLM provider designed to respond to ChecklistCritic evaluations with a security-pass score.
    """
    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: Any | None = None,
        **kwargs: Any,
    ) -> Response:
        content = (
            "SCORE: 1.0\n"
            "FEEDBACK: All security checklist items are perfectly satisfied. "
            "The database login logic now implements parameterized queries to eliminate SQL injection, "
            "the hardcoded database password has been safely externalized via environment variables, "
            "and command injection risks have been removed."
        )
        return Response(content=content, tool_calls=[], usage={})

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return False

    @property
    def model_name(self) -> str:
        return "mock-security-model"


class SimulatedSecTeammate(ACPClientLike):
    """A structural implementation of ACPClientLike tailored for secure software remediation.

    This agent receives tasks from the team on-disk queue, proposes secure implementation plans,
    patches vulnerable target files, and executes verification runs.
    """
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.team_name = cfg.env.get("CHIMERA_TEAM")
        self.agent_id = cfg.env.get("CHIMERA_AGENT")
        self.teams_home = cfg.env.get("CHIMERA_TEAMS_HOME")
        self.workspace_dir = Path(cfg.env.get("CHIMERA_WORKSPACE", ""))
        self.started = False
        self.tasks_handled = 0

    def start(self) -> None:
        self.started = True
        safe_log(f"🛡️  [{self.agent_id}] SecOps persistent channel opened.")

    def stop(self) -> None:
        self.started = False
        safe_log(f"🔌 [{self.agent_id}] SecOps channel closed. Tasks handled: {self.tasks_handled}")

    def send_message(self, text: str) -> object:
        if not self.started:
            raise RuntimeError("ACP session is not started!")
        
        safe_log(f"\n📨 [{self.agent_id}] Received action cue from lead coordinator.")
        
        teams_root_path = Path(self.teams_home) if self.teams_home else None
        team = Team(self.team_name, root=teams_root_path)
        
        # 1. Drain incoming direct messages
        mailbox = TeamMailbox(team, self.agent_id)
        messages = mailbox.recv(drain=True)
        if messages:
            safe_log(f"📥 [{self.agent_id}] Inbox messages received:")
            for m in messages:
                safe_log(f"   - [{m['from']}]: {m['content']}")
        
        # 2. Claim next available task or identify our already claimed task
        tasks = team.list_tasks(status_filter="all")
        matching_task = None
        
        # First, check if we already have a claimed task that we are actively working on (e.g. waiting for plan approval)
        for t in tasks:
            if t.get("claimed_by") == self.agent_id and t.get("status") == "claimed":
                # If we claimed it, we are already the owner
                if self.agent_id == "patch-agent" and "SQL injection" in t["description"]:
                    matching_task = t
                    break
        
        # If no active claimed task, look for an open/unblocked task
        if not matching_task:
            open_tasks = team.list_tasks(status_filter="open")
            for t in open_tasks:
                desc = t["description"]
                if self.agent_id == "patch-agent":
                    if "SQL injection" in desc or "password" in desc:
                        matching_task = t
                        break
                elif self.agent_id == "auditor-agent":
                    if "ChecklistCritic" in desc or "audit" in desc:
                        matching_task = t
                        break

        if not matching_task:
            safe_log(f"🔍 [{self.agent_id}] No unblocked patching tasks ready in queue.")
            return {"status": "no_task"}

        task_id = matching_task["id"]
        desc = matching_task["description"]
        if matching_task.get("plan_status") == "pending":
            # Plan is awaiting lead approval; idle instead of re-claiming or spinning.
            time.sleep(0.5)
            return {"status": "awaiting_approval"}
        # Only claim if it is not already claimed by us
        if matching_task.get("claimed_by") != self.agent_id:
            won = team.claim_task(task_id, self.agent_id)
            if not won:
                safe_log(f"⚠️ [{self.agent_id}] Race condition: task {task_id} already claimed.")
                return {"status": "race_lost"}
            safe_log(f"🛠️  [{self.agent_id}] Claimed task {task_id}: '{matching_task['description']}'")
        else:
            safe_log(f"🛠️  [{self.agent_id}] Continuing active task {task_id}: '{matching_task['description']}'")
            
        self.tasks_handled += 1

        # 3. Process the claimed task
        if self.agent_id == "patch-agent":
            target_file = self.workspace_dir / "app.py"
            
            if "SQL injection" in desc:
                # This task requires a plan to be proposed and approved
                if matching_task.get("plan_status") is None:
                    # Step A: Propose secure implementation plan
                    safe_log(f"📝 [{self.agent_id}] Gated task: Proposing refactoring plan...")
                    plan = (
                        "PROPOSED REMEDIATION PLAN:\n"
                        "1. Identify vulnerable SQLite lookup query containing string interpolation.\n"
                        "2. Replace raw query string `cursor.execute(f'SELECT ...')` with parameterized argument tuple:\n"
                        "   `cursor.execute('SELECT * FROM users WHERE username=? AND password=?', (user, pwd))`\n"
                        "3. This fully eliminates SQL injection risks by allowing the driver to sanitize input."
                    )
                    team.propose_plan(task_id, self.agent_id, plan)
                    safe_log(f"⏳ [{self.agent_id}] Plan proposed for task {task_id}. Awaiting lead architect approval.")
                    
                    # Release/keep claimed so the lead can review.
                    # Under real execution, the agent exits / returns control while waiting for approval.
                    return {"status": "plan_submitted"}

                elif matching_task.get("plan_status") == "approved":
                    # Step B: Apply security patch after plan is approved
                    safe_log(f"🔧 [{self.agent_id}] Plan approved! Applying query remediation...")
                    
                    # Original code has vulnerable query, replace it
                    code = target_file.read_text(encoding="utf-8")
                    vulnerable = "cursor.execute(f\"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'\")"
                    secure = "cursor.execute(\"SELECT * FROM users WHERE username = ? AND password = ?\", (username, password))"
                    
                    if vulnerable in code:
                        code = code.replace(vulnerable, secure)
                        target_file.write_text(code, encoding="utf-8")
                        safe_log(f"💾 [{self.agent_id}] Patched app.py with parameterized SQLite calls.")
                    
                    team.complete_task(task_id, self.agent_id, result="Vulnerable SQLite query refactored into a parameterized statement.")
                    safe_log(f"✅ [{self.agent_id}] SQL injection remediation complete.")

            elif "password" in desc:
                # Task 2: Externalize password
                safe_log(f"🔑 [{self.agent_id}] Hardening credentials...")
                code = target_file.read_text(encoding="utf-8")
                
                vulnerable = 'DB_PASSWORD = "super-secret-production-key-999"'
                secure = 'DB_PASSWORD = os.environ.get("DB_PASSWORD", "safe-dev-fallback")'
                
                if vulnerable in code:
                    code = code.replace(vulnerable, secure)
                    # Add imports if missing
                    if "import os" not in code:
                        code = "import os\n" + code
                    target_file.write_text(code, encoding="utf-8")
                    safe_log(f"💾 [{self.agent_id}] Secrets removed from source code.")
                
                team.complete_task(task_id, self.agent_id, result="Replaced hardcoded database password with environment variable lookup.")
                safe_log(f"✅ [{self.agent_id}] Password externalization complete.")
                
                # Send DM to auditor-agent
                safe_log(f"✉️  [{self.agent_id}] DMing auditor-agent to start audits...")
                TeamMailbox(team, "auditor-agent").send(
                    sender=self.agent_id,
                    content="Vulnerabilities have been patched and credentials externalized. Ready for SecOps audit!"
                )

        elif self.agent_id == "auditor-agent":
            # Task 3: Security audit and ChecklistCritic execution
            safe_log(f"🧐 [{self.agent_id}] Starting ChecklistCritic code quality audit...")
            
            target_file = self.workspace_dir / "app.py"
            patched_code = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
            
            # Configure Critic
            provider = MockSecurityCriticProvider()
            config = CriticConfig(success_threshold=0.9, critic_model="security-critic-3.5")
            critic = ChecklistCritic(
                checklist=[
                    "No SQL injection string interpolations in query executions",
                    "No production credentials hardcoded in plain-text source files",
                    "Imports correctly configured with no syntactic errors",
                ],
                provider=provider,
                config=config
            )
            
            ctx = Context(system="You are a secure coding audit critic.")
            ctx.add(Message.user("Evaluate the security posture of the patched app.py file."))
            action_msg = Message.assistant(f"Code review target:\n```python\n{patched_code}\n```")
            
            result = critic.evaluate(ctx, action_msg)
            
            safe_log(f"📊 [{self.agent_id}] Audit report:")
            safe_log(f"   Security Score: {result.score * 100}%")
            safe_log(f"   Pass State:     {result.passed}")
            safe_log(f"   Feedback:       {result.feedback}")
            
            team.complete_task(
                task_id,
                self.agent_id,
                result=f"Security audit passed successfully. Score={result.score}. Feedback: {result.feedback}"
            )
            safe_log(f"✅ [{self.agent_id}] Audit task complete.")
            
            # DM the lead
            TeamMailbox(team, "lead").send(
                sender=self.agent_id,
                content="Vulnerability audit complete! Patched codebase is certified clean."
            )

        return {"status": "success", "task_id": task_id}


def run_security_squad_demo() -> int:
    temp_dir = tempfile.TemporaryDirectory()
    teams_home = Path(temp_dir.name) / "teams"
    workspace_dir = Path(temp_dir.name) / "workspace"
    
    os.environ["CHIMERA_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    os.environ["CHIMERA_TEAMS_HOME"] = str(teams_home)
    
    # 1. Create target app containing vulnerabilities
    workspace_dir.mkdir(parents=True, exist_ok=True)
    vulnerable_app = workspace_dir / "app.py"
    vulnerable_app.write_text(
        '# app.py\n'
        'import sqlite3\n\n'
        'DB_PASSWORD = "super-secret-production-key-999"\n\n'
        'def login_user(username, password):\n'
        '    conn = sqlite3.connect("users.db")\n'
        '    cursor = conn.cursor()\n'
        '    # CRITICAL: Vulnerable to SQL injection\n'
        '    cursor.execute(f"SELECT * FROM users WHERE username = \'{username}\' AND password = \'{password}\'")\n'
        '    return cursor.fetchone()\n',
        encoding="utf-8"
    )
    
    safe_log("\033[1;35m================================================================================\033[0m")
    safe_log("\033[1;35m🚀 INITIALIZING CHIMERA SECOPS VULNERABILITY MITIGATION & PATCHING DEMO\033[0m")
    safe_log("\033[1;35m================================================================================\033[0m")
    safe_log(f"📂 Sandbox Workspace: {workspace_dir}")
    safe_log(f"💀 Initial Vulnerable File Created at: {vulnerable_app}\n")
    
    # 2. Triage agent sets up the team and seeds tasks
    safe_log("🚨 [triage-agent] Initializing 'secops-hardening-team'...")
    team = Team("secops-hardening-team", root=teams_home)
    team.init(default_model="mock-security-model")
    team.add_member("triage-agent")
    team.add_member("patch-agent")
    team.add_member("auditor-agent")
    
    safe_log("🚨 [triage-agent] Registering findings to the shared queue:")
    
    # Task 1: Gated by plan approvals
    task1_id = team.add_task(
        description="Remediate SQL injection in SQLite login queries.",
        created_by="triage-agent",
        requires_plan=True
    )
    safe_log(f"   🔒 Task 1 [{task1_id}] Registered: Query parameterized refactoring (Requires Plan Approval)")
    
    # Task 2: Credentials
    task2_id = team.add_task(
        description="Externalize hardcoded database password to environment variables.",
        created_by="triage-agent",
        depends_on=[task1_id]
    )
    safe_log(f"   🔒 Task 2 [{task2_id}] Registered: Secrets externalization (Depends on Task 1)")
    
    # Task 3: Audit
    task3_id = team.add_task(
        description="ChecklistCritic audit of the modified source files.",
        created_by="triage-agent",
        depends_on=[task2_id]
    )
    safe_log(f"   🔒 Task 3 [{task3_id}] Registered: Post-patch security audit (Depends on Task 2)")
    
    # 3. Configure persistent teammate runner factory
    def patch_client_factory(cfg: Any) -> ACPClientLike:
        cfg.env["CHIMERA_WORKSPACE"] = str(workspace_dir)
        return SimulatedSecTeammate(cfg)
        
    def auditor_client_factory(cfg: Any) -> ACPClientLike:
        cfg.env["CHIMERA_WORKSPACE"] = str(workspace_dir)
        return SimulatedSecTeammate(cfg)
        
    safe_log("\n🤖 Starting persistent SecOps runners (patching squad)...")
    
    # Spawn background teammates
    patch_runner = threading.Thread(
        target=run_loop,
        kwargs={
            "team_name": "secops-hardening-team",
            "agent_id": "patch-agent",
            "cmd_template": "mock_patch_cmd",
            "teams_root": teams_home,
            "idle_timeout": 12.0,
            "poll_interval": 1.0,
            "reuse_session": True,
            "runtime": "acp",
            "acp_client_factory": patch_client_factory,
            "log": open(os.devnull, "w"),
        },
        daemon=True
    )
    
    auditor_runner = threading.Thread(
        target=run_loop,
        kwargs={
            "team_name": "secops-hardening-team",
            "agent_id": "auditor-agent",
            "cmd_template": "mock_auditor_cmd",
            "teams_root": teams_home,
            "idle_timeout": 12.0,
            "poll_interval": 1.0,
            "reuse_session": True,
            "runtime": "acp",
            "acp_client_factory": auditor_client_factory,
            "log": open(os.devnull, "w"),
        },
        daemon=True
    )
    
    patch_runner.start()
    auditor_runner.start()
    
    # 4. Simulate a Lead Operator polling and approving proposed plans
    safe_log("\n👥 [lead-operator] Monitoring incoming proposals...")
    approved = False
    start_time = time.time()
    
    # Run a short loop in the main thread acting as the human operator (approving pending plans)
    while time.time() - start_time < 30:
        tasks = team.list_tasks(status_filter="all")
        pending = [t for t in tasks if t.get("plan_status") == "pending"]
        
        for task in pending:
            safe_log("\n\033[1;33m>>> Lead Operator Approval Request! <<<\033[0m")
            safe_log(f"    Task:   {task['id']}")
            safe_log(f"    Agent:  {task['claimed_by']}")
            safe_log(f"    Plan:\n\033[0;36m{task['proposed_plan']}\033[0m")
            
            # Interactive simulation: Lead approves the plan
            safe_log("    [Lead Decision] -> Approving plan.")
            team.approve_plan(task["id"], "approve")
            approved = True
            
        # Exit if all tasks are complete
        all_done = all(t["status"] == "completed" for t in tasks)
        if all_done and approved:
            break
            
        time.sleep(1.0)
        
    # Wait for teammates to finish processing and teardown gracefully
    patch_runner.join(timeout=15)
    auditor_runner.join(timeout=15)
    
    # 5. Check final workspace files to ensure patching was successful
    safe_log("\n🔍 Post-Patch Code Inspection:")
    safe_log("-" * 60)
    safe_log(vulnerable_app.read_text(encoding="utf-8"))
    safe_log("-" * 60)
    
    # Final state reporting
    safe_log("\n📊 Final Security Hardening Task Status:")
    all_completed = True
    for t in team.list_tasks():
        status_marker = "🟢" if t["status"] == "completed" else "🔴"
        safe_log(f"   - {status_marker} Task [{t['id']}] status: {t['status']} (Claimed by: {t['claimed_by']})")
        if t["status"] != "completed":
            all_completed = False
            
    # Teardown
    team.destroy(force=True)
    temp_dir.cleanup()
    
    if all_completed and approved:
        safe_log("\n\033[1;32m🎉 SECOPS HARDENING MULTI-AGENT COLLABORATION DEMO COMPLETED SUCCESSFULLY!\033[0m")
        return 0
    else:
        safe_log("\n\033[1;31m🚨 SECOPS DEMO COMPLETED WITH ISSUES (Tasks remaining or plan was not approved).\033[0m")
        return 1


if __name__ == "__main__":
    sys.exit(run_security_squad_demo())
