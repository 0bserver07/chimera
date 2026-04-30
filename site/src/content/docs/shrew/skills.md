---
title: Shrew skills
description: The bundled skill markdown set — what each file contains, where the skills live on disk, and how to add or override your own.
---

# Skills

Shrew ships a small, opinionated set of skill markdown files. They
live alongside the code in `chimera/shrew/skills/` and are mounted
into the agent's system prompt at startup. The skill set is the
single biggest reason shrew handles small local models gracefully:
the curated knowledge / protocols / tools blocks stand in for the
implicit reasoning that frontier models do for free.

This page covers what skills are, what shrew bundles, and how to
extend the set.

## What is a skill?

A skill is a markdown file with a small YAML-style frontmatter
block followed by a body. Example shape:

```markdown
---
name: scaffold-model-fit
description: Match the harness to the model's capability ceiling.
triggers: ["why is the agent failing", "tool soup", "scaffold"]
---
## Scaffold-model-fit

A coding agent has two halves: the model that does the reasoning
and the scaffold (tools, prompts, control flow) it operates inside.
Frontier models forgive a sloppy scaffold; small local models do
not...
```

Frontmatter fields:

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Slug-style id (lowercase, hyphenated, ≤64 chars). |
| `description` | yes | One-line summary (≤1024 chars). |
| `triggers` | no | List of phrases that hint when the skill is relevant. |

The body is plain markdown. There is no length cap, but skill
bodies should stay tight — every byte of skill content is a byte
out of the model's effective context budget. Aim for 30-80 lines.

## Where skills live

Three locations, layered in this precedence order (later wins):

1. **Bundled** — `chimera/shrew/skills/{knowledge,protocols,tools}/`
   inside the chimera install. Read-only at runtime; ship-with-the-CLI.
2. **User overlay** — `~/.shrew/skills/{knowledge,protocols,tools}/`.
   Optional, opt-in. Files placed here override bundled skills with
   the same `name`.
3. **Project overlay** — `.shrew/skills/{knowledge,protocols,tools}/`
   inside the working directory. Honored when shrew is launched from
   a project that ships its own skill files. Same precedence rule.

