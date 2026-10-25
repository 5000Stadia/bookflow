# Performance logging and tracking (R180)

R180 asks for timings per command, recorded and reported, so the system can be optimised
against real numbers; then posting, deposits and reconciliation of 1,000+ rows brought down to
seconds. Increment 1 (this document) builds the instrument and a baseline. Increment 2
optimises against what increment 1 measures.

## Two instruments, kept apart

Bookflow already has `core/performance.py`: an opt-in developer trace
(`BOOKFLOW_TRACE_DIR`), one Chrome-format span tree per process, refused inside any data root,
capped at 60 seconds. It answers "where does the time go inside this one call". It stays as it
is. The new instrument, `core/timings.py`, is always on (unless switched off) and answers
"which commands are slow, how often, and is it getting worse". It records one row per command.

## The one hook

Every command that runs against the books passes through `dispatch.execute()`: the offline
path (`dispatch.run`, used by the CLI and the Python client), the host's HTTP execution
(`adapters/http/execution.py`, which also carries MCP, since `adapters/mcp/runtime.py` calls
`run_hosted`), the workbench (an HTTP context with client `bookflow-workbench`) and the
streaming and transfer paths. Nested commands (demo seeding, memorized entries, cutover) use
`run_in_session` and are part of their parent's row, not rows of their own. The four public
deposit reads (`deposit show/items/history/query`) bypass `execute` through
`publication_deposit.execute_detail`; a second call of the same hook sits there. A forwarded
CLI call is recorded once, by the host that ran it. Standalone tools (`docs generate`, `mcp`,
`perf bench`...) are not recorded.

The hook takes `perf_counter_ns` before and after, and sets a context variable that the
SQLite cursor (`storage/snapshot_sqlite.Cursor.execute/executemany`) increments: statement
count and time spent in `execute` (SQLite steps to the first row there; later `fetch` steps
are not counted, so `sql_ms` is a lower bound). That counter costs one context-variable read
per statement. At the end the hook appends a tuple of numbers and fixed labels to an
in-memory deque (bounded at 10,000; full means dropped and counted). A daemon thread writes
the deque to the store every 2 seconds, and an `atexit` handler flushes a one-shot CLI
process. The store is never touched on the request's thread and never inside its
transaction: if the store is missing, locked, full or corrupt, the writer drops the batch and
the command has already succeeded.

Measured overhead (load 37 on 16 cores): 9 us per command for `begin` and `end`, plus 0.55 us per
SQL statement for the counter (5.1 us to 5.7 us for a trivial `SELECT 1`). An invoice post issues
about 455 statements, so the hook adds about 0.26 ms to a 250 ms command, 0.1%.

Not recorded: a command refused before `execute` on the offline path (an unknown command, input
that fails validation before the lock is taken). Those are cheap refusals, not slow work.

## What a row holds, and what it never holds

| field | meaning |
|---|---|
| `at` | UTC timestamp (seconds) |
| `command` | registered command name (a fixed label) |
| `surface` | `cli`, `python`, `http`, `mcp`, `workbench`, `system` |
| `principal` | `human`, `agent`, `system` |
| `outcome` | `ok` or the error code (`E_VALIDATION`...) |
| `wall_ms` | time inside `execute` (validation, authorization, plan, apply, audit, serialize) |
| `sql_count`, `sql_ms` | statements executed and time in `execute` calls |
| `rows_in`, `rows_out` | longest list in the input (lines, sources) and in the output (items) |
| `company` | opaque: HMAC-SHA256 of the company id under a per-installation random salt, 16 hex |
| `size_class` | company database size bucket: `<1MB`, `<10MB`, `<100MB`, `<1GB`, `>=1GB` |

Never recorded: argument values, names, amounts, memo text, record ids, users, paths, error
messages or details. The size class uses the file size (one `stat`) rather than a transaction
count, which would cost a query per command; it tracks history length closely enough to
separate a new company from one with five years. The time inside `execute` excludes process
start, the root lock and hub open on the offline path; those are the same for every command
and the developer trace covers them.

## The store

`<data root>/telemetry/timings.db`, a SQLite file of its own beside the hub, in WAL mode with
`synchronous=NORMAL`. It is not part of any company, backup, restore, export or audit. Tables:
`calls` (raw rows), `daily` (one row per UTC day, command, surface and outcome class: count,
p50, p95, max, total ms, total statements) and `meta` (salt, enabled flag, sealed days). Bench
runs are JSON files, not rows here (below).

