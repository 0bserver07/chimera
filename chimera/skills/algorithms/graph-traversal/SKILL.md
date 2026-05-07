---
name: algo-graph-traversal
description: Pick the right traversal — BFS for unweighted shortest path, Dijkstra for weighted, A* for goal-directed, 0/1-BFS for binary weights.
when-to-use: "Shortest path, reachability, components, MST, A* on grids and weighted graphs."
---

## Graph traversal — picking the algorithm

Choosing the wrong traversal is the single biggest source of wrong
answers on graph problems. The decision tree below is short on
purpose.

### Decision flow

| input shape                          | algorithm           | time                |
|--------------------------------------|---------------------|---------------------|
| unweighted                           | BFS                 | `O(V + E)`          |
| edge weights `{0, 1}`                | 0/1-BFS (deque)     | `O(V + E)`          |
| non-negative weights                 | Dijkstra (heap)     | `O((V + E) log V)`  |
| any real weights, no neg cycles      | Bellman-Ford        | `O(V E)`            |
| all-pairs                            | Floyd-Warshall      | `O(V^3)`            |
| heuristic + goal node                | A*                  | depends on h        |
| MST                                  | Kruskal / Prim      | `O(E log V)`        |

### Invariants

- **Dijkstra:** when a node is popped from the priority queue, its
  recorded distance is final. Pushing duplicates is fine *if* you
  skip stale entries on pop (`if d > dist[u]: continue`).
- **A\*:** correctness requires an *admissible* heuristic — never
  overestimates the true cost to the goal. Manhattan distance on a
  4-direction grid with unit cost is admissible.
- **Bellman-Ford:** after `V - 1` relaxation rounds, all distances
  are final unless a negative cycle exists.

### Complexity

- BFS / 0-1-BFS: `O(V + E)` time, `O(V)` extra space.
- Dijkstra (binary heap): `O((V + E) log V)` time, `O(V)` extra space.
- Bellman-Ford: `O(V · E)` time, `O(V)` space — slower but handles
  negative weights and detects negative cycles.
- Floyd-Warshall: `O(V³)` time, `O(V²)` space — only viable when
  `V ≤ ~400`.
- A\*: best-case `O(|path|)` with a tight heuristic, worst-case the
  same as Dijkstra. Memory dominates on huge state spaces.

### Template — Python (Dijkstra with heap)

```python
import heapq

def dijkstra(adj: dict[int, list[tuple[int, int]]], src: int) -> dict[int, int]:
    """Shortest distances from src on a non-negatively weighted graph."""
    dist: dict[int, int] = {src: 0}
    pq: list[tuple[int, int]] = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue                    # stale entry
        for v, w in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist
```

### Template — JavaScript (0/1-BFS via deque)

```javascript
// Edges have weight 0 or 1. Push 0-edges to the front, 1-edges to the back.
function zeroOneBFS(adj, src) {
  const dist = new Map([[src, 0]]);
  const dq = [src];
  while (dq.length) {
    const u = dq.shift();
    for (const [v, w] of adj.get(u) || []) {
      const nd = dist.get(u) + w;
      if (nd < (dist.get(v) ?? Infinity)) {
        dist.set(v, nd);
        if (w === 0) dq.unshift(v); else dq.push(v);
      }
    }
  }
  return dist;
}
```

### Common pitfalls

- **Dijkstra with negative weights.** Returns wrong answers without
  warning. Use Bellman-Ford instead.
- **Forgetting the stale-pop guard.** Without `if d > dist[u]:
  continue`, the heap can grow to `O(E)` and runtime degrades.
- **Inadmissible A\* heuristic.** Loses optimality. Manhattan over
  diagonal-allowed grids is *not* admissible.
- **Bellman-Ford without the negative-cycle check.** Always run one
  extra iteration; if any distance still decreases, a negative
  cycle exists.
- **Treating undirected edges as one-way.** Add both directions or
  use an undirected representation.

### Test corner cases

- Single node, src equals goal — distance 0.
- Disconnected graph — unreachable nodes have `inf` distance.
- Tie on edge weights — implementation must be deterministic OR
  the test must accept any valid shortest path.
- Negative-weight edge in Dijkstra input — assert error or sentinel.
- Self-loops with weight > 0 (must not affect shortest path).
- Dense graph (`E = V^2`) — Dijkstra heap version vs `O(V^2)` array
  version: the array version is faster on dense graphs.

### Memory notes

- A* with grid heuristics: closed-set as a 2D bool array beats a
  hash set on hot paths.
- Dijkstra on huge graphs: indexed priority queue (decrease-key)
  saves a constant factor over the duplicate-push pattern.

When in doubt, draw the graph by hand for `V <= 6`, run the chosen
algorithm on paper, and compare with the brute-force answer. The
algorithm is wrong nine times out of ten on small examples before
it's wrong on large ones.
