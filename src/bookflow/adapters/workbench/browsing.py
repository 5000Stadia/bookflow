"""URL and presentation translation for the shared master browsing commands."""
from __future__ import annotations
import json
from urllib.parse import urlencode
from bookflow.company.query_catalog import key_for
from bookflow.core.errors import BookflowError


def page(request, company_id, noun, definition, verbs, meta, run, render):
    raw = {}
    query = request.query_params
    for name in ('query', 'sort', 'direction', 'cursor'):
        if query.get(name):
            raw[name] = query[name]
    raw['include_inactive'] = query.get('include_inactive') == '1'
    try:
        raw['limit'] = int(query.get('limit', '50'))
        custom = json.loads(query.get('custom_filters', '[]'))
    except (ValueError, TypeError) as exc:
        raise BookflowError('E_LIST_FILTER', message='A saved list control is invalid. Clear that control and try again.') from exc
    raw['custom_filters'] = custom
    raw['filter'] = [value for value in query.getlist('filter') if value.strip()]
    active_only = query.get('metadata_active_only') == '1'
    metadata = run(f'{noun} query options', {'include_inactive': not active_only}, company_id)
    filters = run(f'{noun} query options', {'kind': 'filters', 'include_inactive': not active_only}, company_id)
    text = query.get('columns')
    raw['columns'] = [key_for(noun, value.strip()) for value in text.split(',')] if text is not None else metadata['default_columns']
    result = run(f'{noun} query', raw, company_id)
    selected = result['columns']
    criteria = []
    # Explicit criteria can refer to metadata beyond the first page. Resolve only those keys.
    def filter_key(value):
        key = value.split('=', 1)[0]
        return 'custom:' + key[len('custom_fields.'):] if key.startswith('custom_fields.') else key
    keys = list(dict.fromkeys([filter_key(value) for value in raw['filter']] +
        ['custom:' + value['definition'] for value in custom]))
    descriptions = {}
    for offset in range(0, len(keys), 64):
        matching = run(f'{noun} query options', {'kind': 'filters', 'keys': keys[offset:offset + 64], 'limit': 200}, company_id)
        descriptions.update({item['key']: item for item in matching['items']})
    for value in raw['filter']:
        key, stored = value.split('=', 1)
        descriptor = descriptions.get(filter_key(value))
        if descriptor is None:
            raise BookflowError('E_LIST_FILTER', message='This saved filter is not available in the list controls.', details={'field': key})
        display = stored
        if descriptor['kind'] == 'money':
            from bookflow.core.money import Money
            display = Money(int(stored), descriptor['currency']).to_dict()['amount'] + ' ' + descriptor['currency']
        criteria.append({'descriptor': descriptor, 'operator': 'eq', 'value': stored, 'display': display, 'legacy': value})
    for criterion in custom:
        descriptor = descriptions['custom:' + criterion['definition']]
        display = criterion.get('value')
        if descriptor['kind'] == 'choice' and criterion['kind'] != 'presence':
            option = run(f'{noun} query options', {'kind': 'choices', 'definition': descriptor['definition'], 'keys': [display]}, company_id)
            display = option['items'][0]['label'] if option['items'] else display
        criteria.append({'descriptor': descriptor, 'operator': criterion['operator'], 'value': criterion.get('value'),
            'display': display, 'custom': criterion})
    pairs = [(key, value) for key, value in query.multi_items() if key != 'cursor']
    def url(**changes):
        return request.url.path + '?' + urlencode([(key, value) for key, value in pairs if key not in changes] +
            [(key, value) for key, value in changes.items() if value is not None])
    current_sort = definition.resolve_sort(raw.get('sort'), raw.get('direction', 'asc'))[0]
    headings = {item['key']: url(sort=item['sort_key'], direction='desc' if current_sort.field == item['sort_key'] and current_sort.direction == 'asc' else 'asc')
                for item in selected if item['sortable']}
    return render('master_list.html', request, company_id=company_id, noun=noun, meta=meta, verbs=verbs,
        result=result, options=metadata, filter_options=filters, criteria=criteria, raw=raw, headings=headings, metadata_active_only=active_only,
        current_sort=current_sort,
        next_url=url(cursor=result['next_cursor']) if result['next_cursor'] else None,
        inactive_toggle=url(include_inactive=None if raw['include_inactive'] else '1'),
        browser_data={'columns': selected, 'options': metadata, 'filters': filters, 'criteria': criteria,
            'defaults': metadata['default_columns'], 'endpoint': f'/c/{company_id}/_browse/{noun}'})


def details(request, noun, company_id, record, run):
    """Readable public groups; large owned tables have their own bounded continuation."""
    from bookflow.company.query_projection import COLLECTIONS
    from bookflow.company.query_catalog import title
    technical = {'id', 'version', 'seed_key', 'created_by', 'created_via', 'updated_by', 'updated_via'}
    groups = {'Overview': [], 'Contact and address': [], 'Commercial details': [], 'Custom fields': []}
    collections = []
    for key, value in record.items():
        if key in technical or key.endswith('_source_id') or value is None:
            continue
        if (noun, key) in COLLECTIONS:
            raw = {'record': record['id'], 'column': key}
            if request.query_params.get('collection') == key and request.query_params.get('child_cursor'):
                raw['cursor'] = request.query_params['child_cursor']
            page = run(noun + ' query children', raw, company_id)
            fields = list(dict.fromkeys(k for row in page['items'] for k in row if k not in ('id', 'position')
                and not (k.endswith('_id') and (k.removesuffix('_id') in row or k.removesuffix('_id') + '_name' in row))))
            pairs = [('collection', key), ('child_cursor', page['next_cursor'])]
            collections.append({'key': key, 'label': title(key), 'page': page, 'fields': fields,
                'next_url': request.url.path + '?' + urlencode(pairs) + '#' + key if page['next_cursor'] else None})
            continue
        group = 'Custom fields' if key == 'custom_fields' else 'Contact and address' if any(word in key for word in ('contact', 'address', 'phone', 'email', 'fax')) else 'Commercial details' if any(word in key for word in ('account', 'tax', 'price', 'cost', 'balance', 'credit', 'term', 'unit')) else 'Overview'
        # Companion readable names already represent these IDs. Keep IDs in technical details.
        if key.endswith('_id') and key.removesuffix('_id') in record:
            continue
        groups[group].append((title(key), value))
    return {'groups': [{'title': key, 'fields': fields} for key, fields in groups.items() if fields], 'collections': collections}
