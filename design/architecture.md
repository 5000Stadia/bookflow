# Bookflow — architecture

What is built, module by module. Row 1 (package skeleton, registry, data root, hub, organizations, companies, demo, CLI) is the current state.

## Layout

```
src/bookflow/
  __init__.py            lazy exports: connect, Client, BookflowError, Money (nothing heavy imports at package load)
  client.py              Client.run / use_company / attribute form; builds Context with interface "python"
  core/
    registry.py          Command, Plan, Applied, Touched, @command, REGISTRY, load_all()
    dispatch.py          run(): data root, locality, root lock, umask 077, hub open, actor, company, roles, plan/apply, audit, config
    context.py           Context (blueprint 5.2); CONTEXT_FIELD_NAMES
    session.py           Session (open databases, actor, memberships), Actor, now_iso(), localize()
    errors.py            every E_ code and its message; BookflowError with exit_code and to_dict()
    ids.py               ULID new_id / is_ulid / normalize_ulid
    money.py             Money (minor units + code), currency table from data/currencies.csv, parse/format/JSON form
    models.py            ListOutput, WriteOutput, CommonFields, redact_paths()
    config.py            config.toml: [users.<login>] user_id/default_company; atomic save; os_login() from uid
    fs.py                filesystem type detection (Linux mountinfo; macOS statfs; Windows drive type) and check_local()
    locks.py             RootLock: <data_root>/root.lock, exclusive for the whole command, holder info, BOOKFLOW_LOCK_TIMEOUT
    perms.py             private_umask() (077), is_private_dir()
    moves.py             rename_noreplace() per platform; move_dir() with case-only hop
    lazy.py              LazyModule: command modules import services lazily so the CLI builds without SQLAlchemy
  storage/
    paths.py             data root resolution; display-name normalization and name_key; folder derivation, collision choice, reservation; markers
    engine.py            Database (sqlite3 + SQLAlchemy Core on one connection); read-only (query_only) and writable (WAL, checkpoint on close) opens
    migrate.py           HEADS constants; classify(); backup via sqlite backup API; migrate_to_head(); Alembic loaded only when migrating
    hub_migrations/      Alembic chain "hub": hub0001
    company_migrations/  Alembic chain "company": co0001
  hub/
    schema.py            users, api_tokens, organizations, companies, memberships, audit_events, audit_entries
    users.py             bootstrap users, common() field helper, user_names()
    access.py            memberships, org_role(), company_role() -> (access, role), visibility filters, role_satisfies()
    organizations.py     create (folder + marker + row), get, bump
    companies.py         register, get, list_visible, update, delete_company_rows, delete_organization_rows
    audit.py             write_event() with prefixed/compressed snapshots, decode, visible_event_ids_filter()
  company/
    schema.py            company_info (9.1 inventory), principals
    info.py              read_info, upsert_principal/upsert_actor, principal_names, write_display_name_copy (raw)
    rollout.py           create_company_folder(): stages 2-4 with cleanup
  commands/
    common.py            OrganizationOutput, CompanySummary builders, Empty/ListInput/NameInput
    hub_cmds.py          init (bootstrap path run_init), upgrade, organization new/list/show/rename, company new/list/use/attach/detach, demo reset, audit list/show
    company_cmds.py      company show, company rename
  demo/seed.toml         Demo Holdings LLC / Demo Plumbing Co
  adapters/cli/app.py    Typer app generated from the registry; nested fields -> --a-b flags; global options per scope; --interactive; error rendering
  adapters/cli/render.py tables, field views, JSON, errors on stderr
```

## Runtime facts

- Every command takes `root.lock` exclusively for its whole duration; `init` creates the root first. Lock timeout 5 s, `BOOKFLOW_LOCK_TIMEOUT` overrides (tests use 0.2).
- Hub opens writable when the command writes hub or config (the audit event lives in the hub); company opens writable only when the command writes company.
- Writable opens migrate behind-head databases after a backup and record an `upgrade` event; read-only opens of a behind-head database return `E_SCHEMA_BEHIND`.
- `apply` runs inside one hub transaction and one company transaction started by dispatch; commands that need more than one hub transaction (rename with move, organization move, demo reset, upgrade) commit and reopen transactions themselves and return `audited=True`.
- Paths in outputs and error details are nulled for non-hub-admins in dispatch (`redact_paths`).
- `Client.use_company` and `company=` on a call are step 1 of selection; `BOOKFLOW_COMPANY` is step 2; the saved default is step 3.

## Verified on this machine (Linux, ext4, Python 3.12)

- The row 1 done sequence through the library and the CLI, compared field by field (tests/test_row1_flow.py::test_library_and_cli_agree).
- Folder copied to a second data root and attached: identical `company_info`, identical database dump, identical directory listing, original creator's name resolved through `principals`.
- Second process during a command: `E_DB_BUSY` with the holder's command through the CLI and the library.
- File modes under umask 022: every file 0600, every directory 0700.
- Cold start: `bookflow --help` about 290 ms; SQLAlchemy is not imported for help.
- Demo reset repeated on one root; trash accumulates one folder per reset.

## Not verified on hardware

- macOS `statfs` and `renamex_np` branches, Windows drive-type and `MoveFileExW` branches: unit paths only, no real run.
- A refusal on a real network mount: not attempted on this machine (no NFS, CIFS, or sshfs available); `E_NETWORK_SHARE` is exercised with mocked mount tables only.
- Alembic migration of a behind-head database: exercised only with the single initial revision, so the backup-then-migrate path has run on fresh files only.

## Known gaps carried to later rows

- `--help` lists options and positionals but not output fields or error codes (row 4).
- Company writes are recorded by common fields and `principals` only; the company audit tables arrive in row 2.
- No `user add` or `membership grant`; tests insert users through the repository layer (tests/conftest.py::make_actor).
- The currency table holds 62 common codes, not the full ISO 4217 list.
