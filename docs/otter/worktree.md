# `chimera otter worktree` — branch isolation

`worktree` lets you peel off a session into an isolated `git worktree`
so otter can experiment on a parallel checkout without disturbing your
current branch.

## Usage

```bash
chimera otter worktree create <NAME> [--worktree-branch BRANCH] [--worktree-repo PATH]
chimera otter worktree list  [--worktree-json]
chimera otter worktree remove <NAME> [--worktree-force]
```

Worktrees materialize under `~/.chimera/worktrees/<name>/`, on a branch
named `otter/<name>` by default. The manifest at
`~/.chimera/worktrees/index.json` lists every worktree otter created so
`list` is fast and survives across shells.

## Examples

Create a sandbox for a refactor:

```bash
chimera otter worktree create refactor-auth
# → /Users/you/.chimera/worktrees/refactor-auth (branch otter/refactor-auth)
```

Reuse an existing branch:

```bash
chimera otter worktree create wip --worktree-branch wip/draft
```

Show every worktree as JSON:

```bash
chimera otter worktree list --worktree-json
```

Drop one (force lets you bypass the "uncommitted changes" guard):

```bash
chimera otter worktree remove refactor-auth --worktree-force
```

## How it works

`chimera/otter/worktree.py` shells out to `git worktree add` /
`git worktree remove`, persists the entries to
`~/.chimera/worktrees/index.json`, and records:

* `name` — manifest key.
* `path` — absolute filesystem path.
* `branch` — checked-out branch (defaults to `otter/<name>`).
* `repo` — source repo the worktree was cut from.
* `created_at` — ISO-8601 UTC timestamp.

The module composes [`chimera.env.git_env.GitEnvironment`][gitenv] for
in-process synthesis flows; the CLI dispatcher calls plain `git`
because worktree manipulation is one-shot and benefits from the user's
existing git config.

[gitenv]: https://0bserver07.github.io/chimera/api/env/#gitenvironment

## Trademark hygiene

This subcommand is plain git terminology — no upstream brand is named
in source or docs. The trademark scrub
(`scripts/otter_trademark_scrub.sh`) covers `chimera/otter/worktree.py`
and this file.
