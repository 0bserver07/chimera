---
name: algo-math-tricks
description: Number-theory and bit-twiddling cheat-sheet — gcd, modular exponentiation, sieve, popcount, divisor enumeration, fast prime tests.
when-to-use: "Combinatorics with mod, prime factorisation, digit DP, modular inverse, parity checks, bitmask DP setup."
---

## Math tricks — small recipes that win interviews

A handful of arithmetic and bit recipes solve a surprising fraction
of competitive-programming and number-heavy interview problems. Each
is one routine; chain them when the problem composes.

### Recipes covered

| recipe                  | use                              | complexity         |
|-------------------------|----------------------------------|--------------------|
| `gcd(a, b)`             | reductions, fractions            | `O(log min(a, b))` |
| `pow_mod(a, e, m)`      | modular exponentiation, RSA      | `O(log e)`         |
| sieve of Eratosthenes   | all primes up to N               | `O(N log log N)`   |
| Fermat / Miller-Rabin   | primality (probabilistic / det)  | `O(k log³ n)`      |
| `popcount(x)`           | set-bit count, bitmask DP        | `O(1)` HW          |
| modular inverse         | division under mod prime         | `O(log m)`         |
| divisor enumeration     | all divisors of n                | `O(sqrt(n))`       |

### Invariants

- All "mod prime" tricks (Fermat inverse, division) require the
  modulus to be prime AND the value coprime to it.
- `gcd(a, b) = gcd(b, a mod b)` and `gcd(a, 0) = a`. Sign: take
  absolute values up front.
- Sieve memory dominates time on large N — `bytearray` saves 8x vs
  `list` of bools.

### Template — Python

```python
from math import gcd

def pow_mod(a: int, e: int, m: int) -> int:
    """a^e mod m via square-and-multiply."""
    result = 1
    a %= m
    while e:
        if e & 1:
            result = result * a % m
        a = a * a % m
        e >>= 1
    return result


def sieve(n: int) -> list[int]:
    """All primes <= n."""
    if n < 2:
        return []
    is_prime = bytearray([1]) * (n + 1)
    is_prime[0] = is_prime[1] = 0
    for i in range(2, int(n**0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, n + 1, i):
                is_prime[j] = 0
    return [i for i in range(n + 1) if is_prime[i]]


def mod_inv(a: int, p: int) -> int:
    """Inverse of a mod prime p (Fermat's little theorem)."""
    return pow_mod(a, p - 2, p)


def divisors(n: int) -> list[int]:
    """All divisors of n in O(sqrt(n))."""
    out = []
    i = 1
    while i * i <= n:
        if n % i == 0:
            out.append(i)
            if i != n // i:
                out.append(n // i)
        i += 1
    return sorted(out)
```

### Template — JavaScript (bit tricks)

```javascript
// Count set bits in a 32-bit integer (Brian Kernighan).
function popcount(x) {
  let c = 0;
  while (x) { x &= x - 1; c++; }
  return c;
}

// Iterate every non-empty subset of a bitmask (subset DP).
function* subsets(mask) {
  let s = mask;
  while (s) {
    yield s;
    s = (s - 1) & mask;
  }
}

// Lowest set bit (a power of two): isolates the rightmost 1.
const lowbit = (x) => x & -x;
```

`x & (x - 1)` clears the lowest set bit — the engine for popcount,
"is power of 2" (`x && !(x & (x - 1))`), and many iteration tricks.

### Common pitfalls

- **Mod operations on negative numbers.** Python's `%` returns a
  non-negative result; C/Java/JS return a value with the dividend's
  sign. Add `m` and re-mod when porting.
- **Integer overflow in JS.** Numbers above `2^53` lose precision.
  Use `BigInt` for modular arithmetic on big numbers.
- **Sieve below 2.** `n < 2` must return `[]` — no special-cased
  primes.
- **Modular inverse on non-coprime input.** Returns nonsense; check
  `gcd(a, m) == 1` first or use the extended Euclidean algorithm
  for the general case.
- **Pow_mod with negative exponent.** Convert to inverse: `pow(a,
  -1, m) ** abs(e) % m` — Python ≥3.8 supports `pow(a, -1, m)`.

### Test corner cases

- `gcd(0, 0)` — by convention 0; some libraries return undefined.
- `pow_mod(0, 0, m)` — convention: 1 (consistent with `0**0 = 1`).
- Sieve `n = 0` and `n = 1` — return `[]`.
- Divisors of perfect squares: include the square root once.
- Popcount of 0 — must return 0.
- Subset iteration starting from `mask = 0` — yields nothing.

### When NOT to roll your own

- Python: use `math.gcd`, `pow(a, e, m)` (handles inverse since
  3.8), `int.bit_count()` (3.10+).
- JS: use the standard `**` for non-modular pow; bigint pow for
  modular needs `BigInt` or a library.
- Cryptographic primality (RSA prime generation): use a vetted
  library, never hand-roll Miller-Rabin for security.

### Bitmask DP setup

A typical "subset DP" recurrence is `dp[mask] = best over
submasks(mask)`. Iterating submasks via `s = (s - 1) & mask` runs
in `O(3^n)` total over all masks — far cheaper than the naive
`O(4^n)` of nested mask loops.
