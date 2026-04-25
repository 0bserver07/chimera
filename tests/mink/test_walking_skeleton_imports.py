"""Walking-skeleton import + instantiation smoke tests.

The published ``examples/mink_walking_skeleton.py`` is the developer-
facing entrypoint a new contributor copy/pastes to drive the Chimera
ReAct loop. If its imports break or a tool constructor signature drifts,
the whole onboarding story breaks.

These tests assert:

1. The walking-skeleton script imports cleanly under the same module
   load order it uses at runtime.
2. ``TodoTool(persist=True)`` — the tricky call inside the skeleton —
   constructs without raising. (Historically a planned ``persist`` ->
   ``persist_path`` rename would have broken this.)

The tests are intentionally subprocess-based for the import check so
the parent test process's already-loaded ``chimera.tools`` modules
don't mask a circular-import regression.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKELETON = REPO_ROOT / "examples" / "mink_walking_skeleton.py"


def test_walking_skeleton_module_imports_cleanly() -> None:
    """Run the skeleton through ``python -c`` in a fresh interpreter so
    the import graph is exercised exactly as a new user would hit it.
    No prompt is supplied; we exit before ``main()`` runs."""
    assert SKELETON.exists(), f"missing walking-skeleton at {SKELETON}"
    code = (
        "import importlib.util as u, sys\n"
        f"spec = u.spec_from_file_location('mink_skel', r'{SKELETON}')\n"
        "mod = u.module_from_spec(spec)\n"
        "sys.modules['mink_skel'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "print('IMPORTED_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"walking-skeleton failed to import (exit={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert "IMPORTED_OK" in proc.stdout


def test_todo_tool_persist_true_constructs(tmp_path) -> None:
    """``TodoTool(persist=True)`` is the load-bearing call inside the
    skeleton. Constructing one in a fresh interpreter (matching the
    skeleton's own import order) must not raise.

    Runs under an isolated ``HOME`` and ``cwd`` so the project's real
    ``.chimera/todo.json`` (if any) doesn't leak in and so the test
    doesn't write into the contributor's actual user-scope mirror at
    ``~/.chimera/projects/<sha>/todo.json``.
    """
    code = (
        # Match the skeleton's own import order: chimera.core first
        # (to seed the tool_group bootstrap), then chimera.tools.todo.
        "from chimera.core.agent import Agent  # noqa: F401\n"
        "from chimera.tools.todo import TodoTool\n"
        # Don't assert the item list — we just need the constructor to
        # not raise. The persisted state is intentionally allowed to
        # rehydrate; that's the whole point of persist=True.
        "t = TodoTool(persist=True)\n"
        "_ = t.items\n"
        "print('TODO_OK')\n"
    )
    import os as _os
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    isolated_cwd = tmp_path / "cwd"
    isolated_cwd.mkdir()
    env = dict(_os.environ)
    env["HOME"] = str(fake_home)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(isolated_cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, (
        f"TodoTool(persist=True) raised (exit={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert "TODO_OK" in proc.stdout
