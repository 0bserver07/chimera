"""Unit tests for the structured final-answer submit tool."""

from __future__ import annotations

from chimera.tools.submit import SUBMIT_TOOL_NAME, SubmitTool


def test_records_answer_in_metadata() -> None:
    tool = SubmitTool()
    res = tool.execute({"answer": "def f():\n    return 1"}, None)
    assert res.error is None
    assert res.metadata["final_answer"] == "def f():\n    return 1"
    assert tool.name == SUBMIT_TOOL_NAME


def test_rejects_empty_or_missing_answer() -> None:
    tool = SubmitTool()
    assert tool.execute({"answer": "   "}, None).error is not None
    assert tool.execute({}, None).error is not None
    assert tool.execute({"answer": 42}, None).error is not None
