---
name: python-idioms
description: Python idioms small models get wrong, with the corrected forms.
triggers: ["python", ".py", "TypeError", "AttributeError", "mutable default"]
---
## Python idioms small models confuse

A short list of high-frequency mistakes seen from 9B–35B models on Python
work, with the version that actually runs.

**Mutable default arguments.** Small models will write `def f(x=[])` and
then mutate `x`, getting silent shared state across calls. Use `None` as
the sentinel:
```python
def f(x: list[int] | None = None) -> list[int]:
    x = x if x is not None else []
    ...
```

**`is` vs `==`.** `is` checks identity, `==` checks value. Use `is` only
for `None`, `True`, `False`, and singletons. `x is 5` is a CPython
implementation detail, not a contract.

**`dict.get` chaining.** `d.get("a").get("b")` raises `AttributeError`
when `"a"` is missing. Chain with explicit defaults: `d.get("a", {}).get("b")`
or use `match` / a small helper.

**`enumerate` with a start.** `enumerate(xs, 1)` is the idiomatic 1-based
counter — small models often write `for i, x in zip(range(1, len(xs)+1), xs)`.
Prefer the built-in.

**Path joining.** Avoid `"/".join([a, b])` — use `pathlib.Path(a) / b`.
Small models forget Windows separators and then break in CI.

**Generator vs list.** `[x for x in xs if pred(x)]` materialises; a
generator expression `(x for x in xs if pred(x))` does not. If you only
need to iterate once, use the generator.

**`except:` bare clauses.** Always catch a specific exception class.
`except Exception:` is the broadest you should ever go, and even then only
at process boundaries. `except:` catches `KeyboardInterrupt` and
`SystemExit` and will hide real bugs.

**`subprocess.run` shell quoting.** Pass a list of args, not a string with
`shell=True`. Small models often write `subprocess.run(f"cmd {user_input}",
shell=True)` which is a shell injection. Use `subprocess.run(["cmd",
user_input])`.

**Type hints on `None` returns.** `def f() -> None` is required when the
function does not `return`. Forgetting this trips mypy in strict mode.

**f-string `=` debugging.** `f"{x=}"` prints `x=42` — small models often
re-implement this manually with `f"x={x}"`.

When you write Python on the small model, mentally run through this list
before emitting the patch. It costs nothing and catches most regressions.
