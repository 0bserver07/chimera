"""Tests for the chimera code REPL."""
from __future__ import annotations

import argparse

import pytest

from chimera.cli.main import build_parser


class TestCodeParser:
    def test_parse_code_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "code", "--model", "gpt-4o", "--workdir", "/tmp", "--max-steps", "25",
        ])
        assert args.command == "code"
        assert args.model == "gpt-4o"
        assert args.workdir == "/tmp"
        assert args.max_steps == 25

    def test_code_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["code"])
        assert args.command == "code"
        assert args.model == "claude-sonnet-4-20250514"
        assert args.workdir == "."
        assert args.max_steps == 50

    def test_code_help(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["code", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "model" in captured.out.lower() and "workdir" in captured.out.lower()


class TestCodeModule:
    def test_default_system_prompt(self):
        from chimera.cli.code import _DEFAULT_SYSTEM
        assert "coding assistant" in _DEFAULT_SYSTEM

    def test_run_code_exit(self, monkeypatch, tmp_path):
        """REPL exits cleanly on /exit."""
        from chimera.cli.code import run_code

        # Mock create_provider to avoid needing API keys
        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr("chimera.cli.code.create_provider", lambda **kw: mock_provider)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

        args = argparse.Namespace(model="test", workdir=str(tmp_path), max_steps=10)
        result = run_code(args)
        assert result == 0

    def test_run_code_eof(self, monkeypatch, tmp_path):
        """REPL exits cleanly on EOF (Ctrl+D)."""
        from chimera.cli.code import run_code

        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr("chimera.cli.code.create_provider", lambda **kw: mock_provider)
        monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError))

        args = argparse.Namespace(model="test", workdir=str(tmp_path), max_steps=10)
        result = run_code(args)
        assert result == 0

    def test_run_code_empty_input_skipped(self, monkeypatch, tmp_path):
        """Empty lines are skipped, not sent to agent."""
        from chimera.cli.code import run_code

        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr("chimera.cli.code.create_provider", lambda **kw: mock_provider)

        inputs = iter(["", "   ", "/exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

        args = argparse.Namespace(model="test", workdir=str(tmp_path), max_steps=10)
        result = run_code(args)
        assert result == 0
