"""``chimera otter share <session-id>`` — fixture-driven regression tests.

Mirrors :mod:`tests.otter.test_sessions`. We materialize a fake otter
session under ``tmp_path``, redirect ``Path.home`` so the production
helpers resolve to the fixture, then exercise the rendering helpers,
the file sink, the HTTP sink (with a stub opener), and the ``cmd_share``
exit-code matrix.
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from chimera.otter import share_cmd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` so tests never touch the real ``~/``.

    Returns the fake home root so callers can probe the resulting layout
    (``<home>/.chimera/eventlog/`` and ``<home>/.chimera/shares/``).
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".chimera" / "eventlog").mkdir(parents=True)
    return home


def _make_session(
    home: Path,
    session_id: str,
    *,
    prompt: str = "do a thing",
    model: str = "stub-model",
    cost_usd: float = 0.0042,
    success: bool = True,
    extra_events: list[dict[str, Any]] | None = None,
) -> Path:
    """Build a fake ``otter-<id>`` session directory under ``home``.

    Pass ``extra_events=[]`` to deliberately materialize a session with
    zero events (vs. ``None`` which uses the default user/agent pair).
    """
    session_dir = home / ".chimera" / "eventlog" / session_id
    session_dir.mkdir()
    summary = {
        "session_id": session_id,
        "started_at": "2026-04-24T05:00:00Z",
        "ended_at": "2026-04-24T05:01:00Z",
        "model": model,
        "prompt": prompt,
        "cwd": "/tmp",
        "permission_mode": "default",
        "steps": 1,
        "tool_calls_total": 0,
        "success": success,
        "cost_usd": cost_usd,
        "total_tokens": 0,
        "error": None if success else "boom",
    }
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    events: list[dict[str, Any]] = extra_events if extra_events is not None else [
        {
            "idx": 0,
            "event_id": "aaaaaaaa",
            "type": "user_message",
            "timestamp": 1.0,
            "metadata": {"content": prompt, "event_id": "aaaaaaaa"},
        },
        {
            "idx": 1,
            "event_id": "bbbbbbbb",
            "type": "agent_result",
            "timestamp": 2.0,
            "metadata": {
                "output": "all done",
                "steps": 1,
                "success": success,
                "cost": cost_usd,
            },
        },
    ]
    for i, ev in enumerate(events):
        ev_id = ev.get("event_id") or f"{i:08x}"
        (session_dir / f"event-{i:06d}-{ev_id}.json").write_text(
            json.dumps(ev), encoding="utf-8",
        )
    return session_dir


def _share_args(
    *,
    target: str | None = None,
    sink: str = "file",
    fmt: str = "html",
    url: str | None = None,
) -> argparse.Namespace:
    """Build a Namespace mirroring what argparse would produce."""
    return argparse.Namespace(
        subcommand="share",
        share_command="share",
        share_target=target,
        share_sink=sink,
        share_format=fmt,
        share_url=url,
        sub_target=target,
    )


# ---------------------------------------------------------------------------
# Rendering helpers — html / md / json
# ---------------------------------------------------------------------------


def test_render_html_includes_session_metadata(fake_home: Path) -> None:
    _make_session(fake_home, "otter-render-html", prompt="alpha prompt")
    from chimera.otter import sessions as sessions_mod

    detail = sessions_mod.get_session("otter-render-html")
    body = share_cmd.render_html(detail)
    assert body.startswith("<!doctype html>")
    assert "otter-render-html" in body
    assert "alpha prompt" in body
    # The HTML must escape user-controlled strings.
    assert "<script>" not in body
    # Each event is rendered.
    assert "user_message" in body
    assert "agent_result" in body


def test_render_html_escapes_dangerous_prompt(fake_home: Path) -> None:
    """User-controlled prompt content must not break out of HTML context."""
    _make_session(
        fake_home,
        "otter-html-escape",
        prompt="<script>alert(1)</script>",
    )
    from chimera.otter import sessions as sessions_mod

    body = share_cmd.render_html(sessions_mod.get_session("otter-html-escape"))
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_render_markdown_lists_metadata_and_events(fake_home: Path) -> None:
    _make_session(fake_home, "otter-render-md", prompt="markdown prompt")
    from chimera.otter import sessions as sessions_mod

    body = share_cmd.render_markdown(sessions_mod.get_session("otter-render-md"))
    assert body.startswith("# Otter session ")
    assert "otter-render-md" in body
    assert "markdown prompt" in body
    assert "## Events" in body
    assert "user_message" in body


def test_render_markdown_no_events(fake_home: Path) -> None:
    _make_session(fake_home, "otter-md-empty", extra_events=[])
    from chimera.otter import sessions as sessions_mod

    body = share_cmd.render_markdown(sessions_mod.get_session("otter-md-empty"))
    assert "(no events recorded)" in body


def test_render_json_round_trips(fake_home: Path) -> None:
    _make_session(fake_home, "otter-render-json", prompt="json prompt")
    from chimera.otter import sessions as sessions_mod

    body = share_cmd.render_json(sessions_mod.get_session("otter-render-json"))
    payload = json.loads(body)
    assert payload["session_id"] == "otter-render-json"
    assert payload["summary"]["prompt"] == "json prompt"
    assert isinstance(payload["events"], list)
    assert len(payload["events"]) == 2


# ---------------------------------------------------------------------------
# write_file_sink
# ---------------------------------------------------------------------------


def test_write_file_sink_writes_html_under_default_shares_dir(fake_home: Path) -> None:
    path = share_cmd.write_file_sink("otter-file-html", "<html>hi</html>", "html")
    assert path.exists()
    assert path.parent == (fake_home / ".chimera" / "shares")
    assert path.name == "otter-file-html.html"
    assert path.read_text(encoding="utf-8") == "<html>hi</html>"


def test_write_file_sink_uses_correct_extension_per_format(fake_home: Path) -> None:
    md = share_cmd.write_file_sink("otter-extn", "# x", "md")
    js = share_cmd.write_file_sink("otter-extn", '{"x": 1}', "json")
    assert md.suffix == ".md"
    assert js.suffix == ".json"


def test_write_file_sink_prepends_otter_prefix_when_missing(fake_home: Path) -> None:
    """Bare ids without ``otter-`` prefix get one for the filename only."""
    path = share_cmd.write_file_sink("rawid123", "x", "md")
    assert path.name == "otter-rawid123.md"


def test_write_file_sink_creates_shares_dir(fake_home: Path) -> None:
    shares = fake_home / ".chimera" / "shares"
    assert not shares.exists()
    share_cmd.write_file_sink("otter-create-dir", "x", "html")
    assert shares.is_dir()


# ---------------------------------------------------------------------------
# send_http — stub urlopen via the ``opener`` injection point
# ---------------------------------------------------------------------------


class _StubResponse:
    """Minimal stand-in for an ``http.client.HTTPResponse``."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(body)
        self.status = status
        self.code = status

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self._buf.close()


