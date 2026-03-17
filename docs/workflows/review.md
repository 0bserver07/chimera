# Code Review Workflow

## What It Does

`ReviewOrchestrator` manages a two-agent review-fix iteration cycle. A reviewer agent examines a diff and produces structured `ReviewFeedback` with comments. If the feedback contains errors, an author agent receives the comments and applies fixes. The cycle repeats until the reviewer approves or `max_rounds` is reached.

## CLI

```bash
chimera review --diff changes.patch --model claude-sonnet-4 --max-rounds 3
```

## Python API

```python
from chimera.review import ReviewOrchestrator, ReviewFeedback
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider

provider = create_provider(model="claude-sonnet-4-20250514")
reviewer = Agent(provider=provider)
author = Agent(provider=provider)

orchestrator = ReviewOrchestrator(max_rounds=3)
approved = orchestrator.run(
    diff=open("changes.patch").read(),
    reviewer=reviewer,
    author=author,
    env=None,
)

print(f"Approved: {approved}")
print(f"Rounds: {orchestrator.current_round}")
print(f"Total comments: {orchestrator.total_comments}")
```

## Key Classes

### `ReviewOrchestrator`

```python
class ReviewOrchestrator:
    def __init__(self, max_rounds: int = 3) -> None
    def run(self, diff: str, reviewer: Agent, author: Agent, env: Environment | None = None) -> bool
    def add_review(self, feedback: ReviewFeedback) -> ReviewRound
    def mark_fixed(self) -> None
    def needs_another_round(self) -> bool
```

**Properties:** `max_rounds`, `rounds` (list of `ReviewRound`), `current_round`, `is_approved`, `is_complete`, `total_comments`.

### `ReviewFeedback`

Dataclass with fields: `comments` (list of `ReviewComment`), `approved` (bool), `summary` (str).

**Properties:** `has_critical`, `has_errors`, `comment_count`, `files_reviewed`.

**Methods:** `by_severity(severity)`, `by_file(file)`, `parse_from_text(text)` (static, parses `[SEVERITY] file:line: message` format).

### `ReviewComment`

Dataclass with fields: `file`, `line`, `severity` (`Severity` enum), `message`, `suggestion`.

### `Severity`

Enum: `INFO`, `SUGGESTION`, `WARNING`, `ERROR`, `CRITICAL`.

## Import

```python
from chimera.review import ReviewOrchestrator, ReviewFeedback, ReviewComment, Severity
```
