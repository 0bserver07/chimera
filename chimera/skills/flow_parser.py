from __future__ import annotations
import re
from dataclasses import dataclass, field

@dataclass
class FlowNode:
    id: str
    label: str
    node_type: str  # "action", "decision", "begin", "end"

@dataclass
class FlowEdge:
    from_id: str
    to_id: str
    label: str | None = None

@dataclass
class FlowGraph:
    nodes: dict[str, FlowNode]
    edges: list[FlowEdge]

    def get_start(self) -> FlowNode | None:
        for node in self.nodes.values():
            if node.node_type == "begin":
                return node
        return None

    def get_successors(self, node_id: str) -> list[tuple[FlowEdge, FlowNode]]:
        result = []
        for edge in self.edges:
            if edge.from_id == node_id:
                target = self.nodes.get(edge.to_id)
                if target:
                    result.append((edge, target))
        return result

class MermaidFlowParser:
    """Parse Mermaid flowchart syntax into a FlowGraph."""

    # Regex for a "node token": either ID[label], ID{label}, or bare ID
    _NODE_SQUARE = re.compile(r'(\w+)\[([^\]]*)\]')
    _NODE_CURLY = re.compile(r'(\w+)\{([^}]*)\}')
    _NODE_BARE = re.compile(r'(\w+)')

    def _parse_node_token(self, token: str, nodes: dict[str, FlowNode]) -> str:
        """Parse a node token, register the node, return its ID."""
        token = token.strip()

        # Decision node: ID{label}
        m = self._NODE_CURLY.match(token)
        if m and m.end() == len(token):
            nid, label = m.group(1), m.group(2)
            nodes[nid] = FlowNode(id=nid, label=label, node_type="decision")
            return nid

        # Square bracket node: ID[label]
        m = self._NODE_SQUARE.match(token)
        if m and m.end() == len(token):
            nid, label = m.group(1), m.group(2)
            nodes[nid] = self._classify_node(nid, label)
            return nid

        # Bare identifier
        m = self._NODE_BARE.match(token)
        if m and m.end() == len(token):
            nid = m.group(1)
            if nid not in nodes:
                nodes[nid] = self._classify_node(nid, nid)
            return nid

        raise ValueError(f"Cannot parse node token: {token!r}")

    def parse(self, mermaid_text: str) -> FlowGraph:
        nodes: dict[str, FlowNode] = {}
        edges: list[FlowEdge] = []

        # General edge pattern: <source_token> -->|optional label| <target_token>
        # We split on the arrow to handle arbitrary node shapes on both sides.
        _EDGE_RE = re.compile(r'^(.+?)\s*-->(?:\|([^|]*)\|)?\s*(.+)$')

        for line in mermaid_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("graph") or line.startswith("%%"):
                continue

            # Try to parse as an edge
            edge_match = _EDGE_RE.match(line)
            if edge_match:
                left = edge_match.group(1).strip()
                edge_label = edge_match.group(2)
                right = edge_match.group(3).strip()

                try:
                    from_id = self._parse_node_token(left, nodes)
                    to_id = self._parse_node_token(right, nodes)
                    edges.append(FlowEdge(from_id=from_id, to_id=to_id, label=edge_label))
                    continue
                except ValueError:
                    pass

            # Parse standalone node definitions: A[Label]
            node_match = re.match(r'(\w+)\[([^\]]*)\]$', line)
            if node_match:
                nid, label = node_match.group(1), node_match.group(2)
                nodes[nid] = self._classify_node(nid, label)
                continue

            # Parse standalone decision node definitions: A{Decision?}
            decision_match = re.match(r'(\w+)\{([^}]*)\}$', line)
            if decision_match:
                nid, label = decision_match.group(1), decision_match.group(2)
                nodes[nid] = FlowNode(id=nid, label=label, node_type="decision")
                continue

        return FlowGraph(nodes=nodes, edges=edges)

    def _classify_node(self, nid: str, label: str) -> FlowNode:
        nid_lower = nid.lower()
        label_lower = label.lower()
        if nid_lower in ("begin", "start"):
            return FlowNode(id=nid, label=label, node_type="begin")
        if nid_lower in ("end", "done", "finish"):
            return FlowNode(id=nid, label=label, node_type="end")
        if "?" in label:
            return FlowNode(id=nid, label=label, node_type="decision")
        return FlowNode(id=nid, label=label, node_type="action")
