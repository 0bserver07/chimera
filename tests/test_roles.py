"""Tests for chimera.composition.roles — RoleBasedTeam multi-agent composition."""

from __future__ import annotations

import tempfile

from chimera.composition.roles import (
    CODER,
    DEFAULT_ROLES,
    PLANNER,
    REVIEWER,
    TESTER,
    Role,
    RoleBasedTeam,
    _create_loop,
    _filter_tools,
)
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StubProvider(Provider):
    """Provider that records what it receives and returns canned content.

    Each call returns ``"{label}: {last_user_message_excerpt}"``.
    """

    def __init__(self, label: str = "stub"):
        self.label = label
        self.calls: list[list[Message]] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.calls.append(list(messages))
        last_user = ""
        for m in reversed(messages):
            if m.role == "user":
                last_user = m.content[:120]
                break
        return Response(
            content=f"{self.label}: {last_user}",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 10},
        )

    @property
    def context_window(self):
        return 100_000

    @property
    def supports_tool_use(self):
        return False

    @property
    def model_name(self):
        return f"stub-{self.label}"


class SequenceProvider(Provider):
    """Returns a different label on each call to track role ordering.

    Identifies roles by unique keywords in the system prompt (messages[0]).
    """

    def __init__(self):
        self._step = 0
        self.call_labels: list[str] = []
        self.system_prompts: list[str] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        system_msg = messages[0].content if messages else ""
        self.system_prompts.append(system_msg[:80])

        # Use unique keywords to distinguish roles
        if "software architect" in system_msg:
            label = "PLAN_OUTPUT"
        elif "expert coder" in system_msg:
            label = "CODE_OUTPUT"
        elif "code reviewer" in system_msg:
            label = "REVIEW_OUTPUT"
        elif "QA engineer" in system_msg:
            label = "TEST_OUTPUT"
        else:
            label = f"STEP_{self._step}"
        self.call_labels.append(label)
        return Response(
            content=f"[{label}] done",
            tool_calls=[],
            usage={"input_tokens": 15, "output_tokens": 15},
        )

    @property
    def context_window(self):
        return 100_000

    @property
    def supports_tool_use(self):
        return False

    @property
    def model_name(self):
        return "sequence"


