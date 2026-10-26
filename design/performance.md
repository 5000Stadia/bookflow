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

The `large` profile, a small trade business over five years (about 13 documents a business
day): 2,000 customers, 400 items, 150 vendors; per month about 130 invoices of 1 to 4 lines, 110 customer payments, 30 bills, 25 bill
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

## Baseline (2026-10-25, increment 1, before any optimisation)

All figures were taken on this machine (16 cores) while other projects ran: the 1-minute load
average is given beside each, and it inflates every wall time. The statement counts do not
depend on load.

### The generated company

`perf generate --profile large --seed 180` (template `bench-large-s180-co0088`):

- 2,000 customers, 400 items, 150 vendors;
- 7,800 invoices with 19,551 lines, 6,490 customer payments, 354 deposits with 6,490 lines,
  1,800 bills and 1,500 bill payments: 17,944 documents over 60 months (2021-10 to 2026-09);
- 1,000 payments left in Undeposited Funds;
- an organisation of 200 empty companies.

It took **22,646 s (6.3 h)** at load 33 to 45. The phases were: setup 16 s, lists 201 s, history
20,412 s, the 1,000 payments 339 s, the 200 companies 1,673 s. It takes **2.1 GB** on disk:

- the company is 927 MB, about 49 KB per document. `audit_entries` alone is 293 MB: 532,677
  rows, about 30 per document, each with full before and after JSON;
- `reconciliation_effect_versions` is 52 MB for 1,854 rows, because its
  `provenance_snapshot` averages 25 KB and reaches 114 KB per bank movement;
- each empty company is 5.7 MB, so the 200 companies take 1.1 GB.

Posting slows as the history grows. An invoice post averaged 416 ms over the first tenth of the
invoices and 583 ms over the last tenth. Each block of six months took 15 min at the start and
51 min at the end.

That is why the profile has 18,000 documents and not 50,000: at today's speed 50,000 would take
about a day. The generator is resumable (a checkpoint after the lists and every 12 months), and
it gets faster as increment 2 lands.

### Scenario timings

Measured through the Python client, which is the full offline path a CLI call takes. p50 is the
client wall time over the timed repetitions. Max is the largest time inside `execute` that the
timings store recorded, warm-up included (the bench's own run file was not written because the
run was stopped); "stmts" is SQL statements per call; "rows" is the longest list in or out.
Five timed repetitions after a warm-up, except where the reps column says otherwise.

| scenario | command | reps | p50 ms | max ms in execute | stmts | SQL ms (p50) | rows | load |
|---|---|---|---|---|---|---|---|---|
| home | company show | 5 | 43 | 5,468 (the cold first call after the copy; then 32 to 35) | 45 | 16 | 7 | 34.6 |
| register, newest 50 | register query | 5 | 3,552 | 4,109 | 224 | 3,390 | 50 | 34.6 |
| customer list | customer list | 5 | 170,055 | 237,991 | 20,048 | 157,216 | 2,001 | 37.9 |
| item list | item list | 5 | 153 | 405 | 49 | 21 | 400 | 37.9 |
| A/R aging | report ar-aging | 5 | 1,180 | 1,214 | 47 | 906 | 50 | 38.1 |
| profit and loss, 5 years | report profit-and-loss | 5 | 729 | 731 | 50 | 699 | 7 | 38.0 |
| balance sheet | report balance-sheet | 5 | 1,024 | 1,096 | 51 | 986 | 4 | 37.9 |
| invoice post (2 lines) on the big history | invoice post | 5 | 617 | 675 | 455 | 420 | 2 | 37.9 |
| bill post | bill post | 5 | 175 | 510 | 214 | 45 | 1 | 37.9 |
| payment receive | payment receive | 5 | 346 | 715 | 257 | 203 | 1 | 37.9 |
| deposit post, 20 lines | deposit post | 5 | 107,769 | 174,472 | 2,056 | 18,553 | 20 | 36.7 |
| deposit sources, first page | deposit sources | 5 | 18,698 | 19,449 | 14,256 | 12,865 | 200 | 36.7 |
| deposit sources, a page after a 1,005-line void | deposit sources | 10 | about 517,000 | 549,938 | 37,056 | 499,000 | 200 | 34 |
| **deposit post, 1,005 lines** | deposit post | 3 | 184,591 | 193,720 | 46,419 | 15,985 | 1,005 | 34.0 |
| deposit void, 1,005 lines (its preview: 35,131 to 46,319) | deposit void | 3 | 265,643 | 284,277 | 54,128 | 20,943 | 1,005 | 34.0 |
| reconcile opening start (before any movement) | reconcile opening start | 1 | 5,750,576 | | 26,284 | 740 | 0 | 39.7 |
| **reconcile start, 1,854 uncleared rows** | reconcile start | 1 | 7,825,364 | | 26,284 | 12,908 | 0 | 39.7 |
| reconcile candidates, first page | reconcile candidates | 3 | 3,425,318 | 3,594,729 | 8,773 | 1,475 | 200 | 37 |
| **membership grant, organisation of 200** | membership grant | 3 | 17,385 | 23,031 | 201 | 7 | 0 | 34.8 |
| membership revoke, organisation of 200 | membership revoke | 3 | 16,984 | 17,276 | 201 | 6 | 0 | 35.3 |

