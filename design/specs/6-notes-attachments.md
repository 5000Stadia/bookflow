# Row 6

Target: row 6 in `design/intention.md`; product contract: `design/blueprint.md` section 12.

## Scope and boundaries

One company-local collaboration module provides notes, attachment metadata/links,
and a bounded activity query. Existing dispatch owns permissions, attribution,
idempotency, versions and audit. The workbench uses the same commands.

No OCR, extraction history, approval engine, delivery bundles, import pipeline,
cloud storage, new frontend framework or background service is introduced.
Current master-record `notes` fields remain unchanged. Notes do not bump their
target's business version. Financial records and future agent provisioning are
outside this layer. Public schemas describe only implemented commands.

## Increment 1: versioned notes

`company/records.py` resolves explicit company-local record types to persistent
primary-key rows. The supported types are the twenty list definitions, their
stable-id owned children, `company_info`, `directive`, `principal`, company
`audit_event`/`audit_entry`, `customer_vendor_link`, and the new `note`. Singleton company identity is the
selected company's id. No caller-supplied SQL/table name is interpolated. Internal
presence, sequence and retry state are not annotation targets. Future persistent
record types extend this explicit registry. Inactive targets remain valid.

Migration co0005 adds `notes` with common version/provenance fields, target tuple,
body, original author/interface/time, edit time and kind. It has a composite target
index and constrained kind. The migration is frozen, additive and reversible on a
disposable database; existing migrations do not import the new live schema. The
hub's next additive capability migration records the new noun.

`note add <record_type> <record_id> --body`, `note show <note>`,
`note edit <note> --body --expected-version`, and
`note list <record_type> <record_id>` are ordinary typed commands. Body is nonblank
and at most 65,536 UTF-8 bytes; text is preserved, not executed or rendered as HTML.
Reads require membership; writes require standard role plus the existing agent
reason gate. Public add creates only `comment`; `system` is reserved. Original
author/time survive edits. Editing requires a positive expected version and uses
the existing conflict response; no-op edits do not create versions/audit rows.
Adds support existing request idempotency. Show/list include common fields and
actor names from company-local principals.

List returns a page, default 50 and maximum 200, additionally capped at 262,144
UTF-8 body bytes, with count for that page, has_more
and next_cursor. Order is newest first by immutable ULID id, not wall clock; the
opaque cursor binds to company, target and current actor. Invalid/mismatched cursors
are rejected. This is a live list of current note versions, not an issued snapshot;
inserts above the cursor appear when the first page is refreshed. The activity
command separately supplies chronological immutable history.

Target lookup happens inside dispatch's authorized company transaction. Deactivation,
owned-child retirement, undo-create and rename preserve identity and remain permitted
when notes exist. Physical deletion of an annotated target is forbidden; current
list/child APIs already retain these rows, so no extra retirement guard is needed.
A note can be corrected by another standard member, with
the editor attributed; no implicit author-only permission is added.

Increment witnesses cover customer/account/company and registered child targets,
cross-company absence, readonly writes, reason gate, exact body bound, no HTML
interpretation, conflict/no-op/idempotency, historical audit content, target-version
stability, undo dependency checks, bounded pages and dry-run filesystem/database
immutability. Seed small demo comments through commands. Regenerate documentation
and test cold help/query budgets without imposing a new whole-suite duration gate.

## Increment 2: attachment bytes and links

Attachment store remains company-local SHA-256 addressing, one metadata row/body
per digest and separate stable link occurrences. The first upload's original
provenance is retained; link caption/actor/time supplies association context.
Unlink is soft and does not delete the body or audit history. No new source-capture
table is needed. Add/link/unlink/list/get use the existing command contract.

CLI paths are caller-side input/output conveniences only. Python provides a binary
stream; HTTP and local forwarding transfer bytes through a bounded invocation-owned
resource outside the JSON business model. A registry transfer descriptor drives
adapters. Metadata plus computed digest/size bind idempotency. HTTP cannot read or
write an arbitrary server path. Existing host ownership is respected; forwarding
must support bounded chunks rather than base64 in the 8 MiB JSON frame.

Authorize before consuming input and again at final execution. Count actual bytes
against the company setting (default 25,000,000) and a finite documented ingress
ceiling; bound metadata, temporary storage, concurrency and inactivity. Dry run
streams/hash-checks without persistent staging or publication. Cancellation and
rejection clean up only the invocation's temporary file.

