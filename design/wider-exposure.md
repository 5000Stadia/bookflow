# Wider exposure (R128)

Design contract, 2026-11-10. Approved scope: **“cursor key rotation, same-company hardening, encryption at rest and
hosted demo sessions, for use beyond a trusted local network”.** This document specifies future behavior, commands,
storage and build acceptance. It authorizes neither product implementation nor publication of a host. This change
contains design only; the Tests clauses describe later implementation work.

**Outcome.** The owner can deliberately expose a host to less trusted clients without treating company membership
as permission to read everything, keep stolen offline storage and backup copies confidential, rotate compromised
continuation keys, and offer isolated synthetic demo sessions. Nothing sends books, credentials or contact details
to another party without the owner's explicit direction. The local installation remains useful without any service
account, subscription, email provider or remote key service.

## 1. Existing core and threat model

[intention.md](intention.md) and the roadmap Vision govern: exact shared books, attributed writes, no hidden
organizations, portable company data and no unsolicited transmission. [intercompany.md](intercompany.md) and
[commissions.md](commissions.md) supply the design structure; this is a security boundary, not an accounting feature.
[permission-resolution.md](permission-resolution.md) remains the authority owner. Relevant inspected code:

| Owner under `src/bookflow` | Current behavior and R128 seam |
|---|---|
| `core/host.py`, `core/session.py`, `storage/engine.py` | One root lock and writer, per-request readers, WAL/FULL durability, file/transfer gates and publication admission. SQLite files are ordinary plaintext. Retain transaction and crash semantics. |
| `core/public_origin.py`, `commands/host_cmds.py`, `adapters/http/app.py` | Configured public origin forces proxy loopback binding, rewrites the effective origin and checks supplied Origin/Referer. Configuration accepts HTTP as well as HTTPS; local origins are also accepted and missing source headers can pass. Cookies use HttpOnly/SameSite=Lax and Secure for HTTPS. Cookie writes require the workbench header. These are existing controls, not proof of internet readiness. |
| `adapters/http/auth.py`, `hub/credentials.py` | Argon2 password verification with dummy verification for absent passwords, hashed random bearer/session secrets, expiry, revocation and agent principal/epoch checks. Session refresh extends expiry; login throttle bounds simultaneous attempts, not sustained guessing. |
| `company/ledger_schema.py`, `ledger_reports.py`, migration `0007_ledger.py` | `report_cursor_keys` is one 32-byte key at slot 1. Reports use domain-separated HMAC-SHA256 and bind query, company, permissions and data watermark. No key ID, expiry or rotation lifecycle. |
| `company/deposit_public_reads.py`, `deposit_read_pages.py`, deposit query/read owners | Public/private continuation purposes share the company key; public reads bind actor/principal/epoch and independently admit connected resources. All consumers must migrate together. |
| `company/query.py` | Generic list cursors are unsigned base64; the existing permission fingerprint covers identity and legacy role facts, not the complete granular policy. Include these cursors in the authenticated envelope and current-authority migration. |
| `hub/access.py`, `companies.py`, `permission_access.py`, `permission_runtime.py`, `agent_authority.py`, `core/dispatch.py` | Visible-company resolution precedes company opening; current policy and actor/principal intersection govern access. Legacy role/admin paths and identity administration have distinct admission rules. Membership alone cannot establish safe same-company projections. |
| `adapters/http/execution.py`, `published_transfer.py`, `core/commit_hooks.py` | Execution and publication rechecks, including streamed transfers. Extend these owners; do not add an adapter-only authorization layer. |
| `commands/backup_cmds.py`, `company/backup_archive.py`, `backup_checkpoint.py`, `backup_schedule.py` | Verified ZIP archives contain a SQLite snapshot, attachments and manifest. Adjacent checkpoints include audit and trial-balance facts. Restore registers a new company, never overwrites one. Scheduled retention deletes older copies after success. None of these files is an encryption boundary. |

