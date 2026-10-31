# Customer discounts, memberships and promotions (R188)

Design contract, 2026-10-31. R188 is approved for design. Commands/tables introduced here are proposed, not claims
that they already exist. This extends R110 pricing and R147 sales adjustments through the shared sales core, including
the pre-tax POS seam in [point-of-sale.md](point-of-sale.md). Commission consumes the final line nets under
[commissions.md](commissions.md). Neither feature requires the other to be enabled.

**Outcome.** The customer record says which membership prices and offers a customer can use. An invoice, sales
receipt or POS cart explains its selected prices, discounts, gifts and tax. Codes, a membership price and a
buy-X-get-one-free offer work without a loyalty points ledger or marketing suite. Correctness is independently right
line totals, discounted tax, stock and balanced ledger entries, not a screenshot matching the anchor.

## 1. The anchor, prior work and boundaries

The anchor supports customer and per-line price levels, fixed-percent and per-item prices, visible Discount items,
and subtotal/percentage-charge ordering. Its enterprise guide describes date/quantity/customer conditions and
multiple or exclusive pricing rules. Price levels adjust the unit price; Discount items visibly reduce a sale.
Keep both distinctions. The referenced pages do not establish a customer membership enrollment, coupon-redemption
ledger or the same-sale free-item contract below; those are Bookflow additions.

The existing implementation is the starting point, not a second pricing engine:

- `pricing.py` stores fixed-percent and per-item price levels, their rounding and fractional prices.
- `pricing_rules.py:line_price` chooses price-level/rule candidates. `lowest_price` is default: ties prefer the
  price level, then rule priority; `priority` chooses the first matching rule. Rule order is priority, name, ID.
  Conditions include item/category, customer/type, class, header rep, transaction date and base-unit quantity.
  The current `rule_price` deliberately skips zero/negative results: a 100% R110 rule is not a free-gift mechanism.
- `pricing_rule_models.py`, `sales_defaults.py` and sales snapshots preserve rule identity, version and price facts.
  Explicit prices and already allocated billing have their existing meaning; do not replace them from current rules.
- `sales_adjustments.py` applies manual subtotal, discount and percentage-charge lines. It apportions a discount
  across current nets with largest remainder, earlier line first. For mixed taxable/exempt targets, a taxable
  discount reduces taxable sales by its whole amount, limited to remaining taxable bases. Commercial shares and
  taxable reductions are different facts. See [R147](specs/r147-sales-line-kinds.md), including its zero-tax floor.

R188 introduces no gift-card tender, stored value, points currency, automatic membership subscription billing,
customer messaging, price scraping, or external marketing connection. K1 covers those scope extensions. It uses
the company's supported currency and sales-tax policy; foreign-currency tax expansion remains R127.

## 2. Layers, customer attachment and membership prices

Company switches `membership_pricing_enabled`, `discount_codes_enabled`, `promotional_offers_enabled` and
`promotion_stacking_enabled` all start false. The first three work independently. Stacking requires codes or offers
but no membership. A business can keep its existing price levels/manual discounts forever, turn on just codes, or
use membership pricing alone. Off means no new surface or automatic effect; historical views/returns/reversals keep
their facts. A code submitted while its layer is off refuses with `E_FEATURE_DISABLED`; it is never silently ignored.
Disabling a layer lists affected open/held carts in dry run and invalidates their previews. Completed sales do not
change. Company-admin permission is required to change switches; switches grant no authority.

**Membership plan.** A named, versioned plan references an existing price level. A customer enrollment records plan,
start/end dates (inclusive, company-local dates), active/suspended/ended status, and optional member number unique
within the company. No payment or renewal job is implied. On the customer list a Membership column/filter appears
only when enabled; its customer page has Membership and Offers panels with enrollment history and usage links.

At most one effective enrollment per customer: overlapping active intervals refuse. A job uses its own effective
enrollment if present, otherwise the nearest ancestor's; an explicit “no inherited membership” job flag suppresses
inheritance. The sale captures sold-to customer and the membership entitlement owner; each applied benefit separately captures
its promotion and code owners as defined below. Per-customer limits apply across the applicable owner's jobs, not
once per job. Parent/job merges must preserve these identities and combined usage.
Membership cannot be assigned to the register's anonymous walk-in identity. A named customer must be selected.

**Precedence, settled.** An explicit line unit price remains explicit. An explicit line/document price-level
selection remains explicit. Otherwise the effective membership's price level replaces the inherited customer price
level for candidate generation; it does not discount that already-discounted price again. R110 then runs normally:
under `lowest_price`, compare that candidate with matching rules; under `priority`, the first matching rule wins.
With no effective membership use the old resolver unchanged. A membership level with no price for the item falls
through the existing price-level/item behavior. Multiple memberships do not stack. The preview reports the member
plan/level even when an R110 rule wins, with a reason for the losing candidate. Neither enrolling nor expiring a
membership edits the customer record's ordinary `price_level_id` or changes saved invoices.

