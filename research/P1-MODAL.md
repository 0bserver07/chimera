# P1 — Modal Cloud Sandbox Environment (Wave 9)

**Status:** complete (uncommitted, per task constraint).

## Summary

Added `ModalSandboxEnvironment`, a new `Environment` implementation that
runs agent tool calls inside an ephemeral Modal container instead of the
local machine. Wired the ferret CLI to opt into it via
`--sandbox-backend modal`. The optional `modal` dep is gated behind a
new `[modal-sandbox]` extra and the env falls back gracefully (with a
stderr warning) when the package isn't installed.

## Deliverables

| Path | Kind | Notes |
|---|---|---|
| `chimera/env/modal_sandbox.py` | new | `ModalSandboxEnvironment(Environment)` + `_read_stream` helper. |
| `chimera/ferret/cli.py` | modified | New `--sandbox-backend local|modal` flag (default `local`) wired into `_run_print_mode`. |
| `pyproject.toml` | modified | Added `modal-sandbox = ["modal>=0.62"]` extra; added `modal.*` to mypy `ignore_missing_imports`; included it in the `all` extra. |
| `tests/env/test_modal_sandbox.py` | new | 32 tests: in-memory fallback, mocked-modal wiring (legacy `spawn_sandbox` + modern `Sandbox.create`), `_read_stream` helpers, CLI fallback path, `@pytest.mark.live` smoke gated by `pytest.importorskip("modal")`. |
| `docs/ferret/sandbox.md` | modified | Appended a "Cloud sandbox backend — `--sandbox-backend`" section with the `modal` recipe + a Python snippet. |

## Design choices

- **Optional dep.** Followed the same posture as `chimera/env/cloud.py`
  (httpx) and `chimera/env/docker.py` (docker): top-level `try/except
  ImportError` with a `_require_modal()` helper. `setup()` raises
  `ImportError` only when neither `modal` is importable nor a
  `modal_app` was injected.
- **In-memory fallback.** Mirrors `DockerEnvironment`'s no-container
  path so unit tests don't need the SDK. `read_file` / `write_file` /
  `list_files` / checkpoint/restore all work against `self._files`.
- **SDK shape detection.** Both modern (`modal.Sandbox.create`) and
  legacy (`app.spawn_sandbox`) APIs are supported. The modern path is
  preferred when present.
- **Stream normalisation.** `_read_stream` accepts `str`, `bytes`,
  iterables of chunks, and objects with a `read()` method. Never
  raises — output formatting failures must not crash an agent loop.
- **Checkpoints on live sandboxes raise `NotImplementedError`** (with a
  pointer to layering `GitEnvironment`). Mirrors `DockerEnvironment`.

## CLI wiring

```python
# chimera/ferret/cli.py — _run_print_mode
sandbox_backend = getattr(args, "sandbox_backend", "local") or "local"
if sandbox_backend == "modal":
    try:
        from chimera.env.modal_sandbox import ModalSandboxEnvironment
        base_env = ModalSandboxEnvironment(workdir=cwd)
        base_env.setup()
    except ImportError as exc:
        print(f"[ferret] --sandbox-backend modal requested but modal is "
              f"unavailable ({exc}); falling back to local.", file=sys.stderr)
        base_env = LocalEnvironment(workdir=cwd)
        base_env.setup()
else:
    base_env = LocalEnvironment(workdir=cwd)
    base_env.setup()
```

The new flag is orthogonal to `--sandbox` (mode) and `--os-sandbox`
(OS-layer): you can run a `read-only` sandbox **inside** a Modal
container.

## Verification

```bash
$ uv run ruff check chimera/env/modal_sandbox.py chimera/ferret/cli.py \
                    tests/env/test_modal_sandbox.py
All checks passed!

$ uv run mypy chimera/env/modal_sandbox.py
Success: no issues found in 1 source file

$ uv run mypy chimera/ferret/cli.py
Success: no issues found in 1 source file

$ uv run pytest tests/env/test_modal_sandbox.py -q
............................s...                                         [100%]
31 passed, 1 skipped in 0.30s
# (the skipped test is the @pytest.mark.live smoke that requires a real
#  modal account; opt-in via `pytest -m live`)

$ uv run pytest tests/ferret/test_cli.py -q
........................                                                 [100%]
24 passed in 0.97s
```

## Constraints honored

- No commit, no push.
- No real Modal API calls — every test path uses `unittest.mock` or
  monkeypatches `chimera.env.modal_sandbox.modal` to `None`.
- `modal` remains an optional dep; importing
  `chimera.env.modal_sandbox` works on a vanilla install and returns
  the in-memory fallback environment.

## Follow-ups (not this task)

- Wire `ModalSandboxEnvironment` into otter's HTTP serve factory
  (currently only ferret's print-mode opt-in is exposed).
- Add a Modal-backed entry to the parity matrix
  (`docs/ferret/parity-matrix.md`) once the live smoke passes against
  a real account.
- Consider promoting checkpoint/restore on live sandboxes via Modal's
  volume-snapshot APIs when the SDK exposes them.
