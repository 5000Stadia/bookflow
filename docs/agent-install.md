# Agent install: from the GitHub address to working books

For an AI agent asked to set up Bookflow for someone. It takes three shell commands and one call. You need Python 3.12 or newer, git and [uv](https://docs.astral.sh/uv/).

## 1. Install (verified, locked)

<!-- bookflow-example: illustrative -->
```sh
git clone https://github.com/5000Stadia/bookflow ~/bookflow-app
uv sync --frozen --project ~/bookflow-app
```

This is the one install path; use it as written. Why this one: the repository's `uv.lock` pins every dependency to an exact version and hash, and `--frozen` installs exactly that lock or stops, so nothing is resolved fresh from the index; the code you run is the clone you can read; nothing is installed system-wide. Then the command is `~/bookflow-app/.venv/bin/bookflow` (or `uv run --frozen --project ~/bookflow-app bookflow ...`); the examples below write it as `bookflow`. If the owner names a release, `git -C ~/bookflow-app checkout <tag>` before syncing.

## 2. Set up

Run this in the folder your MCP client reads `.mcp.json` from (your project folder), with the owner's details:

<!-- bookflow-example: illustrative -->
```sh
bookflow setup --company "Green Valley Landscaping" --currency USD --owner dana --owner-name "Dana Ruiz" --json
```

In one step it:

- creates the books (in `~/.bookflow`, or `--data-root`) with the owner as their administrator; this computer's login is the owner's;
- creates the company with that home currency and a chart of accounts (`--chart general`, or `service`, `product`, `contractor`, `retail`, `nonprofit`);
- creates your own identity, `assistant` (`--agent`, `--agent-name`), working for the owner with the `standard` role (`--role` chooses another), so the audit trail records "Assistant for Dana Ruiz", never the owner alone;
- issues your token into an owner-only file beside the books and writes the `bookflow` server into `.mcp.json` (`--mcp-config PATH`); the file names the token file and never holds the secret;
- makes a one-time link for the owner to set their password in the browser (`owner_link.url`; it works once and expires after two hours, `--link-minutes`; `bookflow owner-link` makes a fresh one later);
- starts the host in the background and returns once it answers (`url`; `GET <url>health` returns 200). `--no-serve` skips this.

Rerunning `setup` with the same company is safe: it reports what exists, adds what was never made and never makes anything twice. It keeps the agent's current role unless you pass `--role`. It never undoes a person's decision: if the owner unassigned or suspended the agent, revoked its access or revoked its token, setup reports that step as `stopped` with the command the owner runs to restore it, and changes nothing. If the owner has not used their link yet, a rerun replaces it with a new one.

The JSON result holds everything: `company.company_id`, `agent`, `mcp_config`, `mcp_server` (the stdio command for any other MCP client), `token_file`, `url`, `owner_link`, `owner_checklist`, `steps` and `next_steps`. Every path in it, `.mcp.json` and the token file is absolute, whatever `--data-root` you gave.

## 3. Connect and check

Reload your MCP client so it starts the `bookflow` server from `.mcp.json`. It has three tools: `bookflow_list_commands`, `bookflow_help` and `bookflow_run`. Check the connection:

<!-- bookflow-example: illustrative -->
```json
{"command": "company show", "input": {}}
```

as the arguments to `bookflow_run`. The company is already selected. Every write needs a short `reason` beside `command` and `input`. The [MCP guide](mcp-guide.md) and the [agent guide](agent-guide.md) cover the rest.

### Until your MCP client reloads

A running agent often cannot reload `.mcp.json` until it restarts. Do not hand-write MCP messages: run the same call from the shell, in the folder setup wrote `.mcp.json` into, with `bookflow agent run`:

<!-- bookflow-example: illustrative -->
```sh
bookflow agent run "company show" --json
bookflow agent run "customer create" --input '{"name": "Maple St HOA"}' --reason "New customer" --json
```

It reads the host, the token file and the company from `.mcp.json` (`--mcp-config` names another file) and makes the very `bookflow_run` call your MCP client would, with your own token: the books record it as yours, made for the owner, exactly as over MCP. `--dry-run` previews a write. Never run ordinary `bookflow` commands for the owner's books instead: those run as the owner's login, not as you.

## What to tell the owner

Setup's result ends with `owner_checklist`, the two things only the owner does, each with its exact command; hand it over as it is:

- **Choose a password.** You see the owner's link because you ran setup: hand it straight to the owner, and do not open it, keep it or use it yourself. Opened on this computer, it lets them choose a password once; after that they log in at the `url` (for example http://127.0.0.1:8765/) with their username. If it expires or is lost, `bookflow owner-link` makes a fresh one (the old one stops working); never rerun setup just for a link.
- **Turn on daily backups.** The checklist gives the `bookflow backup schedule` command, with a folder; another disk or machine is safer. Only a person may set it.

`company show` lists the owner's steps still pending (`owner_steps`), so either of you can check later. Also tell them:

- What you enter shows in the books as yours, made for them.
- Some things stay theirs: the closing date, who can do what (people, roles, tokens), merging duplicates and backup schedules. You will ask them when one comes up.

## Later

- After a restart of the computer, start the host again with `bookflow serve --background`. It prints the address once it answers.
- When the chart is numbered (it is by default), give a new account the next free number in its type's range with `account create --number`, as the rest of the chart has, and check the numbers with `account list`.
- A revoked token stays revoked. Only the owner decides to issue a new one, with `bookflow setup --company "..." --new-token`.
