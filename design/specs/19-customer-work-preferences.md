# 19 — Customer-work preferences

## Boundary

Complete the estimate/progress preference portion of CW09 with company settings,
shared command behavior and browser controls. Preserve all work/sales documents,
source links, exact allocations, permanent conversion receipts and authority.
No job-status label editor, default item markup, billable time/expense preferences,
print suppression, template editor, payments, inventory or delivery is introduced.
Duplicate document numbers retain hard rejection; no preference permits duplicates.

The supplied Missing Manual printed661–662 describes separate estimate and progress
preferences and making estimates inactive after invoicing when progress is off.
These are workflow preferences, separate from the hub's future licensed features.
Bookflow also supports paid receipts, work orders and immutable corrections; their
explicit behavior is defined below, not inferred from the reference.

## Company settings and compatibility

Add strict, non-null boolean company_info fields:

- estimates_enabled defaults true.
- progress_billing_enabled defaults true.
- close_estimates_after_billing defaults false.

Expose all three as creation-time inputs on company new/rollout and its browser
form, and on company show and company update, retaining the current admin
requirement, info_version concurrency, audit provenance and merge/blind-write rules.
No separate command family or source of truth is added. Explicit null rejects;
omission preserves a saved setting. String/number booleans reject in typed JSON.
Company co0013 appends these fields with defaults on populated and fresh paths;
existing rows, local columns/constraints/objects and prior migration witnesses
remain intact. Upgrading does not alter any document, availability or allocation.
New-company rollout and both demo seeds default to today's behavior.

The estimate and progress settings are independent. A business can use work-order
progress billing while estimate creation is disabled. close_estimates_after_billing
is saved independently but is effective only while progress_billing_enabled is false.
Changing preferences never closes/reopens or bills an existing document by itself.
Enabling progress again makes automatic closure dormant without forgetting its
saved preference. Company show exposes the stored booleans; help and browser label
the effective condition clearly without another stored flag.

## Shared command rules

Current role, company isolation and composite source/sale authority always apply
before any preference-related information. Disabling a preference grants no access.
Preference checks are in the shared work/billing services, not browser-only logic.

When estimates_enabled is false, reject new estimate create/copy and any conversion
whose destination is a NEW estimate with E_FEATURE_DISABLED, details feature=
estimates, setting=estimates_enabled and enable_command=company update. Existing
estimate show/query/history/source/file access, corrections, acceptance, availability,
conversion into work orders and financial billing stay available under their usual
rules. These settings disable initiating a workflow, not access to existing records.
Existing proposals and work orders remain available. Returning an authorized matching
committed operational conversion is allowed even when new estimate creation is off.

When progress_billing_enabled is false, omitted selection bills all remaining
eligible work. Existing line_ids remains allowed and bills the complete remaining
scope of each selected line; it also supplies the bounded completion path when too
many roots would exceed the conversion's2,000-span limit. It never selects a new
partial quantity or percentage within a root.

Explicit percent or ordinary selections reject E_FEATURE_DISABLED with feature=
progress_billing, setting=progress_billing_enabled and enable_command=company update.
The single exception is explicit bounded recovery: a selections entry with only
line_id and net_amount is allowed when that source root currently has more than200
free spans and the requested positive net is EXACTLY the core's current
recommended_net_amount (sum of the earliest at-most200 positive-capacity free spans).
Every entry in such a disabled-progress selection must satisfy this rule. The core
recomputes eligibility and the recommendation at preview and commit. Stale
recommendations reject rather than being replaced or clipped. A caller never
supplies raw spans or a trusted recovery flag. The usual200/2,000 bounds still apply;
select fewer recovery roots if the conversion-wide bound would be exceeded.

BillingLineOutput adds requires_bounded_recovery:boolean and
recommended_net_amount:MoneyOutput|null, non-null only when recovery is needed and
positive net remains. These are current-state instructions, not reservations. The
browser offers an explicitly labeled bounded-recovery choice with the exact suggested
net and selected root; it explains that additional bills may be needed. No silent
partial posting is introduced. Ordinary partial quantities/amounts/percentages and
exact rebill selection require progress to be enabled. After existing installments,
remaining selected lines and bounded net recovery provide a finite completion path
without changing company settings. Zero-charge physical leftovers stay visible and
cannot cause a zero-total financial posting.

Matching authorized committed conversion replay returns the existing current sale
before new-work preference validation and remains a no-op. A changed request under
an old key still rejects key reuse. Corrections, releases, voids and source reads
retain their existing behavior while progress is disabled, including receipt-total
confirmation and consumed-source protection. A new exact partial rebill requires turning
progress back on; remaining-work and bounded-recovery bills remain available. An invoice correction
cannot grow captured source entitlement under either setting.

