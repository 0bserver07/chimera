# Function Synthesis Compile Protocol

The `RemoteCompiler` speaks a small HTTP contract. Implement this contract to
host your own compile service (self-hosted fine-tuning pipeline, third-party
adapter-training provider, etc.).

## Request

```
POST <endpoint>
Content-Type: application/json
Authorization: Bearer <api_key>   # optional
Accept: application/zip

{
  "spec": {
    "name": "classify",
    "description": "Classify sentiment as pos/neg.",
    "examples": [{"input": "...", "output": "..."}],
    "input_schema": null,
    "output_schema": null
  }
}
```

## Response

- **Status 200:** body is the raw `.chi` bundle (`application/zip`). See
  [.chi format](./function-synthesis.md#chi-bundle-format) for required
  members.
- **Status 4xx/5xx:** body is treated as an opaque error message and
  surfaced as `CompilerError` on the client.

## Bundle requirements

The service MUST return a valid `.chi` bundle:

- `manifest.json` with `schema_version: 1`, `name`, `description`,
  `base_model`, `adapter_format: "gguf-lora"`, `created_at`, `chimera_version`.
- `adapter.gguf` — LoRA adapter in GGUF format.
- `prompts.json` — `{"system": str, "user_template": str, "stop": list[str]}`.
- `spec.json` — the request `spec` JSON-serialized.
- `metadata.json` — free-form; at minimum SHOULD include
  `compiler_backend`, `base_model_sha256`.

## Reference clients

- `chimera.function_synthesis.compilers.remote.RemoteCompiler`
- `chimera fs compile --compiler remote --endpoint <URL> --api-key <KEY>`

## Reference servers

None bundled. See `chimera/function_synthesis/compilers/mock.py` for a
zero-network stub suitable for development.
