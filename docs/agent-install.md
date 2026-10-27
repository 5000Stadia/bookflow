# Agent install: from the GitHub address to working books

For an AI agent asked to set up Bookflow for someone. It takes two shell commands and one MCP call. You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) (or pipx).

## 1. Install

<!-- bookflow-example: illustrative -->
```sh
uv tool install git+https://github.com/5000Stadia/bookflow
```

With pipx instead: `pipx install git+https://github.com/5000Stadia/bookflow`. Either way you get the `bookflow` command, MCP support included.

## 2. Set up

Run this in the folder your MCP client reads `.mcp.json` from (your project folder), with the owner's details:

<!-- bookflow-example: illustrative -->
```sh
bookflow setup --company "Green Valley Landscaping" --currency USD --owner dana --owner-name "Dana Ruiz" --json
```

In one step it:

- creates the books (in `~/.bookflow`, or `--data-root`) with the owner as their administrator; this computer's login is the owner's;
- creates the company with that home currency and a chart of accounts (`--chart general`, or `service`, `product`, `contractor`, `retail`, `nonprofit`);
- creates your own identity, `assistant` (`--agent`, `--agent-name`), working for the owner with the `standard` role (`--role`), so the audit trail records "Assistant for Dana Ruiz", never the owner alone;
- issues your token into an owner-only file beside the books and writes the `bookflow` server into `.mcp.json` (`--mcp-config PATH`); the file names the token file and never holds the secret;
- makes a one-time link for the owner to set their password in the browser (`owner_link.url`; it works once and expires after two hours, `--link-minutes`);
- starts the host in the background and returns once it answers (`url`; `GET <url>health` returns 200). `--no-serve` skips this.

Rerunning `setup` with the same company is safe: it reports what exists, repairs what is missing and never makes anything twice. If the owner has not used their link yet, a rerun replaces it with a new one.

The JSON result holds everything: `company.company_id`, `agent`, `mcp_config`, `mcp_server` (the stdio command for any other MCP client), `token_file`, `url`, `owner_link`, `steps` and `next_steps`.

## 3. Connect and check

Reload your MCP client so it starts the `bookflow` server from `.mcp.json`. It has three tools: `bookflow_list_commands`, `bookflow_help` and `bookflow_run`. Check the connection:

<!-- bookflow-example: illustrative -->
```json
{"command": "company show", "input": {}}
```

as the arguments to `bookflow_run`. The company is already selected. Every write needs a short `reason` beside `command` and `input`. The [MCP guide](mcp-guide.md) and the [agent guide](agent-guide.md) cover the rest.

## What to tell the owner

- Open the link from `owner_link.url` on this computer and choose a password. It works once. After that, they log in at the `url` (for example http://127.0.0.1:8765/) with their username.
- What you enter shows in the books as yours, made for them.
- Some things stay theirs: the closing date, who can do what (people, roles, tokens), merging duplicates and backup schedules. You will ask them when one comes up.

## Later

- After a restart of the computer, start the host again with `bookflow serve --background`. It prints the address once it answers.
- When the chart is numbered (it is by default), give a new account the next free number in its type's range with `account create --number`, as the rest of the chart has, and check the numbers with `account list`.
- To stop using your token, revoke it with `token revoke`. Rerunning `setup` issues a new one.
