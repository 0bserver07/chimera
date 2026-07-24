"""Unit tests for BudgetSpec / BudgetTally / BudgetEnforcer / BudgetedProvider."""
from __future__ import annotations

from typing import Any

from chimera.core.budget import (
    BudgetedProvider,
    BudgetEnforcer,
    BudgetSpec,
    BudgetTally,
)
from chimera.core.cancellation import CancellationToken
from chimera.providers.base import Provider, Response
from chimera.types import Message


class TestBudgetSpec:
    def test_all_none_never_exhausts(self) -> None:
        spec = BudgetSpec()
        tally = BudgetTally(tool_calls=10_000, llm_calls=10_000, cost_usd=1e6)
        assert spec.is_exhausted(tally) == (False, None)

    def test_tool_calls_exact_bound(self) -> None:
        spec = BudgetSpec(max_tool_calls=3)
        assert spec.is_exhausted(BudgetTally(tool_calls=2))[0] is False
        hit, reason = spec.is_exhausted(BudgetTally(tool_calls=3))
        assert hit is True
        assert reason is not None and reason.startswith("tool_calls")

    def test_llm_calls_bound(self) -> None:
        spec = BudgetSpec(max_llm_calls=1)
        hit, reason = spec.is_exhausted(BudgetTally(llm_calls=1))
        assert hit and reason is not None and reason.startswith("llm_calls")

    def test_cost_bound(self) -> None:
        spec = BudgetSpec(max_cost_usd=0.5)
        assert spec.is_exhausted(BudgetTally(cost_usd=0.4999))[0] is False
        hit, reason = spec.is_exhausted(BudgetTally(cost_usd=0.5))
        assert hit and reason is not None and reason.startswith("cost")

    def test_wall_clock_uses_elapsed(self, monkeypatch) -> None:
        import chimera.core.budget as budget_mod

        clock = {"now": 100.0}
        monkeypatch.setattr(budget_mod.time, "monotonic", lambda: clock["now"])
        spec = BudgetSpec(max_wall_clock_sec=10.0)
        tally = BudgetTally(started_at=100.0)
        assert spec.is_exhausted(tally)[0] is False
        clock["now"] = 110.0
        hit, reason = spec.is_exhausted(tally)
        assert hit and reason is not None and reason.startswith("wall_clock")

    def test_elapsed_is_zero_before_start(self) -> None:
        assert BudgetTally().elapsed_sec == 0.0

    def test_is_set(self) -> None:
        assert BudgetSpec().is_set is False
        assert BudgetSpec(max_cost_usd=0.1).is_set is True
        assert BudgetSpec(max_llm_calls=3).is_set is True

    def test_first_exhausted_returns_dimension_and_reason(self) -> None:
        spec = BudgetSpec(max_cost_usd=0.5)
        assert spec.first_exhausted(BudgetTally(cost_usd=0.1)) is None
        hit = spec.first_exhausted(BudgetTally(cost_usd=0.5))
        assert hit is not None
        dimension, reason = hit
        assert dimension == "cost"
        assert reason.startswith("cost")

    def test_first_exhausted_order_matches_is_exhausted(self) -> None:
        # tool_calls is checked before cost, so it wins when both are over.
        spec = BudgetSpec(max_tool_calls=1, max_cost_usd=0.1)
        tally = BudgetTally(tool_calls=5, cost_usd=1.0)
        assert spec.first_exhausted(tally)[0] == "tool_calls"
        assert spec.is_exhausted(tally)[1].startswith("tool_calls")


