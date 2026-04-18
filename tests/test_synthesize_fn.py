# tests/test_synthesize_fn.py
"""Tests for chimera.synthesize() convenience function."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch


from chimera.synthesize import synthesize


class TestSynthesizeFunction:
    def test_returns_synthesis_result(self):
        """synthesize() returns a SynthesisResult."""
        from chimera.training.strategies.base import SynthesisResult
        from chimera.providers.base import Provider, Response
        from chimera.types import ToolCall

        class QuickProvider(Provider):
            def __init__(self):
                self._calls = 0

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self._calls += 1
                if messages and messages[-1].role == "tool":
                    return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})
                return Response(
                    content="Writing.",
                    tool_calls=[ToolCall(id=f"c{self._calls}", name="write_file", arguments={
                        "path": "calc.py",
                        "content": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
                    })],
                    usage={"input_tokens": 100, "output_tokens": 50},
                )

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "mock"

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test_calc.py").write_text(
                "from calc import add, sub\n"
                "def test_add():\n    assert add(1, 2) == 3\n"
                "def test_sub():\n    assert sub(3, 1) == 2\n"
            )
            with patch("chimera.synthesize.create_provider", return_value=QuickProvider()):
                result = synthesize(
                    "Implement a calculator",
                    tests=tmpdir,
                    workdir=tmpdir,
                    max_iterations=5,
                )
            assert isinstance(result, SynthesisResult)
            assert result.converged is True

    def test_spec_only_no_tests_dir(self):
        """synthesize() works with just a spec string (no tests dir)."""
        from chimera.providers.base import Provider, Response

        class DoneProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "mock"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("chimera.synthesize.create_provider", return_value=DoneProvider()):
                result = synthesize(
                    "Build something",
                    workdir=tmpdir,
                    max_iterations=1,
                )
            assert result is not None

    def test_max_cost_creates_callback(self):
        """max_cost parameter creates a CostLimit callback."""
        from chimera.providers.base import Provider, Response

        class DoneProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "mock"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("chimera.synthesize.create_provider", return_value=DoneProvider()):
                result = synthesize(
                    "Build something",
                    workdir=tmpdir,
                    max_cost=0.50,
                    max_iterations=1,
                )
            assert result is not None
