"""Structured JSON output with schema validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ValidationError(Exception):
    """Raised when JSON output fails schema validation."""


@dataclass
class StructuredOutput:
    """JSON schema wrapper for structured LLM output.

    Validates that the model's response is valid JSON matching the
    provided schema. Basic validation only (type checks, required fields).

    Args:
        name: Schema name for error messages.
        schema: JSON Schema dict describing expected output.
        max_retries: Number of retry attempts on validation failure.
    """

    name: str
    schema: dict[str, Any]
    max_retries: int = 3

    def validate(self, text: str) -> dict[str, Any]:
        """Parse and validate JSON text against the schema.

        Args:
            text: Raw text from the model.

        Returns:
            Parsed JSON object.

        Raises:
            ValidationError: If text is not valid JSON or fails schema checks.
        """
        # Extract JSON from markdown code blocks if present
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.strip() == "```" and in_block:
                    break
                if in_block:
                    json_lines.append(line)
            stripped = "\n".join(json_lines)

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON for {self.name}: {e}") from e

        # Basic schema validation: check required fields
        if not isinstance(data, dict):
            raise ValidationError(f"{self.name}: expected object, got {type(data).__name__}")

        required = self.schema.get("required", [])
        properties = self.schema.get("properties", {})
        for field_name in required:
            if field_name not in data:
                raise ValidationError(f"{self.name}: missing required field '{field_name}'")

        # Type checks for present fields
        for field_name, value in data.items():
            if field_name in properties:
                expected_type = properties[field_name].get("type")
                if expected_type and not self._check_type(value, expected_type):
                    raise ValidationError(
                        f"{self.name}: field '{field_name}' expected {expected_type}, "
                        f"got {type(value).__name__}"
                    )

        return data

    @staticmethod
    def _check_type(value: Any, expected: str) -> bool:
        """Check if a value matches a JSON Schema type."""
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        py_type = type_map.get(expected)
        if py_type is None:
            return True  # unknown type, skip check
        return isinstance(value, py_type)

    def format_error(self, error: ValidationError) -> str:
        """Format a validation error for LLM retry context.

        Args:
            error: The validation error.

        Returns:
            Human-readable error string suitable for LLM context.
        """
        return (
            f"Your response did not match the expected schema '{self.name}'. "
            f"Error: {error}. Please try again with valid JSON matching: "
            f"{json.dumps(self.schema, indent=2)}"
        )
