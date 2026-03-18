# Portable Features from Other Coding Agents

Specific features identified from source code analysis of 6 coding agents,
with exact file references and porting difficulty.

---

## Quick Wins (Easy, ~200 LOC each)

### 1. Relative Indenter (from Aider)

Normalizes code indentation to make search/replace work robustly across
nested functions. Uses Unicode arrows for outdenting.

- **Source:** `coding_agent_research/aider/aider/coders/search_replace.py` (lines 18-79)
- **Chimera target:** enhance `chimera/tools/replace_in_file.py`
- **Chimera layer:** L4 Tools

### 2. Project Doc Discovery (from Codex)

Auto-scans AGENTS.md hierarchy from project root to cwd. Supports
configurable root markers and fallback filenames.

- **Source:** `coding_agent_research/codex/codex-rs/core/src/project_doc.rs` (get_user_instructions line 74)
- **Chimera target:** extend Agent initialization / `chimera/config/loader.py`
- **Chimera layer:** L2 Config
- **Note:** Chimera has `ProjectConfig` already but doesn't do hierarchical scanning

### 3. Expanded Provider Configs (from OpenCode)

20+ explicit provider registrations (Bedrock, Azure Vertex, Cerebras,
Cohere) with dynamic baseURL templating.

- **Source:** `coding_agent_research/opencode/packages/opencode/src/provider/provider.ts` (BUNDLED_PROVIDERS line 87)
- **Chimera target:** expand `chimera/providers/factory.py` and `chimera/providers/catalog.py`
- **Chimera layer:** L3 Provider

### 4. Action Sampler (from SWE-Agent)

Sample N completions in parallel, format as "colleague ideas," ask model
to synthesize best action. Reduces brittleness.

- **Source:** `coding_agent_research/SWE-agent/sweagent/agent/action_sampler.py` (AskColleagues line 49)
- **Chimera target:** new option in ReAct loop or as a loop wrapper
- **Chimera layer:** L4 Loop

---

## Medium Effort (400-800 LOC each)

### 5. Multiple Coder Strategies (from Aider)

10+ pluggable code editing strategies (WholeFile, EditBlock, Patch,
UnifiedDiff, Architect). Auto-selects based on model capabilities.

- **Source:** `coding_agent_research/aider/aider/coders/` (base_coder.py + 10 subclasses)
- **Chimera target:** new `EditFormat` system in tools layer
- **Chimera layer:** L4 Tools
- **Impact:** Dramatically improves editing success rate across models

### 6. Reviewer/Chooser Pattern (from SWE-Agent)

Three-stage solution ranking: Preselector (N→K), Chooser (K→1),
Reviewer (scores). More sophisticated than RetryLoop.

- **Source:** `coding_agent_research/SWE-agent/sweagent/agent/reviewer.py` (Reviewer line 81, Chooser line 292)
- **Chimera target:** extend `RetryLoop` with preselector stage
- **Chimera layer:** L4 Loop / L6 Synthesis

### 7. File Watching (from Aider)

Monitor filesystem, auto-trigger on inline AI comments ("AI!", "AI?").
Respects .gitignore, uses AST for context extraction.

- **Source:** `coding_agent_research/aider/aider/watch.py` (FileWatcher line 65)
- **Chimera target:** new watch mode in CLI
- **Chimera layer:** L8 CLI

### 8. Web Search Grounding (from Gemini CLI)

Augment web_fetch with structured grounding metadata (URI, title,
confidence). Return source citations with search results.

- **Source:** `coding_agent_research/gemini-cli/packages/core/src/tools/web-search.ts` (GroundingChunkWeb line 22)
- **Chimera target:** enhance `chimera/tools/web_fetch.py`
- **Chimera layer:** L4 Tools

---

## Hard but Valuable (800+ LOC each)

### 9. Two-Phase Memory Consolidation (from Codex)

Phase 1: Extract learnings from rollouts in parallel with job claiming.
Phase 2: Global consolidation with lease coordination, watermark tracking.
Handles concurrent workers with backoff retry.

- **Source:** `coding_agent_research/codex/codex-rs/core/src/memories/` (phase1.rs, phase2.rs)
- **Chimera target:** extend `LongTermMemory` with consolidation pipeline
- **Chimera layer:** L2 Infrastructure
- **Note:** Our `LongTermMemory` is simple key-value. Codex's is a full pipeline.

### 10. Memory Microagents (from OpenHands)

Event-driven memory: listens to RecallAction, dispatches to repo/knowledge
microagents. Returns RecallObservation with relevant code/facts.

- **Source:** `coding_agent_research/OpenHands/openhands/memory/memory.py` (Memory class line 42)
- **Chimera target:** EventBus-driven memory queries
- **Chimera layer:** L2 Events + L4 Agent

---

## Low Priority

### 11. Voice Input (from Aider)

Record audio, detect speech via RMS, transcribe with Whisper.

- **Source:** `coding_agent_research/aider/aider/voice.py`
- **When:** Only if CLI adds voice REPL support

### 12. Real-Time Streaming (from Codex)

WebSocket-based audio/text with queue backpressure.

- **Source:** `coding_agent_research/codex/codex-rs/core/src/realtime_conversation.rs`
- **When:** Only if supporting OpenAI Realtime API

---

## Recommended Porting Order

| Phase | Features | Effort |
|-------|----------|--------|
| 1 | Relative Indenter, Project Doc Discovery, Provider Configs | ~600 LOC |
| 2 | Action Sampler, Coder Strategies, File Watching | ~1800 LOC |
| 3 | Reviewer/Chooser, Web Search Grounding, Memory Consolidation | ~2400 LOC |
