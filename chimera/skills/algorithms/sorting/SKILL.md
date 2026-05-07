---
name: algo-sorting
description: Sorting — when to call the built-in, when to roll your own (counting / radix / heap), stability, custom comparators.
when-to-use: "Pre-sort for two-pointer / binary-search, top-k via heap, kth-smallest via quickselect, custom-key ordering."
---

## Sorting — `sort()` first, custom rarely

Python's `list.sort` and JavaScript's `Array.prototype.sort` are
both Timsort variants — `O(n log n)` worst case, stable. Reach for
a custom sort *only* when you need a different time/space trade
(counting sort for small integer ranges) or you cannot afford the
copy (heap-based top-k).

### Invariants

* Stable sort: equal-key elements retain their original relative
  order. Both `list.sort()` and recent `Array.prototype.sort` are
  stable; older JS engines were not (pre-2019).
* In-place sort returns `None` in Python (`list.sort`); use
  `sorted()` for a new list. JS `arr.sort()` mutates *and* returns
  the array.
* Sorting by multiple keys: sort by least-significant key first,
  then by next, taking advantage of stability. Or use a tuple key.

### Complexity

* Built-in (Timsort): `O(n log n)` worst, `O(n)` best on
  near-sorted input.
* Quickselect (kth element): `O(n)` average, `O(n^2)` worst.
* Heap-based top-k: `O(n log k)` time, `O(k)` extra space.
* Counting sort: `O(n + r)` for value range `r` — beats comparison
  sort when `r` is small (e.g. 0..255).
* Radix sort: `O(n × d)` for `d` digits — strings of bounded
  length, fixed-precision integers.

### Template — Python (multi-key custom comparator via tuple key)

```python
# Sort by score descending, then by name ascending. Stability is
# implicit: equal-tuple rows keep insertion order.
records.sort(key=lambda r: (-r.score, r.name))

# Stability lets you also chain single-key sorts:
records.sort(key=lambda r: r.name)         # secondary
records.sort(key=lambda r: r.score, reverse=True)  # primary
```

Use a tuple key when you can — it is cleaner and roughly twice as
fast as `functools.cmp_to_key`. Reach for `cmp_to_key` only when
the comparison is non-transitive in tuple form (rare; one classic
case is "biggest-number-by-concatenation").

### Template — Python (heap-based top-k)

```python
import heapq

def top_k_largest(arr: list[int], k: int) -> list[int]:
    """The ``k`` largest elements (in arbitrary order)."""
    if k <= 0: return []
    return heapq.nlargest(k, arr)

def top_k_smallest_by_key(items, k, key):
    return heapq.nsmallest(k, items, key=key)
```

`heapq.nsmallest`/`nlargest` are `O(n log k)` and avoid sorting
the whole list when `k << n`.

### Template — JavaScript (custom comparator, stable since 2019)

```javascript
// Numeric sort — default is lexicographic, which is wrong for numbers.
arr.sort((a, b) => a - b);

// Multi-key: descending score, then ascending name.
records.sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
```

`localeCompare` is the right tool for human-language strings. For
ASCII identifiers, the `<`/`>` operators are faster.

### Common pitfalls

* **JavaScript default sort is lexicographic.** `[10, 2, 1].sort()`
  gives `[1, 10, 2]`. Always pass a comparator for numbers.
* **Mutating during iteration.** `for i in range(len(arr)): if pred(arr[i]): arr.remove(...)`
  shifts indices and skips elements. Sort first and walk with a
  write index instead.
* **`list.sort()` returns None in Python.** `x = arr.sort()` sets
  `x = None`. Use `sorted(arr)` to get a new list.
* **Comparator returning bool.** JS `sort((a, b) => a > b)` returns
  true/false (1/0 in numeric coercion) — non-transitive, breaks
  stability. Return `a - b` or `cmp(a, b)`.
* **Stability assumed but not provided.** On older runtimes, equal
  keys may shuffle. If correctness depends on it, augment the key
  with the original index.
* **Sort + binary search on objects with mutating fields.** If a
  later edit mutates the sort key, the array is no longer sorted.

### Test corner cases

* Empty array.
* Single-element array.
* Already sorted (best case).
* Reverse sorted (worst case for naive quicksort).
* All-equal values (stability matters here — verify).
* Mixed types (numbers + strings) — coerces and gives surprising
  results. Always sort homogeneous data.
* Floats with NaN — comparisons with NaN return false, leading to
  undefined order. Filter NaNs first.

For "median in a stream", reach for two heaps (min + max). For
"kth smallest", quickselect or `nsmallest` is faster than sort+index.
