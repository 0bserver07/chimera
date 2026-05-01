"""Tests for ``chimera.badger.slash`` — slash command palette + installer."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.badger import slash


class _FakeRepl:
    """Tiny REPL state object used for installer tests."""

    def __init__(self) -> None:
        self.commands: dict[str, slash.SlashHandler] = {}


def test_palette_includes_badger_specific_commands() -> None:
    """``/parity`` and ``/rerun`` are the load-bearing additions."""
    assert "parity" in slash.BADGER_SLASH_COMMANDS
    assert "rerun" in slash.BADGER_SLASH_COMMANDS


def test_palette_includes_shared_commands() -> None:
    """The palette also reuses the shared shell."""
    for name in ("help", "model", "tools", "exit", "compact"):
        assert name in slash.BADGER_SLASH_COMMANDS


def test_register_badger_slash_installs_all() -> None:
    repl = _FakeRepl()
    n = slash.register_badger_slash(repl)
    assert n == len(slash.BADGER_SLASH_COMMANDS)
    assert "parity" in repl.commands
    assert "rerun" in repl.commands


def test_register_badger_slash_uses_register_method() -> None:
    """When the REPL exposes ``register``, the installer prefers it."""

    class _RegisterRepl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, slash.SlashHandler, str]] = []

        def register(
            self, name: str, handler: slash.SlashHandler, help_text: str = "",
        ) -> None:
            self.calls.append((name, handler, help_text))

    repl = _RegisterRepl()
    slash.register_badger_slash(repl)
    names = [c[0] for c in repl.calls]
    assert "parity" in names
    assert "rerun" in names


def test_cmd_rerun_default_state_print() -> None:
    """``/rerun`` with no argument prints current state."""

    class _S:
        rerun_on_failure = False
        max_reruns = 2

    out_lines: list[str] = []
    slash.cmd_rerun(_S(), None, "", out_lines.append)
    assert any("rerun_on_failure=False" in line for line in out_lines)


def test_cmd_rerun_enable() -> None:
    """``/rerun on`` flips the flag."""

    class _S:
        rerun_on_failure = False
        max_reruns = 2

    s = _S()
    out_lines: list[str] = []
    slash.cmd_rerun(s, None, "on", out_lines.append)
    assert s.rerun_on_failure is True
    assert any("enabled" in line for line in out_lines)


def test_cmd_rerun_set_count() -> None:
    """``/rerun 5`` sets max_reruns and enables."""

    class _S:
        rerun_on_failure = False
        max_reruns = 2

    s = _S()
    out_lines: list[str] = []
    slash.cmd_rerun(s, None, "5", out_lines.append)
    assert s.max_reruns == 5
    assert s.rerun_on_failure is True


def test_cmd_rerun_disable() -> None:
    class _S:
        rerun_on_failure = True
        max_reruns = 2

    s = _S()
    out_lines: list[str] = []
    slash.cmd_rerun(s, None, "off", out_lines.append)
    assert s.rerun_on_failure is False


def test_cmd_rerun_invalid_value() -> None:
    class _S:
        rerun_on_failure = False
        max_reruns = 2

    out_lines: list[str] = []
    slash.cmd_rerun(_S(), None, "banana", out_lines.append)
    assert any("unrecognized" in line for line in out_lines)


def test_cmd_rerun_negative_rejected() -> None:
    class _S:
        rerun_on_failure = False
        max_reruns = 2

    out_lines: list[str] = []
    slash.cmd_rerun(_S(), None, "-1", out_lines.append)
    assert any(">= 0" in line for line in out_lines)


def test_cmd_parity_no_schema_emits_message(tmp_path: Path, monkeypatch) -> None:
    """``/parity`` with no schema prints a helpful message."""
    monkeypatch.chdir(tmp_path)
    out_lines: list[str] = []
    slash.cmd_parity(None, None, "", out_lines.append)
    assert any("no schema found" in line for line in out_lines)


def test_cmd_parity_with_schema_prints_report(tmp_path: Path) -> None:
    """``/parity <path>`` loads the schema and prints OK/FAIL."""
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text(json.dumps({"max_steps": 25}))
    out_lines: list[str] = []
    slash.cmd_parity(None, None, str(schema_path), out_lines.append)
    text = "\n".join(out_lines)
    assert "OK" in text or "FAIL" in text


def test_cmd_parity_invalid_schema_reports_error(tmp_path: Path) -> None:
    """A broken schema surfaces as a load error in the slash output."""
    schema_path = tmp_path / "PARITY.json"
    schema_path.write_text("not json")
    out_lines: list[str] = []
    slash.cmd_parity(None, None, str(schema_path), out_lines.append)
    text = "\n".join(out_lines)
    assert "load failed" in text or "FAIL" in text
