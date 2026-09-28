"""Subtotal, discount and percentage-charge lines, worked out over a sale's ordered lines.

Pure: no database, no defaults. ``apply`` takes resolved lines in document order after each
has its own amount (quantity times rate, an entered amount, or zero for a subtotal, discount
or charge still to be worked out) and fills in what the lines above them decide:

- a subtotal shows the sum of the amounts shown above it back to the previous subtotal;
- a percentage charge is its percentage of the line directly above, or of the subtotal
  directly above, and is then an ordinary sold line;
- a discount is its percentage of, or its fixed amount off, that same base. Its amount is
  shared over what it applies to -- the one line above, or the item and charge lines in the
  span of the subtotal above -- in proportion to their current nets, largest remainder,
  ties to the earlier line. Each share comes out of that line's net. A taxable discount
  also comes out of that line's taxable base; a non-taxable one does not.

``check`` re-derives every net and taxable base from captured facts alone, without the
distribution rule, for the independent validators.
"""
from __future__ import annotations

from bookflow.company.sales_models import _invalid
from bookflow.company.tax_calculations import TAX_DENOMINATOR
from bookflow.core.exact import round_ratio_half_even

LEGACY = 'line_component_half_even'


def kind(profile) -> str:
    """item, subtotal, discount or charge."""
    adjustment = getattr(profile, 'adjustment', None)
    return adjustment.kind if adjustment is not None else 'item'


def percentage(base: int, percent_millionths: int, policy: str) -> int:
    """A percentage of a nonnegative amount, rounded the way the document rounds tax."""
    numerator = base * percent_millionths
    if policy == LEGACY:
        return round_ratio_half_even(numerator, TAX_DENOMINATOR)
    quotient, remainder = divmod(numerator, TAX_DENOMINATOR)
    return quotient + (2 * remainder >= TAX_DENOMINATOR)


