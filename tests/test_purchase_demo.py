"""Execute the appended purchase examples themselves, with an independent cents oracle."""
from collections import Counter, defaultdict
import re
import sqlite3

import bookflow
from tests.test_reference_year import demo_runner


CAPTURES = ('purchase_demo_item', 'purchase_demo_check', 'purchase_demo_card',
            'purchase_demo_free_sale', 'purchase_demo_order', 'purchase_demo_receipt',
            'purchase_demo_bill', 'purchase_demo_order_remaining', 'purchase_demo_receipt_remaining')
FINANCIAL = ('transactions', 'transaction_revisions', 'posting_batches', 'posting_lines',
             'posting_line_sources')
TABLES = FINANCIAL + ('document_lines', 'inventory_movements', 'money_out_documents',
    'money_out_item_lines', 'purchase_profiles', 'purchase_item_lines', 'sales_profiles',
    'sales_line_profiles', 'purchase_orders', 'purchase_order_revisions', 'purchase_order_lines',
    'purchase_order_conversions', 'item_receipts', 'item_receipt_revisions', 'item_receipt_lines',
    'purchase_order_receipt_claims', 'purchase_order_receipt_releases', 'receipt_bill_claims',
    'receipt_bill_releases', 'receipt_bill_adjustments', 'ap_obligation_keys',
    'ap_obligation_components', 'payment_component_keys', 'payment_components', 'deposit_components')


