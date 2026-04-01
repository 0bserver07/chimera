"""Tests for chimera.skills.flow_executor (Issue #126)."""
from __future__ import annotations

import asyncio

import pytest

from chimera.skills.flow_executor import FlowExecutionResult, FlowExecutor
from chimera.skills.flow_parser import FlowGraph, FlowNode, MermaidFlowParser


@pytest.fixture
def linear_graph() -> FlowGraph:
    parser = MermaidFlowParser()
    return parser.parse(
        """\
graph TD
    start[BEGIN] --> A[Do something]
    A --> end[Done]
"""
    )


@pytest.fixture
def decision_graph() -> FlowGraph:
    parser = MermaidFlowParser()
    return parser.parse(
        """\
graph TD
    start[BEGIN] --> D{Ready?}
    D -->|yes| A[Deploy]
    D -->|no| B[Prepare]
    A --> end[Done]
    B --> end
"""
    )


class TestExecuteLinearFlow:
    @pytest.mark.asyncio
    async def test_execute_linear_flow(self, linear_graph: FlowGraph) -> None:
        """Walk BEGIN -> Action -> END, calling run_action for the action node."""

        async def run_action(node: FlowNode) -> str:
            return f"completed {node.label}"

        async def choose_branch(node: FlowNode, options: list[str]) -> str:
            return options[0]

        executor = FlowExecutor(linear_graph)
        result = await executor.execute(run_action, choose_branch)
        assert result.completed is True
        assert "start" in result.path
        assert "end" in result.path
        assert "Do something" in result.output


class TestExecuteWithDecision:
    @pytest.mark.asyncio
    async def test_execute_with_decision_yes(self, decision_graph: FlowGraph) -> None:
        """At a decision node, choose_branch selects the 'yes' path."""

        async def run_action(node: FlowNode) -> str:
            return f"ran {node.label}"

        async def choose_branch(node: FlowNode, options: list[str]) -> str:
            return "yes"

        executor = FlowExecutor(decision_graph)
        result = await executor.execute(run_action, choose_branch)
        assert result.completed is True
        assert "D" in result.path
        # Should have gone through Deploy (A), not Prepare (B)
        assert "A" in result.path
        assert "B" not in result.path

    @pytest.mark.asyncio
    async def test_execute_with_decision_no(self, decision_graph: FlowGraph) -> None:
        """At a decision node, choose_branch selects the 'no' path."""

        async def run_action(node: FlowNode) -> str:
            return f"ran {node.label}"

        async def choose_branch(node: FlowNode, options: list[str]) -> str:
            return "no"

        executor = FlowExecutor(decision_graph)
        result = await executor.execute(run_action, choose_branch)
        assert result.completed is True
        assert "B" in result.path
        assert "A" not in result.path


class TestExecuteMaxStepsSafety:
    @pytest.mark.asyncio
    async def test_execute_max_steps_safety(self) -> None:
        """If the graph loops forever the executor bails after max_steps."""
        # Build a graph that loops: start -> A -> A (cycle)
        nodes = {
            "start": FlowNode(id="start", label="BEGIN", node_type="begin"),
            "A": FlowNode(id="A", label="Loop", node_type="action"),
        }
        from chimera.skills.flow_parser import FlowEdge

        edges = [
            FlowEdge(from_id="start", to_id="A"),
            FlowEdge(from_id="A", to_id="A"),  # self-loop
        ]
        graph = FlowGraph(nodes=nodes, edges=edges)

        async def run_action(node: FlowNode) -> str:
            return "looping"

        async def choose_branch(node: FlowNode, options: list[str]) -> str:
            return options[0]

        executor = FlowExecutor(graph)
        result = await executor.execute(run_action, choose_branch)
        assert result.completed is False
        assert "Max steps exceeded" in result.output

    @pytest.mark.asyncio
    async def test_execute_no_start_node(self) -> None:
        """Executor reports failure when no BEGIN node is found."""
        graph = FlowGraph(
            nodes={"A": FlowNode(id="A", label="Action", node_type="action")},
            edges=[],
        )

        async def run_action(node: FlowNode) -> str:
            return ""

        async def choose_branch(node: FlowNode, options: list[str]) -> str:
            return options[0]

        executor = FlowExecutor(graph)
        result = await executor.execute(run_action, choose_branch)
        assert result.completed is False
        assert "No BEGIN node" in result.output
