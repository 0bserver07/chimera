# tests/test_tool_group.py
from chimera.core.tool_group import ToolGroup
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool


class TestToolGroup:
    def test_create_group(self):
        group = ToolGroup("file_ops", [ReadFileTool(), WriteFileTool()])
        assert group.name == "file_ops"
        assert len(group.tools) == 2

    def test_group_has_tool(self):
        group = ToolGroup("file_ops", [ReadFileTool(), WriteFileTool()])
        assert group.has("read_file")
        assert group.has("write_file")
        assert not group.has("bash")

    def test_group_get_tool(self):
        group = ToolGroup("file_ops", [ReadFileTool(), WriteFileTool()])
        tool = group.get("read_file")
        assert tool is not None
        assert tool.name == "read_file"

    def test_group_iter(self):
        group = ToolGroup("all", [ReadFileTool(), WriteFileTool(), BashTool()])
        names = [t.name for t in group]
        assert names == ["read_file", "write_file", "bash"]

    def test_group_add(self):
        group = ToolGroup("ops", [ReadFileTool()])
        group.add(BashTool())
        assert len(group.tools) == 2
        assert group.has("bash")

    def test_predefined_default_group(self):
        from chimera.core.tool_group import DEFAULT_TOOLS
        assert len(DEFAULT_TOOLS.tools) >= 3
