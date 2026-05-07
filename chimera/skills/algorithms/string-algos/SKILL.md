---
name: algo-string-algos
description: String algorithms cheat-sheet — KMP, Rabin-Karp rolling hash, Z-array, suffix arrays, anagram patterns, and when to use built-ins.
when-to-use: "Substring search, multi-pattern match, longest palindrome / repeated substring, anagram grouping, prefix-function applications."
---

## String algorithms — pick the right one

Substring search has three algorithms worth knowing: built-in find
(don't reinvent), KMP (deterministic), and rolling hash (handles
multi-pattern). Beyond that, the Z-array unlocks "all matches" and
"longest common prefix"; suffix arrays unlock "longest repeated
substring".

### When to reach for what

| problem                         | algorithm           | complexity        |
|---------------------------------|---------------------|-------------------|
| single pattern, single text     | built-in `find`     | engine-defined    |
| single pattern, billions chars  | KMP                 | `O(n + m)`        |
| many patterns at once           | Aho-Corasick        | `O(n + m + z)`    |
| dynamic / 2D match              | rolling hash        | `O(n + m)` exp.   |
| longest palindrome              | Manacher            | `O(n)`            |
| anagram groups                  | sorted-key bucket   | `O(n k log k)`    |
| longest repeated substring      | suffix array + LCP  | `O(n log n)`      |

### Invariants

- KMP's failure function `pi[i]` is the length of the longest
  proper prefix of `pattern[:i+1]` that is also a suffix. Built
  in `O(m)`.
- Rolling hash `H(s) = sum(s[i] * b^(n-1-i)) mod p`. Update is
  `H' = (H * b + s[n]) mod p` (extend) or `(H - s[0] * b^(n-1)) *
  b + s[n+1] mod p` (slide).
- Two distinct hashes with independent `(base, prime)` pairs make
  false positives effectively impossible (probability < `1/p²`).

### Template — Python (KMP failure function + search)

```python
def kmp_search(text: str, pattern: str) -> list[int]:
    """All start indices where pattern occurs in text."""
    if not pattern:
        return list(range(len(text) + 1))
    pi = _failure(pattern)
    out = []
    j = 0
    for i, ch in enumerate(text):
        while j and ch != pattern[j]:
            j = pi[j - 1]
        if ch == pattern[j]:
            j += 1
        if j == len(pattern):
            out.append(i - j + 1)
            j = pi[j - 1]
    return out


def _failure(p: str) -> list[int]:
    pi = [0] * len(p)
    k = 0
    for i in range(1, len(p)):
        while k and p[i] != p[k]:
            k = pi[k - 1]
        if p[i] == p[k]:
            k += 1
        pi[i] = k
    return pi
```

### Template — JavaScript (Rabin-Karp rolling hash, double hash)

```javascript
function rabinKarp(text, pattern) {
  if (pattern.length > text.length) return [];
  const B1 = 131n, M1 = 1_000_000_007n;
  const B2 = 137n, M2 = 998_244_353n;
  const m = pattern.length;
  let pH1 = 0n, pH2 = 0n, tH1 = 0n, tH2 = 0n;
  let pow1 = 1n, pow2 = 1n;
  for (let i = 0; i < m; i++) {
    pH1 = (pH1 * B1 + BigInt(pattern.charCodeAt(i))) % M1;
    pH2 = (pH2 * B2 + BigInt(pattern.charCodeAt(i))) % M2;
    tH1 = (tH1 * B1 + BigInt(text.charCodeAt(i))) % M1;
    tH2 = (tH2 * B2 + BigInt(text.charCodeAt(i))) % M2;
    if (i < m - 1) { pow1 = pow1 * B1 % M1; pow2 = pow2 * B2 % M2; }
  }
  const out = [];
  for (let i = 0; i <= text.length - m; i++) {
    if (tH1 === pH1 && tH2 === pH2) out.push(i);
    if (i + m < text.length) {
      const drop1 = BigInt(text.charCodeAt(i)) * pow1 % M1;
      const drop2 = BigInt(text.charCodeAt(i)) * pow2 % M2;
      tH1 = ((tH1 - drop1 + M1) * B1 + BigInt(text.charCodeAt(i + m))) % M1;
      tH2 = ((tH2 - drop2 + M2) * B2 + BigInt(text.charCodeAt(i + m))) % M2;
    }
  }
  return out;
}
```

### Common pitfalls

- **Hand-rolling KMP when `str.find` would do.** Built-in is
  faster on most real inputs; KMP wins only when the pattern has
  long self-overlap.
- **Single-hash rolling hash on adversarial input.** A single 64-bit
  prime can be collided by a malicious tester. Always use double
  hash or 128-bit.
- **Off-by-one in the failure function.** `pi[i]` is the length of
  the longest proper prefix-suffix of `p[:i+1]` — proper meaning
  `< i + 1`.
- **Anagram via sort vs counter.** Sort is `O(k log k)`; counter
  is `O(k)`. For long strings, counter wins.
- **Unicode boundaries.** Many algorithms assume one byte per
  character. For Unicode, work in code points (`text.codePointAt`)
  or accept O(n) reindexing cost.

### Test corner cases

- Empty pattern (every position is a match — including past the
  end if the spec allows).
- Empty text (no matches).
- Pattern longer than text (no matches).
- Pattern equals text (one match at index 0).
- Pattern of all identical characters (`"aaaa"`) and text of the
  same (`"aaaaaaa"`) — exercises overlapping matches.
- ASCII-only vs Unicode-mixed text.
- Pattern that is a proper prefix of itself shifted (e.g.
  `"abab"` in `"ababab"`) — exercises the failure jump.

### Anagram grouping

```python
from collections import defaultdict

def group_anagrams(words: list[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for w in words:
        key = "".join(sorted(w))
        groups[key].append(w)
    return list(groups.values())
```

For very long words, swap the sorted-key for a 26-int tuple of
character counts to get linear-per-word grouping.

### When NOT to roll your own

- Single substring search → use built-in `find` / `indexOf`.
- Regex tasks → use the engine; don't reimplement.
- Production fuzzy match → use a maintained library (not a hand
  rolled Levenshtein) for correctness on Unicode.
