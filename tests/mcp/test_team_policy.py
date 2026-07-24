"""Tests for per-teammate permission propagation (issue #150).

Four things are load-bearing and each has coverage here:

* A policy configured by the lead is what a teammate resolves — the
  teammate does not get to pick its own.
* Translation into a runtime's own dialect is data-driven and errors
  loudly for a runtime it cannot speak, rather than launching a teammate
  at an unknown posture.
* In-process enforcement is real: a Chimera teammate's disallowed tool
  call is *blocked*, and the denial lands in the team audit where
  ``chimera team status`` reports it.
* With nothing configured, behavior is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.cli.agent_teams import Team, TeamAudit, create_team, list_teams
from chimera.mcp_servers.team_policy import (
    POLICY_DANGEROUS,
    POLICY_READ_ONLY,
    POLICY_WORKSPACE_WRITE,
    RuntimeAdapter,
    WorkspaceWrite,
    apply_policy_args,
    base_tool_name,
    detect_runtime,
    is_coordination_tool,
    parse_policy,
    permission_policy_for,
    team_interceptors_from_env,
    team_policy_interceptor,
    translate_policy,
)
from chimera.permissions.base import PermissionAction
from chimera.types import ToolCall


class TestParsing:
    def test_canonical_names_round_trip(self) -> None:
        for value in (POLICY_READ_ONLY, POLICY_WORKSPACE_WRITE, POLICY_DANGEROUS):
            assert parse_policy(value) == value

    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("readonly", POLICY_READ_ONLY),
            ("read_only", POLICY_READ_ONLY),
            ("workspace_write", POLICY_WORKSPACE_WRITE),
            ("full", POLICY_DANGEROUS),
            ("YOLO", POLICY_DANGEROUS),
        ],
    )
    def test_aliases(self, alias: str, expected: str) -> None:
        assert parse_policy(alias) == expected

    def test_unknown_policy_is_loud(self) -> None:
        with pytest.raises(ValueError, match="unknown team policy"):
            parse_policy("mostly-safe")


class TestToolNaming:
    def test_base_tool_name_strips_the_mcp_namespace(self) -> None:
        assert base_tool_name("mcp__chimera-team__team_claim_task") == (
            "team_claim_task"
        )

    def test_base_tool_name_passes_a_plain_name_through(self) -> None:
        assert base_tool_name("write_file") == "write_file"

    def test_is_coordination_tool_matches_both_spellings(self) -> None:
        assert is_coordination_tool("team_claim_task")
        assert is_coordination_tool("mcp__chimera-team__team_claim_task")

    def test_is_coordination_tool_rejects_other_tools(self) -> None:
        assert not is_coordination_tool("write_file")
        assert not is_coordination_tool("mcp__other__write_file")


class TestTeamPolicyRecord:
    def test_a_new_team_has_no_policy(self, tmp_path: Path) -> None:
        team = Team("plain", root=tmp_path)
        team.init()

        assert team.policy is None

    def test_policy_survives_a_reopen(self, tmp_path: Path) -> None:
        team = Team("posture", root=tmp_path)
        team.init(policy="read-only")

        assert Team("posture", root=tmp_path).policy == POLICY_READ_ONLY

    def test_set_and_clear(self, tmp_path: Path) -> None:
        team = Team("posture", root=tmp_path)
        team.init()
        team.set_policy("workspace-write")
        assert team.policy == POLICY_WORKSPACE_WRITE

        team.set_policy(None)
        assert team.policy is None

    def test_set_rejects_an_unknown_policy(self, tmp_path: Path) -> None:
        team = Team("posture", root=tmp_path)
        team.init()
        with pytest.raises(ValueError):
            team.set_policy("whatever")

    def test_init_on_an_existing_team_updates_the_policy(
        self, tmp_path: Path,
    ) -> None:
        team = Team("posture", root=tmp_path)
        team.init()
        team.init(policy="dangerous")

        assert team.policy == POLICY_DANGEROUS

    def test_create_team_takes_a_policy(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        team = create_team("seeded", policy="read-only")

        assert team.policy == POLICY_READ_ONLY

    def test_list_teams_reports_the_policy(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        create_team("listed", policy="workspace-write")

        rows = list_teams(root=tmp_path)
        assert [r["policy"] for r in rows] == [POLICY_WORKSPACE_WRITE]


class TestPermissionMapping:
    def test_read_only_denies_writes_and_shell(self) -> None:
        policy = permission_policy_for(POLICY_READ_ONLY)

        assert policy.evaluate("read_file", {"path": "a.py"}) is PermissionAction.ALLOW
        assert policy.evaluate("write_file", {"path": "a.py"}) is PermissionAction.DENY
        assert policy.evaluate("bash", {"command": "ls"}) is PermissionAction.DENY

    def test_dangerous_allows_everything(self) -> None:
        policy = permission_policy_for(POLICY_DANGEROUS)

        assert policy.evaluate("bash", {"command": "rm -rf /"}) is PermissionAction.ALLOW

    def test_workspace_write_needs_a_root(self) -> None:
        with pytest.raises(ValueError, match="at least one allowed root"):
            permission_policy_for(POLICY_WORKSPACE_WRITE)


class TestWorkspaceWrite:
    def test_write_inside_the_root_is_allowed(self, tmp_path: Path) -> None:
        policy = WorkspaceWrite([tmp_path])

        action = policy.evaluate("write_file", {"path": "src/app.py"})
        assert action is PermissionAction.ALLOW

    def test_write_outside_the_root_is_denied(self, tmp_path: Path) -> None:
        policy = WorkspaceWrite([tmp_path / "project"])
        (tmp_path / "project").mkdir()

        action = policy.evaluate("write_file", {"path": "/etc/hosts"})
        assert action is PermissionAction.DENY

    def test_traversal_out_of_the_root_is_denied(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        policy = WorkspaceWrite([root])

        action = policy.evaluate("edit_file", {"path": "../secrets.txt"})
        assert action is PermissionAction.DENY

    def test_extra_roots_are_honoured(self, tmp_path: Path) -> None:
        # The teams home is exactly this case: the coordination server has
        # to write there or every team_* call fails mysteriously.
        project = tmp_path / "project"
        teams = tmp_path / "teams"
        project.mkdir()
        teams.mkdir()
        policy = WorkspaceWrite([project, teams])

        assert policy.evaluate(
            "write_file", {"path": str(teams / "x" / "y.jsonl")},
        ) is PermissionAction.ALLOW

    def test_reads_and_shell_are_allowed(self, tmp_path: Path) -> None:
        policy = WorkspaceWrite([tmp_path])

        assert policy.evaluate("read_file", {"path": "/etc/hosts"}) is PermissionAction.ALLOW
        assert policy.evaluate("bash", {"command": "pytest"}) is PermissionAction.ALLOW

    def test_a_write_with_no_readable_path_is_denied(self, tmp_path: Path) -> None:
        policy = WorkspaceWrite([tmp_path])

        assert policy.evaluate("write_file", {}) is PermissionAction.DENY


class TestRuntimeTranslation:
    def test_detect_runtime_uses_the_first_token(self) -> None:
        assert detect_runtime("/opt/bin/some-agent exec {prompt}") == "some-agent"
        assert detect_runtime("") == ""

    def test_builtin_chimera_adapter_needs_no_flags(self, tmp_path: Path) -> None:
        # A Chimera teammate binds itself in-process from the env var, so
        # there is nothing to splice into its command line.
        translation = translate_policy(
            "read-only", "chimera", workspace=tmp_path, teams_home=tmp_path,
        )

        assert translation.policy == POLICY_READ_ONLY
        assert translation.args == ()
        assert translation.args_string == ""

    def test_unknown_runtime_errors_clearly(self) -> None:
        with pytest.raises(ValueError, match="no policy translation for runtime"):
            translate_policy("read-only", "not-a-configured-runtime")

    def test_adapter_from_config_renders_placeholders(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        adapter = RuntimeAdapter.from_config(
            "demo",
            {
                "read-only": "--sandbox read-only",
                "workspace-write":
                    "--sandbox write --add-dir {workspace} --add-dir {teams_home}",
                "dangerous": ["--no-sandbox"],
            },
        )
        monkeypatch.setattr(
            "chimera.mcp_servers.team_policy.load_runtime_adapters",
            lambda: {"demo": adapter},
        )

        workspace = tmp_path / "ws"
        teams = tmp_path / "teams"
        workspace.mkdir()
        teams.mkdir()
        translation = translate_policy(
            "workspace-write", "demo", workspace=workspace, teams_home=teams,
        )

        assert translation.args == (
            "--sandbox", "write",
            "--add-dir", str(workspace.resolve()),
            "--add-dir", str(teams.resolve()),
        )

    def test_adapter_from_config_rejects_an_unknown_policy_key(self) -> None:
        with pytest.raises(ValueError, match="unknown team policy"):
            RuntimeAdapter.from_config("demo", {"sorta-safe": "--x"})

    def test_adapter_from_config_rejects_a_bad_value_type(self) -> None:
        with pytest.raises(ValueError, match="must be a string or a list"):
            RuntimeAdapter.from_config("demo", {"read-only": 7})

    def test_adapter_env_overlay(self, tmp_path: Path, monkeypatch) -> None:
        adapter = RuntimeAdapter.from_config(
            "demo", {"env": {"read-only": {"DEMO_SANDBOX": "ro"}}},
        )
        monkeypatch.setattr(
            "chimera.mcp_servers.team_policy.load_runtime_adapters",
            lambda: {"demo": adapter},
        )

        translation = translate_policy(
            "read-only", "demo", workspace=tmp_path, teams_home=tmp_path,
        )
        assert dict(translation.env) == {"DEMO_SANDBOX": "ro"}

    def test_apply_policy_args_substitutes_the_placeholder(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        adapter = RuntimeAdapter.from_config("demo", {"read-only": "--sandbox ro"})
        monkeypatch.setattr(
            "chimera.mcp_servers.team_policy.load_runtime_adapters",
            lambda: {"demo": adapter},
        )
        translation = translate_policy(
            "read-only", "demo", workspace=tmp_path, teams_home=tmp_path,
        )

        cmd = apply_policy_args("demo {policy_args} run '{prompt}'", translation)
        assert cmd == "demo --sandbox ro run '{prompt}'"

    def test_apply_policy_args_leaves_a_placeholderless_command_alone(
        self, tmp_path: Path,
    ) -> None:
        translation = translate_policy(
            "read-only", "chimera", workspace=tmp_path, teams_home=tmp_path,
        )

        assert apply_policy_args("chimera code -p x", translation) == "chimera code -p x"


class TestInterceptorEnforcement:
    def _call(self, name: str, **args: object) -> ToolCall:
        return ToolCall(id="tc-1", name=name, arguments=dict(args))

    def test_read_only_blocks_a_write(self, tmp_path: Path) -> None:
        intercept = team_policy_interceptor(POLICY_READ_ONLY)

        decision = intercept(self._call("write_file", path="a.py", content="x"))
        assert decision is not None
        assert decision.kind == "block"
        assert "read-only" in decision.reason

    def test_read_only_allows_a_read(self, tmp_path: Path) -> None:
        intercept = team_policy_interceptor(POLICY_READ_ONLY)

        assert intercept(self._call("read_file", path="a.py")) is None

    def test_coordination_tools_are_never_blocked(self, tmp_path: Path) -> None:
        # A read-only teammate that cannot claim its task is not safe,
        # it is broken. Every posture allows team_*.
        intercept = team_policy_interceptor(POLICY_READ_ONLY)

        assert intercept(self._call("team_claim_task", agent_id="w1")) is None
        assert intercept(self._call("team_complete_task", task_id="t1")) is None

    def test_namespaced_coordination_tools_are_never_blocked(
        self, tmp_path: Path,
    ) -> None:
        # REGRESSION (found by the live run in #151): a teammate reaches
        # the coordination server over MCP, so the loop sees
        # mcp__chimera-team__team_claim_task. Prefix-testing the bare
        # name blocked every coordination call and stranded the teammate.
        intercept = team_policy_interceptor(POLICY_READ_ONLY)

        for tool in (
            "mcp__chimera-team__team_recv_messages",
            "mcp__chimera-team__team_claim_task",
            "mcp__chimera-team__team_list_tasks",
            "mcp__chimera-team__team_complete_task",
        ):
            assert intercept(self._call(tool)) is None, tool

    def test_a_namespaced_non_team_tool_is_still_governed(
        self, tmp_path: Path,
    ) -> None:
        # The allowance is for coordination, not for "anything over MCP".
        intercept = team_policy_interceptor(POLICY_READ_ONLY)

        decision = intercept(self._call("mcp__other__delete_everything"))
        assert decision is not None and decision.kind == "block"

    def test_dangerous_blocks_nothing(self, tmp_path: Path) -> None:
        intercept = team_policy_interceptor(POLICY_DANGEROUS)

        assert intercept(self._call("bash", command="rm -rf /tmp/x")) is None

    def test_workspace_write_blocks_an_escaping_write(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        intercept = team_policy_interceptor(POLICY_WORKSPACE_WRITE, roots=[root])

        assert intercept(self._call("write_file", path="in.py", content="")) is None
        blocked = intercept(self._call("write_file", path="/etc/hosts", content=""))
        assert blocked is not None and blocked.kind == "block"

    def test_denials_land_in_the_team_audit(self, tmp_path: Path) -> None:
        team = Team("audited", root=tmp_path)
        team.init(policy="read-only")
        intercept = team_policy_interceptor(
            POLICY_READ_ONLY, team=team, agent_id="worker-1",
        )

        intercept(self._call("write_file", path="a.py", content="x"))

        entries = TeamAudit(team).entries()
        assert len(entries) == 1
        assert entries[0]["agent_id"] == "worker-1"
        assert entries[0]["tool"] == "write_file"
        assert entries[0]["decision"] == "denied"
        assert entries[0]["policy"] == POLICY_READ_ONLY
        assert TeamAudit(team).summary() == {"denied": 1}

    def test_audit_filters_by_agent(self, tmp_path: Path) -> None:
        team = Team("audited", root=tmp_path)
        team.init()
        audit = TeamAudit(team)
        audit.record("a", "write_file", "denied")
        audit.record("b", "bash", "denied")

        assert [e["agent_id"] for e in audit.entries(agent_id="b")] == ["b"]

    def test_allowed_calls_are_not_audited(self, tmp_path: Path) -> None:
        team = Team("audited", root=tmp_path)
        team.init()
        intercept = team_policy_interceptor(
            POLICY_READ_ONLY, team=team, agent_id="worker-1",
        )

        intercept(self._call("read_file", path="a.py"))

        assert TeamAudit(team).entries() == []


class TestEnvPropagation:
    def test_no_policy_means_no_interceptors(self, monkeypatch) -> None:
        # The unchanged-by-default guarantee.
        monkeypatch.delenv("CHIMERA_TEAM_POLICY", raising=False)

        assert team_interceptors_from_env() is None

    def test_policy_from_env_builds_a_tool_call_chain(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        monkeypatch.setenv("CHIMERA_TEAM", "envteam")
        monkeypatch.setenv("CHIMERA_AGENT", "worker-1")
        monkeypatch.setenv("CHIMERA_TEAM_POLICY", "read-only")
        Team("envteam", root=tmp_path).init(policy="read-only")

        interceptors = team_interceptors_from_env(tmp_path)
        assert interceptors is not None
        assert len(interceptors.tool_call) == 1

        decision = interceptors.tool_call[0](
            ToolCall(id="t", name="write_file", arguments={"path": "a.py"}),
        )
        assert decision is not None and decision.kind == "block"

    def test_invalid_env_policy_is_loud(self, monkeypatch) -> None:
        monkeypatch.setenv("CHIMERA_TEAM_POLICY", "sorta-safe")

        with pytest.raises(ValueError, match="unknown team policy"):
            team_interceptors_from_env()

    def test_workspace_write_from_env_allows_the_teams_home(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        teams = tmp_path / "teams"
        workspace = tmp_path / "ws"
        teams.mkdir()
        workspace.mkdir()
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(teams))
        monkeypatch.setenv("CHIMERA_TEAM_POLICY", "workspace-write")

        interceptors = team_interceptors_from_env(workspace)
        assert interceptors is not None
        intercept = interceptors.tool_call[0]

        assert intercept(ToolCall(
            id="t", name="write_file",
            arguments={"path": str(teams / "team-a" / "task_list.jsonl")},
        )) is None
        blocked = intercept(ToolCall(
            id="t", name="write_file", arguments={"path": str(tmp_path / "outside.txt")},
        ))
        assert blocked is not None and blocked.kind == "block"


class TestEnforcementThroughTheLoop:
    def test_a_blocked_write_never_touches_the_filesystem(
        self, tmp_path: Path,
    ) -> None:
        # End-to-end through the real agent loop: the model asks to write,
        # the team policy blocks it, and no file appears.
        from chimera.testing import create_harness

        workspace = tmp_path / "ws"
        workspace.mkdir()
        team = Team("looped", root=tmp_path / "teams")
        team.init(policy="read-only")

        harness = create_harness(
            [
                {"text": "writing", "tool_calls": [
                    {"name": "write_file",
                     "arguments": {"path": "leak.txt", "content": "nope"}},
                ]},
                {"text": "done"},
            ],
            workspace=workspace,
            config={
                "interceptors": __import__(
                    "chimera.core.interception", fromlist=["Interceptors"],
                ).Interceptors(
                    tool_call=[team_policy_interceptor(
                        POLICY_READ_ONLY, team=team, agent_id="worker-1",
                    )],
                ),
            },
        )
        harness.run("write a file")

        assert not (workspace / "leak.txt").exists()
        assert TeamAudit(team).summary() == {"denied": 1}


class TestCLISurface:
    def test_status_reports_policy_and_denials(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from chimera.mink.team import main as team_main

        monkeypatch.setenv("CHIMERA_EXPERIMENTAL_AGENT_TEAMS", "1")
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        team = create_team("statusteam", policy="read-only")
        TeamAudit(team).record("worker-1", "write_file", "denied", "blocked")

        assert team_main(["team", "status", "statusteam"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["policy"] == POLICY_READ_ONLY
        assert payload["policy_decisions"] == {"denied": 1}

    def test_create_accepts_a_policy(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from chimera.mink.team import main as team_main

        monkeypatch.setenv("CHIMERA_EXPERIMENTAL_AGENT_TEAMS", "1")
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))

        assert team_main([
            "team", "create", "policed", "--policy", "workspace-write",
        ]) == 0
        assert "workspace-write" in capsys.readouterr().out
        assert Team("policed", root=tmp_path).policy == POLICY_WORKSPACE_WRITE

    def test_policy_subcommand_shows_and_sets(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from chimera.mink.team import main as team_main

        monkeypatch.setenv("CHIMERA_EXPERIMENTAL_AGENT_TEAMS", "1")
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        create_team("posture")

        assert team_main(["team", "policy", "posture"]) == 0
        assert capsys.readouterr().out.strip() == "none"

        assert team_main(["team", "policy", "posture", "read-only"]) == 0
        assert "read-only" in capsys.readouterr().out
        assert Team("posture", root=tmp_path).policy == POLICY_READ_ONLY

        assert team_main(["team", "policy", "posture", "none"]) == 0
        assert Team("posture", root=tmp_path).policy is None

    def test_audit_subcommand_lists_denials(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from chimera.mink.team import main as team_main

        monkeypatch.setenv("CHIMERA_EXPERIMENTAL_AGENT_TEAMS", "1")
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        team = create_team("audited")
        TeamAudit(team).record("worker-1", "bash", "denied", "team policy")

        assert team_main(["team", "audit", "audited"]) == 0
        out = capsys.readouterr().out
        assert "denied" in out
        assert "worker-1" in out

    def test_audit_subcommand_on_an_empty_trail(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from chimera.mink.team import main as team_main

        monkeypatch.setenv("CHIMERA_EXPERIMENTAL_AGENT_TEAMS", "1")
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
        create_team("quiet")

        assert team_main(["team", "audit", "quiet"]) == 0
        assert "no policy decisions recorded" in capsys.readouterr().out
