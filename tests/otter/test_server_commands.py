"""Tests for the otter HTTP server's custom-command surface.

Wave-3 (F4) wires ``GET /commands`` and ``POST /commands/<name>/invoke``
onto :class:`chimera.otter.server.OtterServer` so a TUI / IDE client
driving over HTTP can discover and invoke the same
``.opencode/command/*.md`` user-defined slash commands the in-process
REPL dispatcher exposes.

These tests use ``tmp_path`` plus a stubbed user-scope dir tuple so the
real ``~/.opencode/command/*.md`` files (if any) cannot leak into the
fixture. The test driver speaks plain :mod:`urllib.request` — no
third-party HTTP client — to keep parity with the rest of
``tests/otter/test_server.py``.
"""
from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

from chimera.otter import commands as otter_commands
from chimera.otter.server import OtterServer


# ---------------------------------------------------------------------------
# Fakes — mirror the shape used by ``test_server.py``.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeAgentResult:
    """Minimal stand-in for :class:`chimera.types.AgentResult`."""

    output: str = "ok"
    steps: int = 1
    cost: float = 0.0
    success: bool = True


class _FakeAgent:
    """Records the prompts the server forwarded to ``async_run``."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def async_run(self, task: str, env: Any | None) -> _FakeAgentResult:
        self.prompts.append(task)
        return _FakeAgentResult(output=f"echo: {task}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_user_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe the module-level ``_USER_DIRS`` tuple so a developer's real
    ``~/.opencode/command/*.md`` files cannot leak into the test fixture.

    We point both entries at a path inside a throwaway temp dir that
    deliberately doesn't exist; ``_scan_dir`` is robust to missing
    directories and returns an empty mapping.
    """
    monkeypatch.setattr(otter_commands, "_USER_DIRS", ())


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """Lay down a synthetic ``.opencode/command/*.md`` palette under ``tmp_path``.

    Two commands:

    * ``wave3-f4-summarize`` — has a ``description``, a positional
      ``$1`` and a named ``$TARGET`` placeholder; exercises the full
      frontmatter/render path.
    * ``wave3-f4-noargs`` — no frontmatter at all; exercises the
      "all-body" degraded path.
    """
    cmd_dir = tmp_path / ".opencode" / "command"
    cmd_dir.mkdir(parents=True)

    (cmd_dir / "wave3-f4-summarize.md").write_text(
        "---\n"
        "description: Summarize $1 about $TARGET\n"
        "args:\n"
        "  - name: target\n"
        "    description: subject of the summary\n"
        "---\n"
        "Please summarize $1 — focus on $TARGET.\n",
        encoding="utf-8",
    )
    (cmd_dir / "wave3-f4-noargs.md").write_text(
        "Just a plain prompt body, no frontmatter.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def fake_agent() -> _FakeAgent:
    return _FakeAgent()


@pytest.fixture()
def server(
    fake_agent: _FakeAgent,
    project_root: Path,
    isolated_user_dirs: None,
) -> Iterator[OtterServer]:
    """Spin up :class:`OtterServer` pointed at the synthetic project root."""
    srv = OtterServer(
        agent_factory=lambda _state: fake_agent,
        host="127.0.0.1",
        port=0,
        commands_cwd=project_root,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


def _base_url(srv: OtterServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


def _http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Same minimal stdlib helper used elsewhere in ``tests/otter``."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}
    raw = resp.read()
    return resp.status, json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# GET /commands
# ---------------------------------------------------------------------------


def test_commands_list_returns_discovered_palette(server: OtterServer) -> None:
    """The list route returns the synthetic palette under ``commands_cwd``."""
    status, body = _http_json("GET", f"{_base_url(server)}/commands")
    assert status == 200
    assert "commands" in body
    names = sorted(c["name"] for c in body["commands"])
    assert names == ["wave3-f4-noargs", "wave3-f4-summarize"]

    summarize = next(
        c for c in body["commands"] if c["name"] == "wave3-f4-summarize"
    )
    assert summarize["description"] == "Summarize $1 about $TARGET"
    assert summarize["args"] == [
        {"name": "target", "description": "subject of the summary"},
    ]
    # ``source`` should be the absolute path of the markdown file we wrote.
    assert summarize["source"] is not None
    assert summarize["source"].endswith("wave3-f4-summarize.md")

    noargs = next(c for c in body["commands"] if c["name"] == "wave3-f4-noargs")
    # No frontmatter -> empty description, empty args list.
    assert noargs["description"] == ""
    assert noargs["args"] == []


def test_commands_list_is_empty_when_no_palette(
    fake_agent: _FakeAgent,
    tmp_path: Path,
    isolated_user_dirs: None,
) -> None:
    """An empty cwd -> ``{"commands": []}`` (not 404)."""
    srv = OtterServer(
        agent_factory=lambda _state: fake_agent,
        host="127.0.0.1",
        port=0,
        commands_cwd=tmp_path,
    )
    srv.start(blocking=False)
    try:
        status, body = _http_json("GET", f"{_base_url(srv)}/commands")
        assert status == 200
        assert body == {"commands": []}
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# POST /commands/<name>/invoke
# ---------------------------------------------------------------------------


def _wait_for_event(
    server: OtterServer, sid: str, event: str, timeout: float = 3.0
) -> None:
    """Poll the session's event log until *event* shows up (or time out)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = server.get_session(sid)
        assert state is not None
        if any(ev["event"] == event for ev in state.events):
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {event!r}")


def test_command_invoke_renders_and_pushes_message(
    server: OtterServer, fake_agent: _FakeAgent
) -> None:
    """Invoking a command renders the template and lands it on the session."""
    # Open a session.
    status, body = _http_json("POST", f"{_base_url(server)}/session", body={})
    assert status == 201
    sid = body["session_id"]

    # Invoke ``wave3-f4-summarize`` with positional ``$1`` and named ``$TARGET``.
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-summarize/invoke",
        body={
            "session_id": sid,
            "args": ["chapter-7"],
            "kwargs": {"target": "the otter REPL"},
        },
    )
    assert status == 202
    assert body["name"] == "wave3-f4-summarize"
    assert "message_id" in body
    # Both substitutions must have happened.
    assert body["rendered"] == (
        "Please summarize chapter-7 — focus on the otter REPL."
    )

    # The agent saw the rendered prompt as a regular user turn.
    _wait_for_event(server, sid, "result")
    assert body["rendered"] in fake_agent.prompts

    # The session's event log carries the user_message + result, exactly the
    # way a direct ``POST /session/<id>/message`` would have produced.
    state = server.get_session(sid)
    assert state is not None
    kinds = [ev["event"] for ev in state.events]
    assert "user_message" in kinds
    assert "result" in kinds


def test_command_invoke_404_on_unknown_command(server: OtterServer) -> None:
    """Unknown command name -> 404 (with the requested name echoed back)."""
    # Need a real session id so the handler reaches the command lookup.
    status, body = _http_json("POST", f"{_base_url(server)}/session", body={})
    assert status == 201
    sid = body["session_id"]

    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-not-a-real-cmd/invoke",
        body={"session_id": sid},
    )
    assert status == 404
    assert body["error"] == "command_not_found"
    assert body["name"] == "wave3-f4-not-a-real-cmd"


def test_command_invoke_404_on_unknown_session(server: OtterServer) -> None:
    """Unknown session id -> 404, even when the command exists."""
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-summarize/invoke",
        body={"session_id": "not-a-real-session"},
    )
    assert status == 404
    assert body["error"] == "session_not_found"


def test_command_invoke_400_when_session_id_missing(server: OtterServer) -> None:
    """Body must carry a ``session_id``; otherwise 400."""
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-summarize/invoke",
        body={},
    )
    assert status == 400
    assert body["error"] == "missing_session_id"


def test_command_invoke_400_when_args_not_a_list(server: OtterServer) -> None:
    """``args`` must be a JSON list — anything else is 400."""
    status, body = _http_json("POST", f"{_base_url(server)}/session", body={})
    assert status == 201
    sid = body["session_id"]

    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-summarize/invoke",
        body={"session_id": sid, "args": "not-a-list"},
    )
    assert status == 400
    assert body["error"] == "args_must_be_list"


def test_command_invoke_400_when_kwargs_not_a_dict(server: OtterServer) -> None:
    """``kwargs`` must be a JSON object — anything else is 400."""
    status, body = _http_json("POST", f"{_base_url(server)}/session", body={})
    assert status == 201
    sid = body["session_id"]

    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-summarize/invoke",
        body={"session_id": sid, "kwargs": ["not", "a", "dict"]},
    )
    assert status == 400
    assert body["error"] == "kwargs_must_be_object"


def test_command_invoke_with_no_args_renders_body_verbatim(
    server: OtterServer, fake_agent: _FakeAgent
) -> None:
    """A zero-arg invocation renders the body without placeholder substitution."""
    status, body = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = body["session_id"]

    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/commands/wave3-f4-noargs/invoke",
        body={"session_id": sid},
    )
    assert status == 202
    assert body["rendered"] == "Just a plain prompt body, no frontmatter."

    _wait_for_event(server, sid, "result")
    assert "Just a plain prompt body, no frontmatter." in fake_agent.prompts
