# Numeric entry and precision

## Live arithmetic entry

Status: implemented. Numeric and currency entry controls offer
calculator-style entry in the browser. Saved values continue to follow the typed
command contract; calculator expressions are entry assistance, not stored formulas.

Editable quantity, amount, price, cost, percentage, exchange-rate, unit-factor,
integer and custom-number controls support decimal arithmetic, including repeated
rows and the register. Identity fields such as document/account numbers, phone
numbers, postal codes and dates remain text or date controls. Protected captured
work economics remain read-only.

Operators are +, -, *, / and parentheses with standard precedence and unary signs.
Decimal literals accept a leading point. Postfix percent divides its operand by100;
thus `100 + 10%` is100.1 and `100 * (1 + 10%)` is110. A percentage control stores
percentage points: entering `5 + 5` yields10 percent. Optional trailing equals and
Enter evaluate the entry; Enter does not submit the form. Tab or leaving the field
uses the result rounded to the field's supported precision. While typing, show the result
beside the unchanged expression. Empty inputs retain existing optional-field rules.

Compute using bounded integer fractions, without executing user text or using
binary floating point. Decimal expressions and intermediate results are bounded
against excessive work. Invalid expressions and division by zero preserve the
attempted text and block submission. Existing core validation still owns permitted
signs, ranges, accounting effects and closed periods.

Exchange rates retain 18 decimal places; unit factors and custom numbers retain 9.
A result that needs rounding automatically uses half-even rounding at the field's
supported precision, displaying the rounded result while typing. Quantity fields
use six decimal places; money uses the currency's posting precision. Integer
fields require an exact integer. Currency precision follows the actual field currency, including an entered ISO
suffix such as `1000 / 3 JPY`;
when unresolved, do not guess a currency or a rounding precision. Committing a
result is an ordinary field edit: it invalidates any earlier sales preview and
requires the normal fresh preview before saving. Dynamic rows and form replacement
must retain these behaviors, including register payload construction and retries.

## Fractional-cent prices and costs

Status: option under consideration; not implemented. Permit an opt-in precision
setting for unit prices and costs below a currency's smallest posting unit. This
is a proposed interpretation of the requested fractional-cent option, not a change
to existing money storage. Current prices and costs still use currency precision.

The proposed behavior is to multiply exact quantity by the more precise unit
price/cost, then round the extended line amount at the established currency
boundary. Posted amounts, settlement balances and report totals retain their
currency's precision. Printed/displayed rates must reveal the captured precision;
there must be no early rounding of a subcent rate to zero.

Before implementation, the owning plan must define supported precision and setting
scope, line/tax rounding, item and price-level defaults, immutable rate snapshots,
unit conversions, historical edits, imports/exports, printing, and preserving
migration of existing values. Existing documents must retain their captured rates
when defaults change. Fractional posted cents, precision greater than the declared
unit-price scale, and automatic enabling are not selected by this proposal.
