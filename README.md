# Bookflow

A multi-company double-entry accounting core for small businesses. A Python library with a CLI, an HTTP host, and a browser workbench over the same commands, so people and AI agents work with the same books. An MCP adapter is planned but is not yet included.

The Python distribution is `bookflow-core`; the import package and command are both `bookflow`.

## Run

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

Run `user set-password` in an interactive terminal; it prompts for the password twice. `serve` listens on 127.0.0.1:8765. `--bind <host>:<port>` moves it; any address outside loopback needs `--allow-network`. Open http://127.0.0.1:8765/ and log in with that username and password.

```
uv run bookflow token issue --label <name>
```

The secret is shown once. Programs and agents send it as `Authorization: Bearer <secret>` to the same commands over HTTP.

CLI commands run on the same machine are handed to a running host automatically.

## Documentation

The generated [documentation index](docs/index.md) includes the complete command and database-schema references, core concepts, and an executable guide for a fresh agent. Regenerate it after changing a command or schema and verify that the committed tree is current:

```
uv run bookflow docs generate --output docs
uv run bookflow docs generate --output docs --check
```

Documentation generation is standalone: it does not need an initialized data root or a running host.

## Layout

- `design/blueprint.md` — what every part is and how it works
- `design/intention.md` — the spec list, in build order
- `docs/index.md` — generated command and schema references plus the agent guide
