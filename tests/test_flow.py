"""Tests for chimera.skills.flow — Mermaid flowchart parsing and execution."""
from __future__ import annotations

import pytest

from chimera.skills.flow import (
    Flow,
    FlowError,
    FlowValidationError,
    parse_choice,
)

SIMPLE_LINEAR = """\
flowchart TD
    A([BEGIN]) --> B[Do work]
    B --> C([END])
"""

DECISION_FLOW = """\
flowchart TD
    A([BEGIN]) --> B{check}
    B -->|yes| C[Action A]
    B -->|no| D[Action B]
    C --> E([END])
    D --> E
"""

LABELED_EDGES_ALT = """\
flowchart LR
    A([BEGIN]) --> B{choose}
    B -- left --> C[Go left]
    B -- right --> D[Go right]
    C --> E([END])
    D --> E
"""

COMMENTS_AND_STYLE = """\
flowchart TD
    %% This is a comment
    A([BEGIN]) --> B[Task]
    classDef default fill:#fff
    style A fill:#f00
    B --> C([END])
"""


def test_parse_simple_linear_flow() -> None:
    flow = Flow.from_mermaid(SIMPLE_LINEAR)
    assert len(flow.nodes) == 3
    assert len(flow.edges) == 2
    assert flow.nodes[flow.begin_id].kind == "begin"
    assert flow.nodes[flow.end_id].kind == "end"


def test_parse_decision_flow() -> None:
    flow = Flow.from_mermaid(DECISION_FLOW)
    assert flow.nodes["B"].kind == "decision"
    nexts = flow.next_nodes("B")
    assert len(nexts) == 2
    labels = {e.label for e, _ in nexts}
    assert labels == {"yes", "no"}


def test_parse_labeled_edges() -> None:
    flow = Flow.from_mermaid(LABELED_EDGES_ALT)
    assert flow.nodes["B"].kind == "decision"
    nexts = flow.next_nodes("B")
    labels = {e.label for e, _ in nexts}
    assert labels == {"left", "right"}


def test_to_prompt_includes_all_nodes() -> None:
    flow = Flow.from_mermaid(DECISION_FLOW)
    prompt = flow.to_prompt()
    for node in flow.nodes.values():
        assert node.label in prompt


def test_to_prompt_with_current_node() -> None:
    flow = Flow.from_mermaid(DECISION_FLOW)
    prompt = flow.to_prompt(current_node_id="B")
    assert "You are currently at:" in prompt
    assert "Available choices:" in prompt


def test_next_nodes() -> None:
    flow = Flow.from_mermaid(SIMPLE_LINEAR)
    nexts = flow.next_nodes(flow.begin_id)
    assert len(nexts) == 1
    assert nexts[0][1].label == "Do work"


def test_advance_linear() -> None:
    flow = Flow.from_mermaid(SIMPLE_LINEAR)
    next_id = flow.advance(flow.begin_id)
    assert next_id == "B"


def test_advance_decision_with_choice() -> None:
    flow = Flow.from_mermaid(DECISION_FLOW)
    next_id = flow.advance("B", choice="yes")
    assert next_id == "C"
    next_id = flow.advance("B", choice="no")
    assert next_id == "D"


def test_advance_decision_wrong_choice() -> None:
    flow = Flow.from_mermaid(DECISION_FLOW)
    with pytest.raises(FlowError, match="Invalid choice"):
        flow.advance("B", choice="maybe")


def test_advance_at_end() -> None:
    flow = Flow.from_mermaid(SIMPLE_LINEAR)
    with pytest.raises(FlowError, match="Already at end"):
        flow.advance(flow.end_id)


def test_parse_choice_tag() -> None:
    assert parse_choice("<choice>yes</choice>") == "yes"
    assert parse_choice("I think <choice>  no  </choice> is best") == "no"


def test_parse_choice_no_tag() -> None:
    assert parse_choice("just text") is None
    assert parse_choice("") is None


def test_validation_no_begin() -> None:
    src = """\
flowchart TD
    A[Task] --> B([END])
"""
    with pytest.raises(FlowValidationError, match="begin"):
        Flow.from_mermaid(src)


def test_validation_no_end() -> None:
    src = """\
flowchart TD
    A([BEGIN]) --> B[Task]
"""
    with pytest.raises(FlowValidationError, match="end"):
        Flow.from_mermaid(src)


def test_comments_and_style_lines_skipped() -> None:
    flow = Flow.from_mermaid(COMMENTS_AND_STYLE)
    assert len(flow.nodes) == 3
    assert len(flow.edges) == 2
    # No nodes created from comment or style lines
    for node in flow.nodes.values():
        assert "comment" not in node.label.lower()
        assert "classDef" not in node.label
