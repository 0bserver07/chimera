---
name: algo-greedy
description: Greedy algorithms — exchange-argument proofs, when greedy beats DP, classic patterns (interval scheduling, Huffman, scheduling).
when-to-use: "Interval scheduling, activity selection, minimum-spanning-tree weights, Huffman codes, fractional knapsack, scheduling with deadlines."
---

## Greedy — locally best, prove globally optimal

Greedy algorithms make the locally optimal choice at every step
and never reconsider. They are the right tool when an "exchange
argument" works: any optimal solution can be transformed into the
greedy solution without losing optimality.

### Invariants

* The greedy choice is *safe*: there exists an optimal solution
  consistent with that choice.
* The remaining problem is the same shape (optimal substructure)
  but smaller.
* If you cannot prove safety, greedy is probably wrong — DP or
  search is the fallback.

### Complexity

* Most greedy algorithms are dominated by an `O(n log n)` sort
  step.
* Min-heap based greedy (Dijkstra, Prim, Huffman) is
  `O((V + E) log V)` or `O(n log n)`.
* Linear-scan greedy (jump game, gas station) is `O(n)`.

### Template — Python (interval scheduling — earliest end time)

```python
def max_non_overlapping(intervals: list[tuple[int, int]]) -> int:
    """Maximum number of non-overlapping intervals (start, end)."""
    intervals.sort(key=lambda iv: iv[1])  # sort by end
    count = 0
    end = float("-inf")
    for s, e in intervals:
        if s >= end:
            count += 1
            end = e
    return count
```

The exchange-argument proof: pick any optimal schedule's first
interval `O`; replace it with the earliest-ending interval `G`.
`G` ends no later than `O`, so the rest of the schedule still
fits. Repeat. The greedy schedule has at least as many intervals.

### Template — Python (Huffman coding — repeatedly merge two smallest)

```python
import heapq

def huffman_cost(weights: list[int]) -> int:
    """Total cost of an optimal binary prefix code (sum of code lengths × freq)."""
    heap = list(weights)
    heapq.heapify(heap)
    total = 0
    while len(heap) > 1:
        a = heapq.heappop(heap)
        b = heapq.heappop(heap)
        total += a + b
        heapq.heappush(heap, a + b)
    return total
```

The merging-pair argument: in *any* optimal prefix tree, the two
lowest-frequency leaves are siblings at the deepest level.
Merging them into one node reduces the problem by 1, and the
optimal solution to the sub-problem extends to the original.

### Template — JavaScript (jump game — farthest reachable)

```javascript
function canJump(nums) {
  let reach = 0;
  for (let i = 0; i < nums.length; i++) {
    if (i > reach) return false;          // cannot reach i
    reach = Math.max(reach, i + nums[i]); // extend horizon
  }
  return true;
}
```

`reach` is the invariant: "farthest index reachable from the
prefix `[0..i]`". Each step either extends the horizon or fails.

### Common pitfalls

* **Greedy on the wrong key.** "Earliest end" works for interval
  scheduling; "shortest" or "earliest start" do not. Test small
  counterexamples before coding the production version.
* **Greedy where DP is needed.** Coin change with arbitrary
  denominations is the canonical DP-vs-greedy trap: `[1, 3, 4]`,
  target 6 — greedy picks 4+1+1 (3 coins), DP picks 3+3 (2 coins).
* **Forgetting the sort.** Most greedy algorithms hinge on a sort
  by the right key. Skipping it gives wrong answers on adversarial
  input.
* **Tie-breaking matters.** Two intervals ending at the same time:
  pick the one with the latest start to maximise headroom. The
  default sort is stable but not necessarily optimal — be explicit.
* **Reasoning by example.** Two examples can both work and your
  greedy still be wrong on the third. Demand an exchange argument
  or a counterexample search.

### Test corner cases

* Empty input.
* Single element.
* All elements identical.
* Adversarial ordering (reverse-sorted, alternating).
* Ties at the greedy-key — ensure tie-breaking is correct.
* Edge-of-range constraints (interval ending exactly when next
  starts — does the schedule allow it?).

When the problem says "minimum number of X" or "maximum number of
non-conflicting Y", greedy is a strong first guess. When it says
"minimum cost to achieve goal Z", DP is the safer first guess.
