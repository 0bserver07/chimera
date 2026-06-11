"""End-to-end: a real agent run emits a valid ATIF v1.7 trajectory."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.atif import ATIFEmitter, ATIFReader, ATIFValidator
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.events.base import EventBus
from chimera.permissions.presets import AutoApprove
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall, ToolResult


class PingTool(BaseTool):
    name = "ping"
    description = "Returns pong."
    parameters = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        return ToolResult(output="pong")


class TwoToolTurnsProvider(Provider):
    """Scripted: two tool-calling turns, then a final plain answer."""

    def __init__(self) -> None:
        self.calls = 0

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
        self.calls += 1
        if self.calls <= 2:
            return Response(
                content=f"pinging round {self.calls}",
                tool_calls=[ToolCall(id=f"c{self.calls}", name="ping", arguments={})],
                usage={"input_tokens": 100 * self.calls, "output_tokens": 10},
            )
        return Response(
            content="FINAL: everything pings.",
            tool_calls=[],
            usage={"input_tokens": 300, "output_tokens": 6},
        )

    @property
    def context_window(self) -> int:
        return 8192

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "scripted-two-turns"


def test_real_react_run_emits_valid_trajectory(tmp_path: Path) -> None:
    bus = EventBus()
    out = tmp_path / "react.atif.json"
    emitter = ATIFEmitter(
        out, agent_name="chimera-react", model_name="scripted-two-turns",
        session_id="integration-1",
    )
    emitter.attach(bus)
    emitter.record_user_message("Ping until it works.")

    config = LoopConfig(event_bus=bus, permissions=AutoApprove())
    agent = Agent(
        provider=TwoToolTurnsProvider(),
        tools=[PingTool()],
        loop=ReAct(max_steps=10, config=config),
    )
    result = agent.run("Ping until it works.", None)
    path = emitter.close()

    assert result.success
    validation = ATIFValidator().check(json.loads(path.read_text(encoding="utf-8")))
    assert validation.valid, validation.errors

    traj = ATIFReader().load(path)
    agent_steps = [s for s in traj["steps"] if s["source"] == "agent"]
    # one step per API turn: two tool turns + the final answer
    assert len(agent_steps) == 3
    # no fabricated assistant text: messages are exactly the provider's output
    assert agent_steps[0]["message"] == "pinging round 1"
    assert agent_steps[2]["message"] == "FINAL: everything pings."
    assert agent_steps[0]["tool_calls"][0]["function_name"] == "ping"
    assert agent_steps[0]["observation"]["results"][0]["content"] == "pong"
    assert traj["final_metrics"]["extra"]["peak_context_tokens"] == 300


def test_run_with_budget_emits_per_task_trajectories(tmp_path: Path) -> None:
    from chimera.core.budget import BudgetSpec
    from chimera.eval.comparative import ComparativeEval

    problems = [
        {"id": "t1", "prompt": "Ping once.", "expected": "FINAL"},
        {"id": "t2", "prompt": "Ping twice.", "expected": "FINAL"},
    ]
    comp = ComparativeEval(TwoToolTurnsProvider(), problems)

    def factory(provider: Any, loop_config: Any) -> Agent:
        return Agent(
            provider=provider,
            tools=[PingTool()],
            loop=ReAct(max_steps=10, config=loop_config),
        )

    comp.add_config("react", factory)
    report = comp.run_with_budget(
        BudgetSpec(max_tool_calls=10),
        model="scripted-two-turns",
        task_pool="unit:atif",
        atif_dir=str(tmp_path / "trajectories"),
    )

    paths = report.trajectory_paths["react"]
    assert len(paths) == 2
    reader = ATIFReader()
    for p in paths:
        assert Path(p).is_file()
        traj = reader.load(p)  # validates
        assert traj["steps"][0]["source"] == "user"
        assert traj["agent"]["name"] == "chimera-react"
    assert {Path(p).name for p in paths} == {"t1.atif.json", "t2.atif.json"}
