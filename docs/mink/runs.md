# `chimera mink runs`

Inspect and share persisted one-shot mink runs that live under
`~/.chimera/eventlog/mink-<utc>-<uuid>/`. Every `chimera mink -p PROMPT`
invocation journals its prompt, agent result, tool calls, and cost data
to a fresh directory there.

## List

```
chimera mink runs list [--limit N] [--runs-model NAME] [--success-only | --failed-only]
```

Renders a fixed-column table, newest first.

## Show

```
chimera mink runs show <run-id> [--no-events]
```

Prints metadata plus the full event transcript. Use `--no-events` to
restrict output to the summary block.

## Sharing

Package a run into a portable token you can email, paste, or hand off to
a teammate. Three sinks are supported:

```
chimera mink runs share <run-id> --sink file     # default
chimera mink runs share <run-id> --sink gist
chimera mink runs share <run-id> --sink base64
```

* `file` writes `~/.chimera/exports/<run-id>.tar.gz` and prints the
  absolute path. Works offline; no auth required.
* `gist` shells out to `gh gist create -p <tarball>`. Requires the
  GitHub CLI (`brew install gh`) and an active `gh auth login` session.
  Prints the resulting gist URL.
* `base64` returns a `data:application/x-mink-session;base64,...` URI
  suitable for inline pastes (chat, email, SMS). The payload is the
  same gzipped tarball — just encoded.

### Importing a shared run

```python
from chimera.sessions.share import import_from_url

# Accepts a gist URL, local file path, or data: URI.
run_id = import_from_url("https://gist.github.com/<owner>/<id>")
# run_id is now extracted under ~/.chimera/eventlog/<run_id>/
```

After import you can `chimera mink runs show <run-id>` to inspect it
locally, or resume it via
`EventSourcedSession.resume(eventlog_root, run_id, agent=...)`.

The export format is a gzip-compressed tar archive of the run's
eventlog directory (`summary.json` + every `event-*.json` file). It is
self-describing and stable across chimera versions as long as the
eventlog schema does not change.
