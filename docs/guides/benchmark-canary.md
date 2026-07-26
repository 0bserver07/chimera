# The benchmark canary

A benchmark adapter can be completely broken while its test suite is green.
Unit tests assert that an adapter **runs**; they rarely assert that a **correct
answer scores**. Everything between those two claims is invisible — and that gap
has produced three fabricated results in this repo:

| What broke | What it looked like | What was true |
|---|---|---|
| `humaneval-x` executed `prompt + raw_reply + test` | a clean `0/50`, `status_counts {completed: 50}` | `50/50` — prose died of `SyntaxError` before any assertion ran |
| `livecodebench` grades `functional` tasks via stdin | a "documented lower bound" of ≥18.9% | 36% of the denominator cannot pass under **any** answer |
| `list_files` used `fnmatch` (`*` crossed `/`) | consistent cross-sandbox results | the same benchmark saw different files per backend |

None of these were subtle once seen. All three survived a green suite.

## The idea

Take a task's **own canonical solution**, hand it to `evaluate()` exactly the
way an agent would, and require a pass.

If a dataset's own reference answer cannot score, nothing an agent writes will
either, and every number that adapter has ever produced is fiction. It is the
cheapest possible check and it catches the entire class.

```bash
python scripts/canary_benchmarks.py              # every staged adapter
python scripts/canary_benchmarks.py --bench mbpp # one adapter
python scripts/canary_benchmarks.py --limit 200  # full-dataset sweep
python scripts/canary_benchmarks.py --json       # machine-readable
```

Exit code is `1` if any adapter is `BROKEN`. Run it **before buying a benchmark
column**, not after — a wrong number is worse than a missing one, because nobody
re-checks a number that already looks plausible.

## Two rules that make it trustworthy

**1. The inverse matters as much as the positive.** A grader hardwired to return
`True` would sail through a positive-only canary with a perfect score. So every
adapter is also fed a wrong answer, an empty answer, and prose-only — and must
reject all three. *A canary that cannot fail is not a canary.*

**2. Submit the shapes agents actually send.** Each answer goes in three ways:
dataset-native, a fenced code block, and a fenced block wrapped in prose (what
`FINAL_ANSWER_CONTRACT` asks matrix agents for). The `humaneval-x` zero hid for a
whole release *precisely* because the old tests only ever fed the first shape —
the one an instructed chat agent never produces.

Adapters are built through `chimera.cli.main._load_benchmark`, the same call
`chimera bench-matrix` makes, so the canary exercises the configuration that
really runs rather than a hand-built stand-in.

## Reading the verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | the reference answer scores in every shape, and wrong answers are rejected |
| `BROKEN` | **no number from this adapter is real.** Retract what was published; do not just re-run |
| `ENV-MISSING` | the graded program needs a module this interpreter lacks — *unverified*, not passing |
| `NOT-STAGED` | no dataset locally. `chimera bench-fetch <name>` then re-run |
| `EXEMPT` | cannot be canaried offline, with a stated reason (needs a repo, a browser, a sim) |

`ENV-MISSING` and `NOT-STAGED` are **not passes**. They are printed rather than
skipped so an unaudited adapter stays visible instead of being mistaken for a
healthy one.

## False alarms are failures too

A canary that calls a working adapter broken sends someone to "fix" correct
code. The canary's own first run produced two false `BROKEN`s, and both fixes
are now pinned by tests:

- **Guessing the stub field.** BigCodeBench's `prompt` is its *natural-language*
  instruct prompt (the code stub is `code_prompt`), and HumanEval-X's
  `declaration` is malformed in the staged data for `Python/142`
  (`'def sum_squares(lst):\n    "\n'`) — a field the adapter never reads. Stub
  fields are now named explicitly per benchmark, never inferred.
- **Scanning the wrong test field.** MBPP carries both a `test` blob and a
  `test_list`, and grades with `test_list`. Scanning `test` invented a numpy
  requirement for tasks that never touch numpy. Recipes declare `test_fields`.

Dependency attribution is judged **per task**, so one numpy-dependent task does
not condemn the other four — and is never counted as verified either.

## Adding a benchmark

Every registered benchmark needs an entry in `RECIPES` — either a way to build
its known-correct answer, or an explicit `exempt=` reason. A test enforces this,
so a benchmark added next month cannot be silently unaudited while the canary
still reports all-green.

```python
RECIPES = {
    "my-bench": Recipe(answer=_joined("code_prompt"), test_fields=("test",)),
    "browser-bench": Recipe(exempt="agentic — grading needs a live browser"),
}
```

## Disclosed exclusions

`KNOWN_UNPASSABLE` lists tasks whose *dataset's own* reference answer cannot
satisfy the *dataset's own* test — a defect in the staged data, not in Chimera's
grader. Listing one is a **disclosure, not a dismissal**: it caps the
benchmark's achievable score, and that cap must travel with any number published
from it.

Currently one entry: `humaneval-plus` / `HumanEval/32`, whose final assertion is
`_poly(*candidate(*inp), inp)` — it splats the float `find_zero` returns and dies
of `TypeError` before comparing anything. It is present verbatim in the raw
upstream rows (`evalplus/humanevalplus`), so it is not a staging artifact. **It
caps humaneval-plus at 163/164 = 99.4%.**

Entries require evidence, because "known bad" is exactly the label a real bug
would love to hide behind — a test rejects a thin reason.

## Current state

Last full sweep (`--limit 200`): **7 pass, 0 broken, 1 not staged, 19 exempt.**

`human-eval`, `humaneval-plus`, `humaneval-x`, `mbpp`, `mbpp-plus`, `aimo` and
`bigcodebench` all grade their own canonical answers correctly across the full
dataset *and* reject wrong ones. Those are the adapters behind the published
flagship scorecard, so those numbers rest on a verified grader.

The 19 exempt adapters are **unverified, not healthy**. Extending the canary to
the SWE-family (gold patch against a real repo + test runner) is the highest
-value next step, since those are the columns most likely to be bought next.
