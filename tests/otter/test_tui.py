"""Tests for the textual-based otter TUI prototype.

The TUI is gated behind the ``[tui]`` optional extra; every test in
this module skips when ``textual`` is not importable. Coverage targets:

* the ``--tui`` CLI flag is wired and degrades gracefully when the
  optional dep is missing;
* :func:`chimera.otter.tui.build_app` returns a working ``textual.App``;
* the app, when driven by :meth:`textual.App.run_test`, can:
    - create a session on the bound :class:`OtterServer`,
    - submit a user message via the input widget and echo it on the
      conversation panel,
    - render an agent reply (synthetic ``result`` SSE event),
    - toggle the side panel and help banner via key bindings.

We never instantiate a real :class:`Agent` — :class:`OtterServer`
accepts an ``agent_factory`` callable, so we plug a fake that records
prompts and lets us drive the streaming loop ourselves.
"""
from __future__ import annotations

import argparse
import dataclasses
from typing import Any, AsyncIterator

import pytest

from chimera.otter import cli as otter_cli
from chimera.otter.server import OtterServer

# Skip the entire module when textual is not installed — the [tui]
# extra is opt-in.
textual = pytest.importorskip("textual")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeLoopEvent:
    """Minimal LoopEvent stand-in compatible with OtterServer fan-out."""

    type: str
    data: Any
    turn: int = 0
    timestamp: float = 0.0