def distribute(amount: int, weights: dict[int, int]) -> dict[int, int]:
    """Split ``amount`` over positive integer weights exactly: largest remainder, earlier index first."""
    whole = sum(weights.values())
    shares = {index: amount * weight // whole for index, weight in weights.items()}
    remainders = sorted(weights, key=lambda index: (-(amount * weights[index] % whole), index))
    for index in remainders[:amount - sum(shares.values())]:
        shares[index] += 1
    return shares


def _where(index):
    return f'lines.{index}'


def amount_of(line) -> int:
    """The amount a line shows before any discount: its extension, entered amount or charge."""
    profile = line['profile']
    if kind(profile) == 'charge':
        return profile.net_amount_minor_units
    return line['amount_minor_units']


def apply(lines: list[dict], policy: str) -> None:
    """Fill in subtotal, charge and discount amounts, discount shares, nets and taxable bases.

    Each line dict carries ``profile`` (its SalesLineProfile), ``net_minor_units`` (its own
    amount so far) and ``adjustment_taxable`` (whether it is effectively taxable). On return
    every line has ``amount_minor_units`` (shown, signed), ``net_minor_units`` and
    ``taxable_minor_units``; a discount's profile records its shares.
    """
    shown, nets, reduced = [], [], []
    spans = {}
    start = 0
    # A billed discount's shares were taken out of lines billed from quoted work. A line billed
    # by allocation carries its net after the discount, so its amount is that net plus its shares.
    restored = [0] * len(lines)
    for line in lines:
        adjustment = line['profile'].adjustment
        if adjustment is not None and adjustment.kind == 'discount' and adjustment.applies_to == 'billed':
            for target in adjustment.targets:
                if lines[target.position - 1]['profile'].pricing_basis == 'allocated':
                    restored[target.position - 1] += target.amount_minor_units
    for index, line in enumerate(lines):
        profile = line['profile']
        role = kind(profile)
        billed = profile.adjustment is not None and profile.adjustment.applies_to == 'billed'
        if role == 'item' or (role == 'charge' and billed):
            amount = line['net_minor_units'] + restored[index]
            shown.append(amount)
            nets.append(amount)
            reduced.append(0)
            continue
        adjustment = profile.adjustment
        if role == 'subtotal':
            value = sum(shown[start:index])
            spans[index] = range(start, index)
            start = index + 1
            adjustment.amount_minor_units = value
            shown.append(value)
            nets.append(0)
            reduced.append(0)
            continue
        if billed:
            # The quote decided every share; only their positions here are this sale's.
            taxable = line['adjustment_taxable']
            value = 0
            for target in adjustment.targets:
                j = target.position - 1
                if not 0 <= j < index or kind(lines[j]['profile']) not in ('item', 'charge'):
                    raise _invalid(_where(index), 'a billed discount names a line that is not above it on this sale')
                if target.amount_minor_units > nets[j]:
                    raise _invalid(_where(index), 'a billed discount is larger than the line it applies to')
                nets[j] -= target.amount_minor_units
                if taxable:
                    reduced[j] += target.amount_minor_units
                value += target.amount_minor_units
            adjustment.amount_minor_units = -value
            shown.append(-value)
            nets.append(0)
            reduced.append(0)
            continue
        noun = 'a discount' if role == 'discount' else 'a percentage charge'
        if index == 0:
            raise _invalid(_where(index), f'{noun} applies to the line directly above it; add the line or '
                                          'a subtotal of the lines it is for above it')
        above = kind(lines[index - 1]['profile'])
        if above == 'discount':
            raise _invalid(_where(index), f'{noun} cannot apply to a discount; put a subtotal between them')
        base = shown[index - 1]
        if base < 0:
            raise _invalid(_where(index), f'{noun} cannot apply to a negative amount')
        adjustment.applies_to = 'subtotal' if above == 'subtotal' else 'line'
        adjustment.base_minor_units = base
        value = (percentage(base, adjustment.percent_millionths, policy)
                 if adjustment.percent_millionths is not None else adjustment.fixed_minor_units)
        if role == 'charge':
            adjustment.amount_minor_units = value
            profile.net_amount_minor_units = value
            shown.append(value)
            nets.append(value)
            reduced.append(0)
            continue
        targets = list(spans[index - 1]) if above == 'subtotal' else [index - 1]
        weights = {j: nets[j] for j in targets if kind(lines[j]['profile']) in ('item', 'charge') and nets[j] > 0}
        if value and any(lines[j]['profile'].pricing_basis == 'allocated' for j in weights):
            raise _invalid(_where(index), 'a discount cannot apply to a line billed from an estimate or '
                                          'work order; its amount is fixed by what was billed')
        if value > sum(weights.values()):
            raise _invalid(_where(index), 'a discount cannot be larger than the amount still owed on what it applies to')
        shares = distribute(value, weights) if value else {}
        taxable = line['adjustment_taxable']
        for j, share in shares.items():
            if share and taxable and not lines[j].get('adjustment_taxable', bool(lines[j].get('taxes'))):
                raise _invalid(_where(index), (
                    'a taxable discount here would apply to a non-taxable line as well; the way it should '
                    'reduce taxable sales across mixed lines is an open question, so subtotal the taxable '
                    'lines and the non-taxable lines separately and discount each, or use a non-taxable '
                    'discount'))
            nets[j] -= share
            if taxable:
                reduced[j] += share
        from bookflow.company.sales_facts import AdjustmentTarget
        adjustment.amount_minor_units = -value
        adjustment.targets = [AdjustmentTarget(position=j + 1, amount_minor_units=share)
                              for j, share in sorted(shares.items()) if share]
        shown.append(-value)
        nets.append(0)
        reduced.append(0)
    for line, amount, net, cut in zip(lines, shown, nets, reduced, strict=True):
        line['amount_minor_units'] = amount
        line['net_minor_units'] = net
        line['taxable_minor_units'] = amount - cut if kind(line['profile']) in ('item', 'charge') else 0
        for component in line.get('taxes', ()):
            component['taxable_minor_units'] = line['taxable_minor_units']


def check(profiles: list, amounts: list[int], nets: list[int], taxable: list[bool], require) -> list[int]:
    """Independently reconcile captured shares; return each line's taxable base.

    ``profiles`` are the captured line profiles in position order, ``amounts`` each line's own
    amount before discounts (extension or entered amount; ignored for subtotal and discount
    lines), ``nets`` the stored nets and ``taxable`` each line's effective taxability.
    """
    cut = [0] * len(profiles)
    taxed_cut = [0] * len(profiles)
    for index, profile in enumerate(profiles):
        if kind(profile) != 'discount':
            continue
        for target in profile.adjustment.targets:
            j = target.position - 1
            require(0 <= j < index and kind(profiles[j]) in ('item', 'charge'), 'discount share names no line above it')
            require(not target.amount_minor_units or not taxable[index] or taxable[j],
                    'taxable discount share on a non-taxable line')
            cut[j] += target.amount_minor_units
            if taxable[index]:
                taxed_cut[j] += target.amount_minor_units
    bases = []
    for index, profile in enumerate(profiles):
        if kind(profile) in ('subtotal', 'discount'):
            require(nets[index] == 0, 'a subtotal or discount line carries no net')
            bases.append(0)
            continue
        require(nets[index] == amounts[index] - cut[index] and nets[index] >= 0, 'net differs from amount less discount shares')
        bases.append(amounts[index] - taxed_cut[index])
    return bases


def own_amount(row, facts) -> int:
    """A stored line's amount before discounts, from its captured pricing alone."""
    from bookflow.company.sales_calculations import extension
    if facts.pricing_basis == 'unit':
        return extension(row['quantity_microunits'], row['unit_price_minor_units'])
    if facts.pricing_basis == 'amount':
        return facts.net_amount_minor_units
    return row['net_minor_units']


def captured(header, rows, require):
    """Captured line facts in position order, each line's own amount, and each taxable base.

    ``rows`` are a revision's sales line profiles in position order. Effective taxability is
    the document's: tax enabled, the line's code taxable, the customer not exempt.
    """
    from bookflow.company.sales_facts import SalesLineProfile
    facts = [SalesLineProfile.model_validate_json(row['item_snapshot']) for row in rows]
    exempt = header.customer_tax_code is not None and not header.customer_tax_code.taxable
    taxable = [bool(header.preferences.sales_tax_enabled and fact.tax_code is not None
                    and fact.tax_code.taxable and not exempt) for fact in facts]
    amounts = [own_amount(row, fact) for row, fact in zip(rows, facts, strict=True)]
    # A line billed by allocation has no pricing of its own to derive its amount from: it is its
    # billed net plus the shares billed discounts name on it. Those shares are checked against
    # the quote by the billing validators.
    for index, fact in enumerate(facts):
        if fact.pricing_basis == 'allocated':
            amounts[index] = rows[index]['net_minor_units'] + sum(
                target.amount_minor_units for other in facts if kind(other) == 'discount'
                for target in other.adjustment.targets if target.position == index + 1)
    bases = check(facts, amounts, [row['net_minor_units'] for row in rows], taxable, require)
    return facts, amounts, bases
