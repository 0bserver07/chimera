# Agent Intelligence — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deepen Chimera's existing layers with 5 agent intelligence modules: MCP client, production LSP, project config, provider catalog, and fuzzy edit strategies.

**Architecture:** No new layers. Each module slots into the existing 6-layer stack. MCP/Config/LSPTool/FuzzyEdit in Layer 3 (Agent), Catalog in Layer 2 (Provider), LSPManager in Layer 1 (Environment). All follow the 3-tier API: one-liner, developer config, framework-author subclassing.

**Tech Stack:** Python 3.11+, stdlib only (`subprocess`, `json`, `threading`, `difflib`, `pathlib`, `importlib.metadata`). No new external dependencies.

---

## Context

Chimera has 978 tests passing across a 6-layer stack. The previous plan added 6 polish features (transactions, plugins, parallel ensemble, parsers, pricing, Docker). This plan adds the intelligence features that make the agent smarter: external tool discovery (MCP), code intelligence (LSP), project awareness (config), provider extensibility (catalog), and edit resilience (fuzzy).

Design doc: `docs/plans/2026-02-28-agent-intelligence-design.md`

---

## Task 1: Fuzzy Edit Strategies

**Files:**
- Create: `chimera/tools/strategies.py`
- Modify: `chimera/tools/edit.py`
- Create: `tests/test_edit_strategies.py`

**Why first:** Smallest task (~80 lines). Self-contained. No cross-module dependencies.

### `chimera/tools/strategies.py` (~60 lines):

```python
"""Fuzzy string matching strategies for the edit tool."""
from __future__ import annotations

import difflib
import re
from abc import ABC, abstractmethod
from enum import Enum


class MatchResult:
    """Result of a fuzzy match attempt."""

    __slots__ = ("start", "end", "strategy_name")

    def __init__(self, start: int, end: int, strategy_name: str) -> None:
        self.start = start
        self.end = end
        self.strategy_name = strategy_name


class EditStrategy(ABC):
    """Base class for edit matching strategies."""

    name: str = ""

    @abstractmethod
    def find(self, content: str, search: str) -> MatchResult | None:
        """Find *search* in *content*.

        Returns:
            A MatchResult with (start, end) positions in the original
            content, or None if no match.
        """


class ExactMatch(EditStrategy):
    """Character-for-character exact match."""

    name = "exact"

    def find(self, content: str, search: str) -> MatchResult | None:
        idx = content.find(search)
        if idx == -1:
            return None
        # Check uniqueness
        if content.find(search, idx + 1) != -1:
            return None  # ambiguous
        return MatchResult(idx, idx + len(search), self.name)


class StripLines(EditStrategy):
    """Strip leading/trailing whitespace per line before comparing."""

    name = "strip_lines"

    def find(self, content: str, search: str) -> MatchResult | None:
        stripped_search = "\n".join(line.strip() for line in search.split("\n"))
        lines = content.split("\n")
        search_lines = stripped_search.split("\n")
        n = len(search_lines)
        for i in range(len(lines) - n + 1):
            window = [lines[j].strip() for j in range(i, i + n)]
            if window == search_lines:
                start = sum(len(lines[j]) + 1 for j in range(i))
                end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
                return MatchResult(start, end, self.name)
        return None


class NormalizeWhitespace(EditStrategy):
    """Collapse runs of whitespace to single space before comparing."""

    name = "normalize_whitespace"

    def find(self, content: str, search: str) -> MatchResult | None:
        norm_search = re.sub(r"\s+", " ", search.strip())
        lines = content.split("\n")
        search_lines = search.strip().split("\n")
        n = len(search_lines)
        for i in range(len(lines) - n + 1):
            window = "\n".join(lines[i : i + n])
            norm_window = re.sub(r"\s+", " ", window.strip())
            if norm_window == norm_search:
                start = sum(len(lines[j]) + 1 for j in range(i))
                end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
                return MatchResult(start, end, self.name)
        return None


class IndentFlexible(EditStrategy):
    """Normalize indentation to relative levels before comparing."""

    name = "indent_flexible"

    def find(self, content: str, search: str) -> MatchResult | None:
        def relative_indent(text: str) -> list[tuple[int, str]]:
            lines = text.split("\n")
            result = []
            base = None
            for line in lines:
                stripped = line.lstrip()
                if not stripped:
                    result.append((0, ""))
                    continue
                indent = len(line) - len(stripped)
                if base is None:
                    base = indent
                result.append((indent - base, stripped))
            return result

        search_rel = relative_indent(search)
        lines = content.split("\n")
        n = len(search_rel)
        for i in range(len(lines) - n + 1):
            window = "\n".join(lines[i : i + n])
            window_rel = relative_indent(window)
            if len(window_rel) == len(search_rel):
                match = all(
                    wr[0] == sr[0] and wr[1] == sr[1]
                    for wr, sr in zip(window_rel, search_rel)
                )
                if match:
                    start = sum(len(lines[j]) + 1 for j in range(i))
                    end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
                    return MatchResult(start, end, self.name)
        return None


class LevenshteinMatch(EditStrategy):
    """Fuzzy match using SequenceMatcher with a similarity threshold."""

    name = "levenshtein"

    def __init__(self, threshold: float = 0.85) -> None:
        self.threshold = threshold

    def find(self, content: str, search: str) -> MatchResult | None:
        lines = content.split("\n")
        search_lines = search.split("\n")
        n = len(search_lines)
        best_ratio = 0.0
        best_start = -1
        best_end = -1
        for i in range(len(lines) - n + 1):
            window = "\n".join(lines[i : i + n])
            ratio = difflib.SequenceMatcher(None, window, search).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = sum(len(lines[j]) + 1 for j in range(i))
                best_end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
        if best_ratio >= self.threshold and best_start >= 0:
            return MatchResult(best_start, best_end, self.name)
        return None


# Default strategy chain
DEFAULT_STRATEGIES: list[EditStrategy] = [
    ExactMatch(),
    StripLines(),
    NormalizeWhitespace(),
    IndentFlexible(),
    LevenshteinMatch(),
]


class FuzzyEditor:
    """Tries strategies in order, returns first match.

    Args:
        strategies: Ordered list of strategies to try. Defaults to all 5.
    """

    def __init__(self, strategies: list[EditStrategy] | None = None) -> None:
        self.strategies = strategies or list(DEFAULT_STRATEGIES)

    def find(self, content: str, search: str) -> MatchResult | None:
        """Try each strategy in order, return first match."""
        for strategy in self.strategies:
            result = strategy.find(content, search)
            if result is not None:
                return result
        return None
```

