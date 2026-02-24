# chimera/training/strategies/tree_search.py
"""Tree search strategy for non-linear synthesis."""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from chimera.env.local import LocalEnvironment
from chimera.training.strategies.base import (
    Callback,
    EpochResult,
    Strategy,
    SynthesisResult,
)

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


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


def _clone_environment(env: LocalEnvironment, suffix: str = "clone") -> LocalEnvironment:
    """Create an independent copy of a LocalEnvironment."""
    parent = env.workdir.parent
    clone_dir = Path(tempfile.mkdtemp(prefix=f"chimera-{suffix}-", dir=parent))

    for item in env.workdir.iterdir():
        if item.name == ".chimera_checkpoints":
            continue
        dest = clone_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    cloned = LocalEnvironment(
        workdir=str(clone_dir),
        test_cmd=env.test_cmd,
        timeout=env.timeout,
    )
    cloned.setup()
    return cloned


class TreeSearch(Strategy):
    """Best-first tree search over solution branches."""

    def __init__(
        self,
        branch_factor: int = 3,
        max_depth: int = 5,
        max_nodes: int = 20,
        max_cost: float | None = None,
        min_pass_rate: float = 0.0,
        branch_fn: Callable[..., list[str]] | None = None,
    ) -> None:
        self.branch_factor = branch_factor
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_cost = max_cost
        self.min_pass_rate = min_pass_rate
        self.branch_fn = branch_fn

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        raise NotImplementedError("TreeSearch.run() not yet implemented")
