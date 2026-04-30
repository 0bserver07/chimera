"""Tests for ``chimera.weasel.rpc`` — JSON-RPC 2.0 stdio server.

The bulk of coverage drives :class:`WeaselRpcServer` in-process via
:class:`io.StringIO` so the assertions stay deterministic and fast. One
end-to-end test spawns the server in a real subprocess via ``python -m``
to catch wire-protocol regressions that only surface across process
boundaries.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from typing import Any

import pytest

from chimera.weasel.rpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    WeaselRpcServer,
)


# ---------------------------------------------------------------------------
# In-process helper
# ---------------------------------------------------------------------------


def _run(
    requests: list[dict[str, Any]],
    *,
    session: Any | None = None,
    list_models: Any | None = None,
) -> list[dict[str, Any]]:
    """Drive a :class:`WeaselRpcServer` with *requests* and parse responses.

    Args:
        requests: Sequence of JSON-RPC frames to feed in.
        session: Optional Chimera session (None → stub mode).
        list_models: Optional override for ``list_models``.

    Returns:
        Parsed JSON objects from stdout, in order.
    """
    stdin_text = "\n".join(json.dumps(r) for r in requests) + "\n"
    stdout_buf = io.StringIO()
    server = WeaselRpcServer(
        session=session,
        stdin=io.StringIO(stdin_text),
        stdout=stdout_buf,
        list_models=list_models,
    )
    rc = server.run()
    assert rc == 0
    out = stdout_buf.getvalue().strip()
    if not out:
        return []
    return [json.loads(line) for line in out.splitlines()]


# ---------------------------------------------------------------------------
# Method coverage (stub mode, no session)
# ---------------------------------------------------------------------------


def test_list_models_returns_envelope() -> None:
    responses = _run(
        [{"jsonrpc": "2.0", "id": 1, "method": "list_models"}],
        list_models=lambda: ["alpha", "beta"],
    )
    assert len(responses) == 1
    r = responses[0]
    assert r["jsonrpc"] == "2.0"
    assert r["id"] == 1
    assert r["result"] == {"models": ["alpha", "beta"]}


def test_list_models_default_does_not_crash() -> None:
    """The default ``list_models`` is best-effort and must not raise."""
    responses = _run(
        [{"jsonrpc": "2.0", "id": "x", "method": "list_models"}],
    )
    assert responses[0]["jsonrpc"] == "2.0"
    assert responses[0]["id"] == "x"
    assert "models" in responses[0]["result"]
    assert isinstance(responses[0]["result"]["models"], list)


def test_prompt_stub_echoes() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "prompt",
         "params": {"message": "hello"}},
    ])
    assert responses[0]["result"]["output"] == "echo: hello"
    assert responses[0]["result"]["success"] is True


def test_prompt_missing_message_invalid_params() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "prompt", "params": {}},
    ])
    err = responses[0]["error"]
    assert err["code"] == INVALID_PARAMS
    assert "message" in err["message"]


def test_prompt_message_not_string_invalid_params() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "prompt",
         "params": {"message": 42}},
    ])
    assert responses[0]["error"]["code"] == INVALID_PARAMS


def test_get_state_stub_initial() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": 9, "method": "get_state"},
    ])
    assert responses[0]["result"] == {"messages": [], "model": ""}


def test_get_state_stub_after_prompts() -> None:
    """Stub session records messages so get_state returns history."""
    stdin_text = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompt",
                    "params": {"message": "first"}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "prompt",
                    "params": {"message": "second"}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "get_state"}),
    ]) + "\n"
    out = io.StringIO()
    server = WeaselRpcServer(stdin=io.StringIO(stdin_text), stdout=out)
    server.run()
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    state = lines[-1]["result"]
    assert len(state["messages"]) == 4  # 2 user + 2 assistant
    assert state["messages"][0] == {"role": "user", "content": "first"}
    assert state["messages"][1] == {"role": "assistant",
                                     "content": "echo: first"}


def test_cancel_returns_cancelled_true() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": "c1", "method": "cancel"},
    ])
    assert responses[0]["result"] == {"cancelled": True}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_method_returns_method_not_found() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "explode"},
    ])
    err = responses[0]["error"]
    assert err["code"] == METHOD_NOT_FOUND
    assert "explode" in err["message"]


def test_missing_jsonrpc_field_invalid_request() -> None:
    responses = _run([{"id": 1, "method": "list_models"}])
    err = responses[0]["error"]
    assert err["code"] == INVALID_REQUEST


def test_missing_method_invalid_request() -> None:
    responses = _run([{"jsonrpc": "2.0", "id": 1}])
    assert responses[0]["error"]["code"] == INVALID_REQUEST


def test_params_must_be_object() -> None:
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "list_models",
         "params": ["nope"]},
    ])
    assert responses[0]["error"]["code"] == INVALID_PARAMS


def test_parse_error_on_bad_json() -> None:
    """Raw stdin garbage triggers a parse error with id=null."""
    stdin = io.StringIO("not-json\n")
    out = io.StringIO()
    server = WeaselRpcServer(stdin=stdin, stdout=out)
    server.run()
    line = out.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] is None
    assert parsed["error"]["code"] == PARSE_ERROR


def test_blank_lines_are_skipped() -> None:
    stdin = io.StringIO("\n\n" + json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "list_models",
    }) + "\n\n")
    out = io.StringIO()
    server = WeaselRpcServer(
        stdin=stdin, stdout=out, list_models=lambda: [],
    )
    server.run()
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 1


def test_notification_produces_no_output() -> None:
    """A request with no ``id`` is a notification → no response."""
    stdin = io.StringIO(json.dumps({
        "jsonrpc": "2.0", "method": "cancel",
    }) + "\n")
    out = io.StringIO()
    server = WeaselRpcServer(stdin=stdin, stdout=out)
    server.run()
    assert out.getvalue() == ""


# ---------------------------------------------------------------------------
# Session integration (composes chimera.rpc.handler.RpcHandler)
# ---------------------------------------------------------------------------


class _FakeProvider:
    model_name = "fake-model-7b"


class _FakeAgent:
    def __init__(self) -> None:
        self.provider = _FakeProvider()


class _FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _FakeResult:
    def __init__(self, output: str) -> None:
        self.output = output
        self.success = True


class _FakeSession:
    def __init__(self) -> None:
        self._agent = _FakeAgent()
        self.messages: list[_FakeMessage] = []
        self.cancelled = False

    def chat(self, message: str) -> _FakeResult:
        self.messages.append(_FakeMessage("user", message))
        out = f"answer: {message}"
        self.messages.append(_FakeMessage("assistant", out))
        return _FakeResult(out)

    def cancel(self) -> None:
        self.cancelled = True


def test_session_prompt_round_trips() -> None:
    sess = _FakeSession()
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "prompt",
         "params": {"message": "ping"}},
    ], session=sess)
    assert responses[0]["result"]["output"] == "answer: ping"
    assert responses[0]["result"]["success"] is True
    assert len(sess.messages) == 2


def test_session_get_state_uses_provider_model_name() -> None:
    sess = _FakeSession()
    sess.chat("alpha")
    responses = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "get_state"},
    ], session=sess)
    state = responses[0]["result"]
    assert state["model"] == "fake-model-7b"
    assert state["messages"][0] == {"role": "user", "content": "alpha"}


def test_session_cancel_propagates() -> None:
    sess = _FakeSession()
    _run([{"jsonrpc": "2.0", "id": 1, "method": "cancel"}], session=sess)
    assert sess.cancelled is True


# ---------------------------------------------------------------------------
# Subprocess end-to-end
# ---------------------------------------------------------------------------


_SUBPROCESS_DRIVER = textwrap.dedent("""
    import io, sys
    from chimera.weasel.rpc import WeaselRpcServer

    server = WeaselRpcServer(
        session=None,
        stdin=sys.stdin,
        stdout=sys.stdout,
        list_models=lambda: ["m1", "m2"],
    )
    sys.exit(server.run())