class _FakeAgent:
    """Streams a canned event sequence and echoes the user's text back."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def async_run_events(
        self, task: str, env: Any | None = None
    ) -> AsyncIterator[_FakeLoopEvent]:
        self.prompts.append(task)
        # One assistant chunk so the conversation panel gets a render
        # (covers the loop_event path in :meth:`OtterTUIApp._render_loop_event`).
        yield _FakeLoopEvent(
            type="assistant_chunk",
            data={"text": f"reply: {task}"},
        )

    async def async_run(self, task: str, env: Any | None = None) -> Any:
        # Compatibility with the legacy back-compat path on OtterServer.
        self.prompts.append(task)
        return {"output": f"reply: {task}", "steps": 1, "cost": 0.0, "success": True}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_agent() -> _FakeAgent:
    return _FakeAgent()


@pytest.fixture()
def server(fake_agent: _FakeAgent) -> OtterServer:
    """In-process :class:`OtterServer` — never bound to a socket."""
    return OtterServer(agent_factory=lambda _state: fake_agent)


# ---------------------------------------------------------------------------
# Module-level wiring
# ---------------------------------------------------------------------------


def test_argparse_exposes_tui_flag() -> None:
    """``chimera otter --tui`` parses without raising."""
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    parsed = parser.parse_args(["--tui"])
    assert getattr(parsed, "tui", False) is True


def test_argparse_tui_default_is_false() -> None:
    """``chimera otter`` (no flag) leaves the readline REPL as default."""
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    parsed = parser.parse_args([])
    assert getattr(parsed, "tui", False) is False


def test_build_app_returns_textual_app(server: OtterServer) -> None:
    """:func:`build_app` returns a real :class:`textual.app.App`."""
    from textual.app import App

    from chimera.otter.tui import TUIConfig, build_app

    app = build_app(server, TUIConfig(model="glm-5"))
    assert isinstance(app, App)


def test_tui_config_defaults_are_safe() -> None:
    """:class:`TUIConfig` has reasonable, non-None defaults."""
    from chimera.otter.tui import TUIConfig

    cfg = TUIConfig()
    assert cfg.model
    assert cfg.title == "Otter"
    assert cfg.cancel_timeout_s > 0


# ---------------------------------------------------------------------------
# Interactive harness — drives the live textual event loop.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tui_echoes_user_message_and_renders_reply(
    server: OtterServer, fake_agent: _FakeAgent
) -> None:
    """Type a message, see it echoed, see the synthetic reply.

    Drives the app via textual's ``App.run_test`` harness:

    1. Mount the app — expect a fresh session on the server.
    2. Type a message into the ``Input`` widget and press Enter.
    3. Wait for the SSE pump to deliver the synthetic events.
    4. Assert the user prompt and the assistant chunk are visible
       on the conversation panel (via ``RichLog.lines``).
    """
    from chimera.otter.tui import TUIConfig, build_app

    app = build_app(server, TUIConfig(model="glm-5"))
    async with app.run_test() as pilot:
        # The mount handler creates the session on the bound server.
        await pilot.pause()
        assert len(server.list_session_ids()) == 1

        # Submit a user message via the input widget.
        from textual.widgets import Input

        input_widget = pilot.app.query_one(Input)
        input_widget.value = "hello otter"
        await pilot.press("enter")

        # Drain the SSE pump — the synthetic agent yields a single
        # assistant_chunk and the server appends a terminal ``result``
        # event after the streaming generator finishes.
        for _ in range(20):
            await pilot.pause()
            if fake_agent.prompts:
                break

        # The fake agent recorded the prompt.
        assert fake_agent.prompts == ["hello otter"]

        # The conversation panel rendered the user's text and the reply.
        conversation = pilot.app.query_one("#conversation")
        rendered = "\n".join(str(line) for line in conversation.lines)
        assert "hello otter" in rendered
        assert "reply: hello otter" in rendered


@pytest.mark.asyncio
async def test_tui_toggles_help_banner_on_f1(server: OtterServer) -> None:
    """F1 toggles the help banner ``hidden`` class."""
    from chimera.otter.tui import TUIConfig, build_app

    app = build_app(server, TUIConfig())
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = pilot.app.query_one("#help_banner")
        assert "hidden" in banner.classes
        await pilot.press("f1")
        assert "hidden" not in banner.classes
        await pilot.press("f1")
        assert "hidden" in banner.classes


@pytest.mark.asyncio
async def test_tui_toggles_side_panel_on_f2(server: OtterServer) -> None:
    """F2 toggles the side panel visibility."""
    from chimera.otter.tui import TUIConfig, build_app

    app = build_app(server, TUIConfig())
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = pilot.app.query_one("#side_panel")
        # The side panel starts visible.
        assert "hidden" not in panel.classes
        await pilot.press("f2")
        assert "hidden" in panel.classes
        await pilot.press("f2")
        assert "hidden" not in panel.classes


@pytest.mark.asyncio
async def test_tui_cancel_action_when_no_pending_turn(
    server: OtterServer,
) -> None:
    """``Ctrl+C`` is a no-op when no turn is in flight (no exception)."""
    from chimera.otter.tui import TUIConfig, build_app

    app = build_app(server, TUIConfig())
    async with app.run_test() as pilot:
        await pilot.pause()
        # Trigger the action directly so we don't fight the harness's
        # own SIGINT handling (run_test() intercepts ctrl+c at the OS
        # level on some platforms).
        await pilot.app.run_action("cancel_turn")
        await pilot.pause()
        # The conversation panel surfaces a "no turn to cancel" line.
        conversation = pilot.app.query_one("#conversation")
        rendered = "\n".join(str(line) for line in conversation.lines)
        assert "no turn to cancel" in rendered


# ---------------------------------------------------------------------------
# Optional-dep guard
# ---------------------------------------------------------------------------


def test_dispatch_tui_returns_2_when_textual_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If ``chimera.otter.tui`` raises ImportError, dispatch returns 2.

    The CLI surface promises a graceful failure rather than a stack
    trace when the optional ``[tui]`` extra is missing. We simulate the
    missing dep by patching ``sys.modules`` so the import statement
    inside :func:`_dispatch_tui` raises.
    """
    import sys

    # Force re-import so our monkeypatch wins.
    sys.modules.pop("chimera.otter.tui", None)
    monkeypatch.setitem(
        sys.modules,
        "chimera.otter.tui",
        None,  # type: ignore[arg-type]
    )

    args = argparse.Namespace(
        cwd=None,
        model="glm-5",
        max_steps=10,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
        no_plugins=True,
    )
    rc = otter_cli._dispatch_tui(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "otter --tui" in err
