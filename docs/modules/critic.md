# Critic

`chimera.critic` adds in-loop evaluation to agent reasoning.  A critic
inspects each action (or only the final response) and scores it against
quality criteria.  When the score falls below a configurable threshold, the
loop automatically injects a refinement prompt and retries -- giving the agent
a chance to self-correct before returning its answer.

## Quick Start

```python
from chimera.critic import LLMCritic, CriticConfig, CriticMode
from chimera.providers import create_provider

provider = create_provider("anthropic")

critic = LLMCritic(
    provider=provider,
    config=CriticConfig(
        mode=CriticMode.FINISH_ONLY,
        success_threshold=0.8,
        max_refinement_iterations=3,
    ),
)
```

## Key Classes

| Class | Description |
|-------|-------------|
| `CriticMode` | Enum controlling when evaluation runs: `ALL_ACTIONS`, `FINISH_ONLY`, or `TOOL_AND_FINISH` |
| `CriticResult` | Dataclass with `score` (0.0--1.0), `passed` (bool), `feedback` (str), and `details` (dict) |
| `CriticConfig` | Dataclass configuring `mode`, `success_threshold`, `max_refinement_iterations`, and optional `critic_model` |
| `Critic` | ABC with `evaluate(context, current_action) -> CriticResult` and `get_followup_prompt()` |
| `LLMCritic` | Uses an LLM provider to score actions on correctness, safety, efficiency, and completeness |
| `ChecklistCritic` | Evaluates actions against a user-supplied checklist of requirements |
| `CriticMixin` | Mixin for loop classes that adds automatic evaluation and iterative refinement |

## Usage

### LLMCritic

`LLMCritic` sends recent conversation history and the current action to an
LLM, which returns a structured `SCORE / PASSED / FEEDBACK` response.

```python
from chimera.critic import LLMCritic, CriticConfig, CriticMode
from chimera.providers import create_provider

provider = create_provider("anthropic")

# Use a different (cheaper) model for the critic
critic = LLMCritic(
    provider=provider,
    config=CriticConfig(
        mode=CriticMode.FINISH_ONLY,
        success_threshold=0.7,
        max_refinement_iterations=2,
        critic_model="claude-haiku-4-20250514",
    ),
)

# Evaluate an action
result = critic.evaluate(context, action)
print(result.score)     # e.g. 0.85
print(result.passed)    # True
print(result.feedback)  # "Response is correct but could handle edge case X"
```

You can supply a custom evaluation prompt to focus on domain-specific criteria:

```python
critic = LLMCritic(
    provider=provider,
    evaluation_prompt=(
        "You are a security reviewer. Score the action from 0.0 to 1.0 "
        "based on whether it introduces security vulnerabilities.\n\n"
        "Respond in this exact format:\n"
        "SCORE: <float>\nPASSED: <true/false>\nFEEDBACK: <one paragraph>"
    ),
)
```

### ChecklistCritic

`ChecklistCritic` evaluates actions against a list of concrete requirements.
The LLM marks each requirement as satisfied or not and computes an overall
score.

```python
from chimera.critic import ChecklistCritic, CriticConfig
from chimera.providers import create_provider

provider = create_provider("anthropic")
critic = ChecklistCritic(
    checklist=[
        "All new functions have docstrings",
        "No hardcoded credentials",
        "Error handling covers network failures",
        "Unit tests are included",
    ],
    provider=provider,
    config=CriticConfig(success_threshold=0.75),
)

result = critic.evaluate(context, action)
# result.score reflects the fraction of checklist items satisfied
```

### CriticConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `CriticMode` | `FINISH_ONLY` | When to evaluate actions |
| `success_threshold` | `float` | `0.8` | Minimum score to pass |
| `max_refinement_iterations` | `int` | `3` | Maximum retries before accepting |
| `critic_model` | `str \| None` | `None` | Optional model override for the critic |

### CriticMode

| Value | Behavior |
|-------|----------|
| `ALL_ACTIONS` | Evaluate every action (tool calls and text responses) |
| `FINISH_ONLY` | Evaluate only the final text response (no tool_calls) |
| `TOOL_AND_FINISH` | Evaluate both tool calls and the final response |

### CriticResult

| Field | Type | Description |
|-------|------|-------------|
| `score` | `float` | Evaluation score from 0.0 to 1.0 |
| `passed` | `bool` | Whether the score met the configured threshold |
| `feedback` | `str \| None` | Actionable feedback for improvement |
| `details` | `dict \| None` | Optional structured metadata |

### Refinement prompts

When an evaluation fails, `Critic.get_followup_prompt()` generates a
refinement message that includes the score, threshold, iteration count, and
feedback:

```python
prompt = critic.get_followup_prompt(result, iteration=1)
# "Your previous response scored 65% (threshold: 80%).
#  Iteration 1/3.
#
#  Feedback: The function is missing input validation for negative values.
#
#  Please revise your response addressing the feedback above."
```

### CriticMixin

`CriticMixin` is mixed into loop classes (e.g. `ReActLoop`) to wire critic
evaluation directly into the reasoning cycle.  It provides two key methods:

- `_should_evaluate(action)` -- checks the critic's `CriticMode` to decide
  whether this action type should be evaluated.
- `_evaluate_and_maybe_refine(context, action, event_bus)` -- runs the critic,
  publishes a `CriticEvent`, and returns `(should_continue, followup_message)`.

```python
from chimera.critic import CriticMixin

class MyLoop(CriticMixin):
    def __init__(self, critic=None):
        self.critic = critic
        self._refinement_iteration = 0

    def step(self, context, action, event_bus=None):
        should_retry, followup = self._evaluate_and_maybe_refine(
            context, action, event_bus,
        )
        if should_retry:
            # Inject followup prompt and re-run
            context.add_message(Message.user(followup))
            return self.step(context, self.get_next_action(context))
        return action
```

When the critic score meets the threshold, `_refinement_iteration` resets to 0.
When `max_refinement_iterations` is exhausted, the loop accepts the current
action and moves on.

## Integration

The critic system integrates with the rest of Chimera through two mechanisms:

1. **LoopConfig**: A `Critic` instance (with its `CriticConfig`) can be passed
   via `LoopConfig` to any loop class that uses `CriticMixin`.

2. **EventBus**: Each evaluation publishes a `CriticEvent` containing the
   score, pass/fail status, feedback, and iteration number:

```python
from chimera.events.types import CriticEvent

# CriticEvent fields:
#   type = "critic"
#   score: float         (e.g. 0.85)
#   passed: bool         (True/False)
#   feedback: str | None
#   iteration: int       (refinement iteration number)
```

This allows external observers (dashboards, logging middleware, session
storage) to track critic evaluations without coupling to the loop internals.

## Import Reference

```python
from chimera.critic import (
    Critic,
    CriticConfig,
    CriticMode,
    CriticResult,
    LLMCritic,
    ChecklistCritic,
    CriticMixin,
)
```
