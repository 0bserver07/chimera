# examples/agent_teams/real_world_team_collaboration.py
"""A high-fidelity, runnable example demonstrating production-grade multi-agent
team collaboration with persistent session reuse over the Agent Client Protocol (ACP).

This standalone script simulates three distinct agents collaborating on a task:
1. PM / Architect Agent (pm-agent): Decomposes requirements, adds structured tasks, and DMs the executor.
2. Executor Agent (executor-agent): Claims Task 1 (writes math_engine.py) and Task 2 (writes tests and runs pytest)
   using the exact same persistent session (session reuse) without subprocess restarts.
3. Critic / Reviewer Agent (critic-agent): Claims Task 3 and runs ChecklistCritic with an LLM MockProvider
   to audit the files before notifying the PM that the build is ready to merge.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import subprocess
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


class MockCriticProvider(Provider):
    """A mock LLM provider designed to respond to ChecklistCritic evaluations with a positive pass score.
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
        # Construct a response text that ChecklistCritic's _parse_result parses.
        # It looks for "SCORE: <float>" and "FEEDBACK: <string>".
        content = (
            "SCORE: 1.0\n"
            "FEEDBACK: All checklist items are perfectly satisfied. "
            "The math_engine.py file implements add and subtract cleanly with appropriate docstrings, "
            "and test_math_engine.py runs valid pytest assertions that execute successfully."
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
        return "mock-critic-model"



class SimulatedACPAgent(ACPClientLike):
    """A structural implementation of ACPClientLike.

    This agent simulates a persistent process that receives prompts over ACP,
    auto-claims tasks from the shared team directory, executes the required
    logic (including file writing and pytest test execution), and marks them complete.
    """
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.team_name = cfg.env.get("CHIMERA_TEAM")
        self.agent_id = cfg.env.get("CHIMERA_AGENT")
        self.teams_home = cfg.env.get("CHIMERA_TEAMS_HOME")
        self.workspace_dir = Path(cfg.env.get("CHIMERA_WORKSPACE", ""))
        self.started = False
        
        # Track the number of tasks handled in this persistent session (to prove session reuse!)
        self.tasks_handled = 0

    def start(self) -> None:
        self.started = True
        safe_log(f"⚡ [{self.agent_id}] Persistent ACP session started.")

    def stop(self) -> None:
        self.started = False
        safe_log(f"🔌 [{self.agent_id}] Persistent ACP session stopped. Total tasks handled in session: {self.tasks_handled}")

    def send_message(self, text: str) -> object:
        if not self.started:
            raise RuntimeError("ACP session is not started!")
        
        safe_log(f"\n📨 [{self.agent_id}] Received message over persistent ACP channel.")
        
        # Instantiate the Team object using the same directory root
        teams_root_path = Path(self.teams_home) if self.teams_home else None
        team = Team(self.team_name, root=teams_root_path)
        
        # 1. Drain mailbox
        mailbox = TeamMailbox(team, self.agent_id)
        messages = mailbox.recv(drain=True)
        if messages:
            safe_log(f"📥 [{self.agent_id}] Drained mailbox messages:")
            for m in messages:
                safe_log(f"   - From {m['from']}: {m['content']}")
        else:
            safe_log(f"📥 [{self.agent_id}] Mailbox is empty.")

        # 2. Claim next available open/unblocked task suited for this agent
        open_tasks = team.list_tasks(status_filter="open")
        matching_task = None
        for t in open_tasks:
            desc = t["description"]
            if self.agent_id == "executor-agent":
                if "math_engine.py" in desc or "test_math_engine.py" in desc:
                    matching_task = t
                    break
            elif self.agent_id == "critic-agent":
                if "critic review" in desc or "critic" in desc:
                    matching_task = t
                    break

        if not matching_task:
            safe_log(f"🔍 [{self.agent_id}] No matching open tasks available to claim.")
            return {"status": "no_task"}

        task_id = matching_task["id"]
        won = team.claim_task(task_id, self.agent_id)
        if not won:
            safe_log(f"⚠️ [{self.agent_id}] Lost the race to claim task {task_id}.")
            return {"status": "race_lost"}

        desc = matching_task["description"]
        safe_log(f"🛠️ [{self.agent_id}] Claimed task {task_id}: '{desc}'")
        
        self.tasks_handled += 1

        
        # 3. Perform work based on agent role and task description
        if self.agent_id == "executor-agent":
            # check test_math_engine.py too: "math_engine.py" is a substring of it
            if "math_engine.py" in desc and "test_math_engine.py" not in desc:
                # Task 1: Create math_engine.py
                self.workspace_dir.mkdir(parents=True, exist_ok=True)
                math_file = self.workspace_dir / "math_engine.py"
                
                content = """# math_engine.py
\"\"\"A simple math engine module with addition and subtraction.
\"\"\"
from __future__ import annotations

def add(a: float, b: float) -> float:
    \"\"\"Return the sum of a and b.\"\"\"
    return a + b

def subtract(a: float, b: float) -> float:
    \"\"\"Return the difference of a and b.\"\"\"
    return a - b
"""
                math_file.write_text(content, encoding="utf-8")
                safe_log(f"💾 [{self.agent_id}] Wrote math_engine.py to {math_file}")
                
                # Complete the task
                team.complete_task(task_id, self.agent_id, result="Created math_engine.py containing add and subtract functions.")
                safe_log(f"✅ [{self.agent_id}] Completed task {task_id}.")
                
            elif "test_math_engine.py" in desc:
                # Task 2: Create test_math_engine.py and run pytest
                self.workspace_dir.mkdir(parents=True, exist_ok=True)
                test_file = self.workspace_dir / "test_math_engine.py"
                
                content = """# test_math_engine.py
\"\"\"Unit tests for the math engine.
\"\"\"
from __future__ import annotations
import pytest
from math_engine import add, subtract

def test_add() -> None:
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

def test_subtract() -> None:
    assert subtract(5, 3) == 2
    assert subtract(0, 0) == 0
    assert subtract(-1, -1) == 0
"""
                test_file.write_text(content, encoding="utf-8")
                safe_log(f"💾 [{self.agent_id}] Wrote test_math_engine.py to {test_file}")
                
                # Run pytest to verify everything is passing
                safe_log(f"🧪 [{self.agent_id}] Running pytest verification inside {self.workspace_dir}...")
                
                env = os.environ.copy()
                env["PYTHONPATH"] = str(self.workspace_dir) + os.pathsep + env.get("PYTHONPATH", "")
                
                res = subprocess.run(
                    [sys.executable, "-m", "pytest", "-v", str(test_file)],
                    cwd=self.workspace_dir,
                    env=env,
                    capture_output=True,
                    text=True
                )
                
                safe_log(f"📋 [{self.agent_id}] pytest stdout:\n{res.stdout}")
                if res.returncode == 0:
                    safe_log(f"💚 [{self.agent_id}] pytest verified successfully with 0 failures!")
                else:
                    safe_log(f"❤️ [{self.agent_id}] pytest failed (exit code {res.returncode}):\n{res.stderr}")
                    
                # Complete the task
                team.complete_task(task_id, self.agent_id, result=f"Pytest verification result:\n{res.stdout}")
                safe_log(f"✅ [{self.agent_id}] Completed task {task_id}.")
                
                # Send DM to critic
                safe_log(f"✉️ [{self.agent_id}] Sending direct message to critic-agent via TeamMailbox...")
                TeamMailbox(team, "critic-agent").send(
                    sender=self.agent_id,
                    content="Code and tests are verified and complete. Ready for critic review!"
                )
                
        elif self.agent_id == "critic-agent":
            if "critic review" in desc or "critic" in desc:
                # Task 3: Run ChecklistCritic on the generated files
                safe_log(f"🧐 [{self.agent_id}] Executing ChecklistCritic audit...")
                
                # Load file contents for real evaluation in the mock provider
                math_file = self.workspace_dir / "math_engine.py"
                test_file = self.workspace_dir / "test_math_engine.py"
                math_content = math_file.read_text(encoding="utf-8") if math_file.exists() else ""
                test_content = test_file.read_text(encoding="utf-8") if test_file.exists() else ""
                
                # Configure critic
                provider = MockCriticProvider()
                config = CriticConfig(success_threshold=0.8, critic_model="mock-critic")
                critic = ChecklistCritic(
                    checklist=[
                        "math_engine.py implements add and subtract",
                        "test_math_engine.py tests both functions thoroughly",
                        "All unit tests pass with zero failures",
                        "Files have appropriate docstrings and no hardcoded values"
                    ],
                    provider=provider,
                    config=config
                )
                
                # Setup context and action
                ctx = Context(system="You are a code quality and safety critic agent.")
                ctx.add(Message.user("Please evaluate the math_engine.py implementation and test coverage."))
                
                action_msg = Message.assistant(
                    f"Here is the code written by the executor:\n\n"
                    f"math_engine.py:\n```python\n{math_content}\n```\n\n"
                    f"test_math_engine.py:\n```python\n{test_content}\n```"
                )
                
                # Evaluate
                result = critic.evaluate(ctx, action_msg)
                
                safe_log(f"📊 [{self.agent_id}] Critic Evaluation Result:")
                safe_log(f"   Score: {result.score}")
                safe_log(f"   Passed: {result.passed}")
                safe_log(f"   Feedback: {result.feedback}")
                
                # Complete the task
                team.complete_task(
                    task_id,
                    self.agent_id,
                    result=f"Critic passed: {result.passed}, score={result.score}. Feedback: {result.feedback}"
                )
                safe_log(f"✅ [{self.agent_id}] Completed task {task_id}.")
                
                # Send DM to PM/Lead agent
                safe_log(f"✉️ [{self.agent_id}] Sending direct message to pm-agent via TeamMailbox...")
                TeamMailbox(team, "pm-agent").send(
                    sender=self.agent_id,
                    content=f"Critic audit passed with score {result.score}! Ready for merge."
                )

        return {"status": "success", "task_id": task_id}


def run_collaboration_demo() -> int:
    # 1. Create a sandboxed temporary directory for teams root and agent workspaces
    temp_dir = tempfile.TemporaryDirectory()
    teams_home = Path(temp_dir.name) / "teams"
    workspace_dir = Path(temp_dir.name) / "workspace"
    
    # Enable experimental agent teams flag in this run
    os.environ["CHIMERA_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    os.environ["CHIMERA_TEAMS_HOME"] = str(teams_home)
    
    safe_log("================================================================================")
    safe_log("🚀 INITIALIZING CHIMERA MULTI-AGENT TEAM COLLABORATION WORKFLOW DEMO")
    safe_log("================================================================================")
    safe_log(f"📁 Sandboxed Teams Home: {teams_home}")
    safe_log(f"📁 Sandboxed Workspace:  {workspace_dir}\n")
    
    # 2. PM / Architect Agent initializes the Team
    safe_log("👑 [pm-agent] Initializing 'math-collaboration-team'...")
    team = Team("math-collaboration-team", root=teams_home)
    team.init(default_model="mock-model")
    team.add_member("pm-agent")
    team.add_member("executor-agent")
    team.add_member("critic-agent")
    
    # PM / Architect Agent decomposes requirements into 3 tasks
    safe_log("👑 [pm-agent] Creating tasks in the shared queue:")
    
    task1_id = team.add_task(
        description="Create math_engine.py containing add and subtract functions.",
        created_by="pm-agent"
    )
    safe_log(f"   ➕ Added Task 1 [{task1_id}]: Create math_engine.py")
    
    task2_id = team.add_task(
        description="Create test_math_engine.py using pytest (depends on Task 1).",
        created_by="pm-agent",
        depends_on=[task1_id]
    )
    safe_log(f"   ➕ Added Task 2 [{task2_id}]: Create test_math_engine.py (Depends on Task 1)")
    
    task3_id = team.add_task(
        description="Run checklist-based critic review on the math engine (depends on Task 2).",
        created_by="pm-agent",
        depends_on=[task2_id]
    )
    safe_log(f"   ➕ Added Task 3 [{task3_id}]: Run critic review (Depends on Task 2)")
    
    # PM sends DM to executor-agent to kick off the workflow
    safe_log("👑 [pm-agent] Sending kickoff message to executor-agent via TeamMailbox...")
    TeamMailbox(team, "executor-agent").send(
        sender="pm-agent",
        content="The implementation specification has been decomposed. Ready to begin!"
    )
    
    # 3. Setup client factories that inject our simulated persistent ACP agents
    def executor_client_factory(cfg: Any) -> ACPClientLike:
        # Pass the workspace directory through the config's env dictionary
        cfg.env["CHIMERA_WORKSPACE"] = str(workspace_dir)
        return SimulatedACPAgent(cfg)
        
    def critic_client_factory(cfg: Any) -> ACPClientLike:
        cfg.env["CHIMERA_WORKSPACE"] = str(workspace_dir)
        return SimulatedACPAgent(cfg)
        
    # 4. Spawn persistent teammate runners in separate background threads
    safe_log("\n🤖 Spawning teammate runner threads (polling & persistent sessions)...")
    
    # Create threads that run the teammate poll loops
    devnull = open(os.devnull, "w")  # shared by both runners; closed after the joins
    executor_thread = threading.Thread(
        target=run_loop,
        kwargs={
            "team_name": "math-collaboration-team",
            "agent_id": "executor-agent",
            "cmd_template": "mock_executor_cmd",
            "teams_root": teams_home,
            "idle_timeout": 8.0,  # Short idle timeout for quick exit on demo completion
            "poll_interval": 1.0,
            "reuse_session": True,
            "runtime": "acp",
            "acp_client_factory": executor_client_factory,
            "log": devnull,  # keep teammate runner internal logs clean
        },
        daemon=True
    )
    
    critic_thread = threading.Thread(
        target=run_loop,
        kwargs={
            "team_name": "math-collaboration-team",
            "agent_id": "critic-agent",
            "cmd_template": "mock_critic_cmd",
            "teams_root": teams_home,
            "idle_timeout": 8.0,
            "poll_interval": 1.0,
            "reuse_session": True,
            "runtime": "acp",
            "acp_client_factory": critic_client_factory,
            "log": devnull,
        },
        daemon=True
    )

    executor_thread.start()
    critic_thread.start()
    
    # Wait for the threads to finish processing
    executor_thread.join(timeout=45)
    critic_thread.join(timeout=45)
    devnull.close()
    
    # 5. Read final messages in PM mailbox and verify the entire loop completed
    safe_log("\n👑 [pm-agent] Checking final coordination inbox...")
    pm_mailbox = TeamMailbox(team, "pm-agent")
    pm_messages = pm_mailbox.recv(drain=True)
    for m in pm_messages:
        safe_log(f"   📬 Received DM from {m['from']}: {m['content']}")
        
    # List final tasks and state
    safe_log("\n📊 Final Team Task List Status:")
    all_completed = True
    for t in team.list_tasks():
        status_marker = "✅" if t["status"] == "completed" else "❌"
        safe_log(f"   - {status_marker} Task [{t['id']}] status: {t['status']} (Claimed by: {t['claimed_by']})")
        if t["status"] != "completed":
            all_completed = False
            
    # Clean up team resources and temp directories
    safe_log("\n🧹 Gracefully tearing down teams home directory...")
    team.destroy(force=True)
    temp_dir.cleanup()
    
    if all_completed and len(pm_messages) > 0:
        safe_log("\n🎉 MULTI-AGENT TEAM COLLABORATION WORKFLOW DEMO COMPLETED SUCCESSFULLY!")
        return 0
    else:
        safe_log("\n🚨 DEMO COMPLETED WITH ISSUES (Not all tasks completed or messages received).")
        return 1


if __name__ == "__main__":
    sys.exit(run_collaboration_demo())
