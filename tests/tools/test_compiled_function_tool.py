from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec
from chimera.tools.compiled_function_tool import CompiledFunctionTool


class _StubBackend(RuntimeBackend):
    def load(self, bundle):
        self.bundle = bundle

    def invoke(self, user_input, *, max_tokens=256):
        return f"OUT[{self.bundle.spec.name}]:{user_input}"

    def close(self):
        pass


def _bundle_path(tmp_path: Path) -> Path:
    ChiBundle(
        spec=FunctionSpec(name="sentiment", description="classify pos/neg"),
        adapter_bytes=b"A",
        prompts={"system": "", "user_template": "{input}", "stop": []},
    ).save(tmp_path / "sentiment.chi")
    return tmp_path / "sentiment.chi"


def test_tool_exposes_function_name_and_description(tmp_path):
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=_StubBackend())
    tool = CompiledFunctionTool(fn)
    assert tool.name == "sentiment"
    assert "classify pos/neg" in tool.description


def test_tool_call_returns_function_output(tmp_path):
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=_StubBackend())
    tool = CompiledFunctionTool(fn)
    assert tool.execute(user_input="great movie") == "OUT[sentiment]:great movie"


def test_tool_name_override(tmp_path):
    fn = CompiledFunction.from_path(_bundle_path(tmp_path), backend=_StubBackend())
    tool = CompiledFunctionTool(fn, name="classify_sentiment")
    assert tool.name == "classify_sentiment"
