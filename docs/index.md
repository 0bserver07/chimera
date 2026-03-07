---
hide:
  - navigation
  - toc
---

# Chimera

## A composable coding agent framework

A Python framework that treats **code synthesis as machine learning**.
Define a spec (your loss function), let agents iterate (training), and deploy
the resulting codebase (trained model) -- without inspecting internals.

---

## Three Levels of Control

=== "One-Liner"

    Synthesize an entire codebase from a prompt and test suite in a single call.

    ```python
    import chimera

    result = chimera.synthesize(
        "Build a REST API for tasks",
        tests="./tests/",
    )
    print(f"Converged: {result.converged}, Cost: ${result.total_cost:.4f}")
    ```

=== "Configured"

    Wire up each component explicitly -- provider, agent, tools, strategy.

    ```python
    import chimera

    trainer = chimera.Trainer(
        spec=chimera.Spec.from_tests("./tests/", "Build a task manager"),
        agent=chimera.Agent(
            provider=chimera.create_provider("claude-sonnet-4"),
            tools=list(chimera.DEFAULT_TOOLS),
            loop=chimera.ReAct(max_steps=50),
        ),
    )
    result = trainer.synthesize(
        strategy=chimera.TestConvergence(max_epochs=10),
    )
    ```

=== "Framework Author"

    Subclass `Agent` and `Strategy` to build your own synthesis pipeline.

    ```python
    import chimera

    class MyAgent(chimera.Agent):
        tools = [chimera.tools.read, chimera.tools.edit, MyCustomTool()]
        loop = chimera.ReAct(max_steps=100)

    class MyStrategy(chimera.Strategy):
        def run(self, trainer, spec, agent, env, **kw):
            for epoch in range(self.max_epochs):
                result = agent.run(spec.prompt, env=env)
                if self.evaluate(result):
                    return self.success(result)
            return self.failure()
    ```

---

## Install

```bash
pip install chimera-ai                  # core (zero dependencies)
pip install chimera-ai[anthropic]       # + Claude support
pip install chimera-ai[openai]          # + OpenAI support
pip install chimera-ai[all]             # all providers
```

Requires **Python 3.11+**.

---

## What's Inside

| Category             | Count | Highlights                                                                 |
|----------------------|------:|----------------------------------------------------------------------------|
| **Providers**        |     6 | Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compatible               |
| **Tools**            |    16 | read, write, edit, bash, search, git, test, web_fetch, repo_map, delegate, browser, image_read, import_graph, replace_in_file, verify, list_files |
| **Loops**            |     4 | ReAct, PlanAndExecute, Reflexion, TreeOfThought                           |
| **Composition**      |     3 | Pipeline, Ensemble, Supervisor                                            |
| **Strategies**       |     7 | TestConvergence, Curriculum, Ensemble, Passthrough, TreeSearch, MajorityVoting, AIMOEnsemble |
| **Environments**     |     6 | Local, Docker, Git, Remote, Cloud, PersistentShell                        |
| **Workflows**        |     6 | CI Fix, Code Review, Research, Migration, Doc Generation, Test Generation |
| **Infrastructure**   |    14 | Security, Secrets, Permissions, Events, Sessions, Compaction, Streaming, Detection, Config, Plugins, MCP, LSP, Auth, Checkpoints |

---

## Explore

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Getting Started**

    ---

    Install Chimera, configure a provider, and run your first agent in five
    minutes.

    [:octicons-arrow-right-24: Getting Started](getting-started.md)

-   :material-book-open-variant:{ .lg .middle } **Core Concepts**

    ---

    Agents, providers, tools, loops, environments, and the training layer
    explained.

    [:octicons-arrow-right-24: Concepts](concepts/index.md)

-   :material-puzzle:{ .lg .middle } **Extension Modules**

    ---

    Events, compaction, detection, permissions, streaming, sessions, auth,
    agent configuration, security, secrets, critic, ACP, plugins, config,
    checkpoints, cost tracking, MCP, and LSP.

    [:octicons-arrow-right-24: Modules](modules/index.md)

-   :material-code-tags:{ .lg .middle } **API Reference**

    ---

    Full autodoc reference for every public class and function.

    [:octicons-arrow-right-24: Reference](reference/index.md)

-   :material-cog-sync:{ .lg .middle } **Workflows**

    ---

    CI fix, code review, research, migration planning, doc generation,
    and test generation -- ready-made pipelines for common tasks.

    [:octicons-arrow-right-24: Workflows](workflows/index.md)

-   :material-console:{ .lg .middle } **CLI & REPL**

    ---

    11 subcommands and an interactive REPL with 14 slash commands for
    hands-on agent interaction.

    [:octicons-arrow-right-24: CLI & REPL](modules/cli.md)

</div>
