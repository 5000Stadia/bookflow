"""Exact progress-billing entitlement arithmetic, independent of sale and work services.

Coordinates are unsigned 160-bit integers; source quantities and net are bounded
by signed 64-bit storage. Span collections may be empty (zero scope). Streams are
validated incrementally, without retaining source history. Consumers that stop
early validate only the prefix they read; callers must supply finite streams.
"""

from collections.abc import Iterable, Iterator, Mapping
from fractions import Fraction
from math import lcm
import re

Span = tuple[int, int]
_MAX_COORDINATE = (1 << 160) - 1
_MAX_SOURCE = (1 << 63) - 1
_SCALE = 1_000_000


class AllocationRangeError(ValueError):
    """Invalid shape, numeric bounds, or unavailable entitlement."""


class FragmentationError(AllocationRangeError):
    """The requested selection needs more than the permitted number of spans."""


def _integer(value: int, name: str, minimum: int = 0,
             maximum: int = _MAX_COORDINATE) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AllocationRangeError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def denominator(quantity_microunits: int, net_minor_units: int) -> int:
    """Return lcm(Q, max(N, 1), 100000000) for a captured source basis."""
    _integer(quantity_microunits, "quantity_microunits", 1, _MAX_SOURCE)
    _integer(net_minor_units, "net_minor_units", 0, _MAX_SOURCE)
    return lcm(quantity_microunits, max(net_minor_units, 1), 100_000_000)


def _rounded(total: int, coordinate: int, denominator: int) -> int:
    whole, remainder = divmod(total * coordinate, denominator)
    return whole + (2 * remainder > denominator or
                    (2 * remainder == denominator and whole % 2 == 1))


def portion(total: int, start: int, end: int, denominator: int) -> int:
    """Difference of half-even rounded endpoint totals; equal endpoints own zero."""
    _integer(total, "total", 0, _MAX_SOURCE)
    _integer(denominator, "denominator", 1)
    _integer(start, "start", 0, denominator)
    _integer(end, "end", start, denominator)
    return _rounded(total, end, denominator) - _rounded(total, start, denominator)


def _spans(spans: Iterable[Span], denominator: int) -> Iterator[Span]:
    if isinstance(spans, (str, bytes, bytearray, Mapping)):
        raise AllocationRangeError("spans must be an iterable of start/end pairs")
    try:
        stream = iter(spans)
    except TypeError as exc:
        raise AllocationRangeError("spans must be an iterable of start/end pairs") from exc
    for span in stream:
        if not isinstance(span, (tuple, list)) or len(span) != 2:
            raise AllocationRangeError("each span must be a start/end pair")
        start, end = span
        _integer(start, "span start", 0, denominator)
        _integer(end, "span end", 0, denominator)
        if start >= end:
            raise AllocationRangeError("spans must have positive width")
        yield start, end


def canonical_spans(spans: Iterable[Span], denominator: int,
                    max_spans: int = 200) -> tuple[Span, ...]:
    """Validate already sorted, disjoint, coalesced spans; never repair a proof."""
    _integer(denominator, "denominator", 1)
    _integer(max_spans, "max_spans", 1)
    result: list[Span] = []
    for start, end in _spans(spans, denominator):
        if result and start <= result[-1][1]:
            raise AllocationRangeError("spans must be sorted, disjoint and coalesced")
        if len(result) == max_spans:
            raise FragmentationError("selection exceeds max_spans")
        result.append((start, end))
    return tuple(result)


def _coalesced(spans: Iterable[Span], denominator: int) -> Iterator[Span]:
    pending: Span | None = None
    for start, end in _spans(spans, denominator):
        if pending is not None:
            if start < pending[1]:
                raise AllocationRangeError("stream must be sorted and nonoverlapping")
            if start == pending[1]:
                pending = pending[0], end
                continue
            yield pending
        pending = start, end
    if pending is not None:
        yield pending


def free_spans(occupied: Iterable[Span], denominator: int) -> Iterator[Span]:
    """Yield gaps in a sorted occupied stream, accepting adjacent occupied spans."""
    _integer(denominator, "denominator", 1)
    cursor = 0
    for start, end in _coalesced(occupied, denominator):
        if cursor < start:
            yield cursor, start
        cursor = end
    if cursor < denominator:
        yield cursor, denominator


def _allocation_basis(denominator: int, source_net: int, max_spans: int) -> None:
    _integer(denominator, "denominator", 1)
    _integer(source_net, "source_net", 0, _MAX_SOURCE)
    _integer(max_spans, "max_spans", 1)
    if source_net and denominator % source_net:
        raise AllocationRangeError("denominator must be divisible by source_net")