The current trusted-LAN/tailnet posture assumes a trusted OS account, local storage, an owner-controlled host and
proxy, and network access limited by an external trusted network. `--allow-network` is an explicit LAN opt-in; a
network file share is never a supported database location. A tailnet supplies network admission, not business
permission. [TLS proxy instructions](../docs/agent-install.md#serving-over-https-phones-the-camera) already describe
`--bind 127.0.0.1:PORT --public-origin https://...`; R128 extends that path rather than opening a second public server.

**Protect:** ledger and audit history; documents and attachments; identities and membership existence; credentials;
query/filter facts; backups, exports and recovery material. The adversary can be an unauthenticated internet client,
a malicious site in an authenticated browser, a company member with narrow rights, a stolen-token holder, another
demo visitor, or someone with a powered-off disk/backup copy. Consider forged selectors/cursors, stale authority,
request floods, path traversal, stored HTML/script input, retry races and interruption during every storage change.

The owner-controlled OS and running host are trusted. An OS administrator, debugger or compromised application
process can read unlocked books and keys; at-rest encryption does not prevent that. It does not revoke previously
downloaded data, prevent an authorized export, or prove that disk contents were never maliciously rolled back.
TLS protects transport only to its termination point. A hosting provider operating that point or the running machine
would be a new trusted party (K1). No claim of protection against a compromised kernel, arbitrary code execution or
volumetric denial of service. Application and proxy bounds limit ordinary abuse; public operation still needs patching
and resource monitoring. Untrusted demo users must never share the production OS security context.

## 2. Layers and defaults

| Layer | Default | Wider profile requirement |
|---|---|---|
| Local / trusted network operation | Local loopback; existing explicit LAN opt-in remains | Not advertised as a wider deployment |
| Current authorization and safe projections | On for every interface; no opt-out from security fixes | Activated, current permission catalog; no legacy fallback |
| Versioned cursor keyring | On after migration, including local mode | Rotation and bounded cursor lifetime |
| Wider host profile | Off, explicit `security configure profile=wider` | HTTPS configured origin, loopback upstream, hardened authentication, resource limits, protected storage and verified recovery readiness |
| Protected local storage | Explicit setup; never silently encrypt or relocate existing books | Verified encrypted storage for every sensitive path; fail closed if unavailable |
| Encrypted portable backups | Explicit recipient enrollment; mandatory for wider mode | Whole payload including checkpoint encrypted before leaving protected storage |
| Demo service | Off; local preview is the default deployment | Separate disposable sandbox per session, synthetic seed only, no production mount or credentials |

The owner can enable cursor rotation and encrypted backups without exposing a host. Enabling wider mode publishes
nothing, creates no DNS record and buys nothing. Preflight returns exact blockers; a missing prerequisite is not a
warning bypassed by `--allow-network`. Turning wider mode off requires an authorized configuration change, stops
network service before relaxing requirements, and does not decrypt files or discard keys. It returns to loopback.

Wider startup requires `https://` public origin, the existing `127.0.0.1` upstream, explicit proxy configuration and
no direct network listener. Accept only the configured external origin for browser writes; do not carry forward the
local-origin allowance in this profile. Reject unexpected Host at the proxy; the app still constructs URLs from
configuration and never takes identity or scheme from forwarded headers. If rate limiting needs a client IP, only a
configured loopback proxy may supply a single overwritten address header; otherwise use conservative global limits.
No arbitrary forwarded chain parsing. Deployment instructions must demonstrate that clients cannot reach upstream.

Cookie-authenticated unsafe requests require an exact accepted Origin (or same-origin Referer fallback), a
session-bound CSRF token and the existing workbench header. Missing/null source is denied. Login, password setup
and logout forms receive the same source protection, with pre-login CSRF state for login/setup. Bearer-only requests
can omit browser source headers, but reject conflicting cookie credentials and reject an explicitly foreign/null
Origin. No wildcard credentialed CORS. GET/HEAD must not perform business writes; one-time setup links display a
form and consume only on protected POST. Return uniform login failures, no account-existence hint.

Default wider browser session limits: 30 minutes idle and 12 hours absolute, whichever is earlier; refresh never
extends the absolute deadline. Reauthentication issues a fresh secret. Password change, credential revocation and
principal suspension invalidate affected sessions and publication permits. Bearers default to 30 days and have a
90-day maximum in wider mode; existing unbounded tokens are preflight blockers requiring explicit replacement, never
silently renewed. Rotation uses a new credential, a verified client handoff and explicit old-token revocation.
Secrets are shown once, never in URLs, logs or persistent browser storage. Setup links remain single-use and expire
within two hours; creation/reissue requires authority and does not invoke email.

Add bounded login token buckets: per normalized account identifier 5 failed attempts per 15 minutes, per trusted
source 30 per 15 minutes, and global 120 per minute; bound concurrent password hashes separately at 5. Count unknown
accounts identically, use expiring bounded storage, and persist aggregate cooldowns across restarts without storing
submitted passwords. No permanent account lockout. Rate refusal gives generic retry timing. Defaults for commands:
60/minute per credential with burst 20, aggregate 600/minute; body limit 1 MiB except registered binary routes with
explicit limits. Keep existing transfer ceilings (8 per host, 2 per principal, 300 seconds). Bound report jobs,
connections, upload expansion, stream duration and writer queue independently; overload returns a typed retryable
error before mutation. Limits are tunable versioned host policy, not permission grants.

Responses containing books use private/no-store caching. Add CSP with nonces for required scripts, no external
scripts/fonts, frame-ancestors none, nosniff and no-referrer; escape stored text in HTML and downloads. Serve untrusted
attachments as downloads with safe types/names, never executable same-origin HTML. Proxy uses HTTPS-only redirects
and HSTS for this host without assuming authority over sibling domains. Suppress login/setup URLs and request bodies
from application/proxy logs; error output contains safe codes, never secrets or raw SQL.

## 3. Cursor key rotation

Replace the singleton constraint with a company-local keyring: opaque random `key_id`, 32-byte `key_material`,
`created_at`, `sign_from`, `sign_until`, `verify_until`, `revoked_at`, and version. A singleton control row holds
active key ID, keyring generation and restore incarnation. Exactly one key signs; only explicitly retained,
unexpired, unrevoked keys verify. Secrets remain excluded from command output, audit snapshots, annotations and
ordinary exports. The encrypted company backup contains them, but restoration creates a fresh incarnation and key.

Use a new bounded envelope carrying format version, key ID, purpose, company ID, incarnation, issued-at, expiry and
canonical payload, authenticated as a whole with HMAC-SHA256 and constant-time comparison. The domain prefix is
versioned; no caller chooses an algorithm. Bind actor ID, actor kind, principal ID/authority epoch, current permission
fingerprint, query/filter digest, report version, data watermark and continuation position. Domain separation covers
financial report, deposit private items, public items, history and every other key consumer found by the build
inventory. Copying between subjects, companies or purposes never works. Tokens provide integrity, not secrecy:
put no labels, descriptions, credential material or raw query text in them; prefer IDs and digests. They grant no
access and cannot replace current authorization or output projection.

Generic list cursors adopt this envelope too, with their own purpose and existing sequence/query contract. Replace
the legacy-role-only permission digest with the current granular policy signature and actor/principal authority
generation across all families. Deposit content fingerprints currently also use `_cursor_key`: during overlap,
verification must recompute that fingerprint with the token's authenticated key ID, not the new active signer.
Each continuation chain retains its issuing key for verification of pinned content, while a newly issued page
envelope uses the active signer and explicitly records the content-key ID inside the authenticated payload.
Both keys must remain valid for that chain's original expiry; never extend either deadline through pagination.

Default cursor lifetime is 15 minutes from the first page. Subsequent pages preserve that original expiry rather
than extending a chain indefinitely. Permit at most 60 seconds positive clock skew; reject impossible timestamps,
overlong input (>4096 bytes), noncanonical/duplicate fields, unknown versions/keys and invalid MAC before using
payload offsets. Safe restart instructions disclose no key diagnostics. A stale watermark keeps `E_QUERY_STALE`;
expired/retired/invalid continuation returns the existing cursor validation error and “restart without cursor”.

Routine rotation every 30 days happens under the existing company writer transaction: create random material,
activate it, stop the old signer and retain its verifier for 16 minutes. Retire expired material after that boundary;
retain nonsecret metadata/audit. On startup rotate overdue signers before serving; a read never creates a key.
Concurrent explicit/scheduled rotations use expected generation and idempotent operation ID. Rotation audit records
key IDs and reason only. Permission changes can invalidate data watermarks, including during overlap: overlap
preserves cryptographic validity, not a promise that changing books can continue paginating.

Emergency rotation revokes the compromised verifier immediately and switches signer atomically; invalidate queued
outputs issued under that key at publication admission. Every dependent cursor family restarts. Ordinary retirement
also rechecks at publication, not solely against a reader's stale SQLite snapshot. Rotation must drain/coordinate
through commit hooks so an old snapshot cannot mint fresh tokens after the old signer's deadline.

Migration rejects all legacy unversioned cursors with a restart instruction, creates a new key and removes the old
singleton material from the live ring; old backups are not retroactively sanitized. No indefinite compatibility
verifier. A missing signing key fences pagination with a typed security-state error, never a hard-coded fallback.
A lost cursor key costs only continuations: an authorized reset generates a fresh ring, preserving every book and
audit row. Never derive encryption keys or session secrets from this ring.

## 4. Same-company hardening and authority

Membership answers “may select this company”, not “may read its entire database”. Produce an inventory of every
registered command and auxiliary read surface: lists, lookups, counts, search, reports, previews, print/export,
attachments, audit/activity, batch/dependency inspectors, idempotency receipts, streams and background work. Each
entry declares required capabilities, connected-resource requirements, permitted output fields, cache/cursor scope,
and execution/publication checks. Unclassified paths fail closed in wider mode; completion requires no unclassified
path in the release inventory. Do not rely on hidden buttons or an HTTP-route allowlist as business authorization.

A clerk permitted to enter a sale but denied cost and payroll must not obtain those facts through item lookup,
profit reports, linked transactions, CSV, saved previews or audit before/after values. A denied aggregate contributes
neither rows nor counts, totals, suggestions, filenames or existence-sensitive error distinctions. Apply filters
before pagination/counting. Exact reads of a hidden record answer the same not-found as an unknown ID. When a
report cannot compute its promised total without denied sources, refuse the whole report; never label a partial
sum as the company's total. Joined objects require each relevant source permission; a permitted root does not
launder a denied child. Attachments require current authority on their owning record, not possession of a hash.

Revalidate credential, membership, deny rules, principal intersection and authority epoch at execution, commit
admission and each publication boundary. Permission reduction invalidates authority-sensitive caches and freezes
queued/streaming output before another chunk; it does not erase already delivered bytes. Retries resolve the original
operation's outcome only through its current authorized projection. A saved draft, schedule, batch or report is not
a durable permission grant. Use the existing writer/publication synchronization, not a second unsynchronized epoch.
Caches include company, effective subject, policy generation, purpose and field projection; no global cross-user
memoization of private results. Inaccessible company resolution still occurs before filesystem access and returns
no path, company name or organization hint.

**Permissions are independent of interface and of human versus agent.** A human browser, human bearer, delegated
agent, CLI and Python caller with equivalent effective grants receive the same result/error. Agent identity and
principal attribution remain mandatory where delegation applies; principal rights intersect actor rights, and a
client cannot supply a different actor or interface to acquire authority. Running under the owner's OS identity is
not an acceptable way for an agent to evade a denied command.

This requires explicit migration of existing special cases, not merely new R128 commands: `hub/people_only.py`,
the people-only catalog descriptors for user set-password, token issue, agent assign/authorize and permission
activate, the human-issuer check in `hub/credentials.py`, and any corresponding backup-schedule/admin adapter checks.
Replace kind/interface gates with narrowly scoped explicit capabilities and owner-approved delegation constraints.
No default agent receives identity, key, policy or backup administration. Neither can an ordinary human exercise
those acts without grants. A grant is necessary but cannot let its holder expand its own delegation or grant rights
outside its authorized grantable scope; self-approval of new authority is refused. Existing owner-only *decision*
requirements remain approvals bound to the operation, not a ban on an authorized agent executing it. Preserve
password/self-service target restrictions and token target/principal binding. Catalog activation previews resulting
authority changes; no automatic broad grants to make migration pass. R128 must not claim parity before these paths
are covered across interfaces, including direct shared-core calls.

## 5. Encryption at rest and storage

**Settled implementation boundary:** protect live data with owner-managed OS volume encryption and portable backups
with application-managed encrypted envelopes. Do not replace SQLite with a custom encrypted page store or decrypt
whole databases into ordinary temporary files. This protects database/WAL/attachment/metadata files together while
preserving the existing SQLite concurrency model. The first verifiable wider-host backend is Linux LUKS2/dm-crypt;
other host-platform verifiers are K4. Local Bookflow remains available on existing platforms. Encryption is at rest:
files read through an unlocked mount are plaintext to authorized OS processes.

The supported service layout places the entire data root on a dedicated encrypted local filesystem, plus any
sensitive service state. Coverage includes hub/company DBs, WAL/SHM/journals, attachments, marker/config files,
trash, migration backups, exports, scheduled-backup staging, SQLite spill files, upload/decompression directories,
application diagnostics and proxy temporary bodies/logs. Set service temp paths inside that mount; use encrypted
swap or disable swap/hibernation and core dumps for the service. Protect service secrets with OS permissions
(directories 0700, private files 0600; read-only backups can remain 0400). Do not put a volume unlock key on the same
unprotected disk as the encrypted volume. Default unlock is interactive/local at boot; unattended key services are K2.

Preflight resolves actual mounts and backing device encryption, including symlink and bind-mount escapes; a TOML
boolean saying “encrypted” is insufficient. Pin the approved mount/device identity and all writable paths. The
service cannot write to the underlying unmounted directory. Check before startup and filesystem-changing jobs,
and fence new admissions if coverage disappears or a configured path changes. Reject unsupported storage topology,
network filesystem or unverified backend. Do not infer encryption from a filename. A missing mount cannot initialize
an empty hub or trigger setup: return `E_STORAGE_LOCKED` and leave the books untouched.

Provisioning uses an owner-selected, already prepared encrypted destination. Bookflow never formats disks or removes
keyslots automatically. Migration is copy/verify/switch under exclusive root/file admission with readers and schedules
drained: take a verified recovery backup, copy consistent hub/company snapshots and all owned files, check hashes,
audit anchors, foreign keys and ledger totals, then fsync and atomically switch the service configuration. Persist
phases `prepared`, `copied`, `verified`, `activated`; restart chooses the verified root or remains fenced, never
merges two diverging roots. Only one root may serve writes. Retain the old root read-only until the owner authorizes
its disposal (K5); mark security status “plaintext residue remains” until addressed. SSD snapshots/backups may keep
old plaintext; deletion is not a claim of secure erasure. This status blocks wider activation. Resolve it only after
the owner approves an inventory-specific disposition: move retained copies into verified encrypted custody and
resolve their former storage/snapshots, or physically remove and secure the source media offline under the owner's
custody, or authorize an appropriate disposal procedure. An offline retained plaintext medium is explicitly outside
the encrypted-media confidentiality claim and must be recorded in the recovery inventory. Merely renaming the old
folder, changing its permissions or acknowledging a warning does not clear readiness. Never erase it automatically.

Key classes are separate:

| Material | Storage / custody | Effect of rotation or loss |
|---|---|---|
| Volume data key and unlock slots | Managed by OS encryption; owner keeps recovery unlock material and matching header backup off the machine | Lost last usable unlock material/header can make that volume unreadable; independent encrypted backups still recover books |
| Backup recipient identity | Random age X25519 identity; public recipient in versioned host config, private identity in owner-held recovery kit | Old archives require a matching old identity; new recipient does not decrypt old archives |
| Optional online backup verification identity | Separate local recipient/private key inside protected service secret storage | Allows automated end-to-end archive verification; its loss does not block offline owner recovery |
| Cursor HMAC keys | Company keyring within protected storage | Rotation/recovery invalidates continuations only |
| Password/token credentials | Existing Argon2 hashes and token hashes in protected hub; client secrets owner-only | Reissue/revoke independently; no book encryption dependency |

No secret appears in config examples, help, dry-run JSON, audit, error reports, process arguments or metrics. Secret
imports use a protected local file/descriptor or dedicated nonlogged secret transfer, never ordinary command JSON.
The server receives opaque secret handles; handles are single-use, short-lived, actor/operation bound and excluded
from audit bodies. Local OS provisioning has the same permission/approval requirements regardless of who operates
the console; a browser cannot turn an arbitrary server path into a secret-file reader.

## 6. Encrypted backups, restore and recovery

Retain the existing verified archive/manifest format as an inner payload. A versioned outer package contains the
archive and its checkpoint, all encrypted with age to an explicitly enrolled owner recovery recipient and, by
default, a separate online verification recipient. Only format identifier, ciphertext size and an opaque backup ID
need be outside; use opaque filenames. Do not leave a plaintext `.checkpoint.json` leaking accounts, totals or names
beside the encrypted archive. Put manifest names, schema, company identity and audit checkpoints inside encryption.
Use the maintained age implementation and format; pin the supported tool/version in packaging, verify its supply
chain, invoke without a shell, and reject external recipient plugins. No custom cryptographic primitive or remote
key lookup. Encryption alone does not authenticate the archive's author: anyone with a public recipient can encrypt
an input. Existing trusted checkpoint custody and restore provenance checks remain necessary.

Create/verify the inner archive only on protected storage. Stream encryption to a unique `.partial` destination,
fsync, decrypt the completed output into protected staging with the online identity, and run archive/checkpoint
verification again before atomic publication and success. An offline-only recipient configuration may create
archives but labels them “encrypted; restore verification pending”; it cannot satisfy wider readiness or trigger
retention deletion until an owner-side decrypt/verify receipt is recorded. Never silently fall back to plaintext
when the encryption utility, key or mount is unavailable. Failed backups preserve the previous known-good copy;
partial outputs are not counted toward retention. Existing already-approved destination/retention policy remains,
but newly encrypted backup operation never authorizes a new destination or deletes plaintext predecessors.

`company backup`, scheduled backups, `hub backup`, pre-migration copies and full-root recovery each declare their
coverage. Internal migration copies may remain ordinary files only inside the verified encrypted mount. Anything
written outside it must be encrypted, including hub snapshots and diagnostics containing sensitive facts. Whole-root
recovery is a coordinated maintenance snapshot with writers paused, containing hub, all registered companies,
attachments, registry/config and any pending-operation metadata; no copying of live WAL files as a substitute for
SQLite snapshots. The owner-held recovery identity is not bundled inside that encrypted backup.

A company backup recovers its books, history and included attachments; it does **not** recover hub users, memberships,
passwords, agent credentials, schedules or unrelated companies. Missing attachments remain explicitly reported as
in the existing backup contract. A verified full-root backup recovers installation state as of its checkpoint.
Recovery manifests list omitted files and recovery timestamps; never call an incomplete snapshot complete.

Restore requires an explicit source, destination, recipient handle and current restore authority. Validate ciphertext
length limits and decrypt/authenticate completely in protected quarantine before parsing or registering anything.
Reuse ZIP member allowlisting, traversal/symlink rejection, size/count/expansion limits, integrity/FK checks, supported
schema checks, company identity verification and audit/ledger verification. Wrong key, truncated ciphertext or
failed verification leaves the original files and registry unchanged. No partial plaintext may be downloaded or
published. Recheck source digest between preview and apply to defeat replacement. Restore continues to create a
new company; `as_copy` explicitly changes its identity. No implicit overwrite or in-place disaster rollback.

Every restore/attach of a copied company rotates its cursor incarnation before serving; source and copy cannot
share valid tokens. Full-root recovery starts quarantined on loopback, with all restored sessions/bearers/setup links
invalid, and schedules/delivery/provider jobs disabled pending explicit review. Reestablish owner authentication
using local recovery authority, then review membership/delegation before issuing credentials. Do not resurrect a
revoked credential from an old hub snapshot. Recovery status records the backup cutoff and warns about post-backup
transactions that are absent; software cannot reconstruct them from ciphertext or audit anchors alone.

**Owner recovery kit:** keep at least one tested, independently stored encrypted company/full-root backup; matching
private backup identities for every retained recipient generation; volume recovery unlock material and matching
LUKS header backup; software/format version and the restore instructions; a trusted audit checkpoint/digest and the
latest backup cutoff. Keep a second protected copy of key material separate from both the host and the backup disk.
Public recipients, passwords, cursor keys and a backup filename cannot substitute for the private backup identity.
Enrollment proves the owner-held identity by decrypting a challenge and a sample backup on a separate recovery path;
record only the proof/digest and date, not the private identity. No automatic key escrow.

| Failure | Exact recovery contract |
|---|---|
| Volume unlock secret lost, recovery slot still works | Unlock with recovery material, verify books, add/test a replacement slot; do not erase old slots during diagnosis |
| Volume/header unavailable, independently encrypted backup and its private identity survive | Restore books through the last verified backup; later writes require separate surviving evidence |
| Backup identity lost, live mount remains readable | Make and restore-test a new backup to a new identity immediately; old archives remain unreadable unless another matching recipient identity survives |
| Online verification key lost, owner identity survives | Restore using owner identity, enroll a new online verifier and test; no book loss |
| All unlock and backup identities lost, but a live mount or other plaintext copy remains readable | Preserve the running mount: do not restart, unmount or shut down. Restrict access and immediately make/restore-test a new encrypted backup to newly enrolled recovery identities; migrate to a newly recoverable encrypted volume before retiring the running one |
| All usable volume unlock/recovery material and all usable backup identities lost, with no readable live mount or surviving plaintext copy | Encrypted books are unrecoverable. Preserve ciphertext and report this explicitly; never delete, reset, initialize empty books or imply a password reset will recover them |
| Cursor key lost | Reset the ring with authorized administration; restart reports; all accounting data survives |

Routine volume unlock rotation adds and tests a new slot first; slot removal waits for explicit authorization. Changing
an unlock passphrase does not change the volume data key. A compromised data key requires migration to a fresh
encrypted volume and new backups, not merely new unlock credentials. Old header backups can preserve access with
old passphrases; retain/protect or dispose of them deliberately. Backup recipient rotation records new public keys,
proves a new recovery backup, then uses them for new archives. Old identities remain in the recovery kit for old
archives, or archives are explicitly re-encrypted and verified; never destroy keys on a timer. Rotation cannot
make stolen prior plaintext/ciphertext plus its compromised key secret again.

## 7. Hosted demo sessions

“Hosted” describes a runnable demo service, not permission to deploy it. The default build runs locally on loopback;
K1 decides any public destination/provider, cost and retention. No sign-up, email, analytics, CAPTCHA service or
external asset fetch by default. The welcome page says “Sample books — temporary session; do not enter real company
data”, shows expiry, and provides Start over. No import of a real company, no sanitized production clone.

Use the repository's synthetic demo seed rebuilt in a disposable sandbox. Each session has a random unguessable
256-bit entry secret, a fresh root, hub/company IDs, cursor keys and demo credentials. A controller outside tenant
sandboxes maps a hash of that secret to one sandbox; requests cannot select a filesystem path, process port or another
root. Token/cookie scope is tied to the session and expires with it. Ordinary company permissions still run inside
each demo. A demo role admits a documented safe subset of accounting commands on sample data; it is not hub admin.
The same role works through browser or an explicitly requested session-scoped MCP bearer. No human/agent exception.

Use separate OS process/user namespace, filesystem and resource sandbox per session with read-only application/seed,
private writable encrypted storage, no host home/config/credential mounts, no production root and no control socket.
The controller can create/reap sandboxes but cannot proxy arbitrary paths. Public demo infrastructure must have no
production data mounted anywhere in its service account; a shared hub plus an `is_demo` flag is insufficient. Startup
rejects attach/restore/custom-seed/config overrides that could inject real data. Dependency/network access is not
needed at runtime. Deny network egress at the OS boundary, including loopback services, link-local metadata, DNS and
SMTP; the front-door channel is the sole admitted traffic. Nothing can reach a production host.

Disable service setup, organization/company attach/restore/import, arbitrary file access, backup configuration,
provider/feed/email/bank connections, outbound delivery, shell/plugin execution and administrative token/delegation
creation via the demo command policy and shared-core checks. Disable binary uploads and arbitrary document imports;
sample attachments come from the seed. Allow previews/printing of permitted synthetic records, without retaining
exports beyond the session. Demo role restrictions apply to every interface; blocked direct endpoints fail closed.
No endpoint takes an untrusted local path. User-entered free text remains possible for the accounting demonstration,
so do not promise the session contains no personal input: the service accepts no real company *source* and warns
against entry, keeps no durable backup of sessions, and removes transient text on expiry.

Default lifetime is 60 minutes absolute or 15 minutes idle, whichever is earlier. Controller enforces expiry even if
a sandbox is stuck or restarting: revoke routing and credentials, terminate/drain the sandbox, then queue cleanup.
Browser logout/end and Start over follow the same lifecycle; Start over creates a new identity, not a recycled root.
Controller restart does not extend expiry or revive expired sessions. Never reuse an old URL/ID. Cleanup is confined
to the controller-owned disposable directory with a verified marker and no symlink following; it cannot invoke
ordinary production `demo reset` or `trash empty`. Routine deletion applies only to these explicitly disposable
sessions, never existing owner books. Do not claim secure erasure of physical media from ordinary unlinking.

Default local demo capacity is 4 concurrent sessions, 1 GiB writable storage and 512 MiB memory per session, one CPU
quota, 2 concurrent requests and a 30-second request deadline. Refuse excess admission with retry timing rather than
evicting an active visitor. Rate-limit session creation (3 per source per 10 minutes, global 20 per hour), with the
same trusted-proxy-source rules as §2. Expiry and cleanup are enforced outside the tenant resource budget. Keep only
bounded operational events (opaque session ID, creation/expiry, resource counts and cleanup result) for 24 hours;
no request bodies, cookies, queries, names or raw IP addresses in retained logs. Changing limits/retention requires
host administration; public spending/retention commitments are K1. A 24-hour cleanup backlog blocks new admission.

## 8. Commands, authority and agent use

Proposed registry contracts, not installed commands. Every mutation takes `operation_id`, `reason`, expected
configuration/keyring version and, when required, an approval reference bound to the preview digest, target and
expiry. Return safe IDs, versions, state, audit ID, blockers and next commands. Retry the same operation ID for the
same result; changed input conflicts. Dry-run performs admission and preview, creates no key/secret or outside
connection, and reveals no inaccessible target. Approvals cannot be forged by setting an input boolean.

| Command family | Inputs → result | Authority |
|---|---|---|
| `security show/check` | Profile/visible scope → safe posture and blockers, never secrets | `security.read`; installation diagnostics require installation scope |
| `security configure` | Profile, origin, bounds, expected generation, preview approval → versioned configuration | `security.manage`, explicit installation grant |
| `cursor-key status/rotate/revoke` | Selected company, generation, key ID for revoke, routine/emergency intent → IDs and deadlines | `cursor.manage` explicit company grant; status may expose only readiness to ordinary report users |
| `storage protection check/plan/activate` | Approved mount/destination IDs, migration operation → coverage and resumable phase | `storage.manage`, installation scope plus approved migration; never formats devices |
| `backup recipient list/enroll/rotate` | Public recipient, proof/secret handle, expected generation → recipient IDs and proof status | `backup.manage`, explicit installation grant; company scope alone cannot replace installation recipients |
| `company backup`, `hub backup`, `backup verify` extensions | Existing scope plus protection policy/approved destination → encrypted artifact and verification status | Existing underlying scope plus explicit full-data export authority; a narrow reader cannot create a full backup |
| `company restore`, `recovery plan/verify/activate` | Source handle/digest, destination, recovery secret handle, copy mode → verified recovery and quarantine state | `recovery.manage` plus destination creation/restore authority; operation-specific approval for activation |
| `demo service check/start/stop` | Local service config/version → readiness/local URL/state | `demo.manage` explicit installation grant; external deployment still K1 |
| `demo session start/status/end` | Session bootstrap or admitted session → own expiry/lifecycle | Anonymous bootstrap grants only a disposable demo scope; no production authority |

Add capabilities at the current catalog tip without rewriting accepted layers. No implication from ordinary company
admin to installation filesystem/key custody. All security commands use shared core admission; CLI/HTTP/MCP/Python
and browser call it identically. OS volume unlocking is a separate local administrative operation with separate OS
access, not an interface-dependent business permission. Remote commands return a blocked prerequisite when that
local action has not occurred. Break-glass recovery requires OS/root custody plus recovery material and an explicit
owner recovery act; it never becomes a public “reset admin” endpoint.

Secrets and enrollment proofs use the protected transfer mechanism, with output-publication checks. Agent help names
required grants, approval needs and exact next steps. An agent can inspect blockers, prepare a migration/rotation,
execute an already approved act within its delegation, and hand recovery instructions to the owner; it never opens
an owner setup link, invents an external destination or sends secrets to a service. Human approval and technical
execution are separate recorded events. Existing `docs/agent-install.md` instructions that classify an act by actor
kind must be revised with the implementation to describe actual grants and approval boundaries.

Use existing permission/not-found/version-conflict/validation errors; add `E_SECURITY_NOT_READY`, `E_STORAGE_LOCKED`,
`E_BACKUP_KEY_UNAVAILABLE`, `E_BACKUP_AUTHENTICATION`, `E_RECOVERY_REQUIRED`, `E_RATE_LIMITED` and `E_DEMO_EXPIRED`.
Errors carry a safe next command and retry timing where applicable, never attempted secret text, inaccessible paths
or a distinction between unknown and inaccessible records. Wrong-key and corrupt encrypted payload share a safe
public refusal. Detailed local diagnostics require installation authority and remain redacted.

## 9. Operations and acceptance

The Security page shows local/trusted/wider profile, readiness blockers, cursor rotation dates, protected storage
coverage, latest backup/restore proof and recovery cutoff. It never displays private keys. At 390 px show stacked
status/actions; at 1440 px show coverage and recovery status together. Keyboard users can preview and confirm a
rotation; expired report pages offer Restart report. A locked root shows a recovery instruction, never an empty
company chooser that invites accidental reinitialization. Demo banner/expiry stays visible on every page.

Operator runbook: patch app/proxy/OS; inspect bounded authentication and cleanup events; verify backups daily; perform
an offline restore drill at enrollment, after recipient changes and every 90 days; inspect disk/capacity before
rotation/migration. No monitoring agent or notification service is enabled automatically. Incident response first
restricts network service, revokes affected credentials/keys, preserves local evidence, checks authorized activity,
then performs verified recovery if required. No automated ledger rollback. Old offline snapshots cannot distinguish
an attacker rewriting all local evidence: independent checkpoint custody remains necessary.

Acceptance requires evidence, not a “secure” checkbox:

1. A fresh local installation still works without an external account; wider startup rejects HTTP origin, exposed
   upstream, legacy policy, unsupported/locked storage, unbounded credentials and absent recovery proof.
2. Routine, emergency, restart and concurrent key rotation work across every cursor consumer. Old-key overlap never
   bypasses current permissions. Tampered, cross-subject/company/purpose, expired and restored-copy tokens fail.
3. Two users in one company with different grants cannot read denied facts through direct commands, field joins,
   counts, previews, cached reports, print, exports, audit, attachments, retries or streams. Revocation races stop
   publication. Equivalent effective human/agent rights pass the same cases on all five interfaces.
4. Cross-organization and cross-company failures disclose no hidden IDs/names/paths. Host or forwarded identity,
   Origin, missing CSRF, stored script text, path traversal and spoofed context cannot create authority.
5. Sustained and concurrent login floods hit bounded limits; nonexistent accounts behave alike; body/queue/report/
   transfer exhaustion fails before mutation. Password/session/bearer lifecycle and absolute expiry survive restart.
6. An offline image contains no readable book/attachment/checkpoint plaintext outside protected paths. Migration
   interruption never yields two writable roots. No lost mount produces an empty database. Residual old plaintext
   blocks wider readiness until an explicit disposition resolves it; physically removed owner-custodied plaintext
   media are recorded as excluded from the encrypted-media claim, never counted as encrypted backups.
7. Restore an encrypted backup on a clean machine using the independently kept kit. Independently compare ledger
   totals, audit anchors, attachments and permissions; distinguish company-only from full-root recovery. Wrong key,
   altered/truncated archive, insufficient disk, newer schema and interruption preserve both source and existing books.
8. Exercise every loss row in §6. No case silently removes a key, destroys ciphertext or calls an unrecoverable loss
   a successful reset. Rotation proves a new recovery path before retirement can be authorized.
9. Concurrent demo sessions cannot see/reach each other or any production sentinel, even with forged roots, stolen
   foreign cursors or direct forbidden commands. Egress/metadata access is denied at the OS boundary. Expiry, crash,
   restart, disk exhaustion and cleanup races cannot revive a session or delete outside disposable storage.
10. A fresh agent using actual MCP help can inspect posture, request a scoped rotation and recover a lost response
    without source reading or owner impersonation. Browser journeys at both widths explain blockers, key custody
    and demo expiry without exposing secrets. All new contracts have generated help/schema references.

## 10. Settled and K-questions

**Settled:** local default; explicit wider profile; existing loopback TLS proxy; permission parity across interfaces
and actor kinds with current principal intersection; fail-closed same-company projection inventory; versioned HMAC
keyring with 30-day rotation/15-minute cursors/16-minute verifier overlap; legacy cursor restart; restored-copy
incarnations; OS encrypted live storage plus age-encrypted portable archives; separate key classes and recovery kit;
no plaintext backup fallback; no credential resurrection on restore; synthetic isolated disposable demos; no public
sign-up or outbound services. These choices are implementation contracts, not repeated approval questions.

Only scope, expenditure, outside trust and irreversible disposition remain questions. Defaults permit local design
and build work; an unanswered question never authorizes external action. Record gates when the relevant build or
operation reaches that boundary; this documentation change does not operate a deployment or dispose of data.

- **K1 — Publication and hosting.** Where, if anywhere, may a wider host/demo be published, with which provider,
  audience, spending limit and public-session retention commitment? **Default:** local-only preview on owner-owned
  equipment, no provider/DNS purchase, no publication and no transfer of real books. Any public demo uses synthetic
  sessions only; approval of a demo never approves hosting real companies.
- **K2 — Outside key custody and unattended unlock.** May a KMS, managed secret store, escrow party or hosting
  provider hold keys or unlock the host? **Default:** none; local interactive unlock and owner-held offline recovery
  identities. No third party or network lookup is needed to restore. Availability after reboot waits for local unlock.
- **K3 — Identity services and public enrollment.** Add email sign-up, recovery mail, federated login, CAPTCHA or
  another identity/abuse service? **Default:** none; locally administered real-user accounts and anonymous expiring
  synthetic demos. No addresses collected or messages sent. Wider authentication includes the controls above;
  additional MFA/federation scope needs a separate design if required.
- **K4 — Additional encryption platforms or per-company cryptographic separation.** Require verified wider hosting
  beyond the initial Linux encrypted-volume backend, or application-level per-company keys/SQLCipher? **Default:**
  Linux wider-host verification plus portable encrypted backups; existing local platforms remain supported without
  an unsupported wider-readiness claim. OS/root access remains trusted, and companies share the host storage boundary.
- **K5 — Destruction and retention changes.** When may plaintext predecessors, old header/backup identities or
  retained archives be destroyed, and with what retention obligations? **Default:** preserve them securely, keep
  current approved retention only, and block destructive retirement pending an explicit inventory-specific decision
  and tested replacement recovery. No automatic last-key deletion or promise of secure physical erasure.

## 11. Build increments

S is a narrow slice, M a bounded workflow, L a cross-cutting security boundary. Each increment includes appropriate
synthetic demo fixtures, typed contracts/help, generated references and migration/permission review. No increment
ships a partial “wider ready” claim; readiness remains blocked until all required acceptance evidence exists.

1. **Deployment posture and transport (M).** Profile/configuration, preflight, proxy/origin/CSRF/cookie rules, secret
   redaction and response headers. **Done when:** a correctly configured local proxy can serve a wider-profile test
   host and every unmet prerequisite is a named blocker without enabling publication. **Tests:** Host/forwarded
   spoofing, missing/null/foreign Origin, login/setup/logout CSRF, bearer-only requests, cookie conflicts, local
   compatibility, stored-script/attachment cases and browser flows at both widths.
2. **Cursor lifecycle (M).** Keyring migration, shared envelope, consumer inventory, scheduled/manual/emergency
   rotation and restore incarnation hook. **Done when:** every report/deposit continuation uses one lifecycle and no
   legacy verifier remains. **Tests:** overlap boundaries, expiry/skew, malformed input, purpose/subject/company
   replay, concurrent rotations, restart, revocation/publication races, missing keys and copied/restored companies.
3. **Same-company authorization closure (L).** Complete surface/projection inventory, capability-based identity
   administration, cache and publication checks. **Done when:** narrow same-company readers cannot infer denied
   facts, and effective human/agent permissions agree across interfaces. **Tests:** adversarial capability matrix,
   connected-resource denial, counts/totals, audit/export/attachments, idempotent receipts, stale grants/epochs,
   stream revocation, self-escalation and direct Python/CLI/HTTP/MCP/browser equivalence.
4. **Authentication and resource bounds (M).** Durable bounded cooldowns, idle/absolute sessions, finite bearers,
   rotation handoff, request/queue limits and security event redaction. **Done when:** sustained abuse remains bounded
   and revocation survives refresh/restart races. **Tests:** known/unknown usernames, distributed sources, forged
   proxy address, login concurrency, absolute deadline, token refresh/revoke, floods and no secret-bearing logs.
5. **Protected storage and migration (L).** Linux volume verifier, complete path coverage, service temp/swap policy,
   resumable copy/verify/switch and lost-mount fencing. **Done when:** all live sensitive paths are covered, only one
   verified root serves, and leftover plaintext is explicitly tracked. **Tests:** mount/symlink escapes, unmount,
   permission failures, spill/WAL/temp coverage, disk-full, crash at every phase, independent totals and source
   preservation; provisioning/destruction stays outside automatic commands.
6. **Encrypted backup and recovery (L).** Recipient enrollment, encrypted archive/checkpoint envelope, schedules,
   company/full-root recovery, key rotation and recovery-kit proof. **Done when:** a clean-machine offline restore
   succeeds and each loss scenario gives the exact recoverability in §6. **Tests:** external plaintext scan, wrong/
   lost key, changed recipients, truncation/tampering, malicious archive, retention failure, restored credentials
   disabled, interrupted restore and an independent ledger/audit/attachment comparison.
7. **Disposable demo sessions (L).** Controller, per-session sandbox/identity, synthetic-only bootstrap, safe command
   policy, quotas/expiry and cleanup. **Done when:** local concurrent demos share no readable state, have no egress
   or production reachability and expire without affecting owner data. **Tests:** session/credential swaps, root/path
   injection, forbidden direct commands, network/metadata probes, two-session sentinel isolation, overload, kill/
   restart/cleanup races, symlink cleanup and no durable session backup.
8. **Operational acceptance and handoff (M).** Security/recovery screens, deployment and incident runbooks, agent
   install corrections and all acceptance evidence. **Done when:** §§2–9 pass, a fresh agent completes the MCP
   journey, and the owner can rehearse recovery locally without buying or publishing anything. **Tests:** full
   cross-interface negative matrix, 390/1440 px journeys, clean-machine recovery drill, supported encrypted-volume
   integration, demo isolation drill and rollback-to-loopback behavior; no production data or external deployment.

## References and adoption

Inspected 2026-11-10; documentation only, no executable downloaded. Security timings, limits, storage coverage,
permissions and operation protocols above are Bookflow design choices, not claims made by these sources.

- [age project documentation](https://github.com/FiloSottile/age) describes recipient encryption, identity-based
  decryption and the v1 format. Adopt the maintained local implementation and explicit recipient identities;
  reject remote/plugins and a homegrown cipher. Treat encrypted-input authenticity separately from author identity.
- [cryptsetup upstream header-backup manual](https://man7.org/linux/man-pages/man8/cryptsetup-luksHeaderBackup.8.html)
  states that a saved header and a passphrase valid for it can still decrypt the data after later passphrase removal.
  Adopt explicit header custody; never describe slot deletion as revoking all old copies.
- [cryptsetup upstream add-key manual](https://man7.org/linux/man-pages/man8/cryptsetup-luksAddKey.8.html) describes
  adding a passphrase-protected slot using existing unlock authority. Adopt add/test before retirement; reject
  automatic removal of the last recovery route. OS encryption is not an application authorization mechanism.
