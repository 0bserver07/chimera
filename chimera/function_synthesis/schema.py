"""Tiny JSON-schema-subset validator for function-synthesis specs.

We deliberately implement only the slice of JSON Schema that is useful
for describing the input and output shapes of a :class:`CompiledFunction`:

- ``type``: one of ``"object"``, ``"array"``, ``"string"``,
  ``"integer"``, ``"number"``, ``"boolean"``, ``"null"``. May also be
  a list of allowed types (``{"type": ["string", "null"]}``).
- ``required``: list of required keys on an object.
- ``properties``: per-key sub-schemas for an object.
- ``items``: sub-schema applied to every element of an array.
- ``enum``: list of allowed values (applied in addition to ``type``).

This covers the vast majority of realistic function specs without
dragging in a full JSON Schema engine. When a constraint you need is
missing, write a custom validator — don't pretend this one is
spec-complete.
"""
from __future__ import annotations

from typing import Any


class SchemaError(ValueError):
    """Raised when a value does not conform to the provided schema.

    The error message includes the JSON-pointer-like path to the
    offending field (e.g. ``"output.items[0].name"``) so failures are
    debuggable without turning on verbose logging.
    """


_TYPE_MAP: dict[str, tuple[type, ...] | tuple[type, type]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    # In JSON, numbers include ints. We mirror that.
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def validate(data: Any, schema: dict[str, Any], *, path: str = "") -> None:
    """Validate ``data`` against ``schema``.

    Args:
        data: The candidate Python value (typically the result of
            ``json.loads`` on a model output).
        schema: A dict using the JSON-schema subset documented in the
            module docstring.
        path: Internal accumulator used to build the error location.
            Callers should leave this at its default.

    Raises:
        SchemaError: If ``data`` violates ``schema`` in any way.
    """
    if not isinstance(schema, dict):
        raise SchemaError(
            f"schema at {path or '<root>'} must be a dict, got {type(schema).__name__}"
        )

    # --- type check -----------------------------------------------------
    if "type" in schema:
        expected = schema["type"]
        expected_list = expected if isinstance(expected, list) else [expected]
        if not _matches_any_type(data, expected_list):
            raise SchemaError(
                f"{path or '<root>'}: expected type {expected!r}, got "
                f"{type(data).__name__} ({data!r})"
            )

    # --- enum -----------------------------------------------------------
    if "enum" in schema:
        allowed = schema["enum"]
        if data not in allowed:
            raise SchemaError(
                f"{path or '<root>'}: value {data!r} not in enum {allowed!r}"
            )

    # --- object constraints --------------------------------------------
    if isinstance(data, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in data:
                raise SchemaError(
                    f"{path or '<root>'}: missing required key {key!r}"
                )
        properties = schema.get("properties") or {}
        for key, sub_schema in properties.items():
            if key in data:
                child_path = f"{path}.{key}" if path else key
                validate(data[key], sub_schema, path=child_path)

    # --- array constraints ---------------------------------------------
    if isinstance(data, list) and "items" in schema:
        item_schema = schema["items"]
        for i, element in enumerate(data):
            child_path = f"{path}[{i}]" if path else f"[{i}]"
            validate(element, item_schema, path=child_path)


def _matches_any_type(data: Any, types: list[str]) -> bool:
    """Return True if ``data`` matches at least one JSON-schema type name."""
    for t in types:
        py_types = _TYPE_MAP.get(t)
        if py_types is None:
            raise SchemaError(f"unsupported type in schema: {t!r}")
        # JSON's "integer" must exclude bool even though bool is an int
        # in Python. Same for "number". Without this, `True` passes
        # `type: integer` which is almost never what specs mean.
        if t in ("integer", "number") and isinstance(data, bool):
            continue
        # JSON's "boolean" conversely does accept bool.
        if isinstance(data, py_types):
            return True
    return False
