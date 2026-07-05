# Pi Gap Analysis — Chimera vs the Pi Agent Harness

**Method:** first-hand read of the user-facing half of the Pi monorepo
(`packages/coding-agent` ≈ 54.4k src LOC / 161 test files / 38.3k test LOC / 76
extension examples; `packages/tui` ≈ 12.1k src LOC / 2 runtime deps) cross-checked
against Chimera's `chimera code`, `chimera/tui/`, `chimera/cli/code.py`, the
plugin/weasel-extension subsystems, and `docs/specs/tui-ux-refinements.md`.
Pi paths below are relative to `packages/coding-agent/` unless noted. Everything
is source-verified; *aspirational* is flagged where it applies.

This doc is the pi-specific companion to `docs/research/feature-comparison.md`
(Chimera vs 8 other agents) — it does not restate that matrix.

---

## 1. The thesis: two different machines

Pi and Chimera are not the same kind of project, and the gap analysis only makes
sense once that is stated plainly.

- **Pi is a deliberately minimal single-agent daily-driver** whose entire surface
  area beyond the core is a **hot-reloadable, in-process TypeScript self-extension
  SDK** (31 event hooks + ~22 `pi.*` methods + ~28 `ctx.ui.*` UI primitives +
  provider/OAuth registration) plus a **custom inline (no-alt-screen) differential
  TUI**. It ships **no evals, no benchmarking, no multi-agent UI, no MCP, no
  permission system** — every one of those is an explicit non-goal
  (`docs/usage.md:306`, `README.md:491`, `docs/security.md:7`).
- **Chimera is a comparative-methodology framework**: replicate agents, control
  variables, unify a benchmark interface (`chimera bench-matrix`, 26 benches, the
  fidelity harness, the synthesis/ML layer). Its daily-driver (`chimera code`) and
  TUI multiplexer are how you *drive* the comparison, not the product itself.

**The differentiation strategy is therefore two-track:**

1. **Guard the moat (comparative methodology).** Nothing pi does threatens the
   moat — pi *has no evals* — so the moat work is offensive: keep widening the
   agent×bench matrix, the fidelity harness, and (the item this doc ships)
   **making every agent's output deterministically gradeable.** This is where pi's
   ideas are *most* useful to steal, because pi's `terminate:true` structured-output
   tool is exactly the deterministic-answer mechanism our matrix has been
   approximating with a prompt convention.
2. **Reach daily-driver parity (ergonomics).** Pi is a genuinely better *single
   terminal session* than `chimera code` today on a handful of concrete axes
   (in-process extension hooks, hot-reload, statusline/theme/keybind
   customization, session share/export). Most of the *presentation* half of that
   gap is already specced in `tui-ux-refinements.md`; the *extensibility* half is
   not, and is the main thing that spec missed (§6).

We do **not** try to become pi. We are not chasing an extension-SDK-as-product;
we are cherry-picking the mechanisms that either reinforce the moat or close a
concrete parity gap at low cost.

---

## 2. Honest inventory (side-by-side)

| Axis | Pi | Chimera | Verdict |
|---|---|---|---|
| **Runtime** | Node ≥22.19 / Bun, single | Python 3.11+, single | tie |
| **Agents** | 1 (subagents = an example extension) | 7 CLIs + presets + 9 strategies + composition | **Chimera** |
| **Multi-agent UI** | none | multiplexer (N lanes race one task) | **Chimera** (unique) |
| **Evals / benchmarks** | **none** (`PI_STARTUP_BENCHMARK` is a startup timer) | 26 benches, `bench-matrix`, fidelity harness, synthesis/ML layer | **Chimera** (the moat) |
| **Self-extension** | in-process TS SDK: 31 hooks, tools/commands/UI/providers, hot-reload | plugins (declarative files) + weasel ext (Python; shell hooks; TS deferred; no UI; no reload) | **Pi** |
| **Permissions** | none by design (trust gates *loading* only) | `~/.claude/settings.json` rules + ferret `--sandbox`×`--approval` | **Chimera** |
| **MCP** | none by design | first-class client (stdio/http) + 7 servers | **Chimera** |
| **TUI renderer** | custom line-diff, inline scrollback, 2 deps, Kitty-kbd, IME, native modifier shims | Textual (alt-screen), event-driven over `AgentDriver` | mixed (pi: single-agent polish; Chimera: multi-agent) |
| **Sessions** | JSONL tree, in-place branching, `/tree` `/fork` `/clone` `/handoff`, branch summaries | eventlog + SessionTree (in-place branch), `/tree` `/branch` `/switch` | tie (near-parity) |
| **Compaction** | auto, split-turn, file tracking, extension hooks | `FileAwareCompaction` + strategies | tie |
| **Skills / prompts** | SKILL.md (Agent Skills std), md→`/cmd` templates | skills discovery + prompt templates | tie |
| **Providers** | bundled ~30 via pi-ai; `registerProvider` + OAuth as extension | 6 adapters + OpenAI-compat catch-all + registry | mixed (pi: breadth+OAuth-ext; Chimera: adapter depth) |
| **Distribution** | `pi install npm:/git:/https:` + hosted gallery | `plugins/marketplace.py` (no hosted index) | **Pi** |
| **Docs-as-agent-context** | system prompt points the agent at `docs/extensions.md`+examples | human-facing docs | **Pi** |
| **Cost display** | footer (`↑↓` tokens, cache, `$`, ctx% thresholds) | `/cost`, cost_tracker | tie |
| **Session share / HTML export** | `/share`→gist + hosted viewer, `/export` HTML | none | **Pi** |

