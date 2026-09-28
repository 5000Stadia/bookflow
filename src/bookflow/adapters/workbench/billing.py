"""Browser projections and typed selection translation for the shared billing commands."""
from copy import deepcopy
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError
from bookflow.company.billing_facts import BILLING_KINDS

# The nouns a sale can be billed from, spelled as the URL segments they are reached by. Derived
# from the one place the billable work kinds are declared, so a kind that gains billing gains
# its billing page, its conversion forms and its remaining-work panel in the same change.
NOUNS = tuple(kind.replace('_', '-') for kind in BILLING_KINDS)


def is_conversion(noun, verb):
    return noun in NOUNS and verb in ('invoice', 'sales-receipt')


def context(result, company_id, source=None):
    from bookflow.adapters.workbench.work import KIND_TITLES
    out = deepcopy(result)
    out['source_url'] = f"/c/{company_id}/{out['source_kind'].replace('_', '-')}/{out['source_id']}"
    out['owner_url'] = f"/c/{company_id}/{out['owner_kind'].replace('_', '-')}/{out['owner_id']}"
    # What the owning document is called where a person reads it, rather than the stored kind:
    # "Time activity" is the table's spelling, and no bookkeeper calls it that.
    out['owner_label'] = KIND_TITLES.get(out['owner_kind'],
                                         out['owner_kind'].replace('_', ' ').capitalize())
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


def _units(microunits):
    from decimal import Decimal
    return format(Decimal(microunits) / Decimal(1_000_000), 'f').rstrip('0').rstrip('.') or '0'


def billed_line_summaries(record):
    """One plain sentence per billed line, read from the record already shown.

    The raw work-document facts and span proofs stay below it. Naming the earlier bills
    that took the other parts would need further reads, so the sentence counts parts only.
    """
    rows = []
    for source in (record.get('revision') or {}).get('billing_sources') or []:
        proof = source.get('allocation_proof') or {}
        facts = source.get('facts_snapshot') or {}
        line = facts.get('line') or {}
        spans = len(proof.get('spans') or [])
        quoted = proof.get('quoted_quantity_microunits')
        text = f"Bills {_units(source['quantity_microunits'])}"
        if quoted:
            text += f" of {_units(quoted)} quoted"
        text += ' units'
        if spans > 1:
            text += f", in {spans} separate parts of the source line (the other parts are on other bills)"
        rows.append(dict(source=' '.join(str(part) for part in (facts.get('source_number'), facts.get('title')) if part)
                         or source.get('source_document_id'),
                         line=line.get('description') or line.get('item_label') or '', text=text + '.'))
    return rows
