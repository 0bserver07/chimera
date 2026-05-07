---
name: algo-recursion
description: Recursion — base case, recursive case, recursion-depth limits, tail vs non-tail, when to convert to iteration.
when-to-use: "Tree/graph traversal, divide-and-conquer (mergesort, quicksort), backtracking, recursive structure naturally — JSON walks, regex compilation."
---

## Recursion — base case first

A recursive function solves a problem by reducing it to a smaller
instance of the same problem. Get the base case right and the
recursive case becomes a one-line transformation.

### Invariants

* **Base case** terminates the recursion. Without it, infinite
  recursion → stack overflow.
* **Recursive case** strictly reduces the problem size — by index,
  by length, by tree height. If the size doesn't shrink, you have
  unbounded recursion.
* The result of the recursive call is *trusted*. Don't second-
  guess; assume it's correct and use it.

### Complexity

* Time: depends on the recurrence. `T(n) = T(n/2) + O(1)` →
  `O(log n)`. `T(n) = T(n-1) + O(1)` → `O(n)`. `T(n) = 2 T(n-1)`
  → `O(2^n)`. Use the Master Theorem for divide-and-conquer.
* Space: `O(depth)` for the call stack. For a balanced binary
  tree of `n` nodes, depth is `O(log n)`. For a degenerate (linked-
  list-shaped) tree, depth is `O(n)`.

### Template — Python (tree traversal, in-order)

```python
class Node:
    def __init__(self, val: int, left=None, right=None):
        self.val, self.left, self.right = val, left, right

def in_order(root: Node | None, out: list[int]) -> None:
    if root is None: return     # base case
    in_order(root.left, out)    # recurse left
    out.append(root.val)        # visit
    in_order(root.right, out)   # recurse right
```

The `None` check is the base case. The recursive case trusts
that `in_order(root.left, out)` correctly populates `out` for the
left subtree.

### Template — Python (divide-and-conquer, mergesort)

```python
def mergesort(arr: list[int]) -> list[int]:
    if len(arr) <= 1: return arr[:]      # base case
    mid = len(arr) // 2
    left = mergesort(arr[:mid])
    right = mergesort(arr[mid:])
    return _merge(left, right)

def _merge(a: list[int], b: list[int]) -> list[int]:
    out: list[int] = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]: out.append(a[i]); i += 1
        else: out.append(b[j]); j += 1
    out.extend(a[i:]); out.extend(b[j:])
    return out
```

The recursive case is trivial because `_merge` correctly merges
two sorted lists — encapsulation lets the recursion stay clean.

### Template — JavaScript (backtracking, n-queens skeleton)

```javascript
function solveNQueens(n) {
  const cols = new Set(), d1 = new Set(), d2 = new Set();
  const out = [];
  const cur = [];
  function place(row) {
    if (row === n) { out.push([...cur]); return; }
    for (let c = 0; c < n; c++) {
      if (cols.has(c) || d1.has(row - c) || d2.has(row + c)) continue;
      cols.add(c); d1.add(row - c); d2.add(row + c); cur.push(c);
      place(row + 1);
      cols.delete(c); d1.delete(row - c); d2.delete(row + c); cur.pop();
    }
  }
  place(0);
  return out;
}
```

The `place(row + 1)` recursion makes the choice; the cleanup
afterward (`delete`, `pop`) restores state for the next sibling.
That symmetry is the backtracking discipline.

### Common pitfalls

* **Missing base case.** Stack overflow on the first call.
* **Base case that doesn't terminate the recursion.** E.g. `if n
  == 0: return f(n)` — call yourself again.
* **Recursing without shrinking.** Off-by-one in the slice / index
  causes the recursive call to receive the same problem.
* **Python recursion depth.** Default 1000. Set
  `sys.setrecursionlimit(10**6)` early, or rewrite iteratively.
* **Returning vs mutating.** Decide once: either every recursive
  call returns the answer, or every recursive call mutates a
  shared accumulator. Mixing the two breaks the contract.
* **Forgetting to copy mutable state when recording solutions.**
  Backtracking pushes a reference; a later mutation changes the
  recorded answer. Push a copy (`[...cur]`).
* **Repeating identical subproblems without memoisation.** That
  is the DP smell — add `@cache` or convert to iteration.

### Test corner cases

* `n == 0` and `n == 1`.
* Tree of depth 1 (root only).
* Maximally unbalanced tree (linked-list shape — depth equals n).
* Backtracking with no solution (return empty list, not crash).
* Backtracking where the *first* choice leads to a dead end (must
  unwind correctly).

When recursion runs into the depth limit, the conversion path is
usually: stack-of-frames → explicit `[(state, kind)]` list with
`kind` distinguishing pre- and post-order visits. The structure
maps 1:1 to the recursive function.