Not measured: `reconcile mark` (all), `reconcile preview` and `reconcile finish` over the
1,854 rows. Each needs a draft whose `reconcile start` took 2.2 hours, and a page of
candidates took about an hour, so the bench was stopped after those three calls (16 hours in
all). They will be measured once the start is fixed.

Two kinds of cost show:

- **SQL-bound** reads (register, the reports, customer list, deposit sources): `sql_ms` is
  close to the wall time;
- **Python-bound** work (reconciliation, membership grant, most of a deposit post): `sql_ms` is
  a small fraction.

Both deposit post and void are well past the "seconds" target. Even a 20-line deposit takes
almost two minutes on this history, against 1.9 s on the tiny company.

### The five slowest, and where the time goes

Profiled with a stack sampler (every 20 ms, 600 s cap) on a fresh copy of the template, plus the
statement counts. (cProfile is installed, but it would have made the multi-hour calls longer
still; py-spy is not installed.)

1. **Reconciliation: opening start 5,751 s, start 7,825 s, a candidates page 3,400 s.** Pure
   Python: the SQL time is 0.7 to 13 s of it.
   - Samples: 96% in `reconciliation_storage_validation.validate` (its row and column loop),
     reached from `reconciliation_preparation.snapshot`, from `reconciliation_loading.load`,
     from `reconcile_cmds.prepare`.
   - Every command loads and revalidates the **complete** reconciliation effect storage: every
     row of every `reconciliation_*` table, every column checked, and each JSON snapshot
     decoded and `model_validate`d strictly.
   - That includes `provenance_snapshot`, 25 KB on average and up to 114 KB on each of the
     1,854 effect versions. Its size grows with the deposits behind each movement.
   - The work is O(the whole effect history) per command; the candidates page also scans it
     each time. An opening before the first movement touches nothing, and still costs 1.6 hours.
   - Fix direction: validate only the rows a command reads or writes, and keep provenance out
     of the per-command load.
2. **Customer list 170 s: 20,048 statements, 157 s of them in SQL.** An N+1 that is O(n²).
   - Samples: 96% in `customer_balances.own_balance`, called once per customer from
     `parties.project_party_record`, from `party_cmds._public_values`.
   - `own_balance` selects `balance_expression`, whose `customer_ar_totals` CTE is
     `MATERIALIZED`. That CTE groups **every** A/R posting line of every customer and then
     picks one customer.
   - So 2,001 customers each re-aggregate the whole A/R ledger (about 35,000 lines), about
     10 statements per customer.
   - Fix direction: one grouped query for the page, or a WHERE on `name_id` inside the CTE.
