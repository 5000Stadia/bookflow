# R147 — subtotal, discount, group and percentage-charge lines on sales

Plan for roadmap item R147 (approved on gate g8b3cb3). The approved behaviour is the
roadmap item's; this file says how it is built and where its edges are.

## Line kinds

A sales line is one of four kinds, read off its captured item type:

| Kind | Item | Amount shown | Net (settles, posts to AR) | Posts |
|---|---|---|---|---|
| item | service, non-inventory, fixed other charge, stock | quantity × rate, or entered amount | amount less every discount share applied to it | income at the shown amount; tax; stock cost |
| subtotal | subtotal | sum of the shown amounts back to the previous subtotal (exclusive) | 0 | nothing |
| charge | other charge with `charge_percent` | percentage of the line directly above (item, charge or subtotal) | amount less discount shares | like an item line |
| discount | discount | minus (percentage of, or fixed amount off) the line directly above (item, charge or subtotal) | 0 | debit to the discount item's account |

A group line is not stored: on entry it expands into its active members in order, each
with quantity = member quantity × group quantity, and each member records the group it came
from (item, description, print_members) so display and print can show the group. Members
are ordinary lines afterwards.

## Discount distribution and posting (no settlement change)

A discount's amount is distributed over what it applies to: the one line above, or the item
and charge lines in the span of the subtotal above, in proportion to their current nets,
largest remainder, ties to the earlier line. Each share reduces that line's net. The
discount line records its shares by position.

Posting, per line: AR debit = net + tax (unchanged); income credit = the shown amount, its
sources being the line's own net plus each discount's share (attributed to that discount
line); the discount line debits its account with the sum of its shares. Every existing
settlement component (a line's net and each tax, each with exactly one AR and one
recognition source of equal amount) keeps its shape, so payments, credits, deposits and
restatement need no change. The general ledger matches the anchor: income at gross,
discount debit, receivable net.

## Tax

The discount item's tax code decides whether it reduces taxable sales. A taxable discount
reduces the taxable base of each line it applies to by that line's share; a non-taxable one
reduces none. The tax calculator receives a line's taxable base separately from its net.
Refused, as a question for the person rather than a choice: a taxable discount whose span
holds any non-taxable line (the anchor reduces taxable sales by the whole discount; a
proportional rule would not). Percentage amounts round with the document's captured policy:
half-even under `line_component_half_even`, half-up under the two half-up policies.

## Refusals

Discount or charge with nothing above it, directly under a discount, or on a negative
base; a discount larger than the nets it applies to; group members with zero quantity;
group lines with anything but item and quantity; quantity other than 1 on subtotal, discount
and charge lines.

## Edges

Touches: sales_facts, sales_models, sales_defaults, a new pure `sales_adjustments`, sales
posting/validation/outputs, tax_attribution/tax_calculations (taxable base), sales-by-item
attribution, workbench line grid, demo seed. Estimates and work orders quote these lines;
billing a work document whose selected lines carry discounts or subtotals is refused with a
plain error until the billing proof carries them. Credit memos keep refusing these items.
Payments, bill payments and deposits are not modified.
