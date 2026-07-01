"""Pure input routing for the Chimera TUIs (spec §5.4 + §6.4).

The meaning of a submission depends on liveness and (for the multiplexer) on the
routing mode. These functions are deliberately pure — no I/O, no widgets — so the
routing table is exhaustively unit-testable and the two frontends share one
definition of "what does pressing enter do".
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

__all__ = ["Action", "RoutingMode", "LaneAction", "classify", "route", "COMMAND_PREFIX"]

COMMAND_PREFIX = "/"


class Action(Enum):
    """What a submission resolves to for a single agent."""

    NOOP = "noop"
    LOCAL_COMMAND = "local_command"
    STEER = "steer"
    NEW_TURN = "new_turn"
    FOLLOW_UP = "follow_up"


class RoutingMode(Enum):
    """Where a multiplexer submission goes."""

    BROADCAST = "broadcast"  # every lane (race the same task)
    TARGETED = "targeted"    # only the focused lane


def classify(text: str, running: bool, *, follow_up: bool = False) -> Action:
    """Classify one submission for a single agent (§5.4).

    Args:
        text: The raw submission.
        running: Whether a turn is currently in flight.
        follow_up: True when the submit gesture requested queue-for-after.

    Returns:
        The resolved :class:`Action`. A running agent steers (or queues a
        follow-up); an idle agent starts a new turn; a ``/`` prefix is a local
        command; empty input is a no-op.
    """
    if not text.strip():
        return Action.NOOP
    if text.lstrip().startswith(COMMAND_PREFIX):
        return Action.LOCAL_COMMAND
    if running:
        return Action.FOLLOW_UP if follow_up else Action.STEER
    return Action.NEW_TURN


@dataclass(frozen=True)
class LaneAction:
    """A resolved action addressed to one lane (or ``"*"`` for the frontend)."""

    lane_id: str
    action: Action


def route(
    text: str,
    mode: RoutingMode,
    lanes: Iterable[tuple[str, bool]],
    focus_id: str | None = None,
    *,
    follow_up: bool = False,
) -> list[LaneAction]:
    """Map a multiplexer submission to per-lane actions (§6.4).

    Extends :func:`classify` across lanes: a local command short-circuits to a
    single frontend-handled action; otherwise broadcast addresses every lane and
    targeted addresses only ``focus_id``. Each addressed lane is classified by
    its own liveness, so a broadcast into a cohort where some lanes are mid-turn
    steers those and starts the idle ones.

    Args:
        text: The raw submission.
        mode: Broadcast or targeted.
        lanes: ``(lane_id, running)`` pairs for every lane in the cohort.
        focus_id: The focused lane, used in targeted mode.
        follow_up: True for the queue-for-after submit gesture.

    Returns:
        One :class:`LaneAction` per addressed lane. ``[]`` for empty input; a
        single ``LaneAction("*", LOCAL_COMMAND)`` for a slash command.
    """
    if not text.strip():
        return []
    if text.lstrip().startswith(COMMAND_PREFIX):
        return [LaneAction("*", Action.LOCAL_COMMAND)]
    addressed = list(lanes)
    if mode is RoutingMode.TARGETED:
        addressed = [(lid, running) for (lid, running) in addressed if lid == focus_id]
    return [
        LaneAction(lid, classify(text, running, follow_up=follow_up))
        for (lid, running) in addressed
    ]
