"""Tests for chimera.core.auto_format — Phase 9."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.core.auto_format import AutoFormatter


class TestDetectFormatters:
    """Formatter detection from project configuration."""

    def test_detect_formatter_from_config_file(self, tmp_path: Path):
        """Creating .prettierrc in project dir should detect prettier."""
        (tmp_path / ".prettierrc").write_text("{}")
        af = AutoFormatter(tmp_path)
        detected = af.detect_formatters()
        assert ".js" in detected
        assert detected[".js"].name == "prettier"
        assert ".ts" in detected
        assert detected[".ts"].name == "prettier"

    def test_detect_formatter_from_package_json(self, tmp_path: Path):
        """package.json with ruff dep should detect ruff for .py files."""
        pkg = {"devDependencies": {"ruff": "^0.4"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        af = AutoFormatter(tmp_path)
        detected = af.detect_formatters()
        assert ".py" in detected
        assert detected[".py"].name == "ruff"

    def test_no_formatter_for_unknown_extension(self, tmp_path: Path):
        """Unknown extensions should not have a formatter."""
        af = AutoFormatter(tmp_path)
        detected = af.detect_formatters()
        assert ".xyz" not in detected
        assert ".wat" not in detected

    def test_always_available_formatters(self, tmp_path: Path):
        """gofmt should be detected for .go even with no config files."""
        af = AutoFormatter(tmp_path)
        detected = af.detect_formatters()
        assert ".go" in detected
        assert detected[".go"].name == "gofmt"

    @pytest.mark.asyncio
    async def test_format_file_formatter_not_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When the formatter binary is not found, return (True, '') gracefully."""
        import shutil

        # Force shutil.which → None so the "not installed" branch runs even on
        # machines that actually have gofmt (CI runners preinstall Go).
        monkeypatch.setattr(shutil, "which", lambda _cmd: None)
        af = AutoFormatter(tmp_path)
        success, output = await af.format_file("nonexistent.go")
        assert success is True
        assert output == ""

    def test_cache_invalidation(self, tmp_path: Path):
        """Calling detect_formatters twice returns the same cached result."""
        (tmp_path / ".prettierrc").write_text("{}")
        af = AutoFormatter(tmp_path)
        result1 = af.detect_formatters()
        result2 = af.detect_formatters()
        assert result1 is result2
        assert af._cache_valid is True
