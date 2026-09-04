# Generated documentation

## Outcome

A reader with no source access can discover every registered command, construct valid CLI and HTTP calls, interpret every output and named error, understand every hub and company table, and complete one authenticated, versioned, audited HTTP write against the demo company. Documentation is a deterministic projection of the command registry and database models wherever those structures are authoritative.

## Owned documentation

`bookflow docs generate [--output <directory>] [--check]` produces this complete documentation tree:

- `.bookflow-generated`, the ownership marker
- `index.md`
- `cli/`, with one `<noun>.md` page per registered noun; spaces become hyphens
- `schema/hub/` and `schema/company/`, with one `<table>.md` page per modeled table

The editable sources for `concepts.md` and `agent-guide.md` are packaged resources; generation copies them into the output without changing their prose. Every structurally generated Markdown file begins with the same generated-file notice. Rendering is ordered, contains no timestamp or machine path, and produces the same bytes in every working directory or installed wheel. Generation renders and validates the whole tree, stages it in a sibling directory on the same filesystem, then swaps the marked output tree atomically with rollback on failure. An empty or absent destination is accepted; a non-empty destination without the exact marker and a symlinked destination are refused. `--check` writes nothing, compares the complete path set and bytes, and raises `E_DOCS_STALE` with sorted missing, extra, and changed paths when they differ.

The command is registered as a local standalone command with an explicit runner and no database capability. It needs no initialized data root, is never forwarded, and is not exposed over HTTP. Its output names the selected output directory, whether the operation checked or generated, the file count, and the sorted relative paths. The Python distribution remains `bookflow-core`; the import and command remain `bookflow`.

## Command reference

Each command section is generated from `registry.Command`, its Pydantic input and output models, the common context contract, and the error catalog. It states:

- purpose, scope, kind, required role, capability, feature gate, local-only status, and HTTP route when routed;
- exact CLI syntax, positional arguments, flags, JSON input paths, types, required/default/null behavior, constraints, secret handling, clear behavior, and context headers;
- every output field recursively, with type, required/default/null behavior, and description where declared;
- command-specific errors and the common infrastructure errors, each with its stable message;
- one deterministic example invocation and valid JSON output.

Example inputs are a structured catalog keyed exactly to the registry, so adding or removing a command makes completeness fail. Input examples validate through the command input model. Output examples validate through the output model and contain every modeled field; unstable identifiers, timestamps, paths, process ids, secrets, and cursors use deterministic valid sample values. `serve` is explicitly marked as long-running and documents startup and shutdown rather than claiming an immediate response. `audit tail --follow` is documented as a streaming CLI option.

## Schema reference

The current SQLAlchemy hub and company metadata are authoritative for table names, columns, SQL types, nullability, primary keys, uniqueness, indexes, defaults, foreign-key targets, and human meaning. Every `Table.info` and `Column.info` has a non-empty description. Shared schema constructors may provide the same description for structurally identical common and address fields. The frozen migration modules remain unchanged. Completeness fails before generation when any current table or column lacks its co-located description.

Hub and company tables with the same name have separate pages. Each page states its database, table purpose, then one row per column with SQL type, nullable/default/key/index/reference facts and its description. `docs/index.md` links every command and schema page plus `concepts.md` and `agent-guide.md`.

## Concepts and agent guide

`docs/concepts.md` states the current command contract, selection precedence, context ownership, dry-run and idempotency behavior, version conflicts and disjoint-field merging, audit and directives, identity and isolation, integer-minor-unit money, interface parity, local storage, and exit/error documents. It makes no future-feature claims.

`docs/agent-guide.md` uses bearer-authenticated HTTP because the reader is a programmatic agent and MCP is not yet present. Operator bootstrap creates a fresh root and demo, issues a short-lived bearer secret, and starts the host. One cumulative Python-standard-library example discovers the demo company, records a directive, reads `info_version`, establishes an audit cursor, updates a field while citing the directive, observes and handles an overlapping stale-version `E_VERSION_CONFLICT`, retries from a fresh version, verifies actor/interface/directive in the audit list, polls the tail, and resumes one server-sent event from its cursor. It discovers every id, version, and cursor from prior output and embeds no machine-specific value. Before user/agent administration exists, the guide says the bootstrap token represents the human who issued it.

The executable guide block is extracted verbatim by a test and run against a real ephemeral HTTP host over a fresh demo root. Every code fence is classified as executable or illustrative, and an unclassified fence fails.

## Verification

- Generate into a temporary directory and compare the complete owned file set and bytes with committed `docs/`; `--check` succeeds on the committed tree.
- Change, remove, and add generated files in a copy; `--check` reports sorted changed, missing, and extra paths without writing. Generation refuses an unrelated or symlinked destination, and injected stage/swap failures preserve the previous complete marked tree.
- Registry coverage is exact. Every command, input leaf, output field, route/locality fact, required role, capability, feature, and error code appears in its page. Every example input and output validates against its model.
- Schema coverage is exact for both metadata collections and matches databases created from the frozen migrations. Same-named hub and company tables remain distinct. Type, nullability, default, key, index, reference, and description facts are asserted against the rendered pages.
- The literal agent-guide journey succeeds against real HTTP and proves the conflict does not overwrite the first update, the retry does, and the audit and resumed event carry the expected command, interface, client, and directive.
- `bookflow --help` stays under its existing cold-start budget. `bookflow docs generate --help` imports documentation machinery only for the `docs` noun.
- The full correctness suite passes. Its aggregate duration is recorded only as diagnostic information.
- One fresh Claude agent and one fresh Codex agent receive only committed `docs/`, a fresh endpoint and token, and a short list of writes. Each performs the task and reports the first page used, retraced steps, and ambiguity. Actionable friction is corrected and both trials rerun before closure.

## Edges

- This row documents all current commands; it does not create ledger posting, user creation, agent-principal assignment, MCP, imports, or exports.
- Reference completeness means every command is usable from its documentation. The agent-guide journey intentionally exercises the smallest representative write path rather than destructively running all commands.
- Generated pages are public declarative product documentation. Test fixtures and usability transcripts contain no credentials or private machine paths.
