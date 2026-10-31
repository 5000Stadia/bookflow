"""R186: whole merge batches restore aliases and references, or refuse without writes."""
import os
import sqlite3
from pathlib import Path

import pytest

import bookflow

from bookflow import BookflowError
from bookflow.company import batches
from tests.conftest import as_user, make_actor, copy_seeded_root, _checkpoint_databases
from tests.test_list_merge import COMPANY, _item, _run, books as _base_books
from tests.test_list_merge_twins import ORG

KINDS = ('customer', 'vendor', 'account', 'item')


@pytest.fixture(scope='module')
def _merge_template(tmp_path_factory):
    root = tmp_path_factory.mktemp('merge_template') / 'root'
    previous = os.environ.get('BOOKFLOW_DATA_ROOT')
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    try:
        client = bookflow.connect(data_root=str(root))
        client.init()
        client.run('organization new', {'name': ORG})
        _base_books.__wrapped__(client)
        del client
    finally:
        if previous is None:
            os.environ.pop('BOOKFLOW_DATA_ROOT', None)
        else:
            os.environ['BOOKFLOW_DATA_ROOT'] = previous
    _checkpoint_databases(root)
    return root


@pytest.fixture
def books(_merge_template, tmp_path, monkeypatch):
    root = copy_seeded_root(_merge_template, tmp_path / 'root')
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    return bookflow.connect(data_root=str(root))


def _db():
    return Path(os.environ['BOOKFLOW_DATA_ROOT']) / 'organizations' / ORG / COMPANY / 'company.db'


def _snapshot():
    with sqlite3.connect(_db()) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: db.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}


def _business():
    tables = ('customers', 'vendors', 'accounts', 'items', 'item_members', 'item_vendor_profiles',
              'vendor_expense_accounts', 'price_levels', 'price_level_items', 'posting_lines',
              'posting_batches', 'transactions', 'transaction_revisions', 'inventory_movements')
    with sqlite3.connect(_db()) as db:
        result = {}
        for table in tables:
            columns = [r[1] for r in db.execute(f'PRAGMA table_info({table})')
                       if r[1] not in ('version', 'updated_at', 'updated_by', 'updated_via')]
            if columns:
                result[table] = db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY rowid').fetchall()
        return result


def _setup(books, kind):
    run = lambda name, body, **kw: _run(books, name, body, **kw)
    if kind in ('customer', 'vendor'):
        merged, survivor = [run(f'{kind} create', {'name': name})['id']
                            for name in ('Duplicate', 'Survivor')]
    elif kind == 'account':
        merged = run('account show', {'account': 'Truck Fuel'})['id']
        survivor = run('account show', {'account': 'Truck Fuel Costs'})['id']
    else:
        merged, survivor = [_item(books, name, kind='inventory_part')
                            for name in ('Duplicate', 'Survivor')]
    if kind == 'customer':
        item = _item(books, 'Merge Service')
        for party, price in ((merged, '120.00'), (survivor, '30.00')):
            run('invoice post', {'customer': party, 'date': '2026-03-01',
                                 'lines': [{'item': item, 'quantity': '1', 'unit_price': price}]})
    elif kind == 'vendor':
        for party, price in ((merged, '50.00'), (survivor, '30.00')):
            run('bill post', {'vendor': party, 'date': '2026-03-01',
                              'expenses': [{'account': 'Truck Fuel', 'amount': price}]})
        run('vendor-credit post', {'vendor': merged, 'date': '2026-03-02',
                                   'expenses': [{'account': 'Truck Fuel', 'amount': '50.00'}]})
    elif kind == 'account':
        run('journal post', {'date': '2026-03-01', 'lines': [
            {'account': survivor, 'side': 'debit', 'amount': '7.00'},
            {'account': 'Bank One', 'side': 'credit', 'amount': '7.00'}]})
        _item(books, 'Fuel Delivery', purchase_enabled=True, purchase_description='Delivery',
              cost='4.00', expense_account_id=merged)
        run('vendor create', {'name': 'Fuel Vendor', 'expense_accounts': [{'account_id': merged}]})
    else:
        run('item create', {'name': 'Duplicate Kit', 'type': 'group', 'description': 'Kit',
                            'print_members': True, 'members': [
                                {'component_item_id': merged, 'quantity': '2'},
                                {'component_item_id': survivor, 'quantity': '3'}]})
        stocked = _item(books, 'Stocked Part', kind='inventory_part')
        vendor = run('vendor create', {'name': 'Stock Vendor'})['id']
        run('bill post', {'vendor': vendor, 'date': '2026-03-01',
                          'items': [{'item': stocked, 'quantity': '4', 'unit_cost': '2.50'}]})
    return merged, survivor