### Changes to `chimera/tools/edit.py`:

Add an optional `editor` parameter to `EditFileTool.__init__`. When provided and exact match fails, try fuzzy matching:

```python
class EditFileTool(BaseTool):
    # ... existing class attrs ...

    def __init__(self, editor: FuzzyEditor | None = None) -> None:
        self._editor = editor

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args["path"]
        try:
            content = env.read_file(path)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {path}")

        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)

        if count == 1:
            # Exact match — existing behavior
            updated = content.replace(old, new, 1)
            match_strategy = "exact"
        elif self._editor is not None:
            # Try fuzzy strategies
            result = self._editor.find(content, old)
            if result is None:
                return ToolResult(output="", error=f"String not found in {path} (tried fuzzy matching)")
            updated = content[:result.start] + new + content[result.end:]
            match_strategy = result.strategy_name
        elif count == 0:
            return ToolResult(output="", error=f"String not found in {path}")
        else:
            return ToolResult(output="", error=f"Multiple matches ({count}) found — ambiguous. Provide more context.")

        env.write_file(path, updated)
        fc = FileChange(
            path=path,
            change_type=ChangeType.EDIT,
            before_content=content,
            after_content=updated,
            diff=FileChange.compute_diff(path, content, updated),
        )
        return ToolResult(
            output=f"Edited {path}",
            metadata={"file_change": fc, "match_strategy": match_strategy},
        )
```

Add import at top: `from chimera.tools.strategies import FuzzyEditor`

### Tests (`tests/test_edit_strategies.py`, ~15 tests):

**Strategy unit tests:**
1. `test_exact_match_found` — exact substring found
2. `test_exact_match_ambiguous` — returns None when multiple matches
3. `test_exact_match_not_found` — returns None
4. `test_strip_lines_match` — matches with extra whitespace per line
5. `test_normalize_whitespace_match` — matches with collapsed whitespace
6. `test_indent_flexible_match` — matches with different indentation base
7. `test_levenshtein_match` — matches with small typo
8. `test_levenshtein_below_threshold` — too different, returns None

**FuzzyEditor tests:**
9. `test_fuzzy_editor_exact_first` — exact match preferred
10. `test_fuzzy_editor_fallback` — exact fails, strip_lines succeeds
11. `test_fuzzy_editor_none` — nothing matches

**EditFileTool integration:**
12. `test_edit_without_editor_exact_only` — default behavior unchanged
13. `test_edit_with_editor_fuzzy_fallback` — fuzzy editor catches whitespace mismatch
14. `test_edit_with_editor_reports_strategy` — metadata["match_strategy"] set
15. `test_edit_existing_tests_unchanged` — run existing test_tools_edit.py to verify backward compat

**Verification:** `python -m pytest tests/test_edit_strategies.py tests/test_tools_edit.py -v`

---

## Task 2: Provider Catalog

**Files:**
- Create: `chimera/providers/catalog.py`
- Modify: `chimera/providers/factory.py`
- Modify: `chimera/providers/__init__.py`
- Modify: `chimera/__init__.py`
- Create: `tests/test_provider_catalog.py`

### `chimera/providers/catalog.py` (~120 lines):

```python
"""Dynamic provider registry mapping model names to configurations."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.providers.base import Provider

from chimera.providers.cost import register_model_cost


@dataclass
class ModelConfig:
    """Configuration for a model in the provider catalog.

    Args:
        model: Model name or prefix (e.g. "deepseek-chat", "bedrock/claude-sonnet-4").
        provider_type: Provider backend ("anthropic", "openai", "google", "ollama",
            "compatible", "modal").
        base_url: API base URL. Use base_url_env to read from environment.
        base_url_env: Environment variable name for base URL.
        api_key_env: Environment variable name for API key.
        context_window: Context window size in tokens.
        supports_tool_use: Whether the model supports tool calling.
        cost: Tuple of (input_cost_per_mtok, output_cost_per_mtok) in USD.
        extra: Additional kwargs passed to the provider constructor.
    """

    model: str
    provider_type: str = "compatible"
    base_url: str | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None
    context_window: int = 128_000
    supports_tool_use: bool = True
    cost: tuple[float, float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve_base_url(self) -> str | None:
        """Resolve base URL from direct value or environment variable."""
        if self.base_url:
            return self.base_url
        if self.base_url_env:
            return os.environ.get(self.base_url_env)
        return None

    def resolve_api_key(self) -> str | None:
        """Resolve API key from environment variable."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


# Built-in catalog entries
_BUILTIN_ENTRIES: list[ModelConfig] = [
    # AWS Bedrock (via OpenAI-compatible gateway)
    ModelConfig("bedrock/claude-sonnet-4", "compatible", base_url_env="AWS_BEDROCK_ENDPOINT",
                api_key_env="AWS_BEDROCK_KEY", context_window=200_000, cost=(3.0, 15.0)),
    ModelConfig("bedrock/claude-haiku-3.5", "compatible", base_url_env="AWS_BEDROCK_ENDPOINT",
                api_key_env="AWS_BEDROCK_KEY", context_window=200_000, cost=(0.80, 4.0)),
    # Azure OpenAI
    ModelConfig("azure/gpt-4o", "compatible", base_url_env="AZURE_OPENAI_ENDPOINT",
                api_key_env="AZURE_OPENAI_KEY", context_window=128_000, cost=(2.50, 10.0)),
    ModelConfig("azure/gpt-4o-mini", "compatible", base_url_env="AZURE_OPENAI_ENDPOINT",
                api_key_env="AZURE_OPENAI_KEY", context_window=128_000, cost=(0.15, 0.60)),
    # Groq
    ModelConfig("groq/llama-3.3-70b", "compatible", base_url="https://api.groq.com/openai/v1",
                api_key_env="GROQ_API_KEY", context_window=128_000, cost=(0.59, 0.79)),
    # DeepSeek
    ModelConfig("deepseek-chat", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=64_000, cost=(0.27, 1.10)),
    ModelConfig("deepseek-reasoner", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=64_000, cost=(0.55, 2.19)),
]


class ProviderCatalog:
    """Dynamic registry mapping model names to provider configurations.

    Supports slash-namespaced models (e.g. "bedrock/claude-sonnet-4") and
    plain model names. Integrates with create_provider() as a fallback.

    Example:
        ```python
        catalog = ProviderCatalog.default()
        catalog.register(ModelConfig(
            model="my-company/llm",
            provider_type="compatible",
            base_url="https://llm.internal/v1",
        ))
        provider = catalog.create("my-company/llm")
        ```
    """

    def __init__(self) -> None:
        self._entries: dict[str, ModelConfig] = {}
        self._provider_types: dict[str, type] = {}

    @classmethod
    def default(cls) -> ProviderCatalog:
        """Create a catalog pre-loaded with built-in entries."""
        catalog = cls()
        for entry in _BUILTIN_ENTRIES:
            catalog.register(entry)
        return catalog

    def register(self, config: ModelConfig) -> None:
        """Register a model configuration.

        Args:
            config: Model configuration to register.
        """
        self._entries[config.model] = config
        if config.cost is not None:
            register_model_cost(config.model, config.cost[0], config.cost[1])

    def register_provider_type(self, name: str, provider_class: type) -> None:
        """Register a custom provider type.

        Args:
            name: Provider type name (e.g. "vllm").
            provider_class: Provider class to instantiate.
        """
        self._provider_types[name] = provider_class

    def get(self, model: str) -> ModelConfig | None:
        """Look up a model configuration.

        Args:
            model: Model name (e.g. "bedrock/claude-sonnet-4").

        Returns:
            ModelConfig if found, None otherwise.
        """
        return self._entries.get(model)

    def create(self, model: str) -> Provider:
        """Create a provider instance from a catalog entry.

        Args:
            model: Model name registered in the catalog.

        Returns:
            Configured Provider instance.

        Raises:
            KeyError: If model not found in catalog.
        """
        config = self._entries.get(model)
        if config is None:
            raise KeyError(f"Model '{model}' not found in catalog")

        from chimera.providers.factory import create_provider

        base_url = config.resolve_base_url()
        api_key = config.resolve_api_key()

        return create_provider(
            provider_type=config.provider_type,
            model=config.model.split("/")[-1] if "/" in config.model else config.model,
            api_key=api_key,
            base_url=base_url,
            **config.extra,
        )

    @property
    def models(self) -> list[str]:
        """List all registered model names."""
        return list(self._entries.keys())
```

