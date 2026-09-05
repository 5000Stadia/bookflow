# Row 8 — manual rates and foreign-tagged journals

Target: intention row 8 and blueprint sections 8.1–8.3, 10.2 and 18.
This increment supplies manual dated rates and foreign journal conversion.
The company ledger and account balances remain entirely in home currency.
Rate fetching, provider configuration, automatic fetching and settlement gains or
losses are outside this increment. No command in this increment accesses a network.

## Exact rate and conversion

A rate is home major units per one original major unit. Input is a positive plain
decimal string with one through twelve integer digits and at most eighteen
fractional digits. Leading zeros and trailing fractional zeros normalize away;
`"000.006800"` becomes `"0.0068"`. No whitespace, sign, exponent, float, boolean,
zero or null is accepted as a supplied rate. Invalid syntax uses E_VALIDATION;
amount precision and stored money bounds retain E_AMOUNT_PRECISION/E_VALUE_RANGE.

For an original amount O in integer minor units, original precision p, home
precision h, and a rate coefficient R with scale s, compute
`O * R * 10^h / (10^p * 10^s)` with integer intermediates and round once to the
nearest integer, ties to even. Original and resulting home amounts must each be
positive signed 64-bit integers. A positive conversion rounding to zero is
E_VALUE_RANGE. Each line converts independently. Converted debit and credit totals
must balance exactly; there is no automatic rounding line or gain/loss plug.

Examples: JPY2345 at0.0068 into USD gives1595 cents. JPY125 at0.0002 gives2 cents;
JPY175 at0.0002 gives4 cents. USD0.03 at50 into JPY gives2 yen. Public amount input
remains a decimal string with optional currency (`"2345 JPY"`) or the existing
strict integer Money object. Currency codes and original precision use the shipped
currency table. Bare strings use company home currency.

## Dated rate commands

All rate commands are company-scoped. `rate set` requires standard role and
`ledger.post`; `rate show` and `rate query` require member role and `ledger.read`.
They use ordinary context, previews, audit and shared command dispatch.

`rate set` takes `date`, `from_currency`, `rate`, and `expected_version` (default0).
The company home currency supplies `to_currency`; callers cannot select another
ledger currency. From currency must be a known non-home code. Version0 means
create only. Updating an existing date/currency pair requires its current positive
version, including a no-op. A nonzero version for a missing pair or a mismatch
returns E_VERSION_CONFLICT without changing anything. No blind overwrite exists.
An unchanged canonical rate at the expected version is a no-op. Changing a rate
increments its version by one. Request idempotency replays the committed receipt.

`rate show` takes either `rate_id` or both `date` and `from_currency`, returning
the selected row or E_RECORD_NOT_FOUND. Supplying both selector forms, an incomplete
pair, or neither is E_VALIDATION. Stable-id selection supports a saved rate detail
link; it does not change exact-date lookup during posting. `rate query` accepts optional inclusive date_from/date_to and
from_currency filters, limit1–200 (default50), and cursor. It orders by date,
from_currency and stable id. Existing authenticated company query continuation
binds actor, permission state, filters and watermark; a changed rate invalidates
continuation with E_QUERY_STALE. No nearest-date fallback, inverse lookup or
triangulation is performed.

The output record includes id, version, date, from_currency, to_currency,
canonical rate, source=`manual`, entered_by and entered_at. The latest setter owns
entered_by/entered_at. Write output also identifies changed/no-op and dry_run.
The rate table is not a master-data list and has no activate/deactivate or list
undo operation. Its current value and previous versions remain in ordinary audit.
Rate forms expose all inputs and results through the generated workbench;
rate query is reachable from the Accounting navigation group. Record details support the stable id or date/currency; successful set opens its
authoritative detail by id.

## Storage and atomicity

Frozen company migration co0008 adds `exchange_rates`: stable id, version,
date, from_currency, to_currency, rate, source, entered_by and entered_at.
The unique key is date/from_currency/to_currency. Check constraints enforce
positive integer version, currency shapes, different currencies, source=`manual`
and bounded nonempty rate text; precise calendar/currency/rate validation lives
in the shared service. entered_by references the company principal mirror.
The table uses this declared schema, not the mutable-list mixin. Existing ledger
history and all earlier frozen migrations remain byte-identical.

A changing set writes one ordinary company audit event with the before/after
rate snapshot and versions. Rate row, principal, audit and request receipt commit
in the same transaction. Preview changes no durable business state. The writer
rechecks version and current row under its transaction; injected audit/rate/retry
failures roll back everything. Copy/reopen and upgrades preserve stored rates and
historical journals. Rates are not annotation targets in this bounded increment.

## Journal posting and rate selection

