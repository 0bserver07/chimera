"""Tests for all new features: Tasks 59-68.

Covers: sandbox enforcement, interactive approval, project config discovery,
microagents, AgentController, trajectory logging, diff proposals,
smart compaction, tool-use streaming (via existing provider), codebase index.
"""
from __future__ import annotations

import json
import os
import tempfile
from io import StringIO
from unittest.mock import MagicMock

import pytest

from chimera.types import Message, ToolCall


# ===================================================================
# Task 59: Sandbox enforcement in DockerEnvironment
# ===================================================================

class TestSandboxEnforcement:

    def test_build_container_kwargs_no_sandbox(self):
        """Without sandbox, default kwargs are returned."""
        from chimera.env.docker import DockerEnvironment
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._image = "python:3.11-slim"
        env._workdir = "/workspace"
        env._sandbox = None
        kwargs = env._build_container_kwargs()
        assert kwargs["image"] == "python:3.11-slim"
        assert "network_mode" not in kwargs
        assert "mem_limit" not in kwargs

    def test_build_container_kwargs_network_denied(self):
        """All-deny network rules set network_mode=none."""
        from chimera.env.docker import DockerEnvironment
        from chimera.security.sandbox import NetworkRule, SandboxPolicy
        policy = SandboxPolicy(
            network_rules=[NetworkRule(host="*", allow=False)],
        )
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._image = "python:3.11-slim"
        env._workdir = "/workspace"
        env._sandbox = policy
        kwargs = env._build_container_kwargs()
        assert kwargs["network_mode"] == "none"

    def test_build_container_kwargs_memory_limit(self):
        """Memory limit from sandbox maps to Docker mem_limit."""
        from chimera.env.docker import DockerEnvironment
        from chimera.security.sandbox import SandboxPolicy
        policy = SandboxPolicy(max_memory_mb=512)
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._image = "python:3.11-slim"
        env._workdir = "/workspace"
        env._sandbox = policy
        kwargs = env._build_container_kwargs()
        assert kwargs["mem_limit"] == "512m"

    def test_build_container_kwargs_process_limit(self):
        """Process limit from sandbox maps to Docker pids_limit."""
        from chimera.env.docker import DockerEnvironment
        from chimera.security.sandbox import SandboxPolicy
        policy = SandboxPolicy(max_processes=100)
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._image = "python:3.11-slim"
        env._workdir = "/workspace"
        env._sandbox = policy
        kwargs = env._build_container_kwargs()
        assert kwargs["pids_limit"] == 100

    def test_build_container_kwargs_read_only(self):
        """No write path rules sets read_only=True with tmpfs."""
        from chimera.env.docker import DockerEnvironment
        from chimera.security.sandbox import SandboxPolicy, PathRule, AccessLevel
        # Policy with only read rules (no write)
        policy = SandboxPolicy(
            path_rules=[PathRule(path="/workspace", access=AccessLevel.READ)],
        )
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._image = "python:3.11-slim"
        env._workdir = "/workspace"
        env._sandbox = policy
        kwargs = env._build_container_kwargs()
        assert kwargs.get("read_only") is True
        assert "/tmp" in kwargs.get("tmpfs", {})


# ===================================================================
# Task 60: Interactive approval UX
# ===================================================================

class TestInteractiveApproval:

    def test_auto_deny_non_interactive(self):
        from chimera.permissions.interactive import InteractiveApprover, PermissionAction
        approver = InteractiveApprover(interactive=False)
        result = approver.prompt("bash", {"command": "rm -rf /"})
        assert result.action == PermissionAction.DENY

    def test_approve_yes(self):
        from chimera.permissions.interactive import InteractiveApprover, PermissionAction
        approver = InteractiveApprover(
            interactive=True,
            output=StringIO(),
            input_fn=lambda: "y",
        )
        result = approver.prompt("read_file", {"path": "test.py"})
        assert result.action == PermissionAction.ALLOW
        assert not result.always

    def test_approve_always(self):
        from chimera.permissions.interactive import InteractiveApprover, ApprovalMemory, PermissionAction
        memory = ApprovalMemory()
        approver = InteractiveApprover(
            interactive=True,
            memory=memory,
            output=StringIO(),
            input_fn=lambda: "a",
        )
        result = approver.prompt("read_file", {"path": "test.py"})
        assert result.action == PermissionAction.ALLOW
        assert result.always
        assert memory.is_always_allowed("read_file")

    def test_memory_auto_allows(self):
        from chimera.permissions.interactive import InteractiveApprover, ApprovalMemory, PermissionAction
        memory = ApprovalMemory()
        memory.remember_allow("bash")
        approver = InteractiveApprover(interactive=True, memory=memory, output=StringIO())
        result = approver.prompt("bash", {"command": "ls"})
        assert result.action == PermissionAction.ALLOW

    def test_deny(self):
        from chimera.permissions.interactive import InteractiveApprover, PermissionAction
        approver = InteractiveApprover(
            interactive=True, output=StringIO(), input_fn=lambda: "n",
        )
        result = approver.prompt("bash", {"command": "rm -rf /"})
        assert result.action == PermissionAction.DENY


