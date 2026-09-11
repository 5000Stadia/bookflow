"""What a returned quantity is worth, and which quantity is still there to return.

One rule decides every cent a linked return carries, and it is stated once here because a
second statement of it somewhere else is a second answer waiting to disagree.

**Ownership is by endpoint.** A captured source line has a base quantity ``Q`` and a captured
net ``N``. A returned half-open interval ``[a, b)`` of that quantity owns

    floor(N*b/Q) - floor(N*a/Q)

cents of the net, and a captured tax cell ``T`` on that line owns, over the net interval
``[u, v)`` the quantity interval produced,

    floor(T*v/N) - floor(T*u/N)

cents of that tax. Both are differences of the same cumulative function evaluated at the two
endpoints, so any set of disjoint intervals telescopes to exactly ``N`` and exactly ``T``,
whatever order the intervals were claimed in; releasing one interval and claiming it again
returns the identical cents; and no interval can ever be worth a cent that another interval
also claims. No running-remainder scheme has that property, which is why none is used: a
remainder carried forward makes the second return depend on the first, and then a release in
the middle is unpriceable.

**The residue is the gaps.** Everything still returnable on a source line is the complement of
its active claims inside ``[0, Q)``, taken lowest first. That is a function of the stored
intervals alone, so two writers preparing the same return read the same residue, and the one
that commits second re-reads it inside the writer transaction and finds its span gone.
"""
from bookflow.core.errors import BookflowError


def owned(total: int, quantity: int, start: int, end: int) -> int:
    """The endpoint-owned share of ``total`` for the half-open interval ``[start, end)``."""
    if quantity <= 0 or start < 0 or end < start or end > quantity:
        raise BookflowError('E_INTERNAL', message='Invalid returned interval for a captured line')
    return total * end // quantity - total * start // quantity


def net_interval(net: int, quantity: int, start: int, end: int) -> tuple[int, int]:
    """The cumulative net endpoints a quantity interval maps onto."""
    return net * start // quantity, net * end // quantity


def merged(claims) -> list[tuple[int, int]]:
    """Active claims as disjoint ascending intervals."""
    ordered = sorted((int(start), int(end)) for start, end in claims)
    out: list[tuple[int, int]] = []
    for start, end in ordered:
        if out and start <= out[-1][1]:
            if start < out[-1][1]:
                raise BookflowError('E_INTERNAL', message='Overlapping returned intervals on one source line')
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def available(claims, quantity: int) -> list[tuple[int, int]]:
    """The gaps inside ``[0, quantity)`` no active claim covers, lowest first."""
    gaps, cursor = [], 0
    for start, end in merged(claims):
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < quantity:
        gaps.append((cursor, quantity))
    return gaps


def remaining(claims, quantity: int) -> int:
    return sum(end - start for start, end in available(claims, quantity))


def take(claims, quantity: int, wanted: int) -> list[tuple[int, int]]:
    """The lowest-first intervals totalling ``wanted``, or a typed refusal with nothing taken."""
    if wanted <= 0:
        raise BookflowError('E_INTERNAL', message='A return must claim a positive quantity')
    left, taken = wanted, []
    for start, end in available(claims, quantity):
        if left <= 0:
            break
        span = min(end - start, left)
        taken.append((start, start + span))
        left -= span
    if left > 0:
        raise BookflowError('E_RETURN_EXHAUSTED', details={
            'requested_base_microunits': wanted,
            'remaining_base_microunits': wanted - left,
            'next': 'Return at most what is left on this invoice line, or credit the customer without linking it.'})
    return taken


def priced(intervals, quantity: int, net: int, taxes) -> dict:
    """Exact net and per-cell tax for a set of claimed intervals on one captured line.

    ``taxes`` is the source line's captured cells, each a mapping with ``taxable_minor_units``
    and ``tax_minor_units``; both are partitioned by the identical endpoint rule over the net
    interval, so a line's taxable base and its tax both telescope to the captured totals.
    """
    net_total, cells = 0, [dict(cell, taxable_minor_units=0, tax_minor_units=0) for cell in taxes]
    for start, end in intervals:
        net_total += owned(net, quantity, start, end)
        low, high = net_interval(net, quantity, start, end)
        for index, cell in enumerate(taxes):
            if net <= 0:
                continue
            cells[index]['taxable_minor_units'] += owned(int(cell['taxable_minor_units']), net, low, high)
            cells[index]['tax_minor_units'] += owned(int(cell['tax_minor_units']), net, low, high)
    return dict(net_minor_units=net_total, taxes=cells)