Retention, applied by the writer at most once a minute: a finished day is rolled up into
`daily` and sealed (sealed days are never recomputed); raw rows older than 30 days are deleted;
raw rows beyond 100,000 are deleted oldest first, their days rolled up first; `daily` rows
older than 730 days are deleted. Measured: 100,000 raw rows (120 commands, 4 surfaces, 29 days)
plus their 4,137 daily rows make a 14.9 MB file; a daily row costs about 60 bytes, so two full years
of daily rows for 50 commands on 3 surfaces add about 6.5 MB. Writing 110,000 rows in batches of
5,000 took 2.6 s; a 7-day report over 26,000 rows takes 0.22 s.

**Off switch.** `perf settings --recording off` (installation administrators) sets the flag in
the store; the writer reads it before each batch, so nothing more is written within 2 seconds
in a running host and at once in a new process. `BOOKFLOW_PERF=off` in the environment turns
the hook into a no-op for that process without opening the store at all.

## Commands

| command | who | what |
|---|---|---|
| `perf report` | any authenticated principal; installation administrators see every company, others only rows for companies they can see | p50/p95/max/count/errors per command and surface over a window, and the slowest single calls; filters `command`, `surface`, `since` (date, or `7d`/`24h`) |
| `perf settings` | installation administrators | show or set recording on/off; shows file size, row counts, caps |
| `perf generate` | local operator tool (standalone) | build the large demo company into a new data root |
| `perf bench` | local operator tool (standalone) | run the scenario set against a copy of a generated root, store the result as a named run |
| `perf compare` | local operator tool (standalone) | a run against a baseline; flags regressions beyond a threshold |

`perf report` and `perf settings` are hub commands on every surface (Python, CLI, HTTP, MCP);
the workbench has a report page at `/hub/perf` (a table, filter form through htmx). The three
operator tools are standalone, local-only commands: they never open the installation's books.
Runs are stored as JSON under `--store` (default `<data root>/telemetry/bench/`); a run file
holds the scenario timings, the machine's load average and CPU count, the generator profile and
the Bookflow version.

## The large company

`perf generate` (module `bookflow/perf/generate.py`) builds a company through the ordinary
commands, in one held session (one lock and hub open, then `dispatch.execute` per command), so
every invariant of the books holds and the generated history is what users would have posted.
It is seeded (`random.Random(seed)`), with dates fixed (history 2021-10-01 to 2026-09-30) so a
rerun gives the same data; record ids are new each time. Demo-style names only.

The `large` profile, a small trade business over five years: 2,000 customers, 400 items, 150
vendors; per month about 130 invoices of 1 to 4 lines, 110 customer payments, 30 bills, 25 bill
payments and 6 deposits gathering the payments. The R180 cases are added at the end: 1,000
payments left in Undeposited Funds for a 1,000-line deposit, the checking account never
reconciled (so a reconciliation sees every deposit and bill payment uncleared), and an
organisation of 200 companies for the membership grant case. The `small` profile is the same
shape at one tenth for tests. The figures, generation time and disk size actually measured are
in the baseline section. Posting through commands is slower than writing rows directly, and
that is deliberate: the generator's own rate is a benchmark, and it gets faster as increment 2
lands.

## The bench scenario set

Each scenario runs through the Python client (the full offline path, as the CLI takes it minus
interpreter start), 5 repetitions after one warm-up, on a fresh copy of the generated root:

- reads: home (`company show`), register (checking, newest page), customer list, item list,
  A/R aging, profit and loss and balance sheet over the five years;
- writes: invoice post, bill post, payment receive, a 20-line deposit;
- the R180 cases: the 1,000-line deposit, reconciliation start and preview over 1,000+
  uncleared rows, an invoice post at the end of the five-year history;
- the known slow case: an organisation-wide `membership grant` over 200 companies.

`perf compare` flags a scenario when its p50 is more than the threshold (default 25%) and more
than 20 ms slower than the baseline's.

**Keeping comparisons fair.** Wall times move with the machine's load: this machine runs other
projects, and the baseline below was taken at a load average around 45 on 16 cores, which
inflates every time. Each run records the 1-minute load average and CPU count beside its figures.
Compare wall times only between runs taken under similar load (within about 20%), and otherwise
compare the SQL statement counts, which do not depend on load at all: a change that halves a
command's statements is real whatever the load. `perf compare` shows both columns.

## Baseline

(Filled in by step 3.)
