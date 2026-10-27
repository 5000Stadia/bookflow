"""R104 increment 3: barcodes in `item` fields, duplicate refusal, `barcode lookup` and `barcode generate`.

The demo's Copper Coupling carries 0012345678905. Every `item` field resolves id, then name, then
barcode, so `invoice post` with that code as `item` bills a coupling. A code equal to another item's
name names that item (name comes first); a code two active items share (kept from before codes were
checked) is ambiguous and both are named; a new duplicate is refused naming the holder.
"""
import pytest

from bookflow import BookflowError
from bookflow.company import barcodes
from tests.demo_oracle import DEMO_AS_OF

CO = 'Demo Plumbing Co'
COUPLING = '0012345678905'


def _run(client, name, body=None, **context):
    return client.run(name, body or {}, company=CO, **context)


def _part(client, name, **extra):
    income = next(row['id'] for row in client.account.query(company=CO, limit=200)['items'] if row['type'] == 'income')
    return client.item.create(company=CO, name=name, type='non_inventory_part', price='3.00', description=name,
                              income_account_id=income, **extra)


def _raw_barcode(root, item_id, code):
    """A duplicate from before codes were checked: written past the application, as old books hold."""
    import sqlite3
    for path in root.rglob('*.db'):
        with sqlite3.connect(path) as conn:
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='items'").fetchone():
                if conn.execute('UPDATE items SET barcode = ? WHERE id = ?', (code, item_id)).rowcount:
                    return
    raise AssertionError('item not found')


@pytest.mark.parametrize('code', ['4006381333931', '0036000291452', '5901234123457', '9780306406157', '0012345678905'])
def test_check_digits_of_known_ean13s(code):
    assert barcodes.ean13_check_digit(code[:12]) == code[12]
    assert barcodes.is_ean13(code)
    assert not barcodes.is_ean13(code[:12] + str((int(code[12]) + 1) % 10))


def test_generated_series_is_prefix_2_with_check_digit():
    codes = [barcodes.ean13(n) for n in range(1, 200)]
    assert codes[0] == '2000000000015'
    assert all(code.startswith('2') and barcodes.is_ean13(code) for code in codes)
    assert len(set(codes)) == len(codes)


def test_invoice_post_with_a_code_as_item_resolves_it(client):
    sale = _run(client, 'invoice post', {'customer': 'Riverside Apartments', 'date': DEMO_AS_OF,
                                         'lines': [{'item': COUPLING, 'quantity': '2'}]}, reason='Scanned sale')
    coupling = _run(client, 'item show', {'item': 'Copper Coupling'})['id']
    assert coupling in repr(sale) and 'Copper Coupling' in repr(sale), sorted(sale)


def test_resolution_order_id_then_name_then_barcode(client, root):
    named = _part(client, '7777777777777')            # an item whose *name* is a code
    coded = _part(client, 'Coded Elbow', barcode='7777777777777')
    shown = _run(client, 'item show', {'item': '7777777777777'})
    assert shown['id'] == named['id']                 # name before barcode
    assert _run(client, 'item show', {'item': coded['id']})['id'] == coded['id']
    assert _run(client, 'item show', {'item': COUPLING})['full_name'] == 'Copper Coupling'
    looked = _run(client, 'barcode lookup', {'code': '7777777777777'})
    assert looked['found'] and looked['match']['name'] == 'Coded Elbow'   # lookup reads codes only