Discovery walks each root, reads every `*.md` file under the three
category subdirectories, and de-duplicates by `name`. The implementation
is in [`chimera/shrew/skills/__init__.py`](https://github.com/0bserver07/chimera/tree/master/chimera/shrew/skills).

To skip the bundled skill set entirely (smaller prompt; useful when
benchmarking or running a frontier model):

```bash
chimera shrew --no-skills -p "..."
```

## What shrew bundles

The shipped catalogue, grouped by category. All paths are relative
to `chimera/shrew/skills/`.

### `knowledge/` — concepts the model needs to do good work

| File | Skill name | One-line summary |
|---|---|---|
| `scaffold_model_fit.md` | `scaffold-model-fit` | Match the harness to the model's capability ceiling. |
| `context_window_discipline.md` | `context-window-discipline` | Use context efficiently — read on demand, summarise on schedule. |
| `escalation_signals.md` | `escalation-signals` | Recognise when to ask the user vs. push through. |
| `python_idioms.md` | `python-idioms` | Idiomatic Python the small model should default to. |

### `protocols/` — decision flows for common situations

| File | Skill name | One-line summary |
|---|---|---|
| `edit_before_write.md` | `edit-before-write` | Prefer edit-style tools over rewriting whole files. |
| `error_recovery.md` | `error-recovery` | What to do when a tool call fails. |
| `one_focused_question.md` | `one-focused-question` | Ask exactly one thing at a time when blocked. |
| `test_first_python.md` | `test-first-python` | Write a failing test, then make it pass. |

### `tools/` — how to use the agent's tools effectively

| File | Skill name | One-line summary |
|---|---|---|
| `core_tools.md` | `core-tools` | The four-tool minimum (Read / Write / Edit / Bash). |
| `grep_vs_ls.md` | `grep-vs-ls` | When to enumerate, when to search. |
| `multi_file_edits.md` | `multi-file-edits` | How to thread changes across multiple files. |

The exact text of each skill lives in source. Read the files
directly rather than trying to match a doc summary to the
implementation; the source is the source of truth.

## How skills are wired into the prompt

At session start, shrew calls `discover_shrew_skills()` and renders
the resulting list with `format_shrew_skills_for_prompt()`. The
output is a small markdown index injected at the bottom of the
system prompt:

```markdown
## Shrew skills

### knowledge
- **scaffold-model-fit** — Match the harness to the model's capability ceiling.
- **context-window-discipline** — Use context efficiently...

### protocols
- **edit-before-write** — Prefer edit-style tools over rewriting whole files.
...

### tools
- **core-tools** — The four-tool minimum (Read / Write / Edit / Bash).
...
```

The **bodies** of the skills (the markdown after the frontmatter)
are not all dumped into the system prompt. The index gives the
model a recall handle (the names + descriptions); the model can
then reference the skill by name when it needs the detail.

This pattern keeps the prompt budget bounded as the catalogue
grows. The full bodies are still on disk and can be retrieved by
the agent reading `chimera/shrew/skills/<category>/<name>.md` like
any other file.

## Adding your own skill

1. Pick a category. If a skill is "background you'd hand to a new
   coder", it's `knowledge/`. If it's "a recipe for handling a
   specific situation", it's `protocols/`. If it's "how to use a
   specific tool well", it's `tools/`.
2. Write the markdown file. Keep the frontmatter shape identical
   to the bundled skills. Pick a unique `name` (override an
   existing skill by reusing its name).
3. Drop it under `~/.shrew/skills/<category>/<name>.md` or
   `<project>/.shrew/skills/<category>/<name>.md`.
4. Restart the shrew session. The next prompt assembly will pick
   it up automatically.

There is no registration step, no manifest. Drop the file and
it's loaded.

## Skill design tips for small models

Small models read every word literally. The skills that work best
share a few traits:

- **Lead with the rule, not the explanation.** Put the actionable
  instruction in the first sentence. Save the rationale for after.
- **Use ordered numbered lists.** Small models follow numbered
  steps better than paragraphs of prose.
- **Show negative examples.** "Don't do this" with a one-line
  example beats abstract descriptions.
- **Reference filenames as paths, not concepts.** `chimera/shrew/cli.py`
  is parsed as a path; "the shrew CLI module" is parsed as a
  pronoun the model has to resolve.
- **Avoid hedging.** "Probably" and "usually" make small models
  ask follow-up questions. "If X then do Y" is direct.

Skills that work well for frontier models often **regress** on
small models. The scaffold-fit lesson cuts both ways: too little
guidance and the small model flounders; too much and it loses
track of the actual task.

## Disabling individual skills

There is no per-skill kill-switch in the CLI today. Three
workarounds:

1. **Override with an empty body.** Place a file with the same
   `name` and a one-line description in your user / project
   overlay; the bundled skill is shadowed.
2. **Drop the whole set.** `--no-skills` disables every skill in
   one shot.
3. **Filter at runtime.** Embedders using the SDK can pass a
   custom skill list to the system-prompt assembler. See
   [`docs/weasel/sdk.md`](../weasel/sdk.md) for the embedding shape;
   shrew shares weasel's SDK.

A flag for fine-grained skill selection (`--skills knowledge.scaffold-model-fit,protocols.edit-before-write`)
is on the post-release roadmap.

## See also

- [`extensions.md`](extensions.md) — `scaffold_fit` is a separate
  layer that wraps the system prompt with a reasoning scaffold;
  skills are the recall index, scaffold_fit is the per-turn frame.
- [`quickstart.md`](quickstart.md) — `--no-skills` flag.
- [`parity-matrix.md`](parity-matrix.md) — skill-set parity status
  vs. the upstream small-model coding agent's skill catalogue.
- The bundled skills themselves under
  [`chimera/shrew/skills/`](https://github.com/0bserver07/chimera/tree/master/chimera/shrew/skills).
