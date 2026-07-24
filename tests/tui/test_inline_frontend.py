"""Tests for the opt-in inline (native-scrollback) daily-driver frontend.

Covers the pure pieces TTY-free (rich line rendering, the key interpreter, the
band builder), a real drive of :class:`InlineFrontend` against a fake driver
(proving it commits + persists + restores without a terminal), and the pilot
integration: :func:`run_single_agent` selecting the inline vs full-screen path.

Rich is an optional extra; CI installs none, so the whole module skips there.
"""
from __future__ import annotations

import asyncio
import io
import sys
from typing import Any

import pytest

pytest.importorskip("rich")  # the inline frontend renders via rich (tui extra)

from rich.markdown import Markdown  # noqa: E402
from rich.text import Text  # noqa: E402

from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult  # noqa: E402
from chimera.tui import inline_frontend as inf  # noqa: E402
from chimera.tui.inline_frontend import (  # noqa: E402
    BandModel,
    InlineFrontend,
    build_band_rows,
    hard_wrap_cells,
    interpret_key,
    render_ansi_lines,
    visible_cells,
)
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.scrollback import strip_ansi  # noqa: E402


# ---------------------------------------------------------------------------
# Rich line rendering (width-accurate)
# ---------------------------------------------------------------------------


class TestRenderHelpers:
    def test_visible_cells_counts_wide_chars_and_ignores_ansi(self) -> None:
        assert visible_cells("\x1b[1mab\x1b[0m你") == 4  # CJK char = 2 cells

    def test_hard_wrap_ascii_and_empty(self) -> None:
        assert hard_wrap_cells("abcdef", 3) == ["abc", "def"]
        assert hard_wrap_cells("", 10) == [""]

    def test_hard_wrap_never_splits_wide_chars(self) -> None:
        lines = hard_wrap_cells("你好你好", 5)  # each char 2 cells
        assert all(visible_cells(line) <= 5 for line in lines)
        assert "".join(lines) == "你好你好"

    def test_render_ansi_lines_respects_width(self) -> None:
        md = Markdown(
            "# Title\n\nSome prose with a fairly long sentence that must wrap, "
            "plus 宽字符文本 and `inline code`.\n"
        )
        for width in (24, 40, 79):
            lines = render_ansi_lines(md, width)
            assert lines, "renderer produced no lines"
            assert all(visible_cells(line) <= width for line in lines)

    def test_render_ansi_lines_drops_trailing_blank(self) -> None:
        # The blank spacer the transcript emits between blocks renders to
        # nothing; the frontend commits one blank row for it instead.
        assert render_ansi_lines(Text(""), 40) == []


# ---------------------------------------------------------------------------
# Pure keyboard interpretation
# ---------------------------------------------------------------------------


class TestInterpretKey:
    def test_enter_submits_when_idle_and_clears_composer(self) -> None:
        a = interpret_key("\r", "hello", running=False)
        assert a.submit == "hello"
        assert a.composer == ""
        assert a.quit is False

    def test_enter_is_inert_mid_turn(self) -> None:
        a = interpret_key("\n", "queued text", running=True)
        assert a.submit is None
        assert a.composer == "queued text"

    def test_backspace(self) -> None:
        assert interpret_key("\x7f", "abc", running=False).composer == "ab"
        assert interpret_key("\x08", "a", running=False).composer == ""
        assert interpret_key("\x7f", "", running=False).composer == ""

    def test_ctrl_u_kills_line(self) -> None:
        assert interpret_key("\x15", "abc", running=False).composer == ""

    def test_ctrl_d_quits_only_on_empty_idle_line(self) -> None:
        assert interpret_key("\x04", "", running=False).quit is True
        assert interpret_key("\x04", "x", running=False).quit is False
        assert interpret_key("\x04", "", running=True).quit is False

    def test_printable_appends_when_idle_and_is_inert_running(self) -> None:
        assert interpret_key("z", "ab", running=False).composer == "abz"
        assert interpret_key("你", "", running=False).composer == "你"
        assert interpret_key("z", "ab", running=True).composer == "ab"

    def test_stray_control_bytes_are_ignored(self) -> None:
        assert interpret_key("\x00", "ab", running=False).composer == "ab"


# ---------------------------------------------------------------------------
# Pure band builder
# ---------------------------------------------------------------------------


def _model(**over: Any) -> BandModel:
    base: dict[str, Any] = dict(
        model="glm-x", composer="hi", running=False, cost=0.0, steps=0,
        elapsed=0.0, committed=5, thinking=False, thinking_chars=0,
        tail_chars=0, rows_total=24, frame=0, interactive=True,
    )
    base.update(over)
    return BandModel(**base)


