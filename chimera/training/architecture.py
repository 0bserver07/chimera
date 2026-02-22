from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Layer:
    """A layer in the architecture -- a logical unit of code to synthesize.

    Layers can depend on other layers (by name), be frozen (skip synthesis),
    and carry descriptions/templates that guide the agent.
    """

    name: str
    depends_on: list[str] = field(default_factory=list)
    description: str = ""
    template: str | None = None
    frozen: bool = False
    constraints: list[str] = field(default_factory=list)
    code: str | None = None

    @property
    def level(self) -> str:
        """Determine the abstraction level of this layer.

        Returns one of: 'frozen', 'templated', 'guided', 'abstract'.
        """
        if self.frozen:
            return "frozen"
        if self.template is not None:
            return "templated"
        if self.description or self.constraints:
            return "guided"
        return "abstract"


class Architecture:
    """Ordered collection of Layers with dependency management.

    Provides topological sort via ``build_order()`` so strategies like
    CurriculumStrategy can process layers in dependency order.
    """

    def __init__(self, layers: list[Layer] | None = None) -> None:
        self.layers: list[Layer] = layers or []
        self._by_name: dict[str, Layer] = {l.name: l for l in self.layers}
        self._validate()

    def _validate(self) -> None:
        """Validate dependencies: no unknown layers, no cycles."""
        known = set(self._by_name.keys())
        for layer in self.layers:
            for dep in layer.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"Layer '{layer.name}' depends on unknown layer '{dep}'"
                    )
        # Check for cycles using DFS
        self._check_cycles()

    def _check_cycles(self) -> None:
        """Detect circular dependencies via DFS."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {l.name: WHITE for l in self.layers}

        def dfs(name: str) -> None:
            color[name] = GRAY
            layer = self._by_name[name]
            for dep in layer.depends_on:
                if color[dep] == GRAY:
                    raise ValueError(
                        f"circular dependency detected involving '{dep}'"
                    )
                if color[dep] == WHITE:
                    dfs(dep)
            color[name] = BLACK

        for layer in self.layers:
            if color[layer.name] == WHITE:
                dfs(layer.name)

    def add(self, layer: Layer) -> None:
        self.layers.append(layer)
        self._by_name[layer.name] = layer

    def get(self, name: str) -> Layer:
        return self._by_name[name]

    def get_layer(self, name: str) -> Layer:
        """Get a layer by name, raising KeyError if not found."""
        if name not in self._by_name:
            raise KeyError(f"No layer named '{name}'")
        return self._by_name[name]

    def build_order(self) -> list[Layer]:
        """Return layers in topological (dependency) order.

        Layers with no dependencies come first, then layers whose
        dependencies have already been listed, and so on.
        Uses Kahn's algorithm.
        """
        # Build in-degree map
        in_degree: dict[str, int] = {l.name: 0 for l in self.layers}
        dependents: dict[str, list[str]] = {l.name: [] for l in self.layers}

        for layer in self.layers:
            for dep in layer.depends_on:
                if dep in in_degree:
                    in_degree[layer.name] += 1
                    dependents[dep].append(layer.name)

        # Seed queue with zero-in-degree nodes (stable order)
        queue: list[str] = [n for n in in_degree if in_degree[n] == 0]
        result: list[Layer] = []

        while queue:
            name = queue.pop(0)
            result.append(self._by_name[name])
            for dep_name in dependents[name]:
                in_degree[dep_name] -= 1
                if in_degree[dep_name] == 0:
                    queue.append(dep_name)

        if len(result) != len(self.layers):
            raise ValueError("Cycle detected in layer dependencies")

        return result