3. **Deposit post: 20 lines 108 s, 1,005 lines 185 s; void of 1,005 lines 266 s.** Mostly
   Python and row fetching.
   - Samples (20 lines): 97% in `deposit_lifecycle.prepare` → `deposit_dependency_history._readset`.
     88% of that is in `load` and `find_owners`; 52% of the time is spent in `fetchone`
     iterating rows.
   - `compare`, `capture` and `reconstruct` each walk the dependency history again (it runs
     twice, in the plan and in the apply).
   - The cost follows the size of the deposit history, not the deposit's own lines: a 20-line
     deposit costs over half as much as a 1,005-line one. It reads rows one at a time,
     including the large provenance JSON.
   - Fix direction: bound the read set to the selected sources, and stop reading it a second
     time between plan and apply.
4. **Deposit sources, one page of 200: 18.7 s and 14,256 statements; 517 s after a
   1,005-line void.** SQL-bound, about 70 statements per row.
   - Samples: 93% in `deposit_draft_validation.admit`, called per candidate from
     `deposit_source_queries.query`. 76% of the time is in its SQL (`candidates`).
   - Authorization repeats per row: 18% in `deposit_dependency_history._authorize_binding_graph`
     and `payment_authority.authorize_query`.
   - `config.load` is called again and again: 12%, each time re-reading `config.toml` and the
     hub's pending config (`_pending_from_disk`).
   - After the big void, each source carries a voided history, so the statement count rises to
     37,056 and SQL time to 499 s.
   - Fix direction: admit the page in one set-based query, authorize once per request, and load
     the config once per command.
5. **Membership grant over an organisation of 200 companies: 17 s at 201 statements, 7 ms of
   SQL.** Pure Python, O(scopes² × actions).
   - Samples: 79% in `permission_setup.edit` → `identity_admin._prepare` →
     `agent_authority.reconcile` → `permission_policy.compare`.
   - `compare` builds a `signature` for every subject, old and new. A signature walks every
     scope (each of the 200 companies) × every admin action key.
   - For each key it rescans every admin action of both catalogs to find its domains.
   - Each check calls `_base_reasons`, which finds the visibility entry with a linear `next()`
     over all of `p.visibility` (subjects × scopes). `admissions` repeats this per requirement.
   - All of it runs twice, in `preview_edit` (plan) and again in `apply_edit`.
   - Another 20% of a CLI call is first-time imports from `permission_runtime.known_catalog`,
     which imports and verifies every catalog layer.
   - Fix direction: index visibility and action domains once per comparison, and reuse the
     plan's comparison in apply.

Other figures worth acting on:

- **register query 3.6 s** for 50 rows: 97% in `ledger_reports.general_ledger`, which computes
  the running balance from the start of the account's history.
- **A/R aging 1.2 s, P&L 0.7 s, balance sheet 1.0 s**: SQL scans of the whole ledger;
  acceptable for now.
- **invoice post grew from about 250 ms on an empty company to 617 ms** at the same 455
  statements, so the cost per statement grows with the tables. About 85 SQLAlchemy compiles
  per post miss the compile cache (measured on the empty company).
- **Storage**: 30 audit entries and about 49 KB per document, and 5.7 MB per empty company.

## Increment 2, lane A (2026-10-25): the straightforward hot spots

Five fixes, each keeping every result byte-for-byte the same. To prove that, the JSON outputs of a fixed
read set were captured before and after, with volatile fields (generation time, cursors, paths) ignored,
on the demo company and on a generated one, and diffed. Both diffs were empty. The read set covered
lists, shows, the register in both directions and over several periods, general ledger, transaction
detail and deposit sources.

**The company measured.** The `large` profile scaled down to about a tenth (`Profile("medium", 24, 600,
120, 45, 45, 38, 10, 8, 2, 300, 60)`): 600 customers, 120 items, 45 vendors, 24 months (2021-10 to
2023-09), about 2,600 documents plus 300 payments in Undeposited Funds, and an organisation of 60
companies. It took 22 minutes to generate at load 39, and 507 MB.

