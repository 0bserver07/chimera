from __future__ import annotations
from dataclasses import dataclass
from chimera.skills.flow_parser import FlowGraph, FlowNode

@dataclass
class FlowExecutionResult:
    completed: bool
    path: list[str]  # Node IDs visited
    output: str

class FlowExecutor:
    """Execute a FlowGraph by walking nodes and delegating actions to an agent."""

    def __init__(self, graph: FlowGraph):
        self._graph = graph
        self._path: list[str] = []

    async def execute(self, run_action, choose_branch) -> FlowExecutionResult:
        """
        Walk the graph from BEGIN to END.

        run_action(node) -> str: execute an action node, return result
        choose_branch(node, options) -> str: at a decision node, pick a branch label
        """
        current = self._graph.get_start()
        if not current:
            return FlowExecutionResult(completed=False, path=[], output="No BEGIN node found")

        outputs = []
        max_steps = 100  # Safety limit

        for _ in range(max_steps):
            self._path.append(current.id)

            if current.node_type == "end":
                return FlowExecutionResult(completed=True, path=list(self._path), output="\n".join(outputs))

            if current.node_type == "action" or current.node_type == "begin":
                if current.node_type == "action":
                    result = await run_action(current)
                    outputs.append(f"[{current.label}] {result}")

                successors = self._graph.get_successors(current.id)
                if not successors:
                    return FlowExecutionResult(completed=False, path=list(self._path), output=f"Dead end at {current.id}")
                current = successors[0][1]  # Follow first edge

            elif current.node_type == "decision":
                successors = self._graph.get_successors(current.id)
                options = {edge.label or f"option_{i}": node for i, (edge, node) in enumerate(successors)}
                choice = await choose_branch(current, list(options.keys()))
                current = options.get(choice, successors[0][1])  # Default to first

            else:
                return FlowExecutionResult(completed=False, path=list(self._path), output=f"Unknown node type: {current.node_type}")

        return FlowExecutionResult(completed=False, path=list(self._path), output="Max steps exceeded")
