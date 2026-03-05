"""Tests for CLI subcommands: review, ci-fix, research, docs, testgen, migrate, plugins."""
from __future__ import annotations

import argparse

import pytest

from chimera.cli.main import build_parser, run_docs, run_migrate, run_testgen


class TestParserBuilds:
    """Parser builds without error and recognizes all subcommands."""

    def test_build_parser(self):
        parser = build_parser()
        assert parser is not None

    @pytest.mark.parametrize("cmd", [
        "review", "ci-fix", "research", "docs", "testgen", "migrate", "plugins",
    ])
    def test_subcommand_recognized(self, cmd):
        parser = build_parser()
        # Verify the subcommand is registered by finding it in subparser actions
        for action in parser._subparsers._actions:  # type: ignore[union-attr]
            if isinstance(action, argparse._SubParsersAction):
                assert cmd in action.choices
                break
        else:
            pytest.fail("No subparsers action found")


class TestArgParsing:
    """Each subcommand's arguments are parsed correctly."""

    def test_review_args(self):
        parser = build_parser()
        args = parser.parse_args(["review", "--diff", "changes.diff", "--max-rounds", "5"])
        assert args.command == "review"
        assert args.diff == "changes.diff"
        assert args.max_rounds == 5
        assert args.model == "claude-sonnet-4-20250514"

    def test_ci_fix_args(self):
        parser = build_parser()
        args = parser.parse_args(["ci-fix", "--log", "ci.log", "--max-attempts", "2"])
        assert args.command == "ci-fix"
        assert args.log == "ci.log"
        assert args.max_attempts == 2

    def test_research_args(self):
        parser = build_parser()
        args = parser.parse_args(["research", "--question", "How does auth work?", "--workdir", "/tmp"])
        assert args.command == "research"
        assert args.question == "How does auth work?"
        assert args.workdir == "/tmp"

    def test_docs_args(self):
        parser = build_parser()
        args = parser.parse_args(["docs", "--source", "src/", "--output", "out/"])
        assert args.command == "docs"
        assert args.source == "src/"
        assert args.output == "out/"

    def test_testgen_args(self):
        parser = build_parser()
        args = parser.parse_args(["testgen", "--source", "src/", "--output", "tests/gen/"])
        assert args.command == "testgen"
        assert args.source == "src/"
        assert args.output == "tests/gen/"

    def test_migrate_args(self):
        parser = build_parser()
        args = parser.parse_args(["migrate", "--source", "src/", "--preset", "python2-to-3"])
        assert args.command == "migrate"
        assert args.source == "src/"
        assert args.preset == "python2-to-3"

    def test_plugins_search_args(self):
        parser = build_parser()
        args = parser.parse_args(["plugins", "search", "my-plugin"])
        assert args.command == "plugins"
        assert args.action == "search"
        assert args.query == "my-plugin"

    def test_plugins_install_args(self):
        parser = build_parser()
        args = parser.parse_args(["plugins", "install", "cool-tool"])
        assert args.command == "plugins"
        assert args.action == "install"
        assert args.query == "cool-tool"

    def test_plugins_uninstall_args(self):
        parser = build_parser()
        args = parser.parse_args(["plugins", "uninstall", "old-tool"])
        assert args.command == "plugins"
        assert args.action == "uninstall"
        assert args.query == "old-tool"


class TestRunDocs:
    """run_docs works with temp directories (no provider needed)."""

    def test_docs_generates_files(self, tmp_path):
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "example.py").write_text(
            '"""Example module."""\n\ndef hello(name):\n    """Say hello."""\n    return f"Hello {name}"\n'
        )
        output_dir = tmp_path / "docs"

        args = argparse.Namespace(source=str(source_dir), output=str(output_dir))
        result = run_docs(args)

        assert result == 0
        assert output_dir.exists()
        generated = list(output_dir.iterdir())
        assert len(generated) >= 1  # at least index.md + one doc file

    def test_docs_empty_source(self, tmp_path):
        source_dir = tmp_path / "empty"
        source_dir.mkdir()
        output_dir = tmp_path / "docs"

        args = argparse.Namespace(source=str(source_dir), output=str(output_dir))
        result = run_docs(args)
        assert result == 0


class TestRunMigrate:
    """run_migrate works with temp directories (no provider needed)."""

    def test_migrate_python2_to_3(self, tmp_path):
        source_dir = tmp_path / "legacy"
        source_dir.mkdir()
        (source_dir / "app.py").write_text('print "hello world"\nraw_input("Enter: ")\n')

        args = argparse.Namespace(source=str(source_dir), preset="python2-to-3")
        result = run_migrate(args)

        assert result == 0
        migrated = (source_dir / "app.py").read_text()
        assert 'print("hello world")' in migrated
        assert "input(" in migrated

    def test_migrate_invalid_preset(self, tmp_path):
        source_dir = tmp_path / "src"
        source_dir.mkdir()

        args = argparse.Namespace(source=str(source_dir), preset="nonexistent")
        result = run_migrate(args)
        assert result == 1


class TestRunTestgen:
    """run_testgen works with temp directories."""

    def test_testgen_generates_tests(self, tmp_path):
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
        )
        output_dir = tmp_path / "tests_out"

        args = argparse.Namespace(source=str(source_dir), output=str(output_dir))
        result = run_testgen(args)

        assert result == 0
        assert output_dir.exists()
        test_files = list(output_dir.iterdir())
        assert len(test_files) >= 1
        content = test_files[0].read_text()
        assert "test_add" in content
