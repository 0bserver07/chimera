# HumanEval — GLM-5.1 Report

**Date:** 2026-03-30
**Model:** GLM-5.1 via api.z.ai (Anthropic-compatible endpoint)
**Result:** 109/164 passed (66.5% pass@1)
**Raw data:** `data/humaneval-glm51-results.json`

## What HumanEval Measures

HumanEval is a function completion benchmark. Given a function signature and docstring, the model outputs a complete Python function. The output is combined with hand-written test cases and executed. Pass if no exceptions.

This is an **LLM benchmark**, not an agent benchmark. There is no tool use, no file system, no iteration. One LLM call per problem.

## Exact Setup

### Environment

```bash
# .env file
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_API_KEY="<key>"
export ANTHROPIC_MODEL="glm-5.1"
```

### Dataset

164 problems from the canonical OpenAI HumanEval dataset:

```bash
python3 -c "
import urllib.request, json, gzip
resp = urllib.request.urlopen('https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz')
tasks = [json.loads(l) for l in gzip.decompress(resp.read()).decode().strip().split('\n')]
json.dump([{
    'id': t['task_id'],
    'prompt': t['prompt'],
    'test': t.get('test', ''),
    'entry_point': t.get('entry_point', ''),
} for t in tasks], open('data/humaneval.json', 'w'))
"
```

### Method

For each of 164 problems:

1. Send one LLM call: system prompt + function signature/docstring
2. Strip markdown fences from output (`\`\`\`python ... \`\`\``)
3. Prepend standard imports: `from typing import *`, `from collections import *`, `import math`, etc.
4. Concatenate: imports + model output + HumanEval test code + `check(entry_point)`
5. `exec()` the combined code
6. Pass if no exception is raised

### Parameters

| Parameter | Value | Justification |
|-----------|-------|---------------|
| `max_tokens` | 1024 | Enough for any HumanEval function |
| `temperature` | 0.0 | Deterministic — pass@1 uses greedy decoding |
| Attempts per problem | 1 | pass@1 metric = single attempt |
| System prompt | "You are a code completion assistant. Given a function signature and docstring, output ONLY the complete Python function. No markdown fences, no explanation, no extra imports." | Standard for code completion benchmarks |
| Agent loop | **None** | Direct `provider.complete()` — one shot |
| Tools | **None** | Pure LLM code generation |
| Retries on API error | **None** | API errors counted as failures |

### Runner Script

The exact script used is embedded in the session history. The core loop:

```python
from chimera.providers.factory import create_provider
from chimera.types import Message

provider = create_provider(model="glm-5.1")
tasks = json.load(open("data/humaneval.json"))

TYPING_IMPORTS = "from typing import *\nfrom collections import *\nimport math\n..."

for task in tasks:
    messages = [
        Message.system("...output ONLY the complete Python function..."),
        Message.user(f"Complete this function:\n\n{task['prompt']}"),
    ]
    response = provider.complete(messages, max_tokens=1024)
    code = response.content.strip()
    # Strip markdown fences
    if "```python" in code:
        code = code.split("```python", 1)[1].split("```", 1)[0]
    # Build and exec
    full = f"{TYPING_IMPORTS}\n{code}\n\n{task['test']}\n\ncheck({task['entry_point']})\n"
    exec(full, {})
```

## Results

**109/164 passed (66.5% pass@1)**

### Failure Breakdown (55 failures)

| Category | Count | % of failures | Description |
|----------|-------|---------------|-------------|
| API errors | 14 | 25% | `Error code: 400` — network/rate limit failures. The model never got a chance to answer. |
| Indent errors | 14 | 25% | Model wraps output in markdown fences. Stripping leaves orphaned indentation. |
| Unterminated strings | 8 | 15% | Model output includes `"""` docstrings that conflict with `exec()` parsing. |
| Syntax errors | 7 | 13% | Various syntax issues in model output. |
| Name not defined | 3 | 5% | Missing imports not covered by our `from typing import *` preamble. |
| Other (unicode, return outside func) | 8 | 15% | Edge cases: unicode chars like `➞`, `return` outside function body. |
| **Wrong answer** | **1** | **2%** | Model got the logic wrong. Only 1 out of 55 failures. |

### What This Means

The 66.5% score is **not a measure of GLM-5.1's code generation ability**. It's a measure of our harness quality. Evidence:

- Only 1/55 failures is a logic error
- 14/55 are API errors (the model never ran)
- 22/55 are output formatting issues (fences, indentation)
- Removing API errors alone: 109/150 = **72.7%**
- Removing API errors + formatting issues: ~130/150 = **~87%** (estimated)

### Known Issues in This Run

1. **No API error retries.** 14 problems failed because the API returned 400. A retry loop would recover these.
2. **Brittle fence stripping.** The `split("```python")` approach fails when the model outputs nested fences or fence-like content inside docstrings.
3. **Incomplete import preamble.** We add `from typing import *` but some problems need `from functools import reduce` or other specific imports.
4. **No output sanitization.** Model sometimes includes explanation text before/after the function. We strip fences but not prose.
5. **`exec()` is fragile.** Any syntax error in the combined string fails the entire problem. A sandboxed execution with better error handling would help.

### Comparison with GLM-5.0

| | GLM-5.0 | GLM-5.1 |
|---|---------|---------|
| Score | 90.9% | 66.5% |
| Run date | 2026-03-20 | 2026-03-30 |
| Harness | Different script (earlier version) | Current script |
| API errors | Unknown | 14 |
| Model output format | Unknown | More likely to use markdown fences |

The 24-point drop may reflect: (a) a different harness with better parsing, (b) GLM-5.0 being more obedient about "no markdown," (c) fewer API errors, or (d) actual model regression. We cannot distinguish without re-running GLM-5.0 with the same harness.

## Improvements to Make Before Next Run

| Fix | Expected impact | Effort |
|-----|-----------------|--------|
| Retry on API error (3 attempts) | +14 problems recovered → ~82% | Low |
| Better output parsing (regex-based fence/prose stripping) | +10-15 problems → ~87% | Low |
| Expanded import preamble | +3 problems | Trivial |
| Subprocess-based execution instead of `exec()` | Better error isolation | Medium |
| Use agent loop with self-repair (try, fail, fix, retry) | Potentially +15 problems → ~92%+ | Medium |

## Reproduction

```bash
# 1. Set up environment
source .env  # ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL=glm-5.1

# 2. Download dataset (one time)
python3 -c "
import urllib.request, json, gzip
resp = urllib.request.urlopen('https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz')
tasks = [json.loads(l) for l in gzip.decompress(resp.read()).decode().strip().split('\n')]
json.dump([{'id':t['task_id'],'prompt':t['prompt'],'test':t.get('test',''),'entry_point':t.get('entry_point','')} for t in tasks], open('data/humaneval.json','w'))
"

# 3. Run benchmark
# (Script is in session history — to be extracted into examples/humaneval_glm51.py)

# 4. Results saved to data/humaneval-glm51-results.json
```
