"""``chimera doctor`` — top-level setup-diagnostics command.

Prints structured findings about the user's environment so a new install
can quickly tell whether API keys, local model daemons, Docker, optional
extras, and on-disk state are all wired up correctly.

The probes are intentionally fast and stdlib-only:

* API keys: presence of ``$ANTHROPIC_API_KEY``, ``$OPENAI_API_KEY``,
  ``$OPENROUTER_API_KEY``, ``$XAI_API_KEY``, ``$MOONSHOT_API_KEY``.
* Local LLM daemons: Ollama (``:11434``), llama.cpp (``:8888``),
  vLLM (``:8000``), SGLang (``:30000``).
* Docker daemon: ``docker info`` subprocess (fast-fail).
* Optional extras: ``rich``, ``textual``, ``asyncssh``, ``modal``.
* CLI versions: ``chimera <cli> --version`` for the 7 codenames.
* Eventlog dir: ``~/.chimera/eventlog/`` exists + writable.
* Plugin index: ``$CHIMERA_PLUGIN_INDEX`` set OR default URL reachable.

Output formats: ``--format text`` (default, optionally colored) or
``--format json``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from chimera.config.paths import store_path, user_scope_dir

# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

OK = "ok"
WARN = "warn"
FAIL = "fail"

# WHY: short timeout so ``chimera doctor`` never blocks on a dead daemon.
_HTTP_TIMEOUT = 0.25


@dataclasses.dataclass
class Check:
    """One row in the diagnostics output."""

    name: str
    status: str  # one of OK, WARN, FAIL
    detail: str
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

_API_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "MOONSHOT_API_KEY",
)


def check_api_keys(env: dict[str, str] | None = None) -> list[Check]:
    """Report which provider API keys are present in the environment."""
    e = env if env is not None else os.environ
    out: list[Check] = []
    for key in _API_KEYS:
        val = e.get(key, "")
        if val:
            out.append(
                Check(
                    name=f"env.{key}",
                    status=OK,
                    detail=f"set ({len(val)} chars)",
                )
            )
        else:
            out.append(
                Check(
                    name=f"env.{key}",
                    status=WARN,
                    detail="not set",
                    hint=f"export {key}=... to enable that provider",
                )
            )
    return out


def _http_get_json(
    url: str,
    timeout: float = _HTTP_TIMEOUT,
    opener: Callable[[str, float], bytes] | None = None,
) -> Any:
    """GET ``url`` and parse JSON. ``opener`` is for tests to inject."""
    if opener is not None:
        raw = opener(url, timeout)
    else:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _probe_openai_compat(
    name: str,
    url: str,
    *,
    opener: Callable[[str, float], bytes] | None = None,
) -> Check:
    """Generic probe for an OpenAI-compatible ``/v1/models`` endpoint."""
    try:
        data = _http_get_json(url, opener=opener)
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return Check(
            name=name,
            status=WARN,
            detail=f"unreachable ({type(exc).__name__})",
            hint=f"start the daemon listening on {url}",
        )
    except json.JSONDecodeError:
        return Check(
            name=name,
            status=WARN,
            detail="responded with non-JSON",
            hint="check the daemon's HTTP API matches /v1/models",
        )
    models: list[str] = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        for entry in data["data"]:
            if isinstance(entry, dict):
                model_id = entry.get("id") or entry.get("name")
                if isinstance(model_id, str):
                    models.append(model_id)
    detail = (
        f"reachable; {len(models)} model(s): {', '.join(models[:5])}"
        if models
        else "reachable; no models loaded"
    )
    return Check(name=name, status=OK, detail=detail)


def check_ollama(
    *, opener: Callable[[str, float], bytes] | None = None
) -> Check:
    """Probe the local Ollama daemon at ``:11434``."""
    url = "http://localhost:11434/api/tags"
    try:
        data = _http_get_json(url, opener=opener)
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return Check(
            name="daemon.ollama",
            status=WARN,
            detail=f"unreachable ({type(exc).__name__})",
            hint="install + start ollama (https://ollama.com)",
        )
    except json.JSONDecodeError:
        return Check(
            name="daemon.ollama",
            status=WARN,
            detail="responded with non-JSON",
            hint="confirm Ollama API matches /api/tags",
        )
    models: list[str] = []
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        for entry in data["models"]:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("model")
                if isinstance(name, str):
                    models.append(name)
    detail = (
        f"reachable; {len(models)} model(s): {', '.join(models[:5])}"
        if models
        else "reachable; no models pulled"
    )
    return Check(name="daemon.ollama", status=OK, detail=detail)


def check_llamacpp(
    *, opener: Callable[[str, float], bytes] | None = None
) -> Check:
    """Probe a local llama.cpp server (default port 8888)."""
    return _probe_openai_compat(
        "daemon.llamacpp",
        "http://localhost:8888/v1/models",
        opener=opener,
    )


def check_vllm(
    *, opener: Callable[[str, float], bytes] | None = None
) -> Check:
    """Probe a local vLLM server (default port 8000)."""
    return _probe_openai_compat(
        "daemon.vllm",
        "http://localhost:8000/v1/models",
        opener=opener,
    )


def check_sglang(
    *, opener: Callable[[str, float], bytes] | None = None
) -> Check:
    """Probe a local SGLang server (default port 30000)."""
    return _probe_openai_compat(
        "daemon.sglang",
        "http://localhost:30000/v1/models",
        opener=opener,
    )


def check_docker(
    *,
    runner: Callable[[list[str]], "subprocess.CompletedProcess[str]"] | None = None,
) -> Check:
    """Probe the Docker daemon by invoking ``docker info``."""
    cmd = ["docker", "info"]

    def _default_runner(c: list[str]) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(  # noqa: S603
            c,
            capture_output=True,
            text=True,
            timeout=2.0,
        )

    run = runner or _default_runner
    try:
        result = run(cmd)
    except FileNotFoundError:
        return Check(
            name="daemon.docker",
            status=WARN,
            detail="docker CLI not installed",
            hint="install Docker Desktop or `brew install docker`",
        )
    except subprocess.TimeoutExpired:
        return Check(
            name="daemon.docker",
            status=WARN,
            detail="docker info timed out",
            hint="is Docker Desktop running?",
        )
    if result.returncode == 0:
        return Check(
            name="daemon.docker",
            status=OK,
            detail="docker info ok",
        )
    return Check(
        name="daemon.docker",
        status=WARN,
        detail=(result.stderr or result.stdout or "non-zero exit").strip().splitlines()[0]
        if (result.stderr or result.stdout)
        else "non-zero exit",
        hint="start Docker Desktop / dockerd",
    )


_OPTIONAL_EXTRAS: tuple[str, ...] = ("rich", "textual", "asyncssh", "modal")


def check_optional_extras(
    importer: Callable[[str], Any] | None = None,
) -> list[Check]:
    """Report which optional extras are importable."""
    import importlib

    imp = importer or importlib.import_module
    out: list[Check] = []
    for mod in _OPTIONAL_EXTRAS:
        try:
            imp(mod)
            out.append(
                Check(
                    name=f"extra.{mod}",
                    status=OK,
                    detail="installed",
                )
            )
        except Exception:  # noqa: BLE001
            out.append(
                Check(
                    name=f"extra.{mod}",
                    status=WARN,
                    detail="not installed",
                    hint=f"uv pip install {mod}",
                )
            )
    return out


_CLI_NAMES: tuple[str, ...] = (
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)


def check_cli_versions(
    *,
    runner: Callable[[list[str]], "subprocess.CompletedProcess[str]"] | None = None,
) -> list[Check]:
    """Run ``chimera <cli> --version`` for each codename."""

    def _default_runner(c: list[str]) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(  # noqa: S603
            c,
            capture_output=True,
            text=True,
            timeout=5.0,
        )

    run = runner or _default_runner
    out: list[Check] = []
    for cli in _CLI_NAMES:
        cmd = [sys.executable, "-m", "chimera", cli, "--version"]
        try:
            result = run(cmd)
        except FileNotFoundError:
            out.append(
                Check(
                    name=f"cli.{cli}",
                    status=FAIL,
                    detail="python interpreter not found",
                )
            )
            continue
        except subprocess.TimeoutExpired:
            out.append(
                Check(
                    name=f"cli.{cli}",
                    status=WARN,
                    detail="--version timed out",
                )
            )
            continue
        text = (result.stdout or result.stderr or "").strip().splitlines()
        first_line = text[0] if text else ""
        if result.returncode == 0:
            out.append(
                Check(
                    name=f"cli.{cli}",
                    status=OK,
                    detail=first_line or "version reported",
                )
            )
        else:
            out.append(
                Check(
                    name=f"cli.{cli}",
                    status=WARN,
                    detail=first_line or f"exit {result.returncode}",
                    hint="scaffold may not yet be built",
                )
            )
    return out


def check_eventlog_dir(home: Path | None = None) -> Check:
    """Verify ``~/.chimera/eventlog/`` exists and is writable."""
    target = user_scope_dir(home) / "eventlog" if home is not None else store_path("eventlog")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check(
            name="eventlog.dir",
            status=FAIL,
            detail=f"cannot create {target}: {exc}",
            hint="check filesystem permissions on $HOME",
        )
    if not os.access(target, os.W_OK):
        return Check(
            name="eventlog.dir",
            status=FAIL,
            detail=f"{target} is not writable",
            hint=f"chmod u+w {target}",
        )
    return Check(
        name="eventlog.dir",
        status=OK,
        detail=str(target),
    )


def check_plugin_index(
    *,
    env: dict[str, str] | None = None,
    opener: Callable[[str, float], bytes] | None = None,
) -> Check:
    """Verify the plugin marketplace index is configured + reachable."""
    e = env if env is not None else os.environ
    override = e.get("CHIMERA_PLUGIN_INDEX", "").strip()
    if override:
        return Check(
            name="plugin.index",
            status=OK,
            detail=f"$CHIMERA_PLUGIN_INDEX={override}",
        )
    # Lazy import so we don't depend on plugins module at file load time.
    try:
        from chimera.plugins.marketplace import DEFAULT_INDEX_URL
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="plugin.index",
            status=WARN,
            detail=f"could not import marketplace ({exc})",
            hint="set $CHIMERA_PLUGIN_INDEX to a JSON registry URL",
        )
    # No env override AND no built-in default: that's the documented
    # zero-config state, not an error. Surface a hint so users can wire
    # one up via env, --index, or `chimera config set plugin_index`.
    if DEFAULT_INDEX_URL is None:
        return Check(
            name="plugin.index",
            status=WARN,
            detail="no plugin index configured",
            hint=(
                "set $CHIMERA_PLUGIN_INDEX, pass --index, or run "
                "`chimera config set plugin_index <url>` "
                "(see docs/plugins-index.md)"
            ),
        )
    # File:// or local path? Just stat it.
    if DEFAULT_INDEX_URL.startswith("file://") or DEFAULT_INDEX_URL.startswith("/"):
        path = (
            DEFAULT_INDEX_URL[len("file://"):]
            if DEFAULT_INDEX_URL.startswith("file://")
            else DEFAULT_INDEX_URL
        )
        if Path(path).exists():
            return Check(
                name="plugin.index",
                status=OK,
                detail=f"local index {path}",
            )
        return Check(
            name="plugin.index",
            status=WARN,
            detail=f"local index missing: {path}",
            hint="set $CHIMERA_PLUGIN_INDEX=<url-or-path>",
        )
    # HTTP probe.
    try:
        if opener is not None:
            opener(DEFAULT_INDEX_URL, _HTTP_TIMEOUT)
        else:
            req = urllib.request.Request(
                DEFAULT_INDEX_URL,
                headers={"Accept": "application/json"},
                method="HEAD",
            )
            try:
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT):  # noqa: S310
                    pass
            except urllib.error.HTTPError as http_exc:
                # Some hosts reject HEAD; that still proves they're up.
                if 400 <= http_exc.code < 500:
                    pass
                else:
                    raise
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return Check(
            name="plugin.index",
            status=WARN,
            detail=f"default index unreachable ({type(exc).__name__})",
            hint=(
                "set $CHIMERA_PLUGIN_INDEX to a custom registry, or "
                "ensure outbound HTTPS is allowed"
            ),
        )
    return Check(
        name="plugin.index",
        status=OK,
        detail=f"default index reachable: {DEFAULT_INDEX_URL}",
    )


# ---------------------------------------------------------------------------
# Aggregator + rendering
# ---------------------------------------------------------------------------


def collect_checks() -> list[Check]:
    """Run every probe and return the flat list of :class:`Check`."""
    checks: list[Check] = []
    checks.extend(check_api_keys())
    checks.append(check_ollama())
    checks.append(check_llamacpp())
    checks.append(check_vllm())
    checks.append(check_sglang())
    checks.append(check_docker())
    checks.extend(check_optional_extras())
    checks.extend(check_cli_versions())
    checks.append(check_eventlog_dir())
    checks.append(check_plugin_index())
    return checks


# WHY: optional rich coloring. The mink extra ships rich; we degrade
# gracefully when it's missing.
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"


def _color_for(status: str, color: bool) -> tuple[str, str]:
    if not color:
        return "", ""
    if status == OK:
        return _ANSI_GREEN, _ANSI_RESET
    if status == WARN:
        return _ANSI_YELLOW, _ANSI_RESET
    if status == FAIL:
        return _ANSI_RED, _ANSI_RESET
    return "", ""


def _color_supported() -> bool:
    """Return True if stdout looks like a TTY and rich is importable."""
    if not sys.stdout.isatty():
        return False
    try:
        import importlib

        importlib.import_module("rich")
    except Exception:  # noqa: BLE001
        return False
    return True


def format_text(checks: list[Check], color: bool | None = None) -> str:
    """Render checks as an aligned text block."""
    use_color = _color_supported() if color is None else color
    name_w = max(len("CHECK"), max((len(c.name) for c in checks), default=5))
    status_w = max(len("STATUS"), 4)
    lines: list[str] = []
    lines.append("chimera doctor:")
    lines.append("")
    lines.append(f"  {'CHECK':<{name_w}}  {'STATUS':<{status_w}}  DETAIL")
    lines.append("  " + "-" * name_w + "  " + "-" * status_w + "  " + "-" * 6)
    for c in checks:
        prefix, suffix = _color_for(c.status, use_color)
        status_cell = f"{prefix}{c.status:<{status_w}}{suffix}"
        lines.append(f"  {c.name:<{name_w}}  {status_cell}  {c.detail}")
        if c.hint and c.status != OK:
            lines.append(f"  {' ' * name_w}  {' ' * status_w}  hint: {c.hint}")
    lines.append("")
    summary = {
        OK: sum(1 for c in checks if c.status == OK),
        WARN: sum(1 for c in checks if c.status == WARN),
        FAIL: sum(1 for c in checks if c.status == FAIL),
    }
    lines.append(
        f"  summary: {summary[OK]} ok, {summary[WARN]} warn, {summary[FAIL]} fail"
    )
    return "\n".join(lines)


def format_json(checks: list[Check]) -> str:
    """Render checks as a JSON document."""
    payload = {
        "checks": [c.to_dict() for c in checks],
        "summary": {
            "ok": sum(1 for c in checks if c.status == OK),
            "warn": sum(1 for c in checks if c.status == WARN),
            "fail": sum(1 for c in checks if c.status == FAIL),
        },
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera doctor`` flags."""
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in text output.",
    )


def run(args: argparse.Namespace) -> int:
    """Run all probes and print the report."""
    checks = collect_checks()
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(format_json(checks))
    else:
        color: bool | None = False if getattr(args, "no_color", False) else None
        print(format_text(checks, color=color))
    # Exit 0 if no FAILs (warns are informational).
    return 0 if not any(c.status == FAIL for c in checks) else 1


__all__ = [
    "Check",
    "OK",
    "WARN",
    "FAIL",
    "add_arguments",
    "check_api_keys",
    "check_cli_versions",
    "check_docker",
    "check_eventlog_dir",
    "check_llamacpp",
    "check_ollama",
    "check_optional_extras",
    "check_plugin_index",
    "check_sglang",
    "check_vllm",
    "collect_checks",
    "format_json",
    "format_text",
    "run",
]
