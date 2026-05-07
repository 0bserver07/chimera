"""Regression tests for the W2 circular-import bug in ``chimera.core.tool_group``.

The previous module-level ``DEFAULT_TOOLS = _make_default_tools()`` at import
time deadlocked when ``chimera.tools.task_tool`` (or any sibling under
``chimera.tools``) was the first symbol pulled in by a test or downstream
caller.  These tests pin the lazy-resolution fix so the bug cannot silently
regress.

Each test is run in a fresh subprocess so ``sys.modules`` cache state from
earlier in the suite cannot mask the bug.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(script: str) -> subprocess.CompletedProcess[str]:
    """Execute ``script`` in a fresh Python process and return the result."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_task_tool_first_then_agent_tools_resolves() -> None:
    """``from chimera.tools.task_tool import TaskTool`` followed by
    ``from chimera.core.tool_group import AGENT_TOOLS`` must succeed.

    This is the exact reproducer W2 documented in W2-REPORT.md section 3.
    """
    result = _run(
        """
        from chimera.tools.task_tool import TaskTool  # noqa: F401
        from chimera.core.tool_group import AGENT_TOOLS
        assert len(AGENT_TOOLS) > 0, "AGENT_TOOLS resolved empty"
        print("OK", len(AGENT_TOOLS))
        """
    )
    assert result.returncode == 0, (
        f"task_tool-first import broke (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert result.stdout.startswith("OK"), result.stdout


def test_tool_group_first_then_task_tool_resolves() -> None:
    """``import chimera.core.tool_group`` then ``import chimera.tools.task_tool``
    must succeed in either order without raising."""
    result = _run(
        """
        import chimera.core.tool_group
        import chimera.tools.task_tool  # noqa: F401
        # Force the lazy attribute to materialise.
        n = len(chimera.core.tool_group.DEFAULT_TOOLS)
        assert n > 0
        print("OK", n)
        """
    )
    assert result.returncode == 0, (
        f"tool_group-first import broke (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_task_tool_first_then_tool_group_module_resolves() -> None:
    """The reverse of the above: task_tool first, then bare module import."""
    result = _run(
        """
        import chimera.tools.task_tool  # noqa: F401
        import chimera.core.tool_group
        n = len(chimera.core.tool_group.AGENT_TOOLS)
        assert n > 0
        print("OK", n)
        """
    )
    assert result.returncode == 0, (
        f"task_tool-first then tool_group module broke "
        f"(exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_agent_tools_after_lazy_resolve_matches_pre_lazy_set() -> None:
    """The lazy-resolved ``AGENT_TOOLS`` ToolGroup must contain the original
    pre-lazy tool set as a subset (no removal). Additions from later waves
    (W13-G13: apply_patch, write_guard, notebook_edit, worktree, cron) are
    expected and welcome — the contract here is "no behavioural drift in the
    legacy 15".
    """
    from chimera.core.tool_group import AGENT_TOOLS, DEFAULT_TOOLS

    legacy_agent = {
        "read_file", "write_file", "edit_file", "bash", "search",
        "list_files", "test", "git", "replace_in_file", "read_image",
        "repo_map", "think", "todo", "verify_answer", "web_search",
    }
    actual_agent = {t.name for t in AGENT_TOOLS}
    missing = legacy_agent - actual_agent
    assert not missing, f"AGENT_TOOLS regressed; missing legacy tools: {missing}"

    expected_default = {"read_file", "write_file", "bash", "read_image"}
    actual_default = {t.name for t in DEFAULT_TOOLS}
    assert actual_default == expected_default, (
        f"DEFAULT_TOOLS drift; missing={expected_default - actual_default} "
        f"extra={actual_default - expected_default}"
    )


def test_lazy_tools_are_cached_singletons() -> None:
    """Repeated access to the lazy attribute must return the *same* ToolGroup
    instance — proves the ``functools.cache`` wrap is in effect.
    """
    from chimera.core.tool_group import AGENT_TOOLS as a1
    from chimera.core.tool_group import AGENT_TOOLS as a2

    assert a1 is a2, "AGENT_TOOLS lazy attribute returned different instances"
