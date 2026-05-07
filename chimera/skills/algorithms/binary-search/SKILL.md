---
name: algo-binary-search
description: Binary search invariants, half-open intervals, and the `lo<hi` template that beats off-by-one bugs.
when-to-use: "Sorted array lookups, first/last index of a value, smallest x s.t. f(x) is true, capacity/feasibility search."
---

## Binary search — half-open template

The off-by-one bug rate on hand-written binary search is famously
high (Bentley reported >90% of in-the-wild implementations had a
bug). Pick **one** template and stick to it. The half-open `[lo, hi)`
form below works for every variant — search, lower_bound,
upper_bound, predicate-search — by changing only what `mid` does.

### Invariants

* `lo` is the smallest index that *might* be the answer.
* `hi` is one past the largest index that might be the answer.
* The answer lies in `[lo, hi)`. When `lo == hi`, the search is over.
* `mid = lo + (hi - lo) // 2` (avoids overflow in languages that
  care; in Python it does not, but use it anyway for muscle memory).

### Complexity

* Time: `O(log n)` worst case for sorted-array search.
* Space: `O(1)` extra.
* When the predicate over `[lo, hi)` is monotone, the same shape
  generalises to capacity / feasibility searches over an integer
  range — no array needed.

### Template — Python (lower_bound)

```python
def lower_bound(arr: list[int], target: int) -> int:
    """Smallest i s.t. arr[i] >= target. Returns len(arr) if none."""
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo
```

For `upper_bound` (smallest `i` s.t. `arr[i] > target`) flip the
comparator to `<=`. For "exact match" check `i < len and arr[i] ==
target` after the loop. Stick to `[lo, hi)` and you never write
`hi - 1` again.

### Template — JavaScript (predicate search)

```javascript
// Smallest x in [lo, hi) s.t. ok(x) is true. Returns hi if none.
function searchPredicate(lo, hi, ok) {
  while (lo < hi) {
    const mid = lo + Math.floor((hi - lo) / 2);
    if (ok(mid)) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}
```

This shape solves "smallest capacity that ships all packages in D
days," "minimum k for which task X passes," and similar boundary
problems. The predicate must be monotone: once `ok(x)` is true, it
stays true for all `x' >= x`.

### Common pitfalls

* **Closed `[lo, hi]` ranges with `<=` in the loop.** Easy to write
  an infinite loop (`lo == hi == mid` and neither side advances).
  The half-open form makes termination obvious — the interval
  shrinks every iteration.
* **`mid = (lo + hi) // 2` in C/Java**: integer overflow on big
  arrays. Use `lo + (hi - lo) // 2`.
* **Unsorted input.** Binary search needs a sorted (or monotone-
  predicate) input. Confirm before applying — `O(log n)` over
  random data returns garbage.
* **Searching for a key that has duplicates.** Distinguish "any
  match" (rare) from "first match" (common). `lower_bound` is the
  right tool for the latter.
* **Predicate that is monotone in the wrong direction.** If `ok`
  becomes false past some threshold, search the negation: replace
  `ok(x)` with `not ok(x)` and find the boundary.

### Test corner cases

* Empty input — should return `len(arr) == 0` cleanly.
* Single element matching, single element not matching.
* All-equal array (every index is a match — `lower_bound` returns
  the first).
* Target smaller than every element / larger than every element.
* Duplicates of the target — first and last index queries.
* Predicate-search with the boundary at `lo` (always-true) and at
  `hi` (always-false).

When in doubt, write the predicate, draw the `[F, F, ..., T, T, ...]`
boundary, and the half-open template gives you the index of the
first `T`.
