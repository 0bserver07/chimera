"""Tests that tools work with both ops and env backends."""
from chimera.core.operations import LocalReadOps, LocalWriteOps, LocalBashOps, LocalSearchOps
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool
from chimera.tools.edit import EditFileTool
from chimera.tools.search import SearchTool
from chimera.tools.list_files import ListFilesTool
from chimera.core.tool_group import create_default_tools


def test_read_tool_with_ops(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    ops = LocalReadOps(cwd=str(tmp_path))
    tool = ReadFileTool(ops=ops)
    result = tool.execute({"path": "hello.txt"}, env=None)
    assert result.success
    assert result.output == "hello world"


def test_read_tool_file_not_found(tmp_path):
    ops = LocalReadOps(cwd=str(tmp_path))
    tool = ReadFileTool(ops=ops)
    result = tool.execute({"path": "nope.txt"}, env=None)
    assert not result.success
    assert "not found" in result.error.lower()


def test_write_tool_with_ops(tmp_path):
    write_ops = LocalWriteOps(cwd=str(tmp_path))
    tool = WriteFileTool(write_ops=write_ops)
    result = tool.execute({"path": "out.txt", "content": "hello"}, env=None)
    assert result.success
    assert (tmp_path / "out.txt").read_text() == "hello"


def test_write_tool_with_read_and_write_ops(tmp_path):
    """Write tool uses read_ops to get before-content for diff."""
    (tmp_path / "existing.txt").write_text("old content")
    read_ops = LocalReadOps(cwd=str(tmp_path))
    write_ops = LocalWriteOps(cwd=str(tmp_path))
    tool = WriteFileTool(read_ops=read_ops, write_ops=write_ops)
    result = tool.execute({"path": "existing.txt", "content": "new content"}, env=None)
    assert result.success
    assert (tmp_path / "existing.txt").read_text() == "new content"
    fc = result.metadata["file_change"]
    assert fc.before_content == "old content"


def test_bash_tool_with_ops(tmp_path):
    ops = LocalBashOps(cwd=str(tmp_path))
    tool = BashTool(ops=ops)
    result = tool.execute({"command": "echo hi"}, env=None)
    assert result.success
    assert "hi" in result.output


def test_bash_tool_failing_command(tmp_path):
    ops = LocalBashOps(cwd=str(tmp_path))
    tool = BashTool(ops=ops)
    result = tool.execute({"command": "false"}, env=None)
    assert not result.success


def test_edit_tool_with_ops(tmp_path):
    (tmp_path / "code.py").write_text("def foo():\n    return 1\n")
    read_ops = LocalReadOps(cwd=str(tmp_path))
    write_ops = LocalWriteOps(cwd=str(tmp_path))
    tool = EditFileTool(read_ops=read_ops, write_ops=write_ops)
    result = tool.execute(
        {"path": "code.py", "old_string": "return 1", "new_string": "return 2"},
        env=None,
    )
    assert result.success
    assert (tmp_path / "code.py").read_text() == "def foo():\n    return 2\n"


def test_edit_tool_file_not_found(tmp_path):
    read_ops = LocalReadOps(cwd=str(tmp_path))
    tool = EditFileTool(read_ops=read_ops)
    result = tool.execute(
        {"path": "missing.py", "old_string": "x", "new_string": "y"},
        env=None,
    )
    assert not result.success
    assert "not found" in result.error.lower()


def test_search_tool_with_ops(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n")
    (tmp_path / "b.txt").write_text("world peace\n")
    ops = LocalSearchOps(cwd=str(tmp_path))
    tool = SearchTool(ops=ops)
    result = tool.execute({"pattern": "world"}, env=None)
    assert result.success
    assert "world" in result.output


def test_search_tool_no_matches(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\n")
    ops = LocalSearchOps(cwd=str(tmp_path))
    tool = SearchTool(ops=ops)
    result = tool.execute({"pattern": "zzznomatch"}, env=None)
    assert result.success
    assert "No matches found" in result.output


def test_list_files_tool_with_ops(tmp_path):
    (tmp_path / "foo.py").write_text("x")
    (tmp_path / "bar.txt").write_text("y")
    ops = LocalSearchOps(cwd=str(tmp_path))
    tool = ListFilesTool(ops=ops)
    result = tool.execute({}, env=None)
    assert result.success
    assert "foo.py" in result.output
    assert "bar.txt" in result.output


def test_list_files_tool_glob_filter(tmp_path):
    (tmp_path / "foo.py").write_text("x")
    (tmp_path / "bar.txt").write_text("y")
    ops = LocalSearchOps(cwd=str(tmp_path))
    tool = ListFilesTool(ops=ops)
    result = tool.execute({"glob": "*.py"}, env=None)
    assert result.success
    assert "foo.py" in result.output
    assert "bar.txt" not in result.output


def test_read_tool_no_ops_no_env():
    """With no ops and no env, tool should assert/error."""
    tool = ReadFileTool()
    try:
        tool.execute({"path": "a.py"}, env=None)
        assert False, "Should have raised"
    except (AssertionError, Exception):
        pass


def test_default_construction():
    """Tools with no args should work (backward compat)."""
    tool = ReadFileTool()
    assert tool._ops is None

    tool2 = WriteFileTool()
    assert tool2._read_ops is None
    assert tool2._write_ops is None

    tool3 = BashTool()
    assert tool3._ops is None


def test_create_default_tools_no_ops():
    """create_default_tools() with no ops returns a ToolGroup."""
    group = create_default_tools()
    assert group.has("read_file")
    assert group.has("write_file")
    assert group.has("bash")
    read_tool = group.get("read_file")
    assert read_tool._ops is None


def test_create_default_tools_with_ops(tmp_path):
    """create_default_tools() wires ops into each tool."""
    read_ops = LocalReadOps(cwd=str(tmp_path))
    write_ops = LocalWriteOps(cwd=str(tmp_path))
    bash_ops = LocalBashOps(cwd=str(tmp_path))

    group = create_default_tools(read_ops=read_ops, write_ops=write_ops, bash_ops=bash_ops)

    # Verify ops are wired
    assert group.get("read_file")._ops is read_ops
    assert group.get("write_file")._write_ops is write_ops
    assert group.get("bash")._ops is bash_ops

    # Verify they actually work
    (tmp_path / "test.txt").write_text("content")
    r = group.get("read_file").execute({"path": "test.txt"}, env=None)
    assert r.success
    assert r.output == "content"