### Changes to `chimera/providers/factory.py`:

Add catalog fallback to `create_provider()`. After `_infer_provider()` fails, try the default catalog:

```python
def _infer_provider(model: str) -> str:
    # ... existing prefix matching ...

    # Catalog fallback: check if model is in default catalog
    from chimera.providers.catalog import ProviderCatalog
    catalog = ProviderCatalog.default()
    config = catalog.get(model)
    if config is not None:
        return config.provider_type

    raise ValueError(...)
```

### Changes to `chimera/providers/__init__.py`:

Add: `from chimera.providers.catalog import ModelConfig, ProviderCatalog`
Add to `__all__`: `"ModelConfig"`, `"ProviderCatalog"`

### Changes to `chimera/__init__.py`:

Add: `from chimera.providers.catalog import ModelConfig, ProviderCatalog`
Add to `__all__`: `"ModelConfig"`, `"ProviderCatalog"`

### Tests (`tests/test_provider_catalog.py`, ~12 tests):

1. `test_model_config_defaults` — ModelConfig with minimal args has sensible defaults
2. `test_model_config_resolve_base_url_direct` — base_url returned directly
3. `test_model_config_resolve_base_url_env` — reads from env var
4. `test_model_config_resolve_api_key` — reads from env var
5. `test_catalog_register_and_get` — register ModelConfig, get it back
6. `test_catalog_default_has_entries` — default catalog has builtin entries
7. `test_catalog_register_custom_model` — register custom, verify in models list
8. `test_catalog_get_missing_returns_none` — unknown model → None
9. `test_catalog_create_raises_for_missing` — unknown model → KeyError
10. `test_catalog_cost_auto_registered` — register with cost, verify calculate_cost works
11. `test_catalog_register_provider_type` — register custom provider type
12. `test_slash_routing_strips_namespace` — "bedrock/claude-sonnet-4" → model passed as "claude-sonnet-4"

**Verification:** `python -m pytest tests/test_provider_catalog.py tests/test_provider_factory.py -v`

---

## Task 3: Project Config

**Files:**
- Create: `chimera/config/__init__.py`
- Create: `chimera/config/loader.py`
- Create: `chimera/config/skills.py`
- Create: `chimera/config/structured.py`
- Modify: `chimera/__init__.py`
- Create: `tests/test_config.py`

### `chimera/config/__init__.py`:

```python
from chimera.config.loader import ConfigSource, ProjectConfig
from chimera.config.skills import Skill, SkillRegistry
from chimera.config.structured import StructuredOutput

__all__ = [
    "ConfigSource",
    "ProjectConfig",
    "Skill",
    "SkillRegistry",
    "StructuredOutput",
]
```

### `chimera/config/loader.py` (~80 lines):

