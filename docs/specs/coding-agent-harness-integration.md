# Chimera × Coding-Agent-Harness Integration Spec

> Master reference for all integration points between Chimera and a coding-agent harness.
> Each section maps to one or more GitHub issues.

## 1. Plugin & Skills Framework

### 1a. Chimera as a Coding-Agent-Harness Plugin (#97)

Package Chimera as an installable harness plugin via `/plugin install`.

**What it delivers:** Any harness user can install Chimera and immediately get access to all its primitives — codebase search, code review, test generation, migration planning, benchmarking — without leaving the harness.

**Plugin structure:**
```
chimera-plugin/
  .claude-plugin/plugin.json     # metadata, version, dependencies
  commands/
    /benchmark.md                # run benchmarks on current project
    /review.md                   # multi-agent code review
    /testgen.md                  # generate test skeletons
    /migrate.md                  # plan code migrations
  agents/
    reviewer.md                  # specialized code review subagent
    researcher.md                # codebase research subagent
    tester.md                    # test generation subagent
  skills/
    retry-loop.md                # teach the agent retry strategies
    plan-act.md                  # teach the agent plan-then-act
    lint-feedback.md             # teach the agent lint-fix loops
    context-management.md        # teach the agent smart compaction
  hooks/
    hooks.json                   # PreToolUse + PostToolUse hooks
    validate-paths.sh            # block edits to non-existent files
    auto-test.sh                 # run tests after every edit
    lint-check.sh                # lint after every write
  .mcp.json                     # MCP server configuration
```

**Implementation:**
1. Create `chimera-plugin/` directory in repo
2. Write plugin.json with metadata
3. Convert existing Chimera modules into skill markdown files
4. Create slash commands that invoke Chimera workflows
5. Wire hooks to existing Chimera tools
6. Test with `/plugin install ./chimera-plugin`

---

### 1b. Agent Skills as SKILL.md Files (#98)

Convert Chimera's agent composition patterns into teachable skills.

**Skills to create:**
- `retry-loop.md` — "When a fix doesn't work, undo and try a different approach"
- `plan-act.md` — "Read the codebase with read-only tools first, then execute"
- `lint-feedback.md` — "After every edit, run the linter and fix errors before proceeding"
- `focus-chain.md` — "Budget your context. Only read files relevant to the current task"
- `ghost-commits.md` — "Create a checkpoint before every edit so you can undo"
- `investigate-first.md` — "Before fixing, run a focused investigation subagent"
- `test-convergence.md` — "Keep iterating until all tests pass, not just until code looks right"

**Format:** Each skill is a markdown file with frontmatter (name, description, triggers) and instructions that the agent loads on-demand.

---

### 1c. Custom Subagents via Chimera (#99)

Create harness subagents that delegate to Chimera-composed agents.

**Subagents to create:**
- `code-reviewer` — Spawns a RoleBasedTeam (reviewer + security auditor) in parallel
- `test-writer` — Uses TestGenerator + Oracle to create comprehensive tests
- `bug-investigator` — Uses InvestigatorAgent to analyze before the main agent acts
- `migration-advisor` — Uses MigrationPlanner to scan and plan transforms

**How it works:** Each subagent is a markdown file in `agents/` that describes the specialization. The harness spawns it as an isolated context with restricted tools.

---

## 2. MCP Servers (Tools the Harness Can Call)

### 2a. Codebase Search MCP Server (#100)

Expose Chimera's CodebaseIndex as an MCP server.

**Tools provided:**
- `chimera_search` — semantic search over the repo (TF-IDF + optional embeddings)
- `chimera_symbols` — find classes/functions/methods by name across the codebase
- `chimera_dependencies` — trace imports and callers for a given function

**Why it matters:** Built-in Grep/Glob in most harnesses are text-based. This provides SEMANTIC search — "find the authentication logic" returns `auth.py:login()` even if the word "authentication" doesn't appear in the file.

**Implementation:**
```python
# chimera/mcp_servers/search_server.py
from chimera.tools.codebase_index import CodebaseIndex
from chimera.mcp import create_mcp_server

index = CodebaseIndex()
index.index_directory(".")

server = create_mcp_server("chimera-search", tools=[
    tool("search", "Semantic search over the codebase", index.search),
    tool("symbols", "Find symbols by name", index.find_symbols),
])
```

---

### 2b. Code Review MCP Server (#101)

Multi-agent parallel code review as an MCP tool.

**Tools provided:**
- `chimera_review_diff` — review a git diff with multiple specialized agents
- `chimera_review_file` — deep review of a single file

**Architecture:** Spawns 4 parallel subagents (logic, security, tests, architecture), each with its own context window, returns structured findings.

---

### 2c. Test Generation MCP Server (#102)

Generate test skeletons from source analysis.

**Tools provided:**
- `chimera_testgen` — analyze a source file and generate test cases
- `chimera_coverage` — identify untested code paths

---

### 2d. RAG/Doc Retrieval MCP Server (#103)

Ground responses in project-specific documentation.

**Tools provided:**
- `chimera_doc_search` — search indexed project documentation
- `chimera_api_lookup` — find API signatures and docstrings
- `chimera_grounded_answer` — search + fetch + cite from web sources

