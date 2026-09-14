"""Original-value allocation over disjoint received-quantity intervals.

Endpoints are integer microunits, values integer minor units. The original basis is
immutable: releasing a middle claim cannot move a rounding penny to another bill.
"""


def cumulative(quantity, value, endpoint):
    if not (type(quantity) is int and quantity > 0 and type(value) is int and value >= 0
            and type(endpoint) is int and 0 <= endpoint <= quantity):
        raise ValueError('invalid receipt interval basis')
    return (2 * value * endpoint + quantity) // (2 * quantity)


def interval_value(quantity, value, start, end):
    if not 0 <= start < end <= quantity:
        raise ValueError('invalid receipt interval')
    return cumulative(quantity, value, end) - cumulative(quantity, value, start)


def allocate(quantity, value, occupied, requested):
    """Claim the lowest available spans, retaining each exact original value."""
    cumulative(quantity, value, 0)
    if type(requested) is not int or requested <= 0:
        raise ValueError('received quantity must be positive')
    cursor, gaps = 0, []
    for start, end in sorted(occupied):
        if not cursor <= start < end <= quantity:
            raise ValueError('overlapping or out-of-range receipt claims')
        if cursor < start:
            gaps.append((cursor, start))
        cursor = end
    if cursor < quantity:
        gaps.append((cursor, quantity))
    if sum(end - start for start, end in gaps) < requested:
        raise ValueError('quantity exceeds unbilled receipt quantity')
    result = []
    for start, end in gaps:
        stop = min(end, start + requested)
        result.append((start, stop, interval_value(quantity, value, start, stop)))
        requested -= stop - start
        if not requested:
            break
    return result


def component_span(quantity, total, shipping, start, end):
    """Product and freight own their pennies independently on the same interval."""
    return (interval_value(quantity, total - shipping, start, end),
            interval_value(quantity, shipping, start, end))
