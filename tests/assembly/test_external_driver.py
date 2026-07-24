"""Unit tests for ExternalAgentDriver (external-agent lanes, issue #169).

Everything runs against ``tests/assembly/fake_external_agent.py`` — a scripted
CLI emitting known stream-json / text output — so the event mapping, telemetry
parse, exit-code handling, cancellation, and env-allowlist behavior are
asserted exactly, with no real agent involved. (The live-race validation
against a real CLI is a release gate, not a unit test.)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from chimera.assembly.external_driver import (
    BUILTIN_PROFILES,
    ExternalAgentDriver,
    ExternalAgentProfile,
    load_external_profiles,
    resolve_external_profile,
)
from chimera.core.loop_events import LoopEventType
from chimera.types import Message, ToolCall, ToolResult

FAKE = str(Path(__file__).parent / "fake_external_agent.py")


def _profile(mode: str, *, protocol: str = "stream-json", timeout: float = 30.0,
             env_allow: tuple[str, ...] | None = None) -> ExternalAgentProfile:
    return ExternalAgentProfile(
        name="scripted",
        command=(sys.executable, FAKE, mode, "{task}"),
        protocol=protocol,
        timeout=timeout,
        env_allow=env_allow,
    )


async def _run(driver: ExternalAgentDriver, text: str = "make it"):
    return [ev async for ev in driver.send(text)]


# -- profiles -----------------------------------------------------------
def test_builtin_claude_profile_is_stream_json():
    prof = BUILTIN_PROFILES["claude"]
    assert prof.protocol == "stream-json"
    assert any("{task}" in part for part in prof.command)
    assert prof.command[0] == "claude"


def test_from_config_validates_command_and_protocol():
    with pytest.raises(ValueError, match="non-empty"):
        ExternalAgentProfile.from_config("x", {})
    with pytest.raises(ValueError, match="task"):
        ExternalAgentProfile.from_config("x", {"command": ["run"]})
    with pytest.raises(ValueError, match="protocol"):
        ExternalAgentProfile.from_config(
            "x", {"command": ["run", "{task}"], "protocol": "carrier-pigeon"},
        )


def test_from_config_full_table():
    prof = ExternalAgentProfile.from_config("mine", {
        "command": ["mine", "--do", "{task}", "--cwd", "{workdir}"],
        "protocol": "text",
        "env": ["MY_KEY"],
        "timeout": 120,
    })
    assert prof.protocol == "text"
    assert prof.env_allow == ("MY_KEY",)
    assert prof.timeout == 120.0


def test_user_config_profiles_merge_over_builtins(tmp_path, monkeypatch):
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        '[external_agents.scripted]\n'
        'command = ["fake", "{task}"]\n'
        'protocol = "text"\n'
        '[external_agents.broken]\n'
        'command = "not-a-list"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(config_home))
    profiles = load_external_profiles()
    assert "claude" in profiles  # built-in survives
    assert profiles["scripted"].protocol == "text"
    assert "broken" not in profiles  # malformed: skipped on discovery …
    with pytest.raises(ValueError, match="non-empty"):
        resolve_external_profile("broken")  # … loud on resolve
    with pytest.raises(ValueError, match="unknown external agent profile"):
        resolve_external_profile("nope")


# -- stream-json mapping ------------------------------------------------
@pytest.mark.asyncio
async def test_stream_json_event_mapping(tmp_path):
    driver = ExternalAgentDriver(_profile("stream"), workdir=str(tmp_path))
    events = await _run(driver)
    kinds = [ev.type for ev in events]

    # init note surfaces; hook/thinking_tokens noise and rate_limit_event drop.
    assert kinds[0] == LoopEventType.system
    assert "external agent ready" in str(events[0].data)
    # thinking block → thinking_chunk
    assert LoopEventType.thinking_chunk in kinds
    # text blocks stream as chunks and commit via per-step assistant events
    chunk_text = "".join(
        str(ev.data) for ev in events if ev.type == LoopEventType.assistant_chunk
    )
    assert chunk_text == "Creating the file now.Done — external.txt written."
    # tool_use maps to a real ToolCall
    tool_use = next(ev for ev in events if ev.type == LoopEventType.tool_use)
    assert isinstance(tool_use.data, ToolCall)
    assert tool_use.data.name == "Write"
    assert tool_use.data.arguments == {"file_path": "external.txt"}
    # tool_result pairs with the matching call
    call, result = next(
        ev.data for ev in events if ev.type == LoopEventType.tool_result
    )
    assert call is tool_use.data
    assert isinstance(result, ToolResult) and result.success
    assert result.output == "File created successfully"
    # exactly one terminal result, last
    assert kinds[-1] == LoopEventType.result
    assert kinds.count(LoopEventType.result) == 1


@pytest.mark.asyncio
async def test_stream_json_telemetry_and_state(tmp_path):
    driver = ExternalAgentDriver(_profile("stream"), workdir=str(tmp_path))
    events = await _run(driver, "write the file")
    result = events[-1].data
    assert result.reason == "completed"
    assert result.cost_usd == pytest.approx(0.0042)
    assert result.turn_count == 2
    assert result.usage == {"input_tokens": 17, "output_tokens": 22}
    # per-step assistant events carry usage (feeds the context gauge)
    step = next(ev for ev in events if ev.type == LoopEventType.assistant)
    assert step.data.usage.get("input_tokens") == 7
    # driver state accrues
    assert driver.total_cost == pytest.approx(0.0042)
    assert driver.turn_count == 1
    assert driver.model == "ext:scripted"
    assert driver.tools == []
    assert driver.context_window is None
    assert driver.auto_compaction is False
    # the CLI's file write landed in the lane workdir
    assert (tmp_path / "external.txt").read_text() == "done: write the file\n"
    # minimal history reconstruction
    roles = [m.role for m in driver.history]
    assert roles == ["user", "assistant"]
    assert driver.history[-1].content == "Done — external.txt written."


@pytest.mark.asyncio
async def test_stream_json_error_result(tmp_path):
    driver = ExternalAgentDriver(_profile("stream-error"), workdir=str(tmp_path))
    events = await _run(driver)
    result = events[-1].data
    # the CLI's own result line wins over the exit code
    assert result.reason == "error_during_execution"
    assert result.cost_usd == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_non_json_lines_surface_as_system_notes(tmp_path):
    driver = ExternalAgentDriver(_profile("badjson"), workdir=str(tmp_path))
    events = await _run(driver)
    notes = [str(ev.data) for ev in events if ev.type == LoopEventType.system]
    assert any("fake banner" in n for n in notes)
    assert events[-1].data.reason == "completed"


# -- text protocol ------------------------------------------------------
@pytest.mark.asyncio
async def test_text_protocol_streams_lines_and_honest_zeros(tmp_path):
    driver = ExternalAgentDriver(
        _profile("text", protocol="text"), workdir=str(tmp_path),
    )
    events = await _run(driver, "plain run")
    # the honesty note comes first
    assert events[0].type == LoopEventType.system
    assert "telemetry unavailable" in str(events[0].data)
    chunk_text = "".join(
        str(ev.data) for ev in events if ev.type == LoopEventType.assistant_chunk
    )
    assert chunk_text == "step one\nstep two\n"
    # the closing assistant event commits the streamed text
    closing = [ev for ev in events if ev.type == LoopEventType.assistant]
    assert closing and closing[-1].data.content == "step one\nstep two\n"
    result = events[-1].data
    assert result.reason == "completed"
    assert result.cost_usd == 0.0 and result.usage == {} and result.turn_count == 0
    assert result.duration_ms > 0  # wall-clock stays real
    assert (tmp_path / "external.txt").exists()


# -- exit codes / stderr ------------------------------------------------
@pytest.mark.asyncio
async def test_nonzero_exit_maps_to_error_with_stderr_tail(tmp_path):
    driver = ExternalAgentDriver(_profile("fail"), workdir=str(tmp_path))
    events = await _run(driver)
    errors = [str(ev.data) for ev in events if ev.type == LoopEventType.error]
    assert any("exited with code 3" in e for e in errors)
    assert any("credentials missing" in e for e in errors)
    assert events[-1].data.reason == "error"
    assert driver.total_cost == 0.0


@pytest.mark.asyncio
async def test_unlaunchable_command_is_an_error_result(tmp_path):
    prof = ExternalAgentProfile(
        name="ghost", command=("/nonexistent/agent-binary", "{task}"),
    )
    driver = ExternalAgentDriver(prof, workdir=str(tmp_path))
    events = await _run(driver)
    assert events[-1].type == LoopEventType.result
    assert events[-1].data.reason == "error"
    assert any(
        "cannot launch" in str(ev.data)
        for ev in events if ev.type == LoopEventType.error
    )


# -- cancellation & timeout ---------------------------------------------
@pytest.mark.asyncio
async def test_cancel_terminates_subprocess_and_reports_cancelled(tmp_path):
    driver = ExternalAgentDriver(_profile("hang"), workdir=str(tmp_path))
    events = []
    started = time.monotonic()
    async for ev in driver.send("hang please"):
        events.append(ev)
        if ev.type == LoopEventType.system and "ready" in str(ev.data):
            driver.cancel()
    elapsed = time.monotonic() - started
    assert events[-1].type == LoopEventType.result
    assert events[-1].data.reason == "cancelled"
    assert elapsed < 30  # nowhere near the 60s sleep — SIGTERM landed


@pytest.mark.asyncio
async def test_timeout_reports_timeout_reason(tmp_path):
    driver = ExternalAgentDriver(
        _profile("hang", timeout=1.0), workdir=str(tmp_path),
    )
    events = await _run(driver)
    assert events[-1].data.reason == "timeout"
    assert any(
        "timed out" in str(ev.data)
        for ev in events if ev.type == LoopEventType.error
    )


# -- steering / follow-up degradation -----------------------------------
@pytest.mark.asyncio
async def test_steer_and_follow_up_note_honestly_when_idle(tmp_path):
    driver = ExternalAgentDriver(_profile("stream"), workdir=str(tmp_path))
    driver.steer("go faster")
    driver.queue_follow_up("and then this")
    events = await _run(driver)
    notes = [str(ev.data) for ev in events if ev.type == LoopEventType.system]
    assert any("steering is not supported" in n for n in notes)
    assert any("follow-up queueing is not supported" in n for n in notes)
    assert events[-1].data.reason == "completed"  # the turn still ran


@pytest.mark.asyncio
async def test_steer_mid_turn_emits_live_note(tmp_path):
    driver = ExternalAgentDriver(_profile("hang"), workdir=str(tmp_path))
    events = []
    async for ev in driver.send("hang please"):
        events.append(ev)
        if ev.type == LoopEventType.system and "ready" in str(ev.data):
            driver.steer("mid-run message")
            driver.cancel()
    notes = [str(ev.data) for ev in events if ev.type == LoopEventType.system]
    assert any("steering is not supported" in n for n in notes)


# -- env allowlist ------------------------------------------------------
@pytest.mark.asyncio
async def test_env_allowlist_filters_parent_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ALLOWED", "yes")
    monkeypatch.setenv("FAKE_SECRET", "leak-me-not")
    driver = ExternalAgentDriver(
        _profile("env", env_allow=("FAKE_ALLOWED",)), workdir=str(tmp_path),
    )
    events = await _run(driver)
    text = "".join(
        str(ev.data) for ev in events if ev.type == LoopEventType.assistant_chunk
    )
    assert "allowed=yes" in text
    assert "secret=unset" in text  # not allowlisted → not passed through


@pytest.mark.asyncio
async def test_default_env_inherits_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ALLOWED", "inherited")
    driver = ExternalAgentDriver(_profile("env"), workdir=str(tmp_path))
    events = await _run(driver)
    text = "".join(
        str(ev.data) for ev in events if ev.type == LoopEventType.assistant_chunk
    )
    assert "allowed=inherited" in text


# -- history round-trip --------------------------------------------------
@pytest.mark.asyncio
async def test_clear_and_load_history_round_trip(tmp_path):
    driver = ExternalAgentDriver(_profile("stream"), workdir=str(tmp_path))
    await _run(driver)
    assert len(driver.history) == 2
    driver.clear()
    assert driver.history == []
    seeded = [Message.user("earlier"), Message.assistant("done earlier")]
    driver.load_history(seeded)
    assert [m.content for m in driver.history] == ["earlier", "done earlier"]
    # a later turn appends after the seeded history
    await _run(driver, "again")
    assert [m.role for m in driver.history] == [
        "user", "assistant", "user", "assistant",
    ]


# -- consecutive turns ---------------------------------------------------
@pytest.mark.asyncio
async def test_two_turns_accrue_cost_and_turns(tmp_path):
    driver = ExternalAgentDriver(_profile("stream"), workdir=str(tmp_path))
    await _run(driver, "one")
    await _run(driver, "two")
    assert driver.turn_count == 2
    assert driver.total_cost == pytest.approx(0.0084)
    assert asyncio.iscoroutinefunction(driver.send) is False  # async generator
