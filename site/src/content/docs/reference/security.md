---
title: "chimera.security"
description: "Reference for chimera.security — risk levels, analyzers (rule-based, LLM, composite), confirmation policies."
---

`chimera.security` classifies tool-call risk and decides whether to ask
the user before executing.

For the tutorial, see [Add Security Policies](/add-security-policies/).

## Top-level exports

```python
from chimera.security import (
    SecurityRisk,
    RiskClassifier,
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

## Risk levels

| `SecurityRisk` | Value | Meaning |
|---|---|---|
| `UNKNOWN` | 0 | Could not classify. Treated as HIGH for safety. |
| `LOW` | 1 | Read-only, harmless. |
| `MEDIUM` | 2 | Writes, installs, network to known endpoints. |
| `HIGH` | 3 | Destructive ops (`rm -rf`, `DROP TABLE`, force push), credential access. |

## Analyzers

| Class | Module | Speed |
|---|---|---|
| `RuleBasedSecurityAnalyzer` | `chimera.security.analyzer` | Fast, free; pattern matches against known-dangerous strings. |
| `LLMSecurityAnalyzer` | `chimera.security.analyzer` | Smarter; sends the call to a `Provider` for classification. |
| `CompositeSecurityAnalyzer` | `chimera.security.analyzer` | Runs the rule-based check first; falls through to the LLM only when uncertain. |

Every analyzer implements `analyze(tool_call) -> SecurityRisk`.

## Confirmation policies

| Class | Module | Behaviour |
|---|---|---|
| `NeverConfirm()` | `chimera.security.policy` | Never ask, regardless of risk. |
| `AlwaysConfirm()` | `chimera.security.policy` | Always ask. |
| `ConfirmAboveThreshold(SecurityRisk.MEDIUM, confirm_unknown=True)` | `chimera.security.policy` | Ask for risks at or above the threshold; `UNKNOWN` triggers confirmation by default. |

## See also

- [Add Security Policies](/add-security-policies/) for end-to-end wiring.
- [`chimera.permissions`](/reference/permissions/) for the policy
  surface that consumes `SecurityRisk` decisions.
- [`chimera.events`](/reference/events/) for `SecurityEvent`.
