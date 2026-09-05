# Bookflow

A multi-company accounting system for small businesses, under development. A Python library with a CLI, an HTTP host, and a browser workbench over the same commands, so people and AI agents work with the same books. Company setup, supporting lists, domestic double-entry journals, corrections, voiding, and accrual ledger reports are available. Foreign-currency posting and an MCP adapter remain planned.

The Python distribution is `bookflow-core`; the import package and command are both `bookflow`.

## Quick start

```
uv venv && uv pip install -e ".[dev]"
uv run bookflow init
uv run bookflow demo reset
uv run bookflow company list
```

Data lives in `~/.bookflow` unless `BOOKFLOW_DATA_ROOT` or `--data-root` says otherwise. Every command takes `--json`; `uv run bookflow --help` lists the rest. Activate the environment (`source .venv/bin/activate`) to drop the `uv run` prefix.

The host serves the same commands over HTTP and a browser workbench:

```
uv run bookflow user set-password <username>
uv run bookflow serve
```

Run `user set-password` in an interactive terminal; it prompts for the password twice. `serve` listens on 127.0.0.1:8765. `--bind <host>:<port>` moves it; any address outside loopback needs `--allow-network`. Open http://127.0.0.1:8765/ and log in with that username and password. Usernames are case-insensitive; passwords are case-sensitive.

```
uv run bookflow token issue --label <name>
```

The secret is shown once. Programs and agents send it as `Authorization: Bearer <secret>` to the same commands over HTTP.

CLI commands run on the same machine are handed to a running host automatically.

Attach a local PDF using a customer id from `bookflow customer query --company "Demo Plumbing Co" --json`:

```sh
uv run bookflow attachment add customer "$customer_id" receipt.pdf --company "Demo Plumbing Co" --json
uv run bookflow attachment get "$attachment_id" --out downloaded-receipt.pdf --company "Demo Plumbing Co" --json
```

Set `attachment_id` to the upload result's `attachment.id`. Downloads require a new output path and publish atomically after verification. Python accepts an open binary file:

```python
from bookflow import connect

client = connect()
client.use_company("Demo Plumbing Co")
customer_id = client.customer.query(limit=1)["items"][0]["id"]
with open("receipt.pdf", "rb") as source:
    result = client.attachment.add(
        record_type="customer", record_id=customer_id,
        original_filename="receipt.pdf", media_type="application/pdf",
        input_stream=source,
    )
```

See [binary transfers](docs/transfers.md) for Python downloads, HTTP bodies, limits, and retry behavior.

## Documentation

The generated [documentation index](docs/index.md) includes the complete command and database-schema references, core concepts, and an executable guide for a fresh agent. Regenerate it after changing a command or schema and verify that the committed tree is current:

```
uv run bookflow docs generate --output docs
uv run bookflow docs generate --output docs --check
```

Documentation generation is standalone: it does not need an initialized data root or a running host.

## Local performance tracing

On Linux, opt into a finite capture when launching a command or host:

```sh
trace_dir=$(mktemp -d /tmp/bookflow-trace.XXXXXX)
BOOKFLOW_TRACE_DIR="$trace_dir" uv run bookflow company list --json
# For hosted work, set the same variable when launching `bookflow serve`.
```

The directory must already exist, be owned by you with no group/other permissions, contain no symlink components, and be outside every selected data root on a supported local filesystem. Tracing is off without the variable. It records up to 10,000 spans over 60 seconds; existing spans may finish later. Stop the host normally to export its capture. Abrupt termination may lose diagnostics, not acknowledged accounting writes. Exported mode-0600 files remain until you remove them; restarting or closing a capture does not delete older files.

Open the resulting JSON file in a [compatible timeline viewer](https://perfetto.dev/docs/visualization/perfetto-ui). Stages include application imports, command planning, queue waits, database execution/fetch/commit and explicit file synchronization. Durations are inclusive: do not add nested stages together. Queue waits occupy separate virtual lanes (`virtual_lane=1`), with the submitting thread and operation/parent identifiers in event arguments; they may outlive a caller timeout. Forwarding client and host captures are separate, uncorrelated processes.

Only fixed diagnostic labels and numeric identifiers are captured—not SQL, values, credentials, paths or exception messages. There is no telemetry or remote capture switch. Failures report a constant warning and cannot change a command's outcome. Capture adds allocations and can shift garbage-collection pauses; evaluate normal performance budgets with tracing off. Coverage limits and dropped/unfinished spans appear in the JSON metadata. Connections opened before capture, explicit custom cursor factories, migration/backup handles outside the central database factory, interpreter startup before bootstrap and browser rendering are outside detailed tracing. SQLite-internal synchronization is part of commit time. Export is currently Linux-only; unsupported platforms fail closed. Library users can explicitly pair `bookflow.core.performance.start(directory)` with `recorder.close()` in a `finally` block.

## Layout

- `design/blueprint.md` — what every part is and how it works
- `design/intention.md` — the spec list, in build order
- `docs/index.md` — generated command and schema references plus the agent guide
