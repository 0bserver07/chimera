---
name: algo-bfs
description: Breadth-first search — shortest path on unweighted graphs, level-order traversal, and the `visited` discipline that prevents O(2^n) blowups.
when-to-use: "Shortest path on unweighted graphs, level traversal, multi-source spread, word-ladder transformations."
---

## Breadth-first search — queue + visited

BFS is the right answer to "shortest number of steps" on an
unweighted graph. The dangerous failure mode is forgetting to mark
nodes visited *when enqueued* — pushing duplicates blows the queue
up exponentially on cyclic graphs.

### Invariants

* The queue holds nodes in non-decreasing distance from the source.
* `visited[v]` is true iff `v` has been enqueued (NOT iff it has
  been dequeued — the difference matters).
* When a node is dequeued, its recorded distance is final and
  correct. No later path can be shorter.
* On unweighted graphs, BFS produces shortest paths. On weighted
  graphs use Dijkstra or 0/1-BFS (deque) instead.

### Complexity

* Time: `O(V + E)` for an adjacency-list representation.
* Space: `O(V)` for the queue and `visited` set.
* Multi-source BFS: same asymptotics — push every source at level
  0, then expand normally.

### Template — Python (grid + multi-source)

```python
from collections import deque

def shortest_grid_path(grid: list[list[int]],
                       starts: list[tuple[int, int]]) -> list[list[int]]:
    """Distance from any start to each cell. ``-1`` if unreachable."""
    rows, cols = len(grid), len(grid[0])
    dist = [[-1] * cols for _ in range(rows)]
    q: deque[tuple[int, int]] = deque()
    for r, c in starts:
        dist[r][c] = 0
        q.append((r, c))
    while q:
        r, c = q.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if (0 <= nr < rows and 0 <= nc < cols
                    and dist[nr][nc] == -1 and grid[nr][nc] != 1):
                dist[nr][nc] = dist[r][c] + 1
                q.append((nr, nc))
    return dist
```

Note the visited check is `dist[nr][nc] == -1` and is performed
*before* enqueueing — never enqueue the same cell twice.

### Template — JavaScript (level-order, generic graph)

```javascript
function bfsLevels(start, neighbors) {
  const visited = new Set([start]);
  let frontier = [start];
  const levels = [];
  while (frontier.length) {
    levels.push(frontier);
    const next = [];
    for (const u of frontier) {
      for (const v of neighbors(u)) {
        if (!visited.has(v)) { visited.add(v); next.push(v); }
      }
    }
    frontier = next;
  }
  return levels;
}
```

The "two-array level swap" form is more readable than a single
queue with a sentinel when the level boundary matters.

### Common pitfalls

* **Marking visited on dequeue, not enqueue.** Allows duplicate
  pushes; on a graph with branching factor `b` and depth `d` the
  queue grows to `O(b^d)`.
* **Using BFS on weighted graphs.** Edge weight 1 only — anything
  else gives wrong shortest paths. Reach for Dijkstra.
* **Re-pushing for shorter paths.** On unweighted graphs, the first
  time a node is reached IS the shortest. No need to relax.
* **Starting with `q = [start]; visited = {}`.** Mark the source
  visited before the loop, otherwise it can be re-pushed.
* **Big grids with `list.pop(0)` instead of `deque.popleft()`.** O(n)
  per pop — the whole BFS becomes `O(V^2)`.

### Test corner cases

* Single-node graph (start equals goal — distance 0).
* Disconnected graph (some nodes unreachable — distance stays `-1`).
* Cycles (must not loop forever).
* All cells blocked except start.
* Start cell IS the goal.
* Multi-source BFS where two sources race to the same cell — the
  closer source must win.

For shortest *weighted* path, use Dijkstra; for `0/1`-weighted, use
a deque (push-front for 0-weight edges, push-back for 1-weight).