# ===================================================================
# Task 61: Project config auto-discovery
# ===================================================================

class TestProjectConfigDiscovery:

    def test_discover_chimera_md(self, tmp_path):
        from chimera.config.project_discovery import discover_config
        config_file = tmp_path / "CHIMERA.md"
        config_file.write_text("---\nmodel: glm-5\n---\nAlways use pytest.\n")
        result = discover_config(tmp_path)
        assert result is not None
        assert result.model == "glm-5"
        assert "pytest" in result.instructions

    def test_discover_claude_md(self, tmp_path):
        from chimera.config.project_discovery import discover_config
        config_file = tmp_path / "CLAUDE.md"
        config_file.write_text("---\nmodel: claude-sonnet\n---\nBe concise.\n")
        result = discover_config(tmp_path)
        assert result is not None
        assert result.model == "claude-sonnet"

    def test_discover_walks_up(self, tmp_path):
        from chimera.config.project_discovery import discover_config
        config_file = tmp_path / "CHIMERA.md"
        config_file.write_text("---\nmodel: test\n---\nInstructions.\n")
        subdir = tmp_path / "src" / "app"
        subdir.mkdir(parents=True)
        result = discover_config(subdir)
        assert result is not None
        assert result.model == "test"

    def test_discover_returns_none_when_missing(self, tmp_path):
        from chimera.config.project_discovery import discover_config
        result = discover_config(tmp_path)
        assert result is None

    def test_no_frontmatter(self, tmp_path):
        from chimera.config.project_discovery import discover_config
        config_file = tmp_path / "CHIMERA.md"
        config_file.write_text("Just plain instructions.\n")
        result = discover_config(tmp_path)
        assert result is not None
        assert result.model is None
        assert "plain instructions" in result.instructions

    def test_discover_all_configs(self, tmp_path):
        from chimera.config.project_discovery import discover_all_configs
        (tmp_path / "CHIMERA.md").write_text("---\nmodel: parent\n---\nParent.\n")
        child = tmp_path / "child"
        child.mkdir()
        (child / "CHIMERA.md").write_text("---\nmodel: child\n---\nChild.\n")
        configs = discover_all_configs(child)
        assert len(configs) >= 2
        assert configs[0].model == "child"  # Most specific first


# ===================================================================
# Task 62: Microagent spawning
# ===================================================================

class TestMicroagent:

    def test_spawn_basic(self):
        from chimera.agents.microagent import MicroagentConfig, MicroagentSpawner
        provider = MagicMock()
        response = MagicMock()
        response.content = "Task completed."
        response.tool_calls = []
        response.has_tool_calls = False
        response.usage = {"input_tokens": 10, "output_tokens": 5}
        provider.complete.return_value = response
        provider.model_name = "test"

        spawner = MicroagentSpawner(provider=provider, available_tools=[])
        config = MicroagentConfig(name="helper", task="Say hello", max_steps=2)
        result = spawner.spawn(config)
        assert result.success
        assert result.output == "Task completed."

    def test_spawn_with_tool_subset(self):
        from chimera.agents.microagent import MicroagentConfig, MicroagentSpawner
        from chimera.tools.read import ReadFileTool
        from chimera.tools.bash import BashTool

        provider = MagicMock()
        response = MagicMock()
        response.content = "Done"
        response.tool_calls = []
        response.has_tool_calls = False
        response.usage = {"input_tokens": 10, "output_tokens": 5}
        provider.complete.return_value = response
        provider.model_name = "test"

        tools = [ReadFileTool(), BashTool()]
        spawner = MicroagentSpawner(provider=provider, available_tools=tools)
        config = MicroagentConfig(name="reader", task="Read files", tools=["read_file"])
        result = spawner.spawn(config)
        assert result.success

    def test_spawn_many(self):
        from chimera.agents.microagent import MicroagentConfig, MicroagentSpawner
        provider = MagicMock()
        response = MagicMock()
        response.content = "Done"
        response.tool_calls = []
        response.has_tool_calls = False
        response.usage = {"input_tokens": 10, "output_tokens": 5}
        provider.complete.return_value = response
        provider.model_name = "test"

        spawner = MicroagentSpawner(provider=provider, available_tools=[])
        configs = [
            MicroagentConfig(name="a", task="Task A"),
            MicroagentConfig(name="b", task="Task B"),
        ]
        results = spawner.spawn_many(configs)
        assert len(results) == 2
        assert all(r.success for r in results)


