"""R189 increment 2: per-line commission recipients on sales receipts and POS sales (design/commissions.md §11).

The same capture as invoices (one attribution path): a typed line, a group's members, a tapped or scanned POS line, a
held and resumed ticket and the native receipt command each keep a distinct recipient per line; a clerk sees names
only; a held ticket whose recipient went inactive is re-asked and refused; a retried sale posts once.
"""
from types import SimpleNamespace
import anyio
import pytest
import bookflow
from bookflow.hub import pos_presets
from tests.test_pos_sale import Company

COMMANDS = ('pos commission-options',)
SURFACES = ('python', 'cli', 'http', 'mcp')


def _on(co):
    info = co.run('company show')
    co.run('company update', {'pos_enabled': True, 'commission_attribution_enabled': True,
                              'expected_version': info['info_version']})


@pytest.fixture
def co(root):
    co = Company(root)
    _on(co)
    income = co.run('account create', {'name': 'Till commission sales', 'type': 'income'})['id']
    code = next(r['id'] for r in co.run('sales-tax-code list')['items'] if not r['taxable'])
    co.item = co.run('item create', {'name': 'Till fitting', 'type': 'service', 'sales_enabled': True,
        'income_account_id': income, 'price': '10.00', 'description': 'Till fitting', 'sales_tax_code_id': code,
        'commission_enabled': True})['id']
    co.plain = co.run('item create', {'name': 'Till bag', 'type': 'service', 'sales_enabled': True,
        'income_account_id': income, 'price': '1.00', 'description': 'Till bag', 'sales_tax_code_id': code})['id']
    co.polish = co.run('item create', {'name': 'Till polish', 'type': 'service', 'sales_enabled': True,
        'income_account_id': income, 'price': '7.00', 'description': 'Till polish', 'sales_tax_code_id': code,
        'commission_enabled': True})['id']
    co.group = co.run('item create', {'name': 'Till bundle', 'type': 'group', 'description': 'Till bundle',
        'print_members': True, 'members': [{'component_item_id': co.item, 'quantity': '1'},
                                           {'component_item_id': co.plain, 'quantity': '1'},
                                           {'component_item_id': co.polish, 'quantity': '2'}]})['id']
    co.people = []
    for name, initials in (('Ada', 'ADA'), ('Bo', 'BO')):
        other = co.run('other-name create', {'name': name})['id']
        co.people.append(co.run('sales-rep create', {'name': name, 'initials': initials, 'name_type': 'other_name',
                                                     'name_id': other})['id'])
    co.run('customer create', {'name': 'Till commission customer'})
    co.bank = co.run('account create', {'name': 'Till commission bank', 'type': 'bank'})['id']
    return co


