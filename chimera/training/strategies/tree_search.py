# chimera/training/strategies/tree_search.py
"""Tree search strategy for non-linear synthesis."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchNode:
    """A node in the search tree."""

    id: str
    parent_id: str | None
    depth: int
    checkpoint_id: str
    pass_rate: float
    passed: int
    total: int
    cost: float
    agent_output: str
    children: list[str] = field(default_factory=list)

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0
