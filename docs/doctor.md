# `chimera doctor`

Setup-diagnostics command. Prints a structured report about the user's
environment so you can quickly tell whether API keys, local model
daemons, Docker, optional extras, and on-disk state are all wired up
correctly.

```bash
chimera doctor               # text output, colored if rich is installed
chimera doctor --format json # machine-readable
chimera doctor --no-color    # plain text, no ANSI escapes
```

## What it probes

Each probe emits a `Check` with a name, a status (`ok`, `warn`, or
`fail`), a one-line detail, and an optional remediation hint. Probes are
designed to be fast and stdlib-only: HTTP probes use `urllib.request`
with a 250 ms timeout, subprocess probes have a 2-5 s ceiling.

| Check                       | What it does                                           |
| --------------------------- | ------------------------------------------------------ |
| `env.ANTHROPIC_API_KEY`     | Is `$ANTHROPIC_API_KEY` set?                           |
| `env.OPENAI_API_KEY`        | Is `$OPENAI_API_KEY` set?                              |
| `env.OPENROUTER_API_KEY`    | Is `$OPENROUTER_API_KEY` set?                          |
| `env.XAI_API_KEY`           | Is `$XAI_API_KEY` set?                                 |
| `env.MOONSHOT_API_KEY`      | Is `$MOONSHOT_API_KEY` set?                            |
| `daemon.ollama`             | GET `http://localhost:11434/api/tags`                  |
| `daemon.llamacpp`           | GET `http://localhost:8888/v1/models`                  |
| `daemon.vllm`               | GET `http://localhost:8000/v1/models`                  |
| `daemon.sglang`             | GET `http://localhost:30000/v1/models`                 |
| `daemon.docker`             | `docker info` (subprocess, fast-fail)                  |
| `extra.rich`                | `import rich`                                          |
| `extra.textual`             | `import textual`                                       |
| `extra.asyncssh`            | `import asyncssh`                                      |
| `extra.modal`               | `import modal`                                         |
| `cli.{mink,otter,...}`      | `python -m chimera <cli> --version` for all 7 codenames |
| `eventlog.dir`              | `~/.chimera/eventlog/` exists + writable               |
| `plugin.index`              | `$CHIMERA_PLUGIN_INDEX` set OR default URL reachable   |

A missing API key is a `warn`, not a `fail`: you might only use one
provider. The exit code is `0` unless one or more checks emit `fail`
(today: only the eventlog probe can fail, when `$HOME` is read-only).

## Sample output

```text
chimera doctor:

  CHECK                       STATUS  DETAIL
  --------------------------  ------  ------
  env.ANTHROPIC_API_KEY       ok      set (108 chars)
  env.OPENAI_API_KEY          warn    not set
                                      hint: export OPENAI_API_KEY=... to enable that provider
  daemon.ollama               ok      reachable; 3 model(s): qwen3-coder:30b, llama3:8b, glm-5:latest
  daemon.llamacpp             warn    unreachable (URLError)
                                      hint: start the daemon listening on http://localhost:8888/v1/models
  daemon.docker               ok      docker info ok
  extra.rich                  ok      installed
  extra.modal                 warn    not installed
                                      hint: uv pip install modal
  cli.mink                    ok      chimera 0.5.0
  eventlog.dir                ok      /Users/you/.chimera/eventlog
  plugin.index                warn    no plugin index configured
                                      hint: set $CHIMERA_PLUGIN_INDEX, pass --index, or run `chimera config set plugin_index <url>` (see docs/plugins-index.md)

  summary: 14 ok, 4 warn, 0 fail
```

## JSON shape

```json
{
  "checks": [
    {"name": "env.ANTHROPIC_API_KEY", "status": "ok",   "detail": "set (108 chars)", "hint": ""},
    {"name": "daemon.ollama",         "status": "ok",   "detail": "reachable; ...",  "hint": ""},
    {"name": "daemon.llamacpp",       "status": "warn", "detail": "unreachable (URLError)", "hint": "..."}
  ],
  "summary": {"ok": 14, "warn": 4, "fail": 0}
}
```

Use this in CI to gate on environment readiness:

```bash
chimera doctor --format json | jq '.summary.fail == 0' || exit 1
```

## Adding a probe

`chimera/cli/doctor.py` is a single stdlib-only module. To add a probe:

1. Define a function that returns a `Check` (or a `list[Check]`).
   Accept any external dependency (network opener, subprocess runner,
   importer) as a keyword arg so tests can inject fakes.
2. Append it to `collect_checks()`.
3. Add a unit test in `tests/cli/test_doctor.py`.

The module never imports optional dependencies at load time; everything
is a soft import inside the relevant probe.
