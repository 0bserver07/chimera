# Core Concepts

Chimera is a framework for building LLM-powered coding agents that can synthesize, test, and iterate on code. Its architecture is organized into six layers: **Agents** orchestrate the interaction between a language model, a set of tools, and a reasoning loop; **Providers** abstract over LLM backends (Anthropic, OpenAI, Google, Ollama, and more); **Tools** give agents the ability to read, write, search, and execute code; **Loops** define the reasoning strategy the agent follows (ReAct, Plan-and-Execute, Reflexion, Tree-of-Thought); **Environments** provide sandboxed workspaces where generated code lives and gets tested; and **Training** ties everything together with specs, strategies, and constraints to drive iterative code synthesis until tests pass.

## The Six Core Concepts

| Concept | Description |
|---------|-------------|
| [Agents](agents.md) | The central orchestrator. An Agent wires together a Provider, Tools, a Loop, and a Prompt, then runs tasks in an Environment. |
| [Providers](providers.md) | LLM backend abstraction. Any class implementing `complete()` can serve as a provider -- Anthropic, OpenAI, Google Gemini, Ollama, or any OpenAI-compatible API. |
| [Tools](tools.md) | Capabilities the agent can invoke during reasoning -- file I/O, shell commands, search, git, and more. Chimera ships 20 built-in tools and supports custom tools via a class or decorator. |
| [Loops](loops.md) | Execution strategies that control how the agent reasons and acts. Choose from ReAct (default), PlanAndExecute, Reflexion, or TreeOfThought. |
| [Environments](environments.md) | Where generated code lives and gets tested. Local filesystem, git-based checkpointing, or Docker container isolation. |
| [Training](training.md) | The synthesis engine. A Spec defines what to build, a Strategy controls how to iterate, and Constraints act as guardrails -- together they drive an agent toward passing tests. |

## How They Fit Together

```
synthesize("Build a calculator", tests="tests/", model="claude-sonnet-4-20250514")
     |
     v
  Trainer  --->  Strategy (e.g. TestConvergence)
     |               |
     v               v
   Agent  -------> Loop (e.g. ReAct)
   /   \              |
  v     v             v
Provider  Tools ---> Environment
```

The `synthesize()` one-liner wires all six layers together with sensible defaults. For full control, compose each layer yourself -- see the individual concept pages for details.
