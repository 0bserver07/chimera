"""Tests for chimera.shrew.output_parser — repair text-mode tool calls.

Six groups:

1. Triple-fenced ``tool`` block parsing.
2. ``<tool_call>`` XML wrapper parsing.
3. Bare JSON ``{"name":..., "arguments":...}`` parsing.
4. Python-shorthand ``tool_name(arg=val, ...)`` parsing.
5. Cross-format precedence + de-duplication.
6. Convenience helpers (looks_like_tool_call, strip_tool_calls,
   to_tool_calls).

All hermetic — pure function tests, no providers, no I/O.
"""
from __future__ import annotations

import pytest

from chimera.shrew.output_parser import (
    MAX_PARSE_LENGTH,
    ParsedToolCall,
    looks_like_tool_call,
    parse_tool_calls,
    strip_tool_calls,
    to_tool_calls,
)
from chimera.types import ToolCall


# ---------------------------------------------------------------------------
# 1. Triple-fenced
# ---------------------------------------------------------------------------


class TestFencedBlocks:
    def test_basic_tool_fence(self) -> None:
        text = (
            "Sure, I'll run that.\n"
            "```tool\n"
            '{"name": "bash", "arguments": {"cmd": "ls -la"}}\n'
            "```\n"
            "Done."
        )
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].name == "bash"
        assert hits[0].arguments == {"cmd": "ls -la"}
        assert hits[0].source == "fence"

    def test_tool_call_fence_alias(self) -> None:
        text = '```tool_call\n{"name": "read", "arguments": {"path": "/tmp/x"}}\n```'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].name == "read"

    def test_json_fence_alias(self) -> None:
        text = '```json\n{"name": "search", "arguments": {"q": "foo"}}\n```'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].source == "fence"

    def test_invalid_json_in_fence_skipped(self) -> None:
        text = "```tool\n{not real json}\n```"
        assert parse_tool_calls(text) == []

    def test_multiple_fences(self) -> None:
        text = (
            "```tool\n"
            '{"name": "read", "arguments": {"path": "/a"}}\n'
            "```\n"
            "Then:\n"
            "```tool\n"
            '{"name": "bash", "arguments": {"cmd": "ls"}}\n'
            "```"
        )
        hits = parse_tool_calls(text)
        assert [h.name for h in hits] == ["read", "bash"]


# ---------------------------------------------------------------------------
# 2. <tool_call> XML wrapper
# ---------------------------------------------------------------------------


class TestXmlWrapper:
    def test_basic_xml(self) -> None:
        text = '<tool_call>{"name": "bash", "arguments": {"cmd": "pwd"}}</tool_call>'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].name == "bash"
        assert hits[0].source == "xml"

    def test_xml_with_whitespace(self) -> None:
        text = (
            "<tool_call>\n"
            '  {"name": "edit", "arguments": {"path": "/foo", "old": "a", "new": "b"}}\n'
            "</tool_call>"
        )
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments["path"] == "/foo"

    def test_unbalanced_xml_falls_back_to_json(self) -> None:
        # Missing closing </tool_call>: XML pattern doesn't match, but
        # the inner bare JSON object is still a valid tool call shape
        # so the bare-JSON parser picks it up.
        text = '<tool_call>{"name": "bash", "arguments": {}}'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].source == "json"


# ---------------------------------------------------------------------------
# 3. Bare JSON object
# ---------------------------------------------------------------------------


class TestBareJson:
    def test_basic_bare(self) -> None:
        text = 'I will run: {"name": "bash", "arguments": {"cmd": "ls"}}'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].name == "bash"
        assert hits[0].source == "json"

    def test_bare_with_nested_args(self) -> None:
        text = '{"name": "edit", "arguments": {"path": "/x", "edits": {"old": "a"}}}'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments["edits"] == {"old": "a"}

    def test_bare_requires_arguments_field(self) -> None:
        # Missing "arguments" key → not a tool call.
        text = '{"name": "bash", "cmd": "ls"}'
        assert parse_tool_calls(text) == []

    def test_bare_inside_prose_is_ok(self) -> None:
        text = (
            "Here's my plan, then I'll call: "
            '{"name": "search", "arguments": {"q": "TODO"}}.\n'
            "Then I'll review."
        )
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments == {"q": "TODO"}


# ---------------------------------------------------------------------------
# 4. Python-shorthand function call
# ---------------------------------------------------------------------------


class TestFunctionShorthand:
    def test_basic_function_call(self) -> None:
        text = "I'll run: bash(cmd=\"ls -la\")"
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].name == "bash"
        assert hits[0].arguments == {"cmd": "ls -la"}
        assert hits[0].source == "function"

    def test_function_with_multiple_args(self) -> None:
        text = "edit(path=\"/tmp/x\", old=\"a\", new=\"b\")"
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments == {"path": "/tmp/x", "old": "a", "new": "b"}

    def test_function_with_int_and_bool(self) -> None:
        text = "read(path=\"/foo\", offset=10, follow=true)"
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments == {"path": "/foo", "offset": 10, "follow": True}

    def test_function_colon_separator(self) -> None:
        text = "search(q: \"todo\", limit: 10)"
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments == {"q": "todo", "limit": 10}

    def test_function_single_quoted(self) -> None:
        text = "bash(cmd='echo hi')"
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments == {"cmd": "echo hi"}

    def test_function_blacklisted_keywords_skipped(self) -> None:
        # ``print`` is on the blacklist — it's a Python builtin, not a tool.
        text = "First print(x=1) then bash(cmd=\"ls\")"
        hits = parse_tool_calls(text)
        # Only bash should have been parsed.
        assert len(hits) == 1
        assert hits[0].name == "bash"

    def test_function_mid_sentence_no_match(self) -> None:
        # ``bash`` here has no leading whitespace boundary — won't match.
        text = "the_bash(cmd=\"ls\")"  # underscore = not identifier-shaped at start
        hits = parse_tool_calls(text)
        # ``bash(...)`` would match if the boundary regex allowed mid-word,
        # but the leading ``_`` makes it part of an identifier.
        assert all(h.name != "bash" for h in hits)


