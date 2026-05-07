"""Named agent presets that compose tools, loops, and prompts.

Each preset recreates the architecture of a well-known coding agent by
selecting the right combination of primitives from Chimera's layered stack.

.. deprecated:: 0.5
   :meth:`AgentPreset.build` is deprecated and will be removed in v0.7.0.
   Use :class:`chimera.assembly.coding_agent.CodingAgent` and
   :meth:`CodingAgent.from_preset` for the canonical, fully-assembled stack.
   Internally, :func:`AgentPreset._compose` is the non-deprecated escape
   hatch used by the test suite to keep exercising the legacy
   ``Agent`` + loop wiring without triggering the removal warning.

Usage (canonical)::

    from chimera.assembly.coding_agent import CodingAgent

    agent = CodingAgent.from_preset("swebench")   # SWE_AGENT analogue
    agent = CodingAgent.from_preset("codex")      # CODEX analogue
    agent = CodingAgent.from_preset("coding_agent")  # AIDER / CLINE analogue
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.core.tool import BaseTool
    from chimera.env.base import Environment
    from chimera.providers.base import Provider


class AgentPreset:
    """Named agent configurations inspired by real coding agents.

    Each preset composes the right tools, loop, context strategy,
    and prompt to recreate a specific agent's architecture.

    .. deprecated:: 0.5
       :meth:`build` emits a :class:`DeprecationWarning` and will be removed
       in v0.7.0. Use :meth:`chimera.assembly.coding_agent.CodingAgent.from_preset`
       for the canonical, fully-assembled stack. The private :meth:`_compose`
       remains as a non-warning escape hatch for tests of the legacy
       ``Agent`` + loop wiring.

    Args:
        name: Short identifier for this preset.
        description: Human-readable summary of the preset's purpose.
        tool_names: List of tool names to resolve, or ``"AGENT_TOOLS"`` /
            ``"DEFAULT_TOOLS"`` to use a pre-built group.
        loop_type: One of ``"react"``, ``"retry"``, ``"plan_act"``, or
            ``"lint_feedback"``.
        loop_kwargs: Extra keyword arguments forwarded to the loop constructor.
        system_prompt: System prompt text injected into the agent.
        max_steps: Maximum ReAct steps per run.
    """

    # Class-level preset instances are assigned after the class body.
    SWE_AGENT: AgentPreset
    CODEX: AgentPreset
    AIDER: AgentPreset
    CLINE: AgentPreset

    def __init__(
        self,
        name: str,
        description: str,
        tool_names: list[str],
        loop_type: str,
        loop_kwargs: dict[str, Any] | None = None,
        system_prompt: str = "",
        max_steps: int = 25,
    ) -> None:
        self.name = name
        self.description = description
        self.tool_names = tool_names
        self.loop_type = loop_type
        self.loop_kwargs = loop_kwargs or {}
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    def build(self, provider: Provider, env: Environment | None = None) -> Agent:
        """Build an Agent from this preset.

        .. deprecated:: 0.5
           Use :meth:`chimera.assembly.coding_agent.CodingAgent.from_preset`
           for the canonical replacement. This method will be removed in
           v0.7.0; internal callers (e.g., the test suite) use
           :meth:`_compose` to keep exercising the legacy ``Agent`` + loop
           wiring without the warning.

        Args:
            provider: LLM provider for completions.
            env: Optional execution environment (unused during construction
                but accepted for API symmetry with other factory functions).

        Returns:
            A fully-wired :class:`~chimera.core.agent.Agent`.
        """
        import warnings
        warnings.warn(
            f"AgentPreset.build() will be removed in v0.7.0. "
            f"Use chimera.assembly.coding_agent.CodingAgent.from_preset() "
            f"instead. See docs/architecture.md or research notes.\n"
            f"  from chimera.assembly.coding_agent import CodingAgent\n"
            f"  agent = CodingAgent.from_preset('{self.name}')",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._compose(provider, env)

    def _compose(
        self, provider: Provider, env: Environment | None = None
    ) -> Agent:
        """Construct an Agent from this preset without emitting the
        :class:`DeprecationWarning` raised by :meth:`build`.

        This is the non-deprecated escape hatch used by the test suite to
        verify that the legacy ``Agent`` + loop wiring still composes
        correctly. End users should migrate to
        :meth:`chimera.assembly.coding_agent.CodingAgent.from_preset` — when
        :meth:`build` is removed in v0.7.0 this method may also be removed
        as part of the broader ``AgentPreset`` deprecation cleanup.

        Args:
            provider: LLM provider for completions.
            env: Optional execution environment (unused during construction
                but accepted for API symmetry with :meth:`build`).

        Returns:
            A fully-wired :class:`~chimera.core.agent.Agent`.
        """
        # `env` is kept for API symmetry with the historical build() signature.
        del env
        from chimera.core.agent import Agent
        from chimera.core.prompt import Prompt

        tools = self._build_tools()
        loop = self._build_loop()
        prompt = Prompt.from_string(self.system_prompt) if self.system_prompt else None

        return Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)

    def _build_tools(self) -> list[BaseTool]:
        """Build tool list from names.

        Returns:
            List of :class:`~chimera.core.tool.BaseTool` instances.
        """
        from chimera.core.tool_group import AGENT_TOOLS, DEFAULT_TOOLS

        # Special tool group names
        if "AGENT_TOOLS" in self.tool_names:
            return list(AGENT_TOOLS)
        if "DEFAULT_TOOLS" in self.tool_names:
            return list(DEFAULT_TOOLS)

        # Build from individual names
        tools = []
        tool_map = {t.name: t for t in AGENT_TOOLS}
        for name in self.tool_names:
            if name in tool_map:
                tools.append(tool_map[name])
        return tools

    def _build_loop(self) -> Any:
        """Build the appropriate loop based on :attr:`loop_type`.

        Returns:
            A loop instance (ReAct, RetryLoop, PlanActLoop, or
            LintFeedbackLoop).
        """
        from chimera.core.loop import ReAct

        if self.loop_type == "react":
            return ReAct(max_steps=self.max_steps)

        elif self.loop_type == "retry":
            from chimera.core.loops.retry import RetryLoop

            inner = ReAct(max_steps=self.max_steps)
            return RetryLoop(
                inner=inner,
                max_retries=self.loop_kwargs.get("max_retries", 3),
            )

        elif self.loop_type == "plan_act":
            from chimera.core.loops.plan_act import PlanActLoop

            return PlanActLoop(
                plan_steps=self.loop_kwargs.get("plan_steps", 10),
                act_steps=self.max_steps,
            )

        elif self.loop_type == "lint_feedback":
            from chimera.core.loops.lint_feedback import LintFeedbackLoop

            inner = ReAct(max_steps=self.max_steps)
            return LintFeedbackLoop(
                inner=inner,
                linter=self.loop_kwargs.get("linter", "ruff"),
                max_lint_rounds=self.loop_kwargs.get("max_lint_rounds", 3),
            )

        return ReAct(max_steps=self.max_steps)


# ---- The 4 presets --------------------------------------------------------

AgentPreset.SWE_AGENT = AgentPreset(
    name="swe_agent",
    description="SWE-Agent style: minimal tools, retry loop, focused on benchmarks.",
    tool_names=["read_file", "edit_file", "bash", "search", "list_files"],
    loop_type="retry",
    loop_kwargs={"max_retries": 3},
    max_steps=30,
    system_prompt=(
        "You are a software engineering agent. You solve coding tasks by reading "
        "code, making targeted edits, and running tests. Be methodical: understand "
        "the problem first, locate the relevant code, make minimal changes, and verify."
    ),
)

AgentPreset.CODEX = AgentPreset(
    name="codex",
    description="Codex style: full tools, standard loop, memory-aware.",
    tool_names=["AGENT_TOOLS"],
    loop_type="react",
    max_steps=50,
    system_prompt=(
        "You are a powerful coding agent with full access to the filesystem, "
        "shell, and development tools. You can create, modify, and test code. "
        "Follow the user's instructions precisely. Think step by step for "
        "complex tasks."
    ),
)

AgentPreset.AIDER = AgentPreset(
    name="aider",
    description="Aider style: lint feedback loop, git-aware, edit-focused.",
    tool_names=[
        "read_file", "edit_file", "bash", "search",
        "list_files", "git", "test", "repo_map",
    ],
    loop_type="lint_feedback",
    loop_kwargs={"linter": "ruff", "max_lint_rounds": 2},
    max_steps=20,
    system_prompt=(
        "You are a pair-programming agent. You help edit code files to accomplish "
        "tasks. Read the code first, make targeted edits, and verify with tests. "
        "Use the repo_map to understand the codebase structure. "
        "After making changes, the linter will check your work automatically."
    ),
)

AgentPreset.CLINE = AgentPreset(
    name="cline",
    description="Cline style: plan/act dual mode, full tools, IDE-like.",
    tool_names=["AGENT_TOOLS"],
    loop_type="plan_act",
    loop_kwargs={"plan_steps": 8},
    max_steps=25,
    system_prompt=(
        "You are an IDE-integrated coding assistant. First, explore the codebase "
        "in a read-only planning phase to understand the structure. Then execute "
        "your plan with full tool access. Be thorough in planning, efficient in "
        "execution."
    ),
)
