"""Tests for the critic module."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.core.context import Context
from chimera.critic.base import Critic, CriticConfig, CriticMode, CriticResult
from chimera.critic.llm_critic import ChecklistCritic, LLMCritic
from chimera.critic.mixin import CriticMixin
from chimera.events.base import EventBus
from chimera.types import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DummyCritic(Critic):
    """Critic that returns a fixed result."""

    def __init__(self, result: CriticResult, config: CriticConfig | None = None):
        super().__init__(config)
        self._result = result
        self.call_count = 0

    def evaluate(self, context, current_action):
        self.call_count += 1
        return self._result


class DummyLoop(CriticMixin):
    """Minimal loop using CriticMixin for testing."""

    def __init__(self, critic=None):
        self.critic = critic
        self._refinement_iteration = 0


def _make_provider(response_text: str) -> MagicMock:
    provider = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    provider.complete.return_value = resp
    return provider


def _make_context() -> Context:
    ctx = Context(system="You are a test agent.")
    ctx.add(Message.user("Do something"))
    ctx.add(Message.assistant("I did something"))
    return ctx


# ---------------------------------------------------------------------------
# CriticConfig / CriticResult
# ---------------------------------------------------------------------------

class TestCriticConfig:
    def test_defaults(self):
        cfg = CriticConfig()
        assert cfg.mode == CriticMode.FINISH_ONLY
        assert cfg.success_threshold == 0.8
        assert cfg.max_refinement_iterations == 3
        assert cfg.critic_model is None

    def test_custom(self):
        cfg = CriticConfig(
            mode=CriticMode.ALL_ACTIONS,
            success_threshold=0.9,
            max_refinement_iterations=5,
            critic_model="claude-haiku-4-5-20251001",
        )
        assert cfg.mode == CriticMode.ALL_ACTIONS
        assert cfg.success_threshold == 0.9
        assert cfg.max_refinement_iterations == 5
        assert cfg.critic_model == "claude-haiku-4-5-20251001"


class TestCriticResult:
    def test_basic(self):
        r = CriticResult(score=0.85, passed=True, feedback="Good")
        assert r.score == 0.85
        assert r.passed is True
        assert r.feedback == "Good"
        assert r.details is None


# ---------------------------------------------------------------------------
# Critic base
# ---------------------------------------------------------------------------

class TestCriticBase:
    def test_default_config(self):
        critic = DummyCritic(CriticResult(score=1.0, passed=True))
        assert critic.config.mode == CriticMode.FINISH_ONLY

    def test_get_followup_prompt(self):
        critic = DummyCritic(CriticResult(score=0.5, passed=False))
        result = CriticResult(score=0.5, passed=False, feedback="Needs more tests")
        prompt = critic.get_followup_prompt(result, 1)
        assert "50.0%" in prompt
        assert "Needs more tests" in prompt
        assert "Iteration 1/3" in prompt


# ---------------------------------------------------------------------------
# LLMCritic
# ---------------------------------------------------------------------------

class TestLLMCritic:
    def test_evaluate_parses_response(self):
        provider = _make_provider(
            "SCORE: 0.9\nPASSED: true\nFEEDBACK: Looks great"
        )
        critic = LLMCritic(provider=provider)
        ctx = _make_context()
        result = critic.evaluate(ctx, Message.assistant("action"))
        assert result.score == 0.9
        assert result.passed is True
        assert result.feedback == "Looks great"

    def test_evaluate_bad_score_defaults(self):
        provider = _make_provider("SCORE: not_a_number\nPASSED: false\nFEEDBACK: Bad")
        critic = LLMCritic(provider=provider)
        ctx = _make_context()
        result = critic.evaluate(ctx, Message.assistant("action"))
        assert result.score == 0.5  # default
        assert result.passed is False

    def test_evaluate_clamps_score(self):
        provider = _make_provider("SCORE: 1.5\nPASSED: true\nFEEDBACK: ok")
        critic = LLMCritic(provider=provider)
        ctx = _make_context()
        result = critic.evaluate(ctx, Message.assistant("action"))
        assert result.score == 1.0

    def test_custom_evaluation_prompt(self):
        provider = _make_provider("SCORE: 0.7\nPASSED: false\nFEEDBACK: meh")
        critic = LLMCritic(
            provider=provider, evaluation_prompt="Custom prompt here"
        )
        ctx = _make_context()
        critic.evaluate(ctx, Message.assistant("action"))
        call_args = provider.complete.call_args
        prompt = call_args[0][0][0]["content"]
        assert "Custom prompt here" in prompt

    def test_critic_model_passed_to_provider(self):
        provider = _make_provider("SCORE: 0.9\nPASSED: true\nFEEDBACK: ok")
        config = CriticConfig(critic_model="cheap-model")
        critic = LLMCritic(provider=provider, config=config)
        ctx = _make_context()
        critic.evaluate(ctx, Message.assistant("action"))
        provider.complete.assert_called_once()
        assert provider.complete.call_args[1]["model"] == "cheap-model"


# ---------------------------------------------------------------------------
# ChecklistCritic
# ---------------------------------------------------------------------------

class TestChecklistCritic:
    def test_evaluate_parses_checklist(self):
        provider = _make_provider(
            "- [x] Has docstrings\n- [ ] No hardcoded creds\n"
            "SCORE: 0.5\nFEEDBACK: Missing credential check"
        )
        critic = ChecklistCritic(
            checklist=["Has docstrings", "No hardcoded creds"],
            provider=provider,
        )
        ctx = _make_context()
        result = critic.evaluate(ctx, Message.assistant("action"))
        assert result.score == 0.5
        assert result.passed is False  # 0.5 < 0.8 threshold
        assert result.feedback is not None and "Missing credential check" in result.feedback

    def test_passes_when_above_threshold(self):
        provider = _make_provider("SCORE: 0.9\nFEEDBACK: All good")
        critic = ChecklistCritic(
            checklist=["item1"], provider=provider,
            config=CriticConfig(success_threshold=0.8),
        )
        ctx = _make_context()
        result = critic.evaluate(ctx, Message.assistant("action"))
        assert result.passed is True


# ---------------------------------------------------------------------------
# CriticMixin
# ---------------------------------------------------------------------------

class TestCriticMixin:
    def test_no_critic_skips_evaluation(self):
        loop = DummyLoop(critic=None)
        ctx = _make_context()
        should_continue, followup = loop._evaluate_and_maybe_refine(
            ctx, Message.assistant("action"),
        )
        assert should_continue is False
        assert followup is None

    def test_passed_resets_iteration(self):
        critic = DummyCritic(CriticResult(score=0.9, passed=True))
        loop = DummyLoop(critic=critic)
        loop._refinement_iteration = 2
        ctx = _make_context()
        should_continue, followup = loop._evaluate_and_maybe_refine(
            ctx, Message.assistant("action"),
        )
        assert should_continue is False
        assert followup is None
        assert loop._refinement_iteration == 0

    def test_failed_triggers_refinement(self):
        critic = DummyCritic(
            CriticResult(score=0.3, passed=False, feedback="Try again"),
        )
        loop = DummyLoop(critic=critic)
        ctx = _make_context()
        should_continue, followup = loop._evaluate_and_maybe_refine(
            ctx, Message.assistant("action"),
        )
        assert should_continue is True
        assert followup is not None
        assert "Try again" in followup
        assert loop._refinement_iteration == 1

    def test_max_iterations_stops_refinement(self):
        critic = DummyCritic(
            CriticResult(score=0.3, passed=False, feedback="Try again"),
            config=CriticConfig(max_refinement_iterations=2),
        )
        loop = DummyLoop(critic=critic)
        loop._refinement_iteration = 2  # already at max
        ctx = _make_context()
        should_continue, followup = loop._evaluate_and_maybe_refine(
            ctx, Message.assistant("action"),
        )
        assert should_continue is False
        assert followup is None
        assert loop._refinement_iteration == 0  # reset

    def test_should_evaluate_all_actions(self):
        critic = DummyCritic(
            CriticResult(score=1.0, passed=True),
            config=CriticConfig(mode=CriticMode.ALL_ACTIONS),
        )
        loop = DummyLoop(critic=critic)
        assert loop._should_evaluate(Message.assistant("text")) is True
        assert loop._should_evaluate(Message.assistant("text", tool_calls=[])) is True

    def test_should_evaluate_finish_only(self):
        critic = DummyCritic(
            CriticResult(score=1.0, passed=True),
            config=CriticConfig(mode=CriticMode.FINISH_ONLY),
        )
        loop = DummyLoop(critic=critic)
        # Final action = no tool_calls
        assert loop._should_evaluate(Message.assistant("text")) is True
        # Non-final = has tool_calls
        from chimera.types import ToolCall
        msg = Message.assistant("text", tool_calls=[
            ToolCall(id="1", name="bash", arguments={}),
        ])
        assert loop._should_evaluate(msg) is False

    def test_should_evaluate_tool_and_finish(self):
        critic = DummyCritic(
            CriticResult(score=1.0, passed=True),
            config=CriticConfig(mode=CriticMode.TOOL_AND_FINISH),
        )
        loop = DummyLoop(critic=critic)
        assert loop._should_evaluate(Message.assistant("text")) is True

    def test_emits_critic_event(self):
        critic = DummyCritic(CriticResult(score=0.9, passed=True))
        loop = DummyLoop(critic=critic)
        bus = EventBus()
        events_received = []
        bus.subscribe("critic", lambda e: events_received.append(e))

        ctx = _make_context()
        loop._evaluate_and_maybe_refine(ctx, Message.assistant("action"), event_bus=bus)
        assert len(events_received) == 1
        assert events_received[0].score == 0.9
        assert events_received[0].passed is True

    def test_is_final_action_no_tool_calls(self):
        loop = DummyLoop()
        assert loop._is_final_action(Message.assistant("text")) is True
        from chimera.types import ToolCall
        msg = Message.assistant("text", tool_calls=[
            ToolCall(id="1", name="bash", arguments={}),
        ])
        assert loop._is_final_action(msg) is False


# ---------------------------------------------------------------------------
# CriticMode enum
# ---------------------------------------------------------------------------

class TestCriticMode:
    def test_string_values(self):
        assert CriticMode.ALL_ACTIONS == "all_actions"
        assert CriticMode.FINISH_ONLY == "finish_only"
        assert CriticMode.TOOL_AND_FINISH == "tool_and_finish"
