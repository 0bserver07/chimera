---
title: "Module Integration Checklist"
description: "Module Integration Checklist"
---

Every time a new module, tool, strategy, or feature is added to Chimera, go through this checklist before calling it done. This prevents the pattern of "code exists but isn't integrated."

---

## 1. Implementation

- [ ] Source file created in the correct package
- [ ] Unit tests in `tests/test_<module>.py`
- [ ] Tests pass: `uv run pytest tests/test_<module>.py -v`

## 2. Integration

- [ ] Exported from package `__init__.py` (e.g., `chimera/training/__init__.py`)
- [ ] Exported from top-level `chimera/__init__.py` + added to `__all__`
- [ ] If it's a tool: added to `AGENT_TOOLS` or documented why not
- [ ] If it's a strategy: added to `chimera/training/strategies/__init__.py`
- [ ] If it needs LoopConfig: field added to `chimera/core/loop_config.py`
- [ ] If it needs Agent wiring: handled in `Agent.run()` / `iter_steps()` / `async_run()`

## 3. Verification with real LLM

- [ ] Write an example in `examples/` that exercises the feature end-to-end
- [ ] Run the example with `source .env && uv run python examples/<example>.py`
- [ ] Verify it produces correct output, not just "no errors"
- [ ] Add test in `tests/test_examples.py` (real provider when available, mock fallback)
- [ ] Update `examples/run_all.py` EXAMPLES list

## 4. Documentation

- [ ] Module page in `docs/modules/<module>.md` with working code examples
- [ ] Added to `mkdocs.yml` nav
- [ ] CLAUDE.md module map updated if it's a new package/directory
- [ ] README.md feature list updated (additive only — don't rewrite existing content)
- [ ] Existing docs checked for stale references (tool counts, strategy counts, etc.)

## 5. Final verification

- [ ] Full test suite passes: `uv run pytest tests/ -q`
- [ ] No regressions in existing tests
- [ ] `uv run mkdocs build` succeeds (if docs were changed)
- [ ] Git commit with descriptive message
- [ ] GitHub issue closed with commit reference (if applicable)

---

## What NOT to do

- Don't claim a feature is "done" if it only has unit tests with mocks
- Don't rewrite existing README/doc content — only add
- Don't add tools to DEFAULT_TOOLS without discussion (use AGENT_TOOLS or document why it's opt-in)
- Don't amend previous commits — make new ones
- Don't skip the real LLM verification step — that's where the real bugs are