BillingOutput adds preferences:WorkBillingPreferences with exactly the three
stored strict booleans above plus auto_close_effective:boolean equal to
!progress_billing_enabled && close_estimates_after_billing. This is company policy,
not a source outcome. It also adds closes_on_remaining_bill:boolean: true only for
an eligible active accepted estimate, positive chargeable remainder, and effective
auto-close policy. Work orders always report false. This describes billing ALL
remaining work; selected-line/recovery previews calculate their actual outcome.
Existing can_invoice/can_sales_receipt continue to describe remaining-bill eligibility.

SalesWriteOutput adds source_effect:WorkBillingSourceEffect|null. A linked conversion
uses {source_id:str, source_kind:estimate|work_order, version_before:int,
version_after:int, active_before:bool, active_after:bool, automatically_closed:bool}.
This is the immutable source change of that conversion. Initial preview/result uses
the prospective/committed change; permanent replay reconstructs the ORIGINAL effect
from its stored source revision and the associated immutable conversion revision,
never today's preferences. Version_after is version_before+1. Legacy conversions
have automatically_closed=false. Ordinary sale writes leave source_effect null.
It also adds source_current:WorkBillingCurrent|null with {source_id:str,version:int,
active:bool,status:str}; initial preview/result uses prospective state, while replay
reads current authorized source state, including later manual reactivation. These
are distinct so historical closure is never reported as current availability.

Dry-run computes the same complete prospective source/sale aggregate and source_effect
as execution, including active=false when closure applies, but persists nothing and
reserves nothing. New financial-conversion fingerprints include the following exact
preference projection: {progress_billing_enabled:<stored>, auto_close_effective:
<company effective policy AND source kind is estimate>}. Estimates_enabled is
excluded because existing-source billing remains available. The dormant saved close
setting is excluded while progress is on and for every work-order conversion.
Operational conversions INTO a new estimate include only estimates_enabled;
other operational conversions include no preference members. Ordinary work/sale
updates retain their existing fingerprint dependencies. Fingerprint serialization
omits these new members on legacy permanent-request hashing: preferences never
become part of client intent or change existing key identity.

Rechecking a still-enabled command against changed relevant preview facts returns
E_PREVIEW_STALE. Disabled estimate creation and disabled percent/quantity/rebill modes
return E_FEATURE_DISABLED before financial fingerprint validation. Disabled-progress
net-only selections are recovery candidates and have a separate deterministic rule:
if their current span count/recommended net no longer qualifies and an
expected_facts_fingerprint is supplied, return E_PREVIEW_STALE with both authorized
consumption_changes and preference_changes; without a fingerprint, return
E_FEATURE_DISABLED explaining the exact current recovery constraint and recommendation.
Do not call the former a preference-disable event. An opaque fingerprint does not
prove that a request was previously valid, nor reveal whether preferences or
consumption changed; these details are latest relevant facts, not an asserted diff.
This recovery-candidate rule applies even when progress was just disabled, avoiding
an unverifiable guess about the caller's previous settings. If recovery still
qualifies, compute its complete current fingerprint and compare normally. The shared
service performs this failed-recovery check before ordinary selection resolution can
replace it with a range/dependency error. Source version and current authority retain
their earlier precedence. A received-total
mismatch is checked only after eligibility/version/preview facts.

Both preference-caused errors carry preference_changes:list of at most3 objects:
{field:str,company_id:str,audit_event_id:str|null,updated_by:str,updated_via:str,
on_behalf_of:str|null,seconds_since_update:int}. Include only the settings relevant
to that operation's fingerprint/gate, deriving the latest actual edit of each field
from authorized company audit changes, not the company's last unrelated edit. If a
setting has never been edited, identify its creation provenance and a nullable audit
ID if no per-setting creation event exists. These are explicitly latest relevant
preference changes, not an exhaustive diff since an unknown preview. Existing billing
consumption_changes remain separate and can accompany them. Browser renders the same
attribution and never leaks inaccessible organization, company or admin records.

## Automatic estimate closure

When progress is off and close_estimates_after_billing is true, a successful NEW
remaining-work financial conversion directly FROM an estimate that leaves NO
positive billable source net unallocated makes that estimate
inactive in the same transaction as the sale and source version/revision already
created by conversion. Applies to invoice and paid sales receipt. Preserve accepted
status, accepted revision/evidence, quoted facts and all root identities. Closure
here means active=false, not declined, fulfilled, paid, deleted or a new status.
The financial destination retains its ordinary status and exact posting facts.

Do not create a second source revision or independent write for closure. The existing
same-facts conversion revision has the single permitted active true→false difference;
its validator reconstructs the condition from current company settings, source kind,
selection and exact remaining work. Its audit/source output identifies active as
changed in addition to billing consumption. A mismatching pending closure, omitted
required closure or spurious status/acceptance change rejects before persistence.
Fault injection verifies this independently rather than trusting the resolver flag.

