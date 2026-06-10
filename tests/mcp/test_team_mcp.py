# tests/mcp/test_team_mcp.py
"""Tests for the Chimera agent-team MCP server."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.cli.agent_teams import Team, TeamMailbox
from chimera.mcp_servers.team_server import (
    DEFAULT_ROLE,
    TOOL_DEFINITIONS,
    TeamMCPServer,
    main,
    _parse_args,
)


# -- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_teams_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the teams root to a per-test temp dir."""
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
    monkeypatch.delenv("CHIMERA_ROLE", raising=False)
    return tmp_path


def _call(server: TeamMCPServer, tool: str, args: dict[str, Any], msg_id: int = 1) -> dict[str, Any]:
    """Send a tools/call and return the result payload."""
    response = server.handle_message({
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })
    assert response is not None
    res = response["result"]
    assert isinstance(res, dict)
    return res


def _parse_json_content(result: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON payload from the first content block."""
    text = result["content"][0]["text"]
    val = json.loads(text)
    assert isinstance(val, dict)
    return val


def _make_lead_server(team_name: str = "alpha") -> TeamMCPServer:
    server = TeamMCPServer(role="lead", team_name=team_name)
    server.handle_message({
        "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {},
    })
    _call(server, "team_init", {})
    return server


# -- TestProtocol ----------------------------------------------------------


class TestProtocol:
    """Cross-cutting JSON-RPC protocol behaviours."""

    def test_initialize_returns_capabilities(self) -> None:
        server = TeamMCPServer(role="lead")
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        })
        assert response is not None
        result = response["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "chimera-team-mcp"

    def test_tools_list_exposes_all_team_tools(self) -> None:
        server = TeamMCPServer(role="lead")
        response = server.handle_message({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        assert response is not None
        names = {t["name"] for t in response["result"]["tools"]}
        expected = {
            "team_init", "team_join", "team_list_members",
            "team_add_task", "team_list_tasks", "team_claim_task",
            "team_release_task", "team_complete_task",
            "team_send_message", "team_recv_messages",
            "team_propose_plan", "team_approve_plan",
        }
        assert expected.issubset(names)

    def test_every_tool_has_input_schema(self) -> None:
        for tool in TOOL_DEFINITIONS:
            assert "inputSchema" in tool, tool["name"]
            assert "properties" in tool["inputSchema"], tool["name"]

    def test_notification_returns_none(self) -> None:
        server = TeamMCPServer(role="lead")
        response = server.handle_message({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        assert response is None

    def test_unknown_method_returns_error(self) -> None:
        server = TeamMCPServer(role="lead")
        response = server.handle_message({
            "jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {},
        })
        assert response is not None
        assert response["error"]["code"] == -32601

    def test_ping(self) -> None:
        server = TeamMCPServer(role="lead")
        response = server.handle_message({
            "jsonrpc": "2.0", "id": 4, "method": "ping", "params": {},
        })
        assert response is not None
        assert "result" in response
        assert response["id"] == 4

    def test_unknown_tool_returns_iserror(self) -> None:
        server = TeamMCPServer(role="lead", team_name="alpha")
        result = _call(server, "nope_tool", {})
        assert result.get("isError") is True
        assert "Unknown tool" in result["content"][0]["text"]


# -- TestLifecycle ---------------------------------------------------------


class TestLifecycle:
    """team_init / team_join / team_list_members."""

    def test_init_creates_team_directory(self, tmp_path: Path) -> None:
        server = TeamMCPServer(role="lead", team_name="alpha")
        result = _call(server, "team_init", {})
        assert "ready" in result["content"][0]["text"]
        assert Team("alpha").exists()

    def test_init_is_idempotent(self) -> None:
        server = TeamMCPServer(role="lead", team_name="alpha")
        _call(server, "team_init", {})
        _call(server, "team_init", {})
        assert Team("alpha").exists()

    def test_init_without_name_returns_error(self) -> None:
        server = TeamMCPServer(role="lead")  # no team_name default
        result = _call(server, "team_init", {})
        assert result.get("isError") is True
        assert "'name' is required" in result["content"][0]["text"]

    def test_join_adds_member(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        members = _parse_json_content(_call(server, "team_list_members", {}))["members"]
        assert "agent-A" in members

    def test_join_is_idempotent(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        _call(server, "team_join", {"agent_id": "agent-A"})
        members = _parse_json_content(_call(server, "team_list_members", {}))["members"]
        assert members.count("agent-A") == 1

    def test_join_missing_agent_id_returns_error(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_join", {})
        assert result.get("isError") is True
        assert "agent_id" in result["content"][0]["text"]

    def test_list_members_on_missing_team(self) -> None:
        server = TeamMCPServer(role="lead", team_name="ghost")
        result = _call(server, "team_list_members", {})
        assert result.get("isError") is True
        assert "ghost" in result["content"][0]["text"]


# -- TestTasks -------------------------------------------------------------


class TestTasks:
    """team_add_task / team_list_tasks / team_claim_task / release / complete."""

    def test_add_task_returns_id(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_add_task", {"description": "write tests"})
        payload = _parse_json_content(result)
        assert isinstance(payload["task_id"], str)
        assert len(payload["task_id"]) >= 8

    def test_add_task_missing_description(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_add_task", {})
        assert result.get("isError") is True

    def test_list_tasks_default_filter(self) -> None:
        server = _make_lead_server()
        _call(server, "team_add_task", {"description": "one"})
        _call(server, "team_add_task", {"description": "two"})
        result = _call(server, "team_list_tasks", {})
        payload = _parse_json_content(result)
        assert payload["filter"] == "all"
        assert len(payload["tasks"]) == 2

    def test_list_tasks_open_filter(self) -> None:
        server = _make_lead_server()
        _call(server, "team_add_task", {"description": "one"})
        result = _call(server, "team_list_tasks", {"filter": "open"})
        payload = _parse_json_content(result)
        assert payload["filter"] == "open"
        assert all(t["status"] == "open" for t in payload["tasks"])

    def test_claim_task_specific_succeeds(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "do it"})
        )["task_id"]
        result = _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})
        payload = _parse_json_content(result)
        assert payload["claimed"] is True
        assert payload["task_id"] == tid

    def test_claim_task_specific_already_claimed(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        _call(server, "team_join", {"agent_id": "agent-B"})
        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "do it"})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})
        result = _call(server, "team_claim_task", {"agent_id": "agent-B", "task_id": tid})
        payload = _parse_json_content(result)
        assert payload["claimed"] is False
        assert "already claimed" in payload["reason"]

    def test_claim_task_auto_returns_first_open(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        first = _parse_json_content(
            _call(server, "team_add_task", {"description": "first"})
        )["task_id"]
        _parse_json_content(
            _call(server, "team_add_task", {"description": "second"})
        )
        result = _call(server, "team_claim_task", {"agent_id": "agent-A"})
        payload = _parse_json_content(result)
        assert payload["claimed"] is True
        assert payload["task_id"] == first

    def test_auto_claim_when_empty(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        result = _call(server, "team_claim_task", {"agent_id": "agent-A"})
        payload = _parse_json_content(result)
        assert payload["claimed"] is False
        assert "no unblocked" in payload["reason"]

    def test_claim_missing_agent_id(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_claim_task", {})
        assert result.get("isError") is True

    def test_release_task_returns_status_to_open(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "do it"})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})
        result = _call(server, "team_release_task", {
            "agent_id": "agent-A", "task_id": tid,
        })
        payload = _parse_json_content(result)
        assert payload["released"] is True
        # Now appears in the "open" filter again
        open_tasks = _parse_json_content(
            _call(server, "team_list_tasks", {"filter": "open"})
        )["tasks"]
        assert any(t["id"] == tid for t in open_tasks)

    def test_release_task_missing_params(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_release_task", {"agent_id": "agent-A"})
        assert result.get("isError") is True

    def test_complete_task_marks_completed(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "do it"})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})
        result = _call(server, "team_complete_task", {
            "agent_id": "agent-A", "task_id": tid, "result": "done",
        })
        payload = _parse_json_content(result)
        assert payload["completed"] is True
        completed = _parse_json_content(
            _call(server, "team_list_tasks", {"filter": "completed"})
        )["tasks"]
        assert any(t["id"] == tid and t["result"] == "done" for t in completed)

    def test_complete_task_missing_params(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_complete_task", {"agent_id": "agent-A"})
        assert result.get("isError") is True


# -- TestMessaging ---------------------------------------------------------


class TestMessaging:
    """team_send_message / team_recv_messages."""

    def test_send_and_recv_round_trip(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "alice"})
        _call(server, "team_join", {"agent_id": "bob"})
        _call(server, "team_send_message", {
            "sender": "alice", "to": "bob", "content": "ping",
        })
        result = _call(server, "team_recv_messages", {"agent_id": "bob"})
        payload = _parse_json_content(result)
        assert payload["count"] == 1
        assert payload["messages"][0]["from"] == "alice"
        assert payload["messages"][0]["content"] == "ping"

    def test_recv_drains_by_default(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "alice"})
        _call(server, "team_join", {"agent_id": "bob"})
        _call(server, "team_send_message", {
            "sender": "alice", "to": "bob", "content": "first",
        })
        first = _parse_json_content(_call(server, "team_recv_messages", {"agent_id": "bob"}))
        assert first["count"] == 1
        second = _parse_json_content(_call(server, "team_recv_messages", {"agent_id": "bob"}))
        assert second["count"] == 0

    def test_recv_peek_does_not_drain(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "alice"})
        _call(server, "team_join", {"agent_id": "bob"})
        _call(server, "team_send_message", {
            "sender": "alice", "to": "bob", "content": "first",
        })
        peeked = _parse_json_content(
            _call(server, "team_recv_messages", {"agent_id": "bob", "drain": False})
        )
        assert peeked["count"] == 1
        again = _parse_json_content(
            _call(server, "team_recv_messages", {"agent_id": "bob", "drain": False})
        )
        assert again["count"] == 1

    def test_send_missing_params_returns_error(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_send_message", {"sender": "alice", "to": "bob"})
        assert result.get("isError") is True

    def test_recv_missing_agent_id_returns_error(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_recv_messages", {})
        assert result.get("isError") is True

    def test_messaging_persists_to_mailbox_file(self, tmp_path: Path) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "alice"})
        _call(server, "team_join", {"agent_id": "bob"})
        _call(server, "team_send_message", {
            "sender": "alice", "to": "bob", "content": "via mcp",
        })
        msgs = TeamMailbox(Team("alpha"), "bob").recv()
        assert len(msgs) == 1
        assert msgs[0]["from"] == "alice"


# -- TestEntryPoint --------------------------------------------------------


class TestEntryPoint:
    """argparse + role resolution at module entry."""

    def test_default_role_is_teammate(self) -> None:
        server = TeamMCPServer()
        assert server.role == "teammate"

    def test_explicit_role_lead(self) -> None:
        server = TeamMCPServer(role="lead")
        assert server.role == "lead"

    def test_role_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHIMERA_ROLE", "lead")
        server = TeamMCPServer()
        assert server.role == "lead"

    def test_invalid_role_falls_back_to_default(self) -> None:
        server = TeamMCPServer(role="evil")
        assert server.role == DEFAULT_ROLE

    def test_parse_args_role_choices(self) -> None:
        ns = _parse_args(["--role", "lead", "--team", "alpha"])
        assert ns.role == "lead"
        assert ns.team == "alpha"

    def test_parse_args_defaults(self) -> None:
        ns = _parse_args([])
        assert ns.role is None
        assert ns.team is None

    def test_main_exported(self) -> None:
        # Smoke check: main exists and is callable. We don't actually run
        # the stdio loop here -- a separate functional test would feed it
        # stdin/stdout. This is just a wiring sanity check.
        assert callable(main)


# -- TestDependencies ------------------------------------------------------


class TestDependencies:
    """Feature 1: task dependencies."""

    def test_blocked_specific_claim_refused(self) -> None:
        """team_claim_task with task_id refuses if deps aren't completed."""
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        a = _parse_json_content(
            _call(server, "team_add_task", {"description": "build"})
        )["task_id"]
        b = _parse_json_content(
            _call(server, "team_add_task", {"description": "ship", "depends_on": [a]})
        )["task_id"]
        result = _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": b})
        payload = _parse_json_content(result)
        assert payload["claimed"] is False
        assert payload["reason"] == "blocked by deps"

    def test_auto_claim_skips_blocked(self) -> None:
        """team_claim_task with no task_id skips tasks whose deps aren't completed."""
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        a = _parse_json_content(
            _call(server, "team_add_task", {"description": "build"})
        )["task_id"]
        b = _parse_json_content(
            _call(server, "team_add_task", {"description": "ship", "depends_on": [a]})
        )["task_id"]
        # Auto-claim should pick `a` (no deps), not `b` (blocked).
        result = _call(server, "team_claim_task", {"agent_id": "agent-A"})
        payload = _parse_json_content(result)
        assert payload["claimed"] is True
        assert payload["task_id"] == a
        assert payload["task_id"] != b

    def test_deps_satisfied_unblocks_task(self) -> None:
        """Once a dependency is completed, the dependent task is claimable."""
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})
        a = _parse_json_content(
            _call(server, "team_add_task", {"description": "build"})
        )["task_id"]
        b = _parse_json_content(
            _call(server, "team_add_task", {"description": "ship", "depends_on": [a]})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": a})
        _call(server, "team_complete_task", {
            "agent_id": "agent-A", "task_id": a, "result": "ok",
        })
        result = _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": b})
        payload = _parse_json_content(result)
        assert payload["claimed"] is True
        assert payload["task_id"] == b

    def test_blocked_filter_lists_blocked_tasks(self) -> None:
        """team_list_tasks(filter='blocked') returns open tasks with unresolved deps."""
        server = _make_lead_server()
        a = _parse_json_content(
            _call(server, "team_add_task", {"description": "build"})
        )["task_id"]
        b = _parse_json_content(
            _call(server, "team_add_task", {"description": "ship", "depends_on": [a]})
        )["task_id"]
        result = _call(server, "team_list_tasks", {"filter": "blocked"})
        payload = _parse_json_content(result)
        ids = [t["id"] for t in payload["tasks"]]
        assert b in ids
        assert a not in ids

    def test_open_filter_excludes_blocked(self) -> None:
        """team_list_tasks(filter='open') excludes blocked tasks; 'open_all' includes them."""
        server = _make_lead_server()
        a = _parse_json_content(
            _call(server, "team_add_task", {"description": "build"})
        )["task_id"]
        b = _parse_json_content(
            _call(server, "team_add_task", {"description": "ship", "depends_on": [a]})
        )["task_id"]
        open_only = _parse_json_content(
            _call(server, "team_list_tasks", {"filter": "open"})
        )
        ids_open = [t["id"] for t in open_only["tasks"]]
        assert a in ids_open
        assert b not in ids_open

        open_all = _parse_json_content(
            _call(server, "team_list_tasks", {"filter": "open_all"})
        )
        ids_all = [t["id"] for t in open_all["tasks"]]
        assert a in ids_all
        assert b in ids_all

    def test_depends_on_persists_on_record(self) -> None:
        """The depends_on list is persisted on the task record."""
        server = _make_lead_server()
        a = _parse_json_content(
            _call(server, "team_add_task", {"description": "build"})
        )["task_id"]
        b = _parse_json_content(
            _call(server, "team_add_task", {"description": "ship", "depends_on": [a]})
        )["task_id"]
        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == b)
        assert rec["depends_on"] == [a]

    def test_depends_on_default_is_empty_list(self) -> None:
        """Tasks added without depends_on default to []."""
        server = _make_lead_server()
        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "lonely"})
        )["task_id"]
        rec = next(t for t in Team("alpha").list_tasks() if t["id"] == tid)
        assert rec["depends_on"] == []

    def test_depends_on_invalid_type_returns_error(self) -> None:
        server = _make_lead_server()
        result = _call(server, "team_add_task", {
            "description": "x", "depends_on": "not-a-list",
        })
        assert result.get("isError") is True

    def test_add_task_schema_includes_depends_on(self) -> None:
        add_task = next(t for t in TOOL_DEFINITIONS if t["name"] == "team_add_task")
        assert "depends_on" in add_task["inputSchema"]["properties"]
        spec = add_task["inputSchema"]["properties"]["depends_on"]
        assert spec["type"] == "array"
        assert spec["items"]["type"] == "string"

    def test_list_tasks_schema_includes_blocked_and_open_all(self) -> None:
        list_tasks = next(t for t in TOOL_DEFINITIONS if t["name"] == "team_list_tasks")
        enum = list_tasks["inputSchema"]["properties"]["filter"]["enum"]
        assert "blocked" in enum
        assert "open_all" in enum
        assert "open" in enum


