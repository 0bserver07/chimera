# Playbook 14 — Release discipline: don't inflate the version

The version number is a **claim about maturity**, not a changelog counter.
Chimera's policy, set explicitly by the project owner:

## The rules

0. **SUB-VERSIONS, not a marching patch digit.** The third digit is NOT a
   batch counter. After `0.9.2`, further work ships as `0.9.2.1`, `0.9.2.2`,
   … — a fourth component (PEP 440 accepts it; `uv`/PyPI sort it correctly).
   The dev version between releases is therefore `0.9.2.N.dev0`, never
   `0.9.3.dev0`.
   **`0.9.3` is not reachable by accumulating batches.** Moving the third
   digit requires an explicit decision by the project owner, for a reason
   stated out loud — not "we shipped a lot since 0.9.2."
   *This rule exists because the third digit was marched 0.9.0 → 0.9.1 →
   0.9.2 inside three weeks. That is inflation. It stops here.*
1. **Stay in 0.9.x for a long time.** Feature accumulation does not justify a
   minor bump, and does not justify a patch bump either — see rule 0.
2. **1.0 is reserved for a major breakthrough.** Not "lots of good releases" —
   a capability step-change that redefines what the framework is. Default
   answer to "is this 1.0?" is *no*.
3. **Slow the cadence.** Not every improvement needs a release. Batch changes,
   let them settle on master, release when a coherent story has accumulated.
   Shipping is a deliberate act, not a reflex after each merged branch.
4. **A spec is not a feature.** `docs/specs/*.md` files are plans with zero
   code. Release notes, docs, and status reports say "designed / spec'd" until
   the code exists, is tested, and has been exercised against a real model.
5. **No inflated claims anywhere the version travels.** Release notes cite
   verified numbers (test counts, benchmark scores with their `data/*.json`
   provenance) — the same standard as the benchmark scorecards.

## The changelog habit

`CHANGELOG.md` is where batching becomes visible. The mechanics:

1. **Entries land with the work.** When a batch of commits merges to master,
   append curated entries (with commit shas as receipts) to the
   `## Unreleased` section — grouped Added / Fixed / Changed / Deprecated.
   Curate, don't enumerate: the changelog tells the story; `git log` holds
   the inventory.
2. **Batches get names.** Every released version block is
   `## X.Y.Z — YYYY-MM-DD — <name>` where the name states the theme
   ("the TUI multiplexer", "the daily driver"). A proposed name may sit in
   the Unreleased section as a comment until release time.
3. **A release = roll the accumulator.** Cutting X.Y.Z renames `Unreleased`
   to the versioned, dated, named block and starts a fresh empty
   `Unreleased`. No release without the roll; no roll without the release.
4. **Same truth standard as the scorecards.** Numbers in changelog entries
   (test counts, benchmark scores) must trace to command output or
   `data/*.json` — an entry nobody can verify doesn't go in.

## What a release needs (unchanged gates)

- Full suite green locally (excluding the documented live-infra files),
  `ruff`, `mypy`, trademark scrubs, docs-sync.
- **CI-posture gate** (`bash scripts/ci_posture_check.sh`): CI installs no
  `tui` extra — replicate its exact env + cold-cache mypy + its pytest
  invocation locally before the push. Added after two red CI runs on the
  0.9.1 batch proved local-green ≠ CI-green.
- **ALL-EXTRAS run** (`uv sync --all-extras` then the full suite). The two
  gates above are both *lower* postures, and a test gated behind
  `pytest.importorskip` for a dep neither installs is checked by **neither** —
  it is skipped in one and skipped in the other, so it can be broken for
  months while every gate stays green. Found on the 0.9.2.2 cut:
  `tests/otter/test_server_tls.py` had raised `TypeError` since `e5d4d725`
  added a `pidfile_prefix` argument its fake never accepted, because the module
  sits behind `importorskip("cryptography")` and CI installs no `cryptography`.
  A release is the one moment worth paying for the highest posture.
  *Corollary for the numbers:* quote the test count from the posture you name.
  The 0.9.2.2 tree reports **11,018 passed / 79 skipped** under all extras and
  **10,501 passed / 133 skipped** under the CI posture — a 517-test gap that is
  entirely `importorskip`, not a smaller suite. A bare test count without its
  posture is not a verifiable number.
- README/status refreshed to match reality.
- Publish pipeline per `docs/playbooks/` release notes and
  the release-ops history (tag → CI publish → uvx verification).
- **Post-publish, RUN the thing — importing it is not verification.** The
  0.9.2.2 check was a clean venv outside the repo confirming
  `chimera --version`, `import chimera`, and that the fixes were in the wheel.
  All true, all green, and it exercised none of what a user does first. Within
  minutes of installing, a user hit a **5.1 s** time-to-first-prompt (all of it
  eagerly importing providers) and a `KeyboardInterrupt` traceback dumped on
  quit. Neither is reachable by importing.
  Minimum for the next release: launch the actual entry point, time it, and
  quit it.

  ```bash
  cd /tmp && python3 -m venv v && ./v/bin/pip install 'chimera-run[anthropic]'
  time ./v/bin/chimera code < /dev/null    # time-to-prompt
  ./v/bin/chimera code & sleep 6; kill -INT %1; wait   # quit must not traceback
  ```

  **Both lines, not just the first.** `< /dev/null` closes stdin, which raises
  `EOFError` — a different path from `SIGINT`. Measured on the 0.9.2.2 tree: the
  redirect exits 0 and silent, while an actual Ctrl+C dumps
  `KeyboardInterrupt`. A gate that only tests the easy exit reports green on a
  crash-on-quit, which is how this one shipped.

  Also verify on a machine that is **not** the dev box. The same release
  installed fine here and failed on a user's Linux host — a `pip` shim bound to
  a dead Python 3.8 while `python3` was 3.11.7, which reports as
  `(from versions: none)` and reads exactly like "the package was never
  published".

## Why this is written down

Version inflation is a ratchet: every unearned bump makes the next one
cheaper, and the number stops meaning anything. Keeping 0.9.x honest is what
makes an eventual 1.0 a signal instead of noise.