def snapshot(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {name: {r['rowid']: dict(r) for r in db.execute(f'SELECT rowid,* FROM "{name}"')}
                for name in TABLES}


def assert_inventory(path, before, after, before_bill):
    added = {}
    for table in TABLES:
        for key, row in before[table].items():
            assert after[table].get(key) == row, (table, key, 'existing row changed/deleted')
        added[table] = [r for key, r in after[table].items() if key not in before[table]]
    assert all(before[t] for t in (*FINANCIAL, 'inventory_movements'))
    with sqlite3.connect(path) as db:
        accounts = dict(db.execute('SELECT id,name FROM accounts'))
        item, = db.execute("SELECT id FROM items WHERE name='December Service Kit'").fetchone()
    transactions = {r['id']: r for r in added['transactions']}
    markers = {r['kind']: r['transaction_id'] for r in added['money_out_documents']}
    assert set(markers) == {'check', 'card_charge'}
    receipt, = added['item_receipts']
    bill, = [r for r in transactions.values() if r['type'] == 'bill']
    sale, = [r for r in transactions.values() if r['type'] == 'sales_receipt']
    adjustment, = added['receipt_bill_adjustments']
    movements = {r['id']: r for r in added['inventory_movements']}
    recost = movements[adjustment['movement_id']]
    owners = {markers['check']: 'check', markers['card_charge']: 'card', sale['id']: 'free',
              receipt['transaction_id']: 'receipt', bill['id']: 'bill', recost['transaction_id']: 'recost'}
    assert set(transactions) == set(owners) and len(transactions) == 6
    assert Counter(r['type'] for r in transactions.values()) == Counter(journal_entry=4, sales_receipt=1, bill=1)
    assert all(r['status'] == 'posted' for r in transactions.values())
    revisions = {r['id']: r for r in added['transaction_revisions']}
    assert Counter(r['transaction_id'] for r in revisions.values()) == Counter(owners.keys())
    batches = {r['id']: r for r in added['posting_batches']}
    assert Counter(r['transaction_id'] for r in batches.values()) == Counter(owners.keys())
    for batch in batches.values():
        assert batch['revision_id'] == transactions[batch['transaction_id']]['current_revision_id']
        assert batch['kind'] == 'original' and batch['reverses_batch_id'] is None

    # Gross legs, not just balanced nets: unrelated cancelling additions also fail.
    expected = {
        'check': [('Payment Example Bank', 0, 1100), ('Professional Fees', 483, 0), ('Inventory Asset', 617, 0)],
        'card': [('Business Credit Card', 0, 2000), ('Inventory Asset', 2000, 0)],
        'free': [('Cost of Goods Sold', 523, 0), ('Inventory Asset', 0, 523)],
        'receipt': [('Accounts Payable', 0, 6000), ('Inventory Asset', 6000, 0)],
        'bill': [('Accounts Payable', 0, 4400), ('Accounts Payable', 4400, 0)],
        'recost': [('Inventory Asset', 400, 0), ('Accounts Payable', 0, 400)],
    }
    dates = dict(check='2026-12-01', card='2026-12-02', free='2026-12-03',
                 receipt='2026-12-05', bill='2026-12-06', recost='2026-12-05')
    lines = {r['id']: r for r in added['posting_lines']}
    assert Counter((owners[r['transaction_id']], accounts[r['account_id']], r['debit_minor_units'],
                    r['credit_minor_units']) for r in lines.values()) == Counter(
        (owner, *leg) for owner, legs in expected.items() for leg in legs)
    for line in lines.values():
        batch = batches[line['batch_id']]
        assert batch['transaction_id'] == line['transaction_id']
        assert batch['effective_date'] == dates[owners[line['transaction_id']]]
        assert line['currency'] == 'USD' and line['reversed_line_id'] is None
    envelopes = {r['id']: r for r in added['document_lines']}
    sources = added['posting_line_sources']
    assert Counter(s['posting_line_id'] for s in sources) == Counter(lines.keys())
    for source in sources:
        line = lines[source['posting_line_id']]
        envelope = envelopes[source['document_line_id']]
        assert source['transaction_id'] == line['transaction_id'] == envelope['transaction_id']
        assert source['revision_id'] == envelope['revision_id'] == batches[line['batch_id']]['revision_id']
        assert source['amount_minor_units'] == max(line['debit_minor_units'], line['credit_minor_units'])
        assert source['currency'] == 'USD' and source['reversed_source_id'] is None
        assert source['payment_component_id'] is None and source['deposit_component_id'] is None
        assert source['tax_component_id'] is None

    # Whole added stock inventory: exactly three receipts, one issue, one value correction.
    expected_stock = [('check', 'receipt', 500000, 617, 'Payment Example Bank'),
        ('card', 'receipt', 2000000, 2000, 'Business Credit Card'),
        ('free', 'issue', -500000, -523, 'Cost of Goods Sold'),
        ('receipt', 'receipt', 6000000, 6000, 'Accounts Payable'),
        ('recost', 'recost', 0, 400, 'Accounts Payable')]
    assert Counter((owners[m['transaction_id']], m['kind'], m['quantity_microunits'],
        m['value_minor_units'], accounts[m['offset_account_id']]) for m in movements.values()) == Counter(expected_stock)
    for movement in movements.values():
        assert movement['item_id'] == item and movement['asset_account_id'] in accounts
        leg = lines[movement['posting_line_id']]
        assert accounts[leg['account_id']] == 'Inventory Asset'
        assert leg['debit_minor_units'] - leg['credit_minor_units'] == movement['value_minor_units']
        assert leg['transaction_id'] == movement['transaction_id']
        assert movement['document_line_id'] in envelopes
    for key, row in before_bill['inventory_movements'].items():
        assert after['inventory_movements'][key] == row
    assert [r for key, r in after['inventory_movements'].items()
            if key not in before_bill['inventory_movements']] == [recost]
    assert after['purchase_order_receipt_claims'] == before_bill['purchase_order_receipt_claims']
    receipt_line, = added['item_receipt_lines']
    assert recost['corrects_movement_id'] == receipt_line['movement_id']
    assert receipt_line['receipt_id'] == receipt['id'] and receipt_line['item_id'] == item
    assert (receipt_line['quantity_microunits'], receipt_line['value_minor_units']) == (6000000, 6000)
    order, = added['purchase_orders']
    po_claim, = added['purchase_order_receipt_claims']
    assert po_claim['receipt_line_id'] == receipt_line['id'] and po_claim['quantity_microunits'] == 6000000
    po_line, = [r for r in added['purchase_order_lines'] if r['line_id'] == po_claim['order_line_id']
                and r['revision_id'] == po_claim['order_revision_id']]
    assert po_line['document_id'] == order['id']
    claim, = added['receipt_bill_claims']
    assert claim['receipt_line_id'] == receipt_line['id'] and claim['bill_id'] == bill['id']
    assert (claim['start_microunits'], claim['end_microunits'], claim['original_minor_units'],
            claim['billed_minor_units']) == (0, 4000000, 4000, 4400)
    assert claim['bill_revision_id'] == bill['current_revision_id']
    assert envelopes[claim['bill_line_id']]['transaction_id'] == bill['id']
    assert adjustment['receipt_line_id'] == receipt_line['id'] and adjustment['bill_id'] == bill['id']
    assert added['purchase_order_conversions'] == added['purchase_order_receipt_releases'] == added['receipt_bill_releases'] == []
    obligation, = added['ap_obligation_keys']
    assert obligation['transaction_id'] == bill['id']
    component, = added['ap_obligation_components']
    assert component['transaction_id'] == bill['id'] and component['amount_minor_units'] == 4400
    assert added['payment_component_keys'] == added['payment_components'] == added['deposit_components'] == []
    return dict(item=item, check=markers['check'], card=markers['card_charge'], sale=sale['id'],
                receipt=receipt['id'], order=order['id'], bill=bill['id'])


def test_added_purchase_seed_examples_preserve_existing_rows_and_match_exact_stock_and_sources(tmp_path, monkeypatch):
    from bookflow.commands import hub_cmds
    root = tmp_path / 'purchase-seed-root'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    load_seed, apply_seed = hub_cmds._load_seed, hub_cmds._apply_seed_history
    observed = {}

    def load_bounded(resource='seed.toml'):
        assert resource == 'seed.toml'
        seed = load_seed(resource)
        commands = seed['commands']
        start = next(i for i, e in enumerate(commands) if e.get('capture') == CAPTURES[0])
        block = commands[start:start + len(CAPTURES)]
        assert tuple(e.get('capture') for e in block) == CAPTURES
        assert [e['command'] for e in block] == ['item create', 'check post', 'card-charge post',
            'sales-receipt post', 'purchase-order post', 'item-receipt post', 'bill post',
            'purchase-order show', 'item-receipt show']
        # Reuse actual original capture producers, in original order. Include populated
        # expense-only purchases and a whole-order bill as preservation sentinels.
        producers = {e['capture']: i for i, e in enumerate(commands) if e.get('capture')}
        # Company preference transitions are uncaptured prerequisites too (classes
        # during vendor creation, then the final commercial tax preferences).
        needed = {i for i, e in enumerate(commands[:start]) if e['command'] == 'company update'}
        def include(name):
            index = producers[name]
            if index in needed:
                return
            needed.add(index)
            for dependency in re.findall(r'\$\{([a-z][a-z0-9_]*)\.', str(commands[index])):
                include(dependency)
        for name in (*CAPTURES, 'demo_check', 'demo_card_charge', 'demo_order_bill'):
            include(name)
        selected = [commands[i] for i in sorted(needed)]
        assert selected[-len(block):] == block
        assert len(selected) < start  # never accidentally run the full historical seed
        return dict(seed, commands=selected)

    def observe(session, context, seed, row):
        path = session.abs_path(row['path']) / 'company.db'
        def commands():
            for entry in seed['commands']:
                if entry.get('capture') == CAPTURES[0]:
                    before = snapshot(path)
                if entry.get('capture') == 'purchase_demo_bill':
                    before_bill = snapshot(path)
                yield entry
            observed.update(assert_inventory(path, before, snapshot(path), before_bill))
            observed['company'] = row['id']
        return apply_seed(session, context, dict(seed, commands=commands()), row)

    monkeypatch.setattr(hub_cmds, '_load_seed', load_bounded)
    monkeypatch.setattr(hub_cmds, '_apply_seed_history', observe)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.demo.reset()
    assert observed
    run = demo_runner(client, observed['company'], 'Inspect actual purchase demo seeds')
    check = run('check show', check=observed['check'])['document']
    assert (check['amount']['minor_units'], check['item_total']['minor_units'], check['expense_total']['minor_units']) == (1100, 617, 483)
    assert check['items'][0]['profile']['quantity_microunits'] == 500000
    card = run('card-charge show', card_charge=observed['card'])['document']
    assert (card['amount']['minor_units'], card['item_total']['minor_units'], card['expense_lines']) == (2000, 2000, 0)
    assert card['items'][0]['profile']['quantity_microunits'] == 2000000
    assert run('sales-receipt show', sales_receipt=observed['sale'])['total_minor_units'] == 0
    order = run('purchase-order show', purchase_order=observed['order'])
    assert order['receiving'][0]['remaining_quantity_microunits'] == 4000000
    receipt = run('item-receipt show', receipt=observed['receipt'])
    assert receipt['items'][0]['unbilled_quantity_microunits'] == 2000000
    assert receipt['receipt_liability_current']['minor_units'] == 2000
    bill = run('bill show', bill=observed['bill'])
    assert bill['settlement_current']['open_minor_units'] == 4400
    for date, quantity, value in [('2026-12-02', '2.5', 2617), ('2026-12-03', '2', 2094),
                                  ('2026-12-05', '8', 8494), ('2026-12-06', '8', 8494)]:
        status = run('report stock-status', as_of=date, limit=200)
        stock, = [r for r in status['rows'] if r['item_id'] == observed['item']]
        assert (stock['quantity_on_hand'], stock['asset_value']['minor_units']) == (quantity, value)

    assert run('deposit sources', date='2026-12-06')['items'] == []
    ledger = run('report general-ledger', date_from='2026-12-01', date_to='2026-12-06', limit=200)
    assert ledger['next_cursor'] is None
    nets = defaultdict(int)
    for row in ledger['rows']:
        if row['kind'] == 'posting':
            nets[row['current_account_label']] += row['debit']['minor_units'] - row['credit']['minor_units']
    assert dict(nets) == {'Inventory Asset': 8494, 'Cost of Goods Sold': 523, 'Professional Fees': 483,
                         'Payment Example Bank': -1100, 'Business Credit Card': -2000, 'Accounts Payable': -6400}
    assert sum(v for v in nets.values() if v > 0) == 9500
    assert -sum(v for v in nets.values() if v < 0) == 9500


def test_added_shipping_seed_executes_actual_commands_without_duplicate_stock(tmp_path, monkeypatch):
    from bookflow.commands import hub_cmds
    captures=('shipping_demo_item','shipping_demo_order','shipping_demo_receipt','shipping_demo_bill')
    root=tmp_path/'shipping-seed-root'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY',raising=False)
    load_seed,apply_seed=hub_cmds._load_seed,hub_cmds._apply_seed_history
    evidence={}
    def load(resource='seed.toml'):
        seed=load_seed(resource);commands=seed['commands']
        producers={e['capture']:i for i,e in enumerate(commands) if e.get('capture')}
        start=producers[captures[0]]
        assert tuple(e['capture'] for e in commands[start:])==captures
        needed={i for i,e in enumerate(commands[:start]) if e['command']=='company update'}
        def include(name):
            index=producers[name]
            if index in needed:return
            needed.add(index)
            for dependency in re.findall(r'\$\{([a-z][a-z0-9_]*)\.',str(commands[index])):include(dependency)
        for name in (*CAPTURES,*captures):include(name)
        assert len(needed)<start
        return dict(seed,commands=[commands[i] for i in sorted(needed)])
    def observe(session,context,seed,row):
        dbpath=session.abs_path(row['path'])/'company.db'
        def commands():
            for entry in seed['commands']:
                if entry.get('capture')==captures[0]:before=snapshot(dbpath)
                if entry.get('capture')==captures[-1]:before_bill=snapshot(dbpath)
                yield entry
            after=snapshot(dbpath)
            added={}
            for table in TABLES:
                assert all(after[table].get(key)==value for key,value in before[table].items()),table
                added[table]=[r for k,r in after[table].items() if k not in before[table]]
            assert before['inventory_movements'] and before['posting_lines']
            assert len(added['transactions'])==len(added['transaction_revisions'])==len(added['posting_batches'])==2
            assert len(added['posting_lines'])==len(added['posting_line_sources'])==4
            assert after['inventory_movements']==before_bill['inventory_movements']
            assert after['purchase_order_receipt_claims']==before_bill['purchase_order_receipt_claims']
            movement,=added['inventory_movements']
            line,=added['item_receipt_lines'];claim,=added['receipt_bill_claims']
            assert (movement['quantity_microunits'],movement['value_minor_units'])==(3000000,3600)
            assert line['movement_id']==movement['id'] and line['shipping_minor_units']==1200
            assert (claim['receipt_line_id'],claim['start_microunits'],claim['end_microunits'],claim['original_minor_units'],claim['billed_minor_units'],claim['shipping_minor_units'])==(line['id'],0,2000000,2400,2400,800)
            assert added['receipt_bill_adjustments']==[]
            assert added['money_out_documents']==added['payment_components']==added['deposit_components']==[]
            with sqlite3.connect(dbpath) as db:accounts=dict(db.execute('SELECT id,name FROM accounts'))
            assert Counter((accounts[x['account_id']],x['debit_minor_units'],x['credit_minor_units']) for x in added['posting_lines'])==Counter([('Inventory Asset',3600,0),('Accounts Payable',0,3600),('Accounts Payable',2400,0),('Accounts Payable',0,2400)])
            posting={x['id']:x for x in added['posting_lines']}
            assert Counter(x['posting_line_id'] for x in added['posting_line_sources'])==Counter(posting.keys())
            for source in added['posting_line_sources']:
                leg=posting[source['posting_line_id']]
                assert source['transaction_id']==leg['transaction_id']
                assert source['amount_minor_units']==max(leg['debit_minor_units'],leg['credit_minor_units'])
            evidence.update(company=row['id'],receipt=line['receipt_id'],order=added['purchase_orders'][0]['id'])
        return apply_seed(session,context,dict(seed,commands=commands()),row)
    monkeypatch.setattr(hub_cmds,'_load_seed',load)
    monkeypatch.setattr(hub_cmds,'_apply_seed_history',observe)
    client=bookflow.connect(data_root=str(root));client.init();client.demo.reset()
    run=demo_runner(client,evidence['company'],'Inspect shipping demo')
    receipt=run('item-receipt show',receipt=evidence['receipt'])
    assert receipt['receipt_liability_current']['minor_units']==1200
    assert receipt['items'][0]['unbilled_quantity_microunits']==1000000
    assert run('purchase-order show',purchase_order=evidence['order'])['receiving'][0]['remaining_quantity_microunits']==2000000
