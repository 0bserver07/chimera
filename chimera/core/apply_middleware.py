"""Apply middleware: intercept file writes and stage them as proposed edits.

When active, write_file and edit_file tool calls are converted to
ProposedEdits instead of being applied immediately. The user can then
review, accept, or reject each change.

Inspired by Cursor's "Apply" pattern.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.core.middleware import LoopMiddleware
from chimera.core.proposed_edit import EditProposal

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.env.base import Environment
    from chimera.providers.base import Response
    from chimera.types import AgentResult


# Tools that modify files
_WRITE_TOOLS = {"write_file", "edit_file", "replace_in_file"}


class ApplyMiddleware(LoopMiddleware):
    """Intercept file-modifying tool calls and stage them as proposals.

    Args:
        auto_accept: If True, accept all edits automatically (non-interactive).
        env: Environment to read original file contents from.
    """

    def __init__(self, auto_accept: bool = False, env: Environment | None = None) -> None:
        self._auto_accept = auto_accept
        self._env = env
        self._proposal = EditProposal()

    @property
    def proposal(self) -> EditProposal:
        """The accumulated edit proposal."""
        return self._proposal

    def after_model(self, response: Response, context: Context) -> Response:
        """Intercept tool calls that write files."""
        if not response.has_tool_calls:
            return response

        from chimera.types import ToolCall

        intercepted: list[ToolCall] = []
        passthrough: list[ToolCall] = []

        for tc in response.tool_calls:
            if tc.name in _WRITE_TOOLS:
                self._stage_edit(tc)
                intercepted.append(tc)
            else:
                passthrough.append(tc)

        if not intercepted:
            return response

        # Replace intercepted tool calls with a synthetic "staged" message
        from chimera.providers.base import Response as Resp

        staged_msg = f"Staged {len(intercepted)} edit(s) for review."
        new_content = response.content
        if new_content:
            new_content += f"\n\n{staged_msg}"
        else:
            new_content = staged_msg

        return Resp(
            content=new_content,
            tool_calls=passthrough,
            usage=response.usage,
        )

    def after_agent(self, result: AgentResult, env: Environment | None) -> AgentResult:
        """After the agent finishes, apply accepted edits."""
        if self._auto_accept:
            self._proposal.accept_all()

        accepted = self._proposal.accepted
        if accepted and env:
            self._proposal.apply(env)

        return result

    def _stage_edit(self, tool_call: object) -> None:
        """Convert a tool call into a ProposedEdit."""
        tc_args = getattr(tool_call, "arguments", {})
        tc_name = getattr(tool_call, "name", "")
        path = tc_args.get("path", "")

        if not path:
            return

        # Try to read the original content
        original = ""
        if self._env:
            try:
                original = self._env.read_file(path)
            except (FileNotFoundError, Exception):
                pass

        if tc_name == "write_file":
            proposed = tc_args.get("content", "")
            self._proposal.add(path, original, proposed, f"write_file: {path}")
        elif tc_name == "edit_file":
            old_str = tc_args.get("old_string", "")
            new_str = tc_args.get("new_string", "")
            proposed = original.replace(old_str, new_str, 1) if original else new_str
            self._proposal.add(path, original, proposed, f"edit_file: {path}")
        elif tc_name == "replace_in_file":
            old_str = tc_args.get("old_string", "")
            new_str = tc_args.get("new_string", "")
            proposed = original.replace(old_str, new_str) if original else new_str
            self._proposal.add(path, original, proposed, f"replace_in_file: {path}")
