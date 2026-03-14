"""Parse Mermaid flowcharts into executable decision trees for agent prompts."""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Literal

FlowNodeKind = Literal["begin", "end", "task", "decision"]

_SKIP_PREFIXES = (
    "flowchart", "graph", "classdef", "style", "linkstyle",
    "class ", "click", "subgraph", "direction", "end",
)

_CHOICE_TAG = re.compile(r"<choice>(.*?)</choice>", re.IGNORECASE | re.DOTALL)

# Edge arrow patterns (split a line into left, label, right)
_EDGE_PIPE = re.compile(r"-->\|(.+?)\|")   # -->|label|
_EDGE_DASH = re.compile(r"--\s*(.+?)\s*-->")  # -- label -->
_EDGE_PLAIN = re.compile(r"-->")            # -->

# Node shape patterns (applied to a token like "A([BEGIN])" or "B{check}")
_SHAPES: list[tuple[re.Pattern[str], FlowNodeKind]] = [
    (re.compile(r"^(\w+)\(\[(.+?)\]\)$"), "task"),    # A([label]) stadium
    (re.compile(r"^(\w+)\{(.+?)\}$"), "decision"),     # A{label} rhombus
    (re.compile(r"^(\w+)\((.+?)\)$"), "task"),          # A(label) rounded
    (re.compile(r"^(\w+)\[(.+?)\]$"), "task"),          # A[label] rectangle
]


class FlowError(ValueError):
    """Base error for flow parsing/validation."""


class FlowParseError(FlowError):
    """Raised when flow parsing fails."""


class FlowValidationError(FlowError):
    """Raised when a flowchart fails validation."""


@dataclass(frozen=True)
class FlowNode:
    id: str
    label: str
    kind: FlowNodeKind


@dataclass(frozen=True)
class FlowEdge:
    source: str
    target: str
    label: str | None = None


