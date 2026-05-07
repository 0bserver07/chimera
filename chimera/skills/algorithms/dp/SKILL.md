---
name: algo-dp
description: Dynamic programming — state design, memoization vs tabulation, dimension-reduction, and when DP is the wrong tool.
when-to-use: "Optimal-substructure problems with overlapping subproblems: knapsack, edit distance, LIS, coin change, interval DP, bitmask DP."
---

## Dynamic programming — state, transition, base

DP fails most often at the design phase, not the coding phase. If
you can name `state`, `transition`, and `base case` in one sentence
each, the implementation usually writes itself.

### Invariants

* **Optimal substructure**: an optimal answer for size `n` is built
  from optimal answers to strictly smaller subproblems.
* **Overlapping subproblems**: the recursion tree visits the same
  subproblem more than once, so caching pays.
* The DP table `dp[s]` is the answer to subproblem `s`. The order
  of filling matters: every dependency of `dp[s]` must already be
  computed.

### Complexity

* Time: `O(states × transitions)`. A 1D DP over `n` with `O(1)`
  transition is `O(n)`; a 2D DP over `n × m` with `O(k)` transition
  is `O(n m k)`.
* Space: same as states by default; often reducible to one or two
  rows when the recurrence only references the previous row(s).

### Template — Python (memoization, top-down)

```python
from functools import cache

def edit_distance(s: str, t: str) -> int:
    """Minimum insert/delete/replace ops to convert s into t."""
    @cache
    def f(i: int, j: int) -> int:
        if i == len(s): return len(t) - j
        if j == len(t): return len(s) - i
        if s[i] == t[j]: return f(i + 1, j + 1)
        return 1 + min(
            f(i + 1, j),     # delete s[i]
            f(i, j + 1),     # insert t[j]
            f(i + 1, j + 1), # replace
        )
    return f(0, 0)
```

`@cache` is the cheapest way to add memoization in Python and turns
a `2^n` recursion into `O(nm)`. For very tight memory budgets,
convert to a bottom-up table.

### Template — JavaScript (tabulation, bottom-up + space reduction)

```javascript
// 0/1 knapsack: max value with weight <= W.
function knapsack(weights, values, W) {
  let dp = new Array(W + 1).fill(0);
  for (let i = 0; i < weights.length; i++) {
    // Iterate w downward so each item is used at most once.
    for (let w = W; w >= weights[i]; w--) {
      dp[w] = Math.max(dp[w], dp[w - weights[i]] + values[i]);
    }
  }
  return dp[W];
}
```

Reducing 2D to 1D by reusing the previous row is the most common
DP optimisation. The downward-iteration trick prevents using the
*current* item more than once (vs unbounded knapsack, which iterates
upward).

### Common pitfalls

* **State explosion.** If states are `(i, j, k, mask, ...)`, count
  them up before coding. `n=100, mask=2^20` is 100M states — almost
  certainly too big.
* **Wrong fill order.** A bottom-up table iterating in the wrong
  direction reads uninitialised cells. Memoization sidesteps this
  by computing dependencies on demand.
* **Recursion depth.** Python's default limit is 1000. Set
  `sys.setrecursionlimit(10**6)` early in the run, or rewrite
  bottom-up.
* **Reconstructing the solution.** Storing only the optimal *value*
  is half the job; the path requires either a parent pointer per
  state or re-running the recurrence with `argmax`.
* **DP where greedy works.** Coin change with arbitrary denominations
  needs DP; canonical denominations (US coins) admit a greedy.
  Don't reach for DP first.

### Test corner cases

* Empty input (zero size, returns the base case).
* Size 1.
* All elements identical.
* Negative or zero weights / values where the problem allows them.
* Boundary at the capacity limit (`W == sum(weights)`,
  `W == 0`).
* For sequence DP: `s == t`, `s == ""`, `t == ""`.

When you cannot describe the state in one sentence, you are not
ready to write the recurrence. Stop and re-decompose.
