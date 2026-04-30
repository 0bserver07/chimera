"""Tests for the minimal weasel REPL (agent W5).

The REPL is exercised through :class:`MinimalRepl`, which the public
:func:`run` entry point wraps. Tests inject a fake input function and a
:class:`io.StringIO` output sink so we can drive the loop without a TTY
and assert on the rendered transcript.

Slash palette under test:

* ``/help``  — prints the four-command help block.
* ``/exit``  — breaks the loop with exit code 0.
* ``/clear`` — drops conversation history.
* ``/model`` — shows or sets the active model id.

Excluded by spec (verified absent):

* ``/agent``, ``/share``, ``/init``.

Originally W1 stubbed the REPL by delegating to
:func:`chimera.cli.code.run_code`; W5 replaces that stub with a
self-contained minimal REPL because the shared REPL ships 19 slash
commands and weasel's brand promise is "minimalism is the feature".
"""

from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

from chimera.weasel import repl as weasel_repl
from chimera.weasel.repl import MinimalRepl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repl(
    inputs: list[str],
    *,
    model: str | None = "claude-sonnet-4-6",
    max_steps: int = 50,
) -> tuple[MinimalRepl, io.StringIO]:
    """Build a :class:`MinimalRepl` driven by a scripted input list.

    The fake input function pops one entry per call and raises
    :class:`EOFError` when the script is exhausted, mimicking a closed
    stdin.
    """
    out = io.StringIO()
    iterator = iter(inputs)

    def fake_input(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError() from exc

    repl = MinimalRepl(
        model=model,
        workdir=".",
        max_steps=max_steps,
        out=out,
        input_fn=fake_input,
    )
    return repl, out


# ---------------------------------------------------------------------------
# Slash palette: scope
# ---------------------------------------------------------------------------


def test_slash_palette_is_minimal() -> None:
    """Exactly four slash commands ship by default — no more, no less."""
    assert MinimalRepl._SLASH_COMMANDS == ("/help", "/exit", "/clear", "/model")


def test_slash_palette_omits_excluded_commands() -> None:
    """``/agent``, ``/share``, ``/init`` are explicitly NOT in the palette."""
    palette = set(MinimalRepl._SLASH_COMMANDS)
    for excluded in ("/agent", "/share", "/init"):
        assert excluded not in palette, f"weasel must not ship {excluded}"


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


def test_help_lists_every_minimal_command() -> None:
    """``/help`` names each of the four palette commands."""
    repl, out = _make_repl(["/help", "/exit"])
    rc = repl.run()
    assert rc == 0
    text = out.getvalue()
    for needle in ("/help", "/exit", "/clear", "/model"):
        assert needle in text, f"/help output missing {needle}"


def test_help_does_not_mention_excluded_commands() -> None:
    """``/help`` must not advertise excluded commands."""
    repl, out = _make_repl(["/help", "/exit"])
    repl.run()
    text = out.getvalue()
    for excluded in ("/agent", "/share", "/init"):
        assert excluded not in text, f"/help leaked {excluded}"


# ---------------------------------------------------------------------------
# /exit
# ---------------------------------------------------------------------------


def test_exit_returns_zero() -> None:
    """``/exit`` cleanly exits with code 0."""
    repl, _ = _make_repl(["/exit"])
    assert repl.run() == 0


def test_eof_returns_zero() -> None:
    """EOF on stdin (Ctrl-D) is treated as ``/exit``."""
    repl, _ = _make_repl([])  # exhausted iterator -> EOFError
    assert repl.run() == 0


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------


def test_clear_drops_history() -> None:
    """``/clear`` empties the conversation history list."""
    repl, out = _make_repl(["/clear", "/exit"])
    # Pre-seed history so /clear has something to drop.
    repl.history.append(("user", "hi"))
    repl.history.append(("assistant", "hello"))
    repl.run()
    assert repl.history == []
    assert "history cleared" in out.getvalue()


# ---------------------------------------------------------------------------
# /model
# ---------------------------------------------------------------------------


def test_model_show_no_arg() -> None:
    """``/model`` with no argument prints the active model id."""
    repl, out = _make_repl(["/model", "/exit"], model="gpt-4o")
    repl.run()
    text = out.getvalue()
    assert "model: gpt-4o" in text


def test_model_set_changes_active_id() -> None:
    """``/model <id>`` mutates :attr:`MinimalRepl.model`."""
    repl, out = _make_repl(["/model claude-haiku-3.5", "/model", "/exit"])
    repl.run()
    assert repl.model == "claude-haiku-3.5"
    text = out.getvalue()
    assert "model set: claude-haiku-3.5" in text
    assert "model: claude-haiku-3.5" in text


def test_model_show_unresolved() -> None:
    """When no model is configured, ``/model`` reports ``(unresolved)``."""
    repl, out = _make_repl(["/model", "/exit"], model=None)
    repl.run()
    assert "(unresolved)" in out.getvalue()


# ---------------------------------------------------------------------------
# Unknown commands
# ---------------------------------------------------------------------------


def test_unknown_slash_command_is_friendly() -> None:
    """An unknown slash command lists the known palette without crashing."""
    repl, out = _make_repl(["/banana", "/exit"])
    rc = repl.run()
    assert rc == 0
    text = out.getvalue()
    assert "unknown command" in text
    assert "/help" in text
    assert "/exit" in text


# ---------------------------------------------------------------------------
# Empty lines + slash dispatch internals
# ---------------------------------------------------------------------------


def test_blank_line_is_noop() -> None:
    """Pressing Enter at the prompt loops without invoking the agent."""
    repl, out = _make_repl(["", "/exit"])
    rc = repl.run()
    assert rc == 0
    # Banner should be the only output beyond the empty newlines.
    assert "weasel" in out.getvalue()


def test_dispatch_slash_returns_false_only_for_exit() -> None:
    """Only ``/exit`` returns ``False`` from :meth:`dispatch_slash`."""
    repl, _ = _make_repl([])
    assert repl.dispatch_slash("/help") is True
    assert repl.dispatch_slash("/clear") is True
    assert repl.dispatch_slash("/model") is True
    assert repl.dispatch_slash("/exit") is False


# ---------------------------------------------------------------------------
# Free-text prompts forward to the agent stack
# ---------------------------------------------------------------------------


def test_free_text_calls_run_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-slash input is forwarded to :meth:`run_turn`."""
    captured: list[str] = []

    def fake_run_turn(self: MinimalRepl, prompt: str) -> str:
        captured.append(prompt)
        return f"echo:{prompt}"

    monkeypatch.setattr(MinimalRepl, "run_turn", fake_run_turn)
    repl, out = _make_repl(["hello world", "/exit"])
    rc = repl.run()
    assert rc == 0
    assert captured == ["hello world"]
    assert "echo:hello world" in out.getvalue()


def test_run_turn_handles_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing provider build surfaces a friendly stderr-like line."""

    def fake_build(self: MinimalRepl) -> Any:
        raise ValueError("no key configured")

    monkeypatch.setattr(MinimalRepl, "_build_provider", fake_build)
    repl, _ = _make_repl([])
    text = repl.run_turn("ping")
    assert text.startswith("weasel: provider error:")
    assert "no key configured" in text


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def test_run_entry_point_builds_minimal_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    """:func:`run` constructs and runs a :class:`MinimalRepl`."""
    captured: dict[str, Any] = {}

    class _FakeRepl:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def run(self) -> int:
            captured["ran"] = True
            return 7

    monkeypatch.setattr(weasel_repl, "MinimalRepl", _FakeRepl)
    args = argparse.Namespace(
        model="gpt-4o",
        cwd="/tmp",
        max_steps=12,
    )
    rc = weasel_repl.run(args)
    assert rc == 7
    assert captured["ran"] is True
    init = captured["init"]
    assert init["model"] == "gpt-4o"
    assert init["workdir"] == "/tmp"
    assert init["max_steps"] == 12


def test_run_entry_point_tolerates_missing_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ``cwd`` / ``max_steps`` fall back to defaults."""
    captured: dict[str, Any] = {}

    class _FakeRepl:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def run(self) -> int:
            return 0

    monkeypatch.setattr(weasel_repl, "MinimalRepl", _FakeRepl)
    args = argparse.Namespace(model=None)
    rc = weasel_repl.run(args)
    assert rc == 0
    init = captured["init"]
    assert init["model"] is None
    # Falls back to cwd / 50, not None.
    assert init["max_steps"] == 50
    assert init["workdir"]  # absolute path of cwd


# ---------------------------------------------------------------------------
# Banner rendering
# ---------------------------------------------------------------------------


def test_banner_mentions_help_pointer() -> None:
    """The startup banner advertises ``/help`` and ``/exit``."""
    repl, out = _make_repl(["/exit"])
    repl.run()
    text = out.getvalue()
    assert "/help" in text
    assert "/exit" in text
