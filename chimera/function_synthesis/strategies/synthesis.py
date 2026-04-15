"""FunctionSynthesisStrategy: compile a FunctionSpec into a .chi bundle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.spec import FunctionSpec


@dataclass
class FunctionSynthesisResult:
    """Result of running :class:`FunctionSynthesisStrategy`."""

    spec: FunctionSpec
    bundle_path: Path


class FunctionSynthesisStrategy:
    """Produce a ``.chi`` bundle from a :class:`FunctionSpec`.

    Unlike code-synthesis strategies (TestConvergence, CEGIS, ...), the
    output here is a neural artifact rather than source.  The strategy is
    a thin orchestration layer: it delegates compilation to the injected
    :class:`CompilerBackend` and writes the bundle to ``output_dir``.

    Args:
        compiler: A concrete :class:`CompilerBackend` (e.g. RemoteCompiler).
        output_dir: Directory to write the resulting ``<name>.chi`` file.
    """

    def __init__(
        self,
        *,
        compiler: CompilerBackend,
        output_dir: str | Path,
    ) -> None:
        self._compiler = compiler
        self._output_dir = Path(output_dir)

    def run(self, spec: FunctionSpec) -> FunctionSynthesisResult:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        bundle = self._compiler.compile(spec)
        dst = self._output_dir / f"{spec.name}.chi"
        bundle.save(dst)
        return FunctionSynthesisResult(spec=spec, bundle_path=dst)
