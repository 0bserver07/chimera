"""Role-based team composition: agents with specialized roles collaborate.

Each agent in the team has a :class:`Role` that defines its system prompt,
tool access, loop type, and step budget.  The :class:`RoleBasedTeam`
orchestrates a sequential workflow where each role's output feeds into the
next, forming a planner -> coder -> reviewer -> tester pipeline by default.

Example:
    ```python
    from chimera.composition.roles import RoleBasedTeam
    from chimera.providers.factory import create_provider

    provider = create_provider(model="claude-sonnet-4-20250514")
    team = RoleBasedTeam(provider=provider)
    result = team.run("Build a REST API for user management.", env=sandbox)
    ```

Custom roles:
    ```python
    from chimera.composition.roles import Role, RoleBasedTeam

    analyst = Role(
        name="analyst",
        description="Analyze requirements and break them into tasks",
        tool_names=["read_file", "search", "list_files", "think"],
        system_prompt="You are a requirements analyst...",
        loop_type="plan_act",
    )
    team = RoleBasedTeam(provider=provider, roles=[analyst, CODER])
    ```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool import BaseTool
from chimera.types import AgentResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.providers.base import Provider


@dataclass
class Role:
    """Definition of a specialized agent role within a team.

    A role describes the persona, capabilities, and constraints for one
    member of a :class:`RoleBasedTeam`.  It is a *specification* (not an
    agent instance) -- the team materialises an :class:`Agent` from each
    role at run time.

    Attributes:
        name: Short identifier, e.g. ``"planner"`` or ``"coder"``.
        description: Human-readable summary of what this role does.
        tool_names: Names of tools this role is allowed to use.
        system_prompt: Full system prompt injected into the agent.
        loop_type: Which reasoning loop to use.  One of ``"react"``,
            ``"plan_act"``, ``"retry"``, ``"reflexion"``, ``"tree_of_thought"``.
        max_steps: Maximum loop iterations for this role's agent.
    """

    name: str
    description: str
    tool_names: list[str] = field(default_factory=list)
    system_prompt: str = ""
    loop_type: str = "react"
    max_steps: int = 20


# ---------------------------------------------------------------------------
# Built-in roles
# ---------------------------------------------------------------------------

PLANNER = Role(
    name="planner",
    description="Explores the codebase and creates a detailed implementation plan",
    tool_names=["read_file", "search", "list_files", "repo_map", "think"],
    system_prompt=(
        "You are a software architect and planner. Your job is to:\n"
        "1. Understand the task requirements thoroughly.\n"
        "2. Explore the existing codebase to understand its structure.\n"
        "3. Produce a detailed, step-by-step implementation plan.\n\n"
        "Do NOT write code or make changes. Only explore and plan.\n"
        "Output your plan as a numbered list of concrete steps."
    ),
    loop_type="plan_act",
    max_steps=15,
)

CODER = Role(
    name="coder",
    description="Implements code changes according to the plan",
    tool_names=[
        "read_file", "write_file", "edit_file", "bash",
        "search", "list_files", "replace_in_file", "think",
    ],
    system_prompt=(
        "You are an expert coder. You receive a task and a plan from the "
        "planner. Implement the plan precisely:\n"
        "1. Follow the plan step by step.\n"
        "2. Write clean, well-documented code.\n"
        "3. Ensure your changes are consistent with the existing codebase style.\n\n"
        "When done, summarize what you changed and any decisions you made."
    ),
    loop_type="react",
    max_steps=30,
)

REVIEWER = Role(
    name="reviewer",
    description="Reviews code changes for correctness, style, and potential issues",
    tool_names=["read_file", "search", "list_files", "repo_map", "think"],
    system_prompt=(
        "You are a code reviewer. Your job is to review the code changes "
        "made by the coder. Check for:\n"
        "1. Correctness: Does the code do what the plan intended?\n"
        "2. Style: Is it consistent with the codebase conventions?\n"
        "3. Edge cases: Are error cases handled?\n"
        "4. Security: Are there any security concerns?\n\n"
        "Do NOT make changes. Only read and review.\n"
        "Output a structured review with issues found (if any) and a "
        "verdict: APPROVE or REQUEST_CHANGES."
    ),
    loop_type="react",
    max_steps=15,
)

TESTER = Role(
    name="tester",
    description="Runs tests and verifies the implementation works correctly",
    tool_names=["read_file", "bash", "test", "search", "list_files", "think"],
    system_prompt=(
        "You are a QA engineer. Your job is to verify that the code changes "
        "work correctly:\n"
        "1. Run existing tests to check for regressions.\n"
        "2. If appropriate, write and run new tests for the changes.\n"
        "3. Verify the overall functionality.\n\n"
        "Output a test report: which tests passed, which failed, and your "
        "overall assessment of the implementation quality."
    ),
    loop_type="react",
    max_steps=20,
)

DEFAULT_ROLES: list[Role] = [PLANNER, CODER, REVIEWER, TESTER]


# ---------------------------------------------------------------------------
# Loop factory
# ---------------------------------------------------------------------------

def _create_loop(loop_type: str, max_steps: int) -> ReAct:
    """Instantiate a loop by name.

    Args:
        loop_type: One of ``"react"``, ``"plan_act"``, ``"retry"``,
            ``"reflexion"``, ``"tree_of_thought"``.
        max_steps: Step budget for the loop.

    Returns:
        A loop instance compatible with :class:`Agent`.

    Raises:
        ValueError: If *loop_type* is not recognised.
    """
    if loop_type == "react":
        return ReAct(max_steps=max_steps)
    if loop_type == "plan_act":
        from chimera.core.loops.plan_act import PlanActLoop
        return PlanActLoop(plan_steps=max(max_steps // 3, 3), act_steps=max_steps)  # type: ignore[return-value]
    if loop_type == "retry":
        from chimera.core.loops.retry import RetryLoop
        return RetryLoop(inner=ReAct(max_steps=max_steps))  # type: ignore[return-value]
    if loop_type == "reflexion":
        from chimera.core.loops.reflexion import Reflexion
        return Reflexion(max_steps=max_steps)  # type: ignore[return-value]
    if loop_type == "tree_of_thought":
        from chimera.core.loops.tree_of_thought import TreeOfThought
        return TreeOfThought(max_steps=max_steps)  # type: ignore[return-value]
    raise ValueError(
        f"Unknown loop_type {loop_type!r}. "
        f"Expected one of: react, plan_act, retry, reflexion, tree_of_thought."
    )


def _filter_tools(all_tools: list[BaseTool], allowed_names: list[str]) -> list[BaseTool]:
    """Return tools whose names appear in *allowed_names*.

    Args:
        all_tools: The full set of available tools.
        allowed_names: Tool names this role is permitted to use.

    Returns:
        Filtered list preserving the order from *all_tools*.
    """
    allowed = set(allowed_names)
    return [t for t in all_tools if t.name in allowed]


# ---------------------------------------------------------------------------
# RoleBasedTeam
# ---------------------------------------------------------------------------

class RoleBasedTeam:
    """Multi-agent team where each agent has a specialized role.

    Roles execute sequentially: each role's output is passed as context
    to the next role.  By default the team uses the four built-in roles
    (planner, coder, reviewer, tester), but any combination can be
    supplied.

    Attributes:
        provider: The LLM backend shared by all role agents.
        roles: Ordered list of roles defining the team pipeline.
        tools: Full tool pool from which each role's subset is drawn.
    """

    def __init__(
        self,
        provider: Provider,
        roles: list[Role] | None = None,
        tools: list[BaseTool] | None = None,
    ) -> None:
        """Initialise the team.

        Args:
            provider: LLM provider used by every role agent.
            roles: Ordered list of roles.  Defaults to
                :data:`DEFAULT_ROLES` (planner -> coder -> reviewer -> tester).
            tools: Full tool pool.  Each role agent receives only the
                subset matching its :attr:`Role.tool_names`.  If ``None``,
                a standard tool set is constructed lazily from
                :mod:`chimera.core.tool_group`.
        """
        self.provider = provider
        self.roles: list[Role] = list(roles) if roles is not None else list(DEFAULT_ROLES)
        self._tools = tools

    def add_role(self, role: Role) -> None:
        """Append a role to the team pipeline.

        Args:
            role: The role to add.
        """
        self.roles.append(role)

    @property
    def tools(self) -> list[BaseTool]:
        """Lazily resolved tool pool."""
        if self._tools is None:
            from chimera.core.tool_group import AGENT_TOOLS
            self._tools = list(AGENT_TOOLS)
        return self._tools

    def _build_agent(self, role: Role) -> Agent:
        """Materialise an :class:`Agent` from a role definition.

        Args:
            role: The role specification.

        Returns:
            A fully configured :class:`Agent`.
        """
        role_tools = _filter_tools(self.tools, role.tool_names)
        loop = _create_loop(role.loop_type, role.max_steps)
        prompt = Prompt.from_string(role.system_prompt)
        return Agent(
            provider=self.provider,
            tools=role_tools,
            loop=loop,
            prompt=prompt,
            name=role.name,
        )

    def run(self, task: str, env: Environment | None) -> AgentResult:
        """Execute the team pipeline.

        Each role runs in sequence.  The first role receives the raw task.
        Subsequent roles receive the original task *plus* a cumulative
        summary of all prior roles' outputs, so context flows forward
        through the pipeline.

        Args:
            task: High-level task description.
            env: Shared execution environment (or ``None``).

        Returns:
            An :class:`~chimera.types.AgentResult` with aggregated cost,
            step count, and tool-call totals.  On early failure the result
            carries the error from the failing role.
        """
        if not self.roles:
            return AgentResult(
                output="No roles configured",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=False,
                error="No roles configured",
            )

        total_steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        role_outputs: list[tuple[str, str]] = []  # (role_name, output)

        for role in self.roles:
            agent = self._build_agent(role)

            # Build input: original task + prior role outputs
            if not role_outputs:
                agent_input = task
            else:
                prior_context = "\n\n".join(
                    f"=== {name.upper()} output ===\n{output}"
                    for name, output in role_outputs
                )
                agent_input = (
                    f"{task}\n\n"
                    f"--- Prior context from team members ---\n"
                    f"{prior_context}"
                )

            result = agent.run(agent_input, env)

            total_steps += result.steps
            total_tool_calls += result.tool_calls_total
            total_cost += result.cost

            if not result.success:
                return AgentResult(
                    output=result.output,
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error=f"Role '{role.name}' failed: {result.error}",
                )

            role_outputs.append((role.name, result.output))

        return AgentResult(
            output=role_outputs[-1][1] if role_outputs else "",
            steps=total_steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=True,
        )
