# Parity Matrix

Tracks badger's flag and slash-command surface vs the sibling
Chimera coding-agent CLIs.

## Flag surface

| Flag                  | mink | otter | ferret | weasel | shrew | stoat | **badger** |
| --------------------- | ---- | ----- | ------ | ------ | ----- | ----- | ---------- |
| `--version`           | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `--model`             | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `-p / --print`        | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `--output-format`     | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `--max-steps`         | yes (50) | yes (50) | yes (50) | yes (50) | yes (50) | yes (50) | **yes (25)** |
| `--cwd`               | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `--allowed-tools`     | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `--rerun-on-failure`  | no   | no    | no     | no     | no    | no    | **yes**    |
| `--max-reruns`        | no   | no    | no     | no     | no    | no    | **yes**    |
| `--against`           | no   | no    | no     | no     | no    | no    | **yes**    |
| `--sandbox`           | no   | no    | yes    | no     | no    | no    | no         |
| `--approval`          | no   | no    | yes    | no     | no    | no    | no         |

## Subcommand surface

| Subcommand   | mink | otter | ferret | weasel | shrew | stoat | **badger** |
| ------------ | ---- | ----- | ------ | ------ | ----- | ----- | ---------- |
| (default)    | REPL | REPL  | REPL   | REPL   | REPL  | REPL  | **REPL**   |
| `serve`      | no   | yes   | yes    | partial| no    | no    | partial    |
| `sessions`   | yes  | yes   | yes    | yes    | yes   | yes   | **yes**    |
| `share`      | yes  | yes   | partial| yes    | yes   | yes   | **yes**    |
| `agents`     | yes  | yes   | yes    | partial| yes   | partial| partial   |
| `bench`      | yes  | yes   | partial| no     | yes   | no    | partial    |
| `parity`     | no   | no    | no     | no     | no    | no    | **yes**    |

`partial` = the dispatcher exists but emits a stub message; the body
is owned by another wave or another sibling CLI.

## Slash-command palette

| Slash command  | shared | mink | otter | ferret | **badger** |
| -------------- | ------ | ---- | ----- | ------ | ---------- |
| `/help`        | yes    | yes  | yes   | yes    | **yes**    |
| `/model`       | yes    | yes  | yes   | yes    | **yes**    |
| `/tools`       | yes    | yes  | yes   | yes    | **yes**    |
| `/clear`       | yes    | yes  | yes   | yes    | **yes**    |
| `/cost`        | yes    | yes  | yes   | yes    | **yes**    |
| `/compact`     | yes    | yes  | yes   | yes    | **yes**    |
| `/yolo`        | yes    | yes  | yes   | yes    | **yes**    |
| `/init`        | yes    | yes  | yes   | yes    | **yes**    |
| `/exit`        | yes    | yes  | yes   | yes    | **yes**    |
| `/sandbox`     | no     | no   | no    | yes    | no         |
| `/approval`    | no     | no   | no    | yes    | no         |
| `/parity`      | no     | no   | no    | no     | **yes**    |
| `/rerun`       | no     | no   | no    | no     | **yes**    |

## Defaults

| Default                        | Value                  |
| ------------------------------ | ---------------------- |
| Default model                  | `claude-sonnet-4-6`    |
| Default max-steps              | `25`                   |
| Default `--rerun-on-failure`   | `False` (opt-in)       |
| Default `--max-reruns`         | `2`                    |
| Eventlog directory prefix      | `~/.chimera/eventlog/badger-*` |
| Provider chain ordering        | Anthropic, OpenAI, OpenRouter, Ollama |

## Live snapshot

The `chimera badger parity --against …` subcommand asserts these
values against the live agent at runtime. See
[parity.md](./parity.md).

## Cross-links

- [Chimera architecture (8-phase map)](../architecture.md) — where the rows in this matrix live in the shared library.
