"""Browser projections and checkbox translation for the shared billing commands."""
from copy import deepcopy
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError


def is_conversion(noun, verb):
    return noun in ('estimate', 'work-order') and verb in ('invoice', 'sales-receipt')


def context(result, company_id):
    out = deepcopy(result)
    out['source_url'] = f"/c/{company_id}/{out['source_kind'].replace('_', '-')}/{out['source_id']}"
    out['owner_url'] = f"/c/{company_id}/{out['owner_kind'].replace('_', '-')}/{out['owner_id']}"
    for row in [out, *out['lines']]:
        for key, value in list(row.items()):
            if key.endswith('_minor_units'):
                row[key.removesuffix('_minor_units')] = Money(value, out['currency']).to_dict()['amount']
    return out


def selection(raw, form):
    result = dict(raw)
    if form.get('billing-selection') == 'selected':
        result['line_ids'] = [key.removeprefix('billing-line:') for key, value in form.items()
                              if key.startswith('billing-line:') and value == '1']
        if not result['line_ids']:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'line_ids',
                'problem': 'Select at least one whole source line.'}]})
    return result
