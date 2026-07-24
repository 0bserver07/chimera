"""External-agent lanes in the multiplexer (issue #169): specs, panes, resume.

The external CLI here is ``tests/assembly/fake_external_agent.py`` (scripted
stream-json), so lane-spec parsing, pane rendering, telemetry folding, and the
cohort persist→resume round-trip are asserted without a real agent.
"""
import sys
from pathlib import Path

import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from chimera.assembly.external_driver import (  # noqa: E402
    ExternalAgentDriver,
    ExternalAgentProfile,
)
from chimera.core.loop_events import LoopEventType  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.multiplex import parse_lane_specs  # noqa: E402
from chimera.tui.routing import RoutingMode  # noqa: E402

FAKE = str(Path(__file__).parent.parent / "assembly" / "fake_external_agent.py")


def _scripted_profile(mode: str = "stream") -> ExternalAgentProfile:
    return ExternalAgentProfile(
        name="scripted",
        command=(sys.executable, FAKE, mode, "{task}"),
        protocol="stream-json",
        timeout=30.0,
    )


def _write_scripted_config(config_home: Path, mode: str = "stream") -> None:
    (config_home / "config.toml").write_text(
        "[external_agents.scripted]\n"
        f'command = [{sys.executable!r}, {FAKE!r}, {mode!r}, "{{task}}"]\n'
        'protocol = "stream-json"\n',
        encoding="utf-8",
    )


# -- lane-spec parsing --------------------------------------------------
def test_parse_lane_specs_external_beside_chimera():
    specs = parse_lane_specs("ext:claude,glm-5.2")
    ext, glm = specs
    assert ext["model"] == "ext:claude"
    assert ext["preset"] == "external"
    assert ext["external"] == "claude"
    assert ext["label"] == "ext:claude"
    assert ext["lane_id"] == "A"
    assert glm["model"] == "glm-5.2"
    assert glm["external"] == ""
    assert glm["lane_id"] == "B"


def test_parse_lane_specs_unknown_profile_is_loud():
    with pytest.raises(ValueError, match="unknown external agent profile"):
        parse_lane_specs("ext:not-a-profile")


def test_parse_lane_specs_external_takes_no_axes():
    with pytest.raises(ValueError, match="no preset/loop axes"):
        parse_lane_specs("ext:claude:minimal")
    with pytest.raises(ValueError, match="ext:<profile-name>"):
        parse_lane_specs("ext:")


def test_parse_lane_specs_user_profile_from_config(_isolated_chimera_config):
    _write_scripted_config(_isolated_chimera_config)
    specs = parse_lane_specs("ext:scripted")
    assert specs[0]["external"] == "scripted"
    assert specs[0]["model"] == "ext:scripted"


# -- Lane folding (telemetry + transcript, no app) ----------------------
@pytest.mark.asyncio
async def test_lane_folds_external_stream_into_telemetry(tmp_path):
    driver = ExternalAgentDriver(_scripted_profile(), workdir=str(tmp_path))
    lane = Lane(LaneConfig("A", "ext:scripted", "ext:scripted", "external"), driver, None)
    async for ev in driver.send("make it"):
        lane.record(ev)
    lane.on_turn_end(order=1)

    t = lane.telemetry
    assert t.cost == pytest.approx(0.0042)
    assert t.steps == 2 and t.turns == 1
    assert t.tokens_in == 17 and t.tokens_out == 22
    assert t.terminal_reason == "completed"
    assert t.context_tokens > 0  # per-step usage reached the context gauge
    assert lane.tool_log == [("Write", True)]
    text = lane.transcript_text()
    assert "Creating the file now." in text
    assert "external agent ready" in text


# -- pilot: an external lane races beside a Chimera-style lane ----------
class _ChimeraFakeDriver:
    """Minimal Chimera-lane stand-in (the test_multiplex FakeDriver shape)."""

    def __init__(self):
        self.tools: list = []
        self.history: list = []

    async def send(self, text):
        from chimera.core.loop_events import LoopEvent, LoopResult

        yield LoopEvent(LoopEventType.assistant_chunk, "chimera says hi", 0)
        yield LoopEvent(
            LoopEventType.result,
            LoopResult(
                reason="completed", messages=[], usage={"input_tokens": 9, "output_tokens": 3},
                cost_usd=0.001, duration_ms=5.0, turn_count=1,
            ),
            0,
        )

    def steer(self, text): ...
    def queue_follow_up(self, text): ...
    def cancel(self): ...
    def clear(self): ...
    def load_history(self, messages): ...


