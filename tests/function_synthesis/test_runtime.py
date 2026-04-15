from __future__ import annotations

from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec


class _FakeBackend(RuntimeBackend):
    def __init__(self) -> None:
        self.loaded: ChiBundle | None = None
        self.calls: list[str] = []

    def load(self, bundle: ChiBundle) -> None:
        self.loaded = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        self.calls.append(user_input)
        return f"echo:{user_input}"

    def close(self) -> None:
        self.loaded = None


def _bundle(tmp_path: Path) -> Path:
    spec = FunctionSpec(name="echo", description="echoes input")
    ChiBundle(
        spec=spec,
        adapter_bytes=b"FAKE",
        prompts={"system": "echo", "user_template": "{input}", "stop": []},
    ).save(tmp_path / "echo.chi")
    return tmp_path / "echo.chi"


def test_compiled_function_loads_and_calls(tmp_path):
    backend = _FakeBackend()
    fn = CompiledFunction.from_path(_bundle(tmp_path), backend=backend)
    assert fn.name == "echo"
    assert fn("hi") == "echo:hi"
    assert backend.calls == ["hi"]


def test_compiled_function_closes_backend(tmp_path):
    backend = _FakeBackend()
    fn = CompiledFunction.from_path(_bundle(tmp_path), backend=backend)
    fn.close()
    assert backend.loaded is None


def test_compiled_function_context_manager(tmp_path):
    backend = _FakeBackend()
    with CompiledFunction.from_path(_bundle(tmp_path), backend=backend) as fn:
        assert fn("x") == "echo:x"
    assert backend.loaded is None
