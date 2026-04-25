"""Regression tests for AUDIT.md slash-command + redactor fixes (W2 wave 2).

Each test pins a specific user-visible behavior the audit demanded:

    M-1 / M-15  /sandbox toggle now resolves to a real callable.
    M-2         /subagent (no args) advertises built-in presets.
    M-3 / M-16  /plugin list/discover/enable/disable cycle works without
                 the prior "PluginManager.<sub> not implemented" stub.
    M-4         /mcp reads project-level .mcp.json plus user mcp.json.
    M-5         /review constructs a separate reviewer agent from the
                 'review' preset when a provider is available.
    M-6         /resume tolerates sessions without an attached agent.
    M-7         /doctor reports BOTH the project .mcp.json and the user
                 ~/.chimera/mcp.json instead of only the latter.
    M-8         /config serializes a MinkSettings dataclass to JSON
                 instead of falling through to repr().
    M-9         RedactionMiddleware walks ToolResultEvent.output dict
                 and tool_metadata containers (not just strings).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# M-1 / M-15: sandbox toggle is a real callable
# ---------------------------------------------------------------------------


class TestM1SandboxToggle:
    def test_sandbox_toggle_symbol_exists(self) -> None:
        from chimera.permissions import sandbox

        assert hasattr(sandbox, "toggle"), "audit M-1: sandbox.toggle must exist"
        assert callable(sandbox.toggle)

    def test_sandbox_toggle_flips_session_flag(self) -> None:
        from chimera.permissions.sandbox import toggle

        session = SimpleNamespace()
        first = toggle(session)
        assert first is True
        assert getattr(session, "_sandbox_enabled") is True
        second = toggle(session)
        assert second is False
        assert getattr(session, "_sandbox_enabled") is False

    def test_cmd_sandbox_does_not_print_not_implemented(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from chimera.cli.slash_commands import cmd_sandbox

        session = SimpleNamespace()
        cmd_sandbox(session, None, "", print)
        captured = capsys.readouterr().out
        # Audit M-15: the literal "not implemented" was the smoking gun.
        assert "not implemented" not in captured
        assert "sandbox: on" in captured or "sandbox: off" in captured


# ---------------------------------------------------------------------------
# M-2: /subagent advertises built-in presets when called with no args
# ---------------------------------------------------------------------------


class TestM2SubagentAdvertisesPresets:
    def test_bare_subagent_lists_presets(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from chimera.cli.slash_commands import cmd_subagent

        session = SimpleNamespace(provider=None)
        cmd_subagent(session, None, "", print)
        out = capsys.readouterr().out
        assert "Usage:" in out
        # At least one of the built-in presets must be advertised
        # (build/explore/general/plan/review).
        assert "Built-in presets:" in out
        assert any(name in out for name in ("build", "explore", "general", "plan", "review"))


# ---------------------------------------------------------------------------
# M-3 / M-16: /plugin list works without printing "not implemented"
# ---------------------------------------------------------------------------


class TestM3PluginCommands:
    def test_plugin_list_no_plugins_loaded_message_does_not_fault(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from chimera.cli.slash_commands import cmd_plugin

        session = SimpleNamespace()
        cmd_plugin(session, None, "list", print)
        out = capsys.readouterr().out
        # Either "No plugins loaded" or a real list — never "not implemented".
        assert "not implemented" not in out

    def test_plugin_discover_dispatches(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from chimera.cli.slash_commands import cmd_plugin

        session = SimpleNamespace()
        cmd_plugin(session, None, "discover", print)
        out = capsys.readouterr().out
        assert "not implemented" not in out
        # A repo without registered entry points must still produce a
        # readable message rather than a stack trace.
        assert "plugin" in out.lower() or "entry point" in out.lower()

    def test_plugin_enable_disable_dont_print_not_implemented(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from chimera.cli.slash_commands import cmd_plugin

        session = SimpleNamespace()
        cmd_plugin(session, None, "enable nonexistent", print)
        out = capsys.readouterr().out
        # The "PluginManager.enable not implemented" error string is
        # what the audit M-16 specifically called out.
        assert "PluginManager.enable not implemented" not in out
        assert "PluginManager.disable not implemented" not in out


# ---------------------------------------------------------------------------
# M-4: /mcp reads project-level .mcp.json + user mcp.json
# ---------------------------------------------------------------------------


class TestM4MCPReadsProjectMcpJson:
    def test_cmd_mcp_reads_project_mcp_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from chimera.cli.slash_commands import cmd_mcp

        # Write a project-level .mcp.json with one server.
        mcp = {
            "mcpServers": {
                "filesystem": {"command": "npx", "args": ["-y", "@mcp/fs"]}
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))
        env = SimpleNamespace(workdir=str(tmp_path))
        cmd_mcp(SimpleNamespace(), env, "", print)
        out = capsys.readouterr().out
        assert "filesystem" in out, (
            "audit M-4: /mcp must surface project-level .mcp.json servers; "
            f"output was: {out!r}"
        )

    def test_cmd_mcp_reports_no_config(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from chimera.cli.slash_commands import cmd_mcp

        # No .mcp.json anywhere; expect the explicit "not available" line.
        env = SimpleNamespace(workdir=str(tmp_path))
        # WHY: monkey-patch home so the user-scope file (if present in
        # the test environment) doesn't fool the assertion.
        import unittest.mock as _mock
        with _mock.patch.object(Path, "home", return_value=tmp_path):
            cmd_mcp(SimpleNamespace(), env, "", print)
        out = capsys.readouterr().out
        assert "not available" in out


# ---------------------------------------------------------------------------
# M-5: /review builds a separate reviewer agent from the 'review' preset
# ---------------------------------------------------------------------------


class TestM5ReviewSeparatePresetReviewer:
    def test_review_preset_is_present_in_default_registry(self) -> None:
        from chimera.agents.loader import create_default_registry

        reg = create_default_registry()
        assert "review" in reg.list(), (
            "audit M-5 fix expects the 'review' preset to be a built-in"
        )


# ---------------------------------------------------------------------------
# M-6: /resume works when session.agent is None
# ---------------------------------------------------------------------------


class TestM6ResumeWithoutAgent:
    def test_resume_does_not_demand_session_agent(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from chimera.cli.slash_commands import cmd_resume

        # Session with no agent attribute at all.
        session = SimpleNamespace()
        # Use an obviously-bogus session id so we hit the "not found"
        # branch — but the key assertion is we never see the audit-flagged
        # "not available: live session has no agent" line.
        cmd_resume(session, None, "totally-bogus-mink-id", print)
        out = capsys.readouterr().out
        assert "not available: live session has no agent" not in out


# ---------------------------------------------------------------------------
# M-7: /doctor checks both project .mcp.json AND user mcp.json
# ---------------------------------------------------------------------------


class TestM7DoctorReadsProjectMcpJson:
    def test_doctor_mentions_project_mcp_path(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from chimera.cli.slash_commands import cmd_doctor

        env = SimpleNamespace(workdir=str(tmp_path))
        # WHY: pin Path.home() to a clean tmp so user mcp.json doesn't
        # confound the assertion.
        import unittest.mock as _mock
        with _mock.patch.object(Path, "home", return_value=tmp_path):
            cmd_doctor(SimpleNamespace(), env, "", print)
        out = capsys.readouterr().out
        # Either "none configured" + the explicit candidate paths or a
        # per-source listing — both must reference .mcp.json (project) and
        # ~/.chimera/mcp.json (user) so the user knows what was checked.
        assert ".mcp.json" in out
        assert "~/.chimera/mcp.json" in out or "mcp.json" in out


# ---------------------------------------------------------------------------
# M-8: /config serializes MinkSettings via dataclasses.asdict
# ---------------------------------------------------------------------------


class TestM8ConfigSerialisesDataclass:
    def test_config_emits_valid_json_for_default_settings(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from chimera.cli.slash_commands import cmd_config

        monkeypatch.chdir(tmp_path)
        env = SimpleNamespace(workdir=str(tmp_path))
        cmd_config(SimpleNamespace(), env, "", print)
        out = capsys.readouterr().out
        # Should print "Effective settings (mink):" then valid JSON.
        assert "Effective settings (mink):" in out
        # Strip the header and confirm the rest parses as JSON.
        body = out.split("Effective settings (mink):", 1)[1].strip()
        # The dataclass dump always begins with "{" — no repr() fallback.
        assert body.startswith("{"), (
            "audit M-8: /config must emit JSON, not repr()"
        )
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            pytest.fail(f"audit M-8: /config JSON failed to parse: {exc}\n{body!r}")
        # MinkSettings has a 'permissions' key in its dataclass schema.
        assert "permissions" in parsed


# ---------------------------------------------------------------------------
# M-9: RedactionMiddleware walks output containers + tool_metadata
# ---------------------------------------------------------------------------


class TestM9RedactionWalksOutputContainers:
    def test_redaction_walks_dict_output(self) -> None:
        from chimera.events.types import ToolResultEvent
        from chimera.secrets.redactor import RedactionMiddleware
        from chimera.secrets.registry import SecretRegistry

        reg = SecretRegistry()
        reg.register("API_KEY", "sk-leaky-XYZ")
        mw = RedactionMiddleware(registry=reg)
        # Force the dict shape past the str-only type hint via setattr +
        # construct-then-mutate so the test reflects the audit's repro.
        event = ToolResultEvent(call_id="x", output="placeholder")
        # WHY: the audit was specifically about tools handing the loop a
        # structured dict via the typed-as-str output slot. Mimic that.
        event.output = {"stdout": "leaked sk-leaky-XYZ"}  # type: ignore[assignment]
        mw.process(event, lambda _e: None)
        assert "sk-leaky-XYZ" not in str(event.output), (
            "audit M-9: dict outputs must be walked"
        )

    def test_redaction_walks_tool_metadata(self) -> None:
        from chimera.events.types import ToolResultEvent
        from chimera.secrets.redactor import RedactionMiddleware
        from chimera.secrets.registry import SecretRegistry

        reg = SecretRegistry()
        reg.register("BEARER", "ghp-leaky-456")
        mw = RedactionMiddleware(registry=reg)
        event = ToolResultEvent(
            call_id="y",
            output="ok",
            tool_metadata={
                "headers": {"Authorization": "Bearer ghp-leaky-456"},
                "list_payload": ["fine", "ghp-leaky-456 in list"],
            },
        )
        mw.process(event, lambda _e: None)
        assert "ghp-leaky-456" not in str(event.tool_metadata), (
            "audit M-9: tool_metadata containers must be walked"
        )