Verify digest and size, fsync body, publish without overwriting existing bytes,
sync directory entries, then commit metadata/link/audit in the ordinary transaction.
No committed metadata may point at an unpublished body. Existing digest bodies are
verified, not replaced. An interrupted pre-commit publication may leave an orphan;
it must not delete bytes another committed operation references. No raw bytes enter
audit. Treat filenames/types as untrusted; basename-only presentation, authorized
download, attachment disposition, no-store and nosniff. Local download writes a
temporary then atomically publishes, refusing overwrite by default.

`company compact` collects only unlinked bodies under the existing filesystem
exclusion mechanism, with a durable intent and recovery. Retain metadata/history
and report collected bytes honestly. Downloads hold a bounded file lease rather
than a SQLite snapshot through slow output. Company copying includes its attachment
directory; verification is read-only and reports missing/corrupt bodies.

Before command or adapter integration, complete the exact transfer framing and lease/compact
state contract in section 12.2. The independent byte-store primitive below does not
expose a command, acquire authority or implement garbage collection. This plan does not permit filling those interfaces
with adapter-specific server paths or silently removing hosted CLI support.

### Increment 2a: bounded byte-store primitive

`company/attachment_store.py` is a standard-library-only implementation of the
byte lifecycle. Its caller supplies an already authorized stream, the resolved
company attachment directory and an effective size limit. It neither resolves
companies nor opens databases. No production adapter calls it until the full
transfer/lease contract above is complete. No dependency, schema or demo change
is needed for this internal-only primitive.

Scan input in requests of at most 65,536 bytes, counting actual bytes and computing
SHA-256. The effective limit is positive and at most 100,000,000 bytes; the later
company setting defaults to 25,000,000. A stream that returns more bytes than
requested, non-byte data or no progress (`None`) is rejected. Empty bytes means
EOF; a zero-length attachment is allowed. Read at most one byte beyond the limit
to identify oversize input. Adapters, not this synchronous primitive, own transport
timeouts and cancellation; exceptions must unwind the temporary owner.

An invocation context stages in a unique owner-only temporary inside the existing
attachment directory. Its path is an internal resource, never command input or
output. Dry-run scanning creates no file or directory. On exit, remove only that
invocation's temporary, including cancellation and oversize failures. Never remove
a published digest path on rollback or cleanup.

Publication consumes a live staged resource from the same store. Reopen neither
arbitrary caller paths nor a closed resource. Flush, re-read and verify the staged
bytes against their computed digest/size, then fsync the held descriptor. A
private two-hex shard directory is created if needed; refuse symlinked store,
shard and body entries and non-regular bodies. Publish with an atomic no-replace
hard link, then synchronize shard and store directories. Filesystems without hard
link support fail closed with `E_IO`; there is no overwrite-prone fallback.
Seal and close the staging writer before linking; publication consumes the resource
even if a later directory sync fails. No writable staged alias remains usable.
Retries use a new invocation. Verification checks descriptor size before hashing
and reads at most the expected size plus one byte, including sparse/corrupt files.

An existing digest path is opened without following symlinks, checked as a regular
file and verified for exact digest/size before deduplication is reported. Retry
synchronizes the existing file and parent directories too, so a preceding failed
directory sync is not mistaken for completed publication. Corruption returns
`E_IO` with a stable check label and does not replace or remove the existing body.
Publication returns digest, size and whether a body was newly published, no path.

The caller must hold the data-root ownership and company filesystem lease across
staging/publication. The primitive rejects pre-existing symlinks but does not
claim isolation from a malicious process with the same OS account changing
ancestor directories concurrently. It never deletes bodies; the later compact
command owns that separate durable transition. Hardware durability and Windows
filesystem behavior remain unverified until exercised there.

Witnesses: exact/over limit including multibyte input, bounded read requests,
malformed streams, no-file dry scan, private modes, cleanup on errors/cancellation,
same-content deduplication, corrupt existing and tampered staged bytes, unsafe
paths, unsupported hard links, file/directory sync failures, no-replace races,
and retry after publication but before its directory sync completes.

### Increment 2b: bounded transfer resources and framing

Implement the internal resource and transport boundary in blueprint 12.2 before
registering attachment commands. This increment changes host lifecycle semantics
only for explicitly owned jobs; ordinary `submit` timeouts retain their current
behavior. It adds no schema, dependency, endpoint, demo data or exposed upload.

