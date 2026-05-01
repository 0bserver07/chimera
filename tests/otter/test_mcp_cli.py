"""Tests for ``chimera.otter.mcp_cli`` — list / add / auth handlers.

Fixture-style: ``tmp_path`` doubles as ``$HOME`` and project root so we
never touch the real ``~/.opencode/`` or ``~/.chimera/``. The OAuth
device flow is mocked end-to-end via the ``flow_factory`` test seam
exposed by :func:`cmd_mcp_auth`.

Aims for behaviour coverage:

* ``cmd_mcp_list`` lists merged user + project entries with the right
  scope label, and survives an empty filesystem.
* ``cmd_mcp_add`` writes valid JSON to the chosen scope, prompts before
  writing, refuses to clobber an existing entry, and supports both
  stdio and http transports.
* ``cmd_mcp_auth`` runs the device flow when ``oauth`` metadata is
  present, persists the credential, and falls back to a manual paste
  flow when it isn't.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from chimera.otter import mcp_cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated fake HOME and re-root ``Path.home`` + ``$HOME``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Project directory under tmp_path — no subdirs created up-front."""
    proj = tmp_path / "project"
    proj.mkdir()
    return proj


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# `mcp list`
# ---------------------------------------------------------------------------


def test_list_empty_returns_friendly_message(
    fake_home: Path, project: Path
) -> None:
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_list(project, home=fake_home, out=out)
    assert rc == 0
    text = out.getvalue()
    assert "No MCP servers configured." in text
    assert "chimera otter mcp add" in text


def test_list_renders_user_and_project_scopes(
    fake_home: Path, project: Path
) -> None:
    user_cfg = fake_home / ".opencode" / "config.json"
    user_cfg.parent.mkdir()
    user_cfg.write_text(json.dumps({
        "mcp": {
            "fs": {"type": "local", "command": ["fs-server"]},
            "shared": {"type": "remote", "url": "https://u/shared"},
        }
    }))
    proj_cfg = project / ".opencode" / "config.json"
    proj_cfg.parent.mkdir()
    proj_cfg.write_text(json.dumps({
        "mcp": {
            "shared": {"type": "remote", "url": "https://p/shared"},
            "extra": {"type": "local", "command": ["extra-bin"]},
        }
    }))
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_list(project, home=fake_home, out=out)
    assert rc == 0
    rendered = out.getvalue()
    assert "fs" in rendered and "user" in rendered
    assert "extra" in rendered and "project" in rendered
    # Project wins on conflict but the scope label shows the union.
    assert "shared" in rendered
    assert "user+project" in rendered


def test_list_marks_disabled_entries(
    fake_home: Path, project: Path
) -> None:
    proj_cfg = project / ".opencode" / "config.json"
    proj_cfg.parent.mkdir()
    proj_cfg.write_text(json.dumps({
        "mcp": {
            "off": {
                "type": "local",
                "command": ["nope"],
                "enabled": False,
            },
        }
    }))
    out = io.StringIO()
    mcp_cli.cmd_mcp_list(project, home=fake_home, out=out)
    assert "(disabled)" in out.getvalue()


# ---------------------------------------------------------------------------
# `mcp add`
# ---------------------------------------------------------------------------


def test_add_writes_project_stdio_entry(
    fake_home: Path, project: Path
) -> None:
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["fs-server", "--root", "/tmp"],
        project_root=project,
        env={"LOG": "1"},
        yes=True,
        out=out,
    )
    assert rc == 0
    cfg = project / ".opencode" / "config.json"
    assert cfg.exists()
    blob = _read_json(cfg)
    assert blob == {
        "mcp": {
            "fs": {
                "type": "local",
                "command": ["fs-server", "--root", "/tmp"],
                "environment": {"LOG": "1"},
            },
        }
    }


def test_add_writes_user_scope_when_user_flag_set(
    fake_home: Path, project: Path
) -> None:
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["fs-server"],
        project_root=project,
        user_scope=True,
        home=fake_home,
        yes=True,
        out=io.StringIO(),
    )
    assert rc == 0
    user_cfg = fake_home / ".opencode" / "config.json"
    proj_cfg = project / ".opencode" / "config.json"
    assert user_cfg.exists()
    assert not proj_cfg.exists()
    blob = _read_json(user_cfg)
    assert "fs" in blob["mcp"]


def test_add_http_entry_writes_remote_type(
    fake_home: Path, project: Path
) -> None:
    rc = mcp_cli.cmd_mcp_add(
        "weather",
        [],
        project_root=project,
        url="https://x/mcp",
        headers={"Authorization": "Bearer abc"},
        yes=True,
        out=io.StringIO(),
    )
    assert rc == 0
    blob = _read_json(project / ".opencode" / "config.json")
    assert blob["mcp"]["weather"] == {
        "type": "remote",
        "url": "https://x/mcp",
        "headers": {"Authorization": "Bearer abc"},
    }


def test_add_refuses_when_both_command_and_url_supplied(
    fake_home: Path, project: Path
) -> None:
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["fs-server"],
        project_root=project,
        url="https://x/mcp",
        yes=True,
        out=out,
    )
    assert rc == 2
    assert "either a command" in out.getvalue()