Example: item $100, customer level $95, member level $90, R110 candidate $85 → $85 under lowest-price. If the first
priority rule yields $92, priority policy gives $92. Explicit $93 stays $93. A subsequent eligible 10% coupon may
discount that final $93 to $83.70, unless the offer excludes explicit-price lines; the default is to include them.

**Customer offers.** A promotion's audience is exactly one of `all_named`, `selected_customers`, `membership_plan`,
or `public`. Selected-customer attachments are dated rows, optionally including jobs (default true), visible from
both the customer and promotion. Membership audience uses the effective enrollment above. Public offers can be used
by walk-ins; a per-customer limit always requires a named customer, even on a public offer. An anonymous customer
never gets all_named benefits because the core stores a walk-in record. POS supplies the separate `customer_named`
fact; ordinary receipts must likewise distinguish that designated identity.

**Usage owner per benefit.** For a membership-audience promotion, the owner is the customer whose enrollment
qualified it. For selected_customers it is the nearest matching dated customer attachment (the job itself first).
For all_named/public it is the top-level named customer; anonymous public uses have no owner and cannot use owner
caps. An owned code uses its explicit owner for its own cap; an unowned code uses the promotion owner. Both owners
are captured on the redemption, and their respective caps are checked independently. Thus a job with its own
membership can consume a parent-owned code without obtaining a new code allowance. Stacked benefits may have
different owners; no single sale-level owner substitutes for their captured identities.

## 3. Offers and codes

A `promotion` has name, customer-facing label, state `draft|active|paused|retired`, immutable published revision,
audience, inclusive start/end dates, currency, allowed sale kinds, item/category filters, minimum eligible base-unit
quantity and/or eligible net subtotal, priority (lower first), optional total-use and entitlement-owner caps (null means unlimited, otherwise positive
integers), and one benefit:

| Benefit | Fields | Result |
|---|---|---|
| Percentage discount | percentage 0–100; optional maximum discount | Discount against eligible remaining nets |
| Fixed discount | positive minor-unit amount in company currency | Up to that amount, capped at eligible remaining net |
| Buy X get one free | positive integer X, qualifying item, reward item, repeat flag (default false) | One additional reward unit per qualifying block, subject to §5 |

Conditions are ANDed; an empty item/category filter means every ordinary sale item, excluding charges unless
`include_charges` is explicitly enabled. Negative lines, subtotals, discounts and allocated progress-billed lines
are never eligible. Groups are tested by expanded member identity. Quantity uses base units; fixed-unit gifts require
integer qualifying base units (fractional quantities below the next whole unit earn no additional block).

Each promotion chooses a normal Discount item with an active discount account and tax code. It may select a taxable
or non-taxable discount; this is captured with the revision. Configuration shows a mixed taxable/exempt example.
Activation requires that explicit choice; the software does not infer a legal tax treatment from the word “free”.
The account/tax semantics are the same as a manually entered discount. Customer exemption and captured document tax
policy still apply. No new tax-law engine or jurisdiction lookup is implied (K2).

`trigger: automatic|code` is explicit. Automatic requires the offers layer. Code requires the codes layer, and links
to a `discount-code`: canonical ASCII uppercase letters/digits/hyphen, length 3–40, trim surrounding spaces; reject
other characters. A code is unique company-wide forever, including retired codes, and points to one promotion.
Each code has active dates within the promotion dates, optional total-use cap and per-customer cap (null means
unlimited, positive integers otherwise), and optional single-customer owner. An owned code requires that customer
or its included jobs; it does not grant membership. A sale accepts one explicit code by default; a code can activate
only its linked benefit once per sale. Customer-specific codes appear on the customer page, public ones in the
promotion editor. Codes are discount identifiers, not payment instruments or bearer access to private customer data.

## 4. One deterministic calculation

Every sale preview and post uses these stages in the domain core:

1. Expand groups and resolve captured item, unit, site and quantity; select membership price-level inputs and run
   existing R110 defaulting. Retain explicitly supplied rates and fixed allocated-billing facts.
2. Calculate the original ordered sale, including manual discounts, subtotals and percentage charges through R147.
   These amounts form the pre-promotion snapshot. Later generated discounts do not alter the base of a manual
   percentage charge retroactively. Manual discounts are neither silently removed nor converted into offers.
3. Evaluate every eligible offer against that pre-promotion snapshot and customer/date facts. Minimum spend excludes
   tax, generated rewards and discounts generated by this pass. It includes manual discounts already applied.
   Quantity thresholds ignore reward quantities and negative/returned quantities. No recursive qualification.
