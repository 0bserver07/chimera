#!/usr/bin/env python3
"""MANUAL smoke test for a Modal managed inference Endpoint.

NOT run by CI or pytest (pytest collects tests/ only; nothing imports this
file). It makes ONE real, billable chat-completion request against a live
Modal endpoint — run it deliberately, after you have created an endpoint
and a proxy-token pair yourself:

    modal endpoint create --model zai-org/GLM-5.2-FP8
    modal workspace proxy-tokens create
    export MODAL_PROXY_TOKEN_ID='wk-...'
    export MODAL_PROXY_TOKEN_SECRET='ws-...'

    uv run python scripts/modal_endpoint_smoke.py \
        --model zai-org/GLM-5.2-FP8

    # or skip CLI discovery with an explicit URL (with or without /v1):
    uv run python scripts/modal_endpoint_smoke.py \
        --model zai-org/GLM-5.2-FP8 \
        --base-url https://myworkspace--glm-5-2-fp8.modal.run

Prints the reply, token usage (the cost-relevant numbers — Modal bills
GPU-seconds while the endpoint serves, so tokens/sec is your unit
economics), and wall-clock latency. Expect the FIRST call after idle to be
slow: scale-to-zero means a cold start spins the container up.
"""
from __future__ import annotations

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    """Run one chat completion against a Modal endpoint and report stats.

    Args:
        argv: CLI args (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 on success, 1 on any failure.
    """
    parser = argparse.ArgumentParser(
        description=(
            "One real chat completion against a Modal managed endpoint "
            "(manual smoke — never run by CI/tests)."
        ),
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Base model repo id the endpoint serves, e.g. zai-org/GLM-5.2-FP8",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Endpoint URL (with or without /v1). Omit to discover it via "
            "'modal endpoint list --json'."
        ),
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: chimera-modal-endpoint-ok",
        help="User prompt to send.",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=128, help="Output token cap.",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Modal environment for discovery (modal endpoint list --env ...).",
    )
    parser.add_argument(
        "--unauthenticated",
        action="store_true",
        help="Endpoint was created with --unauthenticated (skip proxy tokens).",
    )
    args = parser.parse_args(argv)

    from chimera.providers.modal_endpoint import ModalEndpointProvider
    from chimera.types import Message

    try:
        provider = ModalEndpointProvider(
            model=args.model,
            base_url=args.base_url,
            modal_environment=args.env,
            unauthenticated=args.unauthenticated,
        )
    except (ValueError, RuntimeError, ImportError) as err:
        print(f"setup failed: {err}", file=sys.stderr)
        return 1

    print(f"model:    {provider.model_name}")
    print(f"base_url: {provider._base_url}")
    print("note:     scale-to-zero — a cold endpoint takes a while on the "
          "first request.")
    print(f"prompt:   {args.prompt!r}")
    print("sending one chat completion ...")

    start = time.perf_counter()
    try:
        response = provider.complete(
            [Message.user(args.prompt)], max_tokens=args.max_tokens,
        )
    except Exception as err:  # noqa: BLE001 — smoke script: show, don't mask
        print(f"request failed: {err}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - start

    input_tokens = response.usage.get("input_tokens", 0)
    output_tokens = response.usage.get("output_tokens", 0)
    print("\n--- reply " + "-" * 50)
    print(response.content)
    print("--- stats " + "-" * 50)
    print(f"latency:        {elapsed:.2f}s (includes cold start if the "
          "endpoint was idle)")
    print(f"input tokens:   {input_tokens}")
    print(f"output tokens:  {output_tokens}")
    if elapsed > 0 and output_tokens:
        print(f"output tok/sec: {output_tokens / elapsed:.1f}")
    print(
        "cost basis:     Modal bills GPU-seconds while serving "
        "(scale-to-zero when idle) — check the run in your Modal dashboard."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