class DummyTool(BaseTool):
    """Minimal tool for testing tool filtering."""

    def __init__(self, tool_name: str):
        self.name = tool_name
        self.description = f"Dummy {tool_name}"
        self.parameters = {"type": "object", "properties": {}}

    def execute(self, arguments: dict, env: Environment | None = None) -> ToolResult:
        return ToolResult(output=f"{self.name} executed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRoleDataclass:
    def test_defaults(self):
        role = Role(name="demo", description="A demo role")
        assert role.name == "demo"
        assert role.description == "A demo role"
        assert role.tool_names == []
        assert role.system_prompt == ""
        assert role.loop_type == "react"
        assert role.max_steps == 20

    def test_custom_fields(self):
        role = Role(
            name="analyst",
            description="Analyses requirements",
            tool_names=["read_file", "search"],
            system_prompt="You are an analyst.",
            loop_type="plan_act",
            max_steps=10,
        )
        assert role.name == "analyst"
        assert role.tool_names == ["read_file", "search"]
        assert role.loop_type == "plan_act"
        assert role.max_steps == 10

    def test_builtin_roles_exist(self):
        assert PLANNER.name == "planner"
        assert CODER.name == "coder"
        assert REVIEWER.name == "reviewer"
        assert TESTER.name == "tester"
        assert len(DEFAULT_ROLES) == 4

    def test_planner_is_read_only(self):
        write_tools = {"write_file", "edit_file", "bash", "replace_in_file"}
        assert not write_tools.intersection(PLANNER.tool_names), (
            "Planner should not have write tools"
        )

    def test_coder_has_write_tools(self):
        assert "write_file" in CODER.tool_names
        assert "edit_file" in CODER.tool_names
        assert "bash" in CODER.tool_names


class TestTeamWithDefaults:
    """Verify the default planner -> coder -> reviewer -> tester flow."""

    def test_default_pipeline_order(self):
        provider = SequenceProvider()
        team = RoleBasedTeam(
            provider=provider,
            tools=[DummyTool(n) for n in [
                "read_file", "search", "list_files", "repo_map", "think",
                "write_file", "edit_file", "bash", "replace_in_file",
                "test",
            ]],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = team.run("Build a feature", env)

        assert result.success
        # The PlanActLoop for the planner may call provider multiple times
        # (plan phase + act phase).  We check that the *first* occurrence
        # of each role label appears in the correct order.
        labels = provider.call_labels
        assert len(labels) >= 4, f"Expected at least 4 calls, got {labels}"

        # Find first occurrence indices (use -1 for missing as sentinel)
        def first_idx(keyword: str) -> int:
            return next((i for i, l in enumerate(labels) if keyword in l), -1)

        plan_idx = first_idx("PLAN")
        code_idx = first_idx("CODE")
        review_idx = first_idx("REVIEW")
        test_idx = first_idx("TEST")

        assert plan_idx >= 0, f"PLAN not found in {labels}"
        assert code_idx >= 0, f"CODE not found in {labels}"
        assert review_idx >= 0, f"REVIEW not found in {labels}"
        assert test_idx >= 0, f"TEST not found in {labels}"
        assert plan_idx < code_idx < review_idx < test_idx, (
            f"Expected PLAN < CODE < REVIEW < TEST, got "
            f"{plan_idx} < {code_idx} < {review_idx} < {test_idx} from {labels}"
        )

    def test_default_aggregates_cost(self):
        provider = StubProvider("role")
        team = RoleBasedTeam(
            provider=provider,
            tools=[DummyTool("read_file"), DummyTool("think")],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = team.run("Do something", env)

        assert result.success
        # 4 roles, each contributing at least 1 step
        assert result.steps >= 4
        assert result.cost >= 0.0


class TestTeamCustomRoles:
    def test_single_custom_role(self):
        custom = Role(
            name="solo",
            description="Does everything alone",
            tool_names=["read_file"],
            system_prompt="You are a solo agent.",
            loop_type="react",
            max_steps=5,
        )
        provider = StubProvider("solo")
        team = RoleBasedTeam(
            provider=provider,
            roles=[custom],
            tools=[DummyTool("read_file")],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = team.run("Simple task", env)

        assert result.success
        assert result.steps >= 1

    def test_two_custom_roles(self):
        writer = Role(
            name="writer",
            description="Writes content",
            tool_names=["write_file"],
            system_prompt="You are a writer.",
        )
        editor = Role(
            name="editor",
            description="Edits content",
            tool_names=["read_file"],
            system_prompt="You are an editor.",
        )
        provider = StubProvider("custom")
        team = RoleBasedTeam(
            provider=provider,
            roles=[writer, editor],
            tools=[DummyTool("write_file"), DummyTool("read_file")],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = team.run("Write and edit", env)

        assert result.success
        assert result.steps >= 2

    def test_add_role(self):
        provider = StubProvider("add")
        team = RoleBasedTeam(
            provider=provider,
            roles=[],
            tools=[DummyTool("think")],
        )
        role = Role(name="added", description="Added later", tool_names=["think"],
                     system_prompt="You were added.")
        team.add_role(role)
        assert len(team.roles) == 1
        assert team.roles[0].name == "added"


class TestTeamSingleRole:
    def test_coder_only(self):
        provider = StubProvider("coder")
        team = RoleBasedTeam(
            provider=provider,
            roles=[CODER],
            tools=[
                DummyTool("read_file"), DummyTool("write_file"),
                DummyTool("edit_file"), DummyTool("bash"),
                DummyTool("search"), DummyTool("list_files"),
                DummyTool("replace_in_file"), DummyTool("think"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = team.run("Write a function", env)

        assert result.success
        assert "coder" in result.output.lower() or len(result.output) > 0

    def test_no_roles_returns_error(self):
        provider = StubProvider("empty")
        team = RoleBasedTeam(provider=provider, roles=[], tools=[])
        result = team.run("Nothing to do", None)
        assert not result.success
        assert result.error == "No roles configured"


class TestRoleHasCorrectTools:
    def test_planner_gets_read_only_tools(self):
        all_tools = [
            DummyTool("read_file"), DummyTool("search"), DummyTool("list_files"),
            DummyTool("repo_map"), DummyTool("think"),
            DummyTool("write_file"), DummyTool("edit_file"), DummyTool("bash"),
        ]
        filtered = _filter_tools(all_tools, PLANNER.tool_names)
        filtered_names = {t.name for t in filtered}
        assert filtered_names == {"read_file", "search", "list_files", "repo_map", "think"}
        assert "write_file" not in filtered_names
        assert "bash" not in filtered_names

    def test_coder_gets_write_tools(self):
        all_tools = [
            DummyTool("read_file"), DummyTool("write_file"), DummyTool("edit_file"),
            DummyTool("bash"), DummyTool("search"), DummyTool("list_files"),
            DummyTool("replace_in_file"), DummyTool("think"),
            DummyTool("test"), DummyTool("repo_map"),
        ]
        filtered = _filter_tools(all_tools, CODER.tool_names)
        filtered_names = {t.name for t in filtered}
        assert "write_file" in filtered_names
        assert "edit_file" in filtered_names
        assert "bash" in filtered_names
        # Coder should not get tools not in its list
        assert "test" not in filtered_names
        assert "repo_map" not in filtered_names

    def test_tester_gets_test_tool(self):
        all_tools = [
            DummyTool("read_file"), DummyTool("bash"), DummyTool("test"),
            DummyTool("search"), DummyTool("list_files"), DummyTool("think"),
            DummyTool("write_file"),
        ]
        filtered = _filter_tools(all_tools, TESTER.tool_names)
        filtered_names = {t.name for t in filtered}
        assert "test" in filtered_names
        assert "bash" in filtered_names
        assert "write_file" not in filtered_names

    def test_filter_preserves_order(self):
        all_tools = [DummyTool("c"), DummyTool("a"), DummyTool("b")]
        filtered = _filter_tools(all_tools, ["a", "b", "c"])
        assert [t.name for t in filtered] == ["c", "a", "b"]

    def test_missing_tools_silently_skipped(self):
        all_tools = [DummyTool("read_file")]
        filtered = _filter_tools(all_tools, ["read_file", "nonexistent"])
        assert len(filtered) == 1
        assert filtered[0].name == "read_file"


class TestTeamPassesContextBetweenRoles:
    """Verify that output from one role feeds into the next role's input."""

    def test_second_role_receives_first_output(self):
        """The coder should receive the planner's output in its context."""

        class TrackingProvider(Provider):
            def __init__(self):
                self._step = 0
                self.received_messages: list[list[Message]] = []

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self._step += 1
                self.received_messages.append(list(messages))
                return Response(
                    content=f"output_from_step_{self._step}",
                    tool_calls=[],
                    usage={"input_tokens": 10, "output_tokens": 10},
                )

            @property
            def context_window(self):
                return 100_000

            @property
            def supports_tool_use(self):
                return False

            @property
            def model_name(self):
                return "tracking"

        role_a = Role(name="first", description="First", tool_names=[],
                      system_prompt="You are first.", max_steps=5)
        role_b = Role(name="second", description="Second", tool_names=[],
                      system_prompt="You are second.", max_steps=5)

        provider = TrackingProvider()
        team = RoleBasedTeam(
            provider=provider,
            roles=[role_a, role_b],
            tools=[],
        )

        result = team.run("original task", None)
        assert result.success

        # The second role's call should contain the first role's output
        # Provider is called at least twice (once per role)
        assert len(provider.received_messages) >= 2

        # The second call's user message should contain output from step 1
        second_call_messages = provider.received_messages[1]
        user_msgs = [m for m in second_call_messages if m.role == "user"]
        assert len(user_msgs) >= 1
        combined_user_text = " ".join(m.content for m in user_msgs)
        assert "output_from_step_1" in combined_user_text
        assert "original task" in combined_user_text

    def test_third_role_sees_all_prior_outputs(self):
        """The third role should see outputs from both first and second roles."""

        class IndexProvider(Provider):
            def __init__(self):
                self._call = 0
                self.user_inputs: list[str] = []

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self._call += 1
                user_msg = ""
                for m in messages:
                    if m.role == "user":
                        user_msg += m.content + " "
                self.user_inputs.append(user_msg.strip())
                return Response(
                    content=f"result_{self._call}",
                    tool_calls=[],
                    usage={"input_tokens": 5, "output_tokens": 5},
                )

            @property
            def context_window(self):
                return 100_000

            @property
            def supports_tool_use(self):
                return False

            @property
            def model_name(self):
                return "index"

        roles = [
            Role(name="alpha", description="Alpha", system_prompt="Alpha.", max_steps=3),
            Role(name="beta", description="Beta", system_prompt="Beta.", max_steps=3),
            Role(name="gamma", description="Gamma", system_prompt="Gamma.", max_steps=3),
        ]

        provider = IndexProvider()
        team = RoleBasedTeam(provider=provider, roles=roles, tools=[])
        result = team.run("task X", None)

        assert result.success

        # Third role (gamma) should see both alpha's and beta's outputs
        gamma_input = provider.user_inputs[2]
        assert "result_1" in gamma_input, "Gamma should see alpha's output"
        assert "result_2" in gamma_input, "Gamma should see beta's output"
        assert "task X" in gamma_input, "Gamma should see original task"


class TestCreateLoop:
    def test_react(self):
        loop = _create_loop("react", 10)
        assert isinstance(loop, ReAct)
        assert loop.max_steps == 10

    def test_plan_act(self):
        from chimera.core.loops.plan_act import PlanActLoop
        loop = _create_loop("plan_act", 15)
        assert isinstance(loop, PlanActLoop)

    def test_retry(self):
        from chimera.core.loops.retry import RetryLoop
        loop = _create_loop("retry", 10)
        assert isinstance(loop, RetryLoop)

    def test_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="Unknown loop_type"):
            _create_loop("nonexistent", 10)


class TestBuildAgent:
    def test_agent_has_role_properties(self):
        provider = StubProvider("build")
        team = RoleBasedTeam(
            provider=provider,
            roles=[CODER],
            tools=[DummyTool("read_file"), DummyTool("write_file"),
                   DummyTool("edit_file"), DummyTool("bash"),
                   DummyTool("search"), DummyTool("list_files"),
                   DummyTool("replace_in_file"), DummyTool("think")],
        )
        agent = team._build_agent(CODER)
        assert agent.name == "coder"
        assert isinstance(agent, Agent)
        tool_names = {t.name for t in agent.tools}
        assert tool_names == set(CODER.tool_names)
