# Remaining Pi-Mono Gaps — Feature Spec

**Date:** 2026-03-19
**Status:** Final

7 features that are either skeleton stubs or genuinely missing.

---

## Feature 1: OAuth Flows (real implementation)

### Problem
`OAuthDeviceFlow` and `OAuthBrowserFlow` both raise `NotImplementedError`. No HTTP calls, no provider-specific flows.

### Design
Implement both flows using stdlib `urllib.request` (no httpx dependency). Keep the zero-dependency-core principle.

**OAuthDeviceFlow** (RFC 8628):
1. POST to `device_auth_url` → get `device_code`, `user_code`, `verification_uri`
2. Print: "Visit {url} and enter code: {code}"
3. Poll `token_url` every `poll_interval` seconds until authorized or timeout
4. Return `Credential` with access_token, refresh_token, expires_at

**OAuthBrowserFlow** (Authorization Code + PKCE):
1. Generate `code_verifier` + `code_challenge` (SHA256)
2. Start local HTTP server on `redirect_port`
3. Open browser to `auth_url?client_id=...&code_challenge=...&redirect_uri=...`
4. Wait for callback with `code` parameter
5. Exchange code for token via POST to `token_url`
6. Return `Credential`

**Refresh**: POST to `token_url` with `grant_type=refresh_token`.

### Files Changed
| File | Change |
|------|--------|
| `chimera/auth/oauth.py` | Replace stubs with real HTTP implementations |
| `tests/test_oauth.py` | New — test with mocked HTTP responses |

---

## Feature 2: Auto-Compaction (wire the skeleton)

### Problem
`Session.__init__` accepts `auto_compact=True` but never checks it. `LoopConfig.auto_compact_threshold=0.8` exists but is unused.

### Design
After each `chat()` turn, check if context exceeds threshold and compact:

```python
def chat(self, message: str) -> AgentResult:
    self._context.add(Message.user(message))
    if self._tree:
        self._tree.add_message(Message.user(message))
    result = self._agent.loop.run(...)
    if self._tree:
        self._tree.add_message(Message.assistant(result.output))

    # Auto-compact if enabled
    if self._auto_compact and self._compaction:
        self._maybe_compact()

    return result

def _maybe_compact(self) -> None:
    """Compact context if it exceeds the threshold."""
    config = getattr(self._agent.loop, "config", None)
    threshold = config.auto_compact_threshold if config else 0.8
    window = self._agent.provider.context_window
    estimated_tokens = sum(len(m.content) // 4 for m in self._context.messages)
    if estimated_tokens > window * threshold:
        budget = int(window * 0.5)  # Compact to 50% of window
        self._context._messages = self._compaction.compact(
            self._context.messages, budget
        )
```

Also wire into the REPL (`run_code`):
```python
compaction = SummaryCompaction(keep_first=2, keep_last=10)
session = Session(agent=agent, env=env, tree=tree, auto_compact=True, compaction=compaction)
```

### Files Changed
| File | Change |
|------|--------|
| `chimera/sessions/session.py` | Add `_maybe_compact()`, call after `chat()` and `iter_chat()` |
| `chimera/cli/code.py` | Wire `auto_compact=True` + `SummaryCompaction` into session |
| `tests/test_auto_compaction.py` | New |

---

## Feature 3: Thinking Levels

### Problem
Only `AnthropicProvider` has `enable_thinking`/`thinking_budget`. No abstraction. Other providers have zero thinking support.

### Design

Add `ThinkingLevel` enum and optional `thinking` param to Provider base class:

```python
# chimera/providers/thinking.py
from enum import Enum

class ThinkingLevel(str, Enum):
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"

THINKING_BUDGETS: dict[ThinkingLevel, int] = {
    ThinkingLevel.OFF: 0,
    ThinkingLevel.MINIMAL: 1024,
    ThinkingLevel.LOW: 2048,
    ThinkingLevel.MEDIUM: 8192,
    ThinkingLevel.HIGH: 16384,
    ThinkingLevel.MAX: 32768,
}

def budget_for_level(level: ThinkingLevel) -> int:
    return THINKING_BUDGETS.get(level, 0)
```