**The large company could not be opened.** A copy of the parked large template upgraded cleanly:
`upgrade` took 306 s at load 39 for the hub and 201 companies, co0088 to co0085. But every command
afterwards was refused, base code and this branch alike, with `catalog_mismatch` on `state`. That
includes `permission show`, so `permission activate` cannot upgrade the catalog either. The
template's hub stores the permission catalog of the build that generated it, before
performance-v1 was re-chained on scheduling-v1. This chain does not hold that catalog version, so
`load_root` refuses it. Installations only run merged builds and never meet this. A bench template
is different: it must be regenerated whenever a permission layer it was built with is re-chained.
The copy was deleted.

**How it was timed.** `perf.bench.run_bench` with 3 timed repetitions after a warm-up (writes: no
warm-up), p50 of the client wall time. Statements come from the timings store. Both runs were at a
1-minute load of 37 to 38 on 16 cores.

| scenario | before ms | after ms | before stmts | after stmts | fix |
|---|---|---|---|---|---|
| customer list (600) | 8,597 | 263 (load 46; 1,528 at load 38 before the page reads) | 6,048 | 50 | 1 |
| vendor list (45, capture) | 495 | 59 | 353 | 45 | 1 |
| membership grant, organisation of 60 | 3,723 | 1,270 | 208 | 208 | 3 |
| membership revoke, organisation of 60 | 3,820 | 1,062 | 210 | 210 | 3 |
| invoice post | 433 | 179 | 458 | 447 | 4 |
| bill post | 260 | 98 | 217 | 216 | 4 |
| payment receive | 213 | 124 | 260 | 253 | 4 |
| deposit sources, page of 200 | 4,189 | 2,257 | 11,962 | 11,961 | 2 |
| general ledger, every account, one year (capture) | 601 | 295 | 47 | 46 | 5 |
| transaction detail, one year (capture) | 371 | 296 | 48 | 47 | 5 |
| register, newest 50 of a year (capture) | 294 | 258 | 123 | 122 | 5 |
| home (`company show`) | 45 | 34 | 45 | 44 | 4 |

The fixes:

1. **Customer and vendor lists**.
   - `own_balance` and `vendor_balance` used to run the `MATERIALIZED` CTE that groups every A/R (A/P)
     line, once for each projected party. Now they sum only the lines that read as the party:
     `members_sql`, the inverse of `survivor_sql`, through the `name_type, name_id` index.
   - A list now projects through one `parties.PartyPage`. It reads each fact set-based for the whole
     page, in the order the one-record read uses: custom values, addresses, contacts and their points,
     vendor links, item profiles, the prepayment account, labels and ancestors.
   - Balances come from one grouped read of each name's own net plus the live merges. `members_sql`
     and `family_cte` are then walked in Python.
   - A record left off the page (an ancestor dropped by a filter) falls back to the one-record read.
     `test_party_page_reads` compares every party with and without a page, also after a merge.
   - The statements no longer grow with the number of customers: 50 for 600 customers, against
     6,048 before.
   - Item list already projected through a page cache, and employee and other-name lists have no
     balance.
2. **`Config.load` once per command** (`core/config.load_scope`, entered by `dispatch.execute`). The
   first read of each path is kept, and every later load gets its own deep copy. Every command holds
   the RootLock, so no other process changes the settings while it runs. The first settings write in
   the process (`save`, `stage_pending`, `flush_pending`) turns the cache off for the rest of that
   command, and a file that fails to parse is never kept. A deposit sources page went from 1,436
   config disk reads to 2. Those reads use their own connection, so the statement count does not show
   them.
