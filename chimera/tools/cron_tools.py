"""Cron-style scheduled task tools.

Provides three tools for managing scheduled jobs:

* :class:`CronCreateTool` — register a job (and optionally a launchd plist
  on macOS).
* :class:`CronListTool` — enumerate registered jobs.
* :class:`CronDeleteTool` — remove a job by name (and unregister if needed).

Jobs are persisted as JSON in ``$CHIMERA_CRON_DIR`` (default
``~/.chimera/cron``) to keep the user's real crontab untouched. An external
scheduler can pick the file up if ``--register`` is not used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


def _jobs_dir() -> Path:
    """Return the directory where job entries live."""
    override = os.environ.get("CHIMERA_CRON_DIR")
    if override:
        return Path(override)
    return Path.home() / ".chimera" / "cron"


def _jobs_file() -> Path:
    """Return the JSON file backing the job store."""
    return _jobs_dir() / "jobs.json"


def _load_jobs() -> list[dict[str, Any]]:
    """Load the job list, returning ``[]`` if no file exists."""
    f = _jobs_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text())
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        return []


def _save_jobs(jobs: list[dict[str, Any]]) -> None:
    """Persist the job list, creating parent dirs as needed."""
    f = _jobs_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(jobs, indent=2))


def _plist_label(name: str) -> str:
    """Return the launchd label for a named job."""
    safe = "".join(c if c.isalnum() else "_" for c in name)
    return f"com.chimera.cron.{safe}"


def _plist_path(name: str) -> Path:
    """Return the plist file path for a named job."""
    return _jobs_dir() / f"{_plist_label(name)}.plist"


def _register_launchd(job: dict[str, Any]) -> str | None:
    """Write a launchd plist for ``job`` and load it.

    Returns:
        ``None`` on success, an error string otherwise. No-op (returning
        an error) on non-macOS platforms.
    """
    if sys.platform != "darwin":
        return "launchctl registration is only supported on macOS"
    label = _plist_label(job["name"])
    path = _plist_path(job["name"])
    env_xml = ""
    for k, v in (job.get("env") or {}).items():
        env_xml += f"        <key>{k}</key><string>{v}</string>\n"
    env_block = (
        f"    <key>EnvironmentVariables</key>\n    <dict>\n{env_xml}    </dict>\n"
        if env_xml
        else ""
    )
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"    <key>Label</key><string>{label}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        "        <string>/bin/sh</string>\n"
        "        <string>-c</string>\n"
        f"        <string>{job['command']}</string>\n"
        "    </array>\n"
        f"{env_block}"
        "</dict>\n</plist>\n"
    )
    path.write_text(plist)
    try:
        subprocess.run(
            ["launchctl", "load", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return f"launchctl load failed: {exc}"
    return None


def _unregister_launchd(name: str) -> None:
    """Best-effort unload + delete the plist for ``name``."""
    path = _plist_path(name)
    if path.exists():
        try:
            subprocess.run(
                ["launchctl", "unload", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            pass
        try:
            path.unlink()
        except OSError:
            pass


class CronCreateTool(BaseTool):
    """Create a scheduled job entry.

    Persists the job to the on-disk store and, when ``register`` is true on
    macOS, registers a launchd plist for it.
    """

    name = "cron_create"
    description = "Create a scheduled job (persisted to ~/.chimera/cron/jobs.json)."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Unique job name"},
            "schedule": {"type": "string", "description": "Cron expression (5-field)"},
            "command": {"type": "string", "description": "Shell command to run"},
            "env": {
                "type": "object",
                "description": "Optional env vars",
                "additionalProperties": {"type": "string"},
            },
            "register": {
                "type": "boolean",
                "description": "If true on macOS, register via launchctl.",
                "default": False,
            },
        },
        "required": ["name", "schedule", "command"],
    }
    is_destructive = True

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        name = args["name"]
        jobs = _load_jobs()
        if any(j.get("name") == name for j in jobs):
            return ToolResult(output="", error=f"job {name!r} already exists")
        job: dict[str, Any] = {
            "name": name,
            "schedule": args["schedule"],
            "command": args["command"],
            "env": args.get("env") or {},
            "registered": False,
        }
        if args.get("register"):
            err = _register_launchd(job)
            if err is not None:
                return ToolResult(output="", error=err)
            job["registered"] = True
        jobs.append(job)
        _save_jobs(jobs)
        suffix = " (registered)" if job["registered"] else ""
        return ToolResult(output=f"created job {name!r}{suffix}")


class CronListTool(BaseTool):
    """List all registered scheduled jobs."""

    name = "cron_list"
    description = "List scheduled jobs from ~/.chimera/cron/jobs.json."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    is_read_only = True
    is_concurrency_safe = True

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        jobs = _load_jobs()
        return ToolResult(output=json.dumps(jobs, indent=2))


class CronDeleteTool(BaseTool):
    """Delete a scheduled job by name."""

    name = "cron_delete"
    description = "Delete a scheduled job and unregister it if needed."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Job name to delete"},
        },
        "required": ["name"],
    }
    is_destructive = True

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        name = args["name"]
        jobs = _load_jobs()
        keep = [j for j in jobs if j.get("name") != name]
        if len(keep) == len(jobs):
            return ToolResult(output="", error=f"no job named {name!r}")
        was_registered = any(
            j.get("name") == name and j.get("registered") for j in jobs
        )
        if was_registered and shutil.which("launchctl") is not None:
            _unregister_launchd(name)
        _save_jobs(keep)
        return ToolResult(output=f"deleted job {name!r}")


__all__ = ["CronCreateTool", "CronListTool", "CronDeleteTool"]
