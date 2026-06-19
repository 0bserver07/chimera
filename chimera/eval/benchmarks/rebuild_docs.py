"""RAG-augmented repair: fetch real library docs when a build fails on an
unknown symbol, and feed them into the next repair round.

The compile-repair loop (:mod:`chimera.eval.benchmarks.programbench_rebuild`)
stalls when the model guesses a third-party API wrong — e.g. Rust ``no method
named `is_encrypted` found for struct `ZipFile``` because it invented the ``zip``
crate's API. This module:

    * :func:`parse_missing_symbols` — pull the offending symbols/types out of a
      compiler error (pure, testable).
    * :func:`crates_from_cargo_toml` — list the crate deps so we know what to
      look up (pure, testable).
    * :class:`DocProvider` — protocol for "given symbols + crates, return doc
      text". Inject a fake in tests.
    * :class:`DocsRsProvider` — best-effort Rust docs via docs.rs (stdlib
      urllib; returns ``""`` on any failure, so the loop degrades to plain
      repair).

Wiring lives in ``programbench_rebuild.rebuild(..., doc_provider=...)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Rust diagnostics that name a symbol the model got wrong. ``[^`<\s]+`` stops a
# container capture before generics (``ZipFile<'a>`` -> ``ZipFile``).
_RUST_METHOD = re.compile(r"no method named `([^`]+)` found for \w+ `([^`<\s]+)")
_RUST_ASSOC = re.compile(
    r"no (?:function or )?associated item named `([^`]+)` found for \w+ `([^`<\s]+)"
)
_RUST_IMPORT = re.compile(r"unresolved import `([^`]+)`")
_RUST_UNDECLARED = re.compile(r"use of undeclared (?:crate or module|type) `([^`]+)`")
_RUST_TRAIT = re.compile(r"the trait `([^`<]+)[^`]*` is not implemented")
_RUST_CANNOT_FIND = re.compile(
    r"cannot find (?:function|value|macro|type|trait) `([^`]+)` in"
)

# C/C++ diagnostics.
_C_IMPLICIT = re.compile(r"implicit declaration of function ['`]([A-Za-z_]\w*)")
_C_UNDEFINED = re.compile(r"undefined reference to ['`]([A-Za-z_]\w*)")


@dataclass(frozen=True)
class DocQuery:
    """A symbol whose real definition we want to look up.

    Attributes:
        symbol: The function/method/type/trait name (may be empty for a bare
            "undeclared crate" hit).
        container: The owning type or crate/module, when the error names one.
        language: ``"rust"`` / ``"c"`` / ``""``.
    """

    symbol: str
    container: str = ""
    language: str = ""


@runtime_checkable
class DocProvider(Protocol):
    """Given the missing symbols and the project's crate deps, return doc text
    to splice into the repair prompt (``""`` if nothing useful)."""

    def fetch(self, symbols: list[DocQuery], crates: list[str]) -> str: ...


def _dedupe(queries: list[DocQuery]) -> list[DocQuery]:
    seen: set[tuple[str, str]] = set()
    out: list[DocQuery] = []
    for q in queries:
        key = (q.symbol, q.container)
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


def parse_missing_symbols(error_text: str, language: str = "") -> list[DocQuery]:
    """Extract symbols/types a build complained it couldn't find.

    Args:
        error_text: Raw or focused compiler output.
        language: ``"rust"``/``"rs"``, ``"c"``/``"cpp"``, or ``""`` (try all).

    Returns:
        A de-duplicated list of :class:`DocQuery`.
    """
    lang = language.lower()
    queries: list[DocQuery] = []

    if lang in ("rust", "rs", ""):
        for sym, cont in _RUST_METHOD.findall(error_text):
            queries.append(DocQuery(sym, cont, "rust"))
        for sym, cont in _RUST_ASSOC.findall(error_text):
            queries.append(DocQuery(sym, cont, "rust"))
        for path in _RUST_IMPORT.findall(error_text):
            segs = [s for s in path.split("::") if s]
            if segs:
                container = segs[0] if len(segs) > 1 else ""
                queries.append(DocQuery(segs[-1], container, "rust"))
        for crate in _RUST_UNDECLARED.findall(error_text):
            queries.append(DocQuery("", crate, "rust"))
        for trait in _RUST_TRAIT.findall(error_text):
            queries.append(DocQuery(trait.strip(), "", "rust"))
        for sym in _RUST_CANNOT_FIND.findall(error_text):
            queries.append(DocQuery(sym, "", "rust"))

    if lang in ("c", "cpp", "c++", ""):
        for sym in _C_IMPLICIT.findall(error_text):
            queries.append(DocQuery(sym, "", "c"))
        for sym in _C_UNDEFINED.findall(error_text):
            queries.append(DocQuery(sym, "", "c"))

    return _dedupe(queries)


def crates_from_cargo_toml(text: str) -> list[str]:
    """Parse crate names from a ``Cargo.toml``'s dependency tables.

    Handles inline (``zip = "0.6"``) and sub-table
    (``[dependencies.zip]``) forms across ``[dependencies]``,
    ``[dev-dependencies]`` and ``[build-dependencies]``. Best-effort, no TOML
    library (zero-dep).
    """
    crates: list[str] = []
    in_deps = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            in_deps = header in (
                "dependencies",
                "dev-dependencies",
                "build-dependencies",
            )
            for prefix in ("dependencies.", "dev-dependencies.", "build-dependencies."):
                if header.startswith(prefix):
                    crates.append(header[len(prefix):].strip())
            continue
        if in_deps and "=" in line and not line.startswith("#"):
            name = line.split("=", 1)[0].strip().strip('"')
            if name:
                crates.append(name)
    return list(dict.fromkeys(crates))


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t\n]*")


def _strip_html(html: str) -> str:
    """Crudely reduce an HTML page to readable text (no external deps)."""
    html = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html)
    text = _TAG_RE.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[ \t]{2,}", " ", text)
    return _WS_RE.sub("\n", text).strip()


class DocsRsProvider:
    """Best-effort Rust API docs from docs.rs (stdlib urllib only).

    For each crate it fetches ``https://docs.rs/<crate>/latest/<crate>/`` and
    returns the de-chromed text. Any failure (network, 404, timeout) yields no
    text for that crate, so the caller degrades to plain repair. Not a precise
    rustdoc parse — it gives the model the crate's item surface to correct an
    invented API.
    """

    def __init__(self, timeout: float = 8.0, max_chars_per_crate: int = 2800) -> None:
        self.timeout = timeout
        self.max_chars_per_crate = max_chars_per_crate

    def fetch(self, symbols: list[DocQuery], crates: list[str]) -> str:
        import urllib.request

        # Prefer crates explicitly named in errors, then Cargo deps.
        named = [q.container for q in symbols if q.container]
        ordered = list(dict.fromkeys([*named, *crates]))
        chunks: list[str] = []
        for crate in ordered:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", crate):
                continue
            url = f"https://docs.rs/{crate}/latest/{crate}/"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "chimera-programbench/1.0"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    html = resp.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — best-effort; degrade silently
                continue
            # Skip the docs.rs chrome/metadata sidebar — the actual rustdoc item
            # list (Structs, Enums, Functions, methods) lives under main-content.
            main = re.search(r'id="main(?:-content)?"[^>]*>(.*)$', html, re.S)
            text = _strip_html(main.group(1) if main else html)[: self.max_chars_per_crate]
            if text:
                chunks.append(f"=== docs.rs/{crate} ===\n{text}")
        return "\n\n".join(chunks)


__all__ = [
    "DocProvider",
    "DocQuery",
    "DocsRsProvider",
    "crates_from_cargo_toml",
    "parse_missing_symbols",
]
