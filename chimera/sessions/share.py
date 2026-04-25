"""Session sharing via URL — gist, file, or base64 data URI sinks.

Packages an :class:`~chimera.sessions.eventlog.session.EventSourcedSession`
(or any directory under an eventlog root that holds ``summary.json`` plus
``event-*.json`` files) into a portable gzip-compressed tarball, and
reverses the operation when importing.

Three sinks are supported:

* ``gist`` — shells out to ``gh gist create`` (private gist of the
  ``.tar.gz``); returns the gist URL.
* ``file`` — writes ``~/.chimera/exports/<session_id>.tar.gz``; returns
  the absolute path.
* ``base64`` — returns a ``data:application/x-mink-session;base64,...``
  URI suitable for pasting into chat or email.

The import side accepts any of the three: gist URL (fetched via
``urllib`` against ``raw.githubusercontent.com`` style raw URLs), file
path, or data URI. Issue #129.
"""
from __future__ import annotations

import base64
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

__all__ = [
    "DATA_URI_PREFIX",
    "VALID_SINKS",
    "export_to_url",
    "import_from_url",
]


# WHY: a custom MIME type makes the data URI self-describing — anything
# that handles ``data:`` URIs can route the payload back through
# ``import_from_url`` without ambiguity about what's inside.
DATA_URI_PREFIX = "data:application/x-mink-session;base64,"

VALID_SINKS = ("gist", "file", "base64")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def _default_eventlog_root() -> Path:
    """Return the canonical mink eventlog root.

    Imported lazily to keep this module free of cross-package side
    effects at import time. Shadows the constant defined in
    :mod:`chimera.mink.runs` rather than importing it so this module can
    stand on its own when ``mink`` is not installed.
    """
    return Path.home() / ".chimera" / "eventlog"


def _default_export_dir() -> Path:
    """Return ``~/.chimera/exports/`` (created lazily by callers)."""
    return Path.home() / ".chimera" / "exports"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _validate_sink(sink: str) -> None:
    """Raise ``ValueError`` when ``sink`` is not one of :data:`VALID_SINKS`."""
    if sink not in VALID_SINKS:
        raise ValueError(
            f"unknown sink {sink!r}: expected one of {', '.join(VALID_SINKS)}"
        )


def _resolve_session_dir(session_id: str, eventlog_root: Path | None) -> Path:
    """Return the absolute path to ``<eventlog_root>/<session_id>/``.

    Raises:
        FileNotFoundError: When the directory does not exist.
    """
    root = eventlog_root or _default_eventlog_root()
    session_dir = root / session_id
    if not session_dir.is_dir():
        raise FileNotFoundError(
            f"session directory not found: {session_dir} "
            f"(eventlog root: {root})"
        )
    return session_dir


def _build_tarball(session_dir: Path) -> bytes:
    """Pack ``session_dir`` into a gzip-compressed tarball as bytes.

    The archive uses ``session_dir.name`` as the top-level directory so
    extraction round-trips cleanly into any eventlog root.
    """
    buf = io.BytesIO()
    # WHY: ``w:gz`` is single-pass and avoids leaving temp files on disk.
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(session_dir), arcname=session_dir.name)
    return buf.getvalue()


def _write_file_sink(session_id: str, tarball: bytes) -> str:
    """Write ``tarball`` to ``~/.chimera/exports/<session_id>.tar.gz``."""
    export_dir = _default_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    out_path = export_dir / f"{session_id}.tar.gz"
    out_path.write_bytes(tarball)
    return str(out_path.resolve())


def _write_base64_sink(tarball: bytes) -> str:
    """Encode ``tarball`` as a ``data:`` URI."""
    encoded = base64.b64encode(tarball).decode("ascii")
    return f"{DATA_URI_PREFIX}{encoded}"


