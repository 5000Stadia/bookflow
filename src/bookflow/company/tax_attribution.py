"""Resolve tax ordinals and complete tax snapshots without writing in preview."""
from typing import Literal
import json
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, field_validator
from bookflow.company import schema as c, tax_policy
from bookflow.company.tax_calculations import TaxCalculation, TaxLine, TaxRule, calculate_tax


class TaxAttribution(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1
    origin: tax_policy.TaxOrigin
    calculation: TaxCalculation

    @field_validator('calculation', mode='before')
    @classmethod
    def wire_calculation(cls, value):
        # Stored replay results are decoded JSON. Preserve the calculator's
        # strict scalar checks while restoring its immutable tuple/enum DTOs.
        if isinstance(value, dict):
            return TaxCalculation.model_validate_json(json.dumps(value, allow_nan=False))
        return value


class TaxDetails(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    policy: tax_policy.Policy
    origin: tax_policy.TaxOrigin
    legacy_interpretation: bool
    attribution: TaxAttribution | None = None


def prospective(db, document_id, line_ids, *, work=False):
    """Existing keys win; an unkeyed legacy owner assigns its entire population."""
    keys = c.work_tax_line_keys if work else c.sales_tax_line_keys
    identities = c.work_line_identities if work else c.document_line_identities
    owner = 'document_id' if work else 'transaction_id'
    saved = dict(db.conn.execute(sa.select(keys.c.line_id, keys.c.tax_ordinal).where(keys.c[owner] == document_id)).all())
    population = list(db.conn.execute(sa.select(identities.c.id).where(identities.c[owner] == document_id)).scalars())
    assigned = dict(saved)
    maximum = max(assigned.values(), default=0)
    for identity in sorted(population, key=lambda value: value.encode('utf-8')):
        if identity not in assigned:
            maximum += 1
            assigned[identity] = maximum
    ordinals = []
    for identity in line_ids:
        if identity is not None and identity in assigned:
            ordinals.append(assigned[identity])
        else:
            maximum += 1
            ordinals.append(maximum)
            if identity is not None:
                assigned[identity] = maximum
    return ordinals, {key: value for key, value in assigned.items() if key not in saved}


def calculate(lines, profile, currency, ordinals):
    inputs = [TaxLine(tax_ordinal=ordinal, net_minor_units=line['net_minor_units'],
        rules=tuple(TaxRule.model_validate(tax['rule'].model_dump()) for tax in line['taxes']))
        for line, ordinal in zip(lines, ordinals, strict=True)]
    result = calculate_tax(inputs, policy=tax_policy.effective(profile), currency=currency)
    return TaxAttribution(origin=tax_policy.origin(profile), calculation=result)


def apply(lines, attribution, ordinals):
    result = attribution.calculation
    totals = {line.tax_ordinal: line for line in result.lines}
    cells = {(cell.tax_ordinal, cell.rule.id): cell for bucket in result.buckets for cell in bucket.cells}
    for line, ordinal in zip(lines, ordinals, strict=True):
        total = totals[ordinal]
        line.update(tax_ordinal=ordinal, tax_minor_units=total.tax_minor_units, gross_minor_units=total.gross_minor_units)
        for tax in line['taxes']:
            tax['tax_minor_units'] = cells[ordinal, tax['rule'].id].tax_minor_units


def details(profile, snapshot=None):
    return TaxDetails(policy=tax_policy.effective(profile), origin=tax_policy.origin(profile),
        legacy_interpretation=profile.schema_version == 1,
        attribution=TaxAttribution.model_validate_json(snapshot) if snapshot else None)


def semantic_profile(profile):
    """Representation upgrades alone never cause a commercial revision."""
    value = profile.model_dump()
    value.pop('schema_version')
    value.update(sales_tax_calculation=tax_policy.effective(profile), tax_policy_origin=tax_policy.origin(profile).model_dump())
    return value


def validate_sales(s, header, revision, profile, pending, require):
    """Rebuild every cell from nets, applicable captured rules and owned stable keys.

    The calculator's output DTO is deliberately not trusted as a validator.
    This check also verifies prospective key allocation against stored identities.
    """
    snapshots = pending['sales_tax_attributions']
    require(profile.schema_version == 2, 'new commercial revision requires captured policy')
    require(len(snapshots) == 1 and snapshots[0]['revision_id'] == revision['id'], 'tax revision ownership')
    envelopes = sorted(pending['document_lines'], key=lambda line: line['position'])
    new_ids = {row['id'] for row in pending['document_line_identities']}
    ordinals, additions = prospective(s.company, header['id'],
        [None if line['line_id'] in new_ids else line['line_id'] for line in envelopes])
    additions.update({line['line_id']: ordinal for line, ordinal in zip(envelopes, ordinals) if line['line_id'] in new_ids})
    actual_keys = {row['line_id']: row['tax_ordinal'] for row in pending['sales_tax_line_keys']}
    require(actual_keys == additions, 'tax keys differ from immutable historical ordering')
    mapping = pending['sales_tax_attribution_lines']
    expected_mapping = {(line['id'], line['line_id'], ordinal) for line, ordinal in zip(envelopes, ordinals)}
    require(len(mapping) == len(envelopes) and
        {(r['document_line_id'], r['line_id'], r['tax_ordinal']) for r in mapping} == expected_mapping
        and all(r['revision_id'] == revision['id'] for r in mapping), 'tax line ownership or ordinal differs')
    from bookflow.company.sales_facts import SalesLineProfile
    profiles = {row['document_line_id']: row for row in pending['sales_line_profiles']}
    inputs = []
    for envelope in envelopes:
        row = profiles[envelope['id']]
        facts = SalesLineProfile.model_validate_json(row['item_snapshot'])
        taxable = profile.preferences.sales_tax_enabled and facts.tax_code is not None and facts.tax_code.taxable
        exempt = profile.customer_tax_code is not None and not profile.customer_tax_code.taxable
        rules = profile.tax_rules if taxable and not exempt else []
        require(rules is not None, 'missing applicable rules')
        inputs.append(dict(net_minor_units=row['net_minor_units'], taxes=[dict(rule=rule) for rule in rules]))
    expected = calculate(inputs, profile, revision['currency'], ordinals)
    captured = TaxAttribution.model_validate_json(snapshots[0]['facts_snapshot'])
    require(captured == expected, 'exact tax buckets, cells or origin differ from authoritative facts')
    id_by_ordinal = {ordinal: line['id'] for line, ordinal in zip(envelopes, ordinals)}
    return {(id_by_ordinal[cell.tax_ordinal], cell.rule.id): cell.tax_minor_units
            for bucket in expected.calculation.buckets for cell in bucket.cells}
