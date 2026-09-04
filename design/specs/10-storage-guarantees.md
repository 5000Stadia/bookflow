# 10 — Storage guarantees

## Transaction lifetime

Read-only `Database` handles begin a real deferred SQLite transaction after SQLAlchemy connection setup and before caller reads. The first query fixes that database's snapshot. Closing the handle rolls it back and releases all resources, including construction and actor-resolution failure paths. Writable handles keep their existing explicit transaction ownership; no automatic read transaction is added to them.

Hosted and offline read commands, dry runs, and credential reads use this behavior. Hub identity/membership resolution precedes company selection and opening. Each database is internally consistent; independent hub and company snapshots are not a globally atomic snapshot. In-flight reads authorized before a permission change may finish; later requests must reauthorize. Row 7 supplies the stronger agent credential invalidation rule.

Hub and company schema checks and data reads agree on their respective opened read-only handles. Do not migrate or repair from a read/dry run. Writable-transaction detection explicitly excludes read snapshots. Existing writer serialization and source-of-truth commit ordering stay in place. Company show uses the company snapshot for company-owned fields, including duplicate summary fields, and the hub snapshot for registration names and paths.

Every admitted host reader, including credential reads, participates in shutdown accounting. Cleanup decrements that count even when closing raises. Password hashing, login delay, and writer submission hold no read snapshot; session issuance rechecks the verified credential on the writer. Shutdown refuses new readers and waits for admitted readers before the final checkpoint and root-lock release. A timeout retains the lock and reports an unfinished shutdown.

Shutdown also refuses new writer submissions. A folder-changing writer gates reader admission before releasing company handles, waits at most five seconds for admitted readers, and retains the gate through writer cleanup. An admission or drain conflict returns retryable `E_DB_BUSY`; a drain timeout leaves folders untouched. Empty organization moves use the same gate. Ordinary record writes and renames without folder moves remain concurrent with readers.

SSE drains at most 100 events per worker call. Each call owns and closes its reader session. The async generator reauthorizes between batches, yields buffered frames without an open database handle, and immediately drains another full batch before entering the notification wait. Preserve the initial subscribe-and-redrain race closure, filtered cursor advancement, canonical company identity, shutdown wake, and disconnect cleanup.

Audit tail's optional `scan_limit` bounds visible candidate events before business filters, independently of matched output count. Bounded mode returns `scanned_count` and `scan_more`, advances `next_after` across filtered-out candidates, and never scans more than the smaller of `limit` and `scan_limit` plus one lookahead. SSE uses 100 for both limits and redrains by `scan_more`, including empty matched pages. Default command-tail matching-page semantics remain unchanged.

## Commit durability and filesystem state

Writable hub and company opens set and verify WAL, foreign keys, and `synchronous=FULL`. Checkpointing is maintenance rather than the acknowledgement durability boundary. A failed passive checkpoint cannot skip reading committed event sequence numbers or waking subscribers. SQLite/OS errors continue through the existing error/redaction boundary.

A shared durable metadata writer creates an owner-private unique temporary file beside its destination, writes/flushes/synchronizes content, atomically replaces the destination, then synchronizes the containing directory on POSIX. Configuration and organization/company markers use it. Failures before replacement leave the prior file intact; failures after replacement are reported, and retry observes the actual state. Clean up only the exact temporary file owned by the operation.

Hub migration `hub0006` adds a singleton pending configuration projection. A settings change stores its complete intended TOML contents and request identity in the same transaction as its audit event. Committed pending contents override a missing or stale config file on read; reads do not repair it. After commit, the session clears its dirty flag before nested after-commit work so demo commands do not restage the outer intent. It completes that work before file publication, so a settings-file failure cannot suppress demo seeding. The writer then durably publishes the file and deletes the intent. A failed projection retains the intent and returns `E_PARTIAL_WRITE` naming the committed effects; subsequent writable commands retry. Bootstrap and direct trash recovery also commit intent before publication. Pending projection state is ephemeral and is not demo-seeded.

Company/organization no-replace directory moves synchronize destination and source parents before clearing their persisted pending-move record. Already-moved recovery paths perform the same synchronization before recording completion. New authoritative company folders and marker directory entries are synchronized before ready/registration acknowledgement. Keep human-readable folder names, existing no-replace primitives, case-only-hop recovery, and current projection/partial-write semantics. Windows retains its write-through move primitive; platform limitations are explicit and not reported as hardware-verified. No broad filesystem cleanup, live data reset, or new backup product in this row.

## Verification

- Deterministic two-request barrier: pause customer show/list after the owner read, commit a versioned contacts/name update, resume and require a consistent old aggregate; a subsequent read sees the full new aggregate.
- A hub-side rename barrier verifies stable multi-query hub results; company show does not expose stale hub copies as authoritative company fields.
- Read-only handles keep a stable snapshot across concurrent commits and release it on close; error and cancellation paths leak no reader handles.
- Dry-run and normal writes still behave correctly; nested demo execution does not acquire a competing transaction.
- WAL/FULL/foreign-key settings verified on all writable opens; blocked-checkpoint commits remain readable and audit-atomic. On Linux, an isolated syscall witness verifies synchronization after the commit frames while a reader pins the WAL; do not simulate power loss against live data.
- SSE multi-page and filtered backlog, notification race, credential revocation between batches, shutdown and disconnect regression tests.
- Metadata syscall ordering, private modes, replacement failures, exact temporary cleanup, directory synchronization failures and idempotent retry; existing pending-move, migration rollback, and pristine-schema witnesses.
- Relevant storage/host tests during implementation; full suite after integration. Record latency effects without weakening correctness or deleting tests.
- Measure isolated company creation and demo reset before/after durable synchronization; retain cold command budgets. Planned ledger posting budgets remain to be measured when those commands exist.

## Boundaries

No changes to business fields, inherited-job semantics, public hosting, provider credentials, ledger implementation, or existing live roots. Bounded list/query and customer/job UI corrections follow as independent packages; they use these snapshot guarantees. This row does not close Row 5's human browser acceptance gate.

Update blueprint sections 3, 7.1, 15.2 and 18 and the built architecture description to match snapshot lifetimes, bounded complete stream draining, recoverable filesystem state, and measured budgets.
