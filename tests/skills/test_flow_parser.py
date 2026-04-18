"""Tests for chimera.skills.flow_parser (Issue #126)."""
from __future__ import annotations


from chimera.skills.flow_parser import FlowGraph, FlowNode, MermaidFlowParser


class TestParseSimpleLinearFlow:
    def test_parse_simple_linear_flow(self) -> None:
        """Parse a simple BEGIN -> Action -> END flow."""
        parser = MermaidFlowParser()
        graph = parser.parse(
            """\
graph TD
    start[BEGIN] --> A[Read code]
    A --> end[Done]
"""
        )
        assert "start" in graph.nodes
        assert "A" in graph.nodes
        assert "end" in graph.nodes
        assert graph.nodes["start"].node_type == "begin"
        assert graph.nodes["A"].node_type == "action"
        assert graph.nodes["A"].label == "Read code"
        assert len(graph.edges) == 2


class TestParseDecisionNode:
    def test_parse_decision_node(self) -> None:
        """Decision nodes (curly-brace syntax) are typed as 'decision'."""
        parser = MermaidFlowParser()
        graph = parser.parse(
            """\
graph TD
    start[BEGIN] --> D{Has tests?}
    D -->|yes| A[Run tests]
    D -->|no| B[Write tests]
    A --> end[Done]
    B --> end
"""
        )
        assert graph.nodes["D"].node_type == "decision"
        assert graph.nodes["D"].label == "Has tests?"


class TestParseEdgeWithLabel:
    def test_parse_edge_with_label(self) -> None:
        """Edges with |label| syntax carry their label."""
        parser = MermaidFlowParser()
        graph = parser.parse(
            """\
graph TD
    start[BEGIN] --> D{OK?}
    D -->|yes| end[Done]
    D -->|no| A[Fix]
    A --> end
"""
        )
        labeled_edges = [e for e in graph.edges if e.label is not None]
        labels = {e.label for e in labeled_edges}
        assert "yes" in labels
        assert "no" in labels


class TestGetStartNode:
    def test_get_start_node(self) -> None:
        """get_start() returns the node typed 'begin'."""
        parser = MermaidFlowParser()
        graph = parser.parse(
            """\
graph TD
    start[BEGIN] --> end[Done]
"""
        )
        start = graph.get_start()
        assert start is not None
        assert start.node_type == "begin"
        assert start.id == "start"

    def test_get_start_returns_none_when_missing(self) -> None:
        """get_start() returns None if no begin node exists."""
        graph = FlowGraph(nodes={"A": FlowNode(id="A", label="Action", node_type="action")}, edges=[])
        assert graph.get_start() is None


class TestGetSuccessors:
    def test_get_successors(self) -> None:
        """get_successors returns the edges and nodes reachable from a given node."""
        parser = MermaidFlowParser()
        graph = parser.parse(
            """\
graph TD
    start[BEGIN] --> D{Choose?}
    D -->|left| A[Left path]
    D -->|right| B[Right path]
    A --> end[Done]
    B --> end
"""
        )
        successors = graph.get_successors("D")
        assert len(successors) == 2
        edge_labels = {edge.label for edge, _ in successors}
        assert "left" in edge_labels
        assert "right" in edge_labels

    def test_get_successors_empty(self) -> None:
        """get_successors returns empty list for a terminal node."""
        parser = MermaidFlowParser()
        graph = parser.parse(
            """\
graph TD
    start[BEGIN] --> end[Done]
"""
        )
        assert graph.get_successors("end") == []