A selected-line or bounded-recovery bill closes its estimate only if it consumes
the final positive billable net across ALL current roots. Earlier partial bills
never close it. A work-order conversion never changes upstream estimate availability. A conversion
into a work order never closes the estimate under this financial preference. A
zero-total conversion still cannot post or close anything. Remaining uncharged
physical scope and excluded nonbillable lines do not establish physical completion;
the preview explains inactive-after-billing separately from those quantities.

Failure, conflict, closed period and mismatched received amount roll back all sale,
source, closure, conversion-key and audit effects. Committed retry creates no later
closure effect, even if preferences changed. A later sale correction/void releases
scope but does not silently reactivate the estimate. Its billing page shows released
work and that the inactive estimate must be explicitly reactivated before rebilling.
Current permissions, accepted status and dependency guards still govern reactivation.

## Browser and evidence

Company settings group these three labeled controls with the enable/disable effects
above. When estimates are disabled, hide only new-estimate shortcuts and actions whose
DESTINATION is a new estimate (create/copy/proposal-to-estimate). Keep existing-estimate
invoice, receipt and work-order conversion actions under their usual permissions; retain searchable existing estimates and source/history links. On a direct
blocked route show the shared error and company settings link only when authorized.
Progress-disabled conversion forms offer all remaining work, selected remaining
lines and the explicit bounded-recovery choices above, while explaining why other
partial controls are unavailable. Do not emit disabled optional fields as null or silently
reinterpret a previously entered partial request. Preserve inputs on rejected writes.

Only when source_effect.automatically_closed is true does preview say this bill
makes the estimate inactive. Intermediate selected/recovery bills explicitly retain
active availability. Saved results describe that conversion's source_effect separately
from current availability read from source_current, including replay after manual
reactivation. Show accepted decision history, sale links and an authorized reactivation
action only when source_current is inactive. Do not label a work order
complete or an invoice paid. Retain desktop/phone containment and keyboard access.

Both seeds append isolated preference demonstrations after unchanged prior prefixes:
turn settings off, demonstrate remaining billing with automatic estimate inactivation,
show its retained acceptance/source history, void the financial example, explicitly
reactivate, and restore all settings. All new financial examples end voided; old
balances, reference net results, records, files and history remain unchanged.
Generated command/schema docs and examples state the booleans, errors, retry ordering
and closure semantics; no full licensed-feature or granular-permission claim.

Verify default compatibility and populated migration/local objects; strict/null inputs;
admin/version/readonly/isolation; independent estimate/work-order choices; every gated
command family and all available existing-history/correction paths; old/new-key retry
after disable; source/proof preservation; required and forbidden automatic closure;
atomic injected failures; source-version and preference-preview conflicts; closed dates;
void then explicit reactivation and rebill; actual CLI/HTTP and1280/390 browser flows.
Independent review covers schema, closure atomicity, accounting/source validation and
authority. No production implementation precedes review of this plan.


## Comparison and transport boundary

| CW09 control | Reference observation / chosen behavior | Completion evidence |
|---|---|---|
| Create estimates | Printed661: separate enable choice controls new-estimate entry. Compatibility defaulttrue; existing workflows remain accessible. Reference defaults are not established by the cited prose. | Settings and disabled/new/existing estimate screens compared at1280/390. |
| Progress invoicing | Printed661–662: separate enable choice for quantities/amounts/percentages. Compatibility defaulttrue. Selected remaining lines and bounded recovery retain existing exact-history completion. | Enabled/disabled forms compared; finite fragmented completion witness. |
| Close after invoicing | Printed662: inactive after invoicing; control dormant while progress enabled. Compatibility defaultfalse; paid receipts extend the same rule, work orders do not close upstream estimates. | Preview/inactive result/reactivation and stored dormant setting compared. |
| Duplicate numbers | Printed662: warning preference. Bookflow's uniqueness contract retains hard rejection and offers no bypass toggle. | Duplicate-number rejection from form and shared command. |
| Suppress zero print rows | Printed662: output-template preference; remains print-template dependency. | No nonfunctional switch or print-parity claim. |
| Job labels, item markup, billable costs | Existing CW09 master/source settings dependencies retained. | Explicitly staged in their owning inventory; no claim of completeness for those screens. |

Actual MCP transport remains Row9. That acceptance must cover these settings, gates,
preview attribution, closure and replay; registered schemas and interface attribution
checks do not prove MCP transport parity. Row19's completion claim is limited to this
CW09 sub-boundary and its deliberate compatibility/integrity extensions.
