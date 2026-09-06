"""Browser projections and typed selection translation for the shared billing commands."""
from copy import deepcopy
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError


def is_conversion(noun, verb):
    return noun in ('estimate', 'work-order') and verb in ('invoice', 'sales-receipt')


def context(result, company_id, source=None):
    out = deepcopy(result)
    out['source_url'] = f"/c/{company_id}/{out['source_kind'].replace('_', '-')}/{out['source_id']}"
    out['owner_url'] = f"/c/{company_id}/{out['owner_kind'].replace('_', '-')}/{out['owner_id']}"
    quoted = {line['line_id']: line for line in (source or {}).get('revision', {}).get('lines', [])}
    for line in out['lines']:
        line['quoted_rate'] = (quoted.get(line['line_id'], {}).get('unit_price') or {}).get('amount')
    for row in [out, *out['lines']]:
        for key, value in list(row.items()):
            if key.endswith('_minor_units'):
                row[key.removesuffix('_minor_units')] = Money(value, out['currency']).to_dict()['amount']
    return out


def selection(raw, form):
    result = {k: v for k, v in raw.items() if k not in ('line_ids', 'selections', 'percent')}
    mode = form.get('billing-selection', 'remaining')
    selected = [key.removeprefix('billing-line:') for key, value in form.items()
                if key.startswith('billing-line:') and value == '1']
    if mode == 'percent':
        result['percent'] = form.get('billing-percent', '')
    elif mode in ('selected', 'partial', 'recovery'):
        if not selected:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'line_ids',
                'problem': 'Select at least one source line.'}]})
        if mode == 'selected':
            result['line_ids'] = selected
        else:
            result['selections'] = []
            for identity in selected:
                kind = 'net_amount' if mode == 'recovery' else form.get('billing-mode:' + identity, 'quantity')
                if kind not in ('quantity', 'net_amount', 'percent', 'rebill_allocation_id'):
                    raise BookflowError('E_VALIDATION', message='Choose quantity, net, scope percent or an earlier allocation.')
                value = form.get(('billing-recovery:' if mode == 'recovery' else 'billing-rebill:' if kind == 'rebill_allocation_id' else 'billing-value:') + identity, '')
                result['selections'].append({'line_id': identity, kind: value})
    elif mode != 'remaining':
        raise BookflowError('E_VALIDATION', message='Choose a billing selection mode.')
    return result


def progress_context(result, billing):
    """Display the core's proposed work progress; never derive financial totals."""
    rows = deepcopy((result or {}).get('billing_progress', []))
    source_lines = {line['line_id']: line for line in (billing or {}).get('lines', [])}
    for row in rows:
        source = source_lines.get(row['line_id'], {})
        row['description'] = source.get('description')
        row['quoted_quantity'] = source.get('quantity')
        for stage in ('previous', 'current', 'cumulative', 'remaining'):
            for kind in ('net', 'tax', 'gross'):
                row[stage][kind] = Money(row[stage][kind + '_minor_units'], result['currency']).to_dict()['amount']
    return rows
