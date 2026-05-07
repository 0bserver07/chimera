---
name: algo-two-pointers
description: Two-pointer scans — opposite ends, same direction, fast/slow — for sorted arrays, partitioning, cycle detection.
when-to-use: "Sorted-array pair sums, palindromes, removing duplicates in place, linked-list cycle detection, partitioning."
---

## Two pointers — converging or chasing

Two-pointer scans turn many `O(n^2)` loops into `O(n)`. There are
three common shapes:

1. **Opposite ends** — `lo` at the start, `hi` at the end, walk
   inward (sorted-array pair sum, palindrome).
2. **Same direction** — both start at 0; `j` advances every step,
   `i` advances conditionally (in-place dedup, partition).
3. **Fast/slow** — `slow` advances by 1 step, `fast` by 2 (linked-
   list cycle detection, middle of list).

### Invariants

* The pointer pair encloses the *unprocessed* region of the input.
* Each pointer moves monotonically; no backtracking. That is what
  buys the `O(n)` amortisation.
* For opposite-ends on a sorted array: if `arr[lo] + arr[hi]` is
  too small, only `lo++` can help (any pair with the current `lo`
  paired against a smaller `hi` is even smaller).

### Complexity

* Time: `O(n)` for a single linear scan; `O(n^2)` for k-sum
  variants where one pointer is fixed and a two-pointer scan is
  done inside.
* Space: `O(1)` extra.

### Template — Python (opposite ends, sorted pair sum)

```python
def pair_sum_sorted(arr: list[int], target: int) -> tuple[int, int] | None:
    """Indices of two values in sorted ``arr`` summing to target."""
    lo, hi = 0, len(arr) - 1
    while lo < hi:
        s = arr[lo] + arr[hi]
        if s == target: return (lo, hi)
        if s < target: lo += 1
        else: hi -= 1
    return None
```

Note: requires sorted input. If the input is unsorted, hashing is
typically simpler; only use two-pointers when sorting is already
done or stable indices don't matter.

### Template — Python (same direction, in-place dedup)

```python
def dedup_sorted(arr: list[int]) -> int:
    """Compact sorted ``arr`` so the first ``k`` entries are unique. Returns k."""
    if not arr: return 0
    i = 0  # write index
    for j in range(1, len(arr)):
        if arr[j] != arr[i]:
            i += 1
            arr[i] = arr[j]
    return i + 1
```

The "write pointer + read pointer" form is the basis for all
in-place partitioning algorithms (Lomuto, Hoare).

### Template — JavaScript (Floyd's cycle detection)

```javascript
function hasCycle(head) {
  let slow = head, fast = head;
  while (fast && fast.next) {
    slow = slow.next;
    fast = fast.next.next;
    if (slow === fast) return true;
  }
  return false;
}
```

If the lists meet, there is a cycle. The mathematical proof: in a
cycle of length L, `fast` gains 1 step on `slow` per iteration, so
they meet within L iterations of `slow` entering the cycle.

### Common pitfalls

* **Forgetting the input must be sorted.** Two-pointers on
  unsorted input gives wrong answers — sort first, or use hashing.
* **Off-by-one on `hi = len(arr)` vs `hi = len(arr) - 1`.** Choose
  one convention; the templates above use inclusive `hi`. Mixing
  conventions causes index-out-of-range or missed pairs.
* **Mutating while iterating.** In dedup-style code, advance write
  pointer *before* writing, otherwise the first element gets
  overwritten.
* **Floyd's cycle detection with `head == null`.** Guard the loop
  with `fast && fast.next`.
* **Sliding-window mistaken for two-pointers.** Sliding window is
  same-direction with an explicit "shrink" step; two-pointers may
  not need shrinking. Pick the simpler one.

### Test corner cases

* Empty array.
* Single-element array (no pair possible / nothing to dedup).
* All-equal array (dedup compacts to length 1).
* Pair at the boundary (`lo == 0`, `hi == n - 1` matches).
* No matching pair.
* Linked list of length 1 with a self-loop (`head.next = head`).
* Linked list of length 2 with no cycle.

The dedup template generalises to any "compact while preserving
order under predicate P" — change the comparator and you get
remove-zeroes, remove-duplicates-keep-at-most-twice, etc.
