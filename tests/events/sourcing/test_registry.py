"""Tests for the EventRegistry / EventDefinition surface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from chimera.events.sourcing import (
    DEFAULT_REGISTRY,
    EventDefinition,
    EventRegistry,
    UnknownEventTypeError,
)
from chimera.events.sourcing.types import ToolCalledEvent


def test_default_registry_has_all_12_spec_events() -> None:
    expected = {
        "session.created",
        "session.ended",
        "tool.called",
        "tool.completed",
        "file.mutated",
        "permission.decided",
        "model.requested",
        "model.responded",
        "compaction.performed",
        "error.occurred",
        "user.message",
        "agent.result",
    }
    assert set(DEFAULT_REGISTRY.names()) == expected


def test_definition_wire_id_format() -> None:
    d = DEFAULT_REGISTRY.get("tool.called", 1)
    assert d.wire_id == "tool.called.1"


def test_register_duplicate_raises() -> None:
    reg = EventRegistry()
    reg.register(EventDefinition(name="x", version=1, payload_cls=ToolCalledEvent))
    with pytest.raises(ValueError):
        reg.register(EventDefinition(name="x", version=1, payload_cls=ToolCalledEvent))


def test_unknown_lookup_raises() -> None:
    with pytest.raises(UnknownEventTypeError):
        DEFAULT_REGISTRY.get("nope.nope", 1)
    with pytest.raises(UnknownEventTypeError):
        DEFAULT_REGISTRY.get_by_wire("nope.nope.1")


def test_latest_version_tracks_max() -> None:
    reg = EventRegistry()

    @dataclass
    class P1:
        a: int = 0

    @dataclass
    class P2:
        a: int = 0
        b: int = 0

    reg.register(EventDefinition(name="x.evt", version=1, payload_cls=P1))
    reg.register(EventDefinition(name="x.evt", version=2, payload_cls=P2))
    assert reg.latest_version("x.evt") == 2
    assert reg.all_versions("x.evt") == [1, 2]


def test_default_to_dict_handles_dataclass() -> None:
    d = DEFAULT_REGISTRY.get("tool.called", 1)
    payload = ToolCalledEvent(
        session_id="s1", call_id="c1", tool_name="bash", arguments={"cmd": "ls"},
    )
    out = d.to_dict(payload)
    assert out["session_id"] == "s1"
    assert out["arguments"] == {"cmd": "ls"}


def test_default_from_dict_ignores_extras() -> None:
    d = DEFAULT_REGISTRY.get("tool.called", 1)
    instance = d.from_dict(
        d.payload_cls,
        {"session_id": "s1", "call_id": "c1", "tool_name": "bash",
         "arguments": {}, "future_field": "ignored"},
    )
    assert isinstance(instance, ToolCalledEvent)
    assert instance.session_id == "s1"


def test_find_definition_for_payload() -> None:
    payload = ToolCalledEvent(session_id="s1")
    definition = DEFAULT_REGISTRY.find_definition_for(payload)
    assert definition.name == "tool.called"
    assert definition.version == 1


def test_find_definition_for_unknown_raises() -> None:
    @dataclass
    class _Other:
        x: int = 0

    with pytest.raises(UnknownEventTypeError):
        DEFAULT_REGISTRY.find_definition_for(_Other())


def test_payload_cls_can_be_arbitrary_dataclass() -> None:
    reg = EventRegistry()

    @dataclass
    class Custom:
        x: int = 0
        meta: dict[str, Any] = field(default_factory=dict)

    reg.register(EventDefinition(name="custom.evt", version=1, payload_cls=Custom))
    inst = Custom(x=42, meta={"k": "v"})
    out = reg.find_definition_for(inst).to_dict(inst)
    assert out == {"x": 42, "meta": {"k": "v"}}