@pytest.mark.asyncio
async def test_external_lane_renders_in_multiplexer_pilot(tmp_path):
    from textual.widgets import RichLog

    from chimera.tui.multiplex import MultiplexApp
    from chimera.tui.prompt import PromptArea

    ext_dir = tmp_path / "ext-ws"
    ext_dir.mkdir()
    ext = Lane(
        LaneConfig("A", "ext:scripted", "ext:scripted", "external"),
        ExternalAgentDriver(_scripted_profile(), workdir=str(ext_dir)),
        None,
    )
    chim = Lane(
        LaneConfig("B", "glm-fake", "glm-fake"), _ChimeraFakeDriver(), None,
    )
    cohort = Cohort([ext, chim], task=None, routing=RoutingMode.BROADCAST)
    app = MultiplexApp(cohort)
    async with app.run_test() as pilot:
        app.query_one("#prompt", PromptArea).value = "make it"
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        # both lanes finished their race legs with their own telemetry
        assert ext.telemetry.cost == pytest.approx(0.0042)
        assert ext.telemetry.steps == 2
        assert ext.telemetry.terminal_reason == "completed"
        assert chim.telemetry.cost == pytest.approx(0.001)
        assert cohort.all_done
        # the external pane rendered content
        logs = list(app.query(RichLog))
        assert len(logs) == 2 and all(lg.lines for lg in logs)
        # the external CLI's write landed in its own workdir
        assert (ext_dir / "external.txt").exists()


# -- cohort persist → resume round-trip ---------------------------------
@pytest.mark.asyncio
async def test_external_lane_persists_and_resumes(tmp_path, _isolated_chimera_config):
    from chimera.tui import multiplex as mux
    from chimera.tui.workspace import provision_workspaces

    _write_scripted_config(_isolated_chimera_config)

    source = tmp_path / "proj"
    source.mkdir()
    (source / "README.md").write_text("seed\n", encoding="utf-8")
    persist_root = tmp_path / "cohorts"

    workspaces = provision_workspaces(str(source), ["A"], strategy="inplace")
    ws = workspaces[0]
    driver = ExternalAgentDriver(_scripted_profile(), workdir=str(ws.path))
    lane = Lane(
        LaneConfig("A", "ext:scripted", "ext:scripted", "external"), driver, ws,
    )
    cohort = Cohort(
        [lane], task="make it", source=str(source), isolation="inplace",
        routing=RoutingMode.TARGETED, workspaces=workspaces,
    )
    async for ev in driver.send("make it"):
        lane.record(ev)
    lane.on_turn_end(order=1)

    out = cohort.persist(root=persist_root)
    manifest_lane = cohort.manifest().to_dict()["lanes"][0]
    assert manifest_lane["model"] == "ext:scripted"
    assert manifest_lane["preset"] == "external"
    assert manifest_lane["telemetry"]["cost"] == pytest.approx(0.0042)
    assert (out / "lane-A.history.json").exists()
    assert "external.txt" in (out / "lane-A.diff").read_text()

    resumed, resumed_ws = mux._load_saved_cohort(
        cohort.cohort_id, persist_root=str(persist_root),
    )
    try:
        rlane = resumed.lanes[0]
        assert isinstance(rlane.driver, ExternalAgentDriver)
        assert rlane.driver.model == "ext:scripted"
        assert rlane.config.model == "ext:scripted"
        assert rlane.config.preset == "external"
        # saved minimal history seeded the rebuilt driver
        roles = [getattr(m, "role", "?") for m in rlane.driver.history]
        assert roles == ["user", "assistant"]
        # telemetry restored so the scoreboard keeps accumulating
        assert rlane.telemetry.cost == pytest.approx(0.0042)
        assert rlane.telemetry.steps == 2
        # a resumed external lane can run another turn
        events = [ev async for ev in rlane.driver.send("again")]
        assert events[-1].type == LoopEventType.result
        assert events[-1].data.reason == "completed"
    finally:
        resumed_ws.cleanup_all()
        workspaces.cleanup_all()
