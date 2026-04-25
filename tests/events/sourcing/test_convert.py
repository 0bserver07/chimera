"""Tests for the convert_event migration helper."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from chimera.events.sourcing import (
    EventDefinition,
    EventRegistry,
    UnknownEventTypeError,
    convert_event,
)
from chimera.events.sourcing.convert import ConvertError


@dataclass
class _V1:
    name: str = ""


@dataclass
class _V2:
    full_name: str = ""


@dataclass
class _V3:
    full_name: str = ""
    title: str = ""


def _make_registry() -> EventRegistry:
    reg = EventRegistry()
    reg.register(EventDefinition(name="user", version=1, payload_cls=_V1))
    reg.register(
        EventDefinition(
            name="user", version=2, payload_cls=_V2,
            upgrade_from={1: lambda d: {"full_name": d.get("name", "")}},
        ),
    )
    reg.register(
        EventDefinition(
            name="user", version=3, payload_cls=_V3,
            upgrade_from={
                2: lambda d: {**d, "title": ""},
            },
        ),
    )
    return reg


def test_convert_at_latest_returns_unchanged() -> None:
    reg = _make_registry()
    wire, payload = convert_event("user.3", {"full_name": "x", "title": "Mx"}, reg)
    assert wire == "user.3"
    assert payload == {"full_name": "x", "title": "Mx"}


def test_convert_walks_chain() -> None:
    reg = _make_registry()
    wire, payload = convert_event("user.1", {"name": "Alice"}, reg)
    assert wire == "user.3"
    assert payload == {"full_name": "Alice", "title": ""}


def test_convert_missing_chain_raises() -> None:
    reg = EventRegistry()
    reg.register(EventDefinition(name="x", version=1, payload_cls=_V1))
    reg.register(EventDefinition(name="x", version=3, payload_cls=_V3))  # gap at 2
    with pytest.raises(ConvertError):
        convert_event("x.1", {"name": "y"}, reg)


def test_convert_missing_upgrade_fn_raises() -> None:
    reg = EventRegistry()
    reg.register(EventDefinition(name="x", version=1, payload_cls=_V1))
    reg.register(EventDefinition(name="x", version=2, payload_cls=_V2))
    with pytest.raises(ConvertError):
        convert_event("x.1", {"name": "y"}, reg)


def test_convert_unknown_type_raises() -> None:
    reg = _make_registry()
    with pytest.raises(UnknownEventTypeError):
        convert_event("unknown.1", {}, reg)


def test_convert_newer_than_latest_raises() -> None:
    reg = _make_registry()
    with pytest.raises(ConvertError):
        convert_event("user.99", {}, reg)
