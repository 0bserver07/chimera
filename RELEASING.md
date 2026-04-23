# Releasing `chimera-run`

This document describes how to cut a release of `chimera-run` to PyPI.

Releases are automated via `.github/workflows/publish.yml`, which runs on any tag matching `v*` and publishes using **PyPI Trusted Publishing** (OIDC, no API tokens required).

---

## One-Time Setup (PyPI side)

Before the first automated release works, a PyPI maintainer must register this GitHub repository as a Trusted Publisher.

1. Go to <https://pypi.org/manage/account/publishing/> (or to the project page once the package exists: <https://pypi.org/manage/project/chimera-run/settings/publishing/>).
2. Click **Add a new pending publisher** (first release) or **Add a new publisher**.
3. Fill in:
   - **PyPI project name:** `chimera-run`
   - **Owner:** `0bserver07`
   - **Repository name:** `chimera`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `release`
4. Save.

On the GitHub side, create an **Environment** named `release`:

1. Repo → Settings → Environments → **New environment** → `release`.
2. Optional but recommended: add required reviewers so a human approves the publish step.
3. No secrets need to be configured — Trusted Publishing is token-less.

---

## Release Steps

1. **Make sure `master` is green.** CI passes, tests green, docs build.
2. **Bump the version** in the two places it lives:
   - `pyproject.toml` → `project.version`
   - `chimera/__init__.py` → `__version__`
   (The workflow fails fast if the git tag does not match `pyproject.toml`.)
3. **Update `CHANGELOG.md`** with the new version and a summary of changes.
4. **Commit** the bump:
   ```bash
   git add pyproject.toml chimera/__init__.py CHANGELOG.md
   git commit -m "release: v0.3.1"
   git push origin master
   ```
5. **Tag and push:**
   ```bash
   git tag v0.3.1
   git push --tags
   ```
6. **Watch the workflow** at <https://github.com/0bserver07/chimera/actions/workflows/publish.yml>.
   - `build` job: `uv build` produces sdist + wheel, verifies tag matches version.
   - `publish-pypi` job: uploads to PyPI via OIDC.
   - `github-release` job: creates/updates a GitHub Release with artifacts attached.

---

## Post-Release

1. **Verify the package is live** on PyPI:
   ```bash
   pip index versions chimera-run
   pip install chimera-run==0.3.1
   python -c "import chimera; print(chimera.__version__)"
   ```
2. **Update `README.md` install block.** Remove any `pip install git+https://github.com/...` fallback lines and restore the standard:
   ```bash
   pip install chimera-run
   pip install "chimera-run[anthropic]"
   ```
3. **Announce** (if relevant): release notes on the GitHub Release page, docs site banner, any external channels.
4. **Bump to the next dev version** on `master` (optional, if using `X.Y.Z.dev0` conventions).

---

## Pre-Release Checklist

Before pushing a tag, confirm:

- [ ] `uv run pytest` passes locally (and CI is green on `master`).
- [ ] `uv run ruff check chimera/` is clean.
- [ ] `uv run mypy chimera/` is clean (or known failures are documented).
- [ ] `CHANGELOG.md` has an entry for the new version.
- [ ] `pyproject.toml` version and `chimera/__init__.py` `__version__` match the intended tag.
- [ ] Docs build (`cd site && pnpm install && pnpm build`) succeeds.
- [ ] `uv build` locally produces valid sdist + wheel in `dist/`.
- [ ] `uv run python -m twine check dist/*` (or equivalent) reports no issues, if run.
- [ ] No uncommitted changes in the working tree.
- [ ] The previous release is tagged and published (no skipped versions).
- [ ] PyPI Trusted Publisher is registered for this repo/workflow/environment (one-time, see above).
- [ ] GitHub Environment `release` exists (one-time).

---

## Rollback / Yank

PyPI does not allow deleting a released version, but you can **yank** a bad release:

1. Go to <https://pypi.org/manage/project/chimera-run/releases/>.
2. Click the bad version → **Options** → **Yank**.

Yanked versions stay installable by pinned requirements but are excluded from fresh installs. Then cut a new patch release (`v0.3.2`) with the fix.

---

## Notes

- **Do not** commit API tokens or add PyPI secrets to the repo. Trusted Publishing is token-less by design.
- **Tag pattern `v*`** is used by `publish.yml` only. The existing `ci.yml` triggers on `push` to `master` and `pull_request`, so there is no overlap.
- **`uv build`** is used intentionally (the project builds with hatchling via uv). Do not switch to `python -m build` without updating this doc.