```python
"""Project configuration loader — discovers AGENTS.md, CLAUDE.md, and rules files."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from chimera.config.skills import SkillRegistry


class ConfigSource(ABC):
    """Abstract source for project rules."""

    @abstractmethod
    def load(self) -> list[str]:
        """Load rules as a list of text blocks."""


class FileConfigSource(ConfigSource):
    """Load rules from a file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[str]:
        if self._path.exists():
            return [self._path.read_text()]
        return []


# Default rules files, in priority order
_RULES_FILES = ("AGENTS.md", "CLAUDE.md", ".chimera/rules.md")


class ProjectConfig:
    """Discovers and aggregates project-level configuration.

    Searches for rules files (AGENTS.md, CLAUDE.md), skills directories,
    and other project-level settings.

    Example:
        ```python
        config = ProjectConfig.from_directory("./myapp")
        print(config.rules_text)  # Concatenated rules
        skill = config.get_skill("debugging")
        ```
    """

    def __init__(
        self,
        rules: list[str] | None = None,
        rules_files: list[str] | None = None,
        skills_dirs: list[str] | None = None,
        root: Path | None = None,
    ) -> None:
        self._root = root or Path.cwd()
        self._rules = rules or []
        self._rules_files = rules_files or list(_RULES_FILES)
        self._sources: list[ConfigSource] = []
        self._skills = SkillRegistry(
            [self._root / d for d in (skills_dirs or ["skills"])]
        )

        # Build sources from rules_files
        for rf in self._rules_files:
            self._sources.append(FileConfigSource(self._root / rf))

    @classmethod
    def from_directory(cls, path: str) -> ProjectConfig:
        """Auto-discover configuration from a project directory.

        Args:
            path: Project root directory.

        Returns:
            ProjectConfig with discovered rules and skills.
        """
        root = Path(path).resolve()
        # Discover skills directories
        skills_dirs = []
        for candidate in ("skills", ".chimera/skills", ".claude/skills"):
            if (root / candidate).is_dir():
                skills_dirs.append(candidate)
        return cls(root=root, skills_dirs=skills_dirs or ["skills"])

    @property
    def rules_text(self) -> str:
        """Concatenated text of all rules sources."""
        blocks = list(self._rules)
        for source in self._sources:
            blocks.extend(source.load())
        return "\n\n---\n\n".join(b for b in blocks if b.strip())

    def get_skill(self, name: str) -> "Skill | None":
        """Look up a skill by name.

        Args:
            name: Skill name.

        Returns:
            Skill if found, None otherwise.
        """
        from chimera.config.skills import Skill
        return self._skills.get(name)

    @property
    def skill_names(self) -> list[str]:
        """List all discovered skill names."""
        return self._skills.names
```

### `chimera/config/skills.py` (~60 lines):

```python
"""Skill discovery and loading from SKILL.md files."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """An on-demand instruction set loaded from a SKILL.md file.

    Args:
        name: Skill name (from directory name or frontmatter).
        content: Full markdown content of the skill.
        description: Short description (from frontmatter).
        args: Expected arguments (from frontmatter).
    """

    name: str
    content: str
    description: str = ""
    args: list[str] = field(default_factory=list)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from markdown.

    Returns:
        Tuple of (metadata dict, remaining content).
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 3:].strip()
    meta: dict[str, str] = {}
    for line in front.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body


class SkillRegistry:
    """Discovers skills from directories containing SKILL.md files.

    Skills are loaded lazily — only read from disk when accessed.
    """

    def __init__(self, dirs: list[Path]) -> None:
        self._dirs = dirs
        self._cache: dict[str, Skill] = {}
        self._discovered: dict[str, Path] | None = None

    def _discover(self) -> dict[str, Path]:
        """Walk skill directories and find SKILL.md files."""
        if self._discovered is not None:
            return self._discovered
        self._discovered = {}
        for skill_dir in self._dirs:
            if not skill_dir.is_dir():
                continue
            for skill_file in skill_dir.rglob("SKILL.md"):
                name = skill_file.parent.name
                self._discovered[name] = skill_file
        return self._discovered

    def get(self, name: str) -> Skill | None:
        """Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill if found, None otherwise.
        """
        if name in self._cache:
            return self._cache[name]
        paths = self._discover()
        path = paths.get(name)
        if path is None:
            return None
        text = path.read_text()
        meta, body = _parse_frontmatter(text)
        skill = Skill(
            name=meta.get("name", name),
            content=body,
            description=meta.get("description", ""),
            args=[a.strip() for a in meta.get("args", "").split(",") if a.strip()],
        )
        self._cache[name] = skill
        return skill

    @property
    def names(self) -> list[str]:
        """List all discovered skill names."""
        return sorted(self._discover().keys())
```

### `chimera/config/structured.py` (~40 lines):

```python
"""Structured JSON output with schema validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ValidationError(Exception):
    """Raised when JSON output fails schema validation."""


@dataclass
class StructuredOutput:
    """JSON schema wrapper for structured LLM output.

    Validates that the model's response is valid JSON matching the
    provided schema. Basic validation only (type checks, required fields).

    Args:
        name: Schema name for error messages.
        schema: JSON Schema dict describing expected output.
        max_retries: Number of retry attempts on validation failure.
    """

    name: str
    schema: dict[str, Any]
    max_retries: int = 3

    def validate(self, text: str) -> dict[str, Any]:
        """Parse and validate JSON text against the schema.

        Args:
            text: Raw text from the model.

        Returns:
            Parsed JSON object.

        Raises:
            ValidationError: If text is not valid JSON or fails schema checks.
        """
        # Extract JSON from markdown code blocks if present
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            # Remove first and last lines (``` markers)
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.strip() == "```" and in_block:
                    break
                if in_block:
                    json_lines.append(line)
            stripped = "\n".join(json_lines)

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON for {self.name}: {e}") from e

        # Basic schema validation: check required fields
        required = self.schema.get("required", [])
        if isinstance(data, dict):
            missing = [f for f in required if f not in data]
            if missing:
                raise ValidationError(
                    f"Missing required fields for {self.name}: {missing}"
                )

        return data

    def format_prompt_suffix(self) -> str:
        """Generate a prompt suffix instructing the model to use this schema."""
        schema_str = json.dumps(self.schema, indent=2)
        return (
            f"\n\nRespond with valid JSON matching this schema:\n"
            f"```json\n{schema_str}\n```\n"
            f"Do not include any text outside the JSON."
        )
