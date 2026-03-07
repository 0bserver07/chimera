# Research Workflow

## What It Does

`Researcher` decomposes a research question into sub-questions and search terms, searches a codebase for relevant sources, collects findings, and synthesizes them into a prose report. When used with an agent, it builds a structured prompt from the plan and delegates the investigation to `Agent.run()`.

## CLI

```bash
chimera research --question "How does the auth system work?" --workdir .
```

## Python API

```python
from chimera.research import Researcher, ResearchPlan
from chimera.core.agent import Agent

agent = Agent(model="claude-sonnet-4")
researcher = Researcher(max_sources=10)

# Full agent-driven research
output = researcher.run(question="How does the auth system work?", agent=agent, env=None)
print(output)
```

For manual control over the research steps:

```python
from chimera.research import Researcher, Finding, Source

researcher = Researcher(max_sources=5)

# Step 1: Plan
plan = researcher.plan("How does authentication work?")
print(plan.sub_questions)   # e.g. ["What is authentication?", ...]
print(plan.search_terms)    # e.g. ["authentication", "work"]

# Step 2: Search
files = {"auth.py": "class JWTAuth: ...", "views.py": "def login(): ..."}
sources = researcher.search_codebase("authentication", files)

# Step 3: Record findings
researcher.add_finding(Finding(
    title="Auth flow",
    summary="Uses JWT tokens via JWTAuth class",
    sources=sources,
    confidence=0.9,
))

# Step 4: Synthesize
report = researcher.synthesize(researcher.findings)
print(report)
```

## Key Classes

### `Researcher`

```python
class Researcher:
    def __init__(self, max_sources: int = 10) -> None
    def plan(self, question: str) -> ResearchPlan
    def search_codebase(self, query: str, files: dict[str, str]) -> list[Source]
    def add_finding(self, finding: Finding) -> None
    def synthesize(self, findings: list[Finding]) -> str
    def run(self, question: str, agent: Agent, env: Environment | None = None) -> str
```

**Properties:** `findings` (list of `Finding`).

### `ResearchPlan`

Dataclass with fields: `question` (str), `sub_questions` (list of str), `search_terms` (list of str).

### `Finding`

Dataclass with fields: `title`, `summary`, `sources` (list of `Source`), `confidence` (float, 0-1), `tags` (list of str).

### `Source`

Dataclass with fields: `name`, `content`, `url` (str, optional), `relevance` (float, 0-1).

## Import

```python
from chimera.research import Researcher, ResearchPlan, Finding, Source
```