# ===================================================================
# Task 63: AgentController state machine
# ===================================================================

class TestAgentController:

    def test_initial_state(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        assert ctrl.state == AgentState.INIT

    def test_valid_transition(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        ctrl.transition_to(AgentState.PLANNING)
        assert ctrl.state == AgentState.PLANNING
        ctrl.transition_to(AgentState.EXECUTING)
        assert ctrl.state == AgentState.EXECUTING

    def test_invalid_transition_raises(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        with pytest.raises(ValueError, match="Invalid transition"):
            ctrl.transition_to(AgentState.REVIEWING)

    def test_history_recorded(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        ctrl.transition_to(AgentState.PLANNING)
        ctrl.transition_to(AgentState.EXECUTING)
        ctrl.transition_to(AgentState.DONE)
        assert len(ctrl.history) == 3
        assert ctrl.history[0].from_state == AgentState.INIT
        assert ctrl.history[2].to_state == AgentState.DONE

    def test_terminal_state(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        assert not ctrl.is_terminal
        ctrl.transition_to(AgentState.PLANNING)
        ctrl.transition_to(AgentState.DONE)
        assert ctrl.is_terminal

    def test_hooks_fire(self):
        from chimera.core.controller import AgentController, AgentState
        entered = []
        exited = []
        ctrl = AgentController()
        ctrl.on_enter(AgentState.PLANNING, lambda c: entered.append("planning"))
        ctrl.on_exit(AgentState.INIT, lambda c: exited.append("init"))
        ctrl.transition_to(AgentState.PLANNING)
        assert "planning" in entered
        assert "init" in exited

    def test_serialize_deserialize(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        ctrl.transition_to(AgentState.PLANNING)
        ctrl.transition_to(AgentState.EXECUTING)
        data = ctrl.to_dict()
        restored = AgentController.from_dict(data)
        assert restored.state == AgentState.EXECUTING
        assert len(restored.history) == 2

    def test_can_transition_to(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        assert ctrl.can_transition_to(AgentState.PLANNING)
        assert not ctrl.can_transition_to(AgentState.DONE)

    def test_error_can_reset(self):
        from chimera.core.controller import AgentController, AgentState
        ctrl = AgentController()
        ctrl.transition_to(AgentState.EXECUTING)
        ctrl.transition_to(AgentState.ERROR)
        assert ctrl.is_terminal
        ctrl.transition_to(AgentState.INIT)
        assert ctrl.state == AgentState.INIT


# ===================================================================
# Task 64: Trajectory logging
# ===================================================================

class TestTrajectory:

    def test_create_and_finalize(self):
        from chimera.core.trajectory import Trajectory, TrajectoryStep
        traj = Trajectory(task="Fix the bug", agent_name="coder", model="glm-5")
        traj.add_step(TrajectoryStep(step=1, cost=0.01, model_response="Reading file"))
        traj.add_step(TrajectoryStep(step=2, cost=0.02, model_response="Editing file"))
        traj.finalize(success=True, output="Bug fixed")
        assert traj.success
        assert len(traj.steps) == 2
        assert traj.total_cost == pytest.approx(0.03)

    def test_save_and_load_json(self, tmp_path):
        from chimera.core.trajectory import Trajectory, TrajectoryStep
        traj = Trajectory(task="Test task", model="test")
        traj.add_step(TrajectoryStep(step=1, cost=0.01))
        traj.finalize(success=True, output="Done")
        path = tmp_path / "traj.json"
        traj.save(path)
        loaded = Trajectory.load(path)
        assert loaded.task == "Test task"
        assert loaded.success
        assert len(loaded.steps) == 1

    def test_save_and_load_jsonl(self, tmp_path):
        from chimera.core.trajectory import Trajectory, TrajectoryStep
        traj = Trajectory(task="JSONL test", agent_name="agent-1", model="m")
        traj.add_step(TrajectoryStep(step=1, cost=0.005))
        traj.add_step(TrajectoryStep(step=2, cost=0.010))
        traj.finalize(success=False, output="Failed")
        path = tmp_path / "traj.jsonl"
        traj.save_jsonl(path)
        loaded = Trajectory.load_jsonl(path)
        assert loaded.task == "JSONL test"
        assert not loaded.success
        assert len(loaded.steps) == 2

    def test_filter_successful(self):
        from chimera.core.trajectory import Trajectory, filter_successful
        trajs = [
            Trajectory(task="a", success=True),
            Trajectory(task="b", success=False),
            Trajectory(task="c", success=True),
        ]
        filtered = filter_successful(trajs)
        assert len(filtered) == 2

    def test_sort_by_cost(self):
        from chimera.core.trajectory import Trajectory, sort_by_cost
        trajs = [
            Trajectory(task="expensive", total_cost=1.0),
            Trajectory(task="cheap", total_cost=0.1),
            Trajectory(task="mid", total_cost=0.5),
        ]
        sorted_t = sort_by_cost(trajs)
        assert sorted_t[0].task == "cheap"
        assert sorted_t[2].task == "expensive"


# ===================================================================
# Task 65: Diff proposal workflow
# ===================================================================

class TestDiffProposal:

    def test_create_and_accept_all(self):
        from chimera.core.proposed_edit import EditProposal, EditStatus
        proposal = EditProposal()
        proposal.add("a.py", "old", "new", "Fix bug")
        proposal.add("b.py", "", "new file", "Add file")
        assert len(proposal.pending) == 2
        proposal.accept_all()
        assert len(proposal.accepted) == 2
        assert len(proposal.pending) == 0

    def test_reject_all(self):
        from chimera.core.proposed_edit import EditProposal, EditStatus
        proposal = EditProposal()
        proposal.add("a.py", "old", "new")
        proposal.reject_all()
        assert len(proposal.pending) == 0
        assert proposal.edits[0].status == EditStatus.REJECTED

    def test_accept_reject_individual(self):
        from chimera.core.proposed_edit import EditProposal, EditStatus
        proposal = EditProposal()
        proposal.add("a.py", "old", "new")
        proposal.add("b.py", "old", "new")
        proposal.accept(0)
        proposal.reject(1)
        assert proposal.edits[0].status == EditStatus.ACCEPTED
        assert proposal.edits[1].status == EditStatus.REJECTED

    def test_unified_diff(self):
        from chimera.core.proposed_edit import ProposedEdit
        edit = ProposedEdit(path="calc.py", original="return a - b\n", proposed="return a + b\n")
        diff = edit.unified_diff()
        assert "--- a/calc.py" in diff
        assert "+++ b/calc.py" in diff
        assert "-return a - b" in diff
        assert "+return a + b" in diff

    def test_apply_to_env(self):
        from chimera.core.proposed_edit import EditProposal
        env = MagicMock()
        proposal = EditProposal()
        proposal.add("a.py", "old", "new content")
        proposal.add("b.py", "old", "new content")
        proposal.accept_all()
        applied = proposal.apply(env)
        assert len(applied) == 2
        assert env.write_file.call_count == 2

    def test_summary(self):
        from chimera.core.proposed_edit import EditProposal
        proposal = EditProposal()
        proposal.add("new.py", "", "content", "New file")
        proposal.add("mod.py", "old", "new", "Modify")
        summary = proposal.summary()
        assert "new.py" in summary
        assert "mod.py" in summary
        assert "new" in summary.lower()

    def test_is_new_file(self):
        from chimera.core.proposed_edit import ProposedEdit
        edit = ProposedEdit(path="new.py", original="", proposed="content")
        assert edit.is_new_file
        assert not edit.is_deletion

    def test_stat(self):
        from chimera.core.proposed_edit import ProposedEdit
        edit = ProposedEdit(path="a.py", original="line1\nline2\n", proposed="line1\nline3\nline4\n")
        stat = edit.stat()
        assert stat["additions"] >= 1
        assert stat["deletions"] >= 1


# ===================================================================
# Task 66: Smart context compaction
# ===================================================================

class TestSmartCompaction:

    def test_no_compaction_when_small(self):
        from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig
        compaction = SmartCompaction(SmartCompactionConfig(preserve_recent=5))
        msgs = [Message.user("Hi"), Message.assistant("Hello")]
        result = compaction.compact(msgs, budget=4000)
        assert len(result) == 2

    def test_compacts_older_messages(self):
        from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig
        compaction = SmartCompaction(SmartCompactionConfig(preserve_recent=2))
        msgs = [
            Message.user("Step 1"),
            Message.assistant("Response 1"),
            Message.user("Step 2"),
            Message.assistant("Response 2"),
            Message.user("Step 3"),
            Message.assistant("Response 3"),
        ]
        result = compaction.compact(msgs, budget=4000)
        # Should have: 1 summary + 2 recent
        assert len(result) == 3
        assert result[0].role == "system"  # Summary
        assert "[Conversation summary]" in result[0].content
        # Last 2 messages preserved
        assert result[1].content == "Step 3"
        assert result[2].content == "Response 3"

    def test_summary_includes_tool_calls(self):
        from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig
        compaction = SmartCompaction(SmartCompactionConfig(preserve_recent=1))
        msgs = [
            Message.user("Write code"),
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc1", name="write_file", arguments={"path": "a.py"}),
            ]),
            Message(role="tool", content="File written", call_id="tc1"),
            Message.user("Run tests"),
        ]
        result = compaction.compact(msgs, budget=4000)
        summary = result[0].content
        assert "write_file" in summary


# ===================================================================
# Task 68: Codebase indexing
# ===================================================================

class TestCodebaseIndex:

    def test_index_and_search(self, tmp_path):
        from chimera.tools.codebase_index import CodebaseIndex
        (tmp_path / "auth.py").write_text("def login(user, password):\n    return authenticate(user)\n")
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (tmp_path / "README.md").write_text("# Project\nA calculator and auth system.\n")

        index = CodebaseIndex()
        count = index.index_directory(tmp_path)
        assert count == 3

        results = index.search("authentication login")
        assert len(results) >= 1
        assert results[0].path == "auth.py"

    def test_index_single_file(self):
        from chimera.tools.codebase_index import CodebaseIndex
        index = CodebaseIndex()
        index.index_file("test.py", "def test_add():\n    assert add(1, 2) == 3\n")
        assert index.file_count == 1
        results = index.search("test add")
        assert len(results) == 1

    def test_remove_file(self):
        from chimera.tools.codebase_index import CodebaseIndex
        index = CodebaseIndex()
        index.index_file("a.py", "some code")
        index.index_file("b.py", "other code")
        assert index.file_count == 2
        index.remove_file("a.py")
        assert index.file_count == 1

    def test_search_no_results(self):
        from chimera.tools.codebase_index import CodebaseIndex
        index = CodebaseIndex()
        index.index_file("a.py", "hello world")
        results = index.search("xyzzy_nonexistent")
        assert len(results) == 0

    def test_semantic_search_tool(self, tmp_path):
        from chimera.tools.codebase_index import CodebaseIndex, SemanticSearchTool
        index = CodebaseIndex()
        index.index_file("auth.py", "def login(user, password):\n    authenticate(user)\n")
        index.index_file("calc.py", "def add(a, b):\n    return a + b\n")
        tool = SemanticSearchTool(index=index)
        result = tool.execute({"query": "login user"}, env=None)
        assert result.error is None
        assert "auth.py" in result.output

    def test_skips_hidden_dirs(self, tmp_path):
        from chimera.tools.codebase_index import CodebaseIndex
        (tmp_path / "visible.py").write_text("code")
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("secret")
        index = CodebaseIndex()
        count = index.index_directory(tmp_path)
        assert count == 1
