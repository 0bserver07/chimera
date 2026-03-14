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

    def test_image_read_in_default_tools(self):
        from chimera.core.tool_group import DEFAULT_TOOLS
        names = [t.name for t in DEFAULT_TOOLS]
        assert "read_image" in names

    def test_import_graph_importable(self):
        from chimera.tools import ImportGraph
        assert ImportGraph is not None


class TestAgentTools:
    def test_agent_tools_contains_default_tools(self):
        from chimera.core.tool_group import DEFAULT_TOOLS, AGENT_TOOLS
        default_names = {t.name for t in DEFAULT_TOOLS}
        agent_names = {t.name for t in AGENT_TOOLS}
        assert default_names.issubset(agent_names)

    def test_agent_tools_includes_think(self):
        from chimera.core.tool_group import AGENT_TOOLS
        names = {t.name for t in AGENT_TOOLS}
        assert "think" in names

    def test_agent_tools_includes_todo(self):
        from chimera.core.tool_group import AGENT_TOOLS
        names = {t.name for t in AGENT_TOOLS}
        assert "todo" in names

    def test_agent_tools_does_not_include_dmail(self):
        from chimera.core.tool_group import AGENT_TOOLS
        names = {t.name for t in AGENT_TOOLS}
        assert "dmail" not in names

    def test_agent_tools_does_not_include_ask_user(self):
        from chimera.core.tool_group import AGENT_TOOLS
        names = {t.name for t in AGENT_TOOLS}
        assert "ask_user" not in names