def allocate(free: Iterable[Span], *, denominator: int, source_net: int,
             length: int | None = None, net_amount: int | None = None,
             max_spans: int = 200) -> tuple[Span, ...]:
    """Select earliest free scope by exactly one positive length or net amount.

    Net requests skip zero-capacity gaps, consume whole gaps when capacity is no
    greater than the remainder, and otherwise use the exact net inverse endpoint.
    No partial result is returned on exhaustion or fragmentation. Successful
    allocation stops reading once the requested scope is selected.
    """
    _allocation_basis(denominator, source_net, max_spans)
    if (length is None) == (net_amount is None):
        raise AllocationRangeError("provide exactly one of length and net_amount")
    if length is not None:
        remaining = _integer(length, "length", 1, denominator)
    else:
        remaining = _integer(net_amount, "net_amount", 1, source_net)
    selected: list[Span] = []
    for start, end in _coalesced(free, denominator):
        capacity = (end - start if length is not None else
                    _rounded(source_net, end, denominator) -
                    _rounded(source_net, start, denominator))
        if capacity == 0:
            continue
        if len(selected) == max_spans:
            raise FragmentationError("selection exceeds max_spans")
        if capacity > remaining:
            end = (start + remaining if length is not None else
                   (_rounded(source_net, start, denominator) + remaining) *
                   (denominator // source_net))
        selected.append((start, end))
        remaining -= min(remaining, capacity)
        if remaining == 0:
            return tuple(selected)
    raise AllocationRangeError("requested entitlement is unavailable")


def recommended_net(free: Iterable[Span], *, denominator: int, source_net: int,
                    max_spans: int = 200) -> int:
    """Net of the earliest at-most-max_spans positive-capacity free intervals.

    Zero-capacity prefixes do not count toward the limit. Zero means no chargeable
    scope remains; otherwise allocate(net_amount=result) fits the same span limit
    when called with a fresh iterator over the same free intervals.
    """
    _allocation_basis(denominator, source_net, max_spans)
    total = count = 0
    for start, end in _coalesced(free, denominator):
        capacity = (_rounded(source_net, end, denominator) -
                    _rounded(source_net, start, denominator))
        if capacity:
            total += capacity
            count += 1
            if count == max_spans:
                break
    return total


def spans_available(requested: Iterable[Span], free: Iterable[Span],
                    denominator: int) -> bool:
    """Whether the exact canonical proof is a subset of current free scope.

    Malformed inputs raise AllocationRangeError; a valid but unavailable proof
    returns False. No substitute allocation is selected. The full free stream is
    checked, even if the requested proof is empty or already unavailable.
    """
    proof = canonical_spans(requested, denominator)
    index = 0
    available = True
    for start, end in _coalesced(free, denominator):
        while index < len(proof) and proof[index][0] < end:
            a, b = proof[index]
            if a < start or b > end:
                available = False
            index += 1
    return available and index == len(proof)


def fraction(source_quantity_microunits: int, spans: Iterable[Span],
             denominator: int) -> Fraction:
    """Exact allocated quantity in selling units (one unit is 1000000 micros)."""
    _integer(source_quantity_microunits, "source_quantity_microunits", 1, _MAX_SOURCE)
    proof = canonical_spans(spans, denominator)
    return Fraction(source_quantity_microunits * sum(b - a for a, b in proof),
                    denominator * _SCALE)


def _fraction(value: Fraction) -> Fraction:
    if not isinstance(value, Fraction) or value < 0:
        raise AllocationRangeError("quantity must be a nonnegative Fraction")
    return value


def exact_microunits(value: Fraction) -> int | None:
    """Return exact microunits, or None when the quantity needs finer precision."""
    scaled = _fraction(value) * _SCALE
    return scaled.numerator if scaled.denominator == 1 else None


def format_fraction(value: Fraction) -> str:
    """Exact decimal with at most six places, otherwise reduced numerator/denominator."""
    micros = exact_microunits(value)
    if micros is None:
        return f"{value.numerator}/{value.denominator}"
    whole, remainder = divmod(micros, _SCALE)
    if remainder == 0:
        return str(whole)
    return f"{whole}.{remainder:06d}".rstrip("0")


def coordinate_hex(value: int) -> str:
    """Encode an unsigned 160-bit endpoint as fixed-width lowercase hexadecimal."""
    return f"{_integer(value, 'coordinate'):040x}"


def coordinate_int(value: str) -> int:
    """Decode only the canonical fixed-width lowercase endpoint representation."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise AllocationRangeError("coordinate must be 40 lowercase hexadecimal characters")
    return int(value, 16)
