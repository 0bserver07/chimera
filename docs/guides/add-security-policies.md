# Add Security Policies

Add security analysis and confirmation policies so your agent asks for
approval before executing dangerous tool calls.

---

## Prerequisites

A working agent setup.  See [Build a Coding Agent](build-a-coding-agent.md)
if you need one.

---

## Step 1: Understand Risk Levels

`SecurityRisk` is an `IntEnum` with four levels:

| Level | Value | Meaning |
|-------|-------|---------|
| `UNKNOWN` | 0 | Could not classify. **Treated as HIGH** for safety. |
| `LOW` | 1 | Read-only, harmless operations. |
| `MEDIUM` | 2 | File writes, installs, network to known endpoints. |
| `HIGH` | 3 | Destructive ops (`rm -rf`, `DROP TABLE`, force push), credential access. |

```python
from chimera import SecurityRisk

risk = SecurityRisk.UNKNOWN
risk.is_riskier_than(SecurityRisk.MEDIUM)  # True (UNKNOWN treated as HIGH)
```

---

## Step 2: Choose an Analyzer

### RuleBasedSecurityAnalyzer -- fast, no LLM cost

Pattern-matches against known-dangerous strings (`rm -rf`, `drop table`,
`chmod 777`, `dd if=`, etc.).  Returns `HIGH` on match, `LOW` otherwise.

```python
from chimera import RuleBasedSecurityAnalyzer

analyzer = RuleBasedSecurityAnalyzer()
```

### LLMSecurityAnalyzer -- smarter, uses a provider

Sends the tool call to an LLM that classifies risk as LOW/MEDIUM/HIGH.

```python
from chimera import LLMSecurityAnalyzer, create_provider

provider = create_provider(model="claude-sonnet-4")
analyzer = LLMSecurityAnalyzer(provider=provider)
```

### CompositeSecurityAnalyzer -- fast first, LLM for uncertain cases

Runs the rule-based check first.  If it returns `HIGH`, that result is used
immediately.  Otherwise the LLM analyzer evaluates the call.

```python
from chimera import (
    CompositeSecurityAnalyzer,
    LLMSecurityAnalyzer,
    RuleBasedSecurityAnalyzer,
    create_provider,
)

provider = create_provider(model="claude-sonnet-4")
analyzer = CompositeSecurityAnalyzer(
    rule_analyzer=RuleBasedSecurityAnalyzer(),
    llm_analyzer=LLMSecurityAnalyzer(provider=provider),
)
```

---

## Step 3: Set a Confirmation Policy

Policies decide whether a given risk level requires user confirmation.

| Class | Behaviour |
|-------|-----------|
| `NeverConfirm()` | Never ask, regardless of risk. |
| `AlwaysConfirm()` | Always ask, regardless of risk. |
| `ConfirmAboveThreshold(SecurityRisk.MEDIUM)` | Ask for MEDIUM and above. UNKNOWN also triggers confirmation by default. |

```python
from chimera import ConfirmAboveThreshold, SecurityRisk

policy = ConfirmAboveThreshold(
    threshold=SecurityRisk.MEDIUM,
    confirm_unknown=True,  # default
)
policy.should_confirm(SecurityRisk.LOW)     # False
policy.should_confirm(SecurityRisk.MEDIUM)  # True
policy.should_confirm(SecurityRisk.UNKNOWN) # True
```

---

## Step 4: Wire into a PermissionPolicy

The security module is standalone.  To integrate with the agent loop, build
a custom `PermissionPolicy` that calls your analyzer and policy:

```python
from typing import Any
from chimera import PermissionAction
from chimera.permissions.base import PermissionPolicy
from chimera.types import ToolCall

class SecurityPermissionPolicy(PermissionPolicy):
    def __init__(self, analyzer, confirmation_policy):
        self.analyzer = analyzer
        self.policy = confirmation_policy

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        tc = ToolCall(id="", name=tool_name, arguments=args)
        risk = self.analyzer.analyze(tc)
        if self.policy.should_confirm(risk):
            return PermissionAction.ASK
        return PermissionAction.ALLOW
```

Then pass it to `LoopConfig`:

```python
from chimera import Agent, ReAct, LoopConfig, create_provider, DEFAULT_TOOLS

config = LoopConfig(permissions=SecurityPermissionPolicy(analyzer, policy))
loop = ReAct(max_steps=30, config=config)
agent = Agent(provider=create_provider(), tools=list(DEFAULT_TOOLS), loop=loop)
```

---

## Step 5: Combine with Permissions for Defense-in-Depth

Use built-in permission presets alongside security analysis:

```python
from chimera.permissions.presets import AllowList

# Only allow specific tools, AND run security analysis on those
allowed = AllowList(allowed=["read_file", "search", "bash", "write_file"])
```

Or use `Interactive` to auto-allow reads and prompt for writes, then layer
security analysis on top for the writes that are allowed.

---

## Complete Example

```python
"""secure_agent.py -- Agent with security analysis and confirmation."""
from typing import Any

from chimera import (
    Agent,
    ConfirmAboveThreshold,
    DEFAULT_TOOLS,
    LoopConfig,
    PermissionAction,
    ReAct,
    RuleBasedSecurityAnalyzer,
    SecurityRisk,
    create_provider,
)
from chimera.permissions.base import PermissionPolicy
from chimera.types import ToolCall


class SecurityGate(PermissionPolicy):
    def __init__(self):
        self.analyzer = RuleBasedSecurityAnalyzer()
        self.policy = ConfirmAboveThreshold(SecurityRisk.MEDIUM)

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        tc = ToolCall(id="check", name=tool_name, arguments=args)
        risk = self.analyzer.analyze(tc)
        if self.policy.should_confirm(risk):
            return PermissionAction.ASK
        return PermissionAction.ALLOW


provider = create_provider(model="claude-sonnet-4")
config = LoopConfig(permissions=SecurityGate())
loop = ReAct(max_steps=30, config=config)

agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS), loop=loop)
result = agent.run("List all Python files, then delete temp.txt", env=None)
print(result.output)
```

The `read_file` and `search` calls will proceed automatically (LOW risk).
A `bash` call containing `rm` will trigger the ASK permission, pausing for
user confirmation.

---

## Next Steps

- [Configure Permissions](configure-permissions.md) -- permission presets and
  audit logging.
- [Build a Plugin](build-a-plugin.md) -- package security policies as a
  reusable plugin.
