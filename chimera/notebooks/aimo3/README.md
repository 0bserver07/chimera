# AIMO3 Kaggle Submission

## Quick Start

### Local Development

1. Start vLLM with a math-capable model:
   ```bash
   python -m vllm.entrypoints.openai.api_server \
     --model Qwen/Qwen3-235B-AWQ --port 8000
   ```

2. Run the solver:
   ```bash
   python chimera/notebooks/aimo3/notebook.py \
     --problems path/to/problems.json \
     --output submission.csv
   ```

### Using Modal (remote GPU)

```python
import chimera

provider = chimera.create_provider(
    provider_type="modal",
    model="Qwen/Qwen3-235B-AWQ",
    base_url="https://your-modal-app.modal.run",
)
```

### Using HuggingFace Inference API

```python
import chimera

provider = chimera.create_provider(
    provider_type="compatible",
    model="Qwen/Qwen3-235B",
    base_url="https://api-inference.huggingface.co/v1",
    api_key="hf_...",
)
```

## Kaggle Notebook Setup

1. Upload model weights as a Kaggle dataset
2. Copy `notebook.py` into a Kaggle notebook
3. Install chimera: `pip install -e /kaggle/input/chimera/`
4. Start vLLM pointing to the uploaded weights
5. Run the solver

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--model` | Qwen/Qwen3-235B-AWQ | Model name for vLLM |
| `--base-url` | http://localhost:8000 | vLLM server URL |
| `--samples` | 8 | Solutions per problem (majority voting) |
