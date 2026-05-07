---
name: algo-dfs
description: Depth-first search — recursive vs iterative, recursion-depth gotchas, three-color cycle detection, topological sort.
when-to-use: "Connected components, topological sort, cycle detection, tree/graph path enumeration, backtracking skeleton."
---

## Depth-first search — recursion or explicit stack

DFS is the workhorse behind topological sort, cycle detection,
articulation points, SCC, and most backtracking puzzles. Two ways
to spell it: recursive (clean) or iterative-with-stack (deep
graphs without blowing the recursion limit).

### Invariants

* `state[v]` is one of `WHITE` (unseen), `GRAY` (on the current
  DFS path), `BLACK` (fully processed). The three-color invariant
  is what lets cycle detection on directed graphs work.
* Edge `u → v`: if `state[v] == GRAY`, you've found a back edge —
  the directed graph has a cycle.
* In tree DFS, no `BLACK` re-entry is possible because there are
  no cycles.

### Complexity

* Time: `O(V + E)` for adjacency-list graphs.
* Space: `O(V)` for the recursion stack / explicit stack +
  `state` array.
* Recursive DFS depth equals the longest path. On a 1M-node line
  graph, that exceeds Python's default recursion limit.

### Template — Python (cycle detection, three colors)

```python
WHITE, GRAY, BLACK = 0, 1, 2

def has_cycle(adj: list[list[int]]) -> bool:
    """True iff the directed graph contains any cycle."""
    state = [WHITE] * len(adj)

    def dfs(u: int) -> bool:
        state[u] = GRAY
        for v in adj[u]:
            if state[v] == GRAY: return True
            if state[v] == WHITE and dfs(v): return True
        state[u] = BLACK
        return False

    for u in range(len(adj)):
        if state[u] == WHITE and dfs(u):
            return True
    return False
```

The same shape produces a topological order: append `u` to a list
just before turning `BLACK`, then reverse the list. (Equivalent to
"emit in post-order, reverse.")

### Template — JavaScript (iterative with explicit stack)

```javascript
function dfsIterative(start, neighbors) {
  const visited = new Set();
  const stack = [start];
  const order = [];
  while (stack.length) {
    const u = stack.pop();
    if (visited.has(u)) continue;
    visited.add(u);
    order.push(u);
    // Push in reverse so the natural left-to-right order is preserved.
    const ns = neighbors(u);
    for (let i = ns.length - 1; i >= 0; i--) {
      if (!visited.has(ns[i])) stack.push(ns[i]);
    }
  }
  return order;
}
```

For `O(1M)` deep graphs prefer the iterative form; recursion will
hit the engine's call-stack limit. Python: `sys.setrecursionlimit`
buys you headroom but only up to OS thread-stack size (~10K-100K
frames before a SegFault on macOS).

### Common pitfalls

* **Marking visited on entry vs on exit.** Mark on entry. Marking
  on exit lets siblings re-process the same node.
* **Confusing undirected back edge with cycle.** In an undirected
  DFS, the edge to your parent is *always* a back edge but not a
  cycle. Track parent or skip when `v == parent`.
* **Recursion depth.** Python defaults to 1000; deep graphs need
  `sys.setrecursionlimit(10**6)` or an iterative rewrite.
* **Infinite loop on cyclic graphs without a `visited` check.**
* **Topological sort on a graph with cycles.** Ill-defined — detect
  cycles first or use Kahn's algorithm (BFS-based) which surfaces
  the cycle as remaining nodes with non-zero in-degree.

### Test corner cases

* Empty graph (no nodes — should not crash).
* Self-loop (`u → u`) — directed graphs: cycle; undirected: by
  convention also a cycle.
* Disconnected graphs — outer loop iterates every node.
* Bidirectional edges in directed input (`u → v` and `v → u`) — a
  cycle of length 2.
* Topological sort where the graph has multiple valid orderings —
  test both DFS-postorder-reversed and Kahn's.

When the DFS returns *paths*, remember to copy the path list before
recording it — a Python list passed by reference will mutate as the
search continues.
