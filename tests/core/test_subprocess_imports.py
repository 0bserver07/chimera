"""Subprocess-isolated regression tests for FIX-D circular-import flake.

W2 found that ``chimera/core/tool_group.py:_make_default_tools`` could trigger a
circular import when ``chimera.tools.task_tool`` was the first symbol imported.
FIX-C closed it via ``functools.cache`` + module-level ``__getattr__``.

The unit tests at ``tests/core/test_tool_group_lazy.py`` run inside the same
interpreter as the rest of the suite, where the import order has already been
settled by collection. To genuinely catch a regression we have to spawn a fresh
Python so each scenario starts with an empty ``sys.modules`` cache.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Tuple

TIMEOUT_SECONDS = 30
FORBIDDEN_STDERR_TOKENS = ("ImportError", "RecursionError", "RuntimeError")


def _run(snippet: str) -> Tuple[subprocess.CompletedProcess[str], None]:
    """Run *snippet* in a fresh subprocess and return the completed process."""
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    return completed, None


def _assert_clean(completed: subprocess.CompletedProcess[str]) -> None:
    """Assert subprocess exited 0 and stderr is free of import-cycle tokens."""
    assert completed.returncode == 0, (
        f"subprocess exit={completed.returncode}\n"
        f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
    )
    for token in FORBIDDEN_STDERR_TOKENS:
        assert token not in completed.stderr, (
            f"forbidden token {token!r} in stderr:\n{completed.stderr}"
        )


def test_task_tool_first() -> None:
    """Importing task_tool first must not break tool_group.AGENT_TOOLS."""
    snippet = (
        "from chimera.tools.task_tool import TaskTool; "
        "from chimera.core.tool_group import AGENT_TOOLS; "
        "print('ok')"
    )
    completed, _ = _run(snippet)
    _assert_clean(completed)
    assert completed.stdout.strip() == "ok"


def test_tool_group_first() -> None:
    """Importing tool_group first must still allow task_tool import."""
    snippet = (
        "from chimera.core.tool_group import AGENT_TOOLS; "
        "from chimera.tools.task_tool import TaskTool; "
        "print('ok')"
    )
    completed, _ = _run(snippet)
    _assert_clean(completed)
    assert completed.stdout.strip() == "ok"


def test_mink_first() -> None:
    """Importing chimera.mink.cli first must not deadlock or cycle."""
    snippet = "from chimera.mink import cli; print('ok')"
    completed, _ = _run(snippet)
    _assert_clean(completed)
    assert completed.stdout.strip() == "ok"


def test_chimera_core_first_then_tools() -> None:
    """Importing chimera.core then chimera.tools.task_tool must succeed."""
    snippet = (
        "import chimera.core; "
        "import chimera.tools.task_tool; "
        "print('ok')"
    )
    completed, _ = _run(snippet)
    _assert_clean(completed)
    assert completed.stdout.strip() == "ok"


def test_default_tools_resolved_in_subprocess() -> None:
    """AGENT_TOOLS must lazily resolve to a non-empty tuple in a fresh process."""
    snippet = (
        "from chimera.core.tool_group import AGENT_TOOLS; "
        "assert len(AGENT_TOOLS) > 0; "
        "print(len(AGENT_TOOLS))"
    )
    completed, _ = _run(snippet)
    _assert_clean(completed)
    count_str = completed.stdout.strip()
    assert count_str.isdigit(), f"expected integer stdout, got {completed.stdout!r}"
    assert int(count_str) > 0, f"expected positive AGENT_TOOLS count, got {count_str}"