### Where pi is genuinely ahead (daily-driver)
- **In-process event interception.** `pi.on("tool_call", …)` can return
  `{block:true,reason}` or mutate args in place; `input`/`before_provider_request`/
  `before_agent_start` transform prompt/payload/system-prompt live
  (`src/core/extensions/types.ts:1177,1180,1159,1164`). Chimera's plugin hooks are
  **shell-command** hooks (`PreToolUse`), not in-process callables that can veto.
- **UI is an extension surface.** `ctx.ui.setWidget/setFooter/setHeader/setStatus/
  setEditorComponent/custom` + `registerMessageRenderer` (`types.ts:124-275,1225`).
  Chimera has no equivalent — the status/theme/key *registries* in
  `tui-ux-refinements.md` are static config, not an extension API.
- **Hot-reload** (`/reload`, `ctx.reload()`, jiti `moduleCache:false`).
- **Provider + OAuth as userland extension** (`pi.registerProvider`,
  `custom-provider-anthropic/`).
- **Package distribution + gallery** (`docs/packages.md`).
- **Deterministic finish tool** (`structured-output.ts`, `terminate:true`).

### Where Chimera is genuinely ahead
- **Evals are the whole point** and pi has none. This is not a gap to close — it
  is the moat to widen.
- **Multi-agent racing UI** (multiplexer) — no pi analog.
- **Permissions + sandbox composition** (ferret) — pi delegates all safety to
  containers (`docs/containerization.md`: Gondolin micro-VM / Docker / OpenShell).
- **MCP first-class** — pi refuses it on purpose.
- **Synthesis / ML layer** (TestConvergence, CEGIS, curricula, validation splits)
  — no analog in pi or any surveyed agent.

---

## 3. What pi has that Chimera lacks — ranked feature backlog

Ranked by **(impact × alignment) ÷ effort**. Each row tags **[MOAT]** (widens the
comparative-methodology advantage) or **[PARITY]** (closes a daily-driver gap),
gives pi file evidence, the Chimera target, effort (S/M/L), and whether
`tui-ux-refinements.md` already covers it.

### Tier 1 — build now (highest leverage)

| # | Feature | Tag | Pi evidence | Chimera target | Effort | In UX spec? |
|---|---|---|---|---|---|---|
| 1 | **`submit` structured-output finish tool** — deterministic final-answer extraction; the answer is a tool argument the grader reads directly, not free-text scraped from prose | **MOAT** | `examples/extensions/structured-output.ts` (`terminate:true`) | `chimera/tools/submit.py` + `eval/coding_agent_adapter.py` + `assembly/coding_agent.py` | **S** | ❌ |
| 2 | **In-process hook API** on the loop — callables that can block/mutate a tool call, transform input, or replace the system prompt (not shell hooks) | PARITY | `types.ts:1177,1180,1164`; `permission-gate.ts` | `core/loop.py` + `core/tool_executor.py` + `events/` | M | ❌ |
| 3 | **Provider + OAuth as userland extension** — `register_provider` already exists; expose to user extensions + add an OAuth flow | PARITY | `custom-provider-anthropic/index.ts:569`; `docs/custom-provider.md` | `providers/registry.py` + `auth/` | M | ❌ |

**#1 is the one this task ships** (see §4): it is S-effort, serves the moat
directly, and fixes a *documented* failure — multi-step agents (`plan-act`,
`lint-loop`, plan-execute) whose final message is commentary rather than the
artifact score 0% on answer-graded benches even after the `FINAL_ANSWER_CONTRACT`
prompt suffix. A tool the agent *calls* with its answer removes the free-text
scrape entirely.

### Tier 2 — parity wins, low/medium cost

