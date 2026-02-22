"""Architecture — the structure of what to synthesize. A DAG of Layers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Layer:
    """A single component in the architecture."""

    name: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    template: str | None = None  # Path to template file
    frozen: bool = False  # If True, don't modify this layer
    constraints: list[str] = field(default_factory=list)  # Extra constraints

    @property
    def level(self) -> str:
        """Prescriptiveness level: abstract, guided, templated, or frozen."""
        if self.frozen:
            return "frozen"
        if self.template:
            return "templated"
        if self.description or self.constraints:
            return "guided"
        return "abstract"


@dataclass
class Architecture:
    """The structure of what to synthesize. A DAG of Layers."""

    layers: list[Layer]

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        names = {layer.name for layer in self.layers}
        for layer in self.layers:
            for dep in layer.depends_on:
                if dep not in names:
                    raise ValueError(
                        f"Layer '{layer.name}' depends on unknown layer '{dep}'"
                    )
        if self._has_cycle():
            raise ValueError("Architecture has circular dependencies")

    def _has_cycle(self) -> bool:
        """Detect cycles using DFS with coloring."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {layer.name: WHITE for layer in self.layers}
        adj: dict[str, list[str]] = {
            layer.name: layer.depends_on for layer in self.layers
        }

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in adj[node]:
                if color[neighbor] == GRAY:
                    return True  # back edge → cycle
                if color[neighbor] == WHITE and dfs(neighbor):
                    return True
            color[node] = BLACK
            return False

        for name in color:
            if color[name] == WHITE:
                if dfs(name):
                    return True
        return False

    def build_order(self) -> list[Layer]:
        """Topological sort -- returns layers in dependency order (Kahn's algorithm)."""
        layer_map = {layer.name: layer for layer in self.layers}
        in_degree: dict[str, int] = {layer.name: 0 for layer in self.layers}
        dependents: dict[str, list[str]] = {layer.name: [] for layer in self.layers}

        for layer in self.layers:
            for dep in layer.depends_on:
                dependents[dep].append(layer.name)
                in_degree[layer.name] += 1

        queue: deque[str] = deque(
            name for name, deg in in_degree.items() if deg == 0
        )
        result: list[Layer] = []

        while queue:
            name = queue.popleft()
            result.append(layer_map[name])
            for dependent in dependents[name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return result

    def get_layer(self, name: str) -> Layer:
        for layer in self.layers:
            if layer.name == name:
                return layer
        raise KeyError(f"No layer named '{name}'")