class TestBuildBandRows:
    def test_three_rows_each_fit_width(self) -> None:
        rows, _ = build_band_rows(_model(), 80)
        assert len(rows) == 3
        assert all(visible_cells(r) <= 80 for r in rows)

    def test_idle_status_names_model_and_advertises_scrollback(self) -> None:
        rows, _ = build_band_rows(_model(model="glm-5.2"), 80)
        sep, composer, status = (strip_ansi(r) for r in rows)
        assert "native scrollback" in sep
        assert "❯ hi" in composer
        assert "ready" in status and "glm-5.2" in status

    def test_park_col_sits_after_the_typed_text(self) -> None:
        _, park_col = build_band_rows(_model(composer="hi"), 80)
        assert park_col == len("❯ ") + visible_cells("hi") + 1  # 2 + 2 + 1

    def test_running_status_shows_spinner_and_counters(self) -> None:
        rows, _ = build_band_rows(
            _model(running=True, cost=0.0042, steps=3, elapsed=12.3), 80
        )
        status = strip_ansi(rows[2])
        assert "working" in status and "12.3s" in status
        assert "$0.0042" in status and "3 steps" in status

    def test_running_thinking_hint(self) -> None:
        rows, _ = build_band_rows(
            _model(running=True, thinking=True, thinking_chars=1500), 80
        )
        assert "thinking" in strip_ansi(rows[2])

    def test_narrow_width_still_fits(self) -> None:
        rows, park_col = build_band_rows(_model(composer="x" * 100), 20)
        assert all(visible_cells(r) <= 20 for r in rows)
        assert park_col <= 20  # never parks past the screen edge


# ---------------------------------------------------------------------------
# A fake driver / lane to drive the frontend without a real agent
# ---------------------------------------------------------------------------


class _FakeDriver:
    """Minimal :class:`DriverProtocol` that replays a scripted event list."""

    context_window = 128_000
    total_cost = 0.0

    def __init__(self, events: list[LoopEvent]) -> None:
        self._events = events
        self.cancelled = False
        self.cleared = False

    async def send(self, text: str) -> Any:
        for ev in self._events:
            yield ev

    def steer(self, text: str) -> None: ...
    def queue_follow_up(self, text: str) -> None: ...
    def cancel(self) -> None:
        self.cancelled = True

    def clear(self) -> None:
        self.cleared = True

    def load_history(self, messages: list[Any]) -> None: ...

    @property
    def tools(self) -> list[Any]:
        return []

    @property
    def history(self) -> list[Any]:
        return []


def _lane(events: list[LoopEvent]) -> Lane:
    driver = _FakeDriver(events)
    return Lane(LaneConfig(lane_id="A", label="glm-x", model="glm-x"), driver, None)


def _scripted_turn() -> list[LoopEvent]:
    return [
        LoopEvent(LoopEventType.assistant_chunk, "Hello ", turn=1),
        LoopEvent(LoopEventType.assistant_chunk, "world.\n\n", turn=1),
        LoopEvent(
            LoopEventType.result,
            LoopResult(
                reason="completed", messages=[],
                usage={"input_tokens": 10, "output_tokens": 5},
                cost_usd=0.001, duration_ms=1.0, turn_count=1,
            ),
            turn=1,
        ),
    ]


class TestInlineFrontendDrive:
    def test_drives_commits_persists_and_restores(self) -> None:
        out = io.StringIO()
        lane = _lane(_scripted_turn())
        frontend = InlineFrontend(lane, out=out, markdown=False)
        # No interactive stdin under pytest, so pre-enqueue a quit to leave the
        # input loop right after the auto-submitted initial task.
        frontend._input_q.put_nowait(inf._QUIT)
        asyncio.run(frontend.run(initial_task="do the thing"))

        raw = out.getvalue()
        visible = strip_ansi(raw)
        # committed transcript reached native scrollback (assistant prose, the
        # user echo, and the dim result line)
        assert "Hello world." in visible
        assert "› do the thing" in visible
        assert "1 steps" in visible
        assert frontend.screen.committed > 0
        # telemetry + persisted transcript folded identically to the mux
        assert lane.telemetry.turns == 1
        assert abs(lane.telemetry.cost - 0.001) < 1e-9
        assert "Hello world." in lane.transcript_text()
        # terminal left sane: region reset + cursor shown on the way out
        assert "\x1b[r" in raw and "\x1b[?25h" in raw

    def test_interrupt_cancels_a_running_turn_then_quits_when_idle(self) -> None:
        lane = _lane([])
        frontend = InlineFrontend(lane, out=io.StringIO(), markdown=False)
        frontend._running = True
        frontend._on_interrupt()
        assert lane.driver.cancelled is True  # type: ignore[attr-defined]
        assert frontend._quit is False
        frontend._running = False
        frontend._on_interrupt()
        assert frontend._quit is True

    def test_slash_exit_leaves_and_clear_forwards_to_driver(self) -> None:
        lane = _lane([])
        frontend = InlineFrontend(lane, out=io.StringIO(), markdown=False)
        assert frontend._handle_command("/exit") is True
        assert frontend._handle_command("/clear") is False
        assert lane.driver.cleared is True  # type: ignore[attr-defined]
        assert frontend._handle_command("/help") is False