4. With stacking off choose at most one offer across the sale. Evaluate each candidate through final tax calculation
   and choose the greatest reduction in customer amount payable compared with the same basket without the offer.
   A gift is compared using its ordinary resolved reward price less its discount, including any extra tax: free
   item value is not a cash saving. For equal payable reductions prefer greater reward net value before discount,
   then lower priority, normalized name, ID. Offers that increase customer payable are offered explicitly with their
   tax cost and require an explicit selection; they are not silently added. An entered code that loses is displayed
   as “Not applied; [offer] saves more” and consumes no use. The operator may choose that eligible code instead via
   `selected_promotion`; selection is not permission to violate eligibility.
5. With stacking enabled a promotion has `stackable` (false by default). Explicit selection of one non-stackable
   eligible offer excludes all others. Otherwise consider all stackable offers in priority/name/ID order, once each,
   on remaining nets. Exclude any gift whose own addition increases payable unless the operator explicitly selects
   that gift offer; a discount elsewhere in the bundle must not conceal its tax cost. Explicit selection of such a
   gift applies it alone after showing the cost. Compare the combined payable reduction with each non-stackable candidate using step 4's
   comparator. The winning option alone applies. This is a defined ordering, not a search for every possible
   permutation. Percent discounts compound on remaining net; fixed amounts are capped. At most one code still
   applies; multiple-code entry is not part of this design. Reward lines never qualify for another benefit.
6. Materialize chosen discounts and reward lines, recompute taxable bases and tax under the captured policy, then
   calculate commission on final nets. Produce explanation, totals and a preview fingerprint. Post reruns eligibility,
   stock, limits and authorization and compares all pricing/tax/source versions with the accepted preview, not just
   the grand total. A changed result yields `E_PREVIEW_STALE` and no write.

No offer edits the item price, customer defaults or existing posted sales. Draft customer/date/quantity changes
recompute eligibility. A membership renewal, offer revision or code expiry invalidates a held cart's preview at
resume/complete, even when the new total happens to match. Server results own totals; offline carts cannot redeem.

### Generated adjustments and the existing core

Add a narrowly scoped `applies_to: promotion` mode to the pure adjustment calculation, with immutable eligible line
keys and computed shares. It is not `applies_to: billed`: that mode belongs to captured work allocations. Generated
discount lines appear after the user's original ordered lines, in applied-offer order, and target earlier eligible
item/charge lines explicitly. Reward items appear before their generated discount. The manual line order and
subtotal spans remain unchanged. A printed offer label and the targeted line list explain non-adjacent application.

For each generated discount, compute the amount using R147's captured percentage rounding policy; distribute its
commercial amount over positive remaining eligible nets by largest remainder, ties to original position. For a
taxable discount, distribute its taxable reduction under the existing mixed-line rule, capped at remaining taxable
bases. For a non-taxable discount reduce no taxable base. Preserve both `amount_minor_units` and
`taxable_minor_units` on every target. Independent validators check amounts, target membership, zero floor and summed
shares from captured facts, without re-running offer selection. Do not accept client-authored generated shares.

Each generated discount debits its configured discount account; item revenue credits gross at the selected unit
prices; AR or Undeposited Funds debits net plus tax; tax credits the normal tax liability. Inventory and COGS follow
all issued stock, gifts included. Membership price changes alone have no discount posting: revenue is the resolved
selling price, as for ordinary price levels. Tax and commission receive distinct bases. R147's existing manual
refusals and the captured tax policy are unchanged.

## 5. Buy X get one free, precisely

The offer means **X paid qualifying units plus one additional gift**, not “of X units one is free.” For same-item
buy-two-get-one, entering paid quantity 2 proposes a separate reward quantity 1; entering paid quantity 3 proposes
one additional reward, not reclassifying one of the three as free. The screen reads “2 paid + 1 free = 3 supplied.”
With repeat on, reward count is `floor(paid qualifying whole base units / X)`; with it off the maximum is one. A
user-controlled cap per sale may limit that count. Reward units do not themselves earn gifts, and multiple offers
cannot claim the same qualifying units: process in selected-offer order and consume X previously unclaimed units
for each gift, ties to sale-line order. Percentage discounts may still cover paid qualifiers when stacking permits.

Reward item and unit are fixed in the published offer. Resolve its ordinary item/customer/member/R110 price as a
gross amount, then attach a generated 100% discount to that reward alone. Net is zero, but stock issues and COGS
remain real. A reward with ordinary price zero is still an issued zero-value item; it needs no money discount line.
The configured discount tax code determines whether tax is also reduced. Preview says “Free item; tax $…” if tax
remains. Do not promise a zero-total gift where the chosen policy does not produce one.

