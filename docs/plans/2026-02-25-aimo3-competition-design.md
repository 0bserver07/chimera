# AIMO3 Competition Module — Design Document

**Date:** 2026-02-25
**Goal:** Compete seriously in Kaggle AIMO Progress Prize 3 using Chimera as the framework.

## Context

The AI Mathematical Olympiad Progress Prize 3 (AIMO3) is a $2.2M Kaggle competition requiring AI systems to solve 110 original olympiad-level math problems (algebra, combinatorics, geometry, number theory). Problems range from national olympiad to IMO difficulty. Answers are 5-digit integers.

**Constraints:**
- Kaggle notebook submission, no internet access
- H100 GPU, 5-hour runtime limit
- Open-source requirement for prize eligibility
- Problems are LaTeX text; answers are integers

**Winning pattern (from AIMO1/AIMO2):**
1. Strong open-weight math model (Qwen3, DeepSeek-R1)
2. Tool-augmented generation (Python/SymPy code execution)
3. Majority voting / pass@N sampling
4. Per-problem time budget management

Chimera's architecture (Provider + Agent + Strategy + Benchmark) maps directly to this pattern.

## Approach

**Approach A: AIMO Benchmark Module** — Build AIMO3 as first-class Chimera blocks. New provider (Modal), new strategy (MajorityVoting), new benchmark (AIMOBenchmark), new tool (VerifyTool). All reusable framework components.

## Design

### 1. Providers

#### ModalProvider (new)

Wraps Modal's serverless GPU inference. Deploys a vLLM container on Modal H100s, calls it via OpenAI-compatible endpoint.

```python
class ModalProvider(Provider):
    def __init__(
        self,
        model: str,
        gpu: str = "H100",
        token_id: str | None = None,     # or MODAL_TOKEN_ID env var
        token_secret: str | None = None,  # or MODAL_TOKEN_SECRET env var
        context_length: int = 131_072,
    ) -> None: ...
```

Under the hood: deploys a Modal `@app.cls` running vLLM, then delegates to `OpenAICompatibleProvider` for the actual `/v1/chat/completions` calls. The Modal layer handles GPU provisioning; the inference protocol is standard OpenAI-compatible.

#### HuggingFace (no new class needed)

HF Inference API exposes OpenAI-compatible endpoints. Use existing `OpenAICompatibleProvider`:

```python
provider = create_provider(
    provider_type="compatible",
    model="Qwen/Qwen3-235B",
    base_url="https://api-inference.huggingface.co/v1",
    api_key="hf_...",
)
```

#### vLLM local (no new class needed)

Already works via `OpenAICompatibleProvider`:

```python
provider = create_provider(
    provider_type="compatible",
    model="Qwen/Qwen3-235B-AWQ",
    base_url="http://localhost:8000",
)
```

#### Factory updates

Add `"modal"` to `create_provider()` factory and `_infer_provider()`.

### 2. AIMOBenchmark

Implements Chimera's `Benchmark` ABC to load and evaluate AIMO3 problems.

```python
class AIMOBenchmark(Benchmark):
    def __init__(self, problems_path: str | None = None):
        """Load problems from local JSON or Kaggle competition API."""
        self._problems = ...

    def name(self) -> str:
        return "aimo3"

    def tasks(self) -> list[dict[str, Any]]:
        """Returns list of {"id": "p1", "prompt": "<formatted problem>", "answer": 12345}"""
        ...

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        """Extract integer from agent_output, compare to task["answer"]."""
        ...
```

**Problem format:** Each task dict contains:
- `id`: problem identifier
- `prompt`: LaTeX problem text, formatted with instructions for the agent
- `answer`: 5-digit integer (hidden during competition eval, known for public set)

**Evaluation:** Extract the last integer from the agent's output, compare to ground truth.

### 3. VerifyTool

A tool the agent can call to cross-check its candidate answer by running verification code.

```python
class VerifyTool(BaseTool):
    name = "verify_answer"
    description = "Run verification code to cross-check a candidate answer."
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code that prints True/False"},
        },
        "required": ["code"],
    }

    def execute(self, args: dict, env: Environment | None) -> ToolResult:
        # Execute the verification code in a subprocess
        # Return whether it printed True + any output
        ...
```

This lets the agent write separate verification logic (e.g., plug the answer back into the original equation, check boundary conditions, verify via alternative method).

### 4. MajorityVoting Strategy

Core competitive mechanism: sample N solutions, execute each, take consensus answer.

