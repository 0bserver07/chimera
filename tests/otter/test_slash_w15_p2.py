"""Tests for the otter W15-2 P2 slash additions: /plan, /exec.

Covers OPENCODE G24 — plan-mode toggles. ``/plan`` flips
``session.plan_mode`` on (with optional seed text echoed back) and
``/exec`` flips it back off. Both handlers are idempotent and degrade
cleanly when the session object refuses the attribute.
"""
from __future__ import annotations

from chimera.otter.slash import (
    OTTER_SLASH_COMMANDS,
    OTTER_SLASH_HELP,
    cmd_exec,
    cmd_plan,
)


class _CapturePrinter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _FakeSession:
    plan_mode: bool = False


def test_plan_enters_mode() -> None:
    sess = _FakeSession()
    out = _CapturePrinter()
    cmd_plan(sess, None, "", out)
    assert sess.plan_mode is True
    assert any("entered plan-mode" in line for line in out.lines)


def test_plan_with_seed_echoes_seed() -> None:
    sess = _FakeSession()
    out = _CapturePrinter()
    cmd_plan(sess, None, "decompose the search index migration", out)
    assert sess.plan_mode is True
    rendered = "\n".join(out.lines)
    assert "decompose" in rendered


def test_plan_idempotent_when_already_on() -> None:
    sess = _FakeSession()
    sess.plan_mode = True
    out = _CapturePrinter()
    cmd_plan(sess, None, "", out)
    assert sess.plan_mode is True
    assert any("already" in line for line in out.lines)


def test_exec_leaves_plan_mode() -> None:
    sess = _FakeSession()
    sess.plan_mode = True
    out = _CapturePrinter()
    cmd_exec(sess, None, "", out)
    assert sess.plan_mode is False
    assert any("left plan-mode" in line for line in out.lines)


def test_exec_when_not_in_plan_mode() -> None:
    sess = _FakeSession()
    out = _CapturePrinter()
    cmd_exec(sess, None, "", out)
    assert sess.plan_mode is False
    assert any("not in plan-mode" in line for line in out.lines)


def test_palette_registers_plan_and_exec() -> None:
    for name in ("plan", "exec"):
        assert name in OTTER_SLASH_COMMANDS, f"missing /{name}"
        assert name in OTTER_SLASH_HELP, f"missing help for /{name}"


def test_plan_help_mentions_read_only() -> None:
    assert "read-only" in OTTER_SLASH_HELP["plan"].lower()
