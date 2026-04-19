from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.schema import SchemaError
from chimera.function_synthesis.spec import FunctionSpec


class _FakeBackend(RuntimeBackend):
    def __init__(self, response: str | None = None) -> None:
        self.loaded: ChiBundle | None = None
        self.calls: list[str] = []
        self._response = response

    def load(self, bundle: ChiBundle) -> None:
        self.loaded = bundle

    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        self.calls.append(user_input)
        if self._response is not None:
            return self._response
        return f"echo:{user_input}"

    def close(self) -> None:
        self.loaded = None


def _bundle(
    tmp_path: Path,
    *,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> Path:
    spec = FunctionSpec(
        name="echo",
        description="echoes input",
        input_schema=input_schema,
        output_schema=output_schema,
    )
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


# --- validation (opt-in) --------------------------------------------------


def test_validation_off_by_default_accepts_anything(tmp_path):
    """Without validate=True, schemas on the spec are ignored at runtime."""
    backend = _FakeBackend()
    path = _bundle(
        tmp_path,
        input_schema={"type": "object", "required": ["name"]},
    )
    fn = CompiledFunction.from_path(path, backend=backend)
    # The input doesn't match the schema, but validation is disabled.
    assert fn("just a string") == "echo:just a string"


def test_validate_input_object_schema_rejects_bad_json(tmp_path):
    backend = _FakeBackend()
    path = _bundle(
        tmp_path,
        input_schema={"type": "object", "required": ["name"]},
    )
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    with pytest.raises(SchemaError, match="input.*missing required"):
        fn('{"age": 30}')
    # Backend was NOT called because validation failed first.
    assert backend.calls == []


def test_validate_input_object_schema_accepts(tmp_path):
    backend = _FakeBackend(response='{"ok": true}')
    path = _bundle(
        tmp_path,
        input_schema={"type": "object", "required": ["name"]},
        output_schema={"type": "object", "required": ["ok"]},
    )
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    out = fn('{"name": "ada"}')
    assert out == '{"ok": true}'
    assert backend.calls == ['{"name": "ada"}']


def test_validate_input_bad_json_is_schema_error(tmp_path):
    backend = _FakeBackend()
    path = _bundle(tmp_path, input_schema={"type": "object"})
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    with pytest.raises(SchemaError, match="decode failed"):
        fn("not json at all")


def test_validate_input_string_schema_passes_raw(tmp_path):
    """type: string should NOT json-decode — pass through untouched."""
    backend = _FakeBackend()
    path = _bundle(tmp_path, input_schema={"type": "string"})
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    # Raw string input is fine, even though it isn't JSON.
    assert fn("hello world") == "echo:hello world"


def test_validate_output_rejects_bad_output(tmp_path):
    backend = _FakeBackend(response='{"wrong": true}')
    path = _bundle(
        tmp_path,
        output_schema={"type": "object", "required": ["ok"]},
    )
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    with pytest.raises(SchemaError, match="output.*missing required"):
        fn("anything")


def test_validate_output_rejects_non_json(tmp_path):
    backend = _FakeBackend(response="not json")
    path = _bundle(tmp_path, output_schema={"type": "object"})
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    with pytest.raises(SchemaError, match="output.*decode failed"):
        fn("anything")


def test_validate_output_string_schema_accepts_any_string(tmp_path):
    backend = _FakeBackend(response="some free-form response")
    path = _bundle(tmp_path, output_schema={"type": "string"})
    fn = CompiledFunction.from_path(path, backend=backend, validate=True)
    assert fn("in") == "some free-form response"


def test_validate_flag_via_constructor(tmp_path):
    """validate= also works on the direct constructor, not just from_path."""
    from chimera.function_synthesis.bundle import ChiBundle

    bundle = ChiBundle.load(
        _bundle(tmp_path, input_schema={"type": "object"})
    )
    backend = _FakeBackend()
    fn = CompiledFunction(bundle, backend, validate=True)
    with pytest.raises(SchemaError):
        fn("garbage")


def test_spec_validate_helpers(tmp_path):
    """FunctionSpec.validate_input / validate_output mirror the schema module."""
    from chimera.function_synthesis.spec import FunctionSpec

    spec = FunctionSpec(
        name="n",
        description="d",
        input_schema={"type": "object", "required": ["x"]},
        output_schema={"type": "array", "items": {"type": "integer"}},
    )
    spec.validate_input({"x": 1})
    with pytest.raises(SchemaError):
        spec.validate_input({"y": 1})

    spec.validate_output([1, 2, 3])
    with pytest.raises(SchemaError):
        spec.validate_output([1, "two"])

    # With no schema, these are no-ops.
    empty = FunctionSpec(name="n", description="d")
    empty.validate_input({"anything": 1})
    empty.validate_output("anything")