At entry, add a visible proposed reward row and require any serial/lot/bin choices under normal stock rules before
posting. Short stock produces the same refusal/explicit permission-controlled exception as an ordinary sale; do not
silently substitute a different item. Removing a proposed gift sets `declined_reward` for that offer in the cart,
and excludes that offer from selection until the operator chooses it again. It cannot reappear on every preview.
Changing qualifying quantities updates the reward quantity and invalidates any excess serial selection. Commission
enabled rewards follow R189's immediate picker on creation; their final net earns zero. If R189 is off there is no
picker. Selecting Cancel there declines that reward/offer, not the paid lines.

## 6. Redemption, saved sales and returns

Preview and hold do not reserve limited uses. A successful post inserts one redemption per applied promotion and
sale, inside the sale's transaction with stock, tax, commission events and idempotency receipt. A repeating gift
still consumes one use for that sale; store gift units separately. Enforce code and promotion total limits, plus
entitlement-owner limits, with serialized/conditional claims inside that transaction. Two tills seeing the last use
can both preview; exactly one can commit. The other receives `E_PROMOTION_EXHAUSTED`, a refreshed preview and no
partial tender/stock/sale. Retrying the winner returns its original result even after expiry, without a second use.

Record both transaction date (commercial eligibility) and actual posted-at time. Date conditions use the transaction
date; a backdated ordinary invoice can qualify if the actor is allowed to post that date. POS date stays today's
company-local date. Retiring/pausing a promotion prohibits new uses even with an old date. Usage caps count all
committed, not-released uses, independent of invoice date, so backdating cannot bypass a limit. This is explicit
commercial behavior, not a hidden clock-based campaign exception.

Corrections preserve captured facts unless explicitly repriced. Reprice is a complete new revision preview; native
sales restrictions on paid/settled documents and closed periods still apply. For an unapplied invoice it can release
old redemptions and claim replacement ones atomically, treating its own released claim as available; failure leaves
the old revision and uses intact. A correction that changes only memo/description retains entitlement and redemption.
Allocated work lines keep their promised prices/discounts and reject targeted promotions with
`E_PROMOTION_FIXED_BILLING`. Mixed documents can promote their ordinary new lines only; thresholds exclude fixed
allocated lines. Quotes/orders may preview future offers, but reserve no use and promise no unconsumed coupon.
Billing a captured work allocation does not run promotions on that allocated amount again.

**Returns use original facts.** A linked credit memo consumes original line quantity, net, commercial discount shares
and taxable shares, with cumulative proportional minor-unit allocation and the last return taking the residual.
No current coupon or membership lookup. Refund paid lines at their originally paid net plus original tax; reward
lines refund zero net and only their original tax, if any, and return stock normally. Returning a qualifier does not
retrospectively charge for a retained gift or shrink earlier benefits. This settled policy is shown in the offer
editor: “Returns refund the original paid amount; gifts are not charged back.” It avoids an implicit new receivable.
No receipt/no linked original means no invented code usage release or free-item claim.

Partial and full credit returns do **not** release a promotion/code use. Void of the entire original sale does
release its claims once, if native void is allowed, preserving a release event; expired codes remain expired.
Restoration is a new claim with current availability/active-state checks; it refuses actionably if exhausted.
Correction and restore must not erase the original redemption history. A paid sale is corrected by its native
credit/refund workflow, not by silently removing its discounts and re-opening the invoice.

## 7. Hand-worked correctness fixtures

All amounts below are USD; rates are illustrative configured inputs, not jurisdictional advice. Expected values are
computed independently from the written rules, never by calling the production calculator in the assertion.

**Mixed taxable/exempt coupon.** Taxable item A $100, exempt item B $50, taxable $30 coupon, 10% tax. Commercial
discount shares $20/$10, nets $80/$40. Tax reduction is $30 on A, whose taxable base is $70, so tax $7 and total $127.
**Dr AR $127 + Dr Discounts $30 = Cr Sales $150 + Cr Tax Payable $7**. With a non-taxable coupon the same nets yield
tax $10 and total $130. With a taxable $120 coupon the nets are $20/$10, taxable sales floor at zero, tax $0, total
$30; discount taxable reduction is capped at $100, not $120. A 10% commission on A uses $80 in the first case, not $70.

**Membership plus code.** Item $100; membership-selected level gives $90; no winning R110 rule; coupon 10% gives
discount $9, taxable net/base $81 at 10% → tax $8.10, total $89.10. **Dr AR $89.10 + Dr Discounts $9 = Cr Sales $90 +
Cr Tax Payable $8.10**. There is no fictitious additional $10 discount posting for the member price.

