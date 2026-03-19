"""Trajectory logging for recording and replaying agent runs.

A Trajectory captures the full trace of an agent run: task description,
each step (model input/output, tool calls, results, timing), final outcome,
cost, and metadata. Stored as JSON-lines for streaming writes.

Used for: training data collection (SWE-Agent), few-shot demonstrations,
debugging, and performance analysis.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TrajectoryStep:
    """A single step in an agent trajectory."""

    step: int
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    model_response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    cost: float = 0.0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class Trajectory:
    """Complete trace of an agent run.

    Args:
        task: The task description given to the agent.
        agent_name: Name of the agent that ran.
        model: Model identifier used.
        steps: Ordered list of steps.
        success: Whether the task succeeded.
        total_cost: Sum of all step costs.
        total_duration_ms: Wall-clock time in milliseconds.
        metadata: Arbitrary key-value pairs.
    """

    task: str
    agent_name: str = ""
    model: str = ""
    steps: list[TrajectoryStep] = field(default_factory=list)
    success: bool = False
    total_cost: float = 0.0
    total_duration_ms: float = 0.0
    final_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    def add_step(self, step: TrajectoryStep) -> None:
        """Append a step to the trajectory."""
        self.steps.append(step)

    def finalize(self, success: bool, output: str) -> None:
        """Mark the trajectory as complete."""
        self.success = success
        self.final_output = output
        self.total_cost = sum(s.cost for s in self.steps)
        self.total_duration_ms = (time.time() - self.started_at) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return asdict(self)

    def save(self, path: str | Path) -> None:
        """Save trajectory as a JSON file."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    def save_jsonl(self, path: str | Path) -> None:
        """Save trajectory as JSON-lines (one line per step, streaming-friendly)."""
        p = Path(path)
        with p.open("w") as f:
            # Header line
            header = {
                "type": "trajectory_start",
                "task": self.task,
                "agent_name": self.agent_name,
                "model": self.model,
                "started_at": self.started_at,
                "metadata": self.metadata,
            }
            f.write(json.dumps(header) + "\n")
            # Step lines
            for step in self.steps:
                record = {"type": "step", **asdict(step)}
                f.write(json.dumps(record) + "\n")
            # Footer line
            footer = {
                "type": "trajectory_end",
                "success": self.success,
                "final_output": self.final_output,
                "total_cost": self.total_cost,
                "total_duration_ms": self.total_duration_ms,
                "step_count": len(self.steps),
            }
            f.write(json.dumps(footer) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> Trajectory:
        """Load trajectory from a JSON file."""
        data = json.loads(Path(path).read_text())
        steps = [TrajectoryStep(**s) for s in data.pop("steps", [])]
        return cls(steps=steps, **data)

    @classmethod
    def load_jsonl(cls, path: str | Path) -> Trajectory:
        """Load trajectory from a JSON-lines file."""
        p = Path(path)
        lines = p.read_text().strip().split("\n")

        traj = cls(task="")
        for line in lines:
            record = json.loads(line)
            rtype = record.pop("type", "")
            if rtype == "trajectory_start":
                traj.task = record["task"]
                traj.agent_name = record.get("agent_name", "")
                traj.model = record.get("model", "")
                traj.started_at = record.get("started_at", 0)
                traj.metadata = record.get("metadata", {})
            elif rtype == "step":
                traj.steps.append(TrajectoryStep(**record))
            elif rtype == "trajectory_end":
                traj.success = record["success"]
                traj.final_output = record.get("final_output", "")
                traj.total_cost = record.get("total_cost", 0)
                traj.total_duration_ms = record.get("total_duration_ms", 0)
        return traj


def filter_successful(trajectories: list[Trajectory]) -> list[Trajectory]:
    """Return only successful trajectories."""
    return [t for t in trajectories if t.success]


def sort_by_cost(trajectories: list[Trajectory]) -> list[Trajectory]:
    """Return trajectories sorted by total cost (cheapest first)."""
    return sorted(trajectories, key=lambda t: t.total_cost)
