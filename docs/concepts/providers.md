# Providers

A **Provider** is an abstraction over an LLM backend. Any class that implements the `Provider` ABC (specifically, the `complete()` method) can power a Chimera agent. This design lets you swap between Anthropic, OpenAI, Google Gemini, Ollama, or any OpenAI-compatible endpoint without changing agent code.

## The Provider ABC

```python
from chimera.providers.base import Provider, Response

class Provider(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response: ...

    @property
    @abstractmethod
    def context_window(self) -> int: ...

    @property
    @abstractmethod
    def supports_tool_use(self) -> bool: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...
```

The `complete()` method takes a list of messages and optional tool schemas, and returns a `Response`.

## Response Dataclass

```python
@dataclass
class Response:
    content: str               # Text content of the response
    tool_calls: list[ToolCall] # Tool invocations requested by the model
    usage: dict[str, int]      # {"input_tokens": N, "output_tokens": M}

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
```

There is also a `StreamEvent` dataclass for streaming responses, with types `"text_delta"`, `"tool_call_start"`, `"tool_call_delta"`, and `"done"`.

## The `create_provider()` Factory

The recommended way to create a provider is through the factory function, which auto-detects the provider type from the model name. The `model` parameter is optional -- when omitted, it falls back to the `ANTHROPIC_MODEL` environment variable:

```python
from chimera.providers.factory import create_provider

# Model from ANTHROPIC_MODEL env var (default fallback)
provider = create_provider()

# Auto-detected as Anthropic
provider = create_provider(model="claude-sonnet-4-20250514")

# Auto-detected as OpenAI
provider = create_provider(model="gpt-4o")

# Auto-detected as Google
provider = create_provider(model="gemini-2.0-flash")

# Explicit provider type
provider = create_provider(provider_type="ollama", model="llama3.1")

# OpenAI-compatible endpoint
provider = create_provider(
    provider_type="compatible",
    model="my-model",
    base_url="https://my-api.example.com/v1",
    api_key="sk-...",
)
```

### Auto-detection Rules

The factory infers the provider from the model name prefix:

| Model prefix | Provider |
|-------------|----------|
| `claude*` | Anthropic |
| `gpt*`, `o1*`, `o3*`, `codex*` | OpenAI |
| `gemini*` | Google |
| `llama*`, `mistral*`, `qwen*`, `phi*` | Ollama |

If no prefix matches, it falls back to checking environment variables (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`).

## Supported Providers

| Provider | Class | Install extra | Model examples |
|----------|-------|---------------|----------------|
| Anthropic | `AnthropicProvider` | `chimera-ai[anthropic]` | `claude-opus-4`, `claude-sonnet-4`, `claude-haiku-3.5` |
| OpenAI | `OpenAIProvider` | `chimera-ai[openai]` | `gpt-4o`, `gpt-4o-mini`, `o1`, `o3-mini` |
| Google Gemini | `GoogleProvider` | `chimera-ai[google]` | `gemini-2.0-flash`, `gemini-1.5-pro` |
| Ollama | `OllamaProvider` | (none) | `llama3.1`, `mistral`, `qwen2.5` |
| Modal | `ModalProvider` | `chimera-ai[modal]` | Any model deployed on Modal |
| OpenAI-compatible | `OpenAICompatibleProvider` | (none) | Any model behind an OpenAI-compatible API |

## Environment Variable Configuration

Each provider reads credentials from environment variables when no explicit `api_key` is passed:

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | Anthropic |
| `ANTHROPIC_BASE_URL` | Anthropic (custom endpoint) |
| `OPENAI_API_KEY` | OpenAI |
| `GOOGLE_API_KEY` | Google Gemini |

!!! note "Ollama runs locally"
    The Ollama provider defaults to `http://localhost:11434` and requires no API key. Override with `base_url`.

## Cost Tracking

Chimera tracks token costs automatically via `chimera.providers.cost.calculate_cost()`:

```python
from chimera.providers.cost import calculate_cost

usage = {"input_tokens": 1000, "output_tokens": 500}
cost = calculate_cost("claude-sonnet-4-20250514", usage)
# Returns cost in USD based on published pricing
```

The pricing table covers major models:

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|-------|----------------------|------------------------|
| `claude-opus-4` | $15.00 | $75.00 |
| `claude-sonnet-4` | $3.00 | $15.00 |
| `claude-haiku-3.5` | $0.80 | $4.00 |
| `gpt-4o` | $2.50 | $10.00 |
| `gpt-4o-mini` | $0.15 | $0.60 |
| `gemini-2.0-flash` | $0.10 | $0.40 |

Cost is accumulated in `AgentResult.cost` and `SynthesisResult.total_cost`, giving you full visibility into spend.

## Code Example: Custom Provider

You can implement a custom provider by subclassing `Provider`:

```python
from chimera.providers.base import Provider, Response
from chimera.types import Message

class MyCustomProvider(Provider):
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        # Call your custom LLM endpoint
        result = my_api.chat(messages, tools)
        return Response(
            content=result.text,
            tool_calls=[],
            usage={"input_tokens": result.in_tokens, "output_tokens": result.out_tokens},
        )

    @property
    def context_window(self) -> int:
        return 128_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "my-custom-model"
```

## API Reference

- `chimera.providers.base.Provider` -- abstract base class
- `chimera.providers.base.Response` -- response dataclass
- `chimera.providers.base.StreamEvent` -- streaming event dataclass
- `chimera.providers.factory.create_provider` -- factory function
- `chimera.providers.cost.calculate_cost` -- cost estimation