def test_a_new_duplicate_is_refused_and_an_old_one_is_ambiguous(client, root):
    with pytest.raises(BookflowError) as refused:
        _part(client, 'Second Coupling', barcode=COUPLING)
    assert refused.value.code == 'E_VALIDATION' and 'Copper Coupling' in str(refused.value.details)
    other = _part(client, 'Other Coupling')
    with pytest.raises(BookflowError) as changed:
        _run(client, 'item update', {'item': other['id'], 'barcode': COUPLING}, reason='Relabel')
    assert changed.value.code == 'E_VALIDATION' and changed.value.details['item'] == 'Copper Coupling'
    _raw_barcode(root, other['id'], COUPLING)        # an existing duplicate stays
    looked = _run(client, 'barcode lookup', {'code': COUPLING})
    assert looked['ambiguous'] and looked['kind'] == 'ambiguous' and len(looked['matches']) == 2
    with pytest.raises(BookflowError) as ambiguous:
        _run(client, 'item show', {'item': COUPLING})
    assert ambiguous.value.code == 'E_VALIDATION'
    assert {c['name'] for c in ambiguous.value.details['candidates']} == {'Copper Coupling', 'Other Coupling'}
    # Saving the duplicate holder unchanged is no new duplicate, and its other fields still change.
    _run(client, 'item update', {'item': other['id'], 'description': 'Still coded'}, reason='Edit')
    # Deactivating one leaves the code naming the other.
    _run(client, 'item deactivate', {'item': other['id']}, reason='Retire')
    assert _run(client, 'item show', {'item': COUPLING})['full_name'] == 'Copper Coupling'


def test_unknown_code_says_so_and_how_to_assign(client):
    looked = _run(client, 'barcode lookup', {'code': '2999999999999'})
    assert not looked['found'] and looked['kind'] is None and 'item update' in looked['message']
    with pytest.raises(BookflowError) as missing:
        _run(client, 'item show', {'item': '2999999999999'})
    assert missing.value.code == 'E_RECORD_NOT_FOUND'


def test_lookup_reports_on_hand_by_site_and_line_fields(client):
    looked = _run(client, 'barcode lookup', {'code': '0036000291452', 'as_of': DEMO_AS_OF})
    match = looked['match']
    assert match['name'] == 'Service Call Kit' and match['line_fields'] == {'item': 'Service Call Kit'}
    assert match['on_hand'] and all(row['site'] for row in match['on_hand'])


def test_generate_numbers_items_without_a_code_and_never_changes_one(client):
    # The landed-cost demo (R104 increment 6) added Copper Pipe with no code; give it its maker's code so
    # the demo is fully labelled again and the generated numbers below start where they did.
    pipe = _run(client, 'item show', {'item': 'Copper Pipe'})
    _run(client, 'item update', {'item': pipe['id'], 'expected_version': pipe['version'], 'barcode': '4006381333948'},
         reason='Maker code')
    assert _run(client, 'barcode generate', {}, dry_run=True)['assigned'] == []   # every demo part has a code
    _part(client, 'Brass Elbow')
    _part(client, 'Brass Tee')
    preview = _run(client, 'barcode generate', {}, dry_run=True)
    assert preview['assigned'] and all(barcodes.is_ean13(a['barcode']) for a in preview['assigned'])
    assert [a['item'] for a in preview['assigned']] == ['Brass Elbow', 'Brass Tee']
    assert preview['assigned'][0]['barcode'] == '2000000000022'   # 2000000000015 is the Two-Valve Kit's
    done = _run(client, 'barcode generate', {}, reason='Label everything')
    assert [a['barcode'] for a in done['assigned']] == [a['barcode'] for a in preview['assigned']]
    codes = [a['barcode'] for a in done['assigned']]
    assert len(set(codes)) == len(codes) and all(c.startswith('2') for c in codes)
    for row in done['assigned']:
        assert _run(client, 'item show', {'item': row['barcode']})['id'] == row['item_id']
    again = _run(client, 'barcode generate', {'items': ['Copper Coupling', done['assigned'][0]['item']]}, reason='Again')
    assert again['assigned'] == [] and {s['barcode'] for s in again['skipped']} == {COUPLING, codes[0]}
    assert _run(client, 'item show', {'item': 'Copper Coupling'})['barcode'] == COUPLING
    later = _part(client, 'Late Fitting')
    nxt = _run(client, 'barcode generate', {'items': [later['id']]}, reason='One more')
    assert nxt['assigned'][0]['barcode'] not in codes and barcodes.is_ean13(nxt['assigned'][0]['barcode'])
    assert nxt['next_number'] > done['next_number'] - 1