def _write_gist_sink(session_id: str, tarball: bytes) -> str:
    """Shell out to ``gh gist create -p`` and return the gist URL.

    Writes ``tarball`` to a temp file first because ``gh gist create``
    uploads files by path — passing via stdin would lose the binary
    framing through gist's text-only upload path.

    Raises:
        RuntimeError: When ``gh`` is missing or the gist upload fails.
    """
    if shutil.which("gh") is None:
        raise RuntimeError(
            "gh CLI not found on PATH; install it (https://cli.github.com) "
            "and run 'gh auth login' before using sink='gist'."
        )
    # WHY: gh requires a real file (binary uploads can't go through
    # stdin), so we materialize to a NamedTemporaryFile, then unlink it
    # ourselves once gh has read it.
    with tempfile.NamedTemporaryFile(
        suffix=f"-{session_id}.tar.gz", delete=False,
    ) as fh:
        fh.write(tarball)
        tmp_path = Path(fh.name)
    try:
        result = subprocess.run(
            ["gh", "gist", "create", "-p", str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if result.returncode != 0:
        raise RuntimeError(
            f"gh gist create failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    # gh prints the URL on stdout (sometimes preceded by a status line);
    # take the last non-empty line that looks like a URL.
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.startswith("https://"):
            return line
    raise RuntimeError(
        f"gh gist create succeeded but no URL was returned. "
        f"stdout={result.stdout!r}"
    )


def export_to_url(
    session_id: str,
    sink: str,
    eventlog_root: Path | None = None,
) -> str:
    """Package an EventSourcedSession into a portable share token.

    Args:
        session_id: Directory name under ``eventlog_root`` to export
            (e.g. ``mink-20260424T051001-71032a5e`` or any UUID).
        sink: One of ``"gist"``, ``"file"``, or ``"base64"``. See module
            docstring for what each returns.
        eventlog_root: Override the eventlog root. Defaults to
            ``~/.chimera/eventlog``.

    Returns:
        A string whose meaning depends on ``sink``: gist URL, absolute
        file path, or ``data:`` URI.

    Raises:
        ValueError: When ``sink`` is unknown.
        FileNotFoundError: When the session directory doesn't exist.
        RuntimeError: When the gist sink fails (gh missing / upload error).
    """
    _validate_sink(sink)
    session_dir = _resolve_session_dir(session_id, eventlog_root)
    tarball = _build_tarball(session_dir)

    if sink == "file":
        return _write_file_sink(session_id, tarball)
    if sink == "base64":
        return _write_base64_sink(tarball)
    # WHY: gist sink last so the cheaper-to-test paths run first when
    # exercised via the CLI without 'gh' configured.
    return _write_gist_sink(session_id, tarball)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


_GIST_URL_RE = re.compile(r"^https?://(?:gist\.github\.com|gist\.githubusercontent\.com)/")


def _looks_like_data_uri(token: str) -> bool:
    """Return True when ``token`` is a chimera-session ``data:`` URI."""
    return token.startswith(DATA_URI_PREFIX)


def _looks_like_gist_url(token: str) -> bool:
    """Return True when ``token`` looks like a GitHub gist URL."""
    return bool(_GIST_URL_RE.match(token))


def _fetch_gist_tarball(url: str) -> bytes:
    """Fetch a gist's first attached tarball file and return its bytes.

    The strategy: when the user pastes a gist URL we resolve the raw
    download endpoint via ``gh gist view --raw <id>`` (which prints the
    file contents to stdout). This avoids needing to scrape the HTML
    page or guess raw URL patterns — gh handles auth and routing.
    """
    if shutil.which("gh") is None:
        # Fallback: try a direct urllib fetch (works for public gists
        # when the URL already points at the raw file).
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return bytes(resp.read())
    # Pull the gist id out of the URL (last non-empty path segment).
    gist_id = url.rstrip("/").split("/")[-1]
    result = subprocess.run(
        ["gh", "gist", "view", "--raw", gist_id],
        check=False,
        capture_output=False,
        timeout=60,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh gist view failed (exit {result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def _decode_token(token_or_path: str | Path) -> bytes:
    """Resolve ``token_or_path`` to raw tarball bytes.

    Accepts:
        * Local filesystem path (``Path`` or ``str``).
        * ``data:application/x-mink-session;base64,...`` URI.
        * ``https://gist.github.com/...`` URL.

    Raises:
        ValueError: When the token can't be classified.
    """
    token = str(token_or_path)
    if _looks_like_data_uri(token):
        encoded = token[len(DATA_URI_PREFIX):]
        return base64.b64decode(encoded)
    if _looks_like_gist_url(token):
        return _fetch_gist_tarball(token)
    # Fall through: treat as a filesystem path.
    path = Path(token)
    if not path.exists():
        raise ValueError(
            f"could not classify token as data URI, gist URL, or existing "
            f"file path: {token!r}"
        )
    return path.read_bytes()


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> str:
    """Extract ``tf`` into ``dest`` rejecting traversal, return run id.

    Returns the top-level directory name of the archive (the ``run_id``
    that the export was built from). Raises ``ValueError`` when any
    member tries to escape ``dest`` via ``..`` or absolute paths.
    """
    dest_resolved = dest.resolve()
    top_level: str | None = None
    for member in tf.getmembers():
        # WHY: protect against tarbombs and absolute-path escapes —
        # tarfile.data_filter exists in 3.12+ but we keep this manual
        # check to support 3.11 and to fail loudly with a clear message.
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError(
                f"refusing to extract member outside dest: {member.name!r}"
            ) from exc
        first_segment = member.name.split("/", 1)[0]
        if top_level is None:
            top_level = first_segment
        elif first_segment != top_level:
            raise ValueError(
                f"archive has multiple top-level dirs ({top_level!r}, "
                f"{first_segment!r}); expected exactly one session"
            )
    if top_level is None:
        raise ValueError("archive contains no entries")
    # WHY: ``filter="data"`` (Python 3.12+) silences the 3.14 deprecation
    # and applies the safe-extract policy on top of our own traversal
    # check. We already iterated members above, so any residual
    # rejection from the filter is fine.
    tf.extractall(str(dest), filter="data")
    return top_level


def import_from_url(
    url_or_path: str | Path,
    target_eventlog_root: Path | None = None,
) -> str:
    """Inverse of :func:`export_to_url`: extract a share token to disk.

    Args:
        url_or_path: A gist URL, local ``.tar.gz`` path, or
            ``data:application/x-mink-session;base64,...`` URI.
        target_eventlog_root: Where to extract. Defaults to
            ``~/.chimera/eventlog``.

    Returns:
        The ``run_id`` (top-level directory name) that was extracted.
        The caller can resume it via
        ``EventSourcedSession.resume(target_eventlog_root, run_id, ...)``.

    Raises:
        ValueError: When the token can't be classified or the tarball is
            malformed (e.g. multiple top-level dirs, traversal attempt).
    """
    root = target_eventlog_root or _default_eventlog_root()
    root.mkdir(parents=True, exist_ok=True)
    tarball = _decode_token(url_or_path)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        run_id = _safe_extract(tf, root)
    return run_id