class _StubOpener:
    """Captures the last :class:`urllib.request.Request` and replays a stub."""

    def __init__(
        self, body: bytes = b"{\"ok\": true}", status: int = 200,
    ) -> None:
        self.last_request: Any = None
        self.last_timeout: float | None = None
        self._body = body
        self._status = status

    def open(self, req: Any, timeout: float | None = None) -> _StubResponse:
        self.last_request = req
        self.last_timeout = timeout
        return _StubResponse(self._body, status=self._status)


def test_send_http_uses_default_url_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTTER_SHARE_URL", raising=False)
    opener = _StubOpener()
    status, body = share_cmd.send_http("hi", "html", opener=opener)
    assert status == 200
    assert "ok" in body
    assert opener.last_request.full_url == share_cmd.DEFAULT_SHARE_URL
    assert (
        opener.last_request.get_header("Content-type")
        == "text/html; charset=utf-8"
    )


def test_send_http_honors_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTTER_SHARE_URL", "http://example.invalid/share")
    opener = _StubOpener()
    share_cmd.send_http("x", "json", opener=opener)
    assert opener.last_request.full_url == "http://example.invalid/share"
    assert (
        opener.last_request.get_header("Content-type") == "application/json"
    )


def test_send_http_explicit_url_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTTER_SHARE_URL", "http://from-env.invalid")
    opener = _StubOpener()
    share_cmd.send_http(
        "x", "md", url="http://explicit.invalid/x", opener=opener,
    )
    assert opener.last_request.full_url == "http://explicit.invalid/x"


