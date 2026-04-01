"""Tests for UUIDChain."""

from chimera.core.uuid_chain import UUIDChain
from chimera.types import Message


def test_first_message_has_no_parent():
    """The first non-progress message should return None as parent uuid."""
    chain = UUIDChain()
    msg = Message.user("hello")
    parent = chain.next(msg)
    assert parent is None


def test_second_message_has_parent():
    """The second message's parent should be the uuid assigned after the first."""
    chain = UUIDChain()

    msg1 = Message.user("hello")
    parent1 = chain.next(msg1)
    assert parent1 is None
    # After processing msg1, _last_uuid should be set

    msg2 = Message.assistant("world")
    parent2 = chain.next(msg2)
    assert parent2 is not None
    # parent2 should be the uuid generated for msg1
    assert isinstance(parent2, str)
    assert len(parent2) > 0
