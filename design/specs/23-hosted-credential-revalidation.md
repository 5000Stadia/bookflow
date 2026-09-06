# 23 — Hosted credential revalidation

The existing JSON HTTP/workbench command path authenticates before queueing but
does not revalidate the bearer or session credential inside command execution.
An admitted company update currently commits after the authenticating token's
revocation commits ahead of it. The deterministic disposable-host witness is
`/tmp/bookflow-queued-credential-witness.py`; its observed statuses are revocation
200, queued update 200, subsequent request 401.

Revalidate the original secret using the existing `hub.credentials.resolve_token`
inside the actual hosted command session, immediately before `dispatch.execute`.
For writes and advisory commands this happens inside the writer callback after
queueing. For reads and previews it happens inside the owned reader session.
Preserve the admitted token id, user id, token kind and principal binding: reject
any identity mismatch with a safe `E_UNAUTHENTICATED`, never silently switch actor
or principal. Existing dispatch checks current memberships and permissions.

Carry a private validation callback or equivalent nonserialized credential handle
from admission; never put the secret in business inputs, Context, error details,
logs or audit. Reuse the current verifier for expiry, revocation, inactive actors,
principal assignment, suspension and authority epoch. Do not add a second token
eligibility algorithm. Do not refresh credentials during this validation. Keep
existing cookie/CSRF/header precedence and session renewal behavior.

The check precedes command planning, preview, replay lookup and business writes.
Its failure uses the normal hosted session cleanup. Authorized self-revocation
still completes and returns its existing receipt; this prerequisite adds an
execution check, not a publication check after the operation's own effects.
No schema, registry command, business-output or default-permission change.

Verification uses disposable data and real HTTP requests with deterministic
barriers, not timing sleeps. Preserve the original failing bearer witness and
prove no queued update or associated company audit event commits after rejection.
Cover session-cookie revocation, expiry and agent-authority invalidation between
admission and execution; ordinary authorized write/read/preview and self-revocation
remain successful. Confirm a reader-side rejection releases reader ownership and
does not call the command plan. Run affected host/credential and agent/GUI handoff
regressions. Independently establish regression sensitivity using identical tests
on the frozen candidate and the unchanged pre-fix implementation, whose command
path lacks this execution check. Cover the focused failure classes and report
each baseline outcome separately; a baseline case that already passes is not
evidence for the added check. Verify the prior application source against its
commit and the copied test against the candidate. This historical comparison
does not alter authentication code or constitute mutation testing. Independent
code inspection and candidate regressions remain required; no full security or
broader publication-fence claim follows from this bounded evidence.

Implementation belongs in an isolated checkout; parent integrates after independent
review. Expected initial implementation and focused checks: 20 minutes, followed
by artifact review. Record measured phases and flag material overruns. The broader
MCP publication fence, transport, file handling and blind-agent acceptance remain
Row9 requirements; this prerequisite does not close or narrow that row or Row7.