""")


def test_subprocess_round_trip(tmp_path: Any) -> None:
    """Spawn the server in a real subprocess, send 3 calls, verify shapes."""
    driver = tmp_path / "driver.py"
    driver.write_text(_SUBPROCESS_DRIVER)

    # Make sure the subprocess can import chimera by reusing our PYTHONPATH.
    env = dict(os.environ)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        repo_root + (os.pathsep + existing if existing else "")
    )

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "list_models"},
        {"jsonrpc": "2.0", "id": 2, "method": "prompt",
         "params": {"message": "hi"}},
        {"jsonrpc": "2.0", "id": 3, "method": "get_state"},
    ]
    stdin_text = "\n".join(json.dumps(r) for r in requests) + "\n"

    proc = subprocess.run(
        [sys.executable, str(driver)],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 0, (
        f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
    )
    lines = [json.loads(l) for l in proc.stdout.strip().splitlines()]
    assert len(lines) == 3

    # 1) list_models
    assert lines[0]["jsonrpc"] == "2.0"
    assert lines[0]["id"] == 1
    assert lines[0]["result"] == {"models": ["m1", "m2"]}

    # 2) prompt → stub echoes
    assert lines[1]["id"] == 2
    assert lines[1]["result"]["output"] == "echo: hi"

    # 3) get_state shows the user/assistant pair from #2
    assert lines[2]["id"] == 3
    state = lines[2]["result"]
    assert state["model"] == ""
    assert {m["role"] for m in state["messages"]} == {"user", "assistant"}


@pytest.mark.parametrize("bad_input", ["{not json}\n", "[]\n"])
def test_subprocess_handles_bad_input_gracefully(
    tmp_path: Any, bad_input: str,
) -> None:
    """Garbage in shouldn't crash the subprocess; we want a JSON-RPC error."""
    driver = tmp_path / "driver.py"
    driver.write_text(_SUBPROCESS_DRIVER)

    env = dict(os.environ)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        repo_root + (os.pathsep + existing if existing else "")
    )

    proc = subprocess.run(
        [sys.executable, str(driver)],
        input=bad_input,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert proc.returncode == 0
    line = proc.stdout.strip().splitlines()[0]
    parsed = json.loads(line)
    assert parsed["jsonrpc"] == "2.0"
    assert "error" in parsed
