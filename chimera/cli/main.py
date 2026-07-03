"""Chimera CLI entry point.

Usage::

    chimera synthesize --spec "Build a calculator"
    chimera eval --benchmark swe-bench --dataset ./data.json --limit 10 --output results.json
    chimera bench --suite custom --tasks-dir ./tasks/ --output results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from chimera.synthesize import synthesize as synthesize_fn


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="chimera",
        description="Chimera: AI-powered code synthesis framework",
    )
    # WHY (audit H-1): expose `chimera --version` so users (and packaging
    # systems) can confirm what they have installed without importing.
    try:
        from chimera import __version__ as _chimera_version
    except Exception:  # noqa: BLE001
        _chimera_version = "unknown"
    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera {_chimera_version}",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- synthesize subcommand ----
    synth_parser = subparsers.add_parser(
        "synthesize",
        help="Synthesize code from a specification",
    )
    _add_synthesize_args(synth_parser)

    # ---- synth alias ----
    synth_alias = subparsers.add_parser(
        "synth",
        help="Alias for 'synthesize'",
    )
    _add_synthesize_args(synth_alias)

    # ---- eval subcommand ----
    eval_parser = subparsers.add_parser(
        "eval",
        help="Evaluate an agent against a benchmark",
    )
    eval_parser.add_argument(
        "--benchmark",
        required=True,
        help="Benchmark to evaluate against (e.g. swe-bench, human-eval)",
    )
    eval_parser.add_argument(
        "--dataset",
        default=None,
        help="Path to dataset file (JSON)",
    )
    eval_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of tasks to evaluate",
    )
    eval_parser.add_argument(
        "--output",
        default=None,
        help="Path to write results JSON",
    )
    eval_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    eval_parser.add_argument(
        "--agent",
        choices=["react", "code"],
        default="react",
        help=(
            "Agent under test: 'react' (core ReAct Agent, ~single-shot on simple "
            "tasks) or 'code' (the assembled `chimera code` CodingAgent)."
        ),
    )
    eval_parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip tasks already recorded in the <output>.progress.jsonl sidecar "
            "and reuse their results (resume an interrupted run). Requires --output."
        ),
    )

    # ---- bench subcommand ----
    bench_parser = subparsers.add_parser(
        "bench",
        help="Run a benchmark suite",
    )
    bench_parser.add_argument(
        "--suite",
        required=True,
        help="Benchmark suite to run (e.g. custom, full)",
    )
    bench_parser.add_argument(
        "--tasks-dir",
        default=None,
        help="Directory containing task definitions",
    )
    bench_parser.add_argument(
        "--output",
        default=None,
        help="Path to write results JSON",
    )
    bench_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )

    # ---- bench-compare subcommand ----
    from chimera.cli.bench_compare import add_bench_compare_parser
    add_bench_compare_parser(subparsers)

    # ---- bench-matrix subcommand ----
    from chimera.cli.bench_matrix import add_bench_matrix_parser
    add_bench_matrix_parser(subparsers)

    # ---- bench-fidelity subcommand ----
    from chimera.cli.bench_fidelity import add_bench_fidelity_parser
    add_bench_fidelity_parser(subparsers)

    # ---- code subcommand ----
    code_parser = subparsers.add_parser(
        "code",
        help="Interactive coding agent REPL",
    )
    code_parser.add_argument(
        "--model",
        default=None,
        help="Model to use (default: ANTHROPIC_MODEL env var, or claude-sonnet-4-20250514)",
    )
    code_parser.add_argument(
        "--workdir",
        default=".",
        help="Working directory (default: current directory)",
    )
    code_parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum agent steps per turn (default: 50)",
    )
    code_parser.add_argument(
        "--mode",
        choices=["interactive", "rpc", "json"],
        default="interactive",
        help="Output mode (default: interactive)",
    )
    code_parser.add_argument(
        "--models",
        default="",
        help=(
            "Comma-separated models. In the REPL: models to cycle through. "
            "With --tui: one multiplexer lane each, as model[:preset[:loop]] "
            "(e.g. glm-5.2:coding_agent:plan,glm-4.6:explore); a single model "
            "gets one full-featured lane editing the real tree."
        ),
    )
    code_parser.add_argument(
        "--preset",
        default=None,
        choices=["coding_agent", "claude_code", "codex", "minimal", "explore"],
        help=(
            "Agent preset for the CodingAgent stack. The bare REPL "
            "(no --preset, no --legacy-react) defaults to 'coding_agent'. "
            "Pass --preset to pick a different preset (codex / minimal / "
            "explore). 'claude_code' is a deprecated alias for "
            "'coding_agent' and will be removed in a future release."
        ),
    )
    code_parser.add_argument(
        "--max-turns",
        dest="max_turns",
        type=int,
        default=None,
        help=(
            "Max LLM turns before stopping. 0 (or negative) = unlimited "
            "(run until the task completes). Default: the preset's value (100)."
        ),
    )
    code_parser.add_argument(
        "--tui",
        action="store_true",
        default=False,
        help="Launch the full-screen Textual TUI instead of the line REPL (needs the 'tui' extra).",
    )
    code_parser.add_argument(
        "--isolation",
        choices=["auto", "worktree", "copy", "inplace"],
        default=None,
        help=(
            "Multiplexer (--tui with --models): how each lane's workspace is "
            "isolated. 'auto' = git worktree for a repo else copy; 'inplace' "
            "shares the real tree (unsafe with 2+ file-writing lanes). "
            "Default: inplace for a single lane, auto for 2+."
        ),
    )
    code_parser.add_argument(
        "--lane-cap",
        dest="lane_cap",
        type=int,
        default=None,
        help="Multiplexer only: max lanes running a turn at once (default: all lanes).",
    )
    code_parser.add_argument(
        "--export",
        default=None,
        help="Multiplexer only: also write the cohort comparison artifact to this .zip on exit.",
    )
    code_parser.add_argument(
        "--resume",
        default=None,
        metavar="COHORT_ID",
        help="Multiplexer (--tui): reopen a saved cohort by id and continue it (see --list-cohorts).",
    )
    code_parser.add_argument(
        "--list-cohorts",
        dest="list_cohorts",
        action="store_true",
        default=False,
        help="Multiplexer: list saved cohorts (id · task · lanes) and exit.",
    )
    code_parser.add_argument(
        "-p", "--print",
        dest="print_mode",
        default=None,
        help="Non-interactive: run a single task and print the result",
    )
    code_parser.add_argument(
        "--legacy-react",
        dest="legacy_react",
        action="store_true",
        default=False,
        help=(
            "Opt out of the new CodingAgent default and use the legacy "
            "ReAct + Session stack instead. Reserved for back-compat with "
            "users who depend on the rich slash-command REPL "
            "(/checkpoint, /tree, /branch, /switch, steering)."
        ),
    )

    # ---- mink subcommand ----
    # Purpose alias: 'tui' (TUI-first interactive coding agent).
    mink_parser = subparsers.add_parser(
        "mink",
        aliases=["tui"],
        help="Mink (alias: tui) — TUI-first interactive coding agent",
    )
    from chimera.mink import cli as _mink_cli
    _mink_cli.add_arguments(mink_parser)

    # ---- otter subcommand ----
    # Purpose alias: 'multi' (server-first, multi-client HTTP+SSE+ACP).
    otter_parser = subparsers.add_parser(
        "otter",
        aliases=["multi"],
        help="Otter (alias: multi) — server-first multi-client coding agent",
    )
    from chimera.otter import cli as _otter_cli
    _otter_cli.add_arguments(otter_parser)

    # ---- ferret subcommand ----
    # Purpose alias: 'sandbox' (sandbox-first execution, IDE-flagship).
    ferret_parser = subparsers.add_parser(
        "ferret",
        aliases=["sandbox"],
        help="Ferret (alias: sandbox) — sandbox-first IDE-flagship coding agent",
    )
    try:
        from chimera.ferret import cli as _ferret_cli  # type: ignore[attr-defined]
        _ferret_cli.add_arguments(ferret_parser)
    except (ImportError, AttributeError):
        ferret_parser.add_argument("--version", action="store_true")

    # ---- weasel subcommand ----
    # Purpose alias: 'mini' (minimal harness, four operating modes).
    weasel_parser = subparsers.add_parser(
        "weasel",
        aliases=["mini"],
        help="Weasel (alias: mini) — minimal harness, four operating modes",
    )
    try:
        from chimera.weasel import cli as _weasel_cli  # type: ignore[attr-defined]
        _weasel_cli.add_arguments(weasel_parser)
    except (ImportError, AttributeError):
        weasel_parser.add_argument("--version", action="store_true")

    # ---- shrew subcommand ----
    # Purpose alias: 'tiny' (tuned for small local models).
    shrew_parser = subparsers.add_parser(
        "shrew",
        aliases=["tiny"],
        help="Shrew (alias: tiny) — coding agent tuned for small local models",
    )
    try:
        from chimera.shrew import cli as _shrew_cli  # type: ignore[attr-defined]
        _shrew_cli.add_arguments(shrew_parser)
    except (ImportError, AttributeError):
        shrew_parser.add_argument("--version", action="store_true")

    # ---- stoat subcommand ----
    # Purpose alias: 'shell' (shell-mode toggle, Kimi-tuned defaults).
    stoat_parser = subparsers.add_parser(
        "stoat",
        aliases=["shell"],
        help="Stoat (alias: shell) — coding agent with a shell-mode toggle",
    )
    try:
        from chimera.stoat import cli as _stoat_cli  # type: ignore[attr-defined]
        _stoat_cli.add_arguments(stoat_parser)
    except (ImportError, AttributeError):
        stoat_parser.add_argument("--version", action="store_true")

    # ---- badger subcommand ----
    # Purpose alias: 'strict' (harness-rewrite posture, parity tracking).
    badger_parser = subparsers.add_parser(
        "badger",
        aliases=["strict"],
        help="Badger (alias: strict) — harness-rewrite posture, parity tracking",
    )
    try:
        from chimera.badger import cli as _badger_cli  # type: ignore[attr-defined]
        _badger_cli.add_arguments(badger_parser)
    except (ImportError, AttributeError):
        badger_parser.add_argument("--version", action="store_true")

    # ---- resume (top-level dispatcher) ----
    # WHY (B12, wave 11): each CLI already exposes ``--resume <id>`` via
    # wave-9 C1, but you need to know which CLI minted the session to
    # invoke it. ``chimera resume <id>`` auto-detects the originating
    # codename from the run-id prefix and forwards. ``chimera resume``
    # (no id) picks the newest run across all CLIs.
    resume_parser = subparsers.add_parser(
        "resume",
        help=(
            "Resume a prior run by id (auto-detects which CLI saved it). "
            "Omit the id to resume the most recent run across all CLIs."
        ),
    )
    from chimera.cli import resume_cmd as _resume_cmd
    _resume_cmd.add_arguments(resume_parser)

    # ---- agents (top-level discovery) ----
    # WHY: distinct from each CLI's own ``agents`` subcommand. This one
    # lists all 7 codenames + aliases + inspirations so users can pick
    # which CLI to use without grepping the README.
    agents_parser = subparsers.add_parser(
        "agents",
        help="List all 7 coding-agent CLIs with aliases + inspirations",
    )
    from chimera.cli import agents_discovery as _agents_discovery
    _agents_discovery.add_arguments(agents_parser)

    # ---- review subcommand ----
    review_parser = subparsers.add_parser(
        "review",
        help="Run AI code review on a diff",
    )
    review_parser.add_argument(
        "--diff",
        required=True,
        help="Path to diff file",
    )
    review_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    review_parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="Maximum review rounds (default: 3)",
    )

    # ---- ci-fix subcommand ----
    cifix_parser = subparsers.add_parser(
        "ci-fix",
        help="Diagnose and fix CI failures",
    )
    cifix_parser.add_argument(
        "--log",
        required=True,
        help="Path to CI log file",
    )
    cifix_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    cifix_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum fix attempts (default: 3)",
    )

    # ---- research subcommand ----
    research_parser = subparsers.add_parser(
        "research",
        help="Research a question using AI",
    )
    research_parser.add_argument(
        "--question",
        required=True,
        help="Research question",
    )
    research_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    research_parser.add_argument(
        "--workdir",
        default=".",
        help="Working directory (default: current directory)",
    )

    # ---- docs subcommand ----
    docs_parser = subparsers.add_parser(
        "docs",
        help="Generate API documentation from source code",
    )
    docs_parser.add_argument(
        "--source",
        required=True,
        help="Source directory to scan",
    )
    docs_parser.add_argument(
        "--output",
        default="docs/api",
        help="Output directory (default: docs/api)",
    )

    # ---- testgen subcommand ----
    testgen_parser = subparsers.add_parser(
        "testgen",
        help="Generate test case skeletons from source code",
    )
    testgen_parser.add_argument(
        "--source",
        required=True,
        help="Source directory to scan",
    )
    testgen_parser.add_argument(
        "--output",
        default="tests/generated",
        help="Output directory (default: tests/generated)",
    )

    # ---- migrate subcommand ----
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Apply migration rules to source files",
    )
    migrate_parser.add_argument(
        "--source",
        required=True,
        help="Source directory to migrate",
    )
    migrate_parser.add_argument(
        "--preset",
        required=True,
        help="Migration preset (e.g. python2-to-3, commonjs-to-esm)",
    )

    # ---- fs subcommand ----
    from chimera.cli import fs as _fs_cli
    _fs_cli.register(subparsers)

    # ---- config subcommand ----
    # Persistent CLI defaults in ~/.chimera/config.toml. Stdlib only.
    from chimera.cli import config_cmd as _config_cmd
    _config_cmd.register(subparsers)

    # ---- which subcommand ----
    # WHY: heuristic CLI recommender. Late-bind so a broken which_cmd.py
    # never breaks ``chimera --help``. Falls through silently when the
    # module is unimportable; the dispatcher below mirrors the same
    # guard so the missing-subcommand path produces the standard help.
    try:
        from chimera.cli.which_cmd import add_subparser as _add_which
        _add_which(subparsers)
    except (ImportError, AttributeError):
        pass

    # ---- tier-status subcommand ----
    # WHY: feature x tier readiness report; reads docs/tier-status.json
    # and renders text / json / Markdown. Late-bound for the same reason
    # as ``which`` -- a broken module must not break ``chimera --help``.
    try:
        from chimera.cli.tier_status import add_subparser as _add_tier
        _add_tier(subparsers)
    except (ImportError, AttributeError):
        pass

    # ---- team subcommand (experimental, gated by CHIMERA_EXPERIMENTAL_AGENT_TEAMS) ----
    from chimera.mink import team as _team_cli
    _team_cli.register(subparsers)

    # ---- completion subcommand ----
    # WHY: ship shell-completion scripts for bash/zsh/fish. The generator
    # walks this very parser at runtime, so newly-registered subcommands
    # show up automatically without a manual sync step.
    from chimera.cli import completion as _completion_cli
    completion_parser = subparsers.add_parser(
        "completion",
        help="Generate a shell-completion script (bash | zsh | fish).",
    )
    _completion_cli.add_arguments(completion_parser)

    # ---- plugins subcommand ----
    plugins_parser = subparsers.add_parser(
        "plugins",
        help="Manage plugins (search, install, uninstall, list)",
    )
    plugins_parser.add_argument(
        "action",
        choices=["search", "install", "uninstall", "list"],
        help="Plugin action",
    )
    plugins_parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Plugin name or search query (omit for `list`)",
    )
    plugins_parser.add_argument(
        "--cli",
        choices=[
            "mink", "otter", "ferret", "weasel", "shrew", "stoat", "badger",
        ],
        default="otter",
        help=(
            "Per-CLI plugin directory selector for install/uninstall/list "
            "(default: otter)"
        ),
    )
    plugins_parser.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="Install scope: user (~/.<cli>/plugin) or project (./.<cli>/plugin)",
    )
    plugins_parser.add_argument(
        "--index",
        default=None,
        help=(
            "Override registry index URL or local path "
            "(or set $CHIMERA_PLUGIN_INDEX)"
        ),
    )
    plugins_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing installation",
    )
    plugins_parser.add_argument(
        "--legacy-entrypoints",
        action="store_true",
        help=(
            "Use the legacy entry-point-based plugin discovery instead of the "
            "marketplace (search/list only)"
        ),
    )

    # ---- doctor subcommand ----
    # WHY: late-bind the import so a broken doctor.py never breaks the
    # whole CLI. Falls back to a stub parser that prints a friendly error.
    try:
        from chimera.cli.doctor import add_arguments as _doctor_add_arguments
        doctor_parser = subparsers.add_parser(
            "doctor",
            help="Diagnose your chimera setup (API keys, daemons, extras).",
        )
        _doctor_add_arguments(doctor_parser)
    except Exception:  # noqa: BLE001
        doctor_parser = subparsers.add_parser(
            "doctor",
            help="Diagnose your chimera setup (unavailable in this build).",
        )
        doctor_parser.add_argument("--format", default="text")

    # ---- auth subcommand ----
    # OAuth device-flow login + credential management. Stdlib-only.
    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage authentication credentials (login, logout, status).",
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_command", help="auth action")

    auth_login = auth_sub.add_parser(
        "login",
        help="Run OAuth device flow for a provider.",
    )
    auth_login.add_argument(
        "provider",
        choices=["openrouter", "xai", "anthropic", "openai"],
        help="Which provider to authenticate against.",
    )
    auth_login.add_argument(
        "--client-id",
        default=None,
        help="Override the OAuth client_id (required for placeholder providers).",
    )
    auth_login.add_argument(
        "--device-url",
        default=None,
        help="Override the device-authorization URL.",
    )
    auth_login.add_argument(
        "--token-url",
        default=None,
        help="Override the token URL.",
    )
    auth_login.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Override scopes (repeatable).",
    )
    auth_login.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Do not copy the user_code to the clipboard.",
    )

    auth_logout = auth_sub.add_parser(
        "logout",
        help="Remove a stored credential.",
    )
    auth_logout.add_argument("provider", help="Provider name to log out from.")

    auth_sub.add_parser(
        "status",
        help="Show currently configured providers.",
    )

    return parser


def _add_synthesize_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments for synthesize/synth subcommands."""
    parser.add_argument(
        "--spec",
        default=None,
        help="Specification text or path to spec file",
    )
    parser.add_argument(
        "--tests",
        default=None,
        help="Path to test directory",
    )
    parser.add_argument(
        "--output",
        default="./output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--provider",
        default="anthropic",
        help="Provider to use (default: anthropic)",
    )
    parser.add_argument(
        "--strategy",
        default="convergence",
        help="Strategy to use (default: convergence)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=50,
        help="Maximum iterations (default: 50)",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=5,
        help="Patience before stopping (default: 5)",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum cost budget",
    )