# -- TestRoleGating --------------------------------------------------------


class TestRoleGating:
    """Feature 2: lead/teammate role gating."""

    def test_teammate_blocked_from_add_task(self) -> None:
        """Role=teammate causes team_add_task to return isError."""
        server = TeamMCPServer(role="teammate", team_name="alpha")
        # Bootstrap the team out-of-band (init is itself role-gated only on
        # add_task; init/join are fine for teammates).
        _call(server, "team_init", {})
        result = _call(server, "team_add_task", {"description": "x"})
        assert result.get("isError") is True
        text = result["content"][0]["text"]
        assert "only lead can add tasks" in text
        assert "--role lead" in text
        assert "CHIMERA_ROLE=lead" in text

    def test_lead_can_add_tasks(self) -> None:
        """Role=lead allows team_add_task as before."""
        server = TeamMCPServer(role="lead", team_name="alpha")
        _call(server, "team_init", {})
        result = _call(server, "team_add_task", {"description": "x"})
        payload = _parse_json_content(result)
        assert isinstance(payload["task_id"], str)
        # And it actually landed:
        tasks = Team("alpha").list_tasks()
        assert any(t["id"] == payload["task_id"] for t in tasks)


# -- TestPlanApproval ------------------------------------------------------


class TestPlanApproval:
    """Feature 3: plan-approval workflow."""

    def test_propose_approve_roundtrip(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})

        # Add a task that requires a plan
        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "needs plan", "requires_plan": True})
        )["task_id"]

        # Check task record
        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == tid)
        assert rec["requires_plan"] is True
        assert rec["plan_status"] is None

        # Claim task
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})

        # Propose a plan
        prop_res = _parse_json_content(
            _call(server, "team_propose_plan", {"agent_id": "agent-A", "task_id": tid, "plan": "Do X, then Y."})
        )
        assert prop_res["proposed"] is True

        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == tid)
        assert rec["plan_status"] == "pending"
        assert rec["proposed_plan"] == "Do X, then Y."
        assert rec["plan_feedback"] is None

        # Approve the plan (as lead)
        app_res = _parse_json_content(
            _call(server, "team_approve_plan", {"task_id": tid, "decision": "approve"})
        )
        assert app_res["approved"] is True

        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == tid)
        assert rec["plan_status"] == "approved"
        assert rec["plan_feedback"] is None

    def test_reject_revise_resubmit(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})

        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "needs plan", "requires_plan": True})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})

        # Propose initial plan
        _call(server, "team_propose_plan", {"agent_id": "agent-A", "task_id": tid, "plan": "Weak plan."})

        # Reject the plan (as lead) with feedback
        app_res = _parse_json_content(
            _call(server, "team_approve_plan", {"task_id": tid, "decision": "reject", "feedback": "Too weak."})
        )
        assert app_res["approved"] is True

        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == tid)
        assert rec["plan_status"] == "rejected"
        assert rec["plan_feedback"] == "Too weak."

        # Propose revised plan
        _call(server, "team_propose_plan", {"agent_id": "agent-A", "task_id": tid, "plan": "Stronger plan."})

        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == tid)
        assert rec["plan_status"] == "pending"
        assert rec["proposed_plan"] == "Stronger plan."
        assert rec["plan_feedback"] is None

    def test_complete_refuses_pre_approval(self) -> None:
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})

        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "needs plan", "requires_plan": True})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})

        # Try to complete before plan proposed
        comp_res = _parse_json_content(
            _call(server, "team_complete_task", {"agent_id": "agent-A", "task_id": tid, "result": "sneaky"})
        )
        assert comp_res["completed"] is False
        assert comp_res["reason"] == "plan requires approval"

        # Propose plan (status = pending)
        _call(server, "team_propose_plan", {"agent_id": "agent-A", "task_id": tid, "plan": "Some plan."})

        # Try to complete after proposing but before approval
        comp_res = _parse_json_content(
            _call(server, "team_complete_task", {"agent_id": "agent-A", "task_id": tid, "result": "sneaky"})
        )
        assert comp_res["completed"] is False
        assert comp_res["reason"] == "plan requires approval"

        # Reject plan
        _call(server, "team_approve_plan", {"task_id": tid, "decision": "reject", "feedback": "no"})

        # Try to complete after rejection
        comp_res = _parse_json_content(
            _call(server, "team_complete_task", {"agent_id": "agent-A", "task_id": tid, "result": "sneaky"})
        )
        assert comp_res["completed"] is False
        assert comp_res["reason"] == "plan requires approval"

        # Approve plan
        _call(server, "team_approve_plan", {"task_id": tid, "decision": "approve"})

        # Completion should now succeed
        comp_res = _parse_json_content(
            _call(server, "team_complete_task", {"agent_id": "agent-A", "task_id": tid, "result": "done"})
        )
        assert comp_res["completed"] is True

    def test_auto_approve_env_var_skips_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHIMERA_AUTO_APPROVE_PLANS", "1")
        server = _make_lead_server()
        _call(server, "team_join", {"agent_id": "agent-A"})

        tid = _parse_json_content(
            _call(server, "team_add_task", {"description": "needs plan", "requires_plan": True})
        )["task_id"]
        _call(server, "team_claim_task", {"agent_id": "agent-A", "task_id": tid})

        # Propose plan
        prop_res = _parse_json_content(
            _call(server, "team_propose_plan", {"agent_id": "agent-A", "task_id": tid, "plan": "Immediate plan."})
        )
        assert prop_res["proposed"] is True

        # Plan should be auto-approved
        tasks = Team("alpha").list_tasks()
        rec = next(t for t in tasks if t["id"] == tid)
        assert rec["plan_status"] == "approved"

        # Completion should succeed immediately
        comp_res = _parse_json_content(
            _call(server, "team_complete_task", {"agent_id": "agent-A", "task_id": tid, "result": "done"})
        )
        assert comp_res["completed"] is True
