---
title: "ask_user — ask the user a clarifying question"
description: "Pause the agent to ask the user a question, optionally with a multi-choice list. The wired callback collects the response and returns it as the tool output."
---

`ask_user` hands control back to the user via a configured callback. Use it when the agent genuinely needs a decision — a destination directory, a yes/no on a risky action, a choice between equivalent approaches — and not for filler check-ins.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `question` | string | yes | The question to ask. |
| `choices` | array[string] | no | Optional choices to render as a pick-list. |

## Wiring the callback

```python
from chimera.tools.ask_user import AskUserTool

def cli_prompt(question: str, choices: list[str] | None) -> str:
    print(question)
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  {i}) {c}")
    return input("> ").strip()

tool = AskUserTool(callback=cli_prompt)
```

Without a callback, the tool raises an error — the protocol is "configure or don't enable".

## Example invocation

```json
{
  "question": "Run the migration against production?",
  "choices": ["yes", "no", "dry-run first"]
}
```

## Output sample

```
dry-run first
```

(The raw user response, as a string.)

## Notes

- Pair with the [Wire](/chimera/concepts/wire/) channel for GUI front-ends — the wire's `UserQuestion` / `UserAnswer` messages map onto this tool.
- For permission-style prompts ("approve this bash command?"), use the [`Permissions`](/chimera/concepts/permissions/) layer instead — it integrates with auditing.

## See also

- [Wire](/chimera/concepts/wire/), [Permissions](/chimera/concepts/permissions/).
