---
name: algo-sliding-window
description: Sliding window — fixed and variable size, expand-shrink invariant, hash-state-tracking for substring problems.
when-to-use: "Longest/shortest substring with constraint, k-distinct elements, max sum subarray of size k, anagram windows."
---

## Sliding window — expand right, shrink left

Sliding window collapses an `O(n^2)` "try every subarray" loop
into `O(n)` by reusing partial state across moves. The trick: the
window is *contiguous* and you keep enough state about the window
to update it in `O(1)` (or `O(k)` for `k` distinct keys) per step.

### Invariants

* The window `[l, r]` is contiguous — no skipping.
* `r` advances by 1 each outer iteration.
* `l` advances *only* when the window state violates the constraint.
  Once advanced, it never moves back.
* `state(window)` is maintained incrementally: add `arr[r]` on
  expand, subtract `arr[l]` on shrink.

### Complexity

* Time: `O(n)` amortised — each element is pushed once and popped
  at most once across the whole scan.
* Space: `O(k)` for the state (e.g., a counter dict for distinct
  characters).

### Template — Python (variable-size, longest substring with k distinct chars)

```python
def longest_k_distinct(s: str, k: int) -> int:
    """Length of the longest substring with at most k distinct characters."""
    from collections import Counter
    counts: Counter[str] = Counter()
    best = l = 0
    for r, ch in enumerate(s):
        counts[ch] += 1
        while len(counts) > k:
            counts[s[l]] -= 1
            if counts[s[l]] == 0:
                del counts[s[l]]
            l += 1
        best = max(best, r - l + 1)
    return best
```

The shrink loop keeps the invariant `len(counts) <= k`. The
`del` is critical — without it, `counts` keeps stale zeroes and
the `len` check fails.

### Template — Python (fixed-size window, max sum)

```python
def max_sum_subarray(arr: list[int], k: int) -> int:
    """Maximum sum over any contiguous subarray of length k."""
    if len(arr) < k: return 0
    s = sum(arr[:k])
    best = s
    for r in range(k, len(arr)):
        s += arr[r] - arr[r - k]   # roll the window
        best = max(best, s)
    return best
```

For fixed-size windows, the "subtract the leaver, add the joiner"
trick eliminates the inner loop entirely.

### Template — JavaScript (anagram-window detection)

```javascript
function findAnagrams(s, p) {
  if (p.length > s.length) return [];
  const need = new Map();
  for (const c of p) need.set(c, (need.get(c) || 0) + 1);
  const have = new Map();
  const out = [];
  for (let r = 0; r < s.length; r++) {
    const c = s[r];
    have.set(c, (have.get(c) || 0) + 1);
    if (r - p.length >= 0) {
      const left = s[r - p.length];
      have.set(left, have.get(left) - 1);
      if (have.get(left) === 0) have.delete(left);
    }
    // Compare maps by size + entries.
    if (have.size === need.size &&
        [...need].every(([k, v]) => have.get(k) === v)) {
      out.push(r - p.length + 1);
    }
  }
  return out;
}
```

The window slides by exactly one each step; comparison is `O(σ)`
where `σ` is the alphabet size — for ASCII, effectively constant.

### Common pitfalls

* **Shrinking too aggressively.** Some problems want the shrink
  to stop the moment the constraint is repaired (`while > k`),
  not after one step (`if > k`). Off by one breaks "longest".
* **Forgetting to update the answer at every `r`.** "Longest"
  problems update inside the outer loop, after the shrink. "Number
  of substrings" updates differently (count `r - l + 1` after
  shrink — see "subarrays with at most k distinct").
* **Stale zero counts.** Letting `counts[c] == 0` linger inflates
  `len(counts)` and breaks the constraint check. Delete on zero.
* **Negative numbers + monotonic shrink.** Sliding window on
  arbitrary signs *does not* always work — adding a negative
  number can shrink the sum, breaking the monotone-shrink
  property. Consider prefix-sum + monotonic queue / deque instead.
* **Fixed-window length larger than input.** Guard with
  `if len(arr) < k`.

### Test corner cases

* Empty input / k larger than n.
* k == 0 (degenerate — define behaviour explicitly).
* All-same characters.
* Alphabet larger than string length.
* Window straddling the very end of the input.
* "At most k distinct" with k == n (whole string is the window).

If the constraint is "at most k of X", sliding window is usually
the right tool. If the constraint involves a non-monotonic
function, reach for prefix-sum + monotonic deque instead.