**Why it matters:** Solves the #1 hallucination source — agents generating code for APIs that don't exist or have changed. This fetches live, version-specific documentation.

---

### 2e. Benchmark MCP Server (#104)

Run benchmarks from within the harness.

**Tools provided:**
- `chimera_benchmark` — run HumanEval/MBPP-style problems
- `chimera_evaluate` — evaluate a code change against test cases

---

## 3. Hooks (Automated Quality Gates)

### 3a. PreToolUse: Path Validation Hook (#105)

Block edits to files that don't exist in the codebase.

**How:** Before any Write/Edit tool call, check if the path exists in the CodebaseIndex. If not, return exit code 2 with "File not found: {path}. Did you mean: {suggestions}?"

**Solves:** Hallucinated file paths — a frequent user complaint about coding-agent harnesses.

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "python -m chimera.hooks.validate_path \"$TOOL_INPUT\""
      }]
    }]
  }
}
```

---

### 3b. PostToolUse: Auto-Test Hook (#106)

Run relevant tests after every file edit.

**How:** After Write/Edit, find test files related to the modified file (via import graph or naming convention), run them, feed failures back to the agent.

**Solves:** The agent declaring "done" when tests are actually broken.

---

### 3c. PostToolUse: Auto-Lint Hook (#107)

Run linter after every edit, fix issues automatically.

**How:** After Write/Edit, run ruff/eslint on the modified file. If errors found, either auto-fix or feed back to the agent.

---

### 3d. PreToolUse: Security Scanner Hook (#108)

Scan bash commands for dangerous operations before execution.

**How:** Use RiskClassifier to evaluate bash commands. Block HIGH risk (rm -rf /, chmod 777, etc.) with exit code 2.

---

### 3e. Stop: Verification Hook (#109)

Before the agent declares "done", verify all tests pass.

**How:** On Stop event, run the project's test suite. If any test fails, send feedback to the agent that it's not actually done.

---

## 4. Solving Known Harness Problems

### 4a. Anti-Hallucination: Codebase Grounding (#110)

Use CodebaseIndex to validate every file reference the agent makes.

**Components:**
- PreToolUse hook for path validation
- MCP server for semantic search
- RAG server for doc grounding
- Fact-checking: compare the agent's claims against actual codebase

---

### 4b. Context Window Management (#111)

Proactive context management to prevent degradation.

**Components:**
- SmartCompaction: preserve recent turns, summarize older ones
- ThoughtStripCompaction: remove thinking blocks from old messages
- FocusChain: token budgeting for what gets included
- MemoryConsolidation: extract and persist facts across compactions

**Implementation as a harness skill:**
```markdown
---
name: context-management
description: Proactively manage context window to prevent degradation
triggers: ["context", "compact", "memory"]
---

Monitor your context usage. At 70%, start being selective about what you read.
At 85%, summarize older conversation into key facts.
At 90%, compact aggressively — keep only recent turns and extracted facts.

Use the chimera_consolidate tool to extract facts before compacting.
```

---

### 4c. Loop Detection & Recovery (#112)

Detect when the agent is stuck in a repetitive loop and force a different approach.

**Components:**
- ExactRepeatDetector: flag 3x repeated actions
- Pattern cycle detection
- Recovery prompt injection: "Try a completely different approach"

---

### 4d. Persistent Memory Across Sessions (#113)

Survive session resets without losing important context.

**Components:**
- MemoryConsolidation: extract facts into structured storage
- LongTermMemory: JSON-backed persistence
- FileTracker: know which files were read/modified even after compaction

---

## 5. Research & Benchmarking Layer

### 5a. Comparative Agent Benchmarking (#114)

Run the same task through different agent architectures and compare.

**What it enables:**
- A/B test: SWE_AGENT vs AIDER vs CLINE presets on same problem set
- Measure: pass@k, cost, steps, time for each
- Report: which architecture works best for which problem type

---

### 5b. Prompt Engineering Lab (#115)

Systematic prompt optimization using Chimera's modular architecture.

**What it enables:**
- Swap system prompts while keeping everything else constant
- Test long-horizon vs short prompts
- Measure impact of in-context examples
- Use ActionSampler for parallel prompt evaluation

---

## Implementation Roadmap

### Phase 1: Quick Wins (this weekend)
- [ ] #105 Path validation hook (Low effort, HIGH impact)
- [ ] #100 Codebase search MCP server (Low effort, HIGH impact)
- [ ] #106 Auto-test hook (Low effort, MEDIUM impact)

### Phase 2: Plugin (next week)
- [ ] #97 Package as a harness plugin
- [ ] #98 Agent skills as SKILL.md
- [ ] #107 Auto-lint hook

### Phase 3: MCP Servers (week after)
- [ ] #101 Code review MCP
- [ ] #102 Test generation MCP
- [ ] #103 RAG/Doc retrieval MCP

### Phase 4: Deep Integration (ongoing)
- [ ] #110 Anti-hallucination codebase grounding
- [ ] #111 Context window management
- [ ] #113 Persistent memory
- [ ] #114 Comparative benchmarking
