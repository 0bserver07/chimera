# tests/test_agents_discovery.py
"""Tests for AGENTS.md hierarchical discovery in project_discovery.py."""
import tempfile
from pathlib import Path

from chimera.config.project_discovery import (
    AgentDoc,
    discover_agents_docs,
)


class TestDiscoverAgentsDocs:
    def test_single_agents_md(self, tmp_path):
        agents = tmp_path / "AGENTS.md"
        agents.write_text("## Tools\nUse bash carefully.\n")
        result = discover_agents_docs(str(tmp_path))
        assert result is not None
        assert "Use bash carefully" in result.instructions
        assert str(agents) in result.source_paths

    def test_no_agents_md_returns_none(self, tmp_path):
        result = discover_agents_docs(str(tmp_path))
        assert result is None

    def test_child_overrides_parent_section(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        (parent / "AGENTS.md").write_text(
            "## Style\nUse tabs.\n\n## Testing\nRun pytest.\n"
        )
        (child / "AGENTS.md").write_text(
            "## Style\nUse 4 spaces.\n"
        )

        result = discover_agents_docs(str(child))
        assert result is not None
        # Child overrides the Style section
        assert "4 spaces" in result.instructions
        assert "tabs" not in result.instructions
        # Parent's Testing section is preserved
        assert "pytest" in result.instructions

    def test_dotfile_variant(self, tmp_path):
        agents = tmp_path / ".agents.md"
        agents.write_text("## Config\nUse yaml.\n")
        result = discover_agents_docs(str(tmp_path))
        assert result is not None
        assert "yaml" in result.instructions

    def test_multiple_source_paths(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        (parent / "AGENTS.md").write_text("Parent rules.\n")
        (child / "AGENTS.md").write_text("Child rules.\n")
        result = discover_agents_docs(str(child))
        assert result is not None
        assert len(result.source_paths) == 2