3. **Permission comparison** (`permission_policy`).
   - Each phase's facts are indexed once per comparison: visibility and memberships by key,
     requirement and default sets, and admin action keys by domain. Before, every question
     rescanned them.
   - `compare` keeps its last validated input and its answer. The apply still reads and validates its
     own facts in its write transaction. It skips the recomputation only when those facts equal the
     preview's.
   - `tests/permission_policy_oracle.py` keeps the scanning code verbatim as a test-only oracle.
     `test_permission_policy_index` checks every public question against it, answers and refusals
     both, on 300 random rosters, catalogs and semantics. A deliberately broken index is caught on
     62 of 100 seeds.
   - `_check` also reuses the declared field types of the permission modules' own value types.
     Before, it called `get_type_hints` for every value checked.
   - What remains of a grant is mostly snapshot decoding and catalog normalisation.
4. **Compile cache** (`storage/engine`). The cache belongs to an engine, and its keys hold the engine's
   dialect. With one engine per `Database`, every command compiled every statement afresh: 104
   compiles per invoice post however warm the process. There is now one engine per process. It owns
   no connections (`NullPool`): each `Database` still opens and owns its own sqlite3 connection, and
   hands it to the engine's creator only while it connects. A warm invoice post now compiles 2
   statements. The repeated "generated" sites were each a different statement, not uncacheable
   constructs.
5. **Ledger running balance** (`ledger_reports._ledger`). A posting's balance used to be a window sum
   over the account's whole history, rerun on every page. Now it is the account's opening balance
   (from the `balances` aggregate) plus the running sum over the period only. Every effect before the
   period sorts first, so the figure is unchanged. The two parts come back as `base` and `net` and
   are added in Python, because aggregate text never meets SQL arithmetic. The gain grows with the
   history before the period: about 2x on a year of this company, more on five years.

Left for lane B: admitting deposit sources per row (`deposit_draft_validation.admit`, called again for
each listed row at the end of `deposit_source_queries.query`, after `candidates` has already admitted
them) and the dependency history reads.

