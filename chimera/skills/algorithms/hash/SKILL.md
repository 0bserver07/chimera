---
name: algo-hash
description: Hash maps and sets — when O(1) lookup pays off, hash collisions, frozen keys, and hashing custom types.
when-to-use: "Frequency counts, deduplication, two-sum and complement-lookup patterns, anagram grouping, prefix-sum + hash."
---

## Hash maps and sets — the O(1) tool

Hashing trades memory for time. The trade is almost always worth
it when you need membership / count / lookup by key. The dangerous
failure mode is hashing a mutable type — Python silently breaks,
JavaScript happily compares by reference instead of value.

### Invariants

* Average-case `O(1)` insert / lookup / delete for keys with a
  good hash distribution.
* Order is **not** guaranteed in the abstract data type. CPython
  3.7+ preserves insertion order in `dict`, but never rely on it
  for correctness — sort explicitly when order matters.
* Keys must be hashable. In Python: `tuple` of hashables OK;
  `list`, `dict`, `set` are not hashable.

### Complexity

* Insert / lookup / delete: `O(1)` average, `O(n)` worst case
  under adversarial collisions.
* Iteration: `O(n)`.
* Space: `O(n)` for `n` distinct keys; load factor `< 0.75` for
  reasonable performance.

### Template — Python (two-sum + complement lookup)

```python
def two_sum(nums: list[int], target: int) -> tuple[int, int] | None:
    """Indices (i, j) such that nums[i] + nums[j] == target, i < j."""
    seen: dict[int, int] = {}  # value -> first index
    for j, x in enumerate(nums):
        complement = target - x
        if complement in seen:
            return (seen[complement], j)
        seen.setdefault(x, j)
    return None
```

The complement-lookup pattern generalises: any "find pair s.t.
`f(a, b) == target`" reduces to "store seen `f` results, look up
the inverse." The same shape gives prefix-sum + hash for
"subarrays summing to k."

### Template — JavaScript (frequency count + grouping)

```javascript
function groupAnagrams(words) {
  const groups = new Map();
  for (const w of words) {
    // Sorted-letter signature is a canonical key for anagrams.
    const key = [...w].sort().join("");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(w);
  }
  return [...groups.values()];
}
```

`Map` is the right choice over a plain `{}` when keys are not
strings or when you need a defined iteration order. `Set` is the
right choice when you only care about membership.

### Common pitfalls

* **Hashing mutable types in Python.** A `list` raises `TypeError`
  on insertion. A custom class is hashable by default (id-based)
  but compares by `==` only if you implement `__eq__` *and*
  `__hash__` together — missing the pair gives baffling lookup
  failures.
* **`{}` vs `set()` in Python.** `{}` is an empty dict, not a set;
  `set()` is empty set. Easy to write `seen = {}` then call
  `seen.add(x)` and crash.
* **Default-dict pitfalls.** `dict.get(k, [])` returns a fresh list
  each call. Use `dict.setdefault` or `collections.defaultdict`
  when accumulating.
* **Object keys in JavaScript `{}`.** Coerced to string. Use
  `Map` for non-string keys.
* **Hash flooding / DoS.** Untrusted input can be crafted to
  collide. Use the language's randomised hashing (Python does this
  by default; JS engines vary) or a cryptographic hash for adversarial
  inputs.

### Test corner cases

* Empty input.
* All-duplicate input (every key collides logically — count goes
  up, set size stays 1).
* Negative numbers, zero, very large numbers.
* Float keys with NaN — `NaN != NaN` in both languages, so NaN-
  keyed lookups can fail mysteriously.
* Unicode keys (composing characters that are visually identical
  but differ by code point).
* Custom hashable type with broken `__eq__` / `__hash__` contract.

The "store seen, look up complement" pattern shows up in two-sum,
3-sum, contiguous-subarray-sum-equals-k, longest-substring-without-
repeats, and dozens more. Recognise the shape and you skip the
quadratic version.