`core/transfer_resources.py` supplies `TransferLease(principal_id, company_id,
release, lifetime_seconds=300, clock=time.monotonic)`. `add_cleanup(callback)`
registers caller-owned cleanup in LIFO order; `handoff()` moves to writer
ownership, `close()`/context exit close only caller-owned resources, and `finish()`
is the writer's completion path. Completed callbacks are removed; failed callbacks
remain for explicit retry, with capacity/lease retained. `cleanup_pending` becomes
true only after a completed owner's failed cleanup, never while its work runs.
All ownership and callback bookkeeping is thread-safe. `cancel()` signals only
caller-owned I/O; `check_io()` and `check_start()` reject cancellation/expiry with
`E_IO`. A started writer job does not periodically cancel a database commit.
Closed resources reject further use; repeated successful cleanup is harmless.

`Host.acquire_transfer(principal_id, company_id)` uses the same condition as reader
admission and the filesystem gate, with default limits eight global/two principal.
The active set retains resources until successful cleanup releases them.
`submit(..., resource=lease)` consumes ownership on entry and rejects resources
from another host or already handed off; queue-admission failures close its owned
resource. The accepted `_Job` owns it through execution and `_leave_clean`, and
sets done only after resource cleanup. A submitter timeout never closes an accepted
job's resources. `run_write(..., resource=lease, timeout=...)` uses the same path.
Existing token-refresh/maintenance jobs need no resource. `retry_transfer_cleanup`
retries only failed completed cleanup; shutdown uses it outside admission locks,
cancels caller-owned I/O, and waits for both readers and transfers before releasing
the data-root lock. A writer still alive after the final join retains that lock.
Filesystem release waits for readers and transfers; ordinary writes never wait
for transfers. Release timeout uses existing `E_DB_BUSY` behavior. Capacity/lifetime
overrides are constructor-only for deterministic tests, with positive bounds.

`core/transfer_protocol.py` supplies internal bounded metadata and binary framing
helpers. `encode_input`/`decode_input` implement the header contract exactly and
reject duplicate JSON keys, non-object roots, invalid base64/UTF-8, non-finite JSON
numbers and excessive sizes. `FramedReader(socket, limit, lifetime_seconds=300,
idle_seconds=30, check=callback)` presents bounded `.read(n)` over body chunks and
accepts EOF only at a zero terminal frame. It requests at most 65,536 bytes from
the socket, enforces length before reading a frame, and holds at most one frame.
`send_body(socket, stream, limit, ..., check=callback)` sends bounded chunks and a
zero terminator only after successful EOF. Partial send/receive progress cannot
reset the absolute deadline. `send_json`/`recv_json` use bounded four-byte lengths
and exact JSON objects with the same syntax guards. All socket waits use the
lesser of inactivity and remaining lifetime. Framing errors are `E_VALIDATION`,
premature EOF/socket failures/deadlines are `E_IO`, and excess actual body bytes
are `E_VALUE_RANGE`. Helpers do not authenticate, issue commands, auto-retry,
fall back, or claim final command completion on the zero terminator.

Witnesses use deterministic ownership barriers and socket pairs: capacity and
principal limits, cancellation/deadline, cleanup failure/retry, shutdown retaining
the root lock, filesystem exclusion, normal writes during I/O, a queued upload
outliving a submitter timeout, rejected/failed jobs releasing resources, fragmentary
frames, empty/exact/over-limit content, interrupted terminal frame, bounded reads,
slow peers and syntax/metadata bounds. The assembled host/resource boundary gets
a focused independent artifact review with an isolated mutation witness.

## Increment 3: activity and browser

`activity` combines target audit entries with note and attachment events, including
edits/unlinks, once per action. Reuse immutable audit snapshots, not today's edited
body at yesterday's time. Return bounded pages and since/until/kinds filters; stable
chronological tie-breaks and a fixed audit high-water make traversal deterministic.
SQL must limit candidates before snapshot decoding and batch actor lookups.
Start with joins/indexes over existing audit/note/link data; introduce no duplicate
event table unless the 10,000-event witness establishes it is necessary.

Record pages share a Notes and files area: safe text, attributed history, add/edit
note, choose/upload file, download and confirmed unlink. Readonly users have no
mutation controls. Errors preserve entered text and explain when a file needs to be
selected again. Upload/save states do not claim success before command completion.
Unsaved master forms are not discarded by independent comment/file actions.

Extend demo with a small local PDF and notes on customer, account and company. Real
browser tests cover keyboard/narrow view, text/file round-trip, failures and stale
edit conflicts. Verify standalone CLI, hosted CLI, Python and HTTP byte parity,
permissions, interrupted I/O, corruption, copy portability, dry-run and performance.
The row remains open until all increments satisfy its target; a notes-only checkpoint
does not claim attachment/activity completion or human visual acceptance.
