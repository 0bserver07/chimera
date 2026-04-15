from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.spec import FunctionSpec
from chimera.function_synthesis.strategies.synthesis import FunctionSynthesisStrategy


class _RecordingCompiler(CompilerBackend):
    def __init__(self) -> None:
        self.calls: list[FunctionSpec] = []

    def compile(self, spec: FunctionSpec) -> ChiBundle:
        self.calls.append(spec)
        return ChiBundle(
            spec=spec,
            adapter_bytes=b"A",
            prompts={"system": "", "user_template": "{input}", "stop": []},
            metadata={"compiler_backend": "recording"},
        )


def test_strategy_compiles_spec_and_saves_bundle(tmp_path):
    compiler = _RecordingCompiler()
    strategy = FunctionSynthesisStrategy(compiler=compiler, output_dir=tmp_path)
    spec = FunctionSpec(name="cls", description="classify")
    result = strategy.run(spec)

    assert result.bundle_path == tmp_path / "cls.chi"
    assert result.bundle_path.exists()
    loaded = ChiBundle.load(result.bundle_path)
    assert loaded.metadata["compiler_backend"] == "recording"
    assert compiler.calls == [spec]


def test_strategy_overwrites_existing_bundle(tmp_path):
    target = tmp_path / "cls.chi"
    target.write_bytes(b"stale")
    strategy = FunctionSynthesisStrategy(compiler=_RecordingCompiler(), output_dir=tmp_path)
    strategy.run(FunctionSpec(name="cls", description="classify"))
    assert ChiBundle.load(target).spec.name == "cls"
