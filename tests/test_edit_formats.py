# tests/test_edit_formats.py
"""Tests for chimera.tools.edit_formats — multiple coder strategies."""
from chimera.tools.edit_formats import (
    DiffFormat,
    EditFormatType,
    SearchReplaceFormat,
    UdiffFormat,
    WholeFileFormat,
    get_format,
    select_format,
)


class TestWholeFileFormat:
    def test_render(self):
        fmt = WholeFileFormat()
        result = fmt.render("main.py", "x = 1\n")
        assert "```main.py" in result
        assert "x = 1" in result

    def test_parse(self):
        fmt = WholeFileFormat()
        text = "Here is the file:\n```main.py\nx = 42\n```\n"
        edits = fmt.parse(text)
        assert len(edits) == 1
        assert edits[0].path == "main.py"
        assert "x = 42" in edits[0].new_content

    def test_instructions_not_empty(self):
        assert len(WholeFileFormat().instructions()) > 0


class TestSearchReplaceFormat:
    def test_parse_block(self):
        fmt = SearchReplaceFormat()
        text = (
            "File: utils.py\n"
            "<<<<<<< SEARCH\n"
            "old code\n"
            "=======\n"
            "new code\n"
            ">>>>>>> REPLACE"
        )
        edits = fmt.parse(text)
        assert len(edits) == 1
        assert edits[0].path == "utils.py"
        assert edits[0].old_content == "old code"
        assert edits[0].new_content == "new code"


class TestDiffFormat:
    def test_parse_diff_block(self):
        fmt = DiffFormat()
        text = '```diff\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n```'
        edits = fmt.parse(text)
        assert len(edits) == 1
        assert edits[0].path == "foo.py"


class TestGetFormat:
    def test_by_enum(self):
        fmt = get_format(EditFormatType.WHOLE_FILE)
        assert isinstance(fmt, WholeFileFormat)

    def test_by_string(self):
        fmt = get_format("search_replace")
        assert isinstance(fmt, SearchReplaceFormat)


class TestSelectFormat:
    def test_small_single_file(self):
        fmt = select_format(file_count=1, total_lines=20)
        assert fmt.name == EditFormatType.WHOLE_FILE

    def test_large_multi_file(self):
        fmt = select_format(file_count=5, total_lines=1000)
        assert fmt.name == EditFormatType.UDIFF

    def test_medium_complexity(self):
        fmt = select_format(file_count=3, total_lines=100)
        assert fmt.name == EditFormatType.SEARCH_REPLACE
