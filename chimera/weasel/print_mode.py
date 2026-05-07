"""W14-5: weasel print-mode helpers.

Five features land here:

1. ``--thinking [LEVEL]`` — extended thinking with token budget. We map
   the human-readable level (``low`` / ``medium`` / ``high`` / ``max``)
   onto the existing :class:`chimera.providers.thinking.ThinkingLevel`
   enum; numeric arguments are accepted as raw token budgets.

2. ``--stream-json`` — emit one JSON object per stream event (newline-
   delimited) to stdout. Reuses the
   :class:`chimera.cli.output_format.StreamJsonHandler` schema so a
   downstream consumer can share parsers across mink and weasel.

3. **Piped stdin** — when ``stdin`` is not a TTY *and* no ``-p`` arg
   was given, read the prompt from stdin. Pairs naturally with the
   ``echo "do x" | chimera weasel`` pattern.

4. **Multi-message print** — ``-p`` is repeatable; each ``-p`` value
   becomes one sequential agent turn. The output format applies to
   each turn independently (e.g. one JSON envelope per ``-p``).

5. ``@file`` **expansion** — when a ``-p`` argument contains
   ``@/path/to/file`` (or ``@./relative/path``), the file body is
   substituted inline with a ``[<file>]\\n...\\n[/<file>]`` envelope
   so the model sees both the path label and the contents.

Trademark hygiene: the ``@file`` and ``--stream-json`` syntaxes are
generic conventions across many coding-agent CLIs; this module never
names any specific upstream agent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "ThinkingSpec",
    "parse_thinking_arg",
    "expand_at_files",
    "read_stdin_prompt",
    "normalize_prompts",
    "apply_thinking_to_provider",
    "run_streaming_json_turn",
]


# ---------------------------------------------------------------------------
# --thinking parsing
# ---------------------------------------------------------------------------


class ThinkingSpec:
    """Resolved thinking configuration: enabled flag plus token budget.

    Used by :func:`apply_thinking_to_provider` to set the
    ``enable_thinking`` / ``thinking_budget`` attributes on providers
    that support extended reasoning (Anthropic + Anthropic-compat).

    Attributes:
        enabled: Whether extended thinking should be turned on.
        budget: Token budget for the thinking pass. Zero when
            ``enabled`` is ``False``.
        level: Human-readable label preserved for diagnostics.
    """

    __slots__ = ("enabled", "budget", "level")

    def __init__(self, enabled: bool, budget: int, level: str) -> None:
        self.enabled = enabled
        self.budget = int(budget)
        self.level = level

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"ThinkingSpec(enabled={self.enabled}, budget={self.budget}, "
            f"level={self.level!r})"
        )


def parse_thinking_arg(value: str | None) -> ThinkingSpec:
    """Parse the ``--thinking`` CLI argument into a :class:`ThinkingSpec`.

    Accepted forms:

    * ``None`` — no flag passed → ``enabled=False, budget=0``.
    * ``""`` (empty string from ``--thinking`` with no value) →
      enabled at the ``medium`` level (sane default).
    * One of ``off`` / ``minimal`` / ``low`` / ``medium`` / ``high`` /
      ``max`` (case-insensitive) → maps onto
      :class:`chimera.providers.thinking.ThinkingLevel`.
    * A numeric string (``"4096"``) → enabled with the literal token
      budget.

    Args:
        value: The raw flag value.

    Returns:
        A :class:`ThinkingSpec` describing the requested configuration.

    Raises:
        ValueError: When *value* is not a recognised level / number.
    """
    if value is None:
        return ThinkingSpec(enabled=False, budget=0, level="off")

    raw = str(value).strip().lower()
    if raw == "":
        # Bare ``--thinking`` (no value) defaults to medium.
        from chimera.providers.thinking import (
            ThinkingLevel,
            budget_for_level,
        )
        return ThinkingSpec(
            enabled=True,
            budget=budget_for_level(ThinkingLevel.MEDIUM),
            level="medium",
        )

    if raw.isdigit():
        budget = int(raw)
        return ThinkingSpec(enabled=budget > 0, budget=budget, level="custom")

    from chimera.providers.thinking import ThinkingLevel, budget_for_level

    try:
        level = ThinkingLevel(raw)
    except ValueError as exc:
        valid = "/".join(lvl.value for lvl in ThinkingLevel)
        raise ValueError(
            f"--thinking: unknown level {value!r}; expected one of "
            f"{valid} or a token budget integer"
        ) from exc
    return ThinkingSpec(
        enabled=level != ThinkingLevel.OFF,
        budget=budget_for_level(level),
        level=raw,
    )


def apply_thinking_to_provider(provider: Any, spec: ThinkingSpec) -> None:
    """Mutate *provider* in-place to enable extended thinking per *spec*.

    Quietly no-ops on providers that don't expose the underscore-prefixed
    attribute pair (``_enable_thinking`` / ``_thinking_budget``). The
    in-place mutation matches how the provider's own ``__init__`` stores
    them, so the per-call resolution in
    :meth:`AnthropicProvider._prepare_request` will pick them up.

    Args:
        provider: A live :class:`chimera.providers.base.Provider`.
        spec: Parsed thinking spec from :func:`parse_thinking_arg`.
    """
    if not spec.enabled:
        return
    if hasattr(provider, "_enable_thinking"):
        try:
            provider._enable_thinking = True  # noqa: SLF001
        except Exception:  # noqa: BLE001 — defensive
            pass
    if hasattr(provider, "_thinking_budget"):
        try:
            provider._thinking_budget = int(spec.budget)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# @file expansion
# ---------------------------------------------------------------------------


# Match @-prefixed paths anchored at a word boundary. Accept absolute
# paths (``@/abs``), home-relative (``@~/x``), and explicit relative
# (``@./x`` / ``@../x``). We deliberately do *not* match a bare ``@foo``
# without a slash because that's almost always a literal ``@mention``
# rather than a path.
_AT_FILE_PATTERN = re.compile(r"(?<![A-Za-z0-9_])@(/[^\s]+|~[^\s]+|\.{1,2}/[^\s]+)")


def expand_at_files(
    prompt: str,
    *,
    base_dir: str | None = None,
    max_bytes: int = 65_536,
) -> str:
    """Replace ``@/path/to/file`` references in *prompt* with file content.

    Each match becomes::

        [/abs/path/to/file]
        <file body, truncated to max_bytes>
        [/file end]

    Missing files emit a stderr notice and are left as the original
    ``@path`` token so the agent still sees the user's intent.

    Args:
        prompt: The user's raw prompt text.
        base_dir: Working directory for resolving ``@./relative`` paths.
            Defaults to ``os.getcwd()``.
        max_bytes: Per-file truncation cap — keeps very large files
            from blowing the context budget.

    Returns:
        The expanded prompt.
    """
    if not prompt or "@" not in prompt:
        return prompt
    workdir = base_dir or os.getcwd()

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(1)
        path = os.path.expanduser(raw)
        if not os.path.isabs(path):
            path = os.path.join(workdir, path)
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            sys.stderr.write(
                f"[weasel] @file: not found: {raw!r}\n"
            )
            return match.group(0)
        try:
            body = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"[weasel] @file: read failed for {raw!r}: {exc}\n")
            return match.group(0)
        if len(body) > max_bytes:
            body = body[:max_bytes] + f"\n…[truncated to {max_bytes} bytes]"
        return f"[{path}]\n{body}\n[/file end]"

    return _AT_FILE_PATTERN.sub(_replace, prompt)


# ---------------------------------------------------------------------------
# stdin piping + prompt normalization
# ---------------------------------------------------------------------------


def read_stdin_prompt(stdin: Any = None) -> str | None:
    """Read piped stdin if present, return ``None`` when stdin is a TTY.

    Args:
        stdin: Override for tests; defaults to :data:`sys.stdin`.

    Returns:
        The stripped stdin body, or ``None`` when stdin is interactive
        / empty / unreadable.
    """
    src = stdin if stdin is not None else sys.stdin
    try:
        is_tty = bool(src.isatty())
    except Exception:  # noqa: BLE001
        is_tty = True
    if is_tty:
        return None
    try:
        body = src.read()
    except Exception:  # noqa: BLE001
        return None
    body = body.strip()
    return body or None


def normalize_prompts(
    args: argparse.Namespace,
    *,
    stdin: Any = None,
    base_dir: str | None = None,
) -> list[str]:
    """Resolve ``args.print_mode`` (str | list | None) plus stdin into a list.

    Resolution:

    1. If ``args.print_mode`` is a list (``-p`` repeated), use those
       values in order. Each one is expanded via
       :func:`expand_at_files`.
    2. Else if ``args.print_mode`` is a single string, use that.
    3. Else if stdin is piped (non-TTY), read it as the sole prompt.
    4. Else return an empty list (caller should surface a usage error).

    Args:
        args: Parsed weasel CLI namespace.
        stdin: Override for tests.
        base_dir: Working dir for ``@file`` resolution.

    Returns:
        Ordered list of prompt strings. May be empty.
    """
    raw = getattr(args, "print_mode", None)
    prompts: list[str] = []
    if isinstance(raw, list):
        prompts = [str(p) for p in raw if p]
    elif isinstance(raw, str):
        if raw:
            prompts = [raw]

    if not prompts:
        piped = read_stdin_prompt(stdin)
        if piped:
            prompts = [piped]

    return [expand_at_files(p, base_dir=base_dir) for p in prompts]


# ---------------------------------------------------------------------------
# stream-json runner
# ---------------------------------------------------------------------------


def run_streaming_json_turn(
    agent: Any,
    prompt: str,
    env: Any,
    *,
    out: Any = None,
    cancel: Any = None,
) -> int:
    """Stream one ferret-flavored JSON line per LoopEvent for *prompt*.

    Designed for one turn — multi-prompt callers loop and call this once
    per ``-p`` value so each turn boundary is unambiguous in the output
    stream.

    The implementation prefers ``agent.async_run_events`` when present
    (the loop event-stream API) so callers see one JSON line per
    LoopEvent. Falls back to ``async_run`` plus a single synthetic
    ``result`` line when the agent is a legacy implementation.

    Args:
        agent: A weasel-built :class:`Agent`.
        prompt: The prompt for this turn.
        env: The environment to drive the agent against.
        out: Writable text stream for the JSON output (default
            ``sys.stdout``).
        cancel: Optional :class:`CancellationToken` so ``Ctrl-C``
            cancels the run cleanly.

    Returns:
        Process exit code: 0 on agent success, 1 on agent failure,
        130 on cancel.
    """
    import asyncio

    sink = out if out is not None else sys.stdout

    def _emit(line: dict[str, Any]) -> None:
        sink.write(json.dumps(line, default=str, sort_keys=True) + "\n")
        try:
            sink.flush()
        except (AttributeError, ValueError):
            pass

    success_holder: dict[str, bool] = {"value": False}

    async def _drive() -> int:
        events_method = getattr(agent, "async_run_events", None)
        try:
            if events_method is not None:
                async for event in events_method(prompt, env=env):
                    line = {
                        "type": getattr(event.type, "value", str(event.type)),
                        "turn": getattr(event, "turn", 0),
                        "data": _safe_data(event.data),
                    }
                    _emit(line)
                    if line["type"] == "result":
                        reason = ""
                        try:
                            reason = getattr(event.data, "reason", "") or ""
                        except Exception:  # noqa: BLE001
                            reason = ""
                        success_holder["value"] = reason != "error"
            else:
                # Legacy fallback: one synthetic result line.
                result = await agent.async_run(prompt, env=env)
                _emit({
                    "type": "result",
                    "turn": getattr(result, "steps", 0),
                    "data": {
                        "output": getattr(result, "output", ""),
                        "cost": getattr(result, "cost", 0.0),
                        "success": getattr(result, "success", False),
                    },
                })
                success_holder["value"] = bool(getattr(result, "success", False))
        except KeyboardInterrupt:
            if cancel is not None:
                cancel.cancel()
            return 130
        except Exception as exc:  # noqa: BLE001
            _emit({"type": "error", "turn": 0, "data": {"error": str(exc)}})
            return 1
        return 0 if success_holder["value"] else 1

    return asyncio.run(_drive())


def _safe_data(data: Any) -> Any:
    """Coerce LoopEvent ``data`` into something JSON-serializable.

    Mirrors the helper in :mod:`chimera.mink.cli` so weasel's
    ``--stream-json`` lines are shape-compatible with mink's. The two
    use the same JSON envelope (``{"type", "turn", "data"}``) so a
    downstream consumer can share parsers across the CLI fleet.
    """
    if data is None:
        return None
    if isinstance(data, (str, int, float, bool)):
        return data
    if isinstance(data, dict):
        return {k: _safe_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_safe_data(v) for v in data]
    # Fallback — best-effort dataclass / object → dict.
    if hasattr(data, "__dict__"):
        return {
            k: _safe_data(v)
            for k, v in vars(data).items()
            if not k.startswith("_")
        }
    return str(data)


# ---------------------------------------------------------------------------
# Helpers used by cli._run_print_mode
# ---------------------------------------------------------------------------


def select_prompt_strategy(args: argparse.Namespace) -> str:
    """Return the string label for the active output strategy.

    One of ``"stream-json"``, ``"json"``, or ``"text"``. Used by the cli
    to short-circuit into the right path. Centralised here so future
    flag additions don't have to touch the cli body.
    """
    if bool(getattr(args, "stream_json", False)):
        return "stream-json"
    if bool(getattr(args, "json_output", False)):
        return "json"
    return "text"


def iter_print_results(
    prompts: Iterable[str],
    *,
    runner: Any,
) -> Iterable[Any]:
    """Yield the output of *runner* for each prompt in *prompts*.

    Pure helper kept here so :func:`chimera.weasel.cli._run_print_mode`
    can keep its body short.
    """
    for prompt in prompts:
        yield runner(prompt)