Update `Provider.complete()` signature — add optional `thinking` param:
```python
def complete(self, messages, tools=None, temperature=0.0,
             max_tokens=None, thinking=None) -> Response:
```

Default implementation: ignore `thinking` (backward compatible). `AnthropicProvider` maps `ThinkingLevel` → its existing `enable_thinking`/`thinking_budget` logic. OpenAI/Google providers can map to their reasoning tokens when available.

### Files Changed
| File | Change |
|------|--------|
| `chimera/providers/thinking.py` | New — ThinkingLevel enum + budget mapping |
| `chimera/providers/base.py` | Add `thinking` param to `complete()`, `stream()`, `async_complete()`, `async_stream()` |
| `chimera/providers/anthropic.py` | Map ThinkingLevel → existing thinking config |
| `tests/test_thinking_levels.py` | New |

---

## Feature 4: Model Cycling

### Problem
`/model` just prints the current model name. No way to cycle through models mid-session.

### Design

Add `--models` CLI flag (comma-separated list). Store on session. `/model next` and `/model prev` cycle through the list, recreating the provider.

```python
# In run_code():
model_list = getattr(args, "models", "").split(",") if getattr(args, "models", "") else [provider.model_name]
model_index = [0]  # mutable for closure
```

Update `cmd_model`:
```python
def cmd_model(session, env, args, out):
    parts = args.strip().split()
    sub = parts[0] if parts else ""
    if sub == "next":
        model_index[0] = (model_index[0] + 1) % len(model_list)
        new_model = model_list[model_index[0]]
        session.provider = create_provider(model=new_model)
        session._agent.provider = session.provider
        out(f"Switched to: {new_model}")
    elif sub == "prev":
        model_index[0] = (model_index[0] - 1) % len(model_list)
        new_model = model_list[model_index[0]]
        session.provider = create_provider(model=new_model)
        session._agent.provider = session.provider
        out(f"Switched to: {new_model}")
    else:
        out(f"Current model: {session.provider.model_name}")
        if len(model_list) > 1:
            out(f"Available: {', '.join(model_list)}")
            out("Use /model next or /model prev to cycle")
```

### Files Changed
| File | Change |
|------|--------|
| `chimera/cli/main.py` | Add `--models` arg to code subparser |
| `chimera/cli/code.py` | Update `cmd_model`, pass model_list to REPL |
| `tests/test_model_cycling.py` | New |

---

## Feature 5: Skills Discovery

### Problem
`chimera/skills/flow.py` is a Mermaid→decision tree engine, not a skill file discovery system.

### Design

New module `chimera/skills/discovery.py` that walks directories for `SKILL.md` files:

```python
@dataclass
class Skill:
    name: str
    description: str
    content: str
    file_path: str
    base_dir: str

def discover_skills(search_paths: list[str]) -> list[Skill]:
    """Walk directories for SKILL.md files with YAML frontmatter."""
    skills = []
    for path in search_paths:
        for skill_file in Path(path).rglob("SKILL.md"):
            skill = _parse_skill_file(skill_file)
            if skill:
                skills.append(skill)
    return skills

def _parse_skill_file(path: Path) -> Skill | None:
    """Parse SKILL.md with YAML frontmatter."""
    text = path.read_text()
    if not text.startswith("---"):
        return None
    # Parse frontmatter (name, description)
    # Validate name format (lowercase a-z/0-9/hyphens, max 64 chars)
    # Return Skill with content = body after frontmatter

def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Format skills for system prompt injection."""
    lines = ["## Available Skills"]
    for s in skills:
        lines.append(f"- **{s.name}**: {s.description}")
    return "\n".join(lines)
```

Search paths (in order):
1. `.chimera/skills/` (project)
2. `~/.chimera/skills/` (user global)

Wire into `run_code()` — discover skills, append to system prompt.

### Files Changed
| File | Change |
|------|--------|
| `chimera/skills/discovery.py` | New — discover_skills(), parse frontmatter |
| `chimera/cli/code.py` | Discover skills, inject into system prompt |
| `tests/test_skill_discovery.py` | New |

---

## Feature 6: Proxy Mode

### Problem
No way to relay LLM calls through an HTTP proxy for centralized key management.

### Design

New provider `ProxyProvider` that routes all calls through an HTTP endpoint:

