"""Tests for the W14-1 codex-style ferret subcommands.

Covers:

* ``chimera ferret apply [--last]``
* ``chimera ferret review <target>``
* ``chimera ferret fork <session-id> [--last] [--all]``
* ``chimera ferret mcp-server`` (JSON-RPC dispatch)
* ``chimera ferret mcp {add,list,remove}``

Tests stay parser- and helper-level: no live provider, no real
``git apply``. The ``git apply`` subprocess is monkey-patched via the
``runner`` injection point exposed by :func:`chimera.ferret.subcommands.apply._run_git_apply`,
and the review provider chain is replaced with a tiny stub so we never
hit the network.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.ferret import cli as ferret_cli
from chimera.ferret.subcommands import (
    HANDLERS,
    MCP_ACTIONS,
    apply as apply_mod,
    fork as fork_mod,
    mcp_manage,
    mcp_server as mcp_server_mod,
    review as review_mod,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera ferret")
    ferret_cli.add_arguments(parser)
    return parser


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a Namespace seeded with parser defaults plus *overrides*."""
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------


class TestParserSurface:
    def test_w14_subcommands_registered(self) -> None:
        parser = _build_parser()
        # subcommand choices must include each W14-1 entry.
        for sub in ("apply", "review", "fork", "mcp-server", "mcp"):
            args = parser.parse_args([sub])
            assert args.subcommand == sub

    def test_last_flag_default_false(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.last is False

    def test_last_flag_store_true(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["apply", "--last"])
        assert args.last is True

    def test_sub_extra_positional(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["mcp", "add", "search", "python -m foo"])
        assert args.subcommand == "mcp"
        assert args.sub_action == "add"
        assert args.sub_target == "search"
        assert args.sub_extra == "python -m foo"

    def test_handler_registry_keys(self) -> None:
        # The HANDLERS dict declared in the package mirrors the dispatch.
        for sub in ("apply", "review", "fork", "mcp-server", "mcp"):
            assert sub in HANDLERS, f"missing handler for {sub!r}"
        assert MCP_ACTIONS == ("add", "list", "remove")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


_SAMPLE_DIFF = (
    "diff --git a/x.py b/x.py\n"
    "--- a/x.py\n"
    "+++ b/x.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new\n"
)


class TestApply:
    def _make_session(
        self,
        root: Path,
        *,
        sid: str = "ferret-20260507T010101-aaaaaaaa",
        diff: str = _SAMPLE_DIFF,
    ) -> Path:
        sdir = root / sid
        sdir.mkdir(parents=True, exist_ok=True)
        # Write a summary.json so the session is discoverable.
        (sdir / "summary.json").write_text(
            json.dumps({"session_id": sid, "cwd": str(root)}),
            encoding="utf-8",
        )
        # Drop a single event with an apply_patch envelope.
        (sdir / "event-000001-tool.json").write_text(
            json.dumps({"name": "apply_patch", "arguments": {"patch": diff}}),
            encoding="utf-8",
        )
        return sdir

    def test_extract_diff_from_event_envelope(self) -> None:
        ev = {"name": "apply_patch", "arguments": {"patch": _SAMPLE_DIFF}}
        assert apply_mod.extract_diff_from_event(ev) == _SAMPLE_DIFF

    def test_extract_diff_from_event_falls_through(self) -> None:
        # Free-form text containing a unified diff body.
        ev = {"text": _SAMPLE_DIFF}
        assert apply_mod.extract_diff_from_event(ev) == _SAMPLE_DIFF

    def test_extract_diff_returns_none_when_no_patch(self) -> None:
        assert apply_mod.extract_diff_from_event({}) is None
        assert apply_mod.extract_diff_from_event({"name": "ping"}) is None

    def test_find_latest_diff_walks_eventlog(self, tmp_path, monkeypatch) -> None:
        root = tmp_path / ".chimera" / "eventlog"
        root.mkdir(parents=True)
        self._make_session(root)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        result = apply_mod.find_latest_diff()
        assert result is not None
        patch, sdir = result
        assert patch == _SAMPLE_DIFF
        assert sdir.name.startswith("ferret-")

    def test_find_latest_diff_returns_none_when_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # No eventlog directory at all → returns None.
        assert apply_mod.find_latest_diff() is None

    def test_run_apply_invokes_git_apply(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        root = tmp_path / ".chimera" / "eventlog"
        root.mkdir(parents=True)
        self._make_session(root)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        called: dict[str, Any] = {}

        class _Fake:
            returncode = 0
            stdout = ""
            stderr = ""

        def _runner(cmd, **kwargs):
            called["cmd"] = cmd
            called["cwd"] = kwargs.get("cwd")
            return _Fake()

        # Force the patched runner via the helper wrapper.
        monkeypatch.setattr(
            "chimera.ferret.subcommands.apply.subprocess.run", _runner
        )
        rc = apply_mod.run_apply(_ns(last=True, cwd=str(tmp_path)))
        assert rc == 0
        assert called["cmd"][0] == "git"
        assert called["cmd"][1] == "apply"

    def test_run_apply_returns_2_when_no_patch(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc = apply_mod.run_apply(_ns(last=True))
        assert rc == 2
        captured = capsys.readouterr()
        assert "no agent diff found" in captured.err


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


class TestReview:
    def test_resolve_target_pseudo_diff_for_existing_file(self, tmp_path) -> None:
        f = tmp_path / "hello.py"
        f.write_text("print('hello')\n", encoding="utf-8")
        diff = review_mod.resolve_target_to_diff(
            str(f), cwd=str(tmp_path), runner=lambda *a, **k: _FakeProc(rc=0, stdout=""),
        )
        assert "diff --git" in diff
        assert "+print('hello')" in diff

    def test_resolve_target_missing_path(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            review_mod.resolve_target_to_diff(
                str(tmp_path / "does-not-exist.py"),
                cwd=str(tmp_path),
                runner=lambda *a, **k: _FakeProc(rc=0, stdout=""),
            )

    def test_run_review_missing_target_returns_2(self, capsys) -> None:
        rc = review_mod.run_review(_ns(subcommand="review", sub_action=None))
        assert rc == 2
        captured = capsys.readouterr()
        assert "missing TARGET" in captured.err

    def test_run_review_invokes_orchestrator(self, tmp_path, monkeypatch, capsys) -> None:
        f = tmp_path / "hello.py"
        f.write_text("print('hi')\n", encoding="utf-8")

        # Stub provider builder so no SDK is loaded.
        monkeypatch.setattr(
            review_mod, "_build_reviewer_agent", lambda args: _FakeAgent()
        )

        class _StubOrchestrator:
            total_comments = 0
            rounds: list[Any] = []

            def __init__(self, max_rounds: int = 1):
                self.max_rounds = max_rounds

            def run(self, diff, reviewer, author, env=None):
                # Mimic ReviewOrchestrator's protocol surface.
                return True

        monkeypatch.setattr(
            "chimera.review.orchestrator.ReviewOrchestrator",
            _StubOrchestrator,
        )

        rc = review_mod.run_review(
            _ns(subcommand="review", sub_action=str(f), cwd=str(tmp_path))
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "approved=True" in captured.out


class _FakeProc:
    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


class _FakeAgent:
    """Minimal stub satisfying ``ReviewOrchestrator.run``'s reviewer slot."""

    def run(self, prompt: str, env: Any | None = None) -> Any:
        class _R:
            output = "[]"
            success = True

        return _R()


# ---------------------------------------------------------------------------
# fork
# ---------------------------------------------------------------------------


class TestFork:
    def _make_session(self, root: Path, sid: str, *, cwd: str | None = None) -> Path:
        sdir = root / sid
        sdir.mkdir(parents=True, exist_ok=True)
        summary = {"session_id": sid, "cwd": cwd or str(root)}
        (sdir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (sdir / "event-000001.json").write_text("{}", encoding="utf-8")
        return sdir

    def test_resolve_explicit_id(self, tmp_path, monkeypatch) -> None:
        root = tmp_path / ".chimera" / "eventlog"
        root.mkdir(parents=True)
        self._make_session(root, "ferret-20260507T010101-aaaaaaaa")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        args = _ns(subcommand="fork", sub_action="ferret-20260507T010101-aaaaaaaa")
        path, error = fork_mod.resolve_fork_source(args)
        assert error is None
        assert path is not None and path.name.startswith("ferret-")

    def test_resolve_missing_target_errors(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        args = _ns(subcommand="fork", sub_action=None, last=False)
        path, error = fork_mod.resolve_fork_source(args)
        assert path is None
        assert error is not None
        assert "missing <session-id>" in error

    def test_fork_session_creates_copy(self, tmp_path) -> None:
        root = tmp_path / "eventlog"
        root.mkdir()
        source = self._make_session(root, "ferret-20260507T010101-aaaaaaaa")
        new_dir = fork_mod.fork_session(source, eventlog_root=root)
        assert new_dir.exists()
        assert new_dir.name != source.name
        # Summary.json must record the parent id and a forked_at stamp.
        data = json.loads((new_dir / "summary.json").read_text(encoding="utf-8"))
        assert data["parent_id"] == source.name
        assert data["session_id"] == new_dir.name
        assert "forked_at" in data

    def test_run_fork_explicit_id(self, tmp_path, monkeypatch, capsys) -> None:
        root = tmp_path / ".chimera" / "eventlog"
        root.mkdir(parents=True)
        self._make_session(root, "ferret-20260507T010101-aaaaaaaa")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc = fork_mod.run_fork(
            _ns(subcommand="fork", sub_action="ferret-20260507T010101-aaaaaaaa")
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "ferret fork" in captured.out
        assert "resume with" in captured.out

    def test_run_fork_conflicting_flags(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc = fork_mod.run_fork(
            _ns(subcommand="fork", sub_action="some-id", last=True)
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# mcp-server
# ---------------------------------------------------------------------------


class TestMcpServer:
    def test_initialize_handshake(self) -> None:
        server = mcp_server_mod.FerretMCPServer(_ns())
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = server.handle_message(msg)
        assert resp is not None
        assert resp["id"] == 1
        result = resp["result"]
        assert result["serverInfo"]["name"] == mcp_server_mod.SERVER_NAME

    def test_tools_list_returns_documented_tools(self) -> None:
        server = mcp_server_mod.FerretMCPServer(_ns())
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = server.handle_message(msg)
        assert resp is not None
        names = [t["name"] for t in resp["result"]["tools"]]
        assert "ferret_run" in names
        assert "ferret_apply" in names

    def test_unknown_method_returns_error(self) -> None:
        server = mcp_server_mod.FerretMCPServer(_ns())
        msg = {"jsonrpc": "2.0", "id": 3, "method": "totally/bogus", "params": {}}
        resp = server.handle_message(msg)
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# mcp manage
# ---------------------------------------------------------------------------


class TestMcpManage:
    def test_add_persists_entry(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / "mcp_servers.json"
        # All helpers accept an explicit ``path=`` so we don't need to
        # monkey-patch ``Path.home()``.
        entry = mcp_manage.add_mcp_server(
            "search", "python -m chimera.mcp_servers.search_server",
            path=cfg,
        )
        assert entry["command"] == "python"
        assert entry["args"] == ["-m", "chimera.mcp_servers.search_server"]
        # Persisted to disk in the canonical envelope.
        on_disk = json.loads(cfg.read_text(encoding="utf-8"))
        assert "search" in on_disk["mcpServers"]

    def test_list_returns_configured(self, tmp_path) -> None:
        cfg = tmp_path / "mcp_servers.json"
        mcp_manage.add_mcp_server("a", "echo a", path=cfg)
        mcp_manage.add_mcp_server("b", "echo b", path=cfg)
        listed = mcp_manage.list_mcp_servers(path=cfg)
        assert set(listed.keys()) == {"a", "b"}

    def test_remove_drops_entry(self, tmp_path) -> None:
        cfg = tmp_path / "mcp_servers.json"
        mcp_manage.add_mcp_server("zap", "echo z", path=cfg)
        assert mcp_manage.remove_mcp_server("zap", path=cfg) is True
        assert mcp_manage.remove_mcp_server("zap", path=cfg) is False

    def test_run_mcp_add_missing_args_returns_2(self, capsys, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc = mcp_manage.run_mcp(
            _ns(subcommand="mcp", sub_action="add", sub_target=None, sub_extra=None)
        )
        assert rc == 2
        assert "requires" in capsys.readouterr().err

    def test_run_mcp_unknown_action_returns_2(self, capsys, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc = mcp_manage.run_mcp(
            _ns(subcommand="mcp", sub_action="bogus", sub_target=None)
        )
        assert rc == 2
        assert "unknown action" in capsys.readouterr().err

    def test_run_mcp_full_flow_via_namespace(self, capsys, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        rc = mcp_manage.run_mcp(
            _ns(
                subcommand="mcp",
                sub_action="add",
                sub_target="hello",
                sub_extra="echo world",
            )
        )
        assert rc == 0
        # Verify list shows the new entry through the dispatcher.
        capsys.readouterr()  # drain
        rc = mcp_manage.run_mcp(
            _ns(subcommand="mcp", sub_action="list")
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "hello" in out
        # Removal returns 0 on first call, 2 on the second.
        rc = mcp_manage.run_mcp(
            _ns(subcommand="mcp", sub_action="remove", sub_target="hello")
        )
        assert rc == 0
        rc = mcp_manage.run_mcp(
            _ns(subcommand="mcp", sub_action="remove", sub_target="hello")
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# Top-level dispatch wiring
# ---------------------------------------------------------------------------


class TestTopLevelDispatch:
    def test_run_dispatches_apply(self, monkeypatch) -> None:
        captured: list[Any] = []

        def _fake(args):
            captured.append(args)
            return 0

        monkeypatch.setitem(
            ferret_cli._SUBCOMMAND_DISPATCH, "apply", _fake  # noqa: SLF001
        )
        rc = ferret_cli.run(_ns(subcommand="apply", skip_git_repo_check=True))
        assert rc == 0
        assert captured, "apply dispatcher was not invoked"

    def test_run_dispatches_mcp(self, monkeypatch) -> None:
        captured: list[Any] = []

        def _fake(args):
            captured.append(args)
            return 0

        monkeypatch.setitem(
            ferret_cli._SUBCOMMAND_DISPATCH, "mcp", _fake  # noqa: SLF001
        )
        rc = ferret_cli.run(
            _ns(subcommand="mcp", sub_action="list", skip_git_repo_check=True)
        )
        assert rc == 0
        assert captured