def _merge(books, kind, merged, survivor):
    return _run(books, f'{kind} merge', {'merged': merged, 'into': survivor})


def _reverse(books, batch, **kw):
    return _run(books, 'batch reverse', {'batch': batch}, **kw)


def _refused_unchanged(books, batch, *names):
    before = _snapshot()
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            _reverse(books, batch, dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        for name in names:
            assert name in str(caught.value.details), caught.value.details
        assert _snapshot() == before
    return caught.value


def _balances(books, kind):
    if kind == 'customer':
        rows = _run(books, 'report ar-aging', {'as_of': '2026-12-31'})['rows']
        return {r['display_customer_label']: r['total']['minor_units'] for r in rows}
    if kind == 'vendor':
        rows = _run(books, 'report ap-aging', {'as_of': '2026-12-31'})['rows']
        return {r['current_vendor_name']: r['total']['minor_units'] for r in rows}
    return {name: _run(books, 'account show', {'account': name})['balance']['minor_units']
            for name in ('Truck Fuel', 'Truck Fuel Costs')}


@pytest.mark.parametrize('kind', KINDS)
def test_merge_reversal_restores_exact_records_references_and_books(books, kind):
    merged, survivor = _setup(books, kind)
    before = _business()
    if kind != 'item':
        balances = _balances(books, kind)
        expected = {'customer': {'Duplicate': 12000, 'Survivor': 3000},
                    'vendor': {'Survivor': 3000},
                    'account': {'Truck Fuel': 1000, 'Truck Fuel Costs': 700}}[kind]
        assert balances == expected
    else:
        stock = _run(books, 'report stock-status', {'as_of': '2026-12-31'})['rows']
        assert [(r['quantity_on_hand'], r['asset_value']['minor_units']) for r in stock
                if r['quantity_on_hand'] != '0'] == [('4', 1000)]
        assert _run(books, 'report inventory-valuation', {'as_of': '2026-12-31'})['totals']['asset_value']['minor_units'] == 1000
    made = _merge(books, kind, merged, survivor)
    batch = made['batch_id']
    assert batch and batch in made['next'] and 'batch reverse' in made['next']
    stable = _snapshot()
    preview = _reverse(books, batch, dry_run=True)
    assert _snapshot() == stable
    assert len(preview['steps']) == 1
    step = preview['steps'][0]
    assert step['record_type'] == ('party_merge' if kind in ('customer', 'vendor') else 'list_merge')
    assert step['record_id'] == made['merge_id']
    assert step['operation'] == f'{kind} unmerge'
    assert step['alias'] == {'merged_id': merged, 'survivor_id': survivor, 'remove': True}
    assert step['steps'] == [{'command': f'{kind} unmerge', 'payload': {'merged': merged}}]
    expected_refs = [(kind + 's', merged, 'active', True)]
    with sqlite3.connect(_db()) as db:
        refs = db.execute('SELECT table_name, record_id, column_name, action, before_value FROM list_merge_references '
                          'WHERE merge_id=? ORDER BY position DESC', (made['merge_id'],)).fetchall()
    for table, record, field, action, value in refs:
        if action == 'end':
            field, value = 'active', 1
        elif field.endswith('_microunits') and value is not None:
            value = int(value)
        expected_refs.append((table, record, field, value))
    assert sorted((r['table'], r['record_id'], r['field'], r['value']) for r in step['references']) == sorted(expected_refs)
    assert _reverse(books, batch)['state'] == 'reversed'
    assert _business() == before
    if kind != 'item':
        assert _balances(books, kind) == balances
    else:
        assert _run(books, 'report stock-status', {'as_of': '2026-12-31'})['rows'] == stock
        assert _run(books, 'report inventory-valuation', {'as_of': '2026-12-31'})['totals']['asset_value']['minor_units'] == 1000


@pytest.mark.parametrize('kind', KINDS)
def test_later_merge_into_survivor_must_reverse_first(books, kind):
    merged, survivor = _setup(books, kind)
    first = _merge(books, kind, merged, survivor)['batch_id']
    if kind in ('customer', 'vendor'):
        third = _run(books, f'{kind} create', {'name': 'Third'})['id']
    elif kind == 'account':
        third = _run(books, 'account create', {'name': 'Third', 'type': 'expense'})['id']
    else:
        third = _item(books, 'Third', kind='inventory_part')
    second = _merge(books, kind, third, survivor)
    _refused_unchanged(books, first, second['merge_id'])
    assert _reverse(books, second['batch_id'])['state'] == 'reversed'
    assert _reverse(books, first)['state'] == 'reversed'


@pytest.mark.parametrize('kind', KINDS)
def test_reverser_without_unmerge_grant_is_refused(books, kind):
    merged, survivor = _setup(books, kind)
    batch = _merge(books, kind, merged, survivor)['batch_id']
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    company = books.company.show(company=COMPANY)['company_id']
    make_actor(root, 'batch-reader', company_role=(company, 'readonly'))
    reader = as_user(root, 'batch-reader')
    before = _snapshot()
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reader.run('batch reverse', {'batch': batch}, company=COMPANY, dry_run=dry,
                       reason='Undo the named merge batch')
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any(b['code'] == 'E_PERMISSION' for b in caught.value.details['blockers'])
        assert _snapshot() == before


@pytest.mark.parametrize('kind', KINDS)
def test_merge_effect_failure_rolls_back_alias_references_and_batch(books, monkeypatch, kind):
    merged, survivor = _setup(books, kind)
    before = _snapshot()
    original = batches.record_effect
    def fail(*args, **kw):
        original(*args, **kw)
        raise RuntimeError('effect failed')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect failed'):
        _merge(books, kind, merged, survivor)
    assert _snapshot() == before


@pytest.mark.parametrize('kind', ('account', 'item'))
@pytest.mark.parametrize('change', ('changed', 'deleted'))
def test_changed_or_deleted_reference_blocks_complete_unmerge(books, kind, change):
    merged, survivor = _setup(books, kind)
    made = _merge(books, kind, merged, survivor)
    with sqlite3.connect(_db()) as db:
        table = 'vendor_expense_accounts' if kind == 'account' else 'item_members'
        refs = db.execute('SELECT record_id, column_name FROM list_merge_references '
                          'WHERE merge_id=? AND table_name=?', (made['merge_id'], table)).fetchall()
        record, field = refs[0]
        if change == 'deleted':
            db.execute(f'DELETE FROM {table} WHERE id=?', (record,))
        else:
            owner = 'Fuel Delivery' if kind == 'account' else 'Duplicate Kit'
    if change == 'changed':
        shown = _run(books, 'item show', {'item': owner})
        body = {'expense_account_id': _run(books, 'account show', {'account': 'Gas Spend'})['id']} if kind == 'account' else {
            'members': [{'component_item_id': survivor, 'quantity': '9'}]}
        _run(books, 'item update', {'item': shown['id'], 'expected_version': shown['version'], **body})
        needle = shown['id']
    else:
        needle = record
    _refused_unchanged(books, made['batch_id'], needle)


def test_barcode_collision_and_changed_reference_are_both_named(books):
    merged, survivor = _setup(books, 'item')
    _run(books, 'item update', {'item': merged, 'barcode': 'MERGE-CODE'})
    made = _merge(books, 'item', merged, survivor)
    holder = _item(books, 'Barcode Holder', kind='inventory_part', barcode='MERGE-CODE')
    shown_batch = _run(books, 'batch show', {'batch': made['batch_id']})['batch']
    assert not shown_batch['reversible'] and holder in str(shown_batch['first_blocker'])
    queried = _run(books, 'batch query', {'kind': 'item-merge'})['items']
    assert len(queried) == 1 and not queried[0]['reversible']
    kit = _run(books, 'item show', {'item': 'Duplicate Kit'})
    _run(books, 'item update', {'item': kit['id'], 'expected_version': kit['version'],
                               'members': [{'component_item_id': survivor, 'quantity': '9'}]})
    error = _refused_unchanged(books, made['batch_id'], holder, kit['id'])
    assert sum(holder in str(blocker) for blocker in error.details['blockers']) == 1


@pytest.mark.parametrize('kind', KINDS)
def test_survivor_cannot_take_merged_name_and_prevent_restoration(books, kind):
    merged, survivor = _setup(books, kind)
    old = _run(books, f'{kind} show', {kind: merged})
    made = _merge(books, kind, merged, survivor)
    shown = _run(books, f'{kind} show', {kind: survivor})
    before = _snapshot()
    with pytest.raises(BookflowError) as caught:
        _run(books, f'{kind} update', {kind: survivor, 'expected_version': shown['version'], 'name': old['name']})
    assert caught.value.code == 'E_NAME_TAKEN'
    assert _snapshot() == before
    assert _reverse(books, made['batch_id'])['state'] == 'reversed'


@pytest.mark.parametrize('kind', KINDS)
def test_interrupt_after_durable_unmerge_resumes_without_repeating(books, monkeypatch, kind):
    merged, survivor = _setup(books, kind)
    before = _business()
    batch = _merge(books, kind, merged, survivor)['batch_id']
    original = batches.run_nested
    stopped = []
    def interrupt(s, ctx, name, body, **kw):
        out = original(s, ctx, name, body, **kw)
        if name == f'{kind} unmerge' and not kw.get('dry_run') and not stopped:
            stopped.append(True)
            raise KeyboardInterrupt('after durable inverse')
        return out
    monkeypatch.setattr(batches, 'run_nested', interrupt)
    with pytest.raises(KeyboardInterrupt):
        _reverse(books, batch)
    monkeypatch.setattr(batches, 'run_nested', original)
    assert _reverse(books, batch)['state'] == 'reversed'
    assert _business() == before


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('change', ('edit', 'delete'))
def test_original_document_changed_since_merge_blocks_reversal(books, kind, change):
    merged, survivor = _setup(books, kind)
    if kind == 'item':
        # Stocked items with history cannot merge; service history can and must remain intact.
        merged, survivor = _item(books, 'Old Service'), _item(books, 'New Service')
        customer = _run(books, 'customer create', {'name': 'Buyer'})['id']
        document = _run(books, 'invoice post', {'customer': customer, 'date': '2026-03-01',
                                               'lines': [{'item': merged, 'quantity': '1'}]})['id']
        command, selector = 'invoice update', 'invoice'
    else:
        family = {'customer': 'invoice', 'vendor': 'bill', 'account': 'journal'}[kind]
        with sqlite3.connect(_db()) as db:
            document = db.execute('SELECT id FROM transactions WHERE type=? ORDER BY rowid LIMIT 1', ('journal_entry' if family == 'journal' else family,)).fetchone()[0]
        command, selector = family + ' update', family
    made = _merge(books, kind, merged, survivor)
    if change == 'delete':
        _run(books, command.replace(' update', ' delete'), {selector: document, 'expected_version': 1})
    else:
        _run(books, command, {selector: document, 'expected_version': 1, 'memo': 'Corrected after merge'})
    _refused_unchanged(books, made['batch_id'], document)


@pytest.mark.parametrize('kind', ('customer', 'account', 'item'))
def test_original_parent_deactivated_blocks_before_any_reversal_write(books, kind):
    def create(name, parent=None):
        if kind == 'item':
            return _item(books, name, **({'parent_id': parent} if parent else {}))
        body = {'name': name, **({'parent_id': parent} if parent else {})}
        if kind == 'account':
            body['type'] = 'expense'
        return _run(books, f'{kind} create', body)['id']
    parents = [create(name) for name in ('Old Parent', 'New Parent')]
    leaves = [create('Leaf', parent) for parent in parents]
    made = _merge(books, kind, leaves[0], leaves[1])
    _run(books, f'{kind} deactivate', {kind: parents[0]})
    _refused_unchanged(books, made['batch_id'], parents[0])


@pytest.mark.parametrize('kind', KINDS)
def test_batch_and_its_reversal_require_merged_kind_read_permission(books, kind):
    merged, survivor = _setup(books, kind)
    batch = _merge(books, kind, merged, survivor)['batch_id']
    reverse = _reverse(books, batch)['reversal_batch']
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    company = books.company.show(company=COMPANY)['company_id']
    user = make_actor(root, 'denied-reader', company_role=(company, 'readonly'))
    member = next(m for m in books.run('membership list', {'company': company})['items']
                  if m['user_id'] == user and m['scope_type'] == 'company')
    books.run('membership grant', {'user': user, 'company': company, 'expected_version': member['version'],
                                   'denies': [kind]}, reason='Hide this record family')
    reader = as_user(root, 'denied-reader')
    for target in (batch, reverse):
        with pytest.raises(BookflowError) as caught:
            reader.run('batch show', {'batch': target}, company=COMPANY)
        assert caught.value.code == 'E_PERMISSION'


@pytest.mark.parametrize('kind', KINDS)
def test_original_unposted_document_edit_blocks_reversal(books, kind):
    merged, survivor = _setup(books, kind)
    if kind == 'customer':
        item = _item(books, 'Estimated Service')
        made_document = _run(books, 'estimate create', {
            'customer': merged, 'date': '2026-03-01', 'title': 'Proposed work',
            'lines': [{'item': item, 'quantity': '1'}]})
        command, selector, change = 'estimate update', 'estimate', {'title': 'Revised work'}
    else:
        vendor = merged if kind == 'vendor' else _run(books, 'vendor create', {'name': 'Order Vendor'})['id']
        line = {'item': merged, 'quantity': '1', 'rate': '4.00'} if kind == 'item' else {
            'account': merged if kind == 'account' else 'Truck Fuel', 'amount': '4.00'}
        made_document = _run(books, 'purchase-order post', {'vendor': vendor, 'date': '2026-03-01', 'lines': [line]})
        command, selector, change = 'purchase-order update', 'purchase_order', {'memo': 'Revised order'}
    made = _merge(books, kind, merged, survivor)
    _run(books, command, {selector: made_document['id'], 'expected_version': made_document['version'], **change})
    _refused_unchanged(books, made['batch_id'], made_document['id'])


@pytest.mark.parametrize('kind', KINDS)
def test_merge_retry_reuses_batch_and_manual_unmerge_is_not_a_second_reversal(books, kind):
    merged, survivor = _setup(books, kind)
    made = _merge(books, kind, merged, survivor)
    before = _snapshot()
    preview = _run(books, f'{kind} merge', {'merged': merged, 'into': survivor}, dry_run=True)
    assert preview['batch_id'] == made['batch_id'] and preview['next'] == made['next']
    again = _merge(books, kind, merged, survivor)
    assert not again['changed'] and again['batch_id'] == made['batch_id']
    after = _snapshot()
    # A successful no-op refreshes the actor's last-seen timestamp; no business or audit write occurs.
    before['principals'] = [row[:-1] for row in before['principals']]
    after['principals'] = [row[:-1] for row in after['principals']]
    assert after == before
    _run(books, f'{kind} unmerge', {'merged': merged})
    _refused_unchanged(books, made['batch_id'], made['merge_id'])


@pytest.mark.parametrize('kind', KINDS)
def test_missing_unmerge_reason_refuses_before_opening_reversal(books, kind):
    merged, survivor = _setup(books, kind)
    batch = _merge(books, kind, merged, survivor)['batch_id']
    before = _snapshot()
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            books.run('batch reverse', {'batch': batch}, company=COMPANY, dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any(b['code'] == 'E_REASON_REQUIRED' for b in caught.value.details['blockers'])
        assert _snapshot() == before