**Buy two get one.** Same item $10/unit, stock cost $4, tax 10%; paid quantity 2 and reward quantity 1, taxable reward
discount $10. Gross sales $30, discount $10, net $20, tax $2, receipt $22. **Dr Undeposited Funds $22, Dr Discounts
$10, Cr Sales $30, Cr Tax Payable $2**; **Dr COGS $12 / Cr Inventory $12**, three units leave stock. At 5% commission,
paid line earns $1 and gift earns $0. Return one paid unit: credit/refund $11, reverse $10 revenue and $1 tax, restore
$4 stock cost, commission reduction $0.50. Keeping the gift creates no new charge. Returning the gift later gives
$0 net/tax refund but restores its $4 stock cost and reverses its $10 gross revenue/$10 discount. Usage remains one.

**Rounding and stacking.** A fixed $0.05 discount over three equal $0.10 nets shares $0.02/$0.02/$0.01; nets total
$0.25. On $100, two stackable 10% benefits in order produce $10 then $9, total discount $19, not $20. Test percentage
half-cents under all three captured tax policies; R147 half-even versus half-up must remain observable. A zero-net
line is never a positive weight; a candidate capped to $0 is shown as not applied and consumes no use.

## 8. Storage and contracts

Company-local records use common IDs, audit, version checks and RESTRICT foreign keys. No hub customer audience or
cross-company coupon namespace. Every financial/usage fact needed to reopen a sale lives in its company file.

| Record | Required fields and invariants |
|---|---|
| `membership_plans`, revisions | name, active, referenced price-level ID/version; revision history; no implicit membership fee |
| `customer_memberships` | customer, plan, inclusive interval, status, member number, version; no overlapping effective intervals; ancestor source captured on sale |
| `promotions`, revisions | name/label, state, dates, currency, audience, conditions, trigger, priority, benefit, discount item/account/tax snapshot, total/owner use caps, stackable, established actor/revision |
| `customer_promotion_assignments` | promotion, customer, dates, include_jobs, version; unique effective attachment |
| `discount_codes` | normalized unique code, promotion, owner nullable, dates, active, caps, version; no reuse of retired strings |
| Sales revision facts | sold-to and membership owner; per-benefit promotion/code owners; membership/price provenance; candidate reasons and chosen offer revisions; reward origin/paid qualifiers; generated target keys/positions, commercial and tax shares; accepted fingerprint |
| `promotion_redemptions`, releases | sale/revision, promotion/code and their respective owners, applied monetary discount/reward quantity, posted-at/date; unique sale + revision + promotion + claim generation; at most one unreleased claim per sale/promotion; immutable release-of and replacement links |
| Existing batch ledger | exact changed memberships/assignments/code states, before-images and created IDs, source revision dependencies, each effect in its own transaction |

Dates and percentages are validated typed values; quantities are integer microunits; money is integer minor units
with currency, existing sub-cent price representation retained. Counters are transactional projections of claims
minus releases, rebuildable from that ledger, never the sole history. Index customer/interval, code key, active
offer/date, and redemption promotion/code/owner. Audience queries must use these indexes, not scan all customers on
each POS tap. Published revisions and referenced price-level snapshots remain readable after archival or merge.

Membership plan price-level edits affect future drafts through a new observed source version; captured sales never
follow a mutable level pointer. An archival/merge preview names references; merging customers combines effective
usage, rejects overlapping memberships unless resolved explicitly, and cannot reset a cap by creating a new owner.
Stable entitlement identity aliases are retained for those queries. An inactive item/discount account blocks new
activation/posting but not historical return validation from snapshots.

Every new write supports operation-key replay, expected versions, `--dry-run`, reason and the common audit envelope.
Dry run returns touched IDs/versions, exact before/after and accounting effects where relevant. Proposed commands:

| Command family | Inputs → outputs |
|---|---|
| `membership-plan create/update/show/list/activate/deactivate` | name, price level → versioned plan |
| `customer membership set/end/suspend/show` | customer, plan, dates, inheritance, version → enrollment/history |
| `promotion create/update/activate/pause/retire/show/list` | conditions/benefit/audience → version and a plain-language rule summary |
| `promotion assign/unassign` | explicit customers or saved query + captured member versions, dates → assignments, batch ID |
| `discount-code create/update/deactivate/show/list` | canonical code, promotion, dates/caps/owner → record; show only under management/read grants |
| `promotion preview` | sale draft or document/version, customer, code/selection → price candidates, eligibility reasons, generated lines, commercial/tax shares, total, fingerprint |
| Existing sales preview/post/correct and POS preview/complete | `discount_code`, optional `selected_promotion`, `declined_rewards`, accepted pricing fingerprint → same facts plus redemption IDs |
| `report promotions`, `customer offers` | date/customer/promotion filters → usage, discounts, gifts, returns, released claims and document links |
| `promotion apply` | explicit draft IDs/versions, selection → repriced drafts and one batch; posted sales refused |