# ---------------------------------------------------------------------------
# 5. Cross-format precedence
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_fence_overrides_inner_bare_json(self) -> None:
        # A fence containing a bare-JSON-shape body should be parsed
        # as a fence (one hit), not double-counted.
        text = (
            "```tool\n"
            '{"name": "bash", "arguments": {"cmd": "ls"}}\n'
            "```"
        )
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].source == "fence"

    def test_xml_overrides_inner_bare_json(self) -> None:
        text = '<tool_call>{"name": "bash", "arguments": {"cmd": "x"}}</tool_call>'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].source == "xml"

    def test_results_sorted_by_position(self) -> None:
        text = (
            "First: bash(cmd=\"a\")\n"
            "Then: <tool_call>{\"name\": \"read\", \"arguments\": {\"path\": \"/x\"}}</tool_call>"
        )
        hits = parse_tool_calls(text)
        assert len(hits) == 2
        # Positions must be ascending.
        assert hits[0].span[0] < hits[1].span[0]


# ---------------------------------------------------------------------------
# 6. Helpers
# ---------------------------------------------------------------------------


class TestLooksLikeToolCall:
    def test_empty_input(self) -> None:
        assert not looks_like_tool_call("")

    def test_plain_prose(self) -> None:
        assert not looks_like_tool_call("Hello, world!")

    def test_fence_detected(self) -> None:
        assert looks_like_tool_call("```tool\n{}\n```")

    def test_xml_detected(self) -> None:
        assert looks_like_tool_call("<tool_call>x</tool_call>")

    def test_function_detected(self) -> None:
        assert looks_like_tool_call("bash(cmd=\"x\")")


class TestStripToolCalls:
    def test_strips_in_reverse_order(self) -> None:
        text = (
            "Plan:\n"
            "```tool\n"
            '{"name": "bash", "arguments": {}}\n'
            "```\n"
            "Done."
        )
        hits = parse_tool_calls(text)
        stripped = strip_tool_calls(text, hits)
        assert "tool" not in stripped or "```" not in stripped
        assert "Plan:" in stripped
        assert "Done." in stripped

    def test_strips_nothing_if_no_calls(self) -> None:
        text = "Just prose."
        assert strip_tool_calls(text, []) == text


class TestToToolCalls:
    def test_converts_to_tool_call(self) -> None:
        parsed = ParsedToolCall(name="bash", arguments={"cmd": "ls"})
        out = to_tool_calls([parsed])
        assert len(out) == 1
        assert isinstance(out[0], ToolCall)
        assert out[0].name == "bash"
        assert out[0].arguments == {"cmd": "ls"}
        assert out[0].id.startswith("shrew-")


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_input(self) -> None:
        assert parse_tool_calls("") == []

    def test_oversize_input_capped(self) -> None:
        # Input larger than MAX_PARSE_LENGTH gets truncated; we still
        # parse hits up to the cap.
        prefix = "x" * (MAX_PARSE_LENGTH - 100)
        # Place a fence near the end of the cap window.
        text = prefix + "```tool\n" + '{"name": "bash", "arguments": {}}' + "\n```"
        hits = parse_tool_calls(text)
        # The fence is within the first MAX_PARSE_LENGTH chars.
        assert len(hits) == 1

    def test_invalid_args_skipped(self) -> None:
        # ``arguments`` is a list — must be rejected.
        text = '{"name": "bash", "arguments": ["ls"]}'
        assert parse_tool_calls(text) == []

    def test_alternate_keys_accepted(self) -> None:
        # ``args`` and ``parameters`` are accepted as aliases for
        # ``arguments`` *inside fenced blocks*.
        text = '```tool\n{"name": "bash", "args": {"cmd": "ls"}}\n```'
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].arguments == {"cmd": "ls"}

    def test_parsed_tool_call_to_tool_call_with_id(self) -> None:
        p = ParsedToolCall(name="bash", arguments={})
        tc = p.to_tool_call(call_id="custom-id-1")
        assert tc.id == "custom-id-1"
        assert tc.name == "bash"

    @pytest.mark.parametrize(
        "format_name,text,expected_name",
        [
            ("fence", '```tool\n{"name":"a","arguments":{}}\n```', "a"),
            ("xml", '<tool_call>{"name":"b","arguments":{}}</tool_call>', "b"),
            ("json", '{"name": "c", "arguments": {}}', "c"),
            ("function", "do_thing(x=1)", "do_thing"),
        ],
    )
    def test_each_format_yields_a_hit(
        self, format_name: str, text: str, expected_name: str,
    ) -> None:
        hits = parse_tool_calls(text)
        assert len(hits) == 1
        assert hits[0].name == expected_name
