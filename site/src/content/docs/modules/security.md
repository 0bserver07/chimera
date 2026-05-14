---
title: "Security"
description: "Security"
---

`chimera.security` provides LLM-powered and rule-based security analysis for
tool calls.  Before a tool executes, an analyzer classifies the call's risk
level and a confirmation policy decides whether to proceed, prompt the user, or
block.  This gives you defence-in-depth without writing manual allowlists for
every dangerous command.

## Quick Start

```python
from chimera.security import (
    RuleBasedSecurityAnalyzer,
    ConfirmAboveThreshold,
    SecurityRisk,
)

analyzer = RuleBasedSecurityAnalyzer()
policy = ConfirmAboveThreshold(threshold=SecurityRisk.MEDIUM)

# Simulate a tool call
from chimera.types import ToolCall

tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /tmp/build"})
risk = analyzer.analyze(tc)            # SecurityRisk.HIGH
needs_confirm = policy.should_confirm(risk)  # True
```

## Key Classes

| Class | Description |
|-------|-------------|
| `SecurityRisk` | IntEnum with levels `UNKNOWN`, `LOW`, `MEDIUM`, `HIGH` and an `is_riskier_than()` comparator |
| `SecurityAnalyzer` | ABC with `analyze(tool_call) -> SecurityRisk` and `analyze_batch()` |
| `RuleBasedSecurityAnalyzer` | Fast pattern-matching analyzer that flags known-dangerous patterns (e.g. `rm -rf`, `DROP TABLE`, `chmod 777`) |
| `LLMSecurityAnalyzer` | Uses an LLM provider to evaluate risk based on tool call content |
| `CompositeSecurityAnalyzer` | Chains rule-based (fast) and LLM (thorough) analyzers -- escalates to LLM only when the rule-based pass returns LOW |
| `ConfirmationPolicy` | ABC with `should_confirm(risk) -> bool` |
| `NeverConfirm` | Never requires confirmation regardless of risk |
| `AlwaysConfirm` | Always requires confirmation regardless of risk |
| `ConfirmAboveThreshold` | Requires confirmation when risk meets or exceeds a configurable threshold |

## Usage

### Rule-based analysis

`RuleBasedSecurityAnalyzer` checks tool call arguments against a built-in list
of dangerous patterns (`rm -rf`, `drop table`, `--force`, `chmod 777`, `mkfs.`,
`dd if=`, and more).  Any match returns `SecurityRisk.HIGH`; otherwise `LOW`.

```python
from chimera.security import RuleBasedSecurityAnalyzer, SecurityRisk
from chimera.types import ToolCall

analyzer = RuleBasedSecurityAnalyzer()

safe = ToolCall(id="1", name="bash", arguments={"command": "ls -la"})
assert analyzer.analyze(safe) == SecurityRisk.LOW

dangerous = ToolCall(id="2", name="bash", arguments={"command": "rm -rf /"})
assert analyzer.analyze(dangerous) == SecurityRisk.HIGH
```

### LLM-powered analysis

When pattern matching is not enough, `LLMSecurityAnalyzer` sends the tool call
to an LLM for classification.  Use a cheap, fast model to keep latency low.

```python
from chimera.security import LLMSecurityAnalyzer
from chimera.providers import create_provider

provider = create_provider("anthropic", model="claude-sonnet-4-20250514")
analyzer = LLMSecurityAnalyzer(provider, model="claude-sonnet-4-20250514")

risk = analyzer.analyze(tool_call)
```

### Composite analysis

`CompositeSecurityAnalyzer` gives you the best of both worlds: the rule-based
analyzer runs first (fast, zero-cost).  If it returns `HIGH`, that result is
used immediately.  Otherwise the LLM analyzer provides a more nuanced
assessment.

```python
from chimera.security import (
    CompositeSecurityAnalyzer,
    RuleBasedSecurityAnalyzer,
    LLMSecurityAnalyzer,
)
from chimera.providers import create_provider

provider = create_provider("anthropic")
composite = CompositeSecurityAnalyzer(
    rule_analyzer=RuleBasedSecurityAnalyzer(),
    llm_analyzer=LLMSecurityAnalyzer(provider),
)

risk = composite.analyze(tool_call)
```

### Batch analysis

Every `SecurityAnalyzer` supports `analyze_batch()` for evaluating multiple
tool calls at once:

```python
results = analyzer.analyze_batch([tc1, tc2, tc3])
# -> [(tc1, SecurityRisk.LOW), (tc2, SecurityRisk.HIGH), (tc3, SecurityRisk.MEDIUM)]
```

### Confirmation policies

Policies translate a risk level into a yes/no confirmation decision:

```python
from chimera.security import (
    NeverConfirm,
    AlwaysConfirm,
    ConfirmAboveThreshold,
    SecurityRisk,
)

# Never ask -- fully autonomous
NeverConfirm().should_confirm(SecurityRisk.HIGH)   # False

# Always ask -- maximum caution
AlwaysConfirm().should_confirm(SecurityRisk.LOW)   # True

# Threshold -- confirm MEDIUM and above, treat UNKNOWN as needing confirmation
policy = ConfirmAboveThreshold(
    threshold=SecurityRisk.MEDIUM,
    confirm_unknown=True,
)
policy.should_confirm(SecurityRisk.LOW)      # False
policy.should_confirm(SecurityRisk.MEDIUM)   # True
policy.should_confirm(SecurityRisk.UNKNOWN)  # True
```

### SecurityRisk comparison

`UNKNOWN` is treated as equivalent to `HIGH` for safety when using
`is_riskier_than()`:

```python
from chimera.security import SecurityRisk

SecurityRisk.HIGH.is_riskier_than(SecurityRisk.MEDIUM)     # True
SecurityRisk.UNKNOWN.is_riskier_than(SecurityRisk.MEDIUM)  # True (UNKNOWN -> HIGH)
SecurityRisk.LOW.is_riskier_than(SecurityRisk.MEDIUM)      # False
```

## Integration

Security analysis fits into the Chimera agent loop alongside permissions.
A `SecurityAnalyzer` can be wired into `LoopConfig` so that every tool call
is evaluated before execution.  When used with an `EventBus`, security
decisions are published as `SecurityEvent` instances:

```python
from chimera.events.types import SecurityEvent

# SecurityEvent fields:
#   type = "security"
#   tool_name: str
#   arguments: dict
#   risk: str          (e.g. "HIGH")
#   action: str        (e.g. "blocked", "confirmed", "allowed")
```

Security analysis complements but does not replace `chimera.permissions`.
Permissions decide *whether* a tool is callable at all; security analysis
evaluates *how dangerous* a specific invocation is and can trigger user
confirmation before proceeding.

## Import Reference

```python
from chimera.security import (
    SecurityRisk,
    SecurityAnalyzer,
    LLMSecurityAnalyzer,
    RuleBasedSecurityAnalyzer,
    CompositeSecurityAnalyzer,
    ConfirmationPolicy,
    NeverConfirm,
    AlwaysConfirm,
    ConfirmAboveThreshold,
)
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/security/` -- verified against current source at wave-17.