Reads are paginated; public/narrow eligibility lookup returns only this sale's available offer labels/benefits,
never a customer audience, other people's codes or purchase history. An entered unavailable code returns
`E_PROMOTION_INELIGIBLE` with reason `unavailable` to a clerk; authorized management preview can distinguish inactive,
wrong owner or dates. `E_PROMOTION_EXHAUSTED` identifies the entered/applied offer and refresh action, not another
customer's redemption. Other errors: `E_MEMBERSHIP_OVERLAP`, `E_PROMOTION_FIXED_BILLING`, `E_PROMOTION_SELECTION`,
`E_PREVIEW_STALE`; ordinary stock, tax, version, permission and closed-period refusals remain authoritative.

## 9. Permissions, agents and reversible batches

R187 applies at the command boundary in every adapter. No capability follows merely from turning on a layer.

| Capability | Human default | Gives |
|---|---|---|
| `promotion.read` | standard | Plans, codes and usage reports; customer/sale drill-down also requires ordinary read rights |
| `promotion.manage` | admin | Define/publish offers, codes and membership plans; company switches still require company admin |
| `promotion.assign` | standard | Enroll customers and assign offers, with ordinary customer write rights |
| `promotion.apply` | standard | Choose eligible code/offer on a sale the actor may write; narrow eligibility lookup |

Agents receive these only by explicit principal grant, intersected with principal authority. The sale command
applies already-active automatic offers and membership prices under its own sales capability, as it applies R110
prices; it does not allow the caller to alter eligibility or benefits. Entering a code/selecting or declining an
offer requires `promotion.apply`. Editing plan/account/amount is always management. A POS preset for offers adds only
apply; plain POS reads show applied labels/totals but no customer lists or private membership history. Denies and
catalog-upgrade coverage must preserve the clerk's narrow scope. Add a new catalog layer at the then-current tip.

Activation makes a pricing policy available for future explicitly posted sales; it is not an unattended writer and
needs no `automate.*` capability. There is no periodic customer-list sweep or automatic enrollment in this design.
A future automation that writes assignments needs its own R187 establisher checks and per-run R186 effects.

`promotion assign/unassign`, mass membership changes and applying an offer to a draft list are R186 batches. Store
before-images/effects inside each write transaction; retries resume the same run and never append duplicate effects.
One `batch reverse <id> --dry-run` preflights all inverses, names later edits, captured-sale dependencies, delivered
artifacts and other blockers, then reverses newest-first in resumable slices. A sale that used a batch-created
membership/offer prevents erasing that dependency; ending a membership prospectively is a separate action, not an
alleged reversal. A posted draft or externally sent offer/statement blocks whole reversal of the creating run.
Imported customer assignments use the same adapter. No private promotion-only undo door.

Publishing broad offers, mass enrollment/assignment, bulk reprice, void and batch reversal carry `confirm_first`.
Agents show one exact dry run for a broad lasting change even when named. Ordinary named acts run under the existing
directive without a repeated confirmation ritual. No size thresholds, nested mandates or special agent pricing rules.
Help finds “coupon”, “discount code”, “membership”, “buy two get one”, “why this price” and “undo that customer list”.
An agent can preview a human's draft, explain which rule won, and leave the same draft for the human to finish.

## 10. Screens at 390 and 1440 px

At **390 px**, customer cards show membership name/expiry and an Offers count; open those panels for details rather
than widening the customer grid. Sale entry shows the customer chip, an optional Code action and a collapsed “Price
details” row. Code entry is a sheet with one input and Apply, followed by the server's accepted/unused/invalid result.
Cart rewards read “Free: Mug × 1” with stock choices and Remove; paid and free quantities are distinct. Sticky totals
show discount, tax and amount due; while recalculating show “Updating” and disable Complete. Touch targets are at least
44 px; no amount or refusal is clipped. The member picker never asks for a subscription/payment setup.

At **1440 px**, the customer list has optional membership and offer columns; selecting customers opens the common
bulk-edit preview. Sale lines remain centered, price explanations open beside the selected line, and an offer panel
shows candidate benefits, why one won and the entered code's status. The editor starts with audience, dates and one
benefit; stacking and repeat gifts appear only when the layer is enabled. Its preview shows ordinary prices, manual
discounts, offer discount, taxable reduction and final total as separate columns. A report drills from usage into
the captured sale and return. R186 blockers are linked, not hidden behind “can't undo”.