```

### Changes to `chimera/__init__.py`:

Add: `from chimera.config import ProjectConfig, Skill, StructuredOutput`
Add to `__all__`: `"ProjectConfig"`, `"Skill"`, `"StructuredOutput"`

### Tests (`tests/test_config.py`, ~16 tests):

**Loader tests:**
1. `test_project_config_from_directory` — create dir with AGENTS.md, verify rules_text
2. `test_project_config_no_rules_files` — empty dir, rules_text is empty
3. `test_project_config_multiple_rules_files` — AGENTS.md + CLAUDE.md both loaded
4. `test_project_config_explicit_rules` — pass rules=["rule1"], verify in output
5. `test_file_config_source_missing` — missing file returns empty list

**Skills tests:**
6. `test_skill_registry_discover` — create skills/debug/SKILL.md, verify discovered
7. `test_skill_registry_get` — load skill content
8. `test_skill_frontmatter_parsed` — name/description/args from frontmatter
9. `test_skill_registry_names` — list all skill names
10. `test_skill_not_found` — unknown skill → None
11. `test_project_config_get_skill` — via ProjectConfig.get_skill()

**Structured output tests:**
12. `test_structured_output_validate_valid` — valid JSON passes
13. `test_structured_output_validate_invalid_json` — bad JSON → ValidationError
14. `test_structured_output_validate_missing_required` — missing field → ValidationError
15. `test_structured_output_extracts_from_code_block` — JSON in ```json``` block
16. `test_structured_output_format_prompt_suffix` — returns schema instruction

**Verification:** `python -m pytest tests/test_config.py -v`

---

## Task 4: MCP Client

**Files:**
- Create: `chimera/mcp/__init__.py`
- Create: `chimera/mcp/transport.py`
- Create: `chimera/mcp/client.py`
- Create: `chimera/mcp/tools.py`
- Modify: `chimera/__init__.py`
- Create: `tests/test_mcp.py`

### `chimera/mcp/__init__.py`:

```python
from chimera.mcp.client import MCPClient
from chimera.mcp.tools import MCPTool, MCPToolSource
from chimera.mcp.transport import HTTPTransport, MCPTransport, StdioTransport

__all__ = [
    "MCPClient",
    "MCPTool",
    "MCPToolSource",
    "MCPTransport",
    "StdioTransport",
    "HTTPTransport",
]
```

### `chimera/mcp/transport.py` (~100 lines):

```python
"""MCP transport implementations — stdio and HTTP."""
from __future__ import annotations

import json
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Any