```python
class MajorityVoting(Strategy):
    def __init__(
        self,
        n_samples: int = 16,         # solution attempts per problem
        temperature: float = 0.7,     # diversity in reasoning paths
        time_budget: float = 300.0,   # seconds per problem
        min_agreement: int = 2,       # minimum votes for consensus
    ) -> None: ...

    def run(self, agent, spec, env, constraints=None, callbacks=None) -> SynthesisResult:
        # For each sample:
        #   1. agent.run(problem_prompt, env) with temperature
        #   2. Extract integer answer from output
        #   3. Collect (answer, confidence) pairs
        # After N samples (or time_budget expires):
        #   4. Count votes per distinct answer
        #   5. Return most common answer if votes >= min_agreement
        #   6. Otherwise mark as unsolved
        # Early stopping: if one answer reaches majority, stop sampling
```

**Key details:**
- Samples are sequential (single GPU)
- Higher temperature ensures diverse reasoning paths
- Time budget per problem with dynamic reallocation
- Early stopping when consensus reached

### 5. AIMOEnsemble Strategy

Combines MajorityVoting (fast, handles most problems) with TreeSearch (deeper exploration for hard problems).

```python
class AIMOEnsemble(Strategy):
    def __init__(
        self,
        voting_samples: int = 8,
        tree_branch_factor: int = 3,
        tree_max_depth: int = 5,
        time_budget: float = 360.0,
    ) -> None: ...

    def run(self, agent, spec, env, constraints=None, callbacks=None):
        # Phase 1: MajorityVoting (fast)
        result = MajorityVoting(n_samples=self.voting_samples).run(...)
        if result.converged:
            return result
        # Phase 2: TreeSearch fallback (deeper)
        return TreeSearch(
            branch_factor=self.tree_branch_factor,
            max_depth=self.tree_max_depth,
        ).run(...)
```

**Time budget allocation** for 50 problems in 5 hours (18,000s):
- Easy problems (quick consensus): ~60s each
- Hard problems (TreeSearch fallback): ~600s each
- Dynamic reallocation via callback monitoring remaining time

### 6. Agent Prompt

The system prompt instructs the agent to:
1. Read the math problem carefully
2. Reason step-by-step about the approach
3. Write Python code to compute the answer (using sympy, numpy, scipy, itertools, etc.)
4. Execute the code via bash tool
5. Optionally verify the answer using the verify tool
6. Return the final 5-digit integer

Prompt lives in `chimera/eval/benchmarks/aimo_prompts.py` — iterable without touching framework code.

### 7. Kaggle Notebook

```python
# Cell 1: Setup
# Install chimera, start vLLM with pre-bundled model weights

# Cell 2: Configure
import chimera
from chimera.eval.benchmarks.aimo import AIMOBenchmark, AIMOEnsemble, VerifyTool

provider = chimera.create_provider(
    provider_type="compatible",
    model="Qwen/Qwen3-235B-AWQ",
    base_url="http://localhost:8000",
)

agent = chimera.Agent(
    provider=provider,
    tools=[chimera.tools.bash, chimera.tools.read_file,
           chimera.tools.write_file, VerifyTool()],
    loop=chimera.ReAct(max_steps=30),
    prompt=AIMO_SYSTEM_PROMPT,
)

# Cell 3: Solve
benchmark = AIMOBenchmark(problems_path="/kaggle/input/aimo3/")
strategy = AIMOEnsemble(voting_samples=8, time_budget=360.0)
harness = chimera.Harness(benchmark=benchmark, agent=agent)
result = harness.run()

# Cell 4: Submit
write_submission_csv(result)
```

### 8. Development Workflow

1. **Prompt iteration:** Use Anthropic provider (Claude) for fast feedback
2. **Model testing:** Use Modal provider for remote H100 inference with open-weight models
3. **Local eval:** Run AIMOBenchmark with public test problems
4. **Kaggle submission:** Switch to vLLM + OpenAICompatibleProvider in the notebook

## File Layout

```
chimera/
├── providers/
│   ├── modal.py              # NEW: Modal serverless provider
│   └── ...
├── training/strategies/
│   ├── majority_voting.py    # NEW: pass@N with consensus
│   └── ...
├── tools/
│   ├── verify.py             # NEW: answer verification tool
│   └── ...
├── eval/
│   ├── benchmarks/
│   │   ├── __init__.py
│   │   ├── aimo.py           # NEW: AIMOBenchmark class
│   │   └── aimo_prompts.py   # NEW: system prompts
│   └── ...
└── notebooks/
    └── aimo3/
        ├── notebook.py        # Kaggle submission notebook
        └── README.md          # Setup instructions
```

## New Dependencies

- `modal` (optional, for ModalProvider)
- `httpx` (already optional dep, used by compatible/ollama providers)

No new required dependencies. Modal is optional — only needed for dev workflow.

## Success Criteria

1. All new components have tests (provider, benchmark, strategy, tool)
2. Can run AIMOBenchmark end-to-end locally with mock provider
3. Can run against real math problems using Modal/HF with open-weight model
4. Kaggle notebook runs within 5-hour GPU limit
5. Competitive accuracy on public leaderboard