Receipts/invoices print the public membership price if relevant, offer label, gift quantity, discounts, tax and
total. They do not print private membership numbers or customer-specific code strings by default. Existing print
renderers consume the generated lines; no dependency on the held visual template designer (R113). Page reload and
device handoff preserve entered code/declined rewards but demand a fresh server preview before completion.

## 11. Acceptance criteria

1. Every switch off leaves existing sales unchanged. Codes alone, memberships alone and offers alone each work, with
   no loyalty points, commissions, POS or external service prerequisite.
2. Membership precedence and inherited entitlement owner follow §2; customer change/expiry rechecks a draft, not an
   old invoice. Explicit prices, R110 policy and allocated billing retain their defined behavior.
3. §7's arithmetic, ledger, tax floors and gift stock movement pass independent expected-value fixtures. Gift units
   never qualify recursively. R189 commission uses discounted net and zero gift net when enabled.
4. Best-offer ties, explicit selection, stacking, unused codes and caps follow §§3–4. A clerk sees why their price
   changed but cannot learn another customer's code or override eligibility by crafting JSON.
5. Two simultaneous last-use posts commit exactly one claim and sale. Lost response/retry, hold/resume, correction,
   full void, restore and returns obey §6; usage and stock never double. Backdating cannot bypass counters.
6. Partial returns retain original discount/tax facts and never refund a free line at today's retail price. Closed
   periods and settled invoices cannot be repriced through the promotion command.
7. Broad assignment and draft-reprice runs reverse as one R186 unit or refuse with all blockers, changing nothing;
   crash/retry/sliced reversal retains every effect and audit event.
8. Python, CLI, HTTP and actual MCP agree; a human and an agent can continue one draft. At 390/1440 px a clerk can
   enter a code, read gift quantities, resolve stock choices and see the exact posted total without horizontal clipping.

## 12. Settled decisions and open K-questions

Settled: independent off-by-default layers; one effective dated membership; membership selects a price level before
the existing R110 decision; manual adjustments before generated offers; default one best benefit, deterministic
optional stacking; one explicit code; extra gift units with real stock/COGS; original-fact refunds without gift
clawback; transactional use claims; full void releases usage, returns do not; all list operations through R186.
These are reversible design decisions recorded here, not questions for the principal to answer.

- **K1 — Scope beyond membership prices.** Include paid/renewing memberships, accumulated loyalty points, gift cards
  or externally delivered coupons? **Default:** no. Dated membership pricing and same-sale benefits only; no fee,
  renewal charge, stored-value liability or external messaging. Any addition gets its own accounting/permission design.
- **K2 — Tax/legal scope.** Must this release handle reimbursement-funded coupons or legally distinct gift-tax rules
  beyond the current discount tax-code model? **Default:** no new tax regime; configure the existing taxable/non-taxable
  discount treatment explicitly, preserve it on the sale and expose the worked tax preview. Cases that cannot be
  represented by it are not advertised as supported and wait for the corresponding tax design.

## Appendix: prior art and anchor references

Read 2026-10-31, public documentation only; fetched no executable, installed no package and copied no source code.
Names and branded filenames are omitted under the naming instruction. **Public reference A** means the first public
system named in the task; **public reference B** the second. These are retrieval titles/paths, not evidence that
Bookflow must reproduce every option. The governing tax semantics come from Bookflow's R147 decisions and code.

| Reference and locator | Mechanism read | Adopt / reject and why |
|---|---|---|
| Anchor enterprise 24 guide, PDF/printed pp. 57–59, 85, 90 | Price levels, scheduled/quantity/customer pricing, multiple/exclusive rules | Preserve R110 and optional stacking. Do not replace unit-price selection with coupon logic. |
| Anchor 2016 manual, printed pp. 137–141, 229–232, 235–238 (PDF 168–172, 260–263, 266–269) | Customer/per-item levels; visible discount/subtotal placement | Keep price versus discount distinction and manual line ordering; generated targeted adjustments are an explicit extension. |
| A, official docs “Pricing Rule” (`pricing-rule`) | Eligibility by item/party/quantity/value, price or free-product result, priority | Adopt explicit conditions and reward provenance; do not overload R110's positive-price-only rule with gifts. |
| A, official docs “Promotional Scheme” (`promotional-scheme`) | Slabs generate pricing rules; mixed and cumulative conditions | Adopt a readable campaign configuration. Reject generated duplicate R110 rules and cumulative cross-sale thresholds here: R110 already covers tiers, and R188 needs same-sale benefits. |
| A, official docs “Coupon Code” (`coupon-code`) | Code links to rule, validity and maximum uses; web-store entry | Adopt code/benefit separation and caps; reject a web-only execution path because every Bookflow interface shares the contract. |
| A, official docs “Loyalty Program” (`loyalty-program`) | Customer enrollment, tier/spend-based points, redemption/expiry and point entries | Adopt explicit customer attachment and immutable usage evidence. Reject requiring points to obtain a membership price; stored rewards are K1's additional scope. |
| B, official Sales documentation 19.0, “Discount and loyalty programs”, `applications/sales/sales/products_prices/loyalty_discount.html` | Optional feature, program types, rules/rewards, app availability, free products, usage limits and customer-linked cards | Adopt optional setup, conditions/benefits and customer visibility. Reject cross-company default availability and requiring credits for a same-sale gift; company isolation and simple sale arithmetic govern here. |

