---
title: "Message Queues"
description: "Message Queues"
---

`chimera.core.message_queue` provides two thread-safe queues for injecting
messages into a running agent loop from any external thread — webhooks, a
REPL, or another async task.

## MessageQueue

A single FIFO queue for generic message injection:

| Method / Property | Description |
|-------------------|-------------|
| `enqueue(message)` | Add a `Message` to the queue |
| `enqueue_text(text)` | Convenience: wrap `text` in a user `Message` and enqueue |
| `drain()` | Remove and return all queued messages |
| `peek()` | View queued messages without removing them |
| `is_empty()` | `True` if the queue has no messages |
| `size` | Number of queued messages |
| `clear()` | Discard all queued messages |

## MessageQueues

`MessageQueues` holds two separate queues with different semantics:

| Queue | Drained when | Purpose |
|-------|--------------|---------|
| **steering** | Before each model call inside the current turn | Redirect the agent mid-turn |
| **follow_up** | After the current turn completes | Queue the next user prompt |

| Method / Property | Description |
|-------------------|-------------|
| `steer(message)` | Enqueue a steering message |
| `follow_up(message)` | Enqueue a follow-up message |
| `drain_steering()` | Remove and return all steering messages |
| `drain_follow_up()` | Remove and return all follow-up messages |
| `has_steering` | `True` if steering messages are pending |
| `has_follow_up` | `True` if follow-up messages are pending |

All operations on both queues are protected by a single `threading.Lock`.

## Example — mid-turn steering

```python
import threading
from chimera.core.message_queue import MessageQueues
from chimera.types import Message

queues = MessageQueues()

# In another thread (e.g. a webhook handler):
def inject_hint():
    queues.steer(Message.user("Focus only on the login flow."))

threading.Timer(2.0, inject_hint).start()

# The loop drains steering messages before each model call:
for msg in queues.drain_steering():
    context.add(msg)
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/core/message_queue.py` -- verified against current source at wave-17.
