"""FunctionSpec: the 'what to compile' description for function synthesis."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FunctionSpec:
    """Specification for a neural function to be synthesized.

    A FunctionSpec is consumed by a :class:`CompilerBackend` to produce a
    ``.chi`` bundle that can be loaded as a :class:`CompiledFunction`.

    Attributes:
        name: Short identifier (used in bundle filenames).
        description: Natural-language description of what the function does.
        examples: Optional input/output examples to ground compilation.
        input_schema: Optional JSON-schema-like dict describing input shape.
        output_schema: Optional JSON-schema-like dict describing output shape.
    """

    name: str
    description: str
    examples: list[dict[str, str]] = field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self.description:
            raise ValueError("description must be non-empty")

    def to_json(self) -> str:
        """Serialize to a JSON string for inclusion in ``.chi`` bundles."""
        return json.dumps(
            {
                "name": self.name,
                "description": self.description,
                "examples": self.examples,
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, blob: str) -> FunctionSpec:
        """Deserialize from a JSON string produced by :meth:`to_json`."""
        data = json.loads(blob)
        return cls(
            name=data["name"],
            description=data["description"],
            examples=data.get("examples", []),
            input_schema=data.get("input_schema"),
            output_schema=data.get("output_schema"),
        )
