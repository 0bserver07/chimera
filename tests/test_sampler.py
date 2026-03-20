# tests/test_sampler.py
"""Tests for chimera.core.sampler — parallel completion sampling."""
from unittest.mock import MagicMock

from chimera.core.sampler import (
    ActionSampler,
    SampledResult,
    default_scorer,
    tool_call_scorer,
)
from chimera.providers.base import Response
from chimera.types import Message, ToolCall


def _make_response(content: str, tool_calls: list | None = None) -> Response:
    return Response(
        content=content,
        tool_calls=tool_calls or [],
        usage={"input_tokens": 10, "output_tokens": len(content)},
    )


class TestDefaultScorer:
    def test_scores_by_length(self):
        short = _make_response("hi")
        long = _make_response("hello world, this is a longer response")
        assert default_scorer(long) > default_scorer(short)


class TestToolCallScorer:
    def test_prefers_tool_calls(self):
        no_tools = _make_response("just text")
        with_tools = _make_response("text", [ToolCall(id="1", name="bash", arguments={})])
        assert tool_call_scorer(with_tools) > tool_call_scorer(no_tools)


class TestActionSampler:
    def test_sample_returns_best(self):
        responses = [
            _make_response("short"),
            _make_response("this is the longest response of the three"),
            _make_response("medium length"),
        ]
        provider = MagicMock()
        provider.complete.side_effect = responses
        sampler = ActionSampler(provider, n=3)
        result = sampler.sample([Message.user("test")])
        assert isinstance(result, SampledResult)
        assert result.best.content == "this is the longest response of the three"
        assert result.best_index == 1
        assert len(result.all_responses) == 3

    def test_sample_sequential(self):
        responses = [
            _make_response("a"),
            _make_response("ab"),
            _make_response("abc"),
        ]
        provider = MagicMock()
        provider.complete.side_effect = responses
        sampler = ActionSampler(provider, n=3)
        result = sampler.sample_sequential([Message.user("test")])
        assert result.best.content == "abc"
        assert result.best_index == 2

    def test_custom_scorer(self):
        responses = [
            _make_response("best answer"),
            _make_response("the longest answer but not best"),
            _make_response("ok"),
        ]
        provider = MagicMock()
        provider.complete.side_effect = responses

        # Custom scorer: prefer content containing "best"
        def my_scorer(r):
            return 100.0 if "best" in r.content else 1.0

        sampler = ActionSampler(provider, n=3)
        result = sampler.sample_sequential([Message.user("test")], scorer=my_scorer)
        assert result.best.content == "best answer"
