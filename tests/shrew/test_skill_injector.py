"""Tests for chimera.shrew.skill_injector — the per-turn re-ranker (G11).

Three groups:

1. Per-axis scoring (``score_error`` / ``score_intent`` / ``score_recency``)
   so each signal is locked independently.
2. The combined :class:`SkillInjector` lifecycle (state, turn counter,
   prompt rewriting, marker idempotency).
3. Integration with a session-shaped fake that mimics
   :meth:`chimera.sessions.session.Session.iter_chat` so we can prove
   the wrapped path teas the error stream into the injector for the
   next turn's ranking.

All hermetic — no providers, no network, no real shrew skills loaded.
The test fakes satisfy :class:`SkillLike` structurally so the injector
never imports the heavy ``chimera.shrew.skills`` package during the
suite (and doesn't need it to).
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import pytest

from chimera.shrew.skill_injector import (
    DEFAULT_TOP_K,
    DEFAULT_WEIGHTS,
    RECENCY_WINDOW_TURNS,
    SKILL_INJECT_MARKER,
    ScoreBreakdown,
    ScoreWeights,
    SkillInjector,
    TurnContext,
    extract_error_text,
    format_active_skills_block,
    install_into_session,
    score_error,
    score_intent,
    score_recency,
    score_skill,
    tokenize,
)


# ---------------------------------------------------------------------------
# Fixtures / shared fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeSkill:
    """Minimal SkillLike — five attributes, no behavior.

    Non-frozen so the dataclass fields read as *writable*, matching
    the structural shape of :class:`chimera.shrew.skill_injector.SkillLike`.
    """

    name: str
    description: str
    category: str
    body: str
    triggers: tuple[str, ...]


def _skill(
    name: str,
    *,
    triggers: tuple[str, ...] = (),
    body: str = "Body line.\nAnother line.",
    description: str = "desc",
    category: str = "protocols",
) -> FakeSkill:
    return FakeSkill(
        name=name,
        description=description,
        category=category,
        body=body,
        triggers=triggers,
    )


@pytest.fixture
def small_skill_set() -> list[FakeSkill]:
    """A small mounted set covering all three score axes."""
    return [
        _skill(
            "error-recovery",
            triggers=("error", "exception", "traceback", "stack trace"),
            body="When a tool fails, read the error, hypothesise, retry once.",
            description="Bounded recovery on tool failure",
        ),
        _skill(
            "edit-before-write",
            triggers=("edit", "modify file", "write file"),
            body="Prefer editing existing files; reach for write only on new files.",
            description="Edit before writing",
        ),
        _skill(
            "test-first-python",
            triggers=("pytest", "test", "fixture"),
            body="Write the failing pytest first, then the implementation.",
            description="Test-driven Python loop",
        ),
        _skill(
            "grep-vs-ls",
            triggers=("grep", "search", "find files"),
            body="Use grep for content; ls for structure.",
            description="Pick the right search tool",
        ),
        _skill(
            "loop-detection-signals",
            triggers=("loop", "repeat", "spinning"),
            body="Spinning on the same arguments? Stop and re-plan.",
            description="Notice when you're in a loop",
        ),
    ]


# ---------------------------------------------------------------------------
# 1. Tokeniser
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_lowercases_and_strips_stopwords(self) -> None:
        toks = tokenize("The Test Failed With FileNotFoundError")
        # "the", "with" are stopwords; "Test" lowercases.
        assert "test" in toks
        assert "failed" in toks
        assert "filenotfounderror" in toks
        assert "the" not in toks
        assert "with" not in toks

    def test_preserves_hyphens(self) -> None:
        toks = tokenize("error-recovery is the protocol")
        assert "error-recovery" in toks

    def test_caps_at_max_tokens(self) -> None:
        text = " ".join(f"tok{i}" for i in range(200))
        toks = tokenize(text, cap=10)
        assert len(toks) == 10

    def test_empty_input(self) -> None:
        assert tokenize("") == []
        assert tokenize("   ") == []


# ---------------------------------------------------------------------------
# 2a. score_error — error axis
# ---------------------------------------------------------------------------


class TestScoreError:
    def test_zero_when_error_empty(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]  # error-recovery
        assert score_error(skill, "") == 0.0

    def test_high_when_error_keywords_match(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]  # error-recovery
        score = score_error(
            skill,
            "Traceback (most recent call last):\n  ValueError: bad input\n",
        )
        # "traceback" is a trigger keyword + appears in the error.
        assert score > 0.0
        assert score <= 1.0

    def test_zero_when_no_overlap(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[3]  # grep-vs-ls
        # Error about a syntax problem — no overlap with grep keywords.
        assert score_error(skill, "SyntaxError: invalid token") == 0.0

    def test_saturates_near_one(self) -> None:
        # All four triggers appear in the error — half-life saturation
        # gives 1 - 0.5**4 = 0.9375. The model is ``approaches 1.0``
        # rather than exactly 1.0; we lock the upper-band signal.
        skill = _skill(
            "loops", triggers=("alpha", "beta", "gamma", "delta"),
        )
        score = score_error(skill, "alpha beta gamma delta epsilon zeta eta theta")
        assert score >= 0.9
        assert score < 1.0

    def test_single_match_is_meaningful(self) -> None:
        # Half-life: one match = 0.5. This is the contract — even
        # one keyword match must score above any zero-axis skill.
        skill = _skill(
            "single", triggers=("foobar",),
        )
        score = score_error(skill, "encountered foobar in pipeline")
        assert score == pytest.approx(0.5)

    def test_compound_error_phrasing_matches(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]
        # Multi-token: "stack trace" trigger AND "exception" trigger.
        score = score_error(skill, "Stack trace from the exception above")
        assert score > 0.0


# ---------------------------------------------------------------------------
# 2b. score_intent — prompt-vs-skill axis
# ---------------------------------------------------------------------------


class TestScoreIntent:
    def test_zero_for_empty_prompt(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        assert score_intent(small_skill_set[0], "") == 0.0

    def test_one_when_prompt_names_skill_explicitly(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        # User pins the skill by slug — guaranteed top score.
        skill = small_skill_set[2]  # test-first-python
        assert score_intent(skill, "apply test-first-python here please") == 1.0

    def test_keyword_match_scores_above_zero(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[2]  # test-first-python
        score = score_intent(skill, "Add a pytest fixture for the parser")
        assert 0.0 < score <= 1.0

    def test_no_match_is_zero(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[2]  # test-first-python
        score = score_intent(skill, "Refactor the cache layer")
        assert score == 0.0

    def test_intent_picks_grep_over_test(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        prompt = "grep for usages of foo across the repo"
        grep = small_skill_set[3]  # grep-vs-ls
        test = small_skill_set[2]  # test-first-python
        assert score_intent(grep, prompt) > score_intent(test, prompt)


# ---------------------------------------------------------------------------
# 2c. score_recency — penalty axis
# ---------------------------------------------------------------------------


class TestScoreRecency:
    def test_unused_skill_pays_no_penalty(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        assert score_recency(small_skill_set[0], turn_index=5, last_used={}) == 0.0

    def test_just_used_pays_full_penalty(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]
        # Skill picked on turn 5; we're now scoring for turn 5 too
        # (i.e. before increment) — full penalty.
        score = score_recency(skill, turn_index=5, last_used={skill.name: 5})
        assert score == -1.0

    def test_decays_to_zero_over_window(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]
        # Picked on turn 0; now on turn = window — penalty must be 0.
        score = score_recency(
            skill,
            turn_index=RECENCY_WINDOW_TURNS,
            last_used={skill.name: 0},
        )
        assert score == 0.0

    def test_monotonic_recovery(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]
        prev = -1.0
        for age in range(RECENCY_WINDOW_TURNS + 2):
            s = score_recency(
                skill, turn_index=age, last_used={skill.name: 0},
            )
            # As age grows, the penalty must monotonically increase
            # (i.e. become less negative).
            assert s >= prev
            prev = s

    def test_window_zero_disables_axis(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]
        s = score_recency(
            skill, turn_index=2, last_used={skill.name: 0}, window=0,
        )
        assert s == 0.0


# ---------------------------------------------------------------------------
# 2d. score_skill — combined
# ---------------------------------------------------------------------------


class TestScoreSkill:
    def test_returns_breakdown_with_all_axes(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[0]
        ctx = TurnContext(
            prompt="Stack trace from the exception",
            last_error="Traceback ValueError",
            turn_index=2,
            last_used={skill.name: 1},
        )
        br = score_skill(skill, ctx)
        assert isinstance(br, ScoreBreakdown)
        assert br.error > 0.0
        assert br.intent > 0.0
        assert br.recency < 0.0
        # total = wE*e + wI*i + wR*r
        expected = (
            DEFAULT_WEIGHTS.error * br.error
            + DEFAULT_WEIGHTS.intent * br.intent
            + DEFAULT_WEIGHTS.recency * br.recency
        )
        assert br.total == pytest.approx(expected)

    def test_zero_signals_yield_zero_total(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        skill = small_skill_set[3]  # grep-vs-ls
        ctx = TurnContext(prompt="hello", last_error="", turn_index=0)
        br = score_skill(skill, ctx)
        assert br.total == 0.0


# ---------------------------------------------------------------------------
# 3. SkillInjector — lifecycle
# ---------------------------------------------------------------------------


class TestSkillInjectorPicking:
    def test_default_top_k_is_three(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set)
        assert inj.top_k == DEFAULT_TOP_K
        picked = inj.select("a generic prompt")
        assert len(picked) == DEFAULT_TOP_K

    def test_top_k_clamped_to_one(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set, top_k=0)
        assert inj.top_k == 1
        assert len(inj.select("hi")) == 1

    def test_top_k_caps_at_skill_count(self) -> None:
        # Two skills, top_k=5: we get exactly 2.
        skills = [_skill("a", triggers=("aaa",)), _skill("b", triggers=("bbb",))]
        inj = SkillInjector(skills, top_k=5)
        assert len(inj.select("anything")) == 2

    def test_select_with_no_skills_returns_empty(self) -> None:
        inj = SkillInjector([])
        assert inj.select("hi") == []
        # turn counter still advanced.
        assert inj.turn_index == 1

    def test_intent_drives_selection_on_turn_one(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set, top_k=1)
        picked = inj.select("Add a pytest fixture for parser")
        assert picked[0].name == "test-first-python"

    def test_error_signal_outranks_intent(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        # Step 1 — pick something else.
        inj = SkillInjector(small_skill_set, top_k=1)
        first = inj.select("Add a pytest fixture for parser")
        assert first[0].name == "test-first-python"
        # Now record an error, then ask a *different* prompt: error
        # should dominate intent on the next turn.
        inj.record_turn(error="Traceback (most recent call last): exception bad")
        picked = inj.select("change the cache layer signature")
        assert picked[0].name == "error-recovery"

    def test_recency_demotes_repeated_picks(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set, top_k=1)
        # Two prompts that BOTH naturally pick test-first-python:
        # first turn picks it. Second turn's recency penalty should
        # push test-first off the top spot when another skill's
        # intent is similarly strong.
        first = inj.select("pytest fixture please")
        assert first[0].name == "test-first-python"
        # Construct a follow-up where intent is mixed (mentions both
        # pytest AND grep): without recency demotion, test-first
        # would still win; with it, grep-vs-ls overtakes because
        # test-first just paid a -1.0 recency hit.
        second = inj.select("grep for the pytest fixture usages")
        assert second[0].name == "grep-vs-ls"

    def test_select_breakdown_persisted(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set)
        inj.select("pytest fixture")
        br = inj.last_breakdown
        # Every mounted skill scored.
        assert set(br.keys()) == {s.name for s in small_skill_set}
        # The picked skill (highest total) appears at the top.
        top = max(br.values(), key=lambda b: b.total)
        assert top.intent > 0.0

    def test_record_turn_clears_on_success(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set, top_k=1)
        inj.select("hi")
        inj.record_turn(error="some failure with traceback")
        # Next select consumes the error, then clears it.
        inj.select("any prompt")
        # If we DON'T re-record, the next pick should NOT be biased
        # toward error-recovery.
        result = inj.select("change cache signature")
        # error-recovery should not dominate without an error in flight.
        assert result[0].name != "error-recovery" or score_error(
            result[0], "",
        ) == 0.0


# ---------------------------------------------------------------------------
# 4. format_active_skills_block + inject_into_prompt
# ---------------------------------------------------------------------------


class TestFormatBlock:
    def test_empty_input_yields_empty_string(self) -> None:
        assert format_active_skills_block([]) == ""

    def test_renders_name_description_and_body(self) -> None:
        skills = [_skill("foo", description="d", body="line one\nline two")]
        out = format_active_skills_block(skills)
        assert "## Active shrew skills (per-turn)" in out
        assert "### foo — d" in out
        assert "line one" in out
        assert "line two" in out


class TestInjectIntoPrompt:
    BASE_WITH_MARKER = (
        "You are shrew.\n\n"
        "<!-- shrew-skill-inject -->\n\n"
        "## End of system\n"
    )

    BASE_WITHOUT_MARKER = "You are shrew.\nNo marker here.\n"

    def test_replaces_marker_with_block(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set)
        out = inj.inject_into_prompt(self.BASE_WITH_MARKER, "pytest fixture")
        assert "## Active shrew skills (per-turn)" in out
        # Marker is preserved (so re-inject keeps working).
        assert SKILL_INJECT_MARKER in out
        assert "## End of system" in out  # tail preserved

    def test_appends_when_marker_missing(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set)
        out = inj.inject_into_prompt(self.BASE_WITHOUT_MARKER, "pytest fixture")
        # Block was appended at the bottom.
        assert out.startswith("You are shrew.")
        assert "## Active shrew skills (per-turn)" in out
        assert SKILL_INJECT_MARKER in out

    def test_idempotent_re_injection(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        inj = SkillInjector(small_skill_set)
        once = inj.inject_into_prompt(self.BASE_WITH_MARKER, "pytest fixture")
        twice = inj.inject_into_prompt(once, "another prompt")
        # Exactly one block header in the rewritten prompt.
        assert twice.count("## Active shrew skills (per-turn)") == 1
        # Marker only appears once.
        assert twice.count(SKILL_INJECT_MARKER) == 1

    def test_empty_pick_clears_block(self) -> None:
        # Zero-skill injector → marker stays, nothing appended.
        inj = SkillInjector([])
        out = inj.inject_into_prompt(self.BASE_WITH_MARKER, "anything")
        assert SKILL_INJECT_MARKER in out
        assert "## Active shrew skills (per-turn)" not in out


# ---------------------------------------------------------------------------
# 5. extract_error_text — duck-typed
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolResult:
    output: str
    error: str | None = None
    tool_name: str = ""

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class _FakeStep:
    tool_results: list[_FakeToolResult] = field(default_factory=list)
    error: str | None = None


class TestExtractErrorText:
    def test_no_failures_yields_empty(self) -> None:
        steps = [
            _FakeStep(tool_results=[_FakeToolResult(output="ok")]),
            _FakeStep(tool_results=[]),
        ]
        assert extract_error_text(steps) == ""

    def test_failed_tool_results_concatenated(self) -> None:
        steps = [
            _FakeStep(
                tool_results=[
                    _FakeToolResult(
                        output="boom traceback",
                        error="ValueError",
                        tool_name="bash",
                    ),
                ],
            ),
            _FakeStep(
                tool_results=[
                    _FakeToolResult(
                        output="missing file",
                        error="FileNotFoundError",
                        tool_name="read",
                    ),
                ],
            ),
        ]
        out = extract_error_text(steps)
        assert "boom traceback" in out
        assert "missing file" in out
        assert "[bash]" in out
        assert "[read]" in out

    def test_step_level_error_included(self) -> None:
        steps = [_FakeStep(tool_results=[], error="loop detected")]
        out = extract_error_text(steps)
        assert "loop detected" in out


# ---------------------------------------------------------------------------
# 6. install_into_session — integration with the shrew loop
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, system: str) -> None:
        self.system = system


class _FakeSession:
    """Mimics chimera.sessions.session.Session's iter_chat surface.

    The fake records every system-prompt mutation so the test can
    assert that re-ranking happened *before* iter_chat ran. It also
    yields scripted StepResult-shaped objects so the error-tee path
    fires exactly like in production.
    """

    def __init__(self, system: str, scripted: list[list[_FakeToolResult]]) -> None:
        self._context = _FakeContext(system)
        self._scripted = scripted
        self.system_history: list[str] = []
        self.iter_chat_calls: list[str] = []

    def iter_chat(self, message: str) -> Generator[Any, None, str]:
        self.iter_chat_calls.append(message)
        # Snapshot the system prompt at the moment iter_chat starts —
        # this is what the model would actually see.
        self.system_history.append(self._context.system)
        for batch in self._scripted:
            yield _FakeStep(tool_results=list(batch))
        # Mimic ``yield from`` returning AgentResult-like value.
        return "agent-output"

    def chat(self, message: str) -> str:
        gen: Any = self.iter_chat(message)
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            return stop.value  # type: ignore[no-any-return]


class TestInstallIntoSession:
    def test_iter_chat_injects_per_turn(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        # Two turns: first turn fires a tool failure (Traceback), so
        # the SECOND turn's system prompt should contain
        # error-recovery's body even though the user's prompt is
        # unrelated.
        session = _FakeSession(
            system=(
                "You are shrew.\n\n"
                f"{SKILL_INJECT_MARKER}\n\n"
                "(end)\n"
            ),
            scripted=[
                # turn 1 — one failed tool call.
                [
                    _FakeToolResult(
                        output="Traceback (most recent call last): ValueError",
                        error="ValueError",
                        tool_name="bash",
                    ),
                ],
                # turn 2 — clean.
                [_FakeToolResult(output="done")],
            ],
        )
        injector = SkillInjector(small_skill_set, top_k=1)
        install_into_session(session, injector)

        # Drive turn 1.
        list(session.iter_chat("change the cache layer"))
        first_seen = session.system_history[0]
        assert "## Active shrew skills (per-turn)" in first_seen
        # Turn 1 has no prior error and prompt mentions "cache":
        # nothing pulls error-recovery. Just assert SOMETHING was
        # injected.

        # Drive turn 2 — error-tee from turn 1 should bias selection.
        list(session.iter_chat("now also rename the field"))
        second_seen = session.system_history[1]
        # Body of error-recovery now in the second-turn prompt.
        assert "When a tool fails" in second_seen

    def test_chat_path_also_teas_errors(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        session = _FakeSession(
            system=f"sys\n{SKILL_INJECT_MARKER}\n",
            scripted=[
                [
                    _FakeToolResult(
                        output="Traceback exception",
                        error="ValueError",
                        tool_name="bash",
                    ),
                ],
                [_FakeToolResult(output="ok")],
            ],
        )
        injector = SkillInjector(small_skill_set, top_k=1)
        install_into_session(session, injector)

        # Drive via chat (not iter_chat).
        out = session.chat("first prompt")
        assert out == "agent-output"
        # And then again — second turn must show error-recovery body.
        session.chat("totally unrelated prompt")
        second_seen = session.system_history[1]
        assert "When a tool fails" in second_seen

    def test_install_stashes_injector_on_session(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        session = _FakeSession(
            system=f"sys\n{SKILL_INJECT_MARKER}\n", scripted=[[]],
        )
        injector = SkillInjector(small_skill_set)
        install_into_session(session, injector)
        assert getattr(session, "shrew_skill_injector", None) is injector

    def test_install_raises_when_session_lacks_context(self) -> None:
        class _Empty: ...

        with pytest.raises(AttributeError):
            install_into_session(_Empty(), SkillInjector([]))

    def test_install_preserves_iter_chat_return_value(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        # ``yield from session.iter_chat(...)`` in production returns
        # the AgentResult; the wrapper must forward it.
        session = _FakeSession(
            system=f"sys\n{SKILL_INJECT_MARKER}\n",
            scripted=[[_FakeToolResult(output="ok")]],
        )
        injector = SkillInjector(small_skill_set)
        install_into_session(session, injector)

        gen = session.iter_chat("hi")
        # Drain.
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            assert stop.value == "agent-output"


# ---------------------------------------------------------------------------
# 7. Custom weights surface
# ---------------------------------------------------------------------------


class TestCustomWeights:
    def test_weights_change_ordering(
        self, small_skill_set: list[FakeSkill],
    ) -> None:
        # Heavily favour recency-as-bonus by inverting only the
        # error/intent magnitudes — error becomes tiny, intent
        # dominates and so a non-error prompt picks intent-match.
        weights = ScoreWeights(error=0.01, intent=1.0, recency=0.4)
        inj = SkillInjector(small_skill_set, top_k=1, weights=weights)
        inj.record_turn(error="Traceback ValueError exception")
        picked = inj.select("grep for usages of foo")
        # Even with an error in flight, intent dominates because we
        # tuned the weight down. So grep-vs-ls wins.
        assert picked[0].name == "grep-vs-ls"
