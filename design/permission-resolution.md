# Permission resolution and authority transitions

Status: reviewed policy contract for Row7; not implemented. Full implementation
requires the owning identity plan's storage, administration, publication, migration
and verification requirements. The membershipless hub-administrator visibility
conflict between intention and blueprint remains an unresolved user decision;
this contract does not settle that conflict or authorize dependent access changes.

## Company capability resolution

For a selected company, collect the actor's active organization membership for that company and active exact-company memberships. Keep the blueprint's highest applicable role rule for the mandatory command role threshold. Calculate default capability entries from that resolved role, preserving each registry resource's required-role threshold. Explicit capability grants and denies are exact registered names, never string prefixes or wildcard patterns. Union grants across applicable memberships, then subtract the union of applicable denies. A deny at either applicable scope wins over both a role default and an explicit grant. Company grants cannot override an organization deny. Organization grants cannot override a company deny. Memberships in other companies or organizations never participate.

Null grant/deny columns and empty lists both mean no override. Reject a capability appearing in both grant and deny in the same submitted policy rather than guessing user intent. Deduplicate names canonically or reject duplicates consistently in the typed schema. Unknown names reject; display canonical labels from the registry. An explicit grant never overrides the mandatory role threshold, active-user requirement, company visibility, principal binding, feature state or target graph authorization.

For a hub administrator, administrative visibility and role privileges retain their existing contract. The capability resolver uses the hub_admin default entries plus applicable explicit grants/denies. Administrative status does not bypass an explicit applicable deny on a business action. A hub administrator with no membership has hub_admin defaults, not an inferred grant to every future capability. Transaction-delete capabilities have no role defaults, including hub_admin. Delete uses the same explicit organization/company grant inheritance for all actors, including a hub administrator: an explicit organization grant covers current and future companies in that organization; an exact-company grant covers only that company. Applicable denies at either scope still win. Default-off refers to role/default/bootstrap seeds, not erasure of an already explicit organization grant when a new company is created. User setup and its preview must clearly label organization grants as applying to current and future companies. No administrator receives Delete solely by administrator status. The separate question of membershipless hub-administrator visibility is awaiting the user decision.

Effective company action authority for an agent is the intersection of its own resolved authorization and its authenticated bound human's authorization. Assigned principal sets compare all effective roles, scope visibility and capabilities using this same resolver. This may span more than currently visible companies; equality evaluation remains internal and its error presentation must not disclose inaccessible differences. User administration cannot depend on the actor guessing hidden scope names.

## Administration and Delete

User setup displays each capability as effective allowed/denied with visible provenance (role, organization override, company override); do not display a misleading independently effective company checkbox under an organization deny. Administrator edits are versioned and preview the visible before/after effect, suspended agents and token invalidation. Changes use shared typed commands and attributed hub audit, not direct GUI database writes.

Permission management remains a separate administrative authority; it does not require holding the target business action capability. In particular an authorized human administrator can explicitly grant Delete without first holding Delete itself. That delegation is bounded to the scopes/roles it can administer. It cannot grant hub administration or organization scope from company administration. System identities and the last active human hub administrator remain protected. Agents do not become user administrators by holding a user's password or by naming an on-behalf-of field.

Use exact stored transaction-family names for capabilities: transaction.journal_entry.delete, transaction.invoice.delete, transaction.sales_receipt.delete and transaction.payment.delete; add transaction.deposit.delete when its owning lifecycle exists. Public command nouns remain journal, invoice, sales-receipt and payment; capability spelling is not derived by blindly joining the displayed noun. No create/edit/void capability implies Delete. Creating or upgrading a company adds no role/default Delete grants. A previously explicit organization Delete grant applies to a newly created company through the same inherited rule as other capabilities, and setup labels this scope explicitly. User setup can assign each supported family separately. Exposing a permission does not count as implementing its transaction action: each lifecycle must have reviewed exact inverse, dependencies, history, number retention, permanent retry and closed-period behavior before a Delete button/command appears.

A permission change, membership revocation or user deactivation commits even when it leaves a shared-agent set unequal. In the same hub transaction, suspend every affected agent whose own/principal authority was reduced or whose assigned set became unequal, increment its epoch and revoke all tokens. Restoration never revives credentials. Reauthorization is explicit after equality and permitted-use checks; when old context held broader data, fresh isolated context acknowledgment is required as specified by design/specs/7-identity-isolation.md. No claim that revocation erases external memory.


## Transaction Delete admission

A Delete command requires mandatory role standard, its exact per-family Delete
capability and ledger.read, plus current visibility and actor/principal authority. The inverse postings
that implement that same authorized deletion do not separately require ledger.post;
otherwise denying ordinary edits would silently disable an explicitly granted
Delete. This does not confer permission to issue arbitrary journals, edits or voids.

A compound request that explicitly changes another document or unapplies a payment
requires that distinct action's existing authority as well. Dependency inspection
and immutable inverse attribution are not additional user-requested financial
actions. The owning deletion planner must enumerate the action graph; an inverse
helper must not infer capabilities from its low-level implementation function.
For example, standalone invoice Delete cannot silently unapply a receipt, and a
coordinated Delete-plus-unapply requires both invoice Delete and existing payment
unapply authority. Linked work release still requires its established customer-work
authority. These predicates apply equally to preview, execution and permanent replay.

