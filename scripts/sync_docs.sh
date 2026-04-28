#!/usr/bin/env bash
# sync_docs.sh
#
# Mirror canonical Markdown docs from `docs/<tree>/*.md` into the
# Astro/Starlight content tree at `site/src/content/docs/<tree>/*.md`.
#
# Why this exists:
#   wave-1 W7 ported docs/otter/ into the Astro site by hand and added
#   YAML frontmatter (title/description) to each file so Starlight could
#   render them. The two trees will drift over time without tooling --
#   this script is that tooling, additive only:
#
#     * Walks docs/<tree>/ for every *.md file.
#     * Compares the BODY (frontmatter stripped from the destination)
#       against the canonical source.
#     * On drift, rewrites the destination with frontmatter + canonical
#       body, preserving any existing frontmatter title/description.
#     * If the destination is missing or has no frontmatter, derives a
#       default title (first H1) and description (first paragraph after
#       H1) from the canonical source.
#
# Trees synced:
#   docs/otter/  -> site/src/content/docs/otter/
#   docs/mink/   -> site/src/content/docs/mink/   (created on first run)
#
# Usage:
#   bash scripts/sync_docs.sh           # write changes
#   bash scripts/sync_docs.sh --check   # exit 1 on drift, no writes
#
# Stdlib-only Python; runs from CI (.github/workflows/ci.yml job
# `docs-sync-check`).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export REPO_ROOT