@dataclass
class Flow:
    nodes: dict[str, FlowNode]
    edges: list[FlowEdge]
    begin_id: str
    end_id: str

    @classmethod
    def from_mermaid(cls, text: str) -> Flow:
        """Parse a Mermaid flowchart string into a Flow."""
        nodes: dict[str, FlowNode] = {}
        edges: list[FlowEdge] = []

        def register(nid: str, label: str, kind: FlowNodeKind) -> None:
            lower = label.lower().strip()
            if lower == "begin":
                kind = "begin"
            elif lower == "end":
                kind = "end"
            nodes[nid] = FlowNode(id=nid, label=label.strip(), kind=kind)

        def ensure(nid: str) -> None:
            if nid not in nodes:
                register(nid, nid, "task")

        def parse_token(token: str) -> str:
            """Parse a node token, register it, return its ID."""
            token = token.strip()
            for pattern, kind in _SHAPES:
                m = pattern.match(token)
                if m:
                    register(m.group(1), m.group(2), kind)
                    return m.group(1)
            # Bare identifier
            if re.match(r"^\w+$", token):
                ensure(token)
                return token
            raise FlowParseError(f"Cannot parse node token: {token!r}")

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("%%"):
                continue
            if line.lower().startswith(_SKIP_PREFIXES):
                continue

            # Check if this line contains an edge
            if "-->" not in line and "-- " not in line:
                # Standalone node definition
                try:
                    parse_token(line)
                except FlowParseError:
                    pass
                continue

            # Extract edge label and split into left/right
            edge_label: str | None = None

            m = _EDGE_PIPE.search(line)
            if m:
                edge_label = m.group(1).strip()
                left = line[:m.start()].strip()
                right = line[m.end():].strip()
            else:
                m = _EDGE_DASH.search(line)
                if m:
                    edge_label = m.group(1).strip()
                    left = line[:m.start()].strip()
                    right = line[m.end():].strip()
                else:
                    m = _EDGE_PLAIN.search(line)
                    if m:
                        left = line[:m.start()].strip()
                        right = line[m.end():].strip()
                    else:
                        continue

            if not left or not right:
                continue

            try:
                src_id = parse_token(left)
                tgt_id = parse_token(right)
            except FlowParseError:
                continue

            edges.append(FlowEdge(source=src_id, target=tgt_id, label=edge_label))

        # Auto-detect decision nodes (>1 outgoing edge)
        outgoing: dict[str, int] = {}
        for e in edges:
            outgoing[e.source] = outgoing.get(e.source, 0) + 1

        for nid, count in outgoing.items():
            if count > 1 and nid in nodes:
                n = nodes[nid]
                if n.kind == "task":
                    nodes[nid] = FlowNode(id=nid, label=n.label, kind="decision")

        # Validate
        begins = [n for n in nodes.values() if n.kind == "begin"]
        ends = [n for n in nodes.values() if n.kind == "end"]

        if len(begins) != 1:
            raise FlowValidationError(f"Expected exactly 1 begin node, found {len(begins)}")
        if len(ends) != 1:
            raise FlowValidationError(f"Expected exactly 1 end node, found {len(ends)}")

        begin_id = begins[0].id
        end_id = ends[0].id

        # Reachability check
        reachable: set[str] = set()
        queue: deque[str] = deque([begin_id])
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e.source, []).append(e.target)

        while queue:
            cur = queue.popleft()
            if cur in reachable:
                continue
            reachable.add(cur)
            for nxt in adj.get(cur, []):
                if nxt not in reachable:
                    queue.append(nxt)

        if end_id not in reachable:
            raise FlowValidationError("End node is not reachable from begin node")

        # Multi-edge label validation
        for nid, count in outgoing.items():
            if count > 1:
                labels = [e.label for e in edges if e.source == nid]
                non_empty = [lb for lb in labels if lb]
                if len(non_empty) != count:
                    raise FlowValidationError(
                        f"Node '{nid}' has {count} outgoing edges but not all have labels"
                    )
                if len(set(lb.lower() for lb in non_empty)) != len(non_empty):
                    raise FlowValidationError(f"Node '{nid}' has duplicate edge labels")

        return cls(nodes=nodes, edges=edges, begin_id=begin_id, end_id=end_id)

    def to_prompt(self, current_node_id: str | None = None) -> str:
        """Convert the flow into an agent prompt."""
        lines = ["You are following this workflow:", "", "Steps:"]

        ordered = self._bfs_order()
        for i, nid in enumerate(ordered, 1):
            node = self.nodes[nid]
            tag = node.kind.upper()
            line = f"{i}. [{tag}] {node.label}"

            if node.kind == "decision":
                nexts = self.next_nodes(nid)
                for edge, target in nexts:
                    label = edge.label or "?"
                    line += f"\n   - {label} \u2192 {target.label}"

            lines.append(line)

        if current_node_id and current_node_id in self.nodes:
            node = self.nodes[current_node_id]
            tag = node.kind.upper()
            lines.append("")
            lines.append(f"You are currently at: [{tag}] {node.label}")

            nexts = self.next_nodes(current_node_id)
            if len(nexts) > 1:
                choices = ", ".join(e.label or "?" for e, _ in nexts)
                lines.append(f"Available choices: {choices}")
                lines.append("Respond with your choice in <choice>...</choice> tags.")

        return "\n".join(lines)

    def next_nodes(self, node_id: str) -> list[tuple[FlowEdge, FlowNode]]:
        """Return outgoing edges and their target nodes from node_id."""
        return [(e, self.nodes[e.target]) for e in self.edges if e.source == node_id]

    def advance(self, current_id: str, choice: str | None = None) -> str:
        """Advance from current node. For decisions, choice must match an edge label."""
        nexts = self.next_nodes(current_id)

        if not nexts:
            raise FlowError("Already at end")

        if len(nexts) == 1:
            return nexts[0][1].id

        if choice is None:
            labels = [e.label or "?" for e, _ in nexts]
            raise FlowError(f"Decision node requires a choice from: {', '.join(labels)}")

        for edge, target in nexts:
            if edge.label and edge.label.lower() == choice.lower():
                return target.id

        labels = [e.label or "?" for e, _ in nexts]
        raise FlowError(f"Invalid choice '{choice}'. Available: {', '.join(labels)}")

    def _bfs_order(self) -> list[str]:
        """Return node IDs in BFS order from begin."""
        visited: set[str] = set()
        order: list[str] = []
        queue: deque[str] = deque([self.begin_id])
        adj: dict[str, list[str]] = {}
        for e in self.edges:
            adj.setdefault(e.source, []).append(e.target)

        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            order.append(cur)
            for nxt in adj.get(cur, []):
                if nxt not in visited:
                    queue.append(nxt)

        for nid in self.nodes:
            if nid not in visited:
                order.append(nid)

        return order


def parse_choice(text: str) -> str | None:
    """Extract a choice from ``<choice>...</choice>`` tags."""
    m = _CHOICE_TAG.search(text)
    return m.group(1).strip() if m else None
