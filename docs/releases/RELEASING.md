---
title: Releasing chimera-run
description: Release procedure for the chimera-run package — version bump, tag, publish via GitHub Actions Trusted Publisher (OIDC).
---

# Releasing chimera-run

This document is the canonical playbook for cutting a `chimera-run` release.
The PyPI distribution name is **`chimera-run`** (not `chimera-ai`); the
Python import path stays `import chimera`.

## TL;DR

```bash
# 1. Update version
$EDITOR pyproject.toml  # bump [project].version
$EDITOR chimera/__init__.py  # bump __version__ to match
$EDITOR CHANGELOG.md  # add release notes

# 2. Author release notes
$EDITOR docs/releases/<new-version>.md

# 3. Sanity build + lint locally
uv run ruff check chimera/
uv run mypy chimera/
uv run pytest -q
uv build
uv run --with twine twine check dist/*

# 4. Commit + tag (NEVER amend)
git add -p && git commit -m "release: chimera-run vX.Y.Z"
git tag -a vX.Y.Z -m "chimera-run vX.Y.Z"

# 5. Push tag — Actions does the rest
git push origin master
git push origin vX.Y.Z
```

The `v*` tag push triggers `.github/workflows/publish.yml` which
builds, verifies the tag matches `pyproject.toml`, uploads to PyPI via
Trusted Publishing (OIDC), and attaches the wheel + sdist to a GitHub
Release.

## Prerequisites (one-time setup)

### PyPI Trusted Publisher

Trusted Publishing eliminates the need to mint an API token and store
it as a GitHub secret. It is configured **once** in the PyPI project
settings:

1. Sign in at <https://pypi.org/manage/project/chimera-run/settings/publishing/>.
2. Add a Trusted Publisher with:
   - Owner: `0bserver07`
   - Repository: `chimera`
   - Workflow file: `publish.yml`
   - Environment: `release`
3. Save.

Until this is configured the publish job will exit with
`OIDCException: trust relationship not established`. The build job
itself (`uv build`, `twine check`) succeeds independently and is a
useful smoke test even without the trusted publisher in place.

### GitHub environment

`.github/workflows/publish.yml` references an environment named
`release` for the publish job. Create it under
**Settings → Environments → release** if it does not already exist.
The environment can stay empty — it exists so the publish step can be
gated by required reviewers, branch rules, or wait timers when the
project graduates beyond alpha.

## Pre-flight checklist

Before tagging, work the checklist from top to bottom. Stop at the
first item that fails — none of these are "soft" warnings.

### Version + metadata

- [ ] `pyproject.toml` `[project].version` matches the new tag
      (without the leading `v`). The publish workflow re-checks this
      and aborts on mismatch.
- [ ] `chimera/__init__.py` `__version__` matches.
- [ ] `pyproject.toml` `[project].name` is `chimera-run` (not
      `chimera-ai`). Trademark + memory rule.
- [ ] `[project].license = {text = "MIT"}` — verified.
- [ ] `[project].classifiers` covers Python 3.11/3.12/3.13.
- [ ] `[project].urls` carries `Homepage`, `Documentation`,
      `Repository`, `Issues`, `Changelog`.

### Optional dependency groups

- [ ] `[project.optional-dependencies]` declares `anthropic`,
      `openai`, `browser`, `remote`, `mink`, `notebook`, `mcp`,
      `ssh`, `tui`, `modal-sandbox`, `function_synthesis`,
      `function_synthesis_s3`, `function_synthesis_compile`,
      `function_synthesis_transformers`, `function_synthesis_onnx`,
      `dev`, and an `all` aggregator.
- [ ] `all` references each user-facing extra. Aggregators that
      bundle every optional dep should be expanded before adding new
      extras.

### Public API surface

Anything that ships in `chimera/__init__.py`'s `_LAZY_ATTRS` is part
of the v1.0 contract. New top-level names should be intentional, not
accidental. Spot-check that headline symbols still import:

```bash
uv run python -c "
import chimera
for name in ('Agent', 'CodingAgent', 'create_provider', 'AgentLoader',
             'EventSourcedSession', 'PluginManager'):
    obj = getattr(chimera, name)
    print(f'OK: chimera.{name} -> {obj!r}')
"
```

### Build + verify

```bash
rm -rf dist/                  # avoid stale chimera_ai-0.1.0 artifacts
uv build                      # sdist + wheel
uv run --with twine twine check dist/*    # PASSED on both
```

The wheel must be `chimera_run-X.Y.Z-py3-none-any.whl`. If it comes
out as `chimera_ai-...`, the project name is wrong in
`pyproject.toml`.

### Tests + lint

```bash
uv run ruff check chimera/
uv run mypy chimera/
uv run pytest -q
bash scripts/all_trademark_scrub.sh
```

CI runs the same matrix on Python 3.11/3.12/3.13. A green local run
is necessary but not sufficient — wait for the post-push CI to also
go green before tagging.

### Docs + release notes

- [ ] `docs/releases/<X.Y.Z>.md` exists with frontmatter + summary.
- [ ] `CHANGELOG.md` has a top entry for this release.
- [ ] `README.md` install instructions reference `chimera-run` (the
      PyPI name) rather than `chimera-ai`.

## Tagging

```bash
# Annotated tag — required by publish.yml
git tag -a vX.Y.Z -m "chimera-run vX.Y.Z"
git push origin master
git push origin vX.Y.Z
```

Once the tag is pushed:

1. `.github/workflows/publish.yml` triggers on `push` of any `v*` tag.
2. The `build` job verifies tag-vs-pyproject parity, runs `uv build`,
   uploads `dist/` as a workflow artifact.
3. The `publish-pypi` job downloads the artifact and runs
   `pypa/gh-action-pypi-publish@release/v1` against the `release`
   environment, using OIDC.
4. The `github-release` job downloads the artifact again and creates
   (or updates) a GitHub Release with `generate_release_notes: true`,
   attaching the wheel + sdist.

Watch the run at <https://github.com/0bserver07/chimera/actions> and
verify on PyPI: <https://pypi.org/project/chimera-run/>.

## Post-release

- [ ] `pip install chimera-run==X.Y.Z` from a clean venv works.
- [ ] `pip install 'chimera-run[anthropic]'==X.Y.Z` resolves the
      anthropic extra.
- [ ] `chimera --version` reports the new version.
- [ ] Open a follow-up PR bumping `pyproject.toml` to `X.Y.(Z+1).dev0`
      so subsequent commits don't claim the just-released version.

## If something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Workflow fails at "Verify tag matches pyproject.toml" | Forgot to bump `pyproject.toml` before tagging | Delete the tag (`git tag -d`, `git push origin :refs/tags/...`), bump version, re-tag. |
| `OIDCException` during publish | Trusted Publisher not registered, or the `release` environment is missing | Configure on PyPI as above; re-run the failed job from the Actions UI. |
| `403 Forbidden` from PyPI | Project name mismatch — uploading `chimera_ai` to a `chimera-run` project | Confirm `[project].name` in `pyproject.toml`. |
| Twine rejects metadata | Long-description rendering broken | `uv build && uv run --with twine twine check dist/*`; preview the README at <https://github.com/pypa/readme_renderer>. |
| Dist contains stale `chimera_ai-*` files | `dist/` not cleaned before build | `rm -rf dist/ && uv build`. |

## Rolling back

PyPI does **not** allow re-uploading a version, even after deletion.
A botched release means cutting `X.Y.(Z+1)` immediately. Yank the
broken release on PyPI to discourage installs:
<https://pypi.org/help/#yanked>.
