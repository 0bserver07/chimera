# Bench diagnosis — the `darklight1` Modal grid

Receipt under diagnosis: `data/modal-grid-darklight1-20260724-195209.json`
(agent `coding-agent`, model `glm-5.2[1m]`, n=50 per cell).

Two cells were held back from publication pending this diagnosis:

| Cell | Reported | Verdict |
|---|---|---|
| `humaneval-x` | 0/50 (0.0%), `status_counts {completed: 50}` | **Fabricated zero — grader bug. Fixed; the same 50 tasks re-run live at 50/50 (100%).** |
| `livecodebench-codegeneration` | 44/50 (88.0%) | **Not publishable as a LiveCodeBench score.** Measured on a non-representative slice under a lenient grading contract. |

The other two cells in the receipt (`aimo3` 44/50, `bigcodebench-instruct`
49/50) were outside this diagnosis and are not blessed here.

---

## Problem 1 — `humaneval-x` 0/50 with every task `completed`

### Verdict: harness gap, exactly as the playbook predicts. Not a score.

`docs/playbooks/13-live-bench-runs.md` and the measurement-integrity history in
`docs/benchmarks/modal-cloud-benches.md` both state the rule: a uniform-zero
column whose tasks all reached `completed` is a harness-gap signature, never a
measurement. It held here.

### Root cause

The Python grader concatenated the agent's **raw reply** onto the prompt stub
and executed it:

```python
program = "\n".join([task["prompt"], agent_output, task["test"]])
exec(program, {})
```

Two contracts collide at that line.

1. **HumanEval-X is a completion dataset.** Its `prompt` is a signature plus
   docstring with no body, and `canonical_solution` is a *bare, indented
   function body* meant to be appended to it.
2. **The matrix runner drives instructed chat agents.** `chimera/eval/matrix.py:169`
   appends `FINAL_ANSWER_CONTRACT` to every prompt, which asks for "the full
   code in one fenced code block". `chimera/eval/harness.py:188` hands the
   agent the bare stub and `chimera/eval/harness.py:203` hands the reply
   straight to `evaluate()`.

