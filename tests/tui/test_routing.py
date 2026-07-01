"""Exhaustive unit tests for the pure input router (spec §5.4 + §6.4)."""
from chimera.tui.routing import Action, LaneAction, RoutingMode, classify, route


# -- classify (single agent, §5.4) --------------------------------------
def test_classify_empty_is_noop():
    assert classify("", False) is Action.NOOP
    assert classify("   ", True) is Action.NOOP


def test_classify_slash_is_local_command_regardless_of_liveness():
    assert classify("/help", False) is Action.LOCAL_COMMAND
    assert classify("/model", True) is Action.LOCAL_COMMAND
    assert classify("  /cost", True) is Action.LOCAL_COMMAND  # leading space tolerated


def test_classify_idle_starts_new_turn():
    assert classify("do the thing", False) is Action.NEW_TURN


def test_classify_running_steers():
    assert classify("actually use pytest", True) is Action.STEER


def test_classify_follow_up_when_running():
    assert classify("then commit", True, follow_up=True) is Action.FOLLOW_UP
    # follow_up on an idle agent is still a new turn (nothing to queue behind)
    assert classify("then commit", False, follow_up=True) is Action.NEW_TURN


# -- route (multiplex, §6.4) --------------------------------------------
def test_route_empty_and_command():
    assert route("", RoutingMode.BROADCAST, [("A", False)]) == []
    assert route("/cost", RoutingMode.BROADCAST, [("A", False), ("B", True)]) == [
        LaneAction("*", Action.LOCAL_COMMAND)
    ]


def test_route_broadcast_addresses_every_lane_by_its_own_liveness():
    actions = route("go", RoutingMode.BROADCAST, [("A", False), ("B", True), ("C", False)])
    assert actions == [
        LaneAction("A", Action.NEW_TURN),
        LaneAction("B", Action.STEER),
        LaneAction("C", Action.NEW_TURN),
    ]


def test_route_targeted_addresses_only_focus():
    lanes = [("A", False), ("B", False), ("C", True)]
    assert route("go", RoutingMode.TARGETED, lanes, focus_id="C") == [
        LaneAction("C", Action.STEER)
    ]
    assert route("go", RoutingMode.TARGETED, lanes, focus_id="A") == [
        LaneAction("A", Action.NEW_TURN)
    ]


def test_route_targeted_unknown_focus_is_empty():
    assert route("go", RoutingMode.TARGETED, [("A", False)], focus_id="Z") == []


def test_route_broadcast_follow_up_variant():
    actions = route("later", RoutingMode.BROADCAST, [("A", True), ("B", False)], follow_up=True)
    assert actions == [
        LaneAction("A", Action.FOLLOW_UP),
        LaneAction("B", Action.NEW_TURN),
    ]
