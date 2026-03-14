# Kimi CLI Feature Port + Integration Tests Design

**Date:** 2026-03-14
**Goal:** Port 7 novel features from Kimi CLI into Chimera, then prove the whole stack works with real LLM integration tests against GLM-5.

## Phase A: Port Kimi Features

### 1. Four New Tools (Layer 4)

**ThinkTool** (~15 lines) — Scratchpad for agent reasoning. No external action, just logs thought in context and events. Added to DEFAULT_TOOLS.

**AskUserTool** (~40 lines) — Pause agent loop, ask user a question with optional choices. Callback-based: in REPL mode uses stdin, in non-interactive mode uses provided callback or raises. Optional tool, not in DEFAULT_TOOLS.

**TodoTool** (~50 lines) — Agent-managed task list. Actions: add, complete, list. Stores tasks in memory, persists via context. Optional tool.

**DMailTool** (~60 lines) — Context rewind to checkpoint + summary message. Takes checkpoint_name and message. Restores context to checkpoint state, appends message as user message. Requires Context + CheckpointManager. The key Kimi innovation.

### 2. Wire Protocol (Layer 2)

**Wire** (~150 lines) — Bidirectional communication channel between agent and UI. Wraps EventBus with a response queue for request/response patterns (approval, user questions).

Wire message types: TurnBegin, TurnEnd, StepBegin, StepEnd, ApprovalRequest/Response, UserQuestion/Answer, StatusUpdate.

Integrates with LoopConfig as optional field. When present, ReAct loop emits wire messages.

### 3. Flow Skills (Layer 2)

**Flow** (~120 lines) — Parse Mermaid flowcharts into executable decision trees. FlowNode (begin/end/task/decision), FlowEdge (source/target/label), Flow.from_mermaid(), Flow.to_prompt().

Key insight: don't execute programmatically. Convert flow position into agent prompt. Agent follows flowchart by responding with branch choice.

### 4. REPL Enhancements (Layer 8)

**/init** — Analyze working directory, generate project summary via agent. ~20 lines in code.py.

**/yolo** — Toggle auto-approve mid-session. Swaps PermissionPolicy between AutoApprove and original. ~20 lines in code.py.

## Phase C: Integration Tests

New file `tests/test_integration_live.py` (~200 lines). All tests skip when ANTHROPIC_AUTH_TOKEN not set.

Tests:
1. Provider text completion
2. Provider tool use
3. Provider multi-turn
4. Agent file create + verify
5. Agent bash command
6. Agent search + edit
7. Synthesis calculator convergence
8. CIFixWorkflow with broken test
9. Composition pipeline
10. DMailTool context rewind
11. Cost tracking accuracy
12. ThinkTool no side effects

## Layer Mapping

| Feature | Layer | Package |
|---------|-------|---------|
| ThinkTool | L4 Agent | chimera/tools/think.py |
| AskUserTool | L4 Agent | chimera/tools/ask_user.py |
| TodoTool | L4 Agent | chimera/tools/todo.py |
| DMailTool | L4 Agent | chimera/tools/dmail.py |
| Wire | L2 Infrastructure | chimera/wire/ |
| Flow Skills | L2 Infrastructure | chimera/skills/flow.py |
| /init, /yolo | L8 CLI | chimera/cli/code.py |
| Integration tests | Testing | tests/test_integration_live.py |

## Total scope

~650 lines new code + ~200 lines integration tests across 8 files.