So the grader received `Here is the solution:\n\n```python\ndef f(...)...` and
executed the Markdown prose as Python. Every task died of `SyntaxError` before a
single assertion ran. The adapter never imported the shared normalizer even
though `chimera/eval/benchmarks/_code_extract.py` exists precisely for this and
its module docstring says every adapter executing `agent_output` **must** go
through it — the fix from `2c41ad1` reached `human_eval`, `humaneval_plus`,
`livecodebench` and `mbpp`, and missed `humaneval_x`.

### Reproduced offline against the real staged dataset

`~/.chimera/datasets/humaneval-x/python.jsonl`, 164 records, graded with a
**known-correct** answer (the dataset's own `canonical_solution`) in four
shapes:

| Answer shape | Before fix | After fix |
|---|---|---|
| A — bare indented body (upstream completion contract) | 164/164 | 164/164 |
| B — fenced full function + prose (**what the agent actually sends**) | **0/164** | 164/164 |
| C — unfenced full source | 164/164 | 164/164 |
| D — fenced bare body | **0/164** | 164/164 |

Shape B is the production shape. Shape A is the *only* shape the old tests fed
(`tests/eval/test_humaneval_x.py::TestEvaluate::test_python_in_process_pass`
passed `"    return a + b\n"`), which is why a whole suite stayed green while
the live column was structurally incapable of scoring above zero.

Leniency did not regress — after the fix, all four wrong-answer shapes still
grade 0/164: a wrong solution, an empty answer, an empty fence, and prose-only.

### A second bug found underneath: the shared extractor dedented code

`extract_code('```python\n    return sum(numbers)\n```')` returned
`'return sum(numbers)'` — the indentation was gone. The fence regex used a
greedy `\s*` to skip to the code, and `\s` includes the newline *and* the
leading spaces after it. Harmless for a whole module (first line at column 0),
fatal for any completion-shaped answer, which becomes an `IndentationError`.
This is why shape D also scored 0.

Fixed at `chimera/eval/benchmarks/_code_extract.py:25` by consuming only
horizontal whitespace (`[^\S\n]*`) plus at most one newline.

### Fix

- `chimera/eval/benchmarks/humaneval_x.py:186` — `_evaluate_python_in_process`
  now normalizes through `extract_code` (line 219), refuses an answer that
  extracts to nothing (measurement integrity — same guard `human_eval` and
  `livecodebench` already carry), and accepts **both** answer shapes (line 227):
  `prompt + body + test` for the completion contract, `source + test` for the
  full-module contract. Neither shortcut can create a false pass: all 164 of the
  dataset's `test` fields both define `check(...)` and call it, so a program
  that executes cleanly really did run the assertions (verified — 0/164 tasks
  pass with a no-op body).
- `chimera/eval/benchmarks/_code_extract.py:25` — indentation-preserving fence.

### Regression canary

`tests/eval/test_humaneval_x.py::TestKnownCorrectAnswerCanary` — the test this
class of bug always lacked. It pins that a known-correct solution grades as a
**pass** in every shape an agent produces (fenced full function, fenced bare
body, unfenced source, bare body) while wrong / empty / prose-only answers still
fail. Plus `tests/eval/benchmarks/test_mbpp_fence.py::test_extract_code_preserves_leading_indentation`
for the shared extractor.

### Live re-run (real money, real model)

Same agent, same model, same harness, same tasks — only the grader changed:

| Run | Result | Cost | Status |
|---|---|---|---|
| `darklight1` n=50 (broken grader) | **0/50 (0.0%)** | $0.3513 | `{completed: 50}` |
| smoke n=5 (fixed) | 5/5 (100%) | $0.0405 | `{completed: 5}` |
| **`hexfix1` n=50 (fixed)** | **50/50 (100.0%)** | **$0.3324** | `{completed: 50}` |

Receipt: `data/modal-grid-hexfix1-20260724-231500.json`. Live spend for this
diagnosis: **$0.3729**.

The two n=50 runs cost within 5% of each other ($0.3324 vs $0.3513) on the same
50 tasks. The agent was always doing full-price work and solving them; the
grader threw every answer away. **The published-zero error was 100 percentage
points.** This is the strongest possible confirmation of the playbook rule — a
clean uniform zero was not merely depressed, it was the exact inverse of the
truth.

### What is safe to publish

The **0/50 must never be published in any form.** The corrected n=50 column —
**100.0% (50/50), `{completed: 50}`, receipt
`data/modal-grid-hexfix1-20260724-231500.json`** — is EXACT by the page's own
definition and is safe to publish, with one caveat below.

Note the run label: only the dataset's Python
split is staged (`chimera/eval/datasets.py` fetches `humaneval-x/python.jsonl`),
so a cell labeled `humaneval-x` is HumanEval-X **Python**, not the 5-language
benchmark. The other four languages remain an unwired scaffold that returns
`False`; any multi-language claim would be false. If non-Python is ever staged,
those columns will be uniform zeros for exactly the reason this note documents,
and the generator gate below will now stop them.

---

## Problem 2 — `livecodebench` 44/50 (88.0%) vs the published ≥18.9% (33/175)

### Verdict: the new number must NOT be published as a LiveCodeBench score.

It is not inflated by a grading regression, and it is not fraudulent — but it
measures a **different, easier, adapter-friendly population** than the 175-task
run, under a grading contract that is not LiveCodeBench's. The old ≥18.9% is
also not a LiveCodeBench score: it is depressed by a structural harness gap
nobody had found. **Neither number is citable. They are not comparable to each
other and neither is comparable to published LiveCodeBench results.**

### Grading did not change between the runs

| File | Last change before `darklight1` | Date |
|---|---|---|
| `chimera/eval/benchmarks/livecodebench.py` | `0275ec3` empty-run guard | 2026-07-08 23:21 |
| " | `2c41ad1` shared fence extraction | 2026-07-05 15:54 |
| `chimera/eval/matrix.py` | `90f3bb4` per-task `status_counts` | 2026-07-09 08:23 |

`fullscore1` ran 2026-07-09 10:53. Every grading-relevant commit predates it,
and nothing has touched `livecodebench.py` since. The commits landing between
the two runs are TUI, interception-seam and budget work. **Grading is a
constant** — so the delta is entirely task population and error share.

### Finding A — `--limit 50` silently selects an adapter-friendly platform

The staged dataset is **exactly 175 tasks**, so `fullscore1` ran all of it and
`darklight1` took `tasks[:50]` (`chimera/eval/benchmarks/livecodebench.py:206`
is a contiguous head slice, not a sample). The file is **blocked by platform**,
not interleaved:

```
positional platform blocks: [('atcoder', 112), ('leetcode', 63)]
```

AtCoder occupies indices 0–111 and LeetCode 112–174. **Any `--limit N` with
N ≤ 112 can only ever return AtCoder tasks.** The n=50 cell is 50/50 AtCoder and
0/63 LeetCode; the n=175 cell is 112 AtCoder + 63 LeetCode.

### Finding B — the 63 LeetCode tasks are structurally ungradeable

Their test cases are `testtype: "functional"` with a `starter_code` class
method. The staging prompt at `chimera/eval/datasets.py:332` instructs the
agent: *"Complete the following starter code: ```python class Solution: def
zigzagTraversal(self, grid): ...```"*. The grader at
`chimera/eval/benchmarks/livecodebench.py:141` then does:

```python
env.run_command("python solution.py < _stdin.txt")   # compare stdout to expected
```

A `class Solution` file run as a script prints nothing. There is no code
anywhere that instantiates the class and calls the method. **The prompt asks for
one artifact and the grader executes a different one.** Confirmed empirically
with a known-correct solution:

| Task | Answer | Graded |
|---|---|---|
| AtCoder `abc387_b` | correct stdin/stdout program | **True** |
| LeetCode `3708` | correct `class Solution` — *the shape the prompt demands* | **False** |
| LeetCode `3708` | same logic re-shaped as a stdin script — *never requested* | **True** |

All 63/63 LeetCode tasks carry non-empty `starter_code`; 0/112 AtCoder tasks do.
So **36% of the fullscore1 column was incapable of passing**, capping that run
at 112/175 = 64.0% before the model wrote a line.

Restricting `fullscore1` to the gradeable population: 33/112 = **29.5%**, not
18.9%. That closes ~11 points of the ~69-point gap. It is the same class of
silent auto-fail as Problem 1 — and the cost-per-task proxy could never see it,
which is why `docs/benchmarks/modal-cloud-benches.md` reasoned "HIGH full-work
cost ⇒ low error share, so ~real". The cost proxy detects *errored* tasks; it
cannot detect tasks that ran perfectly and were graded against the wrong
contract.

### Finding C — the graded tests are the PUBLIC samples, printed in the prompt

`chimera/eval/datasets.py:313` stages `public_test_cases` only and explicitly
drops the `private_test_cases` payload. Published LiveCodeBench pass@1 is scored
on the *hidden* suite. Measured leakage:

| Slice | graded cases visible verbatim in the prompt | tasks where **every** graded case is visible |
|---|---|---|
| darklight1 `tasks[:50]` | 64/132 (48%) | 24/50 (48%) |
| AtCoder tail (62) | 104/170 (61%) | 39/62 (63%) |
| LeetCode (63) | 23/161 (14%) | 8/63 (13%) |
| all 175 | 191/463 (41%) | 71/175 (41%) |

For roughly half the n=50 slice the model can read every assertion it will be
graded on. This affects both runs, so it does not explain the jump — but it
means **neither number is a LiveCodeBench score**, and an 88% here is not
comparable to any published LiveCodeBench figure.

### Finding D — slice difficulty does not explain the remainder, and errors are unmeasurable

Between the darklight1 head slice and the AtCoder tail the two difficulty
proxies disagree: the head has *fewer* contest-easy problem slots (22/50 = 44%
letters A–C vs 34/62 = 55%) but *fewer* `hard`-labelled tasks (22/50 = 44% vs
38/62 = 61%). No clean easy-slice artifact, but no evidence of comparability
either.

The unmeasurable residual is `fullscore1`'s error share. That cell is
`status: partial_error` with **no `status_counts`** (the run predates
`90f3bb4`), so errored tasks are counted as misses and cannot be separated. The
repo has direct precedent for this dominating a column: the same `fullscore1`
run reported mbpp at ≥35.4% and the clean re-run returned **99.1%** (+64 points,
`docs/benchmarks/modal-cloud-benches.md`). A large error share in the AtCoder
population is entirely consistent with 29.5% → 88%, and there is no data left to
rule it in or out.

Cost per task is comparable and rules out "the new run did less work":
`fullscore1` $9.933/175 = 5.68¢, `darklight1` $2.263/50 = 4.53¢. Both runs paid
for real generation.

### What is safe to publish

- **Do not publish 88% as `livecodebench`.** It is the pass rate of an AtCoder
  stdin/stdout head slice on public sample tests. If it is ever quoted it must
  be labelled in full: *LiveCodeBench v6 codegeneration, AtCoder subset,
  first-50 contiguous slice, graded on public sample tests only, n=50* — and
  even then it is a harness-local metric, not a benchmark result.
- **Retire ≥18.9% rather than keep citing it.** It is worse than a lower bound:
  36% of its denominator could not pass under any answer. **DONE 2026-07-25 —
  the owner approved the retraction.** The figure is withdrawn from
  `docs/benchmarks/observatory.md` (+ site mirror),
  `docs/progress/benchmark-matrix.md`, `docs/benchmarks/modal-cloud-benches.md`
  and the 0.9.1 release notes. It is not merely deleted: `RETRACTED` in
  `scripts/render_observatory.py` is a registry keyed by benchmark, so the
  generator renders `⊘ RETRACTED` plus the reason instead of any score, drops
  the benchmark from the "reproduce this page" commands, and annotates its ✓
  marks in the n=1 breadth grid. **A future run of this adapter therefore
  cannot republish a number** — removing the registry entry is the deliberate
  act that re-enables it, and must not happen before the adapter is fixed
  *and* re-canaried.
- **The blocking work before any LiveCodeBench number is citable:** wire a
  functional-test path for `testtype: "functional"` (instantiate the starter
  class and call the method with the parsed args), and stage the private test
  cases. **DONE 2026-07-25 for the functional path** — `evaluate()` dispatches
  on the task's own `testtype` and grades LeetCode tasks by calling the entry
  point named in `starter_code`; all 63 now have a derivable entry point and
  are gradeable. **STILL OPEN: staging the private tests.** That one is
  disqualifying on its own — with only public samples staged, the score
  measures copying as much as solving — so the column remains RETRACTED.
- **`--limit` on this dataset is unsound** and should shuffle with a fixed seed
  or stratify by platform. A contiguous head slice of a platform-blocked file is
  not a sample. **DONE 2026-07-25** — `_stratified_head` round-robins across
  platforms, deterministically (no RNG, so a run is reproducible from its
  arguments): `--limit 50` is now 25 AtCoder + 25 LeetCode, was 50 + 0.

---

## Generator hardening

`scripts/render_observatory.py` aborted on an `error`-status cell claiming
passes, but a `0/50` cell with `{completed: 50}` would have rendered as a real
**0.0%**. `_uniform_zero_note` now makes that abort generation too: a cell of 5+
tasks that all reached `completed` and passed none is the harness-gap signature,
and the message points at the playbook. Cells below 5 tasks are exempt
(sampling noise) and so are zeros already explained by errors or budget
exhaustion — `lint-loop`'s honest `0/1 budget_exhausted` rows in
`data/matrix-full-glm52.json` still render.

Tests: `tests/scripts/test_render_observatory.py::test_uniform_zero_clean_cell_aborts`,
`::test_uniform_zero_without_status_counts_aborts`,
`::test_uniform_zero_gate_spares_honest_zeros`.

Neither disputed number was ever on the page: `darklight1` matches none of the
generator's `DEFAULT_PATTERNS`. The "How to read these numbers" section gains a
sentence documenting the new abort, **but the page was deliberately not
regenerated in this worktree.**

`data/modal-grid-observatory1-20260723-234334.json` is untracked at this base
commit and absent here, while the committed page renders from it — so
`render_observatory.py --check` already exits 1 on the pristine tree (verified
by stashing the script change and re-running), and regenerating here would
*delete* the entire depth-matrix section. CI does not run the freshness gate.

**Owner action:** run `uv run python scripts/render_observatory.py` once in a
tree that holds every receipt, to pick up the new sentence without dropping the
depth matrix.

---

## Operational note — the Modal "outage" that was not one

Every Modal connection from the repo's default venv interpreter
(uv-managed CPython **3.12.8**) dies instantly with `[Errno 9] Connect call
failed`, which reads exactly like a Modal outage. It is not. Measured on this
machine, connecting to `api.modal.com` (54.163.156.253:443):

| Interpreter | Source | `getaddrinfo` | TCP connect | TLS |
|---|---|---|---|---|
| 3.12.8 | uv-managed | OK | **`[Errno 9] Bad file descriptor`** | — |
| 3.11.7 | uv-managed | OK | OK | OK |
| 3.12.7 | homebrew | OK | OK | OK |
| 3.13.14 | homebrew | OK | OK | OK |

Workaround (verified working — it is what ran the live cells in this note):

```
uv run --python 3.13 --extra modal-sandbox --extra anthropic modal run …
```

**Root cause not established, and the obvious theory is wrong.** It is not
asyncio-specific (blocking sockets fail the same way), not TLS-specific (plain
TCP fails), and not a uv-versus-system-Python split (uv's own 3.11.7 works). The
same 3.12.8 interpreter *does* reach `1.1.1.1:80` successfully, so its socket
layer is not simply broken — the failure is destination-specific *and*
interpreter-specific, which points at environmental interposition (a network
sandbox or proxy) rather than a CPython or OpenSSL defect. Cheap next probe if
it recurs: `uv python install --reinstall 3.12` to rule out a corrupt download.
Repinning `.python-version` off 3.12 is **not** recommended on this evidence.
