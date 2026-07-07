"""Named agent presets that compose tools, loops, and prompts.

Each preset pairs a distinct reasoning-loop posture with a matching tool set
and system prompt, assembled from Chimera's layered primitives. The names are
loop-descriptive — ``RETRY_MIN`` / ``REACT_FULL`` / ``LINT_LOOP`` /
``PLAN_ACT`` — rather than named after the external coding agents whose shapes
they echo.

The canonical user-facing API is
:meth:`chimera.assembly.coding_agent.CodingAgent.from_preset`. The
:class:`AgentPreset` class below is retained for tests of the legacy
``Agent`` + loop wiring; new code should use ``CodingAgent.from_preset``.

Usage (canonical)::

    from chimera.assembly.coding_agent import CodingAgent

    agent = CodingAgent.from_preset("swebench")   # RETRY_MIN analogue
    agent = CodingAgent.from_preset("codex")      # REACT_FULL analogue
    agent = CodingAgent.from_preset("coding_agent")  # LINT_LOOP / PLAN_ACT analogue

Back-compat: the former brand-named attributes (``SWE_AGENT`` / ``CODEX`` /
``AIDER`` / ``CLINE``) remain as aliases of the canonical presets — see the
back-compat block at the bottom of this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.core.tool import BaseTool
    from chimera.env.base import Environment
    from chimera.providers.base import Provider


class AgentPreset:
    """Named agent configurations, each pinning a distinct loop posture.

    Each preset composes the right tools, loop, context strategy,
    and prompt to realise a specific reasoning-loop shape.

    The user-facing entry point is
    :meth:`chimera.assembly.coding_agent.CodingAgent.from_preset`; the
    :meth:`_compose` method here is the in-tree escape hatch used by
    tests of the legacy ``Agent`` + loop wiring.

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
    # Canonical, loop-descriptive names:
    RETRY_MIN: AgentPreset
    REACT_FULL: AgentPreset
    LINT_LOOP: AgentPreset
    PLAN_ACT: AgentPreset
    # Back-compat aliases (assigned at the bottom of this module):
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

    def _compose(
        self, provider: Provider, env: Environment | None = None
    ) -> Agent:
        """Construct an Agent from this preset.

        This is the in-tree escape hatch used by the test suite to
        verify that the legacy ``Agent`` + loop wiring still composes
        correctly. End users should use
        :meth:`chimera.assembly.coding_agent.CodingAgent.from_preset`
        instead; this method is intentionally private.

        Args:
            provider: LLM provider for completions.
            env: Optional execution environment (unused during construction
                but accepted for API symmetry with the historical factory
                signature).

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

AgentPreset.RETRY_MIN = AgentPreset(
    name="retry-min",
    description="Retry-minimal: minimal tools + retry loop, benchmark-focused.",
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

AgentPreset.REACT_FULL = AgentPreset(
    name="react-full",
    description="React-full: full tools, standard ReAct loop, memory-aware.",
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

AgentPreset.LINT_LOOP = AgentPreset(
    name="lint-loop",
    description="Lint-loop: lint-feedback edit loop, git-aware, edit-focused.",
    tool_names=[
        "read_file", "write_file", "edit_file", "bash", "search",
        "list_files", "git", "test", "repo_map",
    ],
    loop_type="lint_feedback",
    loop_kwargs={"linter": "ruff", "max_lint_rounds": 2},
    max_steps=20,
    system_prompt=(
        "You are a pair-programming agent. You write and edit code files to "
        "accomplish tasks. Read the code first when it exists, make targeted "
        "edits, and verify with tests. Use the repo_map to understand the "
        "codebase structure. If the task is to write new code from scratch and no "
        "file exists yet, create it with write_file first, then refine it — never "
        "wait for pre-existing files. Always leave the complete solution as a file "
        "on disk; the linter then checks your work automatically."
    ),
)

AgentPreset.PLAN_ACT = AgentPreset(
    name="plan-act",
    description="Plan/act: plan-then-act dual mode, full tools, IDE-like.",
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


# Back-compat aliases — the replicas were formerly named after the coding
# agents they imitate. Canonical names above are loop-descriptive.
AgentPreset.SWE_AGENT = AgentPreset.RETRY_MIN
AgentPreset.CODEX = AgentPreset.REACT_FULL
AgentPreset.AIDER = AgentPreset.LINT_LOOP
AgentPreset.CLINE = AgentPreset.PLAN_ACT
