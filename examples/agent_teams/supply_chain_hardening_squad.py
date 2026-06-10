# examples/agent_teams/supply_chain_hardening_squad.py
"""A runnable, fully mocked example demonstrating a multi-agent
Supply Chain & Package Vulnerability Hardening Squad.

This standalone script simulates three distinct agents collaborating to identify
and fix supply chain vulnerabilities in a project's dependencies:
1. Supply Chain Triage Agent (triage-agent): Scans the package manifest (e.g. package.json),
   cross-references against known CVEs and malicious typo-squatted packages, and registers tasks.
2. Package Remediation Agent (remediation-agent): Claims tasks, proposes safe upgrade and
   regression-testing plans (requiring human lead approval), and modifies the manifest.
3. Security Auditor Agent (auditor-agent): Runs post-upgrade verification and semantic tests
   to ensure package versions are safe and compatible.
"""
from __future__ import annotations

import os
import sys
import time
import json
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

_print_lock = threading.Lock()

def safe_log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


class MockSupplyChainProvider(Provider):
    """A mock LLM provider designed to respond to dependency audits with a pass score.
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
            "FEEDBACK: Supply chain audit passed. Lodash has been successfully upgraded "
            "to 4.17.21 (remediating CVE-2020-8203 prototype pollution) and Axios has been "
            "upgraded to 1.6.0 (remediating CVE-2020-28168 SSRF). All dependencies are secure."
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
        return "mock-supply-chain-model"


class SimulatedSupplyChainTeammate(ACPClientLike):
    """A persistent teammate agent designed to scan, remediate, and audit NPM supply chain issues.
    """
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.team_name = cfg.env.get("CHIMERA_TEAM")
        self.agent_id = cfg.env.get("CHIMERA_AGENT")
        self.teams_home = cfg.env.get("CHIMERA_TEAMS_HOME")
        workspace = cfg.env.get("CHIMERA_WORKSPACE")
        if not workspace:
            raise RuntimeError("CHIMERA_WORKSPACE must be set by the client factory")
        self.workspace_dir = Path(workspace)
        self.started = False
        self.tasks_handled = 0

    def start(self) -> None:
        self.started = True
        safe_log(f"📦 [{self.agent_id}] Dependency Hardening channel opened.")

    def stop(self) -> None:
        self.started = False
        safe_log(f"🔌 [{self.agent_id}] Hardening channel closed. Tasks handled: {self.tasks_handled}")

    def send_message(self, text: str) -> object:
        if not self.started:
            raise RuntimeError("ACP session is not started!")
        
        safe_log(f"\n📨 [{self.agent_id}] Received action cue from lead coordinator.")
        
        teams_root_path = Path(self.teams_home) if self.teams_home else None
        team = Team(self.team_name, root=teams_root_path)
        
        # 1. Drain inbox
        mailbox = TeamMailbox(team, self.agent_id)
        messages = mailbox.recv(drain=True)
        if messages:
            safe_log(f"📥 [{self.agent_id}] Inbox messages received:")
            for m in messages:
                safe_log(f"   - [{m['from']}]: {m['content']}")
        
        # 2. Claim next matching task or continue existing claimed task
        tasks = team.list_tasks(status_filter="all")
        matching_task = None
        
        for t in tasks:
            if t.get("claimed_by") == self.agent_id and t.get("status") == "claimed":
                if self.agent_id == "remediation-agent" and "upgrade" in t["description"].lower():
                    matching_task = t
                    break
        
        if not matching_task:
            open_tasks = team.list_tasks(status_filter="open")
            for t in open_tasks:
                desc = t["description"].lower()
                if self.agent_id == "remediation-agent":
                    # no bare "upgrade" match: the audit task's description contains
                    # the word too, and the auditor must claim that one
                    if "lodash" in desc or "axios" in desc:
                        matching_task = t
                        break
                elif self.agent_id == "auditor-agent":
                    if "audit" in desc or "critic" in desc or "verify" in desc:
                        matching_task = t
                        break

        if not matching_task:
            safe_log(f"🔍 [{self.agent_id}] No dependency hardening tasks ready in queue.")
            return {"status": "no_task"}

        task_id = matching_task["id"]
        
        if matching_task.get("claimed_by") != self.agent_id:
            won = team.claim_task(task_id, self.agent_id)
            if not won:
                safe_log(f"⚠️ [{self.agent_id}] Race condition: task {task_id} already claimed.")
                return {"status": "race_lost"}
            safe_log(f"🛠️  [{self.agent_id}] Claimed task {task_id}: '{matching_task['description']}'")
        else:
            safe_log(f"🛠️  [{self.agent_id}] Continuing active task {task_id}: '{matching_task['description']}'")
            
        self.tasks_handled += 1

        # 3. Perform work
        if self.agent_id == "remediation-agent":
            manifest_file = self.workspace_dir / "package.json"
            
            if "lodash" in matching_task["description"].lower() or "axios" in matching_task["description"].lower():
                # Upgrade requires human approval (major package bumps can introduce breaking changes)
                if matching_task.get("plan_status") is None:
                    safe_log(f"📝 [{self.agent_id}] Upgrade gate: Proposing upgrade migration path...")
                    plan = (
                        "DEPENDENCY UPGRADE PLAN:\n"
                        "1. Bump `lodash` from 4.17.15 to 4.17.21 (eliminates prototype pollution CVE-2020-8203).\n"
                        "2. Bump `axios` from 0.19.0 to 1.6.0 (removes SSRF CVE-2020-28168).\n"
                        "3. Perform backward-compatibility checking against known API signatures."
                    )
                    team.propose_plan(task_id, self.agent_id, plan)
                    safe_log(f"⏳ [{self.agent_id}] Upgrade plan proposed for task {task_id}. Awaiting lead approval.")
                    return {"status": "plan_submitted"}
                
                elif matching_task.get("plan_status") == "approved":
                    safe_log(f"🔧 [{self.agent_id}] Plan approved! Rewriting package manifest...")
                    
                    if manifest_file.exists():
                        data = json.loads(manifest_file.read_text(encoding="utf-8"))
                        data["dependencies"]["lodash"] = "^4.17.21"
                        data["dependencies"]["axios"] = "^1.6.0"
                        manifest_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        safe_log(f"💾 [{self.agent_id}] Updated package.json dependencies to safe versions.")
                    
                    team.complete_task(task_id, self.agent_id, result="Upgraded lodash to ^4.17.21 and axios to ^1.6.0 in package.json.")
                    safe_log(f"✅ [{self.agent_id}] Package upgrades successfully applied.")
                    
                    # DM auditor-agent
                    safe_log(f"✉️  [{self.agent_id}] Direct Messaging auditor-agent to verify dependencies...")
                    TeamMailbox(team, "auditor-agent").send(
                        sender=self.agent_id,
                        content="Upgraded package.json dependencies are ready for compatibility and security audits."
                    )

        elif self.agent_id == "auditor-agent":
            safe_log(f"🧐 [{self.agent_id}] Beginning checklist audit on updated manifest...")
            manifest_file = self.workspace_dir / "package.json"
            manifest_content = manifest_file.read_text(encoding="utf-8") if manifest_file.exists() else ""
            
            provider = MockSupplyChainProvider()
            config = CriticConfig(success_threshold=0.9, critic_model="supply-chain-critic")
            critic = ChecklistCritic(
                checklist=[
                    "lodash version is bumped to at least 4.17.21",
                    "axios version is bumped to at least 1.6.0",
                    "Manifest is valid and syntactically correct JSON"
                ],
                provider=provider,
                config=config
            )
            
            ctx = Context(system="You are a package security and supply chain auditor.")
            ctx.add(Message.user("Verify the dependency updates in package.json."))
            action_msg = Message.assistant(f"package.json contents:\n```json\n{manifest_content}\n```")
            
            result = critic.evaluate(ctx, action_msg)
            
            safe_log(f"📊 [{self.agent_id}] Critic Evaluation Result:")
            safe_log(f"   Passed:   {result.passed}")
            safe_log(f"   Feedback: {result.feedback}")
            
            team.complete_task(
                task_id,
                self.agent_id,
                result=f"Dependency verification passed: {result.passed}. Feedback: {result.feedback}"
            )
            safe_log(f"✅ [{self.agent_id}] Verification task complete.")
            
            # Message lead
            TeamMailbox(team, "lead").send(
                sender=self.agent_id,
                content="Supply chain hardening complete! Lodash and Axios successfully secured."
            )

        return {"status": "success", "task_id": task_id}


def run_supply_chain_demo() -> int:
    temp_dir = tempfile.TemporaryDirectory()
    teams_home = Path(temp_dir.name) / "teams"
    workspace_dir = Path(temp_dir.name) / "workspace"
    
    os.environ["CHIMERA_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    os.environ["CHIMERA_TEAMS_HOME"] = str(teams_home)
    
    # 1. Create vulnerable package.json manifest
    workspace_dir.mkdir(parents=True, exist_ok=True)
    manifest = workspace_dir / "package.json"
    manifest.write_text(
        json.dumps({
            "name": "vulnerable-nodejs-app",
            "version": "1.0.0",
            "dependencies": {
                "lodash": "4.17.15",
                "axios": "0.19.0"
            }
        }, indent=2),
        encoding="utf-8"
    )
    
    safe_log("\033[1;36m================================================================================\033[0m")
    safe_log("\033[1;36m🚀 INITIALIZING CHIMERA SUPPLY CHAIN & DEPENDENCY HARDENING SQUAD DEMO\033[0m")
    safe_log("\033[1;36m================================================================================\033[0m")
    safe_log(f"📂 Sandbox Workspace: {workspace_dir}")
    safe_log(f"💀 Initial Vulnerable Package Manifest Created at: {manifest}\n")
    
    # 2. Triage agent sets up the team and seeds tasks
    safe_log("🚨 [triage-agent] Initializing 'supply-chain-team'...")
    team = Team("supply-chain-team", root=teams_home)
    team.init(default_model="mock-supply-chain-model")
    team.add_member("triage-agent")
    team.add_member("remediation-agent")
    team.add_member("auditor-agent")
    
    safe_log("🚨 [triage-agent] Registering supply chain flaws in the task queue:")
    
    # Task 1: Requires plan approval
    task1_id = team.add_task(
        description="Upgrade lodash and axios dependencies to remediate high-severity CVEs.",
        created_by="triage-agent",
        requires_plan=True
    )
    safe_log(f"   🔒 Task 1 [{task1_id}] Registered: Upgrade lodash & axios (Requires Plan Approval)")
    
    # Task 2: Audit
    task2_id = team.add_task(
        description="ChecklistCritic audit of package.json to verify security upgrades.",
        created_by="triage-agent",
        depends_on=[task1_id]
    )
    safe_log(f"   🔒 Task 2 [{task2_id}] Registered: Supply chain post-patch audit (Depends on Task 1)")
    
    # 3. Configure persistent teammate runners
    def remediation_client_factory(cfg: Any) -> ACPClientLike:
        cfg.env["CHIMERA_WORKSPACE"] = str(workspace_dir)
        return SimulatedSupplyChainTeammate(cfg)
        
    def auditor_client_factory(cfg: Any) -> ACPClientLike:
        cfg.env["CHIMERA_WORKSPACE"] = str(workspace_dir)
        return SimulatedSupplyChainTeammate(cfg)
        
    safe_log("\n🤖 Starting persistent Dependency Hardening runners...")
    
    remediation_runner = threading.Thread(
        target=run_loop,
        kwargs={
            "team_name": "supply-chain-team",
            "agent_id": "remediation-agent",
            "cmd_template": "mock_remediation_cmd",
            "teams_root": teams_home,
            "idle_timeout": 12.0,
            "poll_interval": 1.0,
            "max_nudges": 5,
            "reuse_session": True,
            "runtime": "acp",
            "acp_client_factory": remediation_client_factory,
            "log": open(os.devnull, "w"),
        },
        daemon=True
    )
    
    auditor_runner = threading.Thread(
        target=run_loop,
        kwargs={
            "team_name": "supply-chain-team",
            "agent_id": "auditor-agent",
            "cmd_template": "mock_auditor_cmd",
            "teams_root": teams_home,
            "idle_timeout": 12.0,
            "poll_interval": 1.0,
            "max_nudges": 5,
            "reuse_session": True,
            "runtime": "acp",
            "acp_client_factory": auditor_client_factory,
            "log": open(os.devnull, "w"),
        },
        daemon=True
    )
    
    remediation_runner.start()
    auditor_runner.start()
    
    # 4. Simulate a Lead Operator polling and approving proposed plans
    safe_log("\n👥 [lead-operator] Monitoring incoming upgrade proposals...")
    approved = False
    start_time = time.time()
    
    while time.time() - start_time < 30:
        tasks = team.list_tasks(status_filter="all")
        pending = [t for t in tasks if t.get("plan_status") == "pending"]
        
        for task in pending:
            safe_log("\n\033[1;33m>>> Lead Operator Approval Request! <<<\033[0m")
            safe_log(f"    Task:   {task['id']}")
            safe_log(f"    Agent:  {task['claimed_by']}")
            safe_log(f"    Plan:\n\033[0;36m{task['proposed_plan']}\033[0m")
            
            safe_log("    [Lead Decision] -> Approving upgrade plan.")
            team.approve_plan(task["id"], "approve")
            approved = True
            
        all_done = all(t["status"] == "completed" for t in tasks)
        if all_done and approved:
            break
            
        time.sleep(1.0)
        
    remediation_runner.join(timeout=15)
    auditor_runner.join(timeout=15)
    
    # 5. Check package.json to verify successful upgrade
    safe_log("\n🔍 Post-Upgrade package.json Inspection:")
    safe_log("-" * 60)
    safe_log(manifest.read_text(encoding="utf-8"))
    safe_log("-" * 60)
    
    safe_log("\n📊 Final Supply Chain Hardening Task Status:")
    all_completed = True
    for t in team.list_tasks():
        status_marker = "🟢" if t["status"] == "completed" else "🔴"
        safe_log(f"   - {status_marker} Task [{t['id']}] status: {t['status']} (Claimed by: {t['claimed_by']})")
        if t["status"] != "completed":
            all_completed = False
            
    team.destroy(force=True)
    temp_dir.cleanup()
    
    if all_completed and approved:
        safe_log("\n\033[1;32m🎉 SUPPLY CHAIN HARDENING SQUAD DEMO COMPLETED SUCCESSFULLY!\033[0m")
        return 0
    else:
        safe_log("\n\033[1;31m🚨 DEMO COMPLETED WITH ISSUES (Tasks remaining or plan was not approved).\033[0m")
        return 1


if __name__ == "__main__":
    sys.exit(run_supply_chain_demo())
