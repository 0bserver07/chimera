# Tree Search Strategy Design

**Goal:** A `TreeSearch` strategy that explores multiple solution branches via environment checkpointing, enabling non-linear problem solving for ARC-AGI and similar tasks.

**Architecture:** Best-first tree search over `SearchNode`s. At each step, expand the highest-scoring node into N branches (parallel via ThreadPoolExecutor with cloned environments). Score by test pass rate. Continue until convergence or limits exhausted.

---

## Data Model

```python
@dataclass
class SearchNode:
    id: str                          # unique node ID
    parent_id: str | None            # None for root
    depth: int
    checkpoint_id: str               # environment checkpoint
    pass_rate: float                 # test score
    passed: int
    total: int
    cost: float
    agent_output: str
    children: list[str]              # child node IDs
```

Tree stored as `dict[str, SearchNode]` — flat lookup, tree structure via parent_id/children.

## Search Loop

1. Create root node (checkpoint current state, run tests for baseline)
2. While not converged and within limits:
   a. **SELECT**: Pick node with highest pass_rate from frontier (leaf nodes)
   b. **EXPAND**: Generate N branches from that node
   c. **EVALUATE**: Run agent + tests on each branch (parallel)
   d. **RECORD**: Create SearchNode per branch, add to tree
   e. **PRUNE**: Discard branches below min_pass_rate threshold
3. Restore best node's checkpoint, return SynthesisResult

## Parallel Execution

Branches within one expansion step run concurrently:

1. **Fork**: Copy workdir from parent checkpoint to N temp directories, create LocalEnvironment per copy
2. **Run**: Submit N `agent.run()` calls to ThreadPoolExecutor
3. **Score**: Run tests in each forked environment
4. **Checkpoint**: If any branch improves global best, checkpoint it into the main environment
5. **Cleanup**: Remove temp directories for non-best branches

Uses `shutil.copytree` for LocalEnvironment cloning. Temp dirs created under the main workdir's parent.

## Branching

Default (branch_fn=None): Run agent.run(spec.to_prompt(), env) N times — LLM sampling temperature provides diversity.

Custom: `branch_fn(spec, node, n) -> list[str]` returns N task prompts. Each prompt feeds a separate agent.run() call.

## Parameters

```python
class TreeSearch(Strategy):
    def __init__(
        self,
        branch_factor: int = 3,
        max_depth: int = 5,
        max_nodes: int = 20,
        max_cost: float | None = None,
        min_pass_rate: float = 0.0,
        branch_fn: Callable | None = None,
    ):
```

## Return Value

Standard `SynthesisResult`. `history` contains one `EpochResult` per node (flattened). `iterations` = number of expansion steps. Best checkpoint restored before return.

## Files

| File | Change |
|------|--------|
| `chimera/training/strategies/tree_search.py` | New — TreeSearch, SearchNode |
| `chimera/training/strategies/__init__.py` | Export TreeSearch |
| `chimera/__init__.py` | Export TreeSearch |

No changes to Environment, Agent, or existing strategies.
