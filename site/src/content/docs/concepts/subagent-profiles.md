---
title: "Sub-agent Profiles"
description: "The four default sub-agent profiles — planner, researcher, executor, reviewer — and how the dispatch router picks one."
---

Chimera ships four built-in sub-agent profiles that cover the canonical phases of a coding task. Each profile is a markdown file with YAML frontmatter under `chimera/agents/presets/subagents/`. The dispatch router (in `chimera/agents/dispatch/`) inspects the user's request and routes to the right profile based on its declared `triggers`.

| Profile | Permissions | Loop | Max steps | Tools |
|---|---|---|---|---|
| `planner` | `read_only` | `plan_execute` | 30 | read, search, list_files, repo_map |
| `researcher` | `read_only` | `react` | 40 | read, search, list_files, repo_map, web_fetch |
| `executor` | `auto_approve` | `react` | 60 | read, write, edit, bash, search, list_files, test, git, replace_in_file, verify, repo_map |
| `reviewer` | `read_only` | `react` | 30 | read, search, list_files, git, repo_map |

## planner

> Subagent that produces step-by-step plans without executing.

**Triggers**: `plan`, `design`, `propose`, `blueprint`, `architecture`

The planner inspects the codebase using read-only tools and produces a plan another sub-agent (typically `executor`) can carry out.

Operating rules:

- Does **not** call edit / write / bash / git tools — its toolset is read-only by configuration.
- Always finishes with an explicit confirmation prompt (`"Approve this plan? (y/n)"`).
- Cites concrete file paths and function names (`path/to/file.py:lineno`).
- Plans should be small and reversible. Large requests get a phased plan.

## researcher

> Read-only subagent that gathers context from the codebase and the web.

**Triggers**: `research`, `investigate`, `explain`, `why`, `summarize`, `summarise`

The researcher surfaces accurate information from the user's codebase, git history, and external documentation. The only profile with `web_fetch` in its toolset.

Operating rules:

- Read-only by configuration. Suggests follow-up actions in plain prose.
- Cites every claim with a source: file path + line range, search query, or URL.
- Surfaces contradictions explicitly when two sources disagree.
- Prefers breadth-first exploration: list candidate files, narrow, then dig in.

## executor

> Full-tool subagent that carries out an approved plan.

**Triggers**: `execute`, `implement`, `build`, `run`, `fix`, `apply`

The executor has the full edit / write / bash / git toolset and `auto_approve` permissions. It is the only sub-agent that mutates the workspace.

Operating rules:

- Treats the incoming plan as the contract. Surfaces deviations before acting.
- Runs the project's tests after every batch of edits. Failure means the change is not done.
- Makes the smallest possible change. No unrelated refactors.
- Inspects diffs before committing. Never amends or force-pushes.
- Pauses for user approval on destructive bash, network calls, etc.

## reviewer

> Subagent that reviews changes (read + git, no edits, no exec).

**Triggers**: `review`, `critique`, `audit`, `check`, `lgtm`

The reviewer inspects work another sub-agent produced and produces a thorough, constructive review.

Operating rules:

- Read-only by configuration. Describes fixes in prose; the orchestrator dispatches `executor` to apply them.
- Inspects the diff (`git status`, `git diff`, `git log --oneline`) before reading individual files.
- Covers four axes for every change: **correctness**, **tests**, **readability**, **risk**.
- Ends every review with an explicit verdict: `APPROVE`, `REQUEST_CHANGES`, or `COMMENT`.

## How dispatch picks a profile

`chimera.agents.dispatch.classifier.classify_request` runs the user's prompt against each profile's `triggers` and returns a ranked list. `chimera.agents.dispatch.router.route` picks the top match (or honours a `force-route` override). Profiles with no matching trigger fall through to the default agent.

```python
from chimera.agents.dispatch.classifier import classify_request

ranked = classify_request("Plan how to migrate the auth layer to JWT")
# [SubagentMatch(name="planner", score=...), ...]
```

## Loading a profile manually

```python
from chimera.agents.config import AgentConfig
from chimera.providers.factory import create_provider

cfg = AgentConfig.from_markdown(
    "chimera/agents/presets/subagents/researcher.md"
)
agent = cfg.build(create_provider(model="glm-5"))
```

`from_markdown` parses the YAML frontmatter (`name`, `description`, `tools`, `permissions`, `loop`, `max_steps`, `triggers`) and the markdown body becomes the system prompt.

## Defining your own profile

Drop a markdown file with the same frontmatter shape under any of:

- `~/.chimera/agents/` — user-global
- `./.chimera/agents/` — project-local
- `chimera/agents/presets/subagents/` — built-in

The loader's priority order is `project > user > built-in`, so a project file can override a built-in profile of the same name.

```markdown
---
name: linter
description: Runs the project's lint suite and reports findings.
tools: [read_file, bash, search]
permissions: read_only
loop: react
max_steps: 20
triggers: [lint, style, format]
---
You are the **linter** subagent.

Operating rules:

- Run the project's lint command (e.g. `uv run ruff check chimera/`) once.
- Group findings by file and severity; cite line numbers.
- Do not auto-fix — defer that to `executor`.
```

## Prerequisites

- Chimera installed: `pip install chimera-run`
- Python 3.11+
- A provider configured via `create_provider(...)` or `chimera config`

## See also

- [Agents](/chimera/concepts/agents/) — how the parent agent orchestrates sub-agents.
- [Permission modes](./permission-modes.md) — `read_only` vs `auto_approve` in the profile frontmatter map onto the same surface.