def test_send_http_handles_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPError carries a body — caller should not see an exception."""
    import email.message

    class _ErrorOpener:
        def open(self, req: Any, timeout: float | None = None) -> Any:
            hdrs = email.message.Message()
            raise urllib.error.HTTPError(
                req.full_url, 503, "boom", hdrs, io.BytesIO(b"unavailable"),
            )

    monkeypatch.delenv("OTTER_SHARE_URL", raising=False)
    status, body = share_cmd.send_http("x", "html", opener=_ErrorOpener())
    assert status == 503
    assert "unavailable" in body


# ---------------------------------------------------------------------------
# cmd_share — exit-code matrix
# ---------------------------------------------------------------------------


def test_cmd_share_missing_target_exits_2(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = share_cmd.cmd_share(_share_args(target=None))
    err = capsys.readouterr().err
    assert rc == 2
    assert "SESSION_ID" in err or "requires" in err


def test_cmd_share_unknown_session_exits_2(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = share_cmd.cmd_share(_share_args(target="otter-does-not-exist"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "not found" in err.lower() or "no summary" in err.lower()


def test_cmd_share_unknown_sink_exits_2(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-bad-sink")
    rc = share_cmd.cmd_share(_share_args(target="otter-bad-sink", sink="ftp"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "sink" in err.lower()


def test_cmd_share_unknown_format_exits_2(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-bad-fmt")
    rc = share_cmd.cmd_share(_share_args(target="otter-bad-fmt", fmt="pdf"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "format" in err.lower()


def test_cmd_share_file_sink_writes_html(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-file-flow", prompt="file flow")
    rc = share_cmd.cmd_share(
        _share_args(target="otter-file-flow", sink="file", fmt="html"),
    )
    out = capsys.readouterr().out
    assert rc == 0
    written = Path(out.strip())
    assert written.exists()
    body = written.read_text(encoding="utf-8")
    assert "otter-file-flow" in body
    assert "file flow" in body


def test_cmd_share_file_sink_writes_markdown(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-file-md", prompt="md prompt")
    rc = share_cmd.cmd_share(
        _share_args(target="otter-file-md", sink="file", fmt="md"),
    )
    out = capsys.readouterr().out
    assert rc == 0
    written = Path(out.strip())
    assert written.suffix == ".md"
    assert written.read_text(encoding="utf-8").startswith("# Otter session ")


def test_cmd_share_file_sink_writes_json(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-file-json", prompt="json prompt")
    rc = share_cmd.cmd_share(
        _share_args(target="otter-file-json", sink="file", fmt="json"),
    )
    out = capsys.readouterr().out
    assert rc == 0
    written = Path(out.strip())
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["session_id"] == "otter-file-json"


def test_cmd_share_stdout_sink_prints_body(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-stdout-md", prompt="stdout prompt")
    rc = share_cmd.cmd_share(
        _share_args(target="otter-stdout-md", sink="stdout", fmt="md"),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("# Otter session ")
    assert "stdout prompt" in out


def test_cmd_share_http_sink_success(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--sink http`` posts the body and prints the response."""
    _make_session(fake_home, "otter-http-ok", prompt="http prompt")
    monkeypatch.delenv("OTTER_SHARE_URL", raising=False)

    captured: dict[str, Any] = {}

    class _Capture:
        def open(self, req: Any, timeout: float | None = None) -> _StubResponse:
            captured["request"] = req
            captured["body"] = req.data
            return _StubResponse(b'{"share_id": "abc123"}', status=201)

    monkeypatch.setattr(share_cmd, "send_http", lambda body, fmt, **kw: (
        201, '{"share_id": "abc123"}',
    ))
    rc = share_cmd.cmd_share(
        _share_args(target="otter-http-ok", sink="http", fmt="json"),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "abc123" in out


def test_cmd_share_http_sink_non_2xx_returns_1(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-http-fail", prompt="http fail")
    monkeypatch.setattr(
        share_cmd, "send_http",
        lambda body, fmt, **kw: (500, "internal server error"),
    )
    rc = share_cmd.cmd_share(
        _share_args(target="otter-http-fail", sink="http", fmt="html"),
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "500" in err


def test_cmd_share_http_sink_url_error_returns_1(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-http-down", prompt="down")

    def _boom(*args: Any, **kwargs: Any) -> tuple[int, str]:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(share_cmd, "send_http", _boom)
    rc = share_cmd.cmd_share(
        _share_args(target="otter-http-down", sink="http", fmt="html"),
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "HTTP share failed" in err


def test_cmd_share_http_sink_passes_url_override(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``share_url`` on the namespace must reach :func:`send_http`."""
    _make_session(fake_home, "otter-http-url", prompt="x")

    captured: dict[str, Any] = {}

    def _spy(body: str, fmt: str, **kw: Any) -> tuple[int, str]:
        captured.update(kw)
        return (200, "{}")

    monkeypatch.setattr(share_cmd, "send_http", _spy)
    share_cmd.cmd_share(
        _share_args(
            target="otter-http-url",
            sink="http",
            fmt="json",
            url="http://override.invalid/path",
        ),
    )
    assert captured.get("url") == "http://override.invalid/path"


# ---------------------------------------------------------------------------
# dispatch_share
# ---------------------------------------------------------------------------


def test_dispatch_share_returns_none_when_other_subcommand() -> None:
    args = argparse.Namespace(
        subcommand="sessions",
        share_command=None,
        share_target=None,
        share_sink="file",
        share_format="html",
        share_url=None,
        sub_target=None,
    )
    assert share_cmd.dispatch_share(args) is None


def test_dispatch_share_routes_to_cmd_share(
    fake_home: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(fake_home, "otter-dispatch", prompt="dispatch prompt")
    rc = share_cmd.dispatch_share(
        _share_args(target="otter-dispatch", sink="stdout", fmt="md"),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "dispatch prompt" in out


# ---------------------------------------------------------------------------
# Trademark hygiene — the default URL must not point at the upstream.
# ---------------------------------------------------------------------------


def test_default_share_url_is_local_placeholder() -> None:
    """The default share URL must be a local placeholder, not third-party."""
    assert share_cmd.DEFAULT_SHARE_URL.startswith(("http://localhost", "http://127."))
