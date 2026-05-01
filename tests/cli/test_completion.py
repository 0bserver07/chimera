"""Tests for ``chimera completion`` — shell-completion script generation.

Coverage:
- All three shells (bash, zsh, fish) emit a non-empty script.
- All seven coding-agent subcommands appear in the bash output.
- The ``--cli`` filter narrows the output to a single agent.
- Direct generator API (no CLI roundtrip) works for each shell.
- Unknown shells raise ``ValueError`` from the generator.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from chimera.cli.completion import (
    CODING_AGENT_CLIS,
    generate_completion,
)
from chimera.cli.main import build_parser, main


def _run_cli(*argv: str) -> str:
    """Invoke the chimera CLI in-process and return captured stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(list(argv))
    assert rc == 0, f"chimera {' '.join(argv)} exited {rc}"
    return buf.getvalue()


class TestBashCompletion:
    """Bash output is sourceable and includes the expected hooks."""

    def test_bash_starts_with_marker(self) -> None:
        out = _run_cli("completion", "bash")
        # Either a shebang or a comment header — both signal a valid script.
        first_line = out.lstrip().splitlines()[0]
        assert first_line.startswith("#!") or first_line.startswith("#")

    def test_bash_has_complete_directive(self) -> None:
        out = _run_cli("completion", "bash")
        assert "complete -F _chimera_completions chimera" in out

    def test_bash_defines_function(self) -> None:
        out = _run_cli("completion", "bash")
        assert "_chimera_completions()" in out

    def test_bash_includes_all_animal_subcommands(self) -> None:
        out = _run_cli("completion", "bash")
        for animal in CODING_AGENT_CLIS:
            assert animal in out, f"missing animal CLI '{animal}' in bash script"

    def test_bash_includes_top_level_subcommands(self) -> None:
        out = _run_cli("completion", "bash")
        for cmd in ("synthesize", "eval", "code", "review", "completion"):
            assert cmd in out


class TestZshCompletion:
    """Zsh output uses ``compdef`` and ``_arguments``."""

    def test_zsh_compdef_directive(self) -> None:
        out = _run_cli("completion", "zsh")
        assert out.lstrip().startswith("#compdef chimera")
        assert "compdef _chimera chimera" in out

    def test_zsh_uses_arguments(self) -> None:
        out = _run_cli("completion", "zsh")
        assert "_arguments" in out

    def test_zsh_lists_animal_subcommands(self) -> None:
        out = _run_cli("completion", "zsh")
        for animal in CODING_AGENT_CLIS:
            assert animal in out


class TestFishCompletion:
    """Fish output uses ``complete -c chimera`` per directive."""

    def test_fish_uses_complete_command(self) -> None:
        out = _run_cli("completion", "fish")
        assert "complete -c chimera" in out

    def test_fish_uses_subcommand_helpers(self) -> None:
        out = _run_cli("completion", "fish")
        assert "__fish_use_subcommand" in out
        assert "__fish_seen_subcommand_from" in out

    def test_fish_lists_animal_subcommands(self) -> None:
        out = _run_cli("completion", "fish")
        for animal in CODING_AGENT_CLIS:
            assert animal in out


class TestCliFilter:
    """The ``--cli`` flag narrows the generated script to one animal."""

    def test_filter_to_mink_excludes_other_animals(self) -> None:
        out = _run_cli("completion", "bash", "--cli", "mink")
        assert "mink" in out
        # Pick a few siblings that should now be absent from the case block.
        # We look for them appearing as a case label (pattern-match form).
        assert ") opts=" not in out or "otter)" not in out
        assert "ferret)" not in out
        assert "weasel)" not in out

    def test_filter_to_otter_includes_otter(self) -> None:
        out = _run_cli("completion", "bash", "--cli", "otter")
        assert "otter)" in out

    @pytest.mark.parametrize("animal", CODING_AGENT_CLIS)
    def test_filter_each_animal_runs(self, animal: str) -> None:
        # Smoke check: every supported filter value generates a non-empty
        # script for every shell.
        for shell in ("bash", "zsh", "fish"):
            out = _run_cli("completion", shell, "--cli", animal)
            assert animal in out
            assert len(out.strip()) > 0


class TestGeneratorAPI:
    """Direct calls to the generator (no CLI dispatch) work for all shells."""

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_generate_returns_non_empty(self, shell: str) -> None:
        parser = build_parser()
        script = generate_completion(parser, shell)
        assert isinstance(script, str)
        assert len(script.strip()) > 0

    def test_unknown_shell_raises(self) -> None:
        parser = build_parser()
        with pytest.raises(ValueError, match="Unsupported shell"):
            generate_completion(parser, "powershell")

    def test_filter_unknown_falls_through_to_all(self) -> None:
        # An unknown filter key should not crash — the argparse layer
        # validates the choice; the generator is forgiving.
        parser = build_parser()
        out = generate_completion(parser, "bash", cli_filter="bogus")
        assert "complete -F" in out
