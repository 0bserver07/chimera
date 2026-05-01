---
title: Shell Completion
description: Tab-complete subcommands and flags for the `chimera` CLI in bash, zsh, and fish.
---

# Shell Completion

Chimera ships a built-in generator for bash, zsh, and fish completion scripts.
Run `chimera completion <shell>` and append the output to your shell's rc
file. Completions are produced by walking the live argparse tree, so any
subcommand registered in `build_parser()` — including all seven coding-agent
sub-CLIs (`mink`, `otter`, `ferret`, `weasel`, `shrew`, `stoat`, `badger`) —
shows up automatically without a separate registry to keep in sync.

## Install

### bash

```bash
chimera completion bash >> ~/.bashrc
# Reload your shell, or:
source ~/.bashrc
```

The generated script defines a `_chimera_completions` function and binds it
to the `chimera` command via `complete -F`. Tab-completion at position 1
suggests the registered subcommands; at later positions it suggests the
flags declared on that subcommand's parser.

### zsh

```bash
chimera completion zsh >> ~/.zshrc
# Reload your shell, or:
source ~/.zshrc
```

The script begins with `#compdef chimera` and uses `_arguments` for each
subcommand, so flags appear in zsh's standard menu-select completion. If
you keep completion fragments in a separate dir (e.g. `~/.zfunc/`), write
the output there and ensure the dir is on your `fpath`.

### fish

```bash
mkdir -p ~/.config/fish/completions
chimera completion fish > ~/.config/fish/completions/chimera.fish
```

Fish auto-loads completion files from the `completions/` directory on
startup — no `source` step required. The generator emits one
`complete -c chimera ...` line per subcommand and per flag, gated with
`__fish_use_subcommand` and `__fish_seen_subcommand_from`.

## Filtering by sub-CLI

If you only ever invoke one of the coding-agent CLIs, narrow the script
with `--cli`:

```bash
chimera completion bash --cli mink   >> ~/.bashrc
chimera completion zsh  --cli otter  >> ~/.zshrc
chimera completion fish --cli ferret > ~/.config/fish/completions/chimera.fish
```

Accepted values: `all` (default), `mink`, `otter`, `ferret`, `weasel`,
`shrew`, `stoat`, `badger`. The `completion` subcommand itself is always
included so `chimera completion <tab>` keeps working in a filtered shell.

## How it works

`chimera/cli/completion.py` walks `parser._subparsers._actions`, collects
each subparser's option strings (excluding `-h`/`--help`), and renders one
of three shell-specific templates:

- **bash:** a `_chimera_completions()` function that switches on
  `${COMP_WORDS[1]}` to pick the right candidate set, wired up via
  `complete -F`.
- **zsh:** a `compdef` block that uses `_arguments` per subcommand and
  `_describe` for the top-level subcommand menu.
- **fish:** a flat list of `complete -c chimera -f` directives — one per
  subcommand, one per top-level flag, and one per per-subcommand flag,
  gated by the standard fish helpers.

Output is sorted by subcommand name for deterministic diffs across runs.

## Updating completions after upgrade

Re-run the generator and replace the relevant block in your rc file
whenever you upgrade chimera or add a plugin that registers new
subcommands. There's no autoreload — your shell only re-reads the rc file
on session start.