def test_add_refuses_empty_command(
    fake_home: Path, project: Path
) -> None:
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_add(
        "fs", [], project_root=project, yes=True, out=out
    )
    assert rc == 2
    assert "missing command" in out.getvalue()


def test_add_refuses_duplicate_name(
    fake_home: Path, project: Path
) -> None:
    proj_cfg = project / ".opencode" / "config.json"
    proj_cfg.parent.mkdir()
    proj_cfg.write_text(json.dumps({
        "mcp": {"fs": {"type": "local", "command": ["existing"]}}
    }))
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["new-fs"],
        project_root=project,
        yes=True,
        out=out,
    )
    assert rc == 2
    assert "already exists" in out.getvalue()
    # File untouched.
    assert _read_json(proj_cfg)["mcp"]["fs"]["command"] == ["existing"]


def test_add_prompts_before_writing_and_aborts_on_no(
    fake_home: Path, project: Path
) -> None:
    answers = iter(["n"])
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["fs-server"],
        project_root=project,
        yes=False,
        reader=lambda _prompt: next(answers),
        out=out,
    )
    assert rc == 1
    assert not (project / ".opencode" / "config.json").exists()
    assert "aborted" in out.getvalue().lower()


def test_add_prompts_and_writes_on_yes(
    fake_home: Path, project: Path
) -> None:
    answers = iter(["y"])
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["fs-server"],
        project_root=project,
        yes=False,
        reader=lambda _prompt: next(answers),
        out=io.StringIO(),
    )
    assert rc == 0
    assert (project / ".opencode" / "config.json").exists()


def test_add_preserves_other_top_level_keys(
    fake_home: Path, project: Path
) -> None:
    """Adding an MCP entry must not clobber unrelated config keys."""
    proj_cfg = project / ".opencode" / "config.json"
    proj_cfg.parent.mkdir()
    proj_cfg.write_text(json.dumps({"model": "stub", "theme": "dark"}))
    rc = mcp_cli.cmd_mcp_add(
        "fs",
        ["fs-server"],
        project_root=project,
        yes=True,
        out=io.StringIO(),
    )
    assert rc == 0
    blob = _read_json(proj_cfg)
    assert blob["model"] == "stub"
    assert blob["theme"] == "dark"
    assert "mcp" in blob and "fs" in blob["mcp"]


# ---------------------------------------------------------------------------
# `mcp auth`
# ---------------------------------------------------------------------------


@dataclass
class _FakeStore:
    """Minimal credential-store stub for the auth path."""

    saved: list[Any] = field(default_factory=list)

    def save(self, credential: Any) -> None:
        self.saved.append(credential)


@dataclass
class _FakeCredential:
    provider: str
    token: str
    refresh_token: str | None = None
    expires_at: float | None = None


@dataclass
class _FakeFlow:
    """Stand-in for :class:`OAuthDeviceFlow` driven by ``flow_factory``."""

    credential: _FakeCredential
    seen_kwargs: dict[str, Any] = field(default_factory=dict)

    def authenticate(self) -> _FakeCredential:
        return self.credential


def _seed_user_oauth_entry(
    home: Path,
    name: str,
    *,
    oauth: dict[str, Any] | None,
    url: str = "https://example.com/mcp",
) -> None:
    cfg = home / ".opencode" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {"type": "remote", "url": url}
    if oauth is not None:
        entry["oauth"] = oauth
    cfg.write_text(json.dumps({"mcp": {name: entry}}))


def test_auth_unknown_server_returns_2(
    fake_home: Path, project: Path
) -> None:
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_auth(
        "missing", project_root=project, home=fake_home, out=out,
    )
    assert rc == 2
    assert "no MCP server" in out.getvalue()


def test_auth_rejects_stdio_transport(
    fake_home: Path, project: Path
) -> None:
    cfg = fake_home / ".opencode" / "config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({
        "mcp": {"fs": {"type": "local", "command": ["fs-server"]}}
    }))
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_auth(
        "fs", project_root=project, home=fake_home, out=out,
    )
    assert rc == 2
    assert "OAuth only applies to http" in out.getvalue()


def test_auth_runs_device_flow_when_oauth_block_present(
    fake_home: Path, project: Path
) -> None:
    _seed_user_oauth_entry(
        fake_home,
        "secured",
        oauth={
            "client_id": "cid",
            "device_authorization_endpoint": "https://issuer/device",
            "token_endpoint": "https://issuer/token",
        },
    )
    store = _FakeStore()
    fake_credential = _FakeCredential(provider="mcp:secured", token="tk-1")
    flow_seen: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeFlow:
        flow_seen.update(kwargs)
        return _FakeFlow(credential=fake_credential)

    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_auth(
        "secured",
        project_root=project,
        home=fake_home,
        flow_factory=factory,
        credential_store=store,
        out=out,
    )
    assert rc == 0
    assert store.saved == [fake_credential]
    assert flow_seen["client_id"] == "cid"
    assert flow_seen["device_auth_url"] == "https://issuer/device"
    assert flow_seen["token_url"] == "https://issuer/token"
    assert flow_seen["provider_name"] == "mcp:secured"
    assert "saved credential" in out.getvalue()


