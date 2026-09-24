# Bookflow

A multi-company accounting system for small businesses. It is one set of commands reached four ways — a Python library, a CLI, an HTTP host with a browser workbench, and an MCP adapter — so people and AI agents work with the same books under the same rules and permissions.

What it does today:

- **Setup and lists** — organizations and companies, chart of accounts, customers and jobs, vendors, items (service, inventory, assembly), employees, terms, tax codes, price levels, classes and typed custom fields.
- **Sales** — proposals, estimates and work orders that become invoices or sales receipts by remaining work, quantity or percentage; statement charges; batch invoicing; customer statements.
- **Receivables** — receive payments and apply them to invoices and statement charges, credit memos (including stocked returns that restore inventory at the cost the unit left with), customer refunds of credits and overpayments, and deposits from Undeposited Funds.
- **Payables** — purchase orders, item receipts, bills, vendor credits, bill payments, cheques and card charges.
- **Banking and ledger** — journal entries, transfers, account registers, reconciliation, memorized transactions and sales-tax remittance.
- **Inventory** — weighted-average costing with dated corrections when a purchase is backdated, adjustments, valuation and stock status.
- **Reports** — profit and loss (also by class and job), balance sheet, cash flows, trial balance, general ledger, transaction detail, A/R and A/P aging, open invoices, unpaid bills, collections, sales by customer/item/rep, expenses by vendor, inventory valuation, stock status, missing cheques and more; accrual or cash basis, CSV export and whole-report printing.
- **Corrections** — revise, void, or delete (nine document families) with retained history, under per-user permissions.

Invoices, sales receipts, estimates and statements produce a PDF from their own page. Not yet available: emailing documents, payroll, bank feeds, budgets and multi-currency ledgers — see [V2 and beyond](design/V2-ROADMAP.md).

The Python distribution is `bookflow-core`; the import package and command are both `bookflow`.

## Quick start

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). This takes you from a clean checkout to the demo company in a browser:

```
git clone https://github.com/5000Stadia/bookflow.git && cd bookflow
uv sync --extra dev                      # installs the exact tested versions from uv.lock
uv run bookflow init                     # creates your data root and you as its owner
uv run bookflow demo reset               # a sample plumbing company with a year of books
uv run bookflow user set-password "$USER"  # prompts twice; run it in a terminal
uv run bookflow serve                    # then open http://127.0.0.1:8765/ and log in
```

Where to go next:

- **Using it** — the browser workbench covers every workflow above; `uv run bookflow --help` lists the same commands for the CLI.
- **Automating it or connecting an AI agent** — the [agent guide](docs/agent-guide.md) and the [MCP guide](docs/mcp-guide.md).
- **Working on it** — `design/blueprint.md` explains every part, and `design/intention.md` the order it was built in.

Data lives in `~/.bookflow` unless `BOOKFLOW_DATA_ROOT` or `--data-root` says otherwise. Add `--extra mcp` to the `uv sync` to use the MCP adapter. Every command takes `--json`; `uv run bookflow --help` lists the rest. Activate the environment (`source .venv/bin/activate`) to drop the `uv run` prefix.

Numeric fields in the browser accept calculations such as `12.5 * 3`, `1 / 3`,
and `(20 + 5) / 2`. The result appears as you type; Enter, Tab, or leaving the
field enters it, automatically rounded to that field's precision. Quantities use
six decimal places and currency amounts use the currency's precision.

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

For a fixed full year of domestic journals, use `bookflow demo reset --include-reference`. This moves the entire previous demo organization and all its companies to trash. See the [reference-year guide](docs/reference-year.md) for isolated invocation, source amounts and independent monthly report expectations.

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
- [V2 and beyond](design/V2-ROADMAP.md) — future functional roadmap, beginning with human-directed UI improvements
- `docs/index.md` — generated command and schema references plus the agent guide
