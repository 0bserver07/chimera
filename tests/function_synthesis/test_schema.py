"""Tests for chimera.function_synthesis.schema — the tiny JSON-schema subset."""
from __future__ import annotations

import math

import pytest

from chimera.function_synthesis.schema import SchemaError, validate


# --- type checks ----------------------------------------------------------
class TestTypeChecks:
    def test_object_accepts_dict(self):
        validate({"a": 1}, {"type": "object"})

    def test_object_rejects_list(self):
        with pytest.raises(SchemaError, match="expected type"):
            validate([], {"type": "object"})

    def test_array_accepts_list(self):
        validate([1, 2, 3], {"type": "array"})

    def test_string_accepts_str(self):
        validate("hi", {"type": "string"})

    def test_string_rejects_int(self):
        with pytest.raises(SchemaError):
            validate(7, {"type": "string"})

    def test_integer_accepts_int(self):
        validate(42, {"type": "integer"})

    def test_integer_rejects_bool(self):
        """JSON 'integer' should NOT accept bool, even though bool is int in Python."""
        with pytest.raises(SchemaError):
            validate(True, {"type": "integer"})

    def test_number_accepts_int_and_float(self):
        validate(1, {"type": "number"})
        validate(1.5, {"type": "number"})

    def test_number_rejects_bool(self):
        with pytest.raises(SchemaError):
            validate(False, {"type": "number"})

    def test_boolean_accepts_bool(self):
        validate(True, {"type": "boolean"})
        validate(False, {"type": "boolean"})

    def test_null_accepts_none(self):
        validate(None, {"type": "null"})

    def test_null_rejects_zero(self):
        with pytest.raises(SchemaError):
            validate(0, {"type": "null"})

    def test_type_list_accepts_any_listed(self):
        schema = {"type": ["string", "null"]}
        validate("hi", schema)
        validate(None, schema)
        with pytest.raises(SchemaError):
            validate(3, schema)

    def test_unsupported_type_raises(self):
        with pytest.raises(SchemaError, match="unsupported type"):
            validate(1, {"type": "bigint"})


# --- required + properties -----------------------------------------------
class TestObjectConstraints:
    def test_required_missing(self):
        with pytest.raises(SchemaError, match="missing required key 'name'"):
            validate(
                {"age": 5},
                {"type": "object", "required": ["name"]},
            )

    def test_required_present(self):
        validate(
            {"name": "x"},
            {"type": "object", "required": ["name"]},
        )

    def test_properties_nested_validation(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        validate({"name": "ada", "age": 30}, schema)
        with pytest.raises(SchemaError, match=r"age.*expected type"):
            validate({"name": "ada", "age": "thirty"}, schema)

    def test_properties_path_in_error(self):
        schema = {
            "type": "object",
            "properties": {"user": {"type": "object", "properties": {
                "id": {"type": "integer"},
            }}},
        }
        with pytest.raises(SchemaError, match=r"user\.id"):
            validate({"user": {"id": "oops"}}, schema)

    def test_missing_optional_property_ok(self):
        """properties alone does not make a key required."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        validate({}, schema)


# --- array / items -------------------------------------------------------
class TestArrayConstraints:
    def test_items_applies_to_every_element(self):
        schema = {"type": "array", "items": {"type": "string"}}
        validate(["a", "b", "c"], schema)
        with pytest.raises(SchemaError, match=r"\[1\]"):
            validate(["a", 7, "c"], schema)

    def test_nested_items(self):
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            },
        }
        validate([{"id": 1}, {"id": 2}], schema)
        with pytest.raises(SchemaError, match=r"\[1\].*missing required"):
            validate([{"id": 1}, {}], schema)


# --- enum ----------------------------------------------------------------
class TestEnum:
    def test_enum_accepts(self):
        validate("red", {"type": "string", "enum": ["red", "green", "blue"]})

    def test_enum_rejects(self):
        with pytest.raises(SchemaError, match="not in enum"):
            validate("yellow", {"type": "string", "enum": ["red", "green", "blue"]})

    def test_enum_without_type(self):
        validate(3, {"enum": [1, 2, 3]})


# --- misc ----------------------------------------------------------------
class TestMisc:
    def test_empty_schema_accepts_anything(self):
        validate(object(), {})

    def test_non_dict_schema_raises(self):
        with pytest.raises(SchemaError, match="schema at"):
            validate("x", "not a dict")  # type: ignore[arg-type]

    def test_nan_still_passes_number(self):
        """We don't special-case NaN here; that's the caller's job."""
        validate(math.nan, {"type": "number"})