The candidate comparator, tax allocation, original-price return policy and concurrency claims are settled Bookflow
contracts. A public product's documented behavior is prior art, not an oracle for these balances.

## 13. Build increments

S is a narrow independently useful slice, M one bounded workflow, L a cross-cutting transaction change. Each increment
ships demo cases, typed command/help/schema references, permission checks and historical migration coverage. The
first slice leaves codes, offers, POS and commissions entirely optional.

1. **Dated customer membership price (S).** One plan linked to an existing price level, explicit customer enrollment,
   simple customer panel and ordinary invoice resolver provenance; no bulk, inheritance or campaigns yet.
   **Done when:** an enrolled customer gets that level in the interval and their ordinary level outside it; an old
   invoice stays fixed. **Tests:** date boundaries, explicit price and R110 precedence, overlap refusal, disabled
   layer, one 390 px enrollment/preview path and all-interface typed input validation.
2. **Membership across sales and jobs (M).** Job inheritance/suppression, customer-list filtering, receipt/POS pricing,
   version revalidation and membership batch inverse. **Done when:** a person and agent see the same membership price
   and a broad mistaken enrollment is reversible. **Tests:** ancestor/owner identity, renewal/expiry on held ticket,
   merge overlap, later-sale reversal blocker, 1440/390 list and sale journeys.
3. **One code and visible discount (M).** Code/offer definitions, customer attachment, one fixed/percentage benefit,
   generated target mode, captured shares and explanation in ordinary sales. This slice offers unlimited-use codes
   only; it records source provenance and refuses cap fields until increment 4 supplies enforcement. **Done when:** mixed-tax §7 fixture
   posts the exact total and journals through the shared core, with codes enabled alone. **Tests:** no tax reduction
   versus taxable reduction, zero floor, nonadjacent targets/manual charges, explicit-price eligibility, wrong owner,
   inactive code, partial return, allocated-line refusal and permissions.
4. **Atomic claims and POS redemption (M).** Total/owner caps, claim/release ledger, preview fingerprint, correction,
   void/restore and POS completion. **Done when:** exactly one of two last-use sales posts; a retried result is stable.
   **Tests:** racing writers, lost response, expiry/retire between preview/post, unchanged-total source change,
   backdated invoice, named versus walk-in, split-tender single claim, sold-to jobs sharing one cap.
5. **Automatic offers and free item (L).** Audience matching, deterministic selection, gift creation/decline, stock
   and serial choices, original-fact refunds and receipt rendering. **Done when:** buy-two-get-one issues three
   units and §7's sale/return balances match by hand. **Tests:** same/different reward item, repeats, fractional
   qualifier, competing gifts, stock shortage, disabled commission versus enabled picker, tax on a gift, refusal
   and retries; ordinary invoice/receipt/POS parity and two-width browser journey.
6. **Optional stacking, mass application and reports (M).** Defined ordering, winner explanations, draft-list apply,
   assignment batches and R186 inverse registry, usage/return reporting. **Done when:** every acceptance criterion
   passes and reports reconcile from immutable claims. **Tests:** $19 compound discount, tie resolution, unused code,
   conflicting non-stackable benefits, cap-to-zero, posted-draft/export blockers, interrupted run/reversal, narrow
   clerk projections and the closing trial below.

**Closing trial.** A fresh agent using actual MCP help enrolls a named customer, assigns a code to an explicit customer
list, and explains the resulting invoice price. A person at 390 px resumes that draft, completes a membership/code
sale and a buy-two-get-one POS sale, then at 1440 px returns one paid item and the gift separately. Two sessions race
for the last code use; retry the successful post after a simulated lost answer. Reverse the mistaken customer-list
assignment before use; repeat after a sale depends on it and inspect the refusal. Work out §7's amounts, tax, stock,
commission when enabled, ledger and remaining usage by hand; check them against stored facts. Run a company with
every layer off and another with only codes on. Record first-attempt success and the agent's help/clarification
friction, fix material findings and rerun failed scenes before closing the build.
