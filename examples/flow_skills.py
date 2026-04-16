#!/usr/bin/env python3
"""Flow Skills: guide an agent through a Mermaid decision tree.

Parses a Mermaid flowchart into steps, then the agent follows the
workflow — making choices at decision nodes.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/flow_skills.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


REVIEW_FLOW = """\
flowchart TD
    A([BEGIN]) --> B[Read the code]
    B --> C{Has tests?}
    C -->|yes| D[Run the tests]
    C -->|no| E[Write tests first]
    D --> F{Tests pass?}
    F -->|yes| G[Approve the code]
    F -->|no| H[Fix the failures]
    E --> D
    H --> D
    G --> I([END])
"""


def main():
    try:
        provider = chimera.create_provider(model=os.environ.get("ANTHROPIC_MODEL"))
    except ValueError as _e:
        import sys
        print(f"Setup error: {_e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL before running.", file=sys.stderr)
        sys.exit(1)

    # Parse the Mermaid flowchart
    flow = chimera.Flow.from_mermaid(REVIEW_FLOW)

    print("=== Flow Skills: Code Review Workflow ===\n")
    print(f"Nodes: {len(flow.nodes)}")
    print(f"Edges: {len(flow.edges)}")
    print()

    # Show the full prompt
    prompt = flow.to_prompt()
    print("Generated prompt:")
    print(prompt)
    print()

    # Walk through the flow with an agent
    agent = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=3),
    )

    current = flow.begin_id
    step = 0
    total_cost = 0.0

    while current != flow.end_id and step < 6:
        step += 1
        node = flow.nodes[current]
        nexts = flow.next_nodes(current)

        print(f"--- Step {step}: [{node.kind.upper()}] {node.label} ---")

        if len(nexts) == 1:
            # Linear node — just advance
            current = flow.advance(current)
            print(f"  → advancing to {flow.nodes[current].label}")
        elif len(nexts) > 1:
            # Decision node — ask the agent
            flow_prompt = flow.to_prompt(current_node_id=current)
            context = (
                "You're reviewing a Python calculator module with 6 tests. "
                "The tests exist and pass. "
            )
            result = agent.run(
                context + "\n\n" + flow_prompt,
                env=None,
            )
            total_cost += result.cost

            choice = chimera.parse_choice(result.output)
            if choice is None:
                # Try to infer from the output
                for edge, target in nexts:
                    if edge.label and edge.label.lower() in result.output.lower():
                        choice = edge.label
                        break

            if choice:
                print(f"  Agent chose: {choice}")
                current = flow.advance(current, choice)
                print(f"  → advancing to {flow.nodes[current].label}")
            else:
                print("  Agent didn't make a clear choice, defaulting to first option")
                current = nexts[0][1].id
        else:
            # End node
            break

        print()

    print("\n=== Flow complete ===")
    print(f"Ended at: {flow.nodes[current].label}")
    print(f"Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
