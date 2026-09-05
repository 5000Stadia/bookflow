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

Before this increment's code, complete the exact transfer framing and lease/compact
state contract in section 12.2. This plan does not permit filling those interfaces
with adapter-specific server paths or silently removing hosted CLI support.

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
