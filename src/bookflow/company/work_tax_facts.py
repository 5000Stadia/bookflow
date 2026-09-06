"""Document-tax work facts and exact economic basis, separate from legacy types.

Version-one parsers and their local half-even checks remain unchanged. Version-two
cells require the complete document effect validator; their local model proves
only references, bounds and line/base consistency. No tax ordinal enters a basis.
"""
import hashlib
import json
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator
from bookflow.company.sales_models import StrictModel
from bookflow.company.sales_facts import TaxRule
from bookflow.company.tax_policy import Policy
from bookflow.company.work_facts import MinorUnits, WorkFacts, WorkLineFacts


class WorkTaxCell(StrictModel):
    rule: TaxRule
    taxable_minor_units: MinorUnits
    tax_minor_units: MinorUnits


class WorkFacts2(WorkFacts):
    schema_version: Literal[2] = 2

    @model_validator(mode='after')
    def captured_policy(self):
        if self.profile.schema_version != 2:
            raise ValueError('work facts2 require a captured version2 tax policy')
        return self


class WorkLineFacts2(WorkLineFacts):
    schema_version: Literal[2] = 2
    taxes: list[WorkTaxCell] = Field(default_factory=list, max_length=200)


def read_facts(value):
    """Dispatch strictly by the stored discriminator, never company knowledge."""
    decoded = json.loads(value) if isinstance(value, str) else value
    model = WorkFacts2 if decoded.get('schema_version', 1) == 2 else WorkFacts
    return model.model_validate(decoded)


def read_line(value):
    decoded = json.loads(value) if isinstance(value, str) else value
    model = WorkLineFacts2 if decoded.get('schema_version', 1) == 2 else WorkLineFacts
    return model.model_validate(decoded)


def economic_basis(line, policy):
    """Exact reviewed basis2 payload; callers separately own root/proof identities."""
    line = WorkLineFacts2.model_validate(line.model_dump(mode='json') if isinstance(line, WorkLineFacts) else line)
    mode = TypeAdapter(Policy).validate_python(policy, strict=True)
    economics = line.model_dump(mode='json', exclude={
        'schema_version', 'completed_quantity_microunits', 'billable',
        'tax_minor_units', 'gross_minor_units', 'taxes'})
    return dict(basis_version=2, sales_tax_calculation=mode, economics=economics,
                tax_rules=[cell.rule.model_dump(mode='json') for cell in line.taxes])


def basis_hash(line, policy):
    payload = economic_basis(line, policy)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()