def _event(client, command, record_id):
    return client.audit.list(company=CO, command=command, record_id=record_id)['items'][0]['id']


def test_coming_back_active_may_not_bring_a_held_code_back(client):
    """Activate, unmerge, undo of a deactivation, and undo of a cascade: each refused naming the holder."""
    # item activate
    old = _part(client, 'Old Elbow', barcode='R-1')
    _run(client, 'item deactivate', {'item': old['id']}, reason='Retire')
    _part(client, 'New Elbow', barcode='R-1')                     # free while Old Elbow is inactive
    with pytest.raises(BookflowError) as activated:
        _run(client, 'item activate', {'item': old['id']}, reason='Back')
    assert activated.value.code == 'E_VALIDATION'
    problem = activated.value.details['fields'][0]['problem']
    assert 'New Elbow' in problem and 'clear the code' in problem, problem
    # undo of the deactivation
    with pytest.raises(BookflowError) as undone:
        _run(client, 'undo', {'event_id': _event(client, 'item deactivate', old['id'])}, reason='Oops')
    assert undone.value.code in ('E_UNDO_CONFLICT', 'E_VALIDATION') and 'New Elbow' in str(undone.value.details)
    # undo of a cascade: a sub-item deactivated with its parent comes back only if its code is free
    parent = _part(client, 'Fittings Group')
    child = client.item.create(company=CO, name='Kid Tee', parent_id=parent['id'], type='non_inventory_part',
                               price='3.00', description='Kid Tee', barcode='R-2',
                               income_account_id=next(r['id'] for r in client.account.query(company=CO, limit=200)['items']
                                                      if r['type'] == 'income'))
    _run(client, 'item deactivate', {'item': parent['id'], 'cascade': True}, reason='Retire group')
    _part(client, 'Adult Tee', barcode='R-2')
    with pytest.raises(BookflowError) as cascaded:
        _run(client, 'undo', {'event_id': _event(client, 'item deactivate', parent['id'])}, reason='Oops')
    assert cascaded.value.code in ('E_UNDO_CONFLICT', 'E_VALIDATION') and 'Adult Tee' in str(cascaded.value.details)
    assert _run(client, 'item show', {'item': child['id']})['active'] is False
    # item unmerge
    merged = _part(client, 'Merged Union', barcode='R-3')
    _part(client, 'Kept Union')
    _run(client, 'item merge', {'merged': 'Merged Union', 'into': 'Kept Union'}, reason='Same thing')
    _part(client, 'Fresh Union', barcode='R-3')
    with pytest.raises(BookflowError) as unmerged:
        _run(client, 'item unmerge', {'merged': merged['id']}, reason='Not the same')
    assert unmerged.value.code == 'E_MERGE_REFUSED' and 'Fresh Union' in unmerged.value.message
    # With the holder's code cleared, each comes back.
    _run(client, 'item update', {'item': 'New Elbow', 'barcode': None}, reason='Relabel')
    assert _run(client, 'item activate', {'item': old['id']}, reason='Back')['active'] is True
    assert _run(client, 'item show', {'item': 'R-1'})['id'] == old['id']


def test_the_scan_route_reads_codes_first_and_says_when_a_name_collides(client):
    from bookflow.adapters.workbench import barcode_pages

    def call(name, raw):
        return _run(client, name, raw)
    named = _part(client, '7777777777777')
    coded = _part(client, 'Coded Elbow', barcode='7777777777777')
    seen = barcode_pages.answer(call, 'C', '7777777777777')
    assert seen['item_id'] == coded['id']                         # a scanner sends codes: code first
    assert seen['message'] == 'Matched code 7777777777777 on Coded Elbow; an item named 7777777777777 also exists.'
    assert barcode_pages.answer(call, 'C', named['id'])['item_id'] == named['id']      # then id
    assert barcode_pages.answer(call, 'C', 'Coded Elbow')['item_id'] == coded['id']    # then name
    unknown = barcode_pages.answer(call, 'C', '2999999999999')
    assert not unknown['found'] and unknown['assign_url'] == '/c/C/item'
