"""Regression tests for AUDIT.md M-22: ``--allowed-tools`` filters AGENT_TOOLS.

Pre-fix the flag was parsed (``args.allowed_tools``) but never read by
``_run_print_mode``, so the agent always saw the full tool set. The fix
extracts the filter into :func:`_filter_allowed_tools` and treats unknown
tool names as fatal (exit 2 with the valid list on stderr).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

# WHY: chimera.mink.cli (transitively) imports chimera.cli.render which
# imports rich (mink extra). The subprocess CLI test below would fail
# in environments without the extra installed; skip the whole file
# cleanly when rich is missing.
pytest.importorskip("rich")


def test_m22_filter_keeps_only_named_tools_case_insensitive() -> None:
    """``--allowed-tools=Bash`` → only the Bash tool survives."""
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.mink.cli import _filter_allowed_tools

    tools = list(AGENT_TOOLS)
    # WHY: case-insensitive — frontmatter style ``Bash`` should match the
    # canonical lowercase ``bash`` name.
    kept = _filter_allowed_tools(tools, "Bash")
    kept_names = [t.name for t in kept]
    assert kept_names == ["bash"], kept_names


def test_m22_filter_no_filter_returns_full_set() -> None:
    """Empty / whitespace-only input must leave the tool list untouched."""
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.mink.cli import _filter_allowed_tools

    tools = list(AGENT_TOOLS)
    assert [t.name for t in _filter_allowed_tools(tools, "")] == [
        t.name for t in tools
    ]
    assert [t.name for t in _filter_allowed_tools(tools, "   ")] == [
        t.name for t in tools
    ]


def test_m22_filter_unknown_tool_raises_with_valid_list() -> None:
    """Unknown name → :class:`_UnknownAllowedTool` carrying the valid list."""
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.mink.cli import _filter_allowed_tools, _UnknownAllowedTool

    tools = list(AGENT_TOOLS)
    with pytest.raises(_UnknownAllowedTool) as excinfo:
        _filter_allowed_tools(tools, "nope_no_such_tool")
    msg = str(excinfo.value)
    assert "unknown tool 'nope_no_such_tool'" in msg, msg
    assert "Valid tools:" in msg, msg
    # WHY: every real tool name should appear in the hint so users can
    # debug typos without consulting docs.
    for name in ("bash", "read_file", "write_file"):
        assert name in msg, f"valid tool {name!r} missing from hint: {msg!r}"


def test_m22_filter_multi_name_keeps_all_matches() -> None:
    """Multiple comma-separated names all survive."""
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.mink.cli import _filter_allowed_tools

    tools = list(AGENT_TOOLS)
    kept = _filter_allowed_tools(tools, "bash,read_file")
    assert {t.name for t in kept} == {"bash", "read_file"}


def test_m22_run_print_exits_2_on_unknown_allowed_tool(tmp_path: Path) -> None:
    """End-to-end CLI: an unknown ``--allowed-tools`` value must exit 2."""
    # WHY: drive the CLI as a subprocess so we exercise the real argparse
    # surface + the env.cleanup() return path. We pass --no-save to keep
    # the test hermetic and a synthetic --print so we hit _run_print_mode.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "chimera.cli.main",
            "mink",
            "--print",
            "noop",
            "--allowed-tools",
            "definitely_not_a_tool",
            "--no-save",
            "--cwd",
            str(tmp_path),
            "--output-format",
            "text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2, (
        f"expected exit 2, got {proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "unknown tool" in proc.stderr, proc.stderr
    assert "Valid tools:" in proc.stderr, proc.stderr


def test_m22_args_namespace_filter_path_is_wired_in_source() -> None:
    """Audit guard: the production code must reference the filter helper.

    Pin the wiring lexically so a refactor that drops the call leaves a
    clear failure marker.
    """
    src = (
        Path(__file__).parent.parent.parent / "chimera" / "mink" / "cli.py"
    ).read_text()
    assert "_filter_allowed_tools" in src, (
        "M-22 regression: _filter_allowed_tools is no longer called in "
        "chimera/mink/cli.py"
    )
    assert "_UnknownAllowedTool" in src, (
        "M-22 regression: _UnknownAllowedTool is no longer caught for the "
        "exit-2 stderr path"
    )


def test_m22_args_default_does_not_filter() -> None:
    """When ``args.allowed_tools`` is empty, the filter is a no-op.

    Smoke against argparse to confirm the default value triggers the
    early-return branch in :func:`_filter_allowed_tools`.
    """
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.mink.cli import _filter_allowed_tools, add_arguments

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args([])
    assert args.allowed_tools == ""
    tools = list(AGENT_TOOLS)
    out = _filter_allowed_tools(tools, args.allowed_tools)
    assert [t.name for t in out] == [t.name for t in tools]
