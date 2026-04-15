from __future__ import annotations


from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.spec import FunctionSpec


class _DummyCompiler(CompilerBackend):
    def compile(self, spec: FunctionSpec) -> ChiBundle:
        return ChiBundle(
            spec=spec,
            adapter_bytes=b"DUMMY",
            prompts={"system": "", "user_template": "{input}", "stop": []},
            metadata={"compiler_backend": "dummy"},
        )


def test_compiler_backend_compile_and_save(tmp_path):
    compiler = _DummyCompiler()
    spec = FunctionSpec(name="echo", description="echo")
    bundle = compiler.compile(spec)
    out = tmp_path / "echo.chi"
    bundle.save(out)
    assert ChiBundle.load(out).metadata["compiler_backend"] == "dummy"


def test_compiler_error_is_value_error_subclass():
    assert issubclass(CompilerError, ValueError)