| # | Feature | Tag | Pi evidence | Chimera target | Effort | In UX spec? |
|---|---|---|---|---|---|---|
| 4 | **Hot-reload** of skills/prompts/themes/keybinds/extensions (`/reload`) | PARITY | `interactive-mode.ts:2655`; jiti `moduleCache:false` | `plugins/manager.py` + REPL + weasel | M | ❌ (spec has theme *live-preview* only) |
| 5 | **models.json auth indirection** — `!shell-command`/`$ENV` resolved at request time (keychain/1Password) | PARITY | `docs/models.md:146` | `auth/` + `providers/factory.py` | S | ❌ |
| 6 | **Session `/share`→gist + HTML export** with a self-contained viewer | PARITY | `src/core/export-html/`; `slash-commands.ts:22` | `sessions/` export | M | ❌ |
| 7 | **`/handoff <goal>`** — distill context into a fresh focused session | PARITY | `handoff.ts` | `sessions/session.py` | S–M | ❌ |
| 8 | **Stackable autocomplete providers** (e.g. `#1234` issue completion) | PARITY | `github-issue-autocomplete.ts` | `chimera/tui/prompt.py` | M | ⚠️ partial (R-IN-2 `/`+`@` only) |

### Tier 3 — UI extension surface (larger, mostly spec-adjacent)

| # | Feature | Tag | Pi evidence | Chimera target | Effort | In UX spec? |
|---|---|---|---|---|---|---|
| 9 | **UI extension hooks** — let extensions register into the statusline/theme/key registries + custom widgets/renderers/overlays | PARITY | `types.ts:124-275,1225` | `chimera/tui/` (Textual) | L | ⚠️ partial (spec defines the *registries as config*, not as an *extension API*) |
| 10 | **Per-tool renderer override by extensions** | PARITY | `built-in-tool-renderer.ts`, `minimal-mode.ts` | `chimera/tui/render.py` | M | ⚠️ partial (R-REN-5 makes renderers built-in, not overridable) |
| 11 | **Inline (no-alt-screen) mode** | PARITY | `tui.ts` (no `?1049h`) | `chimera/tui/app.py` (`App.run(inline=True)`) | L | ✅ R-VIEW-5 (exploratory) |
| 12 | **Package distribution + gallery** (`pi install npm:/git:/https:`) | PARITY | `docs/packages.md`; `package-manager.ts` (2588 LOC) | `plugins/marketplace.py` (host an index) | M–L | ❌ |

### Tier 4 — moat-adjacent / niche
- **IME cursor via APC marker + native modifier shims** (`tui.ts:120`,
  `native/darwin/darwin-modifiers.c`) — Textual may already handle; niche. **[PARITY, M]**
- **Sandbox-as-tool-override (Gondolin pattern)** — implement sandboxing as a tool
  wrapper that routes `read/write/edit/bash` into a VM, a portable alternative to
  ferret's OS-namespacing (`examples/extensions/gondolin/`). **[PARITY, M–L]**
- **Inter-extension event bus** (`pi.events`, `event-bus.ts`). **[PARITY, S]**

---

## 4. What this task builds now: the `submit` finish tool (Tier-1 #1)

**Problem (documented in project memory + `eval/coding_agent_adapter.py:57-106`):**
grading takes the *last assistant text* off the event stream. Multi-step agents
whose terminal message is lint commentary / a "done" note rather than the artifact
score 0% even with the `FINAL_ANSWER_CONTRACT` prompt suffix, because the answer
is scraped from free-text prose.

**Mechanism (pi `structured-output.ts`):** give the agent a tool it *calls* with
its final answer. The answer is a structured tool argument, read deterministically
— no prose scrape, no fence-matching.

**Chimera implementation (additive, opt-in, no loop-core change):**
- `chimera/tools/submit.py` — `SubmitTool(BaseTool)`, records the answer in
  `ToolResult.metadata["final_answer"]`.
- `chimera/assembly/coding_agent.py` — a gentle additive `extra_tools=` seam
  (default `None` → zero behavior change) so the eval path can inject the tool
  without disturbing the 7 CLIs.
- `chimera/eval/coding_agent_adapter.py` — `aggregate_events` prefers the last
  `submit` tool-call's `answer` argument over streamed/result text (harmless when
  the tool is unused: falls through to existing behavior). An opt-in
  `use_submit_tool` flag on `CodingAgentAdapter` injects the tool + a one-line
  instruction.

**Verification gate (project rule — "not done until verified with real LLM"):**
unit tests (tool behavior + extraction precedence) run offline here; the final
gate is a live answer-graded bench (e.g. `lint-loop`/`plan-execute` × HumanEval),
which rides the task #1 grid run.

## 5. Relationship to `tui-ux-refinements.md`

