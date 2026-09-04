# 13 — Local performance tracing

## Capture boundary

The operator enables one finite diagnostic capture at process launch with `BOOKFLOW_TRACE_DIR`, an existing owner-private local directory outside the selected data root. With no setting, tracing is off. The normal console entry remains `bookflow`; a standard-library-only bootstrap initializes tracing before importing the existing CLI application. Direct library users may initialize and close the same recorder explicitly. A hosted capture is enabled when starting that host, not by a remote request, cookie, command input or API token.

No monitoring service, collector, telemetry endpoint, HTTP trace-control route, new dependency, accounting table or audit event is added. Recording is finite, not rolling; exported diagnostic files remain until the operator removes them. Browser network/render profiling uses browser developer tools rather than new browser instrumentation in this slice.

The recorder stops admitting new spans at 10,000 events or 60 seconds, whichever comes first. Active reservations count toward the event bound; maximum concurrent reservations is 256 and maximum nesting depth is 32. Dropped, unfinished and truncated coverage are explicit counters. Events already admitted may finish after the deadline. Closing snapshots the bounded buffer without waiting for running business work; late completions are discarded safely. A host closes its recorder after ordinary host shutdown. Abrupt process death can lose diagnostics and cannot lose or change acknowledged business work.

Export is one exclusively created mode-0600 file in the validated directory. Pin the directory handle where supported, refuse symlinks and unsafe ownership/modes, and never overwrite an existing file. If the selected data root is only resolved later, validate the capture destination against it before export; reject a destination in that root. Trace export is best effort, outside business transactions, and does not use the authoritative metadata writer. A constant diagnostic warning, without a path or exception message, reports failure. An export or recorder error must never replace a command result, mask its original exception or become an accounting `E_PARTIAL_WRITE`.

## Event contract

Use `perf_counter_ns` for elapsed time, with process-relative origins. Export a Chrome Trace Event JSON object with complete duration events, process/thread identifiers and bounded summary metadata. Per-thread nesting and cross-thread queue relationships use separate operation/span identifiers; overlapping threads are not presented as a single synchronous stack. Inclusive stage durations are labeled and must not be added together as exclusive costs.

Each queue wait uses its own virtual lane, identified by `2^30 + span_id` in the event's thread field and `virtual_lane=1` in its arguments; its real submitting thread id remains a numeric argument. This positive uint32 range is separate from native Linux thread ids and interoperates with timeline viewers. This avoids false synchronous nesting when a timed-out caller starts unrelated work while its earlier job is still queued. Other spans use native operating-system thread identifiers, not Python's potentially wider thread cookies. Explicit maintenance submissions carry a fixed maintenance mode independently of context presence.

Every event has a fixed allowlisted phase, start, duration, diagnostic operation id and optional parent id. Safe attributes are fixed mode/interface labels, canonical registry command names, fixed database category (`hub`, `company`, `other`), success/failure, and numeric counters. Failure names are fixed classifications, never arbitrary exception messages. Diagnostic identifiers are generated separately from caller-provided business context.

Never collect SQL text, parameters, query fingerprints, input/output values, usernames, company/record ids, paths, URLs, headers, cookies, credentials, reason/directive text or raw exception strings. Do not retain a function's arguments or return value in an event. No captured authorization context is transferred between threads. Dynamic command labels are admitted only after canonical registry resolution; unknown input is labeled generically.

## Instrumented boundaries

- CLI application import, parser construction and rendering. Interactive prompting is a distinct wait, not computation. External process wall time is measured separately; interpreter startup before bootstrap is not visible to the recorder.
- Core invocation, host forwarding, input validation, company/actor resolution, planner, mutation, audit/projection work and serialization. Nested commands share diagnostic ancestry without resetting their outer operation. All existing transaction ordering and cleanup remains unchanged.
- Root-lock acquisition, writer queue wait, writer execution including cleanup, reader-drain wait and after-write maintenance. Queue waiting ends when the writer dequeues the job. Caller timeout ends caller waiting, not the already-admitted job; late execution retains its original diagnostic identity.
- Database open/setup/close; statement execution; fetch/iteration; begin/commit/rollback; checkpoint operations. Only fixed categories leave the connection wrapper.
- Explicit metadata publication, file synchronization, directory synchronization and atomic replacement. SQLite-internal synchronization remains part of commit timing; the Python observer cannot separate it from other SQLite commit work.

`core/performance.py` owns the dependency-free recorder, context handling and export. Disabled operations return before timing or event allocation; no export path is touched. Recorder-internal failures are isolated from the business call, which is invoked exactly once. Do not wrap the business call itself in a best-effort exception suppressor.

Only tracing-enabled connections use a local sqlite3 Connection/Cursor subclass supplied through the existing factory parameter in `storage/engine.py`. There is no global monkeypatch. SQLAlchemy events alone are insufficient because raw transaction and checkpoint calls bypass them. Connection convenience methods must return the traced cursor and preserve explicit cursor factories, row factories, iterator/fetch semantics, defaults and exceptions. Direct commit/rollback and cursor methods are covered without double counting. Statement classification examines only a bounded prefix and retains only a fixed category. Existing handles opened before tracing are explicitly outside per-statement coverage.

SQL execute and fetch describe API boundaries, not CPU versus transfer: SQLite may execute additional query work during fetch. SQLAlchemy conversion remains inside the surrounding planner/model stage. Migration or backup handles opened outside the central Database factory are outside detailed SQL coverage unless explicitly instrumented; their outer operation still appears.

The writer job carries a diagnostic token only. Install/reset it around job execution and cleanup with a `finally` boundary. Maintenance jobs get an independent diagnostic operation rather than accidentally inheriting a completed caller. Pooled connections consult the current operation at call time. Offline, forwarded and hosted are explicit modes; forwarded CLI and host captures are separate files with no claimed cross-process parentage in this slice.

## Verification

1. Tracing absent: unchanged CLI/HTTP output and errors, no created capture or audit entry, no heavy import from root help, strict root-help/warm-query tests unchanged.
2. Isolated seeded read, versioned write and configuration write: valid bounded trace with nonnegative intervals, database fetch and raw commit stages, explicit POSIX sync stages, and identical persisted business/audit facts with tracing disabled.
3. Barrier-controlled queued writes from independent callers: separate queue/execution intervals, correct SQL/cleanup attribution, no diagnostic-context leakage; caller timeout followed by eventual completion remains correctly attributed.
4. Original plan/commit/open/sync failures preserve their code and cleanup. Recorder append/export failure after a successful commit cannot turn success into failure. Reader accounting and the next writer job remain usable.
5. Sentinel secrets in inputs, parameters, filenames and exception text never occur in export. Event/depth/concurrency exhaustion and late completion leave bounded valid output with honest drop counters. Concurrent exporters cannot overwrite each other or escape the pinned destination.
6. Fresh-process capture includes application import; repeated hosted commands do not claim startup. Export opens in a compatible timeline viewer, with visibly correct thread separation and queue relationships.
7. Alternating traced/untraced workloads on the same machine report sample counts, medians, tail observations and export cost. Measure cold CLI separately from warmed query/show/update. Existing operation budgets are evaluated without tracing. Material disabled overhead or misleading attribution requires simplification before delivery.

No tests run concurrently with timing witnesses. Full storage/host regression coverage runs after integration; a targeted independent check addresses diagnostic privacy, exception/cleanup behavior and per-connection compatibility. Routine labels/docs do not trigger a new broad review round.
