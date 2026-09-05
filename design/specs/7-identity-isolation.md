# Row 7

Target: row 7 in `design/intention.md`; product contract: `design/blueprint.md`
sections 4, 5 and 7.

## Foundation increment

Hub revision `hub0009` adds the nullable token `authority_epoch`, assigned human
principal sets, and per-agent authority epochs and suspension state. Company head
remains `co0006`. Revision-local DDL, constraints and conversion SQL are frozen.
Human identity, tokens, memberships, passwords, configuration and company history
survive upgrade. Existing agents receive suspended epoch 1 authority, without
inferred assignments; every existing unrevoked agent credential is revoked.

The security conversion records one system audit event and safe per-agent/token
entries in the same migration transaction. No secret/hash is copied into these
snapshots. A fresh database without legacy agents needs no conversion event.
Failure rolls back conversion and its audit together. A process interruption after
migration commit and before ordinary migration reporting cannot lose or duplicate
the security event.

Shared credential validation requires an active actor. An agent token additionally
requires an active human principal, active assignment, unsuspended authority and
an issuance epoch equal to the current epoch. Existing `token issue` validates
these conditions before issuing and stores principal and epoch atomically with
the token hash. Human credentials have no principal or authority epoch. Invalid
credentials return an authentication error without disclosing hidden identities.
Issuance errors expose only what the authorized issuing human may administer.

This increment exposes no membership mutation, principal assignment or agent
reauthorization commands. Legacy agents remain suspended until the subsequent
explicit authorization workflow is available. Tests create authority state directly
only to exercise credential invariants. Existing actor-attribution witnesses gain
explicit eligible assignment fixtures. Ordinary human demo login remains usable.

Witnesses cover hub0008 conversion and backups, fresh/upgrade schema equivalence,
frozen DDL, rollback on audit failure, interruption after conversion commit,
null/stale/revoked/expired credentials, inactive actor/principal, missing/revoked
assignment, suspended authority, renewed issuance refusal, valid bound issuance
and preservation of human credentials. Generated schema/command documentation
and current-head expectations are updated with the implementation.

## Identity and permissions integration

Typed user, membership and agent-principal commands use shared hub services and
normal dispatch version, reason, dry-run and audit contracts. User lookup preserves
NFC/casefold matching and stored spelling. System identities and the last active
human hub administrator are protected. Membership administration resolves visible
scope before target identity and cannot grant a role above the administrator's
own authority. Agent ownership conveys no inherited membership or administration.

Effective permission equality applies to assigned humans; the agent may have a
narrower grant, and execution requires the intersection of agent and bound human
permissions. Role thresholds remain mandatory even with explicit capability grants.
Cross-scope conflicting capability overrides remain a pending product decision;
the resolver and dependent command APIs are not frozen before that decision.

Every authorized authority reduction and every change leaving an assigned set
unequal atomically suspends affected agents, increments their epochs and revokes
all their tokens in the initiating hub audit event. Reduction is never refused
merely to preserve equality. Restoration does not revive credentials. Explicit
reauthorization checks current equality and permitted use; narrowed/split contexts
require acknowledgment of fresh isolated execution context, without claiming to
erase external agent memory.

## Execution, publication and isolation

Internal authenticated bindings travel with queued work separately from caller
context JSON. Current token, actor, principal, assignment, epoch and command access
are checked at execution. OS authentication preserves verified login mapping;
CLI/Python token mode fixes the principal at issuance.

A host publication admission protocol orders each bounded response frame against
authority commits. It validates fresh authority immediately before release and
provides bounded cancellation and acknowledgment for each individual ASGI send,
including headers and ordinary JSON/HTML, SSE and binary output. It does not hold
a thread mutex across an await, wait for a writer while admitted, or make a
revocation depend on whole-response cleanup. Already released transport bytes
cannot be recalled; application-buffered data is dropped after revocation. Local
ready/body/final frames follow the same rule. Transfer resource ownership remains
separate from authorization. Deterministic barrier tests exercise revocation under
backpressure, before dequeue, after a read, between frames and after upload staging.

All lists, counts, reference suggestions and audit/activity projections filter
permitted scope and source capabilities before pagination. Continuation state
reveals no global sequence or hidden change signal. Query freshness tracks only
authorized query dependencies; a denied area's write cannot stale an unrelated
permitted query. Permission/epoch changes invalidate authority-bound cursors.
Hub audit event identifiers and tenant-visible summaries reveal no hidden event
counts or compound suspension details.

Workbench controls consume the same command permission decisions as dispatch.
Demo identity examples use dedicated identities with explicit retention scope;
reset preserves unrelated users and OS mappings. Cross-organization, sibling-company
and same-company denied-area isolation use disposable fixtures. Broad visual
redesign and later ledger/MCP behavior remain separate specs.