exec python3 - "$@" <<'PY'
"""Mirror docs/<tree>/*.md into site/src/content/docs/<tree>/*.md."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[0])
# When invoked via the bash wrapper above, __file__ is the heredoc temp;
# fall back to the wrapper's REPO_ROOT computation.
if not (REPO_ROOT / "docs").is_dir():
    # Walk up from cwd to find the repo root by looking for `docs/` + `site/`.
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "docs").is_dir() and (candidate / "site").is_dir():
            REPO_ROOT = candidate
            break

TREES = [
    ("otter", REPO_ROOT / "docs" / "otter", REPO_ROOT / "site" / "src" / "content" / "docs" / "otter"),
    ("mink",  REPO_ROOT / "docs" / "mink",  REPO_ROOT / "site" / "src" / "content" / "docs" / "mink"),
]

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Empty dict if no frontmatter.

    Only `title:` and `description:` keys are extracted; values may be
    bare or quoted. We do not parse arbitrary YAML.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    fm: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        fm[key] = value
    return fm, body


def derive_title_description(body: str, fallback_name: str) -> tuple[str, str]:
    """Derive a title from the first H1 and a description from the
    first non-empty paragraph after it.
    """
    title = fallback_name
    description = ""
    h1 = H1_RE.search(body)
    if h1:
        # Strip backticks/markdown noise from the H1 for a clean title.
        title = re.sub(r"[`*_]+", "", h1.group(1)).strip()
        # First paragraph after H1.
        rest = body[h1.end():]
        for para in re.split(r"\n\s*\n", rest):
            para = para.strip()
            if not para or para.startswith("#"):
                continue
            # Flatten newlines, strip markdown emphasis. Keep it short.
            flat = re.sub(r"\s+", " ", para)
            flat = re.sub(r"`([^`]+)`", r"\1", flat)
            flat = re.sub(r"\*\*([^*]+)\*\*", r"\1", flat)
            flat = re.sub(r"\*([^*]+)\*", r"\1", flat)
            flat = re.sub(r"_([^_]+)_", r"\1", flat)
            flat = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", flat)
            description = flat[:240].strip()
            break
    return title, description


def yaml_quote(value: str) -> str:
    """Quote a YAML scalar if it contains characters that would break
    the simple `key: value` line form. Always emit single-line.
    """
    if not value:
        return '""'
    needs_quote = (
        value[0] in "!&*[]{}|>%@`" or
        ":" in value or
        "#" in value or
        value.strip() != value
    )
    if needs_quote:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def render_frontmatter(title: str, description: str) -> str:
    return (
        "---\n"
        f"title: {yaml_quote(title)}\n"
        f"description: {yaml_quote(description)}\n"
        "---\n\n"
    )


def desired_destination_text(
    source_text: str,
    existing_dest_text: str | None,
    source_stem: str,
) -> str:
    """Compute what the destination file SHOULD contain, given the
    canonical source body and (optionally) the prior destination file
    (whose frontmatter we want to preserve)."""
    src_fm, src_body = split_frontmatter(source_text)

    if existing_dest_text is not None:
        dst_fm, _ = split_frontmatter(existing_dest_text)
    else:
        dst_fm = {}

    # Preference order for title/description:
    #   1. existing destination frontmatter (W7 wrote these by hand)
    #   2. canonical source frontmatter (if any)
    #   3. derived from first H1 / paragraph
    derived_title, derived_desc = derive_title_description(src_body, source_stem)
    title = (
        dst_fm.get("title")
        or src_fm.get("title")
        or derived_title
    )
    description = (
        dst_fm.get("description")
        or src_fm.get("description")
        or derived_desc
    )

    # If the source already had frontmatter, the body is what's after it;
    # otherwise the whole source text is the body.
    body = src_body if src_fm else source_text

    # Ensure body starts with a single leading newline removed (we add
    # exactly one blank line between frontmatter and body).
    body = body.lstrip("\n")

    return render_frontmatter(title, description) + body


def is_in_sync(
    source_text: str,
    existing_dest_text: str | None,
) -> bool:
    """A destination is in sync with its canonical source iff:

      1. It exists.
      2. It has frontmatter with both `title` and `description` set.
      3. Its body (post-frontmatter) matches the canonical source body
         (where the canonical source body is the source text minus its
         own frontmatter, if any).

    This intentionally tolerates frontmatter-style differences (quoted
    vs. unquoted, key ordering, additional keys) so the script never
    fights authors who run `pnpm format` or hand-edit titles.
    """
    if existing_dest_text is None:
        return False
    dst_fm, dst_body = split_frontmatter(existing_dest_text)
    if not dst_fm.get("title") or not dst_fm.get("description"):
        return False
    _, src_body = split_frontmatter(source_text)
    return dst_body.lstrip("\n") == src_body.lstrip("\n")


def sync_tree(name: str, src_dir: Path, dst_dir: Path, check_only: bool) -> tuple[int, int, list[str]]:
    """Return (synced_count, unchanged_count, drift_paths)."""
    if not src_dir.is_dir():
        return 0, 0, []

    drift: list[str] = []
    synced = 0
    unchanged = 0

    if not dst_dir.is_dir():
        if not check_only:
            dst_dir.mkdir(parents=True, exist_ok=True)
        # In --check mode we still want to enumerate the per-file drift
        # below (file path, not directory) so the failure message is
        # actionable.

    for src_path in sorted(src_dir.glob("*.md")):
        rel = src_path.name
        dst_path = dst_dir / rel
        source_text = src_path.read_text(encoding="utf-8")
        existing_text = dst_path.read_text(encoding="utf-8") if dst_path.exists() else None

        if is_in_sync(source_text, existing_text):
            unchanged += 1
            continue

        # Drift detected. Compute what the file should look like.
        desired = desired_destination_text(source_text, existing_text, src_path.stem)

        if check_only:
            drift.append(str(dst_path.relative_to(REPO_ROOT)))
        else:
            dst_path.write_text(desired, encoding="utf-8")
            synced += 1

    return synced, unchanged, drift


def main(argv: list[str]) -> int:
    check_only = "--check" in argv[1:]
    extras = [a for a in argv[1:] if a not in ("--check",)]
    if extras:
        sys.stderr.write(f"sync_docs.sh: unknown argument: {extras[0]}\n")
        sys.stderr.write("usage: sync_docs.sh [--check]\n")
        return 2

    total_synced = 0
    total_unchanged = 0
    all_drift: list[str] = []

    for name, src_dir, dst_dir in TREES:
        synced, unchanged, drift = sync_tree(name, src_dir, dst_dir, check_only)
        total_synced += synced
        total_unchanged += unchanged
        all_drift.extend(drift)
        if not src_dir.is_dir():
            print(f"[{name}] skipped (no {src_dir.relative_to(REPO_ROOT)}/)")
            continue
        if check_only:
            print(f"[{name}] checked: {unchanged} in sync, {len(drift)} drifted")
        else:
            print(f"[{name}] synced: {synced} written, {unchanged} unchanged")

    if check_only and all_drift:
        sys.stderr.write("\nDrift detected in:\n")
        for path in all_drift:
            sys.stderr.write(f"  {path}\n")
        sys.stderr.write("\nRun `bash scripts/sync_docs.sh` (without --check) to fix.\n")
        return 1

    print(f"\nTotal: {total_synced} synced, {total_unchanged} unchanged"
          + (f", {len(all_drift)} drifted" if check_only else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
PY
