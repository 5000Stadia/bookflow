# 10 — Storage guarantees

## Transaction lifetime

Read-only `Database` handles begin a real deferred SQLite transaction after SQLAlchemy connection setup and before caller reads. The first query fixes that database's snapshot. Closing the handle rolls it back and releases all resources, including construction and actor-resolution failure paths. Writable handles keep their existing explicit transaction ownership; no automatic read transaction is added to them.

Hosted and offline read commands, dry runs, and credential reads use this behavior. Hub identity/membership resolution precedes company selection and opening. Each database is internally consistent; independent hub and company snapshots are not a globally atomic snapshot. In-flight reads authorized before a permission change may finish; later requests must reauthorize. Row 7 supplies the stronger agent credential invalidation rule.

Company schema checks and data reads must agree on the same opened read-only handle. Do not migrate or repair from a read/dry run. Existing writer serialization and source-of-truth commit ordering stay in place.

SSE drains at most 100 events per worker call. Each call owns and closes its reader session. The async generator reauthorizes between batches, yields buffered frames without an open database handle, and immediately drains another full batch before entering the notification wait. Preserve the initial subscribe-and-redrain race closure, filtered cursor advancement, canonical company identity, shutdown wake, and disconnect cleanup.

## Commit durability and filesystem state

Writable hub and company opens set and verify WAL, foreign keys, and `synchronous=FULL`. Checkpointing is maintenance rather than the acknowledgement durability boundary. SQLite/OS errors continue through the existing error/redaction boundary.

A shared durable metadata writer creates an owner-private unique temporary file beside its destination, writes/flushes/synchronizes content, atomically replaces the destination, then synchronizes the containing directory on POSIX. Configuration and organization/company markers use it. Failures before replacement leave the prior file intact; failures after replacement are reported, and retry observes the actual state. Clean up only the exact temporary file owned by the operation.

Company/organization no-replace directory moves synchronize destination and source parents before clearing their persisted pending-move record. Already-moved recovery paths perform the same synchronization before recording completion. New authoritative company folders and marker directory entries are synchronized before ready/registration acknowledgement. Keep human-readable folder names, existing no-replace primitives, case-only-hop recovery, and current projection/partial-write semantics. Windows retains its write-through move primitive; platform limitations are explicit and not reported as hardware-verified. No broad filesystem cleanup, live data reset, or new backup product in this row.

## Verification

- Deterministic two-request barrier: pause customer show/list after the owner read, commit a versioned contacts/name update, resume and require a consistent old aggregate; a subsequent read sees the full new aggregate.
- Read-only handles keep a stable snapshot across concurrent commits and release it on close; error and cancellation paths leak no reader handles.
- Dry-run and normal writes still behave correctly; nested demo execution does not acquire a competing transaction.
- WAL/FULL/foreign-key settings verified on all writable opens; blocked-checkpoint commits remain readable and audit-atomic. On Linux, an isolated syscall witness verifies synchronization after the commit frames while a reader pins the WAL; do not simulate power loss against live data.
- SSE multi-page and filtered backlog, notification race, credential revocation between batches, shutdown and disconnect regression tests.
- Metadata syscall ordering, private modes, replacement failures, exact temporary cleanup, directory synchronization failures and idempotent retry; existing pending-move, migration rollback, and pristine-schema witnesses.
- Relevant storage/host tests during implementation; full suite after integration. Record latency effects without weakening correctness or deleting tests.

## Boundaries

No changes to business fields, inherited-job semantics, public hosting, provider credentials, ledger implementation, or existing live roots. Bounded list/query and customer/job UI corrections follow as independent packages; they use these snapshot guarantees. This row does not close Row 5's human browser acceptance gate.