# Backward-compatible alias for Phase 6-8 tests
create_parser = build_parser


# Canonical benchmark name -> "module:ClassName". Multiple keys may point at the
# same adapter (hyphenated + squashed aliases). Every adapter under
# chimera/eval/benchmarks/ with a runnable Benchmark subclass is registered here
# so `chimera bench <name>` can reach it; see docs/reference/capability-matrix.md.
_BENCHMARKS: dict[str, str] = {
    "aider-polyglot": "chimera.eval.benchmarks.aider_polyglot:AiderPolyglot",
    "aiderpolyglot": "chimera.eval.benchmarks.aider_polyglot:AiderPolyglot",
    "aimo": "chimera.eval.benchmarks.aimo:AIMOBenchmark",
    "bigcodebench": "chimera.eval.benchmarks.bigcodebench:BigCodeBench",
    "cline-bench": "chimera.eval.benchmarks.cline_bench:ClineBench",
    "clinebench": "chimera.eval.benchmarks.cline_bench:ClineBench",
    "context-bench": "chimera.eval.benchmarks.context_bench:ContextBench",
    "contextbench": "chimera.eval.benchmarks.context_bench:ContextBench",
    "custom": "chimera.eval.benchmarks.custom:CustomBenchmark",
    "dpai-arena": "chimera.eval.benchmarks.dpai_arena:DPAIArena",
    "dpaiarena": "chimera.eval.benchmarks.dpai_arena:DPAIArena",
    "feature-bench": "chimera.eval.benchmarks.feature_bench:FeatureBench",
    "featurebench": "chimera.eval.benchmarks.feature_bench:FeatureBench",
    "harbor": "chimera.eval.benchmarks.harbor:HarborBenchmark",
    "human-eval": "chimera.eval.benchmarks.human_eval:HumanEval",
    "humaneval": "chimera.eval.benchmarks.human_eval:HumanEval",
    "humaneval-plus": "chimera.eval.benchmarks.humaneval_plus:HumanEvalPlus",
    "humanevalplus": "chimera.eval.benchmarks.humaneval_plus:HumanEvalPlus",
    "humaneval-x": "chimera.eval.benchmarks.humaneval_x:HumanEvalX",
    "humanevalx": "chimera.eval.benchmarks.humaneval_x:HumanEvalX",
    "lcb": "chimera.eval.benchmarks.livecodebench:LiveCodeBench",
    "livecodebench": "chimera.eval.benchmarks.livecodebench:LiveCodeBench",
    "math-500": "chimera.eval.benchmarks.math500:MATH500Benchmark",
    "math500": "chimera.eval.benchmarks.math500:MATH500Benchmark",
    "mbpp": "chimera.eval.benchmarks.mbpp:MBPP",
    "multi-swe-bench": "chimera.eval.benchmarks.multi_swe_bench:MultiSWEBench",
    "multiswebench": "chimera.eval.benchmarks.multi_swe_bench:MultiSWEBench",
    "nocha": "chimera.eval.benchmarks.nocha:NoCha",
    "programbench": "chimera.eval.benchmarks.programbench:ProgramBench",
    "swe-bench": "chimera.eval.benchmarks.swe_bench:SWEBench",
    "swebench": "chimera.eval.benchmarks.swe_bench:SWEBench",
    "swe-bench-verified": "chimera.eval.benchmarks.swe_bench_verified:SWEBenchVerified",
    "swebench-verified": "chimera.eval.benchmarks.swe_bench_verified:SWEBenchVerified",
    "swe-lancer": "chimera.eval.benchmarks.swe_lancer:SWELancer",
    "swelancer": "chimera.eval.benchmarks.swe_lancer:SWELancer",
    "swe-polybench": "chimera.eval.benchmarks.swe_polybench:SWEPolyBench",
    "swepolybench": "chimera.eval.benchmarks.swe_polybench:SWEPolyBench",
    "swt-bench": "chimera.eval.benchmarks.swt_bench:SWTBench",
    "swtbench": "chimera.eval.benchmarks.swt_bench:SWTBench",
    "tau-bench": "chimera.eval.benchmarks.tau_bench:TauBench",
    "taubench": "chimera.eval.benchmarks.tau_bench:TauBench",
    "webarena": "chimera.eval.benchmarks.webarena:WebArena",
}


