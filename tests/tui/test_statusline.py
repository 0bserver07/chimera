"""Tests for the customizable status line + terminal title (R-STAT-1..5).

Layout mirrors the deliverable: pure item rendering (incl.
hide-when-unavailable), ordered composition from config, the width
degradation ladder, the async git watcher against a real temp repo, title
sanitization, and single/multi-lane integration via the pilot harness.
"""
import io
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")  # statusline needs rich (ships with the tui extra)

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.statusline import (  # noqa: E402
    DEFAULT_STATUS_LINE,
    DEFAULT_STATUS_LINE_MULTI,
    DEFAULT_TITLE,
    GitFacts,
    GitFactsWatcher,
    StatusContext,
    StatusItem,
    StatusLine,
    TerminalTitle,
    _resolve_git_dir,
    all_items,
    build_cohort_context,
    build_lane_context,
    compose_status,
    compose_title,
    format_tokens,
    get_item,
    parse_item_order,
    register_item,
    sanitize_title,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="git not installed")


def _render(item_id: str, ctx: StatusContext, *, short: bool = False):
    item = get_item(item_id)
    assert item is not None, f"unregistered item {item_id!r}"
    renderer = item.render_short if short else item.render
    assert renderer is not None
    return renderer(ctx)


def _plain(item_id: str, ctx: StatusContext, *, short: bool = False) -> str | None:
    text = _render(item_id, ctx, short=short)
    return None if text is None else text.plain


def _git(*args: str, cwd) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(
        [GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True,
    )


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    _git("config", "commit.gpgsign", "false", cwd=path)
    (path / "a.txt").write_text("hello\n")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "init", cwd=path)


