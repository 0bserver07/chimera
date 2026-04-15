from __future__ import annotations

import json

from chimera.function_synthesis.spec import FunctionSpec


def test_spec_requires_name_and_description():
    spec = FunctionSpec(name="classify", description="Classify sentiment as pos/neg.")
    assert spec.name == "classify"
    assert spec.description == "Classify sentiment as pos/neg."
    assert spec.examples == []


def test_spec_with_examples_round_trips_json():
    spec = FunctionSpec(
        name="extract_email",
        description="Extract the first email from text.",
        examples=[{"input": "ping a@b.com", "output": "a@b.com"}],
    )
    blob = spec.to_json()
    restored = FunctionSpec.from_json(blob)
    assert restored == spec
    assert json.loads(blob)["name"] == "extract_email"


def test_spec_rejects_empty_name():
    import pytest

    with pytest.raises(ValueError, match="name must be non-empty"):
        FunctionSpec(name="", description="x")
