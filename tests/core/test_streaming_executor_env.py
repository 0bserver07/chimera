"""Regression: StreamingToolExecutor must pass a real env to tools.

Previously ``StreamingToolExecutor._execute`` called
``tool.async_execute(args, None)``. Tools that require an environment
(``bash``, ``list_files``, ``search``, etc.) have ``assert env is not None``,
and the bare AssertionError was caught and swallowed into
``ToolResult(output="", error="")``. The agent silently retried different
tools until max_turns with no progress. This lock the fix in place.
"""
from __future__ import annotations

import pytest

from chimera.core.streaming_executor import StreamingToolExecutor
from chimera.tools.bash import BashTool
from chimera.tools.list_files import ListFilesTool
from chimera.types import ToolCall


@pytest.mark.asyncio
async def test_streaming_executor_passes_default_env_to_bash(tmp_path):
    """Bash tool should produce real output when given no explicit env."""
    (tmp_path / "marker.txt").write_text("hello")
    import os
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        executor = StreamingToolExecutor([BashTool()])
        await executor.submit(ToolCall(id="1", name="bash", arguments={"command": "cat marker.txt"}))
        results = await executor.collect()
    finally:
        os.chdir(old)

    assert len(results) == 1
    _, tr = results[0]
    assert tr.success, f"bash failed: {tr.error!r}"
    assert tr.output.strip() == "hello", f"unexpected output: {tr.output!r}"


@pytest.mark.asyncio
async def test_streaming_executor_passes_default_env_to_list_files(tmp_path):
    """list_files should return real entries when given no explicit env."""
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    import os
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        executor = StreamingToolExecutor([ListFilesTool()])
        await executor.submit(ToolCall(id="1", name="list_files", arguments={"path": "."}))
        results = await executor.collect()
    finally:
        os.chdir(old)

    assert len(results) == 1
    _, tr = results[0]
    assert tr.success, f"list_files failed: {tr.error!r}"
    assert "a.py" in tr.output, f"expected a.py in output, got: {tr.output!r}"
    assert "b.py" in tr.output


@pytest.mark.asyncio
async def test_streaming_executor_uses_provided_env(tmp_path):
    """Explicit env should override the default."""
    (tmp_path / "scoped.txt").write_text("scoped-content")
    from chimera.env.local import LocalEnvironment
    env = LocalEnvironment(str(tmp_path))
    executor = StreamingToolExecutor([BashTool()], env=env)
    await executor.submit(ToolCall(id="1", name="bash", arguments={"command": "cat scoped.txt"}))
    results = await executor.collect()

    _, tr = results[0]
    assert tr.success
    assert "scoped-content" in tr.output