def _wait_for(cond, *, timeout: float = 8.0, msg: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {msg}")


RICH_CTX = StatusContext(
    model="glm-5.2[1m]",
    reasoning="high",
    project_dir="/very/long/path/to/some/project/checkout",
    git=GitFacts(branch="feature/statusline", dirty=True),
    run_state="running",
    busy=True,
    context_used=90_000,
    context_window=128_000,
    auto_compaction=True,
    tokens_in=123_456,
    tokens_out=7_890,
    cost=1.2345,
    lanes_total=3,
    lanes_done=1,
    first_label="B",
    task="fix everything everywhere all at once",
    elapsed=12.3,
    mode="broadcast",
)

WIDE_ORDER = (
    "task", "progress", "model", "reasoning", "context-used", "tokens",
    "cost", "elapsed", "git", "project", "run-state", "mode", "version",
)


# ---------------------------------------------------------------------------
# 1. item rendering + hide-when-unavailable (R-STAT-1)
# ---------------------------------------------------------------------------

def test_every_catalog_item_is_registered():
    ids = {item.item_id for item in all_items()}
    assert {
        "model", "reasoning", "project", "git", "run-state", "context-used",
        "tokens", "cost", "progress", "version",
    } <= ids


def test_items_hide_when_unavailable():
    empty = StatusContext(cost=None)
    for item_id in (
        "model", "reasoning", "project", "git", "context-used", "tokens",
        "cost", "progress", "task", "elapsed", "mode", "hint",
    ):
        assert _render(item_id, empty) is None, f"{item_id} should hide on empty ctx"
    # run-state and version are the only always-on defaults given data.
    assert _plain("run-state", StatusContext(run_state="idle")) == "idle"
    assert _plain("version", empty).startswith("v")


def test_model_and_reasoning_render():
    ctx = StatusContext(model="glm-5.2", reasoning="high")
    assert _plain("model", ctx) == "glm-5.2"
    assert _plain("reasoning", ctx) == "high"
    long = StatusContext(model="a-very-long-model-name-here")
    assert _plain("model", long, short=True).endswith("…")


def test_git_item_variants():
    assert _plain("git", StatusContext(git=GitFacts(branch="main"))) == "main"
    assert _plain("git", StatusContext(git=GitFacts(branch="main", dirty=True))) == "main±"
    assert _plain("git", StatusContext(git=GitFacts(sha="abc1234"))) == "@abc1234"
    assert _render("git", StatusContext(git=GitFacts())) is None  # no facts yet


def test_context_meter_only_from_real_data():
    # No usage reported -> hidden, even with a window known (never invented).
    assert _render("context-used", StatusContext(context_window=128_000)) is None
    # Usage but no advertised window -> absolute form, no percentage.
    assert _plain("context-used", StatusContext(context_used=28_500)) == "28.5k ctx"
    # Both -> percent + used/window.
    both = StatusContext(context_used=28_500, context_window=128_000)
    assert _plain("context-used", both) == "22% · 28.5k/128k"
    assert _plain("context-used", both, short=True) == "22%"


def test_context_meter_thresholds_and_auto_marker():
    def meter(used, window=100_000, auto=False):
        return _render(
            "context-used",
            StatusContext(context_used=used, context_window=window, auto_compaction=auto),
        )

    assert meter(50_000).style == ""            # calm below 70%
    assert meter(75_000).style == "yellow"      # warn >= 70%
    assert meter(95_000).style == "bold red"    # error >= 90%
    assert "(auto)" in meter(50_000, auto=True).plain
    assert "(auto)" not in meter(50_000).plain


def test_tokens_cost_progress_render():
    ctx = RICH_CTX
    assert _plain("tokens", ctx) == "↑123.5k ↓7.9k"
    assert _plain("cost", ctx) == "Σ$1.2345"            # multi-lane Σ prefix
    assert _plain("cost", StatusContext(cost=0.5)) == "$0.5000"
    assert _plain("cost", ctx, short=True) == "$1.23"
    assert _plain("progress", ctx) == "done 1/3 · first B"
    assert _plain("progress", ctx, short=True) == "1/3"
    idle_multi = StatusContext(lanes_total=2)  # never raced
    assert _plain("progress", idle_multi) == "lanes 2"


def test_run_state_styles():
    assert _render("run-state", StatusContext(run_state="running")).style == "yellow"
    assert _render("run-state", StatusContext(run_state="error:boom")).style == "bold red"
    assert _render("run-state", StatusContext(run_state="done")).style == "green"


def test_format_tokens():
    assert format_tokens(950) == "950"
    assert format_tokens(28_500) == "28.5k"
    assert format_tokens(128_000) == "128k"
    assert format_tokens(1_200_000) == "1.2M"


def test_register_item_rejects_duplicates_unless_replace():
    extra = StatusItem("model", lambda ctx: None)
    with pytest.raises(ValueError):
        register_item(extra)
    custom = StatusItem("x-custom-test", lambda ctx: None, description="test-only")
    try:
        assert register_item(custom) is custom
        assert get_item("x-custom-test") is custom
    finally:
        from chimera.tui import statusline as _m

        _m._REGISTRY.pop("x-custom-test", None)


# ---------------------------------------------------------------------------
# 2. ordered composition from config
# ---------------------------------------------------------------------------

def test_compose_respects_configured_order():
    ctx = StatusContext(model="m1", cost=0.5, run_state="idle")
    line = compose_status(("cost", "model"), ctx, 100).plain
    assert line.index("$0.5000") < line.index("m1")


def test_compose_skips_unknown_ids_and_unavailable_items():
    ctx = StatusContext(model="m1")
    line = compose_status(("no-such-item", "context-used", "model"), ctx, 100).plain
    assert line == "m1"  # typo + hidden meter degrade silently


def test_parse_item_order_forms():
    assert parse_item_order(["model", "cost"], DEFAULT_STATUS_LINE) == ("model", "cost")
    assert parse_item_order("cost, model", DEFAULT_STATUS_LINE) == ("cost", "model")
    assert parse_item_order(None, DEFAULT_STATUS_LINE) == DEFAULT_STATUS_LINE
    assert parse_item_order([], DEFAULT_STATUS_LINE) == DEFAULT_STATUS_LINE
    assert parse_item_order(42, DEFAULT_STATUS_LINE) == DEFAULT_STATUS_LINE


def test_statusline_defaults_by_lane_count():
    single = StatusLine(config={})
    multi = StatusLine(config={}, single=False)
    assert single.order == DEFAULT_STATUS_LINE
    assert multi.order == DEFAULT_STATUS_LINE_MULTI
    assert single.title_order == DEFAULT_TITLE


def test_statusline_reads_config_order_and_title_off():
    sl = StatusLine(config={"status_line": "cost,model", "title": "off"})
    assert sl.order == ("cost", "model")
    assert sl.title_enabled is False
    assert sl.apply_title(RICH_CTX) == ""


def test_describe_lists_items_order_and_availability():
    sl = StatusLine(config={"status_line": ["model", "context-used", "nope"]})
    ctx = StatusContext(model="m1")  # context meter has no data
    lines = sl.describe(ctx)
    assert lines[0].startswith("status line: model → context-used → nope")
    joined = "\n".join(lines)
    assert "model" in joined and "#1" in joined
    assert "hidden (no data yet)" in joined      # context-used, configured but dataless
    assert "unknown ids ignored: nope" in joined
    assert any("select dialog" in ln for ln in lines)  # picker seam, stated


# ---------------------------------------------------------------------------
# 3. degradation ladder (R-STAT-2) — pure, no TTY
# ---------------------------------------------------------------------------

def test_line_never_exceeds_width_at_any_width():
    for width in range(0, 201, 1):
        line = compose_status(WIDE_ORDER, RICH_CTX, width)
        assert len(line.plain) <= width, f"wrapped at width {width}: {line.plain!r}"


def test_wide_line_shows_full_forms():
    from chimera import __version__

    line = compose_status(WIDE_ORDER, RICH_CTX, 300).plain
    assert "task: 'fix everything everywhere all at once'" in line
    assert "done 1/3 · first B" in line
    assert "70% · 90k/128k (auto)" in line
    assert "↑123.5k ↓7.9k" in line
    assert "feature/statusline±" in line
    assert "running" in line and "[broadcast]" in line
    assert line.endswith(f"v{__version__}")


def test_ladder_shortens_lowest_priority_first():
    # At a middling width the low-priority task swaps to its short form
    # while the high-priority context meter is still full.
    line = compose_status(("task", "context-used"), RICH_CTX, 45).plain
    assert "task:" not in line                  # task degraded first…
    assert "70% · 90k/128k (auto)" in line      # …meter still full


def test_ladder_drops_lowest_priority_first():
    # Width for exactly one short item: version (prio 20) must vanish before
    # run-state (prio 90).
    line = compose_status(("version", "run-state"), RICH_CTX, 9).plain
    assert line == "running"


def test_ladder_left_truncates_project_path():
    # A basename too long even for the short form forces the left-`…` rung.
    ctx = StatusContext(project_dir="/repos/really-extremely-long-project-checkout-name")
    line = compose_status(("project",), ctx, 18).plain
    assert line.startswith("…")
    assert line.endswith("name")
    assert len(line) <= 18


def test_ladder_hard_truncates_last_survivor():
    line = compose_status(("run-state",), StatusContext(run_state="running"), 4).plain
    assert len(line) <= 4 and line.startswith("ru")


def test_ladder_is_deterministic():
    a = compose_status(WIDE_ORDER, RICH_CTX, 60).plain
    b = compose_status(WIDE_ORDER, RICH_CTX, 60).plain
    assert a == b


# ---------------------------------------------------------------------------
# context data plumbing: what the meter reads is real (lane telemetry)
# ---------------------------------------------------------------------------

def _assistant_event(usage):
    return LoopEvent(
        LoopEventType.assistant, SimpleNamespace(content="hi", usage=usage), 0,
    )


def _lane(driver=None):
    from chimera.tui.lane import Lane, LaneConfig

    driver = driver or SimpleNamespace(context_window=128_000, tools=[])
    return Lane(LaneConfig(lane_id="A", label="L", model="glm-5.2"), driver, None)


def test_lane_tracks_latest_request_prompt_tokens():
    lane = _lane()
    lane.record(_assistant_event({"input_tokens": 100, "cache_read_input_tokens": 900}))
    assert lane.telemetry.context_tokens == 1000  # fresh + cache-read summed
    lane.record(_assistant_event({"input_tokens": 1_500}))
    assert lane.telemetry.context_tokens == 1500  # latest request wins
    lane.record(_assistant_event({}))             # step without usage reported
    assert lane.telemetry.context_tokens == 1500  # last real value sticks


def test_lane_result_usage_is_not_a_context_measure():
    lane = _lane()
    lane.record(_assistant_event({"input_tokens": 800}))
    result = SimpleNamespace(
        reason="completed", turn_count=3, cost_usd=0.01, messages=[],
        duration_ms=1.0, usage={"input_tokens": 99_999, "output_tokens": 5},
    )
    lane.record(LoopEvent(LoopEventType.result, result, 0))
    assert lane.telemetry.context_tokens == 800   # cumulative turn usage ignored


def test_build_lane_context_sources():
    driver = SimpleNamespace(context_window=128_000, tools=[], auto_compaction=True)
    lane = _lane(driver)
    lane.record(_assistant_event({"input_tokens": 640}))
    ctx = build_lane_context(lane, git=GitFacts(branch="main"))
    assert ctx.model == "glm-5.2"
    assert ctx.context_used == 640 and ctx.context_window == 128_000
    assert ctx.auto_compaction is True
    assert ctx.reasoning is None          # driver exposes no thinking level today
    assert ctx.git.branch == "main"
    assert ctx.run_state == "idle" and ctx.lanes_total == 1


def test_build_cohort_context_aggregates():
    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Liveness

    a, b = _lane(), _lane()
    a.telemetry.tokens_in, a.telemetry.tokens_out, a.telemetry.cost = 100, 10, 0.1
    b.telemetry.tokens_in, b.telemetry.cost = 200, 0.2
    cohort = Cohort([a, b], task="fix it")
    idle = build_cohort_context(cohort, mode="broadcast", racing=False)
    assert idle.model == "glm-5.2"        # identical models -> shown
    assert idle.run_state == "idle" and idle.hint is not None
    assert idle.tokens_in == 300 and idle.tokens_out == 10
    assert idle.cost == pytest.approx(0.3)
    a.telemetry.liveness = Liveness.RUNNING
    racing = build_cohort_context(cohort, mode="broadcast", racing=True, elapsed=1.0)
    assert racing.run_state == "running" and racing.busy and racing.hint is None
    b.config.model = "other"
    assert build_cohort_context(cohort, racing=False).model is None  # mixed models hide


# ---------------------------------------------------------------------------
# 4. async git facts (R-STAT-3) — a real temp repo
# ---------------------------------------------------------------------------

@requires_git
def test_watcher_branch_fast_path_and_switch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    watcher = GitFactsWatcher(repo, poll_interval=0.02, dirty_ttl=0.1)
    assert watcher.available
    try:
        watcher.start()
        # Branch is read synchronously on start (fast path, no wait).
        assert watcher.snapshot().branch == "main"
        # Switching branches rewrites HEAD via atomic rename (new inode);
        # the path-based directory watch must still see it.
        _git("checkout", "-b", "feature", cwd=repo)
        _wait_for(
            lambda: watcher.snapshot().branch == "feature", msg="branch switch seen",
        )
    finally:
        watcher.stop()


@requires_git
def test_watcher_dirty_flips_both_ways(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    watcher = GitFactsWatcher(repo, poll_interval=0.02, dirty_ttl=0.05)
    try:
        watcher.start()
        _wait_for(lambda: watcher.snapshot().dirty is False, msg="initial clean status")
        (repo / "a.txt").write_text("changed\n")   # TTL poll: edits don't touch HEAD
        _wait_for(lambda: watcher.snapshot().dirty is True, msg="dirty seen")
        (repo / "a.txt").write_text("hello\n")
        _wait_for(lambda: watcher.snapshot().dirty is False, msg="clean again")
    finally:
        watcher.stop()


@requires_git
def test_watcher_detached_head(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    watcher = GitFactsWatcher(repo, poll_interval=0.02, dirty_ttl=0.5)
    try:
        watcher.start()
        _git("checkout", "--detach", cwd=repo)
        _wait_for(
            lambda: watcher.snapshot().branch is None and watcher.snapshot().sha,
            msg="detached HEAD seen",
        )
        assert len(watcher.snapshot().sha) == 7
    finally:
        watcher.stop()


@requires_git
def test_watcher_resolves_linked_worktrees(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = tmp_path / "wt"
    _git("worktree", "add", str(wt), "-b", "wt-branch", cwd=repo)
    # .git is a file pointer here; it must resolve to the private gitdir.
    git_dir = _resolve_git_dir(wt)
    assert git_dir is not None and git_dir.is_dir()
    assert "worktrees" in git_dir.parts
    watcher = GitFactsWatcher(wt, poll_interval=0.02, dirty_ttl=0.5)
    try:
        watcher.start()
        assert watcher.snapshot().branch == "wt-branch"
    finally:
        watcher.stop()


def test_watcher_outside_a_repo_is_inert(tmp_path):
    watcher = GitFactsWatcher(tmp_path / "plain")
    assert watcher.available is False
    watcher.start()                      # no-op, no thread
    assert watcher.snapshot() is None
    watcher.stop()                       # safe without start


@requires_git
def test_watcher_clean_shutdown(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    watcher = GitFactsWatcher(repo, poll_interval=0.02)
    watcher.start()
    thread = watcher._thread
    assert thread is not None and thread.is_alive()
    watcher.stop()
    assert not thread.is_alive()
    assert watcher._thread is None


# ---------------------------------------------------------------------------
# 5. terminal title (R-STAT-5)
# ---------------------------------------------------------------------------

def test_sanitize_title_strips_control_and_bidi():
    assert sanitize_title("a\x1b]0;evil\x07b") == "a]0;evilb"   # ESC/BEL gone
    assert sanitize_title("x\n\r\ty") == "xy"
    # RLO / LRI direction overrides are printable but must be stripped.
    assert sanitize_title("safe \u202eevil\u2066 end") == "safe evil end"
    assert len(sanitize_title("A" * 500)) == 120


def test_compose_title_orders_and_skips():
    ctx = StatusContext(busy=True, project_dir="/tmp/proj", model="glm-5.2")
    assert compose_title(("activity", "project"), ctx) == "✳ proj"
    assert compose_title(("project",), StatusContext()) == ""     # nothing available
    assert compose_title(("no-such", "model"), ctx) == "glm-5.2"  # unknown skipped
    idle = StatusContext(busy=False, project_dir="/tmp/proj")
    assert compose_title(DEFAULT_TITLE, idle) == "proj"           # no idle spinner


def test_terminal_title_push_set_dedupe_restore():
    buf = io.StringIO()
    title = TerminalTitle(buf, enabled=True)
    title.apply("hello")
    assert buf.getvalue() == "\x1b[22;0t\x1b]0;hello\x07"  # push once, then set
    title.apply("hello")                                    # unchanged -> no rewrite
    assert buf.getvalue() == "\x1b[22;0t\x1b]0;hello\x07"
    title.apply("world")
    assert buf.getvalue().endswith("\x1b]0;world\x07")
    assert buf.getvalue().count("\x1b[22;0t") == 1
    title.restore()
    assert buf.getvalue().endswith("\x1b[23;0t")            # pop restores prior title
    title.restore()                                         # idempotent
    assert buf.getvalue().count("\x1b[23;0t") == 1


def test_terminal_title_is_noop_off_tty_and_survives_broken_streams():
    buf = io.StringIO()                       # .isatty() is False
    TerminalTitle(buf).apply("hi")
    assert buf.getvalue() == ""
    no_restore = TerminalTitle(buf)
    no_restore.restore()                      # restore without apply: nothing
    assert buf.getvalue() == ""

    class Broken:
        def write(self, data):  # noqa: ARG002
            raise OSError("gone")

        def flush(self):
            raise OSError("gone")

    broken = TerminalTitle(Broken(), enabled=True)
    broken.apply("a")                         # disables itself, no raise
    broken.apply("b")


# ---------------------------------------------------------------------------
# config discovery (spec §11) — real loader, isolated scopes
# ---------------------------------------------------------------------------

def _write_cfg(root, payload):
    (root / ".chimera").mkdir(parents=True, exist_ok=True)
    (root / ".chimera" / "config.json").write_text(json.dumps(payload))


def test_load_tui_config_project_over_user(tmp_path, real_load_tui_config):
    home, project = tmp_path / "home", tmp_path / "proj"
    _write_cfg(home, {"tui": {"status_line": ["model", "cost"], "title": ["project"]}})
    _write_cfg(project, {"tui": {"status_line": ["cost"]}})
    cfg = real_load_tui_config(project, home=home)
    assert cfg["status_line"] == ["cost"]     # project wins…
    assert cfg["title"] == ["project"]        # …user keys survive the merge


def test_load_tui_config_yaml_scope_and_absence(tmp_path, real_load_tui_config):
    home, project = tmp_path / "home", tmp_path / "proj"
    scope = home / ".config" / "chimera"
    scope.mkdir(parents=True)
    (scope / "config.yaml").write_text("tui:\n  status_line: [model, git]\n")
    assert real_load_tui_config(project, home=home)["status_line"] == ["model", "git"]
    empty = tmp_path / "elsewhere"
    assert real_load_tui_config(empty, home=empty) == {}


def test_load_tui_config_broken_file_degrades(tmp_path, real_load_tui_config):
    home, project = tmp_path / "home", tmp_path / "proj"
    (project / ".chimera").mkdir(parents=True)
    (project / ".chimera" / "config.json").write_text("{not json")
    assert real_load_tui_config(project, home=home) == {}


# ---------------------------------------------------------------------------
# 6. integration via the pilot harness (single & multi lane)
# ---------------------------------------------------------------------------

class _ToolRes:
    output = "ok"
    success = True


class FakeDriver:
    """Scripted AgentDriver stand-in whose steps report real-looking usage."""

    def __init__(self, model="glm-5.2", cost=0.001, *, window=100_000,
                 used=None, auto=False):
        self.model = model
        self.tools: list = []
        self.total_cost = 0.0
        self.history: list = []
        self.context_window = window
        self.auto_compaction = auto
        self._cost, self._used = cost, used
        self.steered: list[str] = []

    async def send(self, text):  # noqa: ARG002
        usage = {"input_tokens": self._used} if self._used else {}
        yield LoopEvent(LoopEventType.assistant_chunk, "hi", 0)
        yield LoopEvent(
            LoopEventType.assistant, SimpleNamespace(content="hi", usage=usage), 0,
        )
        result = SimpleNamespace(
            reason="completed", turn_count=2, cost_usd=self._cost, messages=[],
            duration_ms=1.0, usage={"input_tokens": 100, "output_tokens": 50},
        )
        yield LoopEvent(LoopEventType.result, result, 0)

    def steer(self, text):
        self.steered.append(text)

    def cancel(self):
        pass

    def clear(self):
        pass

    def queue_follow_up(self, text):
        pass


def _cohort(drivers, task=None):
    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Lane, LaneConfig
    from chimera.tui.routing import RoutingMode

    lanes = [
        Lane(LaneConfig(lane_id=chr(65 + i), label=f"{d.model}-{i}", model=d.model), d, None)
        for i, d in enumerate(drivers)
    ]
    routing = RoutingMode.TARGETED if len(lanes) == 1 else RoutingMode.BROADCAST
    return Cohort(lanes, task=task, routing=routing)


async def _submit(app, pilot, text):
    from chimera.tui.prompt import PromptArea

    app.query_one("#prompt", PromptArea).value = text
    await pilot.press("enter")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_single_lane_status_defaults_then_context_meter():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver(used=90_000, auto=True)
    app = MultiplexApp(_cohort([d]))
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        before = app._global_status_text().plain
        assert "glm-5.2" in before and "idle" in before
        assert "%" not in before                     # no usage yet -> meter hidden
        await _submit(app, pilot, "fix the bug")
        after = app._global_status_text().plain
        assert "90% · 90k/100k (auto)" in after      # provider-reported, threshold-armed
        assert "$0.0010" in after and "done" in after


@pytest.mark.asyncio
async def test_multi_lane_status_keeps_the_scoreboard():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    # cohort.task is a construction-time fact (the --task / resume flow);
    # the task item hides when it was never set, so seed it here.
    app = MultiplexApp(
        _cohort([FakeDriver("m1"), FakeDriver("m2", cost=0.002)], task="fix the bug"),
    )
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        idle = app._global_status_text().plain
        assert "lanes 2" in idle and "idle" in idle
        assert "Σ$0.0000" in idle and "[broadcast]" in idle
        assert "type a task, Enter to race" in idle
        await _submit(app, pilot, "fix the bug")
        racing = app._global_status_text().plain
        assert "task: 'fix the bug'" in racing
        assert "done 2/2" in racing and "first" in racing
        assert "Σ$0.0030" in racing and "done" in racing


@pytest.mark.asyncio
async def test_statusline_command_lists_catalog():
    pytest.importorskip("textual")
    from textual.widgets import RichLog

    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_cohort([FakeDriver()]))
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/statusline")
        lines = [str(strip.text) for strip in app.query_one(RichLog).lines]
        joined = "\n".join(lines)
        assert "status line: model → context-used → cost → budget → run-state" in joined
        assert "context-used" in joined and "hidden" in joined  # availability shown
        assert "select dialog" in joined                        # picker seam declared


@pytest.mark.asyncio
async def test_title_applied_via_textual_and_restored_on_exit():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    buf = io.StringIO()
    app = MultiplexApp(_cohort([FakeDriver()]))
    app._statusline.title_order = ("model",)          # deterministic title source
    app._statusline._title = TerminalTitle(buf, enabled=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._refresh_global()
        assert app.sub_title == "glm-5.2"             # Textual's title mechanism
        assert "\x1b]0;glm-5.2\x07" in buf.getvalue()  # emulator title (OSC 0)
    assert buf.getvalue().endswith("\x1b[23;0t")      # prior title restored on exit


@pytest.mark.asyncio
async def test_git_watcher_lifecycle_is_bound_to_the_app(tmp_path):
    pytest.importorskip("textual")
    if GIT is None:
        pytest.skip("git not installed")
    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Lane, LaneConfig
    from chimera.tui.multiplex import MultiplexApp
    from chimera.tui.routing import RoutingMode

    repo = tmp_path / "repo"
    _init_repo(repo)
    d = FakeDriver()
    lane = Lane(LaneConfig(lane_id="A", label="L", model=d.model), d, None)
    cohort = Cohort([lane], routing=RoutingMode.TARGETED, source=str(repo))
    app = MultiplexApp(cohort)
    # Opt the git item in (the watcher only starts when the layout needs it).
    app._statusline.order = ("model", "git", "run-state")
    app._statusline._watcher = GitFactsWatcher(repo, poll_interval=0.02, dirty_ttl=0.2)
    async with app.run_test() as pilot:
        await pilot.pause()
        thread = app._statusline._watcher._thread
        assert thread is not None and thread.is_alive()
        _wait_for(
            lambda: "main" in app._global_status_text().plain, msg="branch in status",
        )
    assert not thread.is_alive()                      # clean shutdown on app exit


# ---------------------------------------------------------------------------
# budget meter (#170)
# ---------------------------------------------------------------------------

def test_budget_item_hidden_when_no_budget():
    item = get_item("budget")
    assert item is not None
    assert item.render(StatusContext()) is None  # hide-when-unset


def test_budget_item_shows_used_vs_cap():
    from chimera.core.budget import BudgetSpec

    ctx = StatusContext(
        budget=BudgetSpec(max_cost_usd=0.10, max_llm_calls=20),
        budget_cost_used=0.04, budget_steps_used=3,
    )
    text = get_item("budget").render(ctx)
    assert text is not None
    assert "$0.0400/$0.10" in text.plain
    assert "3/20 steps" in text.plain
    assert text.plain.startswith("budget ")


def test_budget_item_threshold_colored():
    from chimera.core.budget import BudgetSpec

    warn = StatusContext(budget=BudgetSpec(max_cost_usd=1.0), budget_cost_used=0.75)
    assert str(get_item("budget").render(warn).style) == "yellow"
    err = StatusContext(budget=BudgetSpec(max_cost_usd=1.0), budget_cost_used=0.95)
    assert str(get_item("budget").render(err).style) == "bold red"
    ok = StatusContext(budget=BudgetSpec(max_cost_usd=1.0), budget_cost_used=0.10)
    assert str(get_item("budget").render(ok).style) == ""


def test_budget_item_sigma_prefix_for_cohort():
    from chimera.core.budget import BudgetSpec

    ctx = StatusContext(
        budget=BudgetSpec(max_cost_usd=1.0), budget_cost_used=0.1, lanes_total=3,
    )
    assert get_item("budget").render(ctx).plain.startswith("Σ budget ")


def test_build_lane_context_wires_budget():
    from chimera.core.budget import BudgetSpec, BudgetTally
    from chimera.tui.lane import Lane, LaneConfig

    spec = BudgetSpec(max_cost_usd=0.10)
    tally = BudgetTally(cost_usd=0.03, llm_calls=2)
    driver = SimpleNamespace(
        context_window=128_000, tools=[], budget=spec, budget_tally=tally,
    )
    lane = Lane(LaneConfig(lane_id="A", label="L", model="glm-5.2", budget=spec), driver, None)
    ctx = build_lane_context(lane)
    assert ctx.budget == spec
    assert ctx.budget_cost_used == 0.03
    assert ctx.budget_steps_used == 2
