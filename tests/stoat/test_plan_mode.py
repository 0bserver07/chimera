"""Tests for the stoat plan-mode posture.

Plan mode is the third posture beyond agent / shell. It's split across:

* :class:`PlanModeManager` — the in-memory toggle.
* :class:`Plan` + :func:`save_plan` / :func:`load_plan` /
  :func:`iter_plans` — the persistence layer (``~/.chimera/plans/``,
  one JSON file per plan).
* :func:`build_plan_system_prompt` — wraps the agent system prompt with
  the "produce a plan, ask for confirmation, don't act" addendum.

The REPL integration (``/plan`` slash + ``run_plan_turn``) is exercised
in ``test_repl.py``-style fixtures here too.
"""

from __future__ import annotations

import io

import pytest

from chimera.stoat.plan_mode import (
    MODE_PLAN,
    PLAN_SYSTEM_PROMPT,
    Plan,
    PlanModeManager,
    build_plan_system_prompt,
    default_plans_dir,
    iter_plans,
    load_plan,
    save_plan,
)
from chimera.stoat.repl import StoatRepl
from chimera.stoat.shell_mode import MODE_AGENT, MODE_SHELL, ShellModeManager
from chimera.stoat.slash import SlashPalette, build_default_palette


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_mode_plan_constant() -> None:
    """The plan-mode label is exposed as a top-level constant."""
    assert MODE_PLAN == "plan"


def test_plan_system_prompt_mentions_confirmation() -> None:
    """The system prompt addendum demands a confirmation question."""
    assert "PLAN" in PLAN_SYSTEM_PROMPT
    assert "confirm" in PLAN_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# PlanModeManager
# ---------------------------------------------------------------------------


def test_default_plan_manager_is_inactive() -> None:
    """Plan mode is off by default."""
    pm = PlanModeManager()
    assert pm.is_active() is False
    assert pm.last_plan_id is None


def test_plan_manager_toggle_flips_state() -> None:
    """``toggle()`` returns the new state and mutates ``active``."""
    pm = PlanModeManager()
    assert pm.toggle() is True
    assert pm.is_active() is True
    assert pm.toggle() is False
    assert pm.is_active() is False


def test_plan_manager_enable_and_disable_are_idempotent() -> None:
    """``enable`` / ``disable`` can be called repeatedly."""
    pm = PlanModeManager()
    pm.enable()
    pm.enable()
    assert pm.is_active() is True
    pm.disable()
    pm.disable()
    assert pm.is_active() is False


def test_plan_manager_prompt_default() -> None:
    """The default plan-mode prompt is distinct from agent/shell."""
    pm = PlanModeManager()
    sm = ShellModeManager()
    assert pm.plan_prompt != sm.agent_prompt
    assert pm.plan_prompt != sm.shell_prompt


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------


def test_plan_new_populates_id_and_timestamp() -> None:
    """``Plan.new`` produces a fresh id + ISO timestamp."""
    plan = Plan.new(prompt="add tests", content="1. write tests")
    assert plan.plan_id.startswith("plan-")
    assert "T" in plan.created_at
    assert plan.prompt == "add tests"
    assert plan.content == "1. write tests"
    assert plan.tags == []


def test_plan_new_ids_are_unique() -> None:
    """Two consecutive ``Plan.new`` calls produce different ids."""
    a = Plan.new(prompt="x", content="y")
    b = Plan.new(prompt="x", content="y")
    assert a.plan_id != b.plan_id


def test_plan_to_json_round_trips_via_load(tmp_path) -> None:
    """JSON written by ``save_plan`` round-trips through ``load_plan``."""
    plan = Plan.new(
        prompt="ship g7",
        content="1. wire chord\n2. ship plan mode",
        model="kimi-k2.6",
        cwd="/repo",
    )
    target = save_plan(plan, root=tmp_path)
    assert target.is_file()

    loaded = load_plan(plan.plan_id, root=tmp_path)
    assert loaded.plan_id == plan.plan_id
    assert loaded.prompt == plan.prompt
    assert loaded.content == plan.content
    assert loaded.model == "kimi-k2.6"
    assert loaded.cwd == "/repo"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def test_default_plans_dir_honors_chimera_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """``default_plans_dir`` follows ``$CHIMERA_HOME`` for embedders."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    target = default_plans_dir()
    assert str(target).startswith(str(tmp_path))
    assert target.name == "plans"


def test_save_plan_creates_directory(tmp_path) -> None:
    """``save_plan`` ``mkdir(parents=True)`` so the dir doesn't have to exist."""
    nested = tmp_path / "deep" / "nested" / "plans"
    plan = Plan.new(prompt="x", content="y")
    target = save_plan(plan, root=nested)
    assert target.parent == nested
    assert target.is_file()


