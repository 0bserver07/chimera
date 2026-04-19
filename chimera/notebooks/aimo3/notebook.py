#!/usr/bin/env python3
"""AIMO3 Kaggle Submission Template.

This script is the template for the Kaggle notebook submission.
It demonstrates the full pipeline: vLLM setup -> Chimera agent -> solve -> submit.

Local usage:
    1. Start vLLM: python -m vllm.entrypoints.openai.api_server \
         --model Qwen/Qwen3-235B-AWQ --port 8000
    2. Run: python chimera/notebooks/aimo3/notebook.py \
         --problems path/to/problems.json --output submission.csv

Kaggle usage:
    Convert to notebook cells and adjust paths for Kaggle environment.
"""
from __future__ import annotations

import argparse
import csv

import chimera
from chimera.eval.benchmarks.aimo import AIMOBenchmark, extract_answer
from chimera.tools.verify import VerifyTool
from chimera.training.spec import Spec
from chimera.training.strategies.majority_voting import MajorityVoting


def main(
    problems_path: str,
    output_path: str = "submission.csv",
    model: str = "Qwen/Qwen3-235B-AWQ",
    base_url: str = "http://localhost:8000",
    n_samples: int = 8,
) -> None:
    # --- Provider ---
    provider = chimera.create_provider(
        provider_type="compatible",
        model=model,
        base_url=base_url,
    )

    # --- Agent ---
    agent = chimera.Agent(
        provider=provider,
        tools=[chimera.tools.bash, chimera.tools.read_file, chimera.tools.write_file, VerifyTool()],  # type: ignore[attr-defined]  # notebook demo; attrs are dynamically registered
        loop=chimera.ReAct(max_steps=30),
    )

    # --- Strategy ---
    strategy = MajorityVoting(n_samples=n_samples, temperature=0.7, min_agreement=2)

    # --- Solve ---
    benchmark = AIMOBenchmark(problems_path=problems_path)
    tasks = benchmark.tasks()

    answers: dict[str, int] = {}
    total_cost = 0.0
    for task in tasks:
        spec = Spec(prompt=task["prompt"], tests=[])  # type: ignore[call-arg]  # notebook demo; Spec API may drift
        result = strategy.run(agent, spec, chimera.LocalEnvironment())  # type: ignore[call-arg]  # notebook demo; LocalEnvironment signature may drift
        total_cost += result.total_cost
        answer = extract_answer(result.history[-1].agent_output) if result.history else None
        answers[task["id"]] = answer if answer is not None else 0

    # --- Write submission ---
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "answer"])
        for task_id, answer in answers.items():
            writer.writerow([task_id, answer])

    solved = sum(1 for a in answers.values() if a != 0)
    print(f"Solved: {solved}/{len(tasks)}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Submission written to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIMO3 solver")
    parser.add_argument("--problems", required=True, help="Path to problems JSON")
    parser.add_argument("--output", default="submission.csv", help="Output CSV path")
    parser.add_argument("--model", default="Qwen/Qwen3-235B-AWQ")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--samples", type=int, default=8)
    args = parser.parse_args()
    main(args.problems, args.output, args.model, args.base_url, args.samples)
