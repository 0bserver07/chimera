"""Tests for chimera.skills.loader — Phase 7."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from chimera.skills.loader import SkillLoader


class TestSkillLoader:
    """SkillLoader parses .md files with YAML frontmatter."""

    def test_loads_skill_from_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir)
            skill_file = skill_dir / "code-review.md"
            skill_file.write_text(
                "---\n"
                "name: code-review\n"
                "description: Review code for issues\n"
                "allowed_tools:\n"
                "  - bash\n"
                "  - read\n"
                "model: opus\n"
                "---\n"
                "Please review the following code: $ARGUMENTS\n"
            )

            loader = SkillLoader([skill_dir])
            definitions = asyncio.run(loader.load_all())
            assert len(definitions) == 1

            defn = definitions[0]
            assert defn.name == "code-review"
            assert defn.description == "Review code for issues"
            assert defn.allowed_tools == ["bash", "read"]
            assert defn.model == "opus"
            assert "$ARGUMENTS" in defn.prompt_content
            assert defn.source_path == skill_file