Required witnesses: standard user with invoice Delete granted, ledger.read allowed and ledger.post
denied can delete an otherwise eligible invoice but cannot edit it, post a journal
or void it; readonly plus Delete grant still cannot delete. A dependent payment
unapply request with ledger.post denied fails atomically without deletion; independently
permitted unapply followed by deletion works. Invoice Delete never grants payment
Delete. Agent/principal intersection and newly applied denies remain effective on
all these paths. Full Row7/deletion implementation-plan review remains required before code.


## Delete disclosure

Delete requires ledger.read in addition to standard and the exact family Delete
capability. Confirmation, preview, execution, permanent result recovery and deleted
history disclosure must all meet their complete current resource predicates. An
explicit ledger.read deny therefore blocks this Delete workflow before target
facts are returned or any accounting/history mutation occurs. Delete does not
create an implicit exception to a denied read capability. The previously specified
ledger.post independence remains: with ledger.read allowed, a standard user granted
invoice Delete but denied ledger.post can delete an otherwise eligible invoice;
they cannot edit, void or post arbitrary journals.

This is the primary resource conjunction, not permission to browse unrelated
resources. Retain customer-work and every other established historical/current
source dependency. A formerly linked invoice or payment does not shed historical
work authority after void or full unapply. Independent show/history/attachment
commands enforce their own complete current predicates. No retry key substitutes
for authorization. Because dispatch can invoke permanent recovery before ordinary
input authorization, the deletion recovery path must explicitly enforce the same
full action/resource graph before returning an original result.

A Delete-plus-unapply request retains the separate unapply ledger.post requirement.
Failure anywhere in the conjunction rejects the whole compound action. The UI
must explain the missing permitted capability without exposing hidden graph facts;
setup shows Delete as ineffective while required read access is denied.

## Canonical authority signature

Compare authority as a policy function, not raw membership JSON, the number of
permissions, current documents, feature activation or closing-date eligibility.
For each applicable scope, retain separately (1) effective visibility and (2)
resolved role, and compare (3) admission bits for each supported registered
capability at its declared mandatory role thresholds. An admission bit means the
scope is visible, the actor is active, the resolved role meets that threshold and
the capability survives defaults/grants/denies. The catalog must distinguish known
capabilities, threshold requirements, defaults and actual command availability.
Action graphs are conjunctions of these bits, never grants manufactured by a
helper. Complete registry/resource coverage is a prerequisite for this comparison.

For company scope, evaluate every existing company and one symbolic future company
per organization: it inherits organization policy and has no exact-company
override. Include the separately defined organization/hub administrative actions
in their proper scopes. The symbolic scope is an internal comparison witness, not
a new company or an externally disclosed name. It distinguishes an organization
Delete grant from company-only grants covering all companies that happen to exist
today. Thus two organization-standard humans with those different policies are
already unequal for shared-agent assignment even before another company is created.

Explicit grants below an unmet role floor are not admitted capabilities. Two
readonly humans with identical visibility/roles remain equal when only one has an
otherwise unusable Delete grant; removing that grant alone is not an effective
reduction. Raising a role, changing company visibility, activating an account or
changing scope structure recomputes the complete signature: a previously latent
grant can then produce inequality and require suspension atomically. Separately
compared role reductions still count even if the admitted business bits happen
to remain the same. Disabled command features or an empty company do not erase
capabilities from the signature.

A reduction is loss of any previously visible scope, decrease of a compared role,
or any admitted capability changing true to false, with principal deactivation
and agent-own reductions retained. An increase elsewhere never cancels a loss.
Evaluate every assigned human before intersecting with the agent's own authority;
a human's loss suspends the agent even when the agent already lacked that action.
An increase without any loss also suspends if assigned-human signatures become
unequal. Otherwise a redundant membership edit or removed ineffective grant does
not manufacture an epoch change.

Scope creation/attach, permission-catalog/default changes, role/membership edits,
principal binding changes and activation changes must recompute affected signatures
and perform required suspension/epoch/token changes in the same authoritative hub
transaction before publication. The future-scope comparison is additional coverage,
not a substitute for this transactional check. Error/UI presentation must not reveal
inaccessible scope differences. Hub-admin visibility bits remain parameterized by
the user's pending governing choice; this contract resolves neither branch.

Required independent witnesses include read-denied Delete preview/write/replay;
read-allowed/post-denied standalone Delete; historical-work authorization after
unapply/void; readonly latent-grant equality/removal; role increase exposing that
grant; current-company equality with unequal future inheritance; net increase plus
one capability loss; any assigned-human loss under a narrower agent; and atomic
company attach/catalog changes. These are implementation-plan obligations, not passing tests.

## Assignment eligibility transition

Policy signatures are not the entire transition predicate. For each affected agent,
compare the old and proposed active assigned-human eligibility sets as well. Loss
of any previously eligible binding is an authority reduction in its own right:
removing G→P, replacing P with equally authorized R, or deactivating an assigned
principal suspends G, increments its epoch and revokes every G token atomically,
including tokens bound to otherwise unchanged Q. Per-token assignment refusal
alone is insufficient. Surviving-set equality cannot cancel this loss. Compare
policy changes for the union of old and proposed assigned humans, including P
when removal and a permission reduction are requested together. Newly added humans
must meet current eligibility/equality and explicit authorization requirements;
adding one never repairs an already suspended agent or resurrects prior tokens.

Exact no-op assignment writes do not manufacture an eligibility loss. A redundant
organization/company membership edit with identical effective policy and identical
eligible binding set retains the earlier no-suspension rule. P/Q removal, equal
replacement P→R, and simultaneous P removal/permission loss must all produce
agent-wide suspension/epoch/all-token effects before any publication; restore
requires explicit reauthorization and the existing fresh-context acknowledgment.
