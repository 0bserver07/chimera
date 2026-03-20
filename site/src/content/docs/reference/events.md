---
title: "chimera.events"
description: "chimera.events"
---

::: chimera.events
    options:
      show_submodules: true

## New event types (pi-mono)

Ten additional event dataclasses were added in `chimera.events.types` to cover
the full agent lifecycle:

| Class | `type` field | Key fields |
|-------|-------------|------------|
| `ModelRequestEvent` | `model_request` | `model`, `message_count`, `tool_count` |
| `ModelResponseEvent` | `model_response` | `model`, `content_length`, `tool_calls_count`, `input_tokens`, `output_tokens` |
| `TurnStartEvent` | `turn_start` | `turn_number` |
| `TurnEndEvent` | `turn_end` | `turn_number`, `tool_calls_count` |
| `StreamStartEvent` | `stream_start` | `model` |
| `StreamEndEvent` | `stream_end` | `total_tokens` |
| `AgentStartEvent` | `agent_start` | `max_steps` |
| `AgentEndEvent` | `agent_end` | `steps`, `success`, `total_cost` |
| `SteeringEvent` | `steering` | `content` |
| `CancellationEvent` | `cancellation` | `at_step` |

All ten are exported from `chimera.events.types` and included in `__all__`.
