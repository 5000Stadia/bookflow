"""Typed, immutable allocation evidence; ordinary sale inputs cannot supply it."""
from fractions import Fraction
from math import gcd, lcm
from typing import Annotated

from pydantic import Field, model_validator

from bookflow.company.sales_models import StrictModel, Fingerprint
from bookflow.company.work_models import CanonicalId
from bookflow.core.exact import INT64_MAX, round_ratio_half_even

UnsignedText = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)$', max_length=100)]
Coordinate = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)$', max_length=49)]


class ExactFraction(StrictModel):
    numerator: UnsignedText
    denominator: UnsignedText

    @model_validator(mode='after')
    def canonical(self):
        n, d = int(self.numerator), int(self.denominator)
        if d <= 0 or gcd(n, d) != 1:
            raise ValueError('fraction must be reduced with a positive denominator')
        return self

    @classmethod
    def of(cls, value: Fraction):
        return cls(numerator=str(value.numerator), denominator=str(value.denominator))


class AllocationSpan(StrictModel):
    start: Coordinate
    end: Coordinate


class AllocationProof(StrictModel):
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_line_id: CanonicalId
    root_document_id: CanonicalId
    root_line_id: CanonicalId
    source_basis_hash: Fingerprint
    quoted_quantity_microunits: int = Field(gt=0, le=INT64_MAX)
    quoted_base_quantity_microunits: int = Field(gt=0, le=INT64_MAX)
    quoted_net_minor_units: int = Field(ge=0, le=INT64_MAX)
    denominator: Coordinate
    spans: list[AllocationSpan] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def canonical(self):
        d = int(self.denominator)
        if d != lcm(self.quoted_quantity_microunits, max(self.quoted_net_minor_units, 1), 100_000_000):
            raise ValueError('allocation denominator disagrees with quoted quantity/net')
        previous = -1
        for span in self.spans:
            a, b = int(span.start), int(span.end)
            if not (previous < a < b <= d):
                raise ValueError('allocation spans must be bounded, sorted, disjoint and coalesced')
            previous = b
        return self

    def intervals(self):
        return tuple((int(span.start), int(span.end)) for span in self.spans)

    def quantity(self, *, base=False):
        q = self.quoted_base_quantity_microunits if base else self.quoted_quantity_microunits
        return Fraction(q * sum(b-a for a, b in self.intervals()), int(self.denominator) * 1_000_000)

    def net(self):
        n, d = self.quoted_net_minor_units, int(self.denominator)
        return sum(round_ratio_half_even(n*b, d) - round_ratio_half_even(n*a, d)
                   for a, b in self.intervals())
