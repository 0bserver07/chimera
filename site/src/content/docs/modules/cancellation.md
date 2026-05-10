---
title: "Cancellation"
description: "Cancellation"
---

`chimera.core.cancellation` provides a cooperative, thread-safe cancellation
token that propagates a cancel signal from any thread to an in-progress agent
operation.

## CancellationToken

| Method / Property | Description |
|-------------------|-------------|
| `cancel()` | Set the cancelled flag and invoke all registered callbacks |
| `is_cancelled` | `bool` property — `True` after `cancel()` |
| `check()` | Raise `OperationCancelled` if cancelled; otherwise no-op |
| `on_cancel(callback)` | Register a zero-argument callback; fires immediately if already cancelled |
| `wait(timeout)` | Block until cancelled or timeout; returns `True` if cancelled |

### OperationCancelled

`OperationCancelled` is a plain `Exception` subclass raised by `check()`.
Tools should call `check()` at safe yield points to honour a cancel request.

## CancellableTool mixin

`CancellableTool` adds a single method to any `BaseTool` subclass:

| Method | Description |
|--------|-------------|
| `bind_cancellation(token)` | Store a `CancellationToken` as `self._cancel_token` |

Call `self._cancel_token.check()` inside long-running tool logic to abort
cleanly when the user presses Ctrl+C in the REPL.

## LoopConfig wiring

Pass a token through `LoopConfig` so the loop cancels on SIGINT:

```python
import signal
from chimera.core.cancellation import CancellationToken
from chimera.core.loop_config import LoopConfig
from chimera.core.agent import Agent

token = CancellationToken()
signal.signal(signal.SIGINT, lambda *_: token.cancel())

agent = Agent(provider=provider, tools=tools)
config = LoopConfig(cancellation=token)
result = agent.run("Refactor the auth module", loop_config=config)
```

The REPL (`chimera code`) wires a `CancellationToken` automatically so
Ctrl+C cancels the running turn without killing the process.

## AbortSignal (AgentLoop)

`AgentLoop` (the modern async-generator loop) takes an
`AbortSignal` rather than a `CancellationToken`:

```python
from chimera.core.abort import AbortSignal
from chimera.core.agent_loop import AgentLoop

abort = AbortSignal()
loop = AgentLoop()
async for event in loop.run(
    messages=msgs, tools=tools, provider=provider,
    system_prompt="...", abort_signal=abort,
):
    if user_pressed_ctrl_c:
        abort.set()
```

Both surfaces are cooperative — tools call `check()`/`is_set()` at
safe yield points and raise `OperationCancelled` to unwind cleanly.

## Related

- [LoopConfig](/modules/loop-config/) — `cancellation` field plumbing
- [Loops](/modules/loops/) — `ReAct` (token) vs `AgentLoop` (signal)
