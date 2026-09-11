"""Read-only bill presentation; never resolve a default or recalculate money.

The payables mirror of ``sales.py``, and deliberately the same two jobs: what the correction
form opens with, and what a saved bill shows. Nothing here reads a record, resolves a
default or computes an amount — every value comes from what ``bill show`` returned, and a
bill read years later shows what its revision captured rather than what the vendor, the
account or the terms record says today.

Integration: use ``editable_values`` for the correction form's originals, and render
``bill_detail.html`` with ``bill=detail_context(result, company_id)``.
"""
from copy import deepcopy
from urllib.parse import quote

# One home for the order an address reads in, shared with the printed documents.
from bookflow.documents.model import address_lines


def _id(value):
    return value['id'] if isinstance(value, dict) and 'id' in value else None


def _line_class(line, header_class):
    """What this saved line said about its class, in the words the input uses.

    The revision stores the resolved class and where it came from, not the mode that was
    typed, so the mode is read back out of the pair. A line that resolved to no class while
    the bill itself carries one was deliberately left unclassified, and a correction that
    reopened it as ``inherit`` would silently reclassify it on the next save.
    """
    snapshot = line.get('line_snapshot') or {}
    origin = (snapshot.get('origins') or {}).get('class_id') or {}
    if origin.get('kind') == 'explicit' and line.get('class_id'):
        return {'class_id': line['class_id'], 'class_mode': 'value'}
    if line.get('class_id') is None and header_class is not None:
        return {'class_mode': 'none'}
    return {'class_mode': 'inherit'}


def editable_values(record):
    """Display resolved values, retaining null/false/empty and stable line ids.

    This is the form's comparison baseline, not a replacement payload: a leaf equal to its
    original is not submitted, which is what lets a header-only correction reach the writer
    without an ``expenses`` grid and keep the saved lines exactly as they were captured.
    """
    revision = record['revision']
    profile = revision['profile']
    header_class = _id(profile.get('class_id'))
    values = {
        'number': revision['number'],
        'date': revision['date'],
        'memo': revision['memo'],
        'vendor': profile['vendor']['id'],
        'ap_account': profile['ap_account']['id'],
        'terms': _id(profile.get('terms')),
        'due_date': profile['due_date'],
        'supplier_reference': profile.get('supplier_reference'),
        'class_id': header_class,
        'custom_fields': {field['definition_id']: deepcopy(field['value'])
                          for field in revision.get('custom_fields', [])},
        'expenses': [],
    }
    for line in revision['expenses']:
        row = {'line_id': line['line_id'], 'account': line['account_id'],
               'amount': line['amount']['amount'], 'memo': line['memo'],
               'customer': line['customer_id'], 'billable': line['billable']}
        row.update(_line_class(line, header_class))
        values['expenses'].append(row)
    return values


def detail_context(record, company_id, *, preview=False):
    """Template context for one saved bill revision, including safe local links."""
    record = deepcopy(record)
    revision = record['revision']
    base = '/c/' + quote(str(company_id), safe='') + '/bill'
    url = base + '/' + quote(str(record['id']), safe='')
    links = []
    if not preview:
        number = revision['revision_number']
        if number > 1:
            links.append(('Previous revision', url + '?revision_number=' + str(number - 1)))
        if revision['id'] != record['current_revision_id']:
            links += [('Next revision', url + '?revision_number=' + str(number + 1)),
                      ('Current revision', url)]
    issuer = revision.get('issuer_snapshot') or {}
    return dict(record=record, revision=revision, profile=revision['profile'],
                preview=preview, links=links,
                settlement=record.get('settlement_current'),
                duplicates=record.get('duplicate_references') or [],
                issuer=issuer,
                issuer_address=address_lines({key.removeprefix('address_'): value
                                              for key, value in issuer.items()
                                              if key.startswith('address_')}))
