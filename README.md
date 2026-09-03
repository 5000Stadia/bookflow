# Bookflow

A multi-company double-entry accounting core for small businesses. A Python library with a CLI, an HTTP host, and an MCP server over the same commands, so people and AI agents post to the same books.

## Run

```
uv venv && uv pip install -e ".[dev]"
bookflow init
bookflow demo reset
bookflow company list
```

Every command takes `--json`; `bookflow --help` lists the rest.

## Layout

- `design/blueprint.md` — what every part is and how it works
- `design/intention.md` — the spec list, in build order
- `docs/` — command and schema reference, generated once something runs
