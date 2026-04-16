# tests/function_synthesis/test_mock_compiler.py
from __future__ import annotations

from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.spec import FunctionSpec


def test_mock_compiler_emits_bundle_without_network():
    compiler = MockCompiler()
    spec = FunctionSpec(name="classify", description="classify sentiment")
    bundle = compiler.compile(spec)
    assert bundle.spec == spec
    assert bundle.adapter_bytes  # non-empty
    assert bundle.prompts["user_template"].strip() != ""
    assert bundle.metadata["compiler_backend"] == "mock"


def test_mock_compiler_uses_spec_description_in_system_prompt():
    spec = FunctionSpec(name="x", description="Extract the first email address.")
    bundle = MockCompiler().compile(spec)
    assert "Extract the first email address." in bundle.prompts["system"]