def test_load_plan_raises_for_missing(tmp_path) -> None:
    """Missing plans surface a ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        load_plan("plan-2026-01-01T00-00-00Z-deadbeef", root=tmp_path)


def test_load_plan_raises_for_malformed(tmp_path) -> None:
    """Malformed JSON surfaces a ``ValueError``."""
    plan_id = "plan-2026-01-01T00-00-00Z-deadbeef"
    (tmp_path / f"{plan_id}.json").write_text("not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_plan(plan_id, root=tmp_path)


def test_iter_plans_returns_newest_first(tmp_path) -> None:
    """``iter_plans`` orders by id descending so newest is first."""
    older = Plan(
        plan_id="plan-2026-01-01T00-00-00Z-aaaaaaaa",
        created_at="2026-01-01T00:00:00Z",
        prompt="old",
        content="old",
    )
    newer = Plan(
        plan_id="plan-2026-05-06T22-00-00Z-bbbbbbbb",
        created_at="2026-05-06T22:00:00Z",
        prompt="new",
        content="new",
    )
    save_plan(older, root=tmp_path)
    save_plan(newer, root=tmp_path)

    plans = list(iter_plans(root=tmp_path))
    assert [p.plan_id for p in plans] == [newer.plan_id, older.plan_id]


def test_iter_plans_handles_missing_dir(tmp_path) -> None:
    """A missing plans dir yields an empty iterator (no exception)."""
    missing = tmp_path / "does-not-exist"
    assert list(iter_plans(root=missing)) == []


def test_iter_plans_skips_malformed(tmp_path) -> None:
    """Malformed files are skipped; valid files still surface."""
    plan = Plan.new(prompt="ok", content="ok")
    save_plan(plan, root=tmp_path)
    (tmp_path / "plan-2026-01-01T00-00-00Z-bad.json").write_text(
        "not json", encoding="utf-8"
    )
    plans = list(iter_plans(root=tmp_path))
    assert len(plans) == 1
    assert plans[0].plan_id == plan.plan_id


# ---------------------------------------------------------------------------
# build_plan_system_prompt
# ---------------------------------------------------------------------------


def test_build_plan_system_prompt_appends_addendum() -> None:
    """The base prompt is preserved; the addendum is appended."""
    out = build_plan_system_prompt("You are Stoat.")
    assert out.startswith("You are Stoat.")
    assert "PLAN mode" in out


def test_build_plan_system_prompt_handles_empty() -> None:
    """An empty base returns just the addendum."""
    assert build_plan_system_prompt("") == PLAN_SYSTEM_PROMPT
    assert build_plan_system_prompt(None) == PLAN_SYSTEM_PROMPT  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Slash palette integration
# ---------------------------------------------------------------------------


def _palette(plan_mode: PlanModeManager | None = None) -> SlashPalette:
    return build_default_palette(
        shell_mode=ShellModeManager(),
        model="kimi-k2.6",
        plan_mode=plan_mode,
    )


def test_plan_slash_toggles_plan_mode() -> None:
    """``/plan`` flips the plan-mode manager."""
    pm = PlanModeManager()
    palette = _palette(plan_mode=pm)
    result = palette.dispatch("/plan")
    assert result.handled is True
    assert pm.is_active() is True
    assert "plan mode" in (result.text or "")

    result = palette.dispatch("/plan")
    assert pm.is_active() is False
    assert "plan mode off" in (result.text or "")


def test_plan_slash_reports_when_unavailable() -> None:
    """Without a manager, ``/plan`` says so but doesn't crash."""
    palette = _palette(plan_mode=None)
    result = palette.dispatch("/plan")
    assert result.handled is True
    assert "unavailable" in (result.text or "").lower()


def test_shell_slash_clears_plan_mode_when_active() -> None:
    """``/shell`` leaves plan mode so the postures stay mutually exclusive."""
    pm = PlanModeManager(active=True)
    palette = _palette(plan_mode=pm)
    palette.dispatch("/shell")
    assert pm.is_active() is False