def _keys(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def pick(co, who):
    return {'recipients': [{'recipient': co.people[who]}]}


def rows(co, **query):
    return [(r['document_type'], r['recipient_name'], r['net_minor_units'])
            for r in co.run('report commission-attribution', query)['rows']]


def cart(co, lines, **kw):
    return dict(lines=lines, **kw)


def sell(co, ticket, lines, run=None, **kw):
    run = run or co.run
    total = run('pos sale preview', {'lines': lines})['total']['amount']
    return run('pos sale complete', dict(ticket=ticket, lines=lines, expected_total=total,
                                         tender={'payment_method': 'Cash'}, **kw))


def receipt(co, lines, **kw):
    return co.run('sales-receipt post', dict(customer='Till commission customer', date='2026-11-02', deposit_to=co.bank, payment_method='Cash',
                                             lines=lines, **kw))


def test_native_receipt_keeps_identical_items_apart_like_an_invoice(co):
    before = co.run('sales-receipt query', {})['items']
    for lines, code in (([{'item': co.item}], 'E_COMMISSION_REQUIRED'),
                        ([{'item': co.item, 'commission': {'recipients': [{'recipient': 'Nobody'}]}}], 'E_RECORD_NOT_FOUND'),
                        ([{'item': co.group}], 'E_COMMISSION_REQUIRED'),
                        ([{'item': co.group, 'member_commissions': [{'member': 1, 'commission': pick(co, 0)}]}],
                         'E_COMMISSION_REQUIRED'),
                        ([{'item': co.group, 'member_commissions': [{'member': 2, 'commission': pick(co, 0)}]}],
                         'E_VALIDATION'),
                        ([{'item': co.plain, 'member_commissions': [{'member': 1, 'commission': pick(co, 0)}]}],
                         'E_VALIDATION')):
        assert co.fails('sales-receipt post', dict(customer='Till commission customer', date='2026-11-02',
                                                    deposit_to=co.bank, payment_method='Cash', lines=lines)).code == code, lines
    missing = co.fails('sales-receipt post', dict(customer='Till commission customer', date='2026-11-02', deposit_to=co.bank, payment_method='Cash',
                                                   lines=[{'item': co.plain}, {'item': co.item}]))
    assert missing.details['line_keys'] == ['line_2'] and missing.details['pos_lookup_command'] == 'pos commission-options'
    # Two identical items, two people, plus a group whose two enabled members are asked separately.
    sale = receipt(co, [{'item': co.item, 'commission': pick(co, 0)}, {'item': co.item, 'commission': pick(co, 1)},
                        {'item': co.group, 'member_commissions': [{'member': 1, 'commission': pick(co, 1)},
                                                                  {'member': 3, 'commission': pick(co, 0)}]}])
    assert sorted(rows(co)) == [('sales_receipt', 'Ada', 1000), ('sales_receipt', 'Ada', 1400),
                                ('sales_receipt', 'Bo', 1000), ('sales_receipt', 'Bo', 1000)]
    shown = co.run('sales-receipt show', {'sales_receipt': sale['id']})
    labels = [line['item_snapshot'].get('commission', {}).get('recipients', [{}])[0].get('recipient_label')
              for line in shown['revision']['lines']]
    assert [x for x in labels if x] == ['Ada', 'Bo', 'Bo', 'Ada']
    # The same inactive refusal as an invoice, all or nothing.
    co.run('sales-rep deactivate', {'sales_rep': co.people[1], 'expected_version': 1})
    assert co.fails('sales-receipt post', dict(customer='Till commission customer', date='2026-11-02', deposit_to=co.bank, payment_method='Cash',
                    lines=[{'item': co.item, 'commission': pick(co, 0)},
                           {'item': co.item, 'commission': pick(co, 1)}])).code == 'E_INACTIVE_REFERENCE'
    assert len(co.run('sales-receipt query', {})['items']) == len(before) + 1
    # Off: a plain receipt posts exactly as before (no commission fact); a selection is refused.
    info = co.run('company show')
    co.run('company update', {'commission_attribution_enabled': False, 'expected_version': info['info_version']})
    plain = receipt(co, [{'item': co.item}])
    assert 'commission' not in co.run('sales-receipt show', {'sales_receipt': plain['id']})['revision']['lines'][0]['item_snapshot']
    assert co.fails('sales-receipt post', dict(customer='Till commission customer', date='2026-11-02', deposit_to=co.bank, payment_method='Cash',
                    lines=[{'item': co.item, 'commission': pick(co, 0)}])).code == 'E_FEATURE_DISABLED'


def test_pos_tap_scan_and_group_lines_keep_their_own_recipients_and_a_retry_posts_once(co):
    lines = [{'item': co.item, 'commission': pick(co, 0)}, {'item': co.item, 'commission': pick(co, 1)},
             {'item': co.item, 'quantity': '2', 'commission': pick(co, 0)}, {'item': co.plain},
             {'item': co.group, 'member_commissions': [{'member': 1, 'commission': pick(co, 1)},
                                                       {'member': 3, 'commission': pick(co, 1)}]}]
    assert co.fails('pos sale preview', {'lines': [{'item': co.item}]}).code == 'E_COMMISSION_REQUIRED'
    preview = co.run('pos sale preview', {'lines': lines})
    assert [(line['item_name'], (line.get('commission') or {}).get('recipients', [{}])[0].get('recipient_label'))
            for line in preview['lines']] == [('Till fitting', 'Ada'), ('Till fitting', 'Bo'), ('Till fitting', 'Ada'),
            ('Till bag', None), ('Till fitting', 'Bo'), ('Till bag', None), ('Till polish', 'Bo')]
    assert all(set(line['commission']['recipients'][0]) == {'recipient_id', 'recipient_label'}
               for line in preview['lines'] if line.get('commission'))
    sale = sell(co, 'till-attrib-1', lines)
    again = sell(co, 'till-attrib-1', lines)
    assert again['replayed'] and again['id'] == sale['id']
    changed = [dict(lines[0], commission=pick(co, 1)), *lines[1:]]
    assert co.fails('pos sale complete', dict(ticket='till-attrib-1', lines=changed, expected_total=sale['total']['amount'],
                                              tender={'payment_method': 'Cash'})).code == 'E_IDEMPOTENCY_MISMATCH'
    assert sorted(rows(co)) == sorted([('sales_receipt', 'Ada', 1000), ('sales_receipt', 'Bo', 1000),
        ('sales_receipt', 'Ada', 2000), ('sales_receipt', 'Bo', 1000), ('sales_receipt', 'Bo', 1400)])
    # Split tender posts an invoice through the same capture.
    total = co.run('pos sale preview', {'lines': lines[:2]})['total']['amount']
    co.run('pos sale complete', dict(ticket='till-attrib-split', lines=lines[:2], expected_total=total,
        tenders=[{'payment_method': 'Cash', 'amount': '5.00'}, {'payment_method': 'Visa', 'amount': '15.00'}]))
    assert ('invoice', 'Ada', 1000) in rows(co) and ('invoice', 'Bo', 1000) in rows(co)


def test_a_held_ticket_with_an_inactive_recipient_is_re_asked_and_refused(co):
    lines = [{'item': co.item, 'commission': pick(co, 0)}, {'item': co.item, 'commission': pick(co, 1)},
             {'item': co.group, 'member_commissions': [{'member': 1, 'commission': pick(co, 0)},
                                                       {'member': 3, 'commission': pick(co, 1)}]}]
    held = co.run('pos ticket hold', {'ticket': 'held-attrib', 'lines': lines})
    assert held['commission_review'] == [] and held['lines'] == co.run('pos ticket list')['items'][0]['lines']
    co.run('sales-rep deactivate', {'sales_rep': co.people[0], 'expected_version': 1})
    resumed = co.run('pos ticket resume', {'ticket': held['id'], 'expected_version': held['version']})
    review = [(r['line'], r['member'], r['problem'], r['recipient_id']) for r in resumed['commission_review']]
    assert review == [(1, None, 'recipient_inactive', co.people[0]), (3, 1, 'recipient_inactive', co.people[0])]
    refused = co.fails('pos sale complete', dict(ticket=held['id'], ticket_version=resumed['version'],
        lines=resumed['lines'], expected_total='45.00', tender={'payment_method': 'Cash'}))
    assert refused.code == 'E_INACTIVE_REFERENCE'
    # Re-asked: the clerk names someone active and the sale completes once with the corrected lines.
    fixed = [dict(lines[0], commission=pick(co, 1)), lines[1],
             dict(lines[2], member_commissions=[{'member': 1, 'commission': pick(co, 1)},
                                                {'member': 3, 'commission': pick(co, 1)}])]
    sale = sell(co, held['id'], fixed, ticket_version=resumed['version'])
    assert {r[1] for r in rows(co)} == {'Bo'} and sale['id']
    # A held ticket carrying a selection keeps the layer on until it is settled.
    co.run('pos ticket hold', {'ticket': 'held-attrib-2', 'lines': [lines[1]], 'label': 'Bo at lunch'})
    info = co.run('company show')
    blocked = co.fails('company update', {'commission_attribution_enabled': False, 'expected_version': info['info_version']})
    assert blocked.code == 'E_WORK_DEPENDENCY' and 'Bo at lunch' in blocked.message


def test_a_clerk_attributes_with_the_preset_and_never_sees_rates_or_earnings(co, root):
    from tests.test_scheduling import member
    books = SimpleNamespace(root=root, client=co.c, company=co.cid)
    run = {}
    for name in ('pos-clerk', 'pos-clerk-commission'):
        preset = pos_presets.preset(name)
        _, run[name] = member(books, name.replace('-', '_'), role='readonly', denies=preset['denies'])
        co.c.run('membership grant', {'user': name.replace('-', '_'), 'company': co.cid, 'role': 'readonly',
            'grants': preset['grants'], 'denies': preset['denies'], 'expected_version': 1})

    def as_clerk(name):
        return lambda command, raw=None, **kw: run[name](command, raw, **kw)
    plain, attrib = as_clerk('pos-clerk'), as_clerk('pos-clerk-commission')
    lines = [{'item': co.item, 'commission': pick(co, 0)}, {'item': co.item, 'commission': pick(co, 1)}]
    for command, raw in (('pos commission-options', {'item': co.item}), ('pos sale preview', {'lines': lines})):
        with pytest.raises(bookflow.BookflowError) as err:
            plain(command, raw)
        assert err.value.code == 'E_PERMISSION', command
    with pytest.raises(bookflow.BookflowError) as err:
        plain('pos sale preview', {'lines': [{'item': co.item}]})
    # An enabled item needs someone named, which this clerk may not do: refused naming the capability.
    assert err.value.code == 'E_PERMISSION' and 'commission.attribute' in repr(err.value.details)
    assert plain('pos sale preview', {'lines': [{'item': co.plain}]})['total']['amount'] == '1.00'
    options = attrib('pos commission-options', {'item': co.item, 'search': 'Ada'})
    assert options['commission_enabled'] and [r['name'] for r in options['items']] == ['Ada']
    assert all(set(r) == {'id', 'name'} for r in options['items'])
    for command in ('commission recipient-options', 'report commission-attribution'):
        with pytest.raises(bookflow.BookflowError) as err:
            attrib(command, {})
        assert err.value.code == 'E_PERMISSION', command
    sale = sell(co, 'clerk-attrib', lines, run=attrib)
    preview = attrib('pos sale preview', {'lines': lines})
    assert [line['commission'] for line in preview['lines']] == [
        {'recipients': [{'recipient_id': co.people[0], 'recipient_label': 'Ada'}]},
        {'recipients': [{'recipient_id': co.people[1], 'recipient_label': 'Bo'}]}]
    printed = attrib('pos receipt show', {'sale': sale['id']})
    # Names only where a seller looks; the customer's printed receipt carries no commission at all.
    assert set(_keys(sale) | _keys(preview) | _keys(printed)).isdisjoint(
        {'share', 'rate', 'commission_rate', 'earning', 'earnings', 'payout', 'net_minor_units', 'plan'})
    assert 'Ada' not in repr(printed) and 'commission' not in _keys(printed)
    assert sorted(rows(co)) == [('sales_receipt', 'Ada', 1000), ('sales_receipt', 'Bo', 1000)]


def test_pos_commission_options_contract_python_cli_http_mcp(root, tmp_path):
    from tests.mcp_matrix_support import Matrix

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in SURFACES:
                async def call(command, raw, **kw):
                    return await matrix.call(surface, command, raw, **kw)
                info = await call('company show', {})
                await call('company update', {'pos_enabled': True, 'commission_attribution_enabled': True,
                                              'expected_version': info['info_version']})
                options = await call('pos commission-options', {'limit': 5})
                assert options['items'] and all(set(r) == {'id', 'name'} for r in options['items'])
                info = await call('company show', {})
                await call('company update', {'commission_attribution_enabled': False,
                                              'expected_version': info['info_version']})
                # Off, the till's lookup answers as the invoice one does: nothing to ask.
                assert await call('pos commission-options', {}) == {'commission_enabled': False, 'items': [],
                                                                    'suggested_recipient_id': None}
                info = await call('company show', {})
                await call('company update', {'pos_enabled': False, 'expected_version': info['info_version']})
        finally:
            await matrix.close()
    anyio.run(witness)