class MCPTransport(ABC):
    """Abstract transport for MCP communication.

    Implements JSON-RPC 2.0 message exchange with an MCP server.
    """

    @abstractmethod
    def start(self) -> None:
        """Start the transport connection."""

    @abstractmethod
    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC message and return the response.

        Args:
            message: JSON-RPC request or notification.

        Returns:
            JSON-RPC response dict, or None for notifications.
        """

    @abstractmethod
    def close(self) -> None:
        """Close the transport connection."""

    def __enter__(self) -> MCPTransport:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class StdioTransport(MCPTransport):
    """Stdio transport — communicates via stdin/stdout of a subprocess.

    Uses Content-Length headers (same framing as LSP).
    """

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._process = subprocess.Popen(
            [self._command] + self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
        )

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self._write_message(message)
            if "id" in message:
                return self._read_message()
            return None

    def close(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    def _write_message(self, msg: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    def _read_message(self) -> dict[str, Any] | None:
        assert self._process is not None and self._process.stdout is not None
        # Read headers
        content_length = None
        while True:
            line = self._process.stdout.readline()
            if not line or line.strip() == b"":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
        if content_length is None:
            return None
        body = self._process.stdout.read(content_length)
        return json.loads(body)


class HTTPTransport(MCPTransport):
    """HTTP transport — communicates via POST requests to an MCP endpoint."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._session_id: str | None = None

    def start(self) -> None:
        pass  # No persistent connection needed

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        import urllib.request
        import urllib.error

        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        data = json.dumps(message).encode("utf-8")
        req = urllib.request.Request(self._url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Capture session ID if present
                session_id = resp.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                response_data = resp.read().decode("utf-8")
                if response_data:
                    return json.loads(response_data)
        except urllib.error.HTTPError as e:
            raise ConnectionError(f"MCP HTTP error {e.code}: {e.reason}") from e

        return None

    def close(self) -> None:
        self._session_id = None
```

### `chimera/mcp/client.py` (~80 lines):

```python
"""MCP client — manages server connections and discovers tools/resources."""
from __future__ import annotations

from typing import Any

from chimera.mcp.transport import MCPTransport, StdioTransport, HTTPTransport


class MCPClient:
    """Manages connections to one or more MCP servers.

    Discovers tools and resources from connected servers, wrapping
    them as Chimera BaseTool instances.

    Example:
        ```python
        client = MCPClient()
        client.add_stdio("fs", "npx", ["-y", "@modelcontextprotocol/server-filesystem"])
        client.connect_all()
        agent = Agent(tools=DEFAULT_TOOLS + client.tools)
        ```
    """

    def __init__(self) -> None:
        self._transports: dict[str, MCPTransport] = {}
        self._tool_defs: dict[str, list[dict[str, Any]]] = {}
        self._request_id = 0

    def add_stdio(self, name: str, command: str, args: list[str] | None = None,
                  env: dict[str, str] | None = None) -> None:
        """Register a stdio MCP server.

        Args:
            name: Unique server name.
            command: Command to start the server.
            args: Command arguments.
            env: Environment variables for the subprocess.
        """
        self._transports[name] = StdioTransport(command, args, env)

    def add_http(self, name: str, url: str, auth: str | None = None) -> None:
        """Register an HTTP MCP server.

        Args:
            name: Unique server name.
            url: MCP endpoint URL.
            auth: Bearer token for authentication.
        """
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        self._transports[name] = HTTPTransport(url, headers)

    def add_transport(self, name: str, transport: MCPTransport) -> None:
        """Register a custom transport.

        Args:
            name: Unique server name.
            transport: Transport instance.
        """
        self._transports[name] = transport

    def connect_all(self) -> None:
        """Start all transports and discover tools."""
        for name, transport in self._transports.items():
            transport.start()
            self._initialize(name, transport)
            self._discover_tools(name, transport)

    def disconnect_all(self) -> None:
        """Close all transport connections."""
        for transport in self._transports.values():
            transport.close()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _initialize(self, name: str, transport: MCPTransport) -> None:
        """Send initialize request to an MCP server."""
        response = transport.send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "chimera", "version": "0.1.0"},
            },
        })
        # Send initialized notification
        transport.send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

    def _discover_tools(self, name: str, transport: MCPTransport) -> None:
        """Discover tools from an MCP server."""
        response = transport.send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
        })
        if response and "result" in response:
            self._tool_defs[name] = response["result"].get("tools", [])

    @property
    def tools(self) -> list:
        """All discovered tools as BaseTool instances."""
        from chimera.mcp.tools import MCPTool
        result = []
        for name, defs in self._tool_defs.items():
            transport = self._transports[name]
            for tool_def in defs:
                result.append(MCPTool(
                    tool_def=tool_def,
                    transport=transport,
                    server_name=name,
                    client=self,
                ))
        return result

    def call_tool(self, transport: MCPTransport, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on an MCP server.

        Args:
            transport: Transport to the server.
            tool_name: Name of the tool to call.
            arguments: Tool arguments.

        Returns:
            Tool result dict.
        """
        response = transport.send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        if response and "result" in response:
            return response["result"]
        if response and "error" in response:
            return {"error": response["error"].get("message", "Unknown error")}
        return {"error": "No response from MCP server"}

    def __enter__(self) -> MCPClient:
        self.connect_all()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect_all()
```

### `chimera/mcp/tools.py` (~60 lines):

```python
"""MCP tool wrappers — wraps MCP tools as Chimera BaseTool instances."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.mcp.client import MCPClient
    from chimera.mcp.transport import MCPTransport, StdioTransport


class MCPTool(BaseTool):
    """Wraps an MCP tool definition as a Chimera BaseTool.

    Created automatically by MCPClient.tools — not typically
    instantiated directly.
    """

    def __init__(
        self,
        tool_def: dict[str, Any],
        transport: MCPTransport,
        server_name: str,
        client: MCPClient,
    ) -> None:
        self.name = tool_def.get("name", "unknown")
        self.description = tool_def.get("description", "")
        self.parameters = tool_def.get("inputSchema", {
            "type": "object", "properties": {},
        })
        self._transport = transport
        self._server_name = server_name
        self._client = client

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the MCP tool by calling the remote server.

        Args:
            args: Tool arguments.
            env: Execution environment (unused — MCP tools manage their own state).

        Returns:
            ToolResult with the server's response.
        """
        try:
            result = self._client.call_tool(self._transport, self.name, args)
        except Exception as e:
            return ToolResult(output="", error=f"MCP tool error: {e}")

        if "error" in result:
            return ToolResult(output="", error=result["error"])

        # MCP returns content as list of content blocks
        content = result.get("content", [])
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            output = "\n".join(text_parts)
        else:
            output = str(content)

        is_error = result.get("isError", False)
        return ToolResult(
            output=output,
            error=output if is_error else None,
            metadata={"mcp_server": self._server_name},
        )


class MCPToolSource:
    """Convenience wrapper for quickly connecting to an MCP server.

    Example:
        ```python
        tools = MCPToolSource.from_stdio("npx", ["-y", "@mcp/server-fs"])
        agent = Agent(tools=DEFAULT_TOOLS + tools)
        ```
    """

    @staticmethod
    def from_stdio(command: str, args: list[str] | None = None,
                   env: dict[str, str] | None = None) -> list[BaseTool]:
        """Connect to a stdio MCP server and return its tools.

        Args:
            command: Command to start the server.
            args: Command arguments.
            env: Environment variables.

        Returns:
            List of BaseTool instances wrapping the server's tools.
        """
        from chimera.mcp.client import MCPClient
        client = MCPClient()
        client.add_stdio("default", command, args, env)
        client.connect_all()
        return client.tools
```

### Changes to `chimera/__init__.py`:

Add: `from chimera.mcp import MCPClient, MCPToolSource`
Add to `__all__`: `"MCPClient"`, `"MCPToolSource"`

### Tests (`tests/test_mcp.py`, ~14 tests):

Tests use mock transports — no real MCP server needed.

```python
class MockTransport(MCPTransport):
    """Mock transport for testing."""
    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._sent: list[dict] = []
    def start(self): pass
    def send(self, message):
        self._sent.append(message)
        if self._responses:
            return self._responses.pop(0)
        return None
    def close(self): pass
```

Tests:
1. `test_stdio_transport_write_message` — verify Content-Length framing
2. `test_http_transport_session_id` — verify session ID tracking
3. `test_mcp_client_initialize` — sends initialize + initialized
4. `test_mcp_client_discover_tools` — parses tools/list response
5. `test_mcp_client_tools_property` — returns MCPTool instances
6. `test_mcp_client_call_tool` — sends tools/call, gets result
7. `test_mcp_tool_execute_success` — MCPTool returns ToolResult
8. `test_mcp_tool_execute_error` — MCP error → ToolResult.error
9. `test_mcp_tool_schema` — name/description/parameters from tool_def
10. `test_mcp_tool_content_blocks` — parses list of content blocks
11. `test_mcp_client_add_http` — registers HTTPTransport
12. `test_mcp_client_add_custom_transport` — registers MockTransport
13. `test_mcp_client_context_manager` — __enter__/__exit__ lifecycle
14. `test_mcp_tool_source_from_stdio` — convenience wrapper (mock transport)

**Verification:** `python -m pytest tests/test_mcp.py -v`

---

## Task 5: LSP Rewrite

**Files:**
- Replace: `chimera/lsp/__init__.py`
- Replace: `chimera/lsp/base.py`
- Replace: `chimera/lsp/subprocess.py` → rename to `chimera/lsp/session.py`
- Create: `chimera/lsp/manager.py`
- Create: `chimera/lsp/tool.py`
- Create: `chimera/lsp/servers.py`
- Modify: `chimera/core/loop_config.py` — add `lsp` field
- Modify: `chimera/__init__.py` — update LSP exports
- Create: `tests/test_lsp_rewrite.py`

### `chimera/lsp/__init__.py`:

```python
from chimera.lsp.base import Diagnostic, Severity
from chimera.lsp.manager import LSPManager
from chimera.lsp.session import LSPSession
from chimera.lsp.tool import LSPTool
from chimera.lsp.servers import LanguageServerConfig, BUILTIN_SERVERS

__all__ = [
    "Diagnostic",
    "LanguageServerConfig",
    "LSPManager",
    "LSPSession",
    "LSPTool",
    "Severity",
    "BUILTIN_SERVERS",
]
```

### `chimera/lsp/base.py` — keep existing Diagnostic/Severity, remove LSPClient ABC:

Keep `Severity` and `Diagnostic` exactly as they are. Remove the `LSPClient` ABC (it will be replaced by `LSPManager`).

### `chimera/lsp/servers.py` (~30 lines):

```python
"""Built-in language server configurations."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LanguageServerConfig:
    """Configuration for a language server."""
    name: str
    command: list[str]
    extensions: tuple[str, ...]
    initialization_options: dict = field(default_factory=dict)


BUILTIN_SERVERS: list[LanguageServerConfig] = [
    LanguageServerConfig("python", ["pyright-langserver", "--stdio"], (".py",)),
    LanguageServerConfig("typescript", ["typescript-language-server", "--stdio"], (".ts", ".tsx", ".js", ".jsx")),
    LanguageServerConfig("go", ["gopls", "serve"], (".go",)),
    LanguageServerConfig("rust", ["rust-analyzer"], (".rs",)),
]
```

### `chimera/lsp/session.py` — rewrite from subprocess.py (~90 lines):

Refactor `SubprocessLSPClient` into `LSPSession` — a single language server connection that supports all core LSP methods (not just diagnostics).

```python
"""LSP session — single language server connection over stdio."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from chimera.lsp.base import Diagnostic, Severity


class LSPSession:
    """A single LSP server connection over stdin/stdout.

    Handles JSON-RPC communication with Content-Length framing.
    Supports: initialize, didOpen, didChange, didSave,
    definition, references, hover, documentSymbol.
    """

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False

    def start(self, root_path: str) -> None:
        """Start the language server and initialize it."""
        self._process = subprocess.Popen(
            self._command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._send_request("initialize", {
            "processId": None,
            "rootUri": Path(root_path).as_uri(),
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False},
                    "documentSymbol": {"dynamicRegistration": False},
                },
            },
        })
        self._send_notification("initialized", {})
        self._initialized = True

    def stop(self) -> None:
        """Shut down the language server."""
        if self._process is not None:
            try:
                self._send_request("shutdown", None)
                self._send_notification("exit", None)
            except (BrokenPipeError, OSError):
                pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            self._initialized = False

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Notify server that a file was opened."""
        self._send_notification("textDocument/didOpen", {
            "textDocument": {"uri": uri, "languageId": language_id, "version": 1, "text": text},
        })

    def did_change(self, uri: str, text: str, version: int = 2) -> None:
        """Notify server of file content change."""
        self._send_notification("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}],
        })

    def definition(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Go to definition."""
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return []
        locations = result.get("result", [])
        if isinstance(locations, dict):
            return [locations]
        return locations or []

    def references(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Find all references."""
        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        })
        if result is None:
            return []
        return result.get("result", []) or []

    def hover(self, uri: str, line: int, character: int) -> str | None:
        """Get hover information."""
        result = self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return None
        hover_result = result.get("result")
        if hover_result is None:
            return None
        contents = hover_result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", str(contents))
        if isinstance(contents, list):
            return "\n".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
        return str(contents)

    def document_symbols(self, uri: str) -> list[dict[str, Any]]:
        """Get document symbols."""
        result = self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        if result is None:
            return []
        return result.get("result", []) or []

    # ---- JSON-RPC helpers ----

    def _send_request(self, method: str, params: Any) -> dict[str, Any] | None:
        with self._lock:
            self._request_id += 1
            msg = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
            self._write(msg)
            return self._read()

    def _send_notification(self, method: str, params: Any) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write(msg)

    def _write(self, msg: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    def _read(self) -> dict[str, Any] | None:
        assert self._process is not None and self._process.stdout is not None
        content_length = None
        while True:
            line = self._process.stdout.readline()
            if not line or line.strip() == b"":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
        if content_length is None:
            return None
        body = self._process.stdout.read(content_length)
        return json.loads(body)
```

### `chimera/lsp/manager.py` (~80 lines):

```python
"""LSP manager — manages multiple language server sessions."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from chimera.lsp.base import Diagnostic, Severity
from chimera.lsp.servers import BUILTIN_SERVERS, LanguageServerConfig
from chimera.lsp.session import LSPSession


class LSPManager:
    """Manages language server lifecycles and routes requests by file extension.

    Example:
        ```python
        lsp = LSPManager.for_project("./myapp")
        lsp.start("./myapp")
        diagnostics = lsp.get_diagnostics("src/main.py")
        lsp.stop()
        ```
    """

    def __init__(self) -> None:
        self._configs: dict[str, LanguageServerConfig] = {}
        self._sessions: dict[str, LSPSession] = {}
        self._ext_map: dict[str, str] = {}  # extension → config name

    @classmethod
    def for_project(cls, path: str) -> LSPManager:
        """Auto-detect languages and configure servers.

        Only adds servers whose commands are available on PATH.

        Args:
            path: Project root directory.

        Returns:
            Configured LSPManager (not yet started).
        """
        manager = cls()
        for config in BUILTIN_SERVERS:
            cmd = config.command[0]
            if shutil.which(cmd) is not None:
                manager.add(config.name, config.command, config.extensions)
        return manager

    def add(self, name: str, command: list[str] | str,
            extensions: tuple[str, ...] | None = None) -> None:
        """Register a language server.

        Args:
            name: Server name (e.g. "python").
            command: Command to start the server (string or list).
            extensions: File extensions this server handles.
        """
        if isinstance(command, str):
            command = command.split()
        config = LanguageServerConfig(name=name, command=command, extensions=extensions or ())
        self._configs[name] = config
        for ext in config.extensions:
            self._ext_map[ext] = name

    def start(self, workdir: str) -> None:
        """Start all configured language servers.

        Args:
            workdir: Project root path.
        """
        for name, config in self._configs.items():
            session = LSPSession(config.command)
            try:
                session.start(workdir)
                self._sessions[name] = session
            except (FileNotFoundError, OSError):
                pass  # Server not available, skip silently

    def stop(self) -> None:
        """Stop all running language servers."""
        for session in self._sessions.values():
            session.stop()
        self._sessions.clear()

    def get_session(self, file_path: str) -> LSPSession | None:
        """Get the LSP session for a file based on its extension.

        Args:
            file_path: File path to look up.

        Returns:
            LSPSession if a server handles this file type, None otherwise.
        """
        ext = Path(file_path).suffix
        name = self._ext_map.get(ext)
        if name is None:
            return None
        return self._sessions.get(name)

    def get_diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Get diagnostics for a file.

        Args:
            file_path: Path to the file.

        Returns:
            List of Diagnostic objects.
        """
        session = self.get_session(file_path)
        if session is None:
            return []
        # Open the file and wait for diagnostics
        path = Path(file_path)
        uri = path.resolve().as_uri()
        lang_id = self._detect_language(path.suffix)
        try:
            text = path.read_text()
        except (FileNotFoundError, OSError):
            return []
        session.did_open(uri, lang_id, text)
        # Note: real diagnostics come asynchronously via publishDiagnostics
        # For now, return empty — full async support in a future pass
        return []

    def _detect_language(self, ext: str) -> str:
        """Map file extension to LSP language ID."""
        mapping = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescriptreact",
            ".js": "javascript", ".jsx": "javascriptreact",
            ".go": "go", ".rs": "rust",
        }
        return mapping.get(ext, "plaintext")

    def __enter__(self) -> LSPManager:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