def test_auth_falls_back_to_manual_paste_when_no_oauth_block(
    fake_home: Path, project: Path
) -> None:
    _seed_user_oauth_entry(fake_home, "manual", oauth=None)
    store = _FakeStore()
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_auth(
        "manual",
        project_root=project,
        home=fake_home,
        flow_factory=None,
        credential_store=store,
        reader=lambda _prompt: "  pasted-token  ",
        out=out,
    )
    assert rc == 0
    assert len(store.saved) == 1
    saved = store.saved[0]
    assert saved.token == "pasted-token"
    assert saved.provider == "mcp:manual"
    assert "Paste token" not in out.getvalue()  # prompt goes through reader
    assert "saved credential" in out.getvalue()


def test_auth_manual_paste_aborts_on_empty_input(
    fake_home: Path, project: Path
) -> None:
    _seed_user_oauth_entry(fake_home, "manual", oauth=None)
    store = _FakeStore()
    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_auth(
        "manual",
        project_root=project,
        home=fake_home,
        flow_factory=None,
        credential_store=store,
        reader=lambda _prompt: "   ",
        out=out,
    )
    assert rc == 1
    assert store.saved == []
    assert "aborted" in out.getvalue().lower()


def test_auth_handles_flow_failure_gracefully(
    fake_home: Path, project: Path
) -> None:
    _seed_user_oauth_entry(
        fake_home,
        "secured",
        oauth={
            "client_id": "cid",
            "device_authorization_endpoint": "https://issuer/device",
            "token_endpoint": "https://issuer/token",
        },
    )

    class _BoomFlow:
        def authenticate(self) -> Any:
            raise RuntimeError("network down")

    out = io.StringIO()
    rc = mcp_cli.cmd_mcp_auth(
        "secured",
        project_root=project,
        home=fake_home,
        flow_factory=lambda **_kw: _BoomFlow(),
        credential_store=_FakeStore(),
        out=out,
    )
    assert rc == 1
    assert "OAuth flow failed" in out.getvalue()


# ---------------------------------------------------------------------------
# Dispatcher entry point
# ---------------------------------------------------------------------------


def _ns(**kw: Any) -> Any:
    """Build a stand-in for :class:`argparse.Namespace`."""
    import types

    return types.SimpleNamespace(**kw)


def test_dispatch_routes_list(
    fake_home: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(root: Path, **kw: Any) -> int:
        captured["called"] = (root, kw)
        return 0

    monkeypatch.setattr(mcp_cli, "cmd_mcp_list", fake_list)
    args = _ns(sub_action="list", cwd=str(project))
    assert mcp_cli.dispatch_mcp(args) == 0
    assert captured["called"][0] == project


def test_dispatch_routes_add_with_extras(
    fake_home: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_add(name: str, command: list[str], **kw: Any) -> int:
        captured["name"] = name
        captured["command"] = command
        captured["kw"] = kw
        return 0

    monkeypatch.setattr(mcp_cli, "cmd_mcp_add", fake_add)
    args = _ns(
        sub_action="add",
        sub_target="fs",
        mcp_extra=["fs-server", "--root", "/tmp"],
        agents_user=False,
        mcp_http=None,
        mcp_header=["X=1", "Y=2"],
        mcp_env=["FOO=bar"],
        mcp_yes=True,
        cwd=str(project),
    )
    assert mcp_cli.dispatch_mcp(args) == 0
    assert captured["name"] == "fs"
    assert captured["command"] == ["fs-server", "--root", "/tmp"]
    assert captured["kw"]["headers"] == {"X": "1", "Y": "2"}
    assert captured["kw"]["env"] == {"FOO": "bar"}
    assert captured["kw"]["yes"] is True
    assert captured["kw"]["user_scope"] is False


def test_dispatch_routes_auth(
    fake_home: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_auth(name: str, **kw: Any) -> int:
        captured["name"] = name
        captured["kw"] = kw
        return 0

    monkeypatch.setattr(mcp_cli, "cmd_mcp_auth", fake_auth)
    args = _ns(sub_action="auth", sub_target="github", cwd=str(project))
    assert mcp_cli.dispatch_mcp(args) == 0
    assert captured["name"] == "github"


def test_dispatch_auth_missing_name_returns_2(
    fake_home: Path, project: Path
) -> None:
    args = _ns(sub_action="auth", sub_target=None, cwd=str(project))
    assert mcp_cli.dispatch_mcp(args) == 2


def test_dispatch_unknown_action_returns_2(
    fake_home: Path, project: Path
) -> None:
    args = _ns(sub_action="bogus", sub_target=None, cwd=str(project))
    assert mcp_cli.dispatch_mcp(args) == 2


def test_parse_kv_list_skips_malformed() -> None:
    parsed = mcp_cli._parse_kv_list(["A=1", "no-equals", "=missing-key", "B="])
    assert parsed == {"A": "1", "B": ""}


def test_parse_kv_list_handles_none() -> None:
    assert mcp_cli._parse_kv_list(None) == {}