That spec was mined from a corpus that includes pi (its R6 = "custom
differential-line renderer + overlay-stack agent"), so it already absorbed pi's
**presentation** mechanics: streaming commit discipline, fence normalization,
head+tail elision, per-tool renderers, the statusline/theme/keybinding registries,
overlays, inline mode. Its Phase-2/3 *is* catching up to pi's shipped presentation
layer.

**What the spec never contemplated — the axis to add:** *extensibility as a
first-class surface.* Pi's lesson is that its statusline/theme/keybinding
registries are not just user config — they are the **UI surface of an in-process
extension API**, and the agent is a first-class client of that API. To extend the
spec, add: (a) an in-process hook API on the loop (block/mutate/transform — Tier-1
#2), (b) UI-registration hooks into those registries (Tier-3 #9), (c) hot-reload
(Tier-2 #4), (d) provider-as-extension + OAuth (Tier-1 #3). The single cheapest,
highest-value item — the `submit` finish tool (Tier-1 #1) — is orthogonal to the
TUI entirely and serves the moat, which is why it goes first.

---

## 6. Runtime-half addendum (pi-agent-core / pi-ai / orchestrator)

Second mining pass over the runtime packages (~45.9k of pi's ~109k src LOC).
Key facts the tables above don't carry:

**Scale/shape.** Node/TS-only monorepo; `ai` 35.8k LOC (148 files), `agent`
8.1k, `orchestrator` 2.0k (0 tests, "experimental, may be removed"),
coding-agent 51.3k, tui 12.1k. agent-core ships **two parallel runtimes** (a
low-level in-memory loop and a batteries "harness") and **the product still
drives the low-level one** — the harness is ahead of its own adoption.

**Provider layer (pi's crown jewel).** 9 wire protocols back 35 providers and a
**1,029-model catalog generated at build time** from models.dev + live provider
APIs (`ai/scripts/generate-models.ts`, 2.3k LOC) — pricing/context/reasoning
auto-synced, never hand-edited. Unified prompt-caching knob
(`cacheRetention` → per-provider cache_control / prompt_cache_key / cachePoint).
5-level thinking with per-model clamps. A `faux` scripted provider streams real
protocols (paced tokens, simulated thinking/tools/errors/caching) as a
first-class deterministic test double. A ~23-flag `compat` table lets one
OpenAI-compat protocol serve ~23 backends. Absences: no Ollama/Modal
first-class, no failover chains, no cost *aggregation* (per-call only), no
tokenizer, no JSON-mode.

**Loop/runtime.** Turn = 1 LLM call + parallel tool batch; **no max-steps, no
loop detection, no budget** — the embedding app must bound it. Three injection
queues (steer / followUp / **nextTurn**, which survives abort). Session =
append-only JSONL **tree** with journaled in-place branching and
**branch summarization** (abandoned explorations summarized into returnable
entries); file-op tracking chains across compactions. Errors are data, never
throws (12-variant streaming event union; retryable/overflow classifiers).

**Orchestrator = process-fleet supervision, not multi-agent.** A daemon
spawning N headless `pi --mode rpc` children over line-JSON stdio, Unix-socket
CLI (`serve/spawn/list/rpc`), instance state in `~/.pi/orchestrator/`, plus a
hosted presence backplane (`radius.pi.dev`, heartbeats, OAuth; `relay`/`iroh`
p2p flags reserved). Zero in-task collaboration primitives repo-wide — no
delegate/subagent/team. Chimera's composition + teams have no pi counterpart.

**Design-only in pi (documented, unbuilt):** durable/resumable runs, the
phantom-typed hooks system, OTel observability, auto-compaction.

**Runtime steal-list** (extends §3's backlog; effort in brackets): generated
model catalog [M] · prompt-caching knob [M] · `faux` provider [S–M] ·
compat-flags table for `providers/compatible.py` [M] · orchestrator-style
daemon over `chimera code --mode rpc` [M–L] · error/overflow taxonomies [S] ·
`nextTurn` queue [S] · session-tree branch summarization [M].

## 7. Shipped from this analysis (same day)

- **`submit` finish tool** (§4's Tier-1 #1) — `chimera/tools/submit.py` +
  `extra_tools=` seam on `CodingAgent` + `use_submit_tool` on
  `CodingAgentAdapter` with stream-level precedence (submit > streamed prose >
  result messages). **Live-verified on glm-5.2[1m]:** swebench preset ×
  HumanEval → agent called `submit`, answer graded verbatim, passed, $0.009.
- **`pi-cli` external-agent registry entry** — pi itself is now a measurable
  row for `bench-matrix`/`bench-fidelity` (`docs/examples/agent-registry.example.json`).
