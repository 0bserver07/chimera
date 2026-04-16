# Function Synthesis

Compile natural-language specs into callable neural artifacts (`.chi` bundles),
run them locally, and expose them as agent tools.

## Install

```bash
pip install 'chimera[function_synthesis]'
```

Pulls in `llama-cpp-python` (runtime) and `huggingface_hub` (base model cache).

## 60-second quickstart

```python
from chimera.function_synthesis import FunctionSpec
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.registry import ProgramRegistry

spec = FunctionSpec(
    name="sentiment",
    description="Classify the text as 'positive' or 'negative'.",
)
bundle = MockCompiler().compile(spec)                     # no network, no training
slug = ProgramRegistry.default().install(spec=spec, bundle=bundle)
print(slug)   # -> "sentiment-ab12cd34"
```

## CLI

```bash
# compile a spec file into a bundle + install it
echo '{"name":"sentiment","description":"classify pos/neg"}' > spec.json
chimera fs compile spec.json --compiler mock

# list installed programs
chimera fs list

# invoke one (requires a base GGUF + llama-cpp-python)
chimera fs run sentiment-ab12cd34 "I loved it!" \
    --base-model ~/.chimera/function_synthesis/models/.../model.gguf

# inspect / remove
chimera fs info sentiment-ab12cd34
chimera fs rm sentiment-ab12cd34
```

## Using a real compile service

Point `RemoteCompiler` at any endpoint implementing the
[compile protocol](./function-synthesis-compile-protocol.md):

```python
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.strategies.synthesis import FunctionSynthesisStrategy

compiler = RemoteCompiler(endpoint="https://your-service/compile", api_key="...")
strategy = FunctionSynthesisStrategy(
    compiler=compiler,
    output_dir="./bundles",
)
result = strategy.run(spec)
print(result.bundle_path)
```

## Agent tool

Any loaded `CompiledFunction` can be exposed to a Chimera agent:

```python
from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
from chimera.function_synthesis.runtime import CompiledFunction
from chimera.tools.compiled_function_tool import CompiledFunctionTool

backend = LlamaCppBackend(base_model_path="...")
fn = CompiledFunction.from_path("sentiment-ab12cd34.chi", backend=backend)
tool = CompiledFunctionTool(fn)

# add `tool` to your agent's tool list --- agents can now call the compiled
# function just like any other tool.
```

## Cache layout

```
$CHIMERA_FS_HOME/  (default: ~/.chimera/function_synthesis/)
  models/       # base GGUF files, downloaded on first use
  bundles/      # installed .chi bundles (one per slug)
  index.json    # slug -> bundle path + metadata
  prefix/       # cold-start state cache (see PrefixCache)
```

Set `CHIMERA_FS_HOME` to override. Set `CHIMERA_FS_OFFLINE=1` to refuse any
network call --- cache misses raise `OfflineError` instead of downloading.

## `.chi` bundle format

A `.chi` file is a ZIP archive containing:

| Member           | Contents                                                    |
|------------------|-------------------------------------------------------------|
| `manifest.json`  | Schema version, name, base model ID, adapter format         |
| `adapter.gguf`   | LoRA adapter (Q4_0 by default)                              |
| `prompts.json`   | `{"system": str, "user_template": str, "stop": [str]}`      |
| `spec.json`      | Serialized `FunctionSpec`                                   |
| `metadata.json`  | Compiler backend info, base model hash, free-form fields    |

See `chimera/function_synthesis/bundle.py` for the loader.
