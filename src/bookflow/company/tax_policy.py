"""Captured tax policy and field-specific origin; pricing versions stay separate."""
from typing import Literal
from pydantic import BaseModel, ConfigDict

Policy = Literal['line_component_half_even', 'line_combined_half_up', 'invoice_combined_half_up']
LEGACY = 'line_component_half_even'
DEFAULT = 'invoice_combined_half_up'


class TaxOrigin(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['legacy_implicit', 'default', 'explicit']
    source_id: str | None = None


def effective(profile):
    return getattr(profile, 'sales_tax_calculation', None) or LEGACY


def origin(profile):
    return getattr(profile, 'tax_policy_origin', None) or TaxOrigin(kind='legacy_implicit')


def resolve(inp, previous, info):
    field = 'sales_tax_calculation'
    if field in inp.model_fields_set:
        return getattr(inp, field), TaxOrigin(kind='explicit')
    if (previous is None or field in inp.use_defaults
            or (inp.refresh_defaults and origin(previous).kind == 'default')):
        return info[field], TaxOrigin(kind='default', source_id=info['id'])
    return effective(previous), origin(previous)
