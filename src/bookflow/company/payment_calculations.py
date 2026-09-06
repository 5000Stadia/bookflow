"""Pure receipt ownership, settlement allocation and shared draft calculation.

Inputs are resolved business facts. No IDs, posting rows or clock values are
created here, so previews and commits use the same logical allocation recipe.
"""
from dataclasses import dataclass, replace

from bookflow.core.exact import INT64_MAX


def _units(value, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= INT64_MAX):
        raise ValueError('money must be an in-range integer' + (' greater than zero' if positive else ''))
    return value


@dataclass(frozen=True, order=True)
class ComponentKey:
    ordinal: int
    kind: int  # 0 = stored sales net; 1 = stored tax component
    tax_item_id: str = ''

    def __post_init__(self):
        if type(self.ordinal) is not int or self.ordinal <= 0:
            raise ValueError('settlement ordinal must be positive')
        if type(self.kind) is not int or self.kind not in (0, 1):
            raise ValueError('unknown settlement component kind')
        if not isinstance(self.tax_item_id, str) or bool(self.tax_item_id) != bool(self.kind):
            raise ValueError('only tax components have a tax item identity')


def allocate(amount: int, capacities: dict[ComponentKey, int]) -> dict[ComponentKey, int]:
    """Largest remainder against remaining stored capacities; omit zero rows."""
    _units(amount, positive=True)
    for key, value in capacities.items():
        if not isinstance(key, ComponentKey):
            raise ValueError('allocation requires logical settlement keys')
        _units(value)
    total = sum(capacities.values())
    if amount > total:
        raise ValueError('application exceeds remaining invoice capacity')
    quotient = {key: amount * value // total for key, value in capacities.items()}
    order = sorted(capacities, key=lambda key: (-(amount * capacities[key] % total), key))
    for key in order[:amount - sum(quotient.values())]:
        quotient[key] += 1
    return {key: value for key, value in quotient.items() if value}


def receipt_components(payer: str, amount: int, applications: list[tuple[str, int]]) -> dict[str, int]:
    """Derive NEW cash ownership. Never use this to redistribute old credit."""
    _units(amount, positive=True)
    components = {}
    for party, applied in applications:
        _units(applied, positive=True)
        components[party] = components.get(party, 0) + applied
    residual = amount - sum(components.values())
    if residual < 0:
        raise ValueError('applications exceed cash received')
    if residual:
        components[payer] = components.get(payer, 0) + residual
    return components


@dataclass(frozen=True)
class DraftRow:
    invoice: str
    ordinal: int
    due: int
    amount: int | None
    origin: str

    def __post_init__(self):
        _units(self.due)
        if self.amount is not None:
            _units(self.amount)
        if self.origin not in ('entered', 'calculated', 'unresolved'):
            raise ValueError('unknown row amount origin')
        if (self.origin == 'unresolved') != (self.amount is None):
            raise ValueError('unresolved row must have absent amount')
        if type(self.ordinal) is not int or self.ordinal <= 0:
            raise ValueError('draft ordinal must be positive')


@dataclass(frozen=True)
class Calculation:
    amount: int | None
    amount_origin: str
    rows: tuple[DraftRow, ...]
    unapplied: int | None
    problems: tuple[str, ...]


def calculate(amount: int | None, origin: str, rows: list[DraftRow], *, calculate_unresolved=False,
              source_capacities: dict[str, int] | None = None, source_owners: dict[str, str] | None = None) -> Calculation:
    """Recompute derived values, preserving entered values even when invalid.

Policy adoption is supplied explicitly by the caller. This function never
consults today's company preference to reinterpret a saved draft.
"""
    if origin not in ('entered', 'selection_total', 'unresolved'):
        raise ValueError('unknown header amount origin')
    if amount is not None:
        _units(amount)
    if origin == 'entered' and amount is None or origin == 'unresolved' and amount is not None:
        raise ValueError('header amount does not match its origin')
    ordered = sorted(rows, key=lambda row: (row.ordinal, row.invoice))
    if len({row.invoice for row in rows}) != len(rows) or len({row.ordinal for row in rows}) != len(rows):
        raise ValueError('duplicate invoice or ordinal')
    entered = sum(row.amount for row in rows if row.origin == 'entered')
    remaining = max(0, amount - entered) if origin == 'entered' else None
    source_remaining = dict(source_capacities) if source_capacities is not None else None
    if source_remaining is not None:
        for units in source_remaining.values():
            _units(units)
        if source_owners is None or set(source_owners) != {row.invoice for row in rows}:
            raise ValueError('every selected invoice requires an exact source owner')
        for row in ordered:
            if row.origin == 'entered':
                owner = source_owners[row.invoice]
                source_remaining[owner] = source_remaining.get(owner, 0) - row.amount
    result = []
    for row in ordered:
        if row.origin == 'calculated' or row.origin == 'unresolved' and calculate_unresolved:
            if origin == 'unresolved':
                row = replace(row, amount=None, origin='unresolved')
            else:
                value = row.due if remaining is None else min(row.due, remaining)
                if source_remaining is not None:
                    owner = source_owners[row.invoice]
                    value = min(value, max(0, source_remaining.get(owner, 0)))
                    source_remaining[owner] = source_remaining.get(owner, 0) - value
                row = replace(row, amount=value, origin='calculated')
                if remaining is not None:
                    remaining -= value
        result.append(row)
    problems = []
    if source_remaining is not None:
        problems.extend(f'{owner}:exceeds_source_capacity' for owner, units in source_remaining.items() if units < 0)
    for row in result:
        if row.amount is None:
            problems.append(f'{row.invoice}:unresolved')
        elif row.amount == 0:
            problems.append(f'{row.invoice}:unfunded')
        elif row.amount > row.due:
            problems.append(f'{row.invoice}:exceeds_due')
    total = sum(row.amount or 0 for row in result)
    if origin == 'selection_total':
        amount = total if all(row.amount is not None for row in result) else None
        if amount is not None and amount > INT64_MAX:
            problems.append('amount:out_of_range')
    unapplied = None if amount is None else amount - total
    if unapplied is not None and unapplied < 0:
        problems.append('applications:exceed_amount')
    return Calculation(amount, origin, tuple(result), unapplied, tuple(problems))
