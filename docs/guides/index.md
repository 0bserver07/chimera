# Guides

Hands-on walkthroughs that take you from zero to a working Chimera application.

| Guide | What you will build |
|---|---|
| [Build a Coding Agent](build-a-coding-agent.md) | An end-to-end interactive agent that reads files, runs commands, and carries multi-turn conversations. |
| [Add a Custom Tool](add-custom-tool.md) | Extend agent capabilities with your own tools -- from a quick decorator to a full `BaseTool` subclass. |
| [Compose Agents](compose-agents.md) | Multi-agent patterns: pipelines, ensembles, and supervisor/worker topologies. |
| [Configure Permissions](configure-permissions.md) | Safe tool execution with permission rules, presets, and event monitoring. |
| [Module Integration Checklist](module-integration-checklist.md) | Step-by-step checklist for adding new modules, tools, strategies, or features. |

**Prerequisites** -- Install chimera and have an Anthropic API key (or another supported provider) ready:

```bash
pip install chimera
export ANTHROPIC_API_KEY="sk-..."
```

Each guide is self-contained and includes complete, runnable code examples.