class TestBudgetEnforcer:
    def test_records_and_trips_on_nth_tool_call(self) -> None:
        token = CancellationToken()
        enforcer = BudgetEnforcer(BudgetSpec(max_tool_calls=2), cancellation=token)
        enforcer.record_tool_call("read")
        assert not enforcer.exhausted
        assert not token.is_cancelled
        enforcer.record_tool_call("write")
        assert enforcer.exhausted
        assert enforcer.exhausted_reason is not None
        assert token.is_cancelled

    def test_reason_is_first_hit_only(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_tool_calls=1, max_llm_calls=1))
        enforcer.record_tool_call()
        first = enforcer.exhausted_reason
        enforcer.record_llm_call()
        assert enforcer.exhausted_reason == first

    def test_works_without_token(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_llm_calls=1))
        enforcer.record_llm_call(cost=0.25)
        assert enforcer.exhausted
        assert enforcer.tally.cost_usd == 0.25

    def test_start_is_idempotent(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec())
        enforcer.start()
        first = enforcer.tally.started_at
        enforcer.start()
        assert enforcer.tally.started_at == first

    def test_check_trips_wall_clock_without_records(self, monkeypatch) -> None:
        import chimera.core.budget as budget_mod

        clock = {"now": 0.0}
        monkeypatch.setattr(budget_mod.time, "monotonic", lambda: clock["now"])
        token = CancellationToken()
        enforcer = BudgetEnforcer(
            BudgetSpec(max_wall_clock_sec=5.0), cancellation=token
        )
        enforcer.start()
        clock["now"] = 6.0
        enforcer.check()
        assert enforcer.exhausted and token.is_cancelled

    def test_exhausted_dimension_is_set_on_trip(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_cost_usd=0.1))
        assert enforcer.exhausted_dimension is None
        enforcer.record_llm_call(cost=0.2)
        assert enforcer.exhausted_dimension == "cost"

    def test_pause_banks_active_time_and_excludes_idle(self, monkeypatch) -> None:
        # Cumulative active wall-clock: two 3s bursts count, the idle gap between
        # them does not. This is the semantics a multi-turn TUI lane relies on.
        import chimera.core.budget as budget_mod

        clock = {"now": 0.0}
        monkeypatch.setattr(budget_mod.time, "monotonic", lambda: clock["now"])
        enforcer = BudgetEnforcer(BudgetSpec(max_wall_clock_sec=5.0))
        enforcer.start()          # interval 1 opens at t=0
        clock["now"] = 3.0
        enforcer.pause()          # bank 3s; idle begins
        assert not enforcer.exhausted
        clock["now"] = 100.0      # 97s of idle must NOT count
        enforcer.start()          # interval 2 opens at t=100
        clock["now"] = 102.5
        enforcer.check()          # 3 + 2.5 = 5.5s active >= 5s cap
        assert enforcer.exhausted
        assert enforcer.exhausted_dimension == "wall_clock"

    def test_start_after_pause_resumes(self, monkeypatch) -> None:
        import chimera.core.budget as budget_mod

        clock = {"now": 10.0}
        monkeypatch.setattr(budget_mod.time, "monotonic", lambda: clock["now"])
        enforcer = BudgetEnforcer(BudgetSpec())
        enforcer.start()
        clock["now"] = 12.0
        enforcer.pause()
        assert enforcer.tally.accumulated_sec == 2.0
        assert enforcer.tally.started_at is None
        enforcer.start()
        assert enforcer.tally.started_at == 12.0


class _EchoProvider(Provider):
    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: Any | None = None,
        **kwargs: Any,
    ) -> Response:
        return Response(
            content="ok",
            tool_calls=[],
            usage={"input_tokens": 1_000_000, "output_tokens": 0},
        )

    @property
    def context_window(self) -> int:
        return 8192

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "glm-5"


class TestBudgetedProvider:
    def test_records_llm_calls_and_cost(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_llm_calls=2))
        provider = BudgetedProvider(_EchoProvider(), enforcer)
        provider.complete([Message.user("hi")])
        assert enforcer.tally.llm_calls == 1
        assert enforcer.tally.cost_usd > 0  # glm-5 pricing is in the catalog
        assert not enforcer.exhausted
        provider.complete([Message.user("again")])
        assert enforcer.exhausted

    def test_async_complete_records_cost_and_trips(self) -> None:
        # The async gap: assembled agents drive async_complete; without the
        # wrapper max_cost never tripped. 1M input tokens prices well over $0.01.
        import asyncio

        enforcer = BudgetEnforcer(BudgetSpec(max_cost_usd=0.01))
        provider = BudgetedProvider(_EchoProvider(), enforcer)
        asyncio.run(provider.async_complete([Message.user("hi")]))
        assert enforcer.tally.llm_calls == 1
        assert enforcer.tally.cost_usd > 0.01
        assert enforcer.exhausted  # cost cap now trips on the async path

    def test_async_stream_records_one_call(self) -> None:
        import asyncio

        enforcer = BudgetEnforcer(BudgetSpec(max_llm_calls=1))
        provider = BudgetedProvider(_EchoProvider(), enforcer)

        async def _drain() -> None:
            async for _ev in provider.async_stream([Message.user("hi")]):
                pass

        asyncio.run(_drain())
        assert enforcer.tally.llm_calls == 1
        assert enforcer.exhausted

    def test_sync_stream_records_cost_from_done_event(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_llm_calls=1))
        provider = BudgetedProvider(_EchoProvider(), enforcer)
        for _ev in provider.stream([Message.user("hi")]):
            pass
        assert enforcer.tally.llm_calls == 1
        assert enforcer.tally.cost_usd > 0  # done event carried usage

    def test_delegates_properties(self) -> None:
        provider = BudgetedProvider(_EchoProvider(), BudgetEnforcer(BudgetSpec()))
        assert provider.model_name == "glm-5"
        assert provider.context_window == 8192
        assert provider.supports_tool_use is True