```python
class ProxyProvider(Provider):
    """Routes LLM calls through an HTTP proxy."""

    def __init__(self, proxy_url: str, auth_token: str | None = None,
                 model: str = "") -> None:
        self._proxy_url = proxy_url
        self._auth_token = auth_token
        self._model = model

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None, thinking=None):
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        import json, urllib.request
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        req = urllib.request.Request(
            f"{self._proxy_url}/api/complete",
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        return Response(content=data["content"], tool_calls=[], usage=data.get("usage", {}))
```

Register with provider registry:
```python
register_provider("proxy", lambda model="", base_url=None, auth_token=None, **kw:
    ProxyProvider(proxy_url=base_url, auth_token=auth_token, model=model))
```

### Files Changed
| File | Change |
|------|--------|
| `chimera/providers/proxy.py` | New — ProxyProvider |
| `tests/test_proxy_provider.py` | New |

---

## Feature 7: Extended Event Hooks

### Problem
16 event types vs pi-mono's 40+. Missing: model request/response hooks, turn lifecycle, streaming lifecycle.

### Design

Add 10 new event types covering the most important gaps:

```python
# New events in chimera/events/types.py

@dataclass
class ModelRequestEvent(Event):
    """About to send a request to the LLM provider."""
    type: str = field(default="model_request", init=False)
    model: str = ""
    message_count: int = 0
    tool_count: int = 0

@dataclass
class ModelResponseEvent(Event):
    """Received a response from the LLM provider."""
    type: str = field(default="model_response", init=False)
    model: str = ""
    content_length: int = 0
    tool_calls_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass
class TurnStartEvent(Event):
    """A new agent turn (step) is starting."""
    type: str = field(default="turn_start", init=False)
    turn_number: int = 0

@dataclass
class TurnEndEvent(Event):
    """An agent turn (step) has completed."""
    type: str = field(default="turn_end", init=False)
    turn_number: int = 0
    tool_calls_count: int = 0

@dataclass
class StreamStartEvent(Event):
    """Streaming response has started."""
    type: str = field(default="stream_start", init=False)
    model: str = ""

@dataclass
class StreamEndEvent(Event):
    """Streaming response has completed."""
    type: str = field(default="stream_end", init=False)
    total_tokens: int = 0

@dataclass
class AgentStartEvent(Event):
    """Agent loop has started."""
    type: str = field(default="agent_start", init=False)
    max_steps: int = 0

@dataclass
class AgentEndEvent(Event):
    """Agent loop has completed."""
    type: str = field(default="agent_end", init=False)
    steps: int = 0
    success: bool = True
    total_cost: float = 0.0

@dataclass
class SteeringEvent(Event):
    """A steering message was injected."""
    type: str = field(default="steering", init=False)
    content: str = ""

@dataclass
class CancellationEvent(Event):
    """The agent was cancelled."""
    type: str = field(default="cancellation", init=False)
    at_step: int = 0
```

Wire them into `loop.py` at the appropriate points:
- `ModelRequestEvent` before `provider.complete()` / `provider.stream()`
- `ModelResponseEvent` after response received
- `TurnStartEvent` / `TurnEndEvent` around each step
- `StreamStartEvent` / `StreamEndEvent` around stream accumulation
- `AgentStartEvent` / `AgentEndEvent` at loop entry/exit
- `SteeringEvent` when steering messages are drained
- `CancellationEvent` when cancellation is detected

### Files Changed
| File | Change |
|------|--------|
| `chimera/events/types.py` | Add 10 new event dataclasses |
| `chimera/core/loop.py` | Emit new events at appropriate points |
| `tests/test_events_extended.py` | New |

---

## Implementation Order

Independent features (can be parallelized):
1. OAuth Flows — touches only `chimera/auth/`
2. Thinking Levels — touches `chimera/providers/`
3. Skills Discovery — new file only
4. Proxy Provider — new file only
5. Extended Events — touches `chimera/events/` + `chimera/core/loop.py`

Sequential (depends on above):
6. Auto-Compaction — touches `chimera/sessions/session.py` + `chimera/cli/code.py`
7. Model Cycling — touches `chimera/cli/code.py` + `chimera/cli/main.py`