def _load_benchmark(
    name: str,
    dataset: str | None = None,
    limit: int | None = None,
    tasks_dir: str | None = None,
) -> Any:
    """Instantiate a benchmark by name.

    Adapters name their dataset constructor argument differently
    (``dataset_path``, ``problems_path``, or ``dataset_dir``) and some take no
    ``limit``. Rather than assume one signature, inspect the constructor and
    only pass arguments it actually declares, so every registered adapter loads
    regardless of its individual signature.
    """
    if name not in _BENCHMARKS:
        raise ValueError(f"Unknown benchmark: {name}. Available: {', '.join(_BENCHMARKS)}")
    module_path, class_name = _BENCHMARKS[name].rsplit(":", 1)
    import importlib
    import inspect
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if name == "custom":
        return cls(tasks_dir=tasks_dir or dataset)
    params = inspect.signature(cls.__init__).parameters
    kwargs: dict[str, Any] = {}
    if dataset:
        # Adapters name their dataset argument differently; use the first the
        # class actually declares.
        for dataset_arg in ("dataset_path", "problems_path", "dataset_dir"):
            if dataset_arg in params:
                kwargs[dataset_arg] = dataset
                break
    if limit and "limit" in params:
        kwargs["limit"] = limit
    return cls(**kwargs)


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Convert EvalResult to a JSON-serializable dict."""
    import dataclasses
    return {
        "benchmark": result.benchmark,
        "total": result.total,
        "passed": result.passed,
        "pass_rate": result.pass_rate,
        "total_cost": result.total_cost,
        "results": [dataclasses.asdict(r) for r in result.results],
    }


def run_synthesize(args: argparse.Namespace) -> int:
    """Execute the synthesize command."""
    if not args.spec and not args.tests:
        print("Error: at least one of --spec or --tests is required.", file=sys.stderr)
        return 1

    spec_text = args.spec or "Make all tests pass."

    try:
        result = synthesize_fn(
            spec_text,
            tests=args.tests,
            model=args.model,
            workdir=args.output,
            max_iterations=args.max_iterations,
            patience=args.patience,
            max_cost=args.max_cost,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if result.converged:
        print(
            f"Synthesis converged in {result.iterations} iterations "
            f"(cost: ${result.total_cost:.4f})",
        )
        return 0
    else:
        print(
            f"Synthesis failed after {result.iterations} iterations "
            f"(best: {result.best_pass_rate:.0%}, cost: ${result.total_cost:.4f})",
            file=sys.stderr,
        )
        if result.failure_reason:
            print(f"Reason: {result.failure_reason}", file=sys.stderr)
        return 1


def run_eval(args: argparse.Namespace) -> int:
    """Execute the eval command."""
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark(args.benchmark, dataset=args.dataset, limit=args.limit)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    if getattr(args, "agent", "react") == "code":
        from chimera.eval.coding_agent_adapter import CodingAgentAdapter

        agent: Any = CodingAgentAdapter(provider=provider)
    else:
        agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))

    def _eval_env_factory() -> LocalEnvironment:
        import tempfile
        d = tempfile.mkdtemp(prefix="chimera-eval-")
        e = LocalEnvironment(workdir=d)
        e.setup()
        return e

    progress_path = f"{args.output}.progress.jsonl" if args.output else None
    harness = Harness(
        benchmark,
        agent,
        env_factory=_eval_env_factory,
        progress_path=progress_path,
        resume=getattr(args, "resume", False),
    )

    print(f"Running {benchmark.name()} ({len(benchmark.tasks())} tasks) with {args.model}...", file=sys.stderr)
    result = harness.run()

    print(f"\n{result.benchmark}: {result.passed}/{result.total} passed ({result.pass_rate:.1%})", file=sys.stderr)
    print(f"Total cost: ${result.total_cost:.4f}", file=sys.stderr)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)

    return 0 if result.passed == result.total else 1


def run_bench(args: argparse.Namespace) -> int:
    """Execute the bench command."""
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark("custom", tasks_dir=args.tasks_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
    harness = Harness(benchmark, agent)

    print(f"Running {benchmark.name()} ({len(benchmark.tasks())} tasks) with {args.model}...", file=sys.stderr)
    result = harness.run()

    print(f"\n{result.benchmark}: {result.passed}/{result.total} passed ({result.pass_rate:.1%})", file=sys.stderr)
    print(f"Total cost: ${result.total_cost:.4f}", file=sys.stderr)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)

    return 0 if result.passed == result.total else 1


def run_review(args: argparse.Namespace) -> int:
    """Execute the review command."""
    from chimera.core.agent import Agent
    from chimera.core.prompt import Prompt
    from chimera.env.local import LocalEnvironment
    from chimera.providers.factory import create_provider
    from chimera.review.orchestrator import ReviewOrchestrator

    try:
        with open(args.diff) as f:
            diff = f.read()
    except FileNotFoundError:
        print(f"Error: diff file not found: {args.diff}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    reviewer = Agent(provider=provider, prompt=Prompt("You are a code reviewer. Review diffs for bugs, style issues, and improvements."))
    author = Agent(provider=provider)
    env = LocalEnvironment(workdir=".")

    orchestrator = ReviewOrchestrator(max_rounds=args.max_rounds)
    approved = orchestrator.run(diff, reviewer, author, env)

    if approved:
        print("Review: APPROVED")
        return 0
    else:
        print(f"Review: NOT APPROVED after {orchestrator.current_round} rounds")
        return 1


def run_ci_fix(args: argparse.Namespace) -> int:
    """Execute the ci-fix command."""
    from chimera.ci.fix_workflow import CIFixWorkflow
    from chimera.core.agent import Agent
    from chimera.env.local import LocalEnvironment
    from chimera.providers.factory import create_provider

    try:
        with open(args.log) as f:
            log = f.read()
    except FileNotFoundError:
        print(f"Error: log file not found: {args.log}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider)
    env = LocalEnvironment(workdir=".")

    workflow = CIFixWorkflow(max_attempts=args.max_attempts)
    success = workflow.run(log, agent, env)

    if success:
        print("CI fix: SUCCESS")
        return 0
    else:
        print(f"CI fix: FAILED after {len(workflow.attempts)} attempts")
        return 1


def run_research(args: argparse.Namespace) -> int:
    """Execute the research command."""
    from chimera.core.agent import Agent
    from chimera.env.local import LocalEnvironment
    from chimera.providers.factory import create_provider
    from chimera.research.researcher import Researcher

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider)
    env = LocalEnvironment(workdir=args.workdir)

    researcher = Researcher()
    result = researcher.run(args.question, agent, env)
    print(result)
    return 0


def run_docs(args: argparse.Namespace) -> int:
    """Execute the docs command."""
    from chimera.docs.generator import DocGenerator

    generator = DocGenerator(root=args.source, output_dir=args.output)
    sections = generator.scan()
    written = generator.write(sections)

    print(f"Generated {len(written)} documentation files:")
    for path in written:
        print(f"  {path}")
    return 0


def run_testgen(args: argparse.Namespace) -> int:
    """Execute the testgen command."""
    from pathlib import Path

    from chimera.testgen.generator import TestGenerator

    source_dir = Path(args.source)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = TestGenerator()
    total_cases = 0

    for filepath in sorted(source_dir.rglob("*.py")):
        if any(part.startswith(".") or part in ("__pycache__", "venv")
               for part in filepath.parts):
            continue
        cases = generator.analyze(str(filepath))
        if cases:
            total_cases += len(cases)
            test_filename = f"test_{filepath.stem}.py"
            out_path = output_dir / test_filename
            lines = [f"# Auto-generated tests for {filepath}\n"]
            for case in cases:
                lines.append(case.test_code)
                lines.append("")
            out_path.write_text("\n".join(lines))

    print(f"Generated {total_cases} test cases from {args.source}")
    return 0


def run_migrate(args: argparse.Namespace) -> int:
    """Execute the migrate command."""
    from pathlib import Path

    from chimera.migration.planner import MigrationPlanner

    try:
        planner = MigrationPlanner.from_preset(args.preset)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    source_dir = Path(args.source)
    files: dict[str, str] = {}
    for filepath in sorted(source_dir.rglob("*")):
        if filepath.is_file():
            try:
                files[str(filepath.relative_to(source_dir))] = filepath.read_text()
            except Exception:
                continue

    result = planner.apply(files)

    changed = 0
    for rel_path, content in result.items():
        if content != files.get(rel_path, ""):
            out_path = source_dir / rel_path
            out_path.write_text(content)
            changed += 1
            print(f"  migrated: {rel_path}")

    print(f"Migration '{args.preset}': {changed} files changed")
    return 0


def run_plugins(args: argparse.Namespace) -> int:
    """Execute the ``chimera plugins`` command.

    Two backends:

    - **Marketplace** (default): a JSON registry index at
      ``$CHIMERA_PLUGIN_INDEX`` (or :data:`DEFAULT_INDEX_URL`)
      describes downloadable plugins; installs land in
      ``~/.<cli>/plugin/<name>/`` (user scope) or
      ``./.<cli>/plugin/<name>/`` (project scope).
    - **Legacy entry points** (``--legacy-entrypoints``): inspects
      Python packages registered via the ``chimera.plugins`` entry
      point group. Useful for environments where pip-installed plugins
      coexist with marketplace ones.
    """
    from chimera.plugins.marketplace import (
        NO_INDEX_HELP,
        MarketplaceClient,
        MarketplaceError,
        fetch_index,
        list_installed,
        resolve_index_url,
        uninstall_plugin,
    )

    cli_name: str = args.cli
    scope: str = args.scope
    index_override: str | None = args.index

    def _require_index() -> int | None:
        """Print friendly help to stderr if no index resolves. Return rc."""
        if resolve_index_url(index_override) is None:
            print(NO_INDEX_HELP, file=sys.stderr)
            return 2
        return None

    # ---- legacy entry-point path ----
    if getattr(args, "legacy_entrypoints", False):
        from chimera.plugins.manager import PluginManager

        manager = PluginManager()
        try:
            discovered = manager.discover()
        except Exception as exc:
            print(f"Error discovering plugins: {exc}", file=sys.stderr)
            return 1
        if args.action in ("search", "list"):
            query = (args.query or "").lower().strip()
            matches = (
                discovered
                if not query
                else [n for n in discovered if query in n.lower()]
            )
            if not matches:
                print("No matching entry-point plugins installed.")
                return 0
            for name in matches:
                print(f"  {name}")
            return 0
        print(
            "--legacy-entrypoints only supports `search` and `list`. "
            "Use pip/uv to install or remove entry-point plugins.",
            file=sys.stderr,
        )
        return 1

    # ---- marketplace path ----
    if args.action == "list":
        installed = list_installed(cli_name, scope=scope)
        if not installed:
            print(
                f"No plugins installed under {cli_name} ({scope} scope)."
            )
            return 0
        print(f"Plugins installed for {cli_name} ({scope} scope):")
        for name in installed:
            print(f"  {name}")
        return 0

    if args.action == "search":
        rc = _require_index()
        if rc is not None:
            return rc
        try:
            registry = fetch_index(index_override)
        except MarketplaceError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        client = MarketplaceClient(registry)
        results = client.search(args.query or "")
        if not results:
            print(f"No plugins match {args.query!r}.")
            return 0
        for info in results:
            tags = f" [{', '.join(info.tags)}]" if info.tags else ""
            print(f"  {info.name} {info.version}{tags} — {info.description}")
        return 0

    if args.action == "install":
        if not args.query:
            print("Error: install requires a plugin name", file=sys.stderr)
            return 1
        rc = _require_index()
        if rc is not None:
            return rc
        try:
            registry = fetch_index(index_override)
        except MarketplaceError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        client = MarketplaceClient(registry)
        try:
            dest = client.install(
                args.query,
                cli_name,
                scope=scope,
                overwrite=args.overwrite,
            )
        except MarketplaceError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Installed {args.query} -> {dest}")
        return 0

    if args.action == "uninstall":
        if not args.query:
            print("Error: uninstall requires a plugin name", file=sys.stderr)
            return 1
        try:
            removed = uninstall_plugin(args.query, cli_name, scope=scope)
        except MarketplaceError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not removed:
            print(
                f"Plugin {args.query!r} is not installed under "
                f"{cli_name} ({scope} scope).",
                file=sys.stderr,
            )
            return 1
        print(f"Uninstalled {args.query} from {cli_name} ({scope} scope).")
        return 0

    return 1


def run_auth(args: argparse.Namespace) -> int:
    """Execute the auth command (login / logout / status)."""
    from chimera.auth.oauth_device import (
        PROVIDER_PRESETS,
        SCAFFOLD_PROVIDERS,
        DeviceFlowError,
        login as oauth_login,
        scaffold_message,
    )
    from chimera.auth.store import CredentialStore

    sub = getattr(args, "auth_command", None)
    store = CredentialStore()

    if sub is None or sub == "status":
        providers = store.list_providers()
        if not providers:
            print("No stored credentials. Run 'chimera auth login <provider>'.")
            return 0
        print("Stored credentials:")
        for name in providers:
            cred = store.get(name)
            if cred is None:
                continue
            preview = (cred.token[:6] + "..") if cred.token else "<empty>"
            expiry = "no expiry"
            if cred.expires_at is not None:
                expiry = "expired" if cred.is_expired else "valid"
            print(f"  {name}: {preview} ({expiry})")
        return 0

    if sub == "logout":
        existing = store.get(args.provider)
        if existing is None:
            print(f"No stored credential for '{args.provider}'.")
            return 0
        store.delete(args.provider)
        print(f"Removed credential for '{args.provider}'.")
        return 0

    if sub == "login":
        provider = args.provider
        if provider not in PROVIDER_PRESETS:
            print(
                f"Unknown provider '{provider}'. "
                f"Known: {sorted(PROVIDER_PRESETS)}",
                file=sys.stderr,
            )
            return 2
        # Scaffold-only providers (no public device-flow client). Bail with a
        # friendly hint pointing at the API-key env var unless the user has
        # supplied their own client_id + endpoints to drive a private client.
        if (
            provider in SCAFFOLD_PROVIDERS
            and not (args.client_id and args.device_url and args.token_url)
        ):
            print(scaffold_message(provider), file=sys.stderr)
            return 2
        try:
            cred = oauth_login(
                provider,
                client_id=args.client_id,
                device_url=args.device_url,
                token_url=args.token_url,
                scopes=args.scope,
                store=store,
                clipboard=not args.no_clipboard,
            )
        except DeviceFlowError as exc:
            print(f"chimera auth: {exc}", file=sys.stderr)
            return 1
        except TimeoutError as exc:
            print(f"chimera auth: {exc}", file=sys.stderr)
            return 1
        print(f"Authenticated as '{cred.provider}'. Token stored.")
        return 0

    print(f"Unknown auth command: {sub}", file=sys.stderr)
    return 2


def _emit_setup_hook(command: str) -> None:
    """Fire :data:`HookEvent.SETUP` once at CLI startup.

    Best-effort: any wiring or hook error is swallowed so the CLI never
    refuses to run because of a hook misconfiguration.
    """
    try:
        from chimera.hooks.emitter import get_global_emitter
        from chimera.hooks.events import HookEvent
        emitter = get_global_emitter()
        if emitter.active:
            emitter.emit_sync(HookEvent.SETUP, tool_name=command)
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # Fire SETUP hook once per CLI invocation, after argv is parsed and the
    # subcommand is known. No-op when no global emitter has been registered.
    _emit_setup_hook(args.command)

    if args.command in ("synthesize", "synth"):
        return run_synthesize(args)
    elif args.command == "eval":
        return run_eval(args)
    elif args.command == "bench":
        return run_bench(args)
    elif args.command == "bench-compare":
        from chimera.cli.bench_compare import run_bench_compare
        return run_bench_compare(args)
    elif args.command == "bench-matrix":
        from chimera.cli.bench_matrix import run_bench_matrix
        return run_bench_matrix(args)
    elif args.command == "bench-fidelity":
        from chimera.cli.bench_fidelity import run_bench_fidelity
        return run_bench_fidelity(args)
    elif args.command == "code":
        from chimera.cli.code import run_code
        return run_code(args)
    elif args.command in ("mink", "tui"):
        from chimera.mink import cli as _mink_cli
        return _mink_cli.run(args)
    elif args.command in ("otter", "multi"):
        from chimera.otter import cli as _otter_cli
        return _otter_cli.run(args)
    elif args.command in ("ferret", "sandbox"):
        try:
            from chimera.ferret import cli as _ferret_cli  # type: ignore[attr-defined]
            return _ferret_cli.run(args)
        except (ImportError, AttributeError) as exc:
            print(f"chimera ferret: scaffold not yet built ({exc})", file=sys.stderr)
            return 2
    elif args.command in ("weasel", "mini"):
        try:
            from chimera.weasel import cli as _weasel_cli  # type: ignore[attr-defined]
            return _weasel_cli.run(args)
        except (ImportError, AttributeError) as exc:
            print(f"chimera weasel: scaffold not yet built ({exc})", file=sys.stderr)
            return 2
    elif args.command in ("shrew", "tiny"):
        try:
            from chimera.shrew import cli as _shrew_cli  # type: ignore[attr-defined]
            return _shrew_cli.run(args)
        except (ImportError, AttributeError) as exc:
            print(f"chimera shrew: scaffold not yet built ({exc})", file=sys.stderr)
            return 2
    elif args.command in ("stoat", "shell"):
        try:
            from chimera.stoat import cli as _stoat_cli  # type: ignore[attr-defined]
            return _stoat_cli.run(args)
        except (ImportError, AttributeError) as exc:
            print(f"chimera stoat: scaffold not yet built ({exc})", file=sys.stderr)
            return 2
    elif args.command == "agents":
        from chimera.cli import agents_discovery as _agents_discovery
        return _agents_discovery.run(args)
    elif args.command == "resume":
        from chimera.cli import resume_cmd as _resume_cmd
        return _resume_cmd.run(args)
    elif args.command in ("badger", "strict"):
        try:
            from chimera.badger import cli as _badger_cli  # type: ignore[attr-defined]
            return _badger_cli.run(args)
        except (ImportError, AttributeError) as exc:
            print(f"chimera badger: scaffold not yet built ({exc})", file=sys.stderr)
            return 2
    elif args.command == "review":
        return run_review(args)
    elif args.command == "ci-fix":
        return run_ci_fix(args)
    elif args.command == "research":
        return run_research(args)
    elif args.command == "docs":
        return run_docs(args)
    elif args.command == "testgen":
        return run_testgen(args)
    elif args.command == "migrate":
        return run_migrate(args)
    elif args.command == "completion":
        from chimera.cli import completion as _completion_cli
        return _completion_cli.run(args)
    elif args.command == "plugins":
        return run_plugins(args)
    elif args.command == "doctor":
        try:
            from chimera.cli.doctor import run as _doctor_run
        except Exception as exc:  # noqa: BLE001
            print(f"chimera doctor: unavailable ({exc})", file=sys.stderr)
            return 2
        return _doctor_run(args)
    elif args.command == "auth":
        return run_auth(args)
    elif args.command == "fs":
        rc: int = args.func(args)
        return rc
    elif args.command == "config":
        from chimera.cli import config_cmd as _config_cmd
        return _config_cmd.run(args)
    elif args.command == "which":
        try:
            from chimera.cli.which_cmd import run as _which_run
        except (ImportError, AttributeError) as exc:
            print(f"chimera which: unavailable ({exc})", file=sys.stderr)
            return 2
        return _which_run(args)
    elif args.command == "tier-status":
        try:
            from chimera.cli.tier_status import run as _tier_run
        except (ImportError, AttributeError) as exc:
            print(f"chimera tier-status: unavailable ({exc})", file=sys.stderr)
            return 2
        return _tier_run(args)
    elif args.command == "team":
        rc2: int = args.func(args)
        return rc2
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