`journal post` and `journal update` add an optional `rate` and update adds
`refresh_rates` (defaultfalse). A non-null supplied rate is the explicit manual
command override. Explicit null is rejected; omission means no override. An
override requires exactly one distinct foreign currency among the effective
entered lines, otherwise E_VALIDATION. It applies to every line in that foreign
currency, including retained lines. Home-currency lines are unaffected. Without
an override, new or changed foreign amounts use the exact-date table rate.
Multiple foreign currencies are supported through their separate table rows.
No per-line manual override input is exposed in this increment.

Each foreign entered line and its posting line store original_minor_units,
original_currency, rate_used and rate_source. Table selection records
rate_source=`table:manual`; command overrides record `manual`. Domestic lines
have all four foreign facts null. Output adds original_amount as Money or null,
while existing amount remains home Money. Historical output reads captured facts,
not the current rate table. Missing lookup returns E_NO_EXCHANGE_RATE with date,
from_currency and to_currency. The final company writer resolves lookup rates
again; a preview is advisory and a later rate change can affect a new posting.
The saved receipt and idempotent replay identify the committed rate and amount.

## Correction, preservation and explicit repricing

An existing line is matched only by its stable current line_id. If its entered
original amount and currency are unchanged, omission of both rate override and
refresh_rates retains its exact captured home amount, rate and source. This
preservation also applies when editing its account, side, dimensions, description,
custom fields, memo or accounting date. Changing the date alone does not reprice
an unchanged original. Retired line identities remain rejected. A replacement
without a retained identity is a new input and selects a rate at its effective date.

Changing entered amount or currency selects the explicit override or current
exact-date table rate. Setting refresh_rates=true explicitly reselects the table
rate for every foreign line, unless the command's manual override is present.
refresh_defaults refreshes displayed master/custom metadata only; it does not
reprice. Rate/source-only changes produce an immutable correction even if home
rounding gives the same amount. A true no-op produces no new revision or audit.
Voids and correction reversals copy exact old foreign facts without any rate lookup.
Both old and new accounting dates still obey the closing-date gate.

Final aggregate validation checks captured foreign-fact completeness, known and
non-home original currency, positive bounded originals, canonical positive rate,
recognized source, exact conversion and exact entered/posting agreement. It also
binds proposed foreign facts to the effective original command lines, saved prior
lines and decisive writer-selected rates. A coordinated alteration of original,
rate and home amount across document and posting rows cannot create unrequested
money. Original input intent is not inferred from already generated posting rows.
Domestic journal cost must not gain a rate-table query per line.

## Browser, register and seed

Generated journal post accepts tagged amount strings in the existing entered-line
controls. Update shows each original tagged amount and preserves line_id; it never
resubmits home display money as a foreign original. The detail and preview display
home totals plus captured original amount/currency/rate/source. The editor exposes
one optional manual rate and the explicit refresh_rates control with a plain
explanation of preserved conversions. Browser errors retain attempted strings,
manual rate, flags and stable line identities. Desktop1280 and phone390 remain
contained. Opening, previewing or editing a memo must not silently reprice.

The dedicated register remains a home-currency composer. Foreign journals can be
read there with home balances, but are not editable through its restricted shape;
they link to the generated journal editor. Direct register.update rejects such a
journal, and existing domestic register behavior remains unchanged.

The ordinary demo sets the2026-07-15 JPY rate to0.0068, posts DEMO-JPY
(debit Checking2345JPY, credit Service Income15.95USD), then changes the table
rate to0.007. The journal retains its original0.0068 conversion. Checking becomes
612095minor units, Service Income161595 and trial balance664595 each; ten
journal headers remain. The
reference2026 source/oracles are unchanged. Existing fixed-count and balance
demo tests are extended with explicit new expected totals. The preserved human
demo is upgraded additively through public commands after backup; no reset.

## Required witnesses

- Canonical rate parsing, rejected float/bool/exponent/zero/null/oversize values;
  JPY/USD and non-USD-home precision, ties-even, large integer intermediates,
  zero-rounded and i64 overflow, independently computed balanced totals.
- Rate create/update/version/no-op/retry/preview; exact-date missing rate and
  no fallback/inversion; readonly and foreign-company denial; frozen migration,
  copy/reopen, audit provenance and injected late rollback.
- Manual override scope, mixed table currencies, home-only override rejection,
  no implicit balancing; decisive writer rate change after preview and retry.
- Changed rate does not alter old history, memo/custom-field/date/default edits,
  source preservation and void; explicit refresh/override and changed originals
  create exact reversal/replacement, including source-only changes and closed dates.
- Corrupt but internally coherent foreign plans rejected with no effects; all
  domestic and custom-field aggregate protections retained.
- CLI/Python/HTTP parity, generated post/preview/update/receipt, actual Chrome
  desktop/phone original-input preservation, rate entry/query, exact demo balance,
  foreign register read-only behavior and existing domestic register regressions.
- Generated docs, registry/error matrix, complete integration suite, and quiet
  domestic posting/query budgets after parallel workers stop.