# ---------------------------------------------------------------------------
# Pilot: run_single_agent selects the inline vs full-screen path
# ---------------------------------------------------------------------------


class _FakeWS:
    path = "/tmp/inline-pilot"
    strategy = "inplace"


class _FakeWorkspaces:
    strategy = "inplace"

    def __init__(self) -> None:
        self.cleaned = False

    def __getitem__(self, i: int) -> _FakeWS:
        return _FakeWS()

    def cleanup_all(self) -> None:
        self.cleaned = True


class _FakeCohort:
    def __init__(self, lanes: Any, **kw: Any) -> None:
        self.lanes = list(lanes)
        self.kw = kw


def _patch_run_single_agent(monkeypatch: pytest.MonkeyPatch, decision: Any) -> list[str]:
    """Stub run_single_agent's construction + both runners; return a call log."""
    from chimera.tui import multiplex

    calls: list[str] = []
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(
        "chimera.tui.workspace.provision_workspaces",
        lambda *a, **k: _FakeWorkspaces(),
    )
    monkeypatch.setattr("chimera.assembly.driver.AgentDriver", lambda **k: object())
    monkeypatch.setattr(multiplex, "Cohort", _FakeCohort)
    monkeypatch.setattr(multiplex, "inline_capability", lambda requested: decision)
    monkeypatch.setattr(
        multiplex, "_run_inline_single",
        lambda *a, **k: calls.append("inline") or "inline-dir",
    )
    monkeypatch.setattr(
        multiplex, "_run_cohort_loop",
        lambda *a, **k: calls.append("full") or "full-dir",
    )
    return calls


class TestPathSelection:
    def test_inline_capability_grant_runs_inline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("textual")
        from chimera.tui import multiplex
        from chimera.tui.scrollback import InlineDecision

        calls = _patch_run_single_agent(monkeypatch, InlineDecision(True, "inline"))
        result = multiplex.run_single_agent(model="glm-x", inline=True, project_dir="/tmp")
        assert calls == ["inline"]
        assert result == "inline-dir"

    def test_refusal_falls_back_to_full_screen_with_a_note(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pytest.importorskip("textual")
        from chimera.tui import multiplex
        from chimera.tui.scrollback import InlineDecision

        calls = _patch_run_single_agent(
            monkeypatch, InlineDecision(False, "multiplexer:ZELLIJ")
        )
        result = multiplex.run_single_agent(model="glm-x", inline=True, project_dir="/tmp")
        assert calls == ["full"]
        assert result == "full-dir"
        err = capsys.readouterr().err
        assert "inline mode unavailable (multiplexer:ZELLIJ)" in err
        assert "full-screen" in err

    def test_disabled_is_silent_full_screen(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pytest.importorskip("textual")
        from chimera.tui import multiplex
        from chimera.tui.scrollback import InlineDecision

        calls = _patch_run_single_agent(monkeypatch, InlineDecision(False, "disabled"))
        result = multiplex.run_single_agent(model="glm-x", inline=False, project_dir="/tmp")
        assert calls == ["full"]
        assert result == "full-dir"
        assert "inline mode unavailable" not in capsys.readouterr().err


class TestResolveInline:
    def test_arg_short_circuits_config(self) -> None:
        from chimera.tui import multiplex

        assert multiplex._resolve_inline(None, True) is True

    def test_reads_the_config_knob(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chimera.tui import multiplex

        monkeypatch.setattr(
            "chimera.config.user_config.load_tui_config", lambda p: {"inline": True}
        )
        assert multiplex._resolve_inline("/x", False) is True
        monkeypatch.setattr(
            "chimera.config.user_config.load_tui_config", lambda p: {}
        )
        assert multiplex._resolve_inline("/x", False) is False