# ---------------------------------------------------------------------------
# REPL integration
# ---------------------------------------------------------------------------


def _repl(inputs: list[str], **kwargs) -> tuple[StoatRepl, io.StringIO]:
    out = io.StringIO()
    iterator = iter(inputs)

    def fake_input(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError() from exc

    repl = StoatRepl(
        model="kimi-k2.6",
        workdir=".",
        max_steps=5,
        out=out,
        input_fn=fake_input,
        **kwargs,
    )
    return repl, out


def test_repl_can_start_in_plan_mode() -> None:
    """``start_in_plan_mode=True`` boots into the plan posture."""
    repl, _ = _repl([], start_in_plan_mode=True)
    assert repl.plan_mode.is_active() is True
    assert repl.shell_mode.mode == MODE_AGENT


def test_repl_plan_slash_toggles_plan_mode() -> None:
    """``/plan`` from the REPL flips the manager and renders a banner."""
    repl, out = _repl(["/plan", "/exit"])
    repl.run()
    text = out.getvalue()
    assert "plan mode" in text
    assert repl.plan_mode.is_active() is True


def test_repl_banner_shows_plan_posture() -> None:
    """Booting with plan mode active surfaces ``posture: plan`` in the banner."""
    repl, out = _repl(["/exit"], start_in_plan_mode=True)
    repl.run()
    assert "posture: plan" in out.getvalue()


def test_repl_chord_callbacks_render_banners() -> None:
    """Direct chord callback invocations write banners to the REPL output."""
    repl, out = _repl(["/exit"])

    repl._on_chord_plan(True)
    repl._on_chord_shell(MODE_SHELL)
    repl._on_chord_help("CHORD HELP TEXT")

    repl.run()  # consume the /exit so the StringIO is final

    text = out.getvalue()
    assert "plan mode" in text
    assert "shell mode" in text
    assert "CHORD HELP TEXT" in text


def test_repl_plan_mode_routes_to_run_plan_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """In plan mode, plain input is routed through ``run_plan_turn``."""
    repl, out = _repl(["plan: add tests", "/exit"], start_in_plan_mode=True)
    captured: list[str] = []

    def fake_plan_turn(prompt: str) -> str:
        captured.append(prompt)
        return "PLANNED"

    monkeypatch.setattr(repl, "run_plan_turn", fake_plan_turn)
    repl.run()
    assert captured == ["plan: add tests"]
    assert "PLANNED" in out.getvalue()


def test_repl_run_plan_turn_persists_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """``run_plan_turn`` persists a Plan to ``$CHIMERA_HOME/.chimera/plans``."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    repl, _ = _repl([])

    # Stub the agent stack so we don't hit a real provider — the plan
    # text is whatever the stub returns, what we want to verify is that
    # the plan is saved and the REPL records it.
    class _StubResult:
        output = "1. write tests\n2. ship it\nApprove? (y/n)"

    def fake_async_run(self, *args, **kwargs):  # pragma: no cover — dispatched async
        return _StubResult()

    # Easier: stub _build_provider + replace asyncio.run inside the
    # method's import path. Cleanest is to monkeypatch the agent
    # stack via a fake Agent that returns the result directly.
    import asyncio

    from chimera.core.agent import Agent

    monkeypatch.setattr(repl, "_build_provider", lambda: object())

    async def fake_run(self, prompt, *, env=None):  # noqa: ARG001
        return _StubResult()

    monkeypatch.setattr(Agent, "async_run", fake_run, raising=False)

    text = repl.run_plan_turn("add tests")
    # Sanity: text contains the agent body + a "[plan saved: ...]" footer.
    assert "1. write tests" in text
    assert "[plan saved:" in text

    plans_dir = tmp_path / ".chimera" / "plans"
    saved = list(plans_dir.glob("plan-*.json"))
    assert len(saved) == 1
    plan = load_plan(saved[0].stem, root=plans_dir)
    assert plan.prompt == "add tests"
    assert "1. write tests" in plan.content
    # The REPL stashes the last plan id on the manager for /resume.
    assert repl.plan_mode.last_plan_id == plan.plan_id

    # Sanity: untouched constants remain in scope.
    assert MODE_AGENT == "agent"
    assert MODE_SHELL == "shell"

    # Drain asyncio bookkeeping so the test doesn't leave open loops.
    asyncio.set_event_loop(asyncio.new_event_loop())
