"""Chimera agent adapter for Terminal-Bench.

Subclasses terminal_bench.BaseAgent to run a Chimera agent inside
Terminal-Bench's sandboxed tmux environment.

Usage:
    tb run --agent chimera --model anthropic/glm-5 --dataset-name terminal-bench-core
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from terminal_bench.agents.base_agent import AgentResult, BaseAgent
    from terminal_bench.agents.failure_mode import FailureMode
    from terminal_bench.terminal import TmuxSession
    _HAS_TB = True
except ImportError:
    _HAS_TB = False
    BaseAgent = object  # type: ignore[misc,assignment]

from chimera.providers.factory import create_provider
from chimera.types import Message


class ChimeraAgent(BaseAgent):
    """Chimera-powered agent for Terminal-Bench.

    Uses Chimera's provider layer to interact with the LLM and
    executes commands via the TmuxSession provided by Terminal-Bench.
    """

    def __init__(self, **kwargs: Any) -> None:
        if not _HAS_TB:
            raise ImportError("terminal-bench not installed. pip install terminal-bench")
        super().__init__(**kwargs)
        self._model = kwargs.get("model_name", os.environ.get("ANTHROPIC_MODEL", "glm-5"))
        self._max_turns = int(kwargs.get("max_turns", 30))
        self._provider = create_provider(model=self._model)

    @staticmethod
    def name() -> str:
        return "chimera"

    @property
    def version(self) -> str:
        return self._version or "0.1.0"

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
    ) -> AgentResult:
        """Execute the Terminal-Bench task using a ReAct-style loop.

        Instead of using Chimera's built-in tools (which operate on files),
        we use a simple loop:
        1. Send instruction + terminal output to the LLM
        2. LLM returns a command to execute
        3. Execute the command in tmux
        4. Read the output
        5. Repeat until LLM says DONE or max turns reached
        """
        total_input = 0
        total_output = 0
        markers: list[tuple[float, str]] = []

        system = (
            "You are an expert terminal operator. You will be given a task to complete "
            "in a Linux terminal. Execute commands one at a time to accomplish the task.\n\n"
            "Rules:\n"
            "- Return ONLY the command to execute, nothing else\n"
            "- If you need to see output before deciding next step, execute the command\n"
            "- When the task is complete, return exactly: DONE\n"
            "- Do not use interactive commands (vim, nano, etc.) — use sed, echo, tee instead\n"
            "- Do not use commands that require user input\n"
        )

        messages: list[Message] = [
            Message.system(system),
            Message.user(f"Task: {instruction}\n\nThe terminal is ready. What command should I run first?"),
        ]

        terminal_output = ""

        for turn in range(self._max_turns):
            response = self._provider.complete(messages, max_tokens=1024)
            total_input += response.usage.get("input_tokens", 0)
            total_output += response.usage.get("output_tokens", 0)

            command = response.content.strip()

            # Strip markdown code blocks if present
            if command.startswith("```"):
                lines = command.split("\n")
                command = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
                command = command.strip()

            # Check if done
            if command.upper() == "DONE" or "DONE" in command.upper().split():
                markers.append((session.get_asciinema_timestamp(), f"DONE at turn {turn}"))
                break

            # Execute in tmux
            markers.append((session.get_asciinema_timestamp(), f"CMD: {command[:80]}"))
            session.send_keys(command)
            session.send_keys("", enter=True)  # press enter

            # Wait for output
            import time
            time.sleep(2)

            # Read terminal content
            terminal_output = session.get_visible_pane_content()

            # Add to conversation
            messages.append(Message.assistant(command))
            messages.append(Message.user(
                f"Terminal output:\n```\n{terminal_output[-2000:]}\n```\n\n"
                "What command should I run next? (or DONE if task is complete)"
            ))

        return AgentResult(
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            failure_mode=FailureMode.NONE,
            timestamped_markers=markers,
        )