```

### `chimera/lsp/tool.py` (~70 lines):

```python
"""LSP tool — exposes language server queries as a Chimera BaseTool."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.lsp.manager import LSPManager


class LSPTool(BaseTool):
    """Tool that exposes LSP code intelligence queries to the agent.

    Supports: go_to_definition, find_references, hover, document_symbols.

    Args:
        lsp: An LSPManager instance with running sessions.
    """

    name = "lsp"
    description = (
        "Query language servers for code intelligence. Actions: "
        "go_to_definition, find_references, hover, document_symbols."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["go_to_definition", "find_references", "hover", "document_symbols"],
                "description": "The LSP query to perform.",
            },
            "file": {"type": "string", "description": "File path to query."},
            "line": {"type": "integer", "description": "0-indexed line number (for definition/references/hover)."},
            "character": {"type": "integer", "description": "0-indexed character offset (for definition/references/hover)."},
        },
        "required": ["action", "file"],
    }

    def __init__(self, lsp: LSPManager) -> None:
        self._lsp = lsp

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        action = args["action"]
        file_path = args["file"]
        line = args.get("line", 0)
        character = args.get("character", 0)

        session = self._lsp.get_session(file_path)
        if session is None:
            return ToolResult(output="", error=f"No language server for {file_path}")

        uri = Path(file_path).resolve().as_uri()

        try:
            if action == "go_to_definition":
                locations = session.definition(uri, line, character)
                if not locations:
                    return ToolResult(output="No definition found")
                output = "\n".join(
                    f"{loc.get('uri', '?')}:{loc.get('range', {}).get('start', {}).get('line', 0)}"
                    for loc in locations
                )
                return ToolResult(output=output)

            elif action == "find_references":
                refs = session.references(uri, line, character)
                if not refs:
                    return ToolResult(output="No references found")
                output = "\n".join(
                    f"{ref.get('uri', '?')}:{ref.get('range', {}).get('start', {}).get('line', 0)}"
                    for ref in refs
                )
                return ToolResult(output=f"{len(refs)} references:\n{output}")

            elif action == "hover":
                info = session.hover(uri, line, character)
                return ToolResult(output=info or "No hover information")

            elif action == "document_symbols":
                symbols = session.document_symbols(uri)
                if not symbols:
                    return ToolResult(output="No symbols found")
                lines = []
                for sym in symbols:
                    name = sym.get("name", "?")
                    kind = sym.get("kind", 0)
                    line_num = sym.get("range", {}).get("start", {}).get("line", 0)
                    lines.append(f"  {name} (kind={kind}) at line {line_num}")
                return ToolResult(output=f"{len(symbols)} symbols:\n" + "\n".join(lines))

            else:
                return ToolResult(output="", error=f"Unknown action: {action}")
        except Exception as e:
            return ToolResult(output="", error=f"LSP error: {e}")
```

### Changes to `chimera/core/loop_config.py`:

Add `lsp` field:

```python
if TYPE_CHECKING:
    from chimera.lsp.manager import LSPManager
    # ... existing imports ...

@dataclass
class LoopConfig:
    # ... existing fields ...
    lsp: LSPManager | None = None
```

### Changes to `chimera/__init__.py`:

Update LSP imports:
```python
# Was:
from chimera.lsp import Diagnostic, LSPClient, Severity
# Now:
from chimera.lsp import Diagnostic, LSPManager, LSPTool, Severity
```

Update `__all__`: replace `"LSPClient"` with `"LSPManager"`, `"LSPTool"`

### Tests (`tests/test_lsp_rewrite.py`, ~14 tests):

Tests mock the subprocess — no real language server needed.

**Session tests (mock process):**
1. `test_lsp_session_write_message_framing` — Content-Length header correct
2. `test_lsp_session_definition` — sends request, parses locations
3. `test_lsp_session_references` — sends request, parses list
4. `test_lsp_session_hover` — sends request, parses contents
5. `test_lsp_session_document_symbols` — sends request, parses symbols

**Manager tests:**
6. `test_lsp_manager_add` — registers server config
7. `test_lsp_manager_ext_routing` — .py → python session, .ts → typescript session
8. `test_lsp_manager_get_session_unknown_ext` — returns None
9. `test_lsp_manager_for_project` — auto-detects available servers

**Tool tests:**
10. `test_lsp_tool_go_to_definition` — returns formatted locations
11. `test_lsp_tool_find_references` — returns reference list
12. `test_lsp_tool_hover` — returns hover info
13. `test_lsp_tool_document_symbols` — returns symbol list
14. `test_lsp_tool_no_server` — unknown extension → error

**LoopConfig:**
15. `test_loop_config_lsp_field` — LoopConfig(lsp=manager) accepted

**Backward compat:**
16. `test_diagnostic_and_severity_still_exported` — existing imports work

**Verification:** `python -m pytest tests/test_lsp_rewrite.py tests/test_lsp.py -v`

---

## Implementation Order

All 5 tasks are independent. Recommended order by complexity:

```
Task 1: Fuzzy Edit Strategies      (~80 lines new)
Task 2: Provider Catalog           (~120 lines new)
Task 3: Project Config             (~180 lines new, 3 files)
Task 4: MCP Client                 (~240 lines new, 3 files)
Task 5: LSP Rewrite                (~250 lines new, 4 files)
```

---

## Verification

After all tasks:
1. `python -m pytest tests/ -x -q` — all 978+ existing tests still pass
2. `python -c "from chimera import MCPClient, MCPToolSource, LSPManager, LSPTool, ProjectConfig, StructuredOutput, ProviderCatalog, ModelConfig"` — new exports work
3. `python -m pytest tests/test_edit_strategies.py tests/test_provider_catalog.py tests/test_config.py tests/test_mcp.py tests/test_lsp_rewrite.py -v` — all new tests pass