**The first catalog build, once per process (lane B's finding).**
- `permission_runtime.known_catalog` is built and verified once per process, not once per command:
  a warm process spends under 1 ms per command in it.
- The cost lands on every new process, so on every CLI command. The first `current_catalog()`
  imports all 56 catalog layers, and each layer re-validates the whole accumulated catalog
  (`_normal_catalog`, `catalog_manifest`).
- `_check` now caches each annotation's shape, and a union first tries the value's own plain type.
  Together with the shared field-type cache, the first build fell from 5.1 to 3.8 s CPU.
- What remains is that per-layer full validation, O(layers × catalog). It could be cut by
  validating only each layer's delta, or by a verified on-disk cache. That changes the
  verification itself, so it is left for its own decision.

## Increment 2, lane D (2026-10-26): the sealed permission catalog

Chosen 2026-10-26: keep a sealed, already-checked copy of the catalog on disk, re-checked
whenever the code changes. Code: `hub/permission_catalog_cache.py`, the first-call logic in
`permission_runtime.known_catalog`, `configure` in `core/dispatch.py`.

**What a full verification proves today.** Three things, in this order.
1. *The ancestor is unchanged.* Importing `permission_runtime` hashes the frozen ancestor
   (`permission_catalog.FROZEN_CATALOG`) and refuses to import unless it equals
   `ACCEPTED_ANCESTOR_SHA256`, which lives in the runtime and not beside the literal.
2. *Every layer is a well-formed catalog.* Importing each of the 56 layer modules builds
   `CATALOG = replace(previous.CATALOG, ...)` and `MANIFEST = catalog_manifest(CATALOG, standalone)`.
   That runs `_normal_catalog` over the whole accumulated catalog: every field has its exact
   declared type (literals, tuples, no empty strings or `*`); capability, command, company
   action, admin action, default and conditional source keys are unique; threshold rules hold,
   including the delete-capability rules; every command and company action names only
   registered requirements, and company-routed ones only company requirements; defaults are
   registered and never a delete capability; source call sites are positive lines;
   standalone names are typed, unique and not commands. Then it hashes the canonical JSON of
   the normalised catalog: the descriptor sha256.
3. *Every selectable version is pinned and unmoved.* `_verify_accepted` refuses to serve if any
   version `known_catalog` can return lacks a pin in `ACCEPTED_DESCRIPTOR_SHA256`, or if any
   layer's descriptor sha256 differs from its pin. Since a root stores the exact descriptor it
   activated, this is what keeps every stored root loadable.
It does not prove call-site parity with `SOURCE_COMMIT` or that each delta adds only its own
commands; those are tests (`test_permission_catalog_history`), unchanged.

**The sealed copy.** The first process of a code version that runs the full verification
writes what it verified to `<data root>/cache/permission-catalog-<key>.bin`: for each version,
its `CATALOG` as JSON, its standalone names, its `SOURCE_COMMIT` and its bundle's source
commit, exclusions and inventory digest.
- *The key* is a sha256 over the source bytes of every `hub/permission_*catalog*.py` (all
  layers, `permission_catalog.py` and the cache module itself, found by glob so a new layer such
  as serial-lot-v1 is covered without an edit here), `permission_runtime.py`,
  `permission_snapshot.py`, `permission_policy.py` and `core/deletion_families.py`; the pins and
  the ancestor pin; the Bookflow version; the Python version and cache tag. Never mtimes. A copy
  under any other key is never read, and is deleted when the new one is written.
- *The seal* is a sha256 over the file's index and body, re-hashed on every load.
- *On a hit* the runtime still imports `permission_runtime` (so guard 1 still runs), checks the
  key and the seal, and decodes only the versions actually asked for (usually one, the tip),
  each with the strict snapshot decoder. Each decoded version is then validated in full by
  `catalog_manifest` (rule 2 for that one catalog) and must hash to its pin (rule 3 for it).
  What a hit skips is importing the layer modules and re-validating the versions not used.
- *Never fail open.* A missing, partial, damaged or wrong-key file, any read, parse or decode
  error, or a version that no longer reproduces its pin, means the full verification and a
  rewrite. Writes go to a temporary file in the same folder, then `os.replace`, so concurrent
  first runs leave one whole file, and a reader sees an old whole file or a new whole one.
- *What the seal protects against:* truncation, partial writes, disk or copy corruption and
  stale code, which is the threat model: anyone who can write the data root can already edit
  the books. It is not a signature. Someone who rewrites the file and recomputes the seal can
  change what is not re-derived on a hit: element order within a version (the pin is over the
  normalised form) and the bundle's source commit and inventory digest. They cannot make a
  catalog that fails rule 2 or a descriptor that misses its pin.
- *Forcing the full check:* `BOOKFLOW_CATALOG_VERIFY=full` makes a process ignore the copy (and
  rewrite it). There is no `doctor` or `verify` command; `permission show` and `permission
  activate`, the commands that inspect and store the executable catalog, always run the full
  verification first (once per process; a warm server pays it on the first such call only).
- *Where it applies:* dispatch names the data root before any catalog is built. A process that has
  not named a data root verifies in full as before, and the copy is never written into a root
  that does not exist yet (`init` creates it).

**Before and after.** `customer list --json` on the demo company, one new process each, user
CPU seconds, three runs each, at a 1-minute load of 37 on 16 cores:

| | wall s | user s |
|---|---|---|
| main (db5d39c9) | 6.7 | 6.2 to 6.5 |
| this branch, no copy yet (full check, then writes the copy) | 6.5 | 6.1 to 6.3 |
| this branch, copy present | 2.8 to 3.0 | 2.6 to 2.8 |
| this branch, `BOOKFLOW_CATALOG_VERIFY=full` | 6.6 | 6.2 to 6.3 |

In isolation the first build fell from about 3.6 s CPU to 0.2 s (key 3 ms, load and seal
60 ms, decode of the tip 70 ms, its validation and pin 110 ms). Writing the copy costs the
first process about 0.2 s: the layers share most descriptor objects, so each is encoded once.
The file is 19 MB, since each version is stored whole.
