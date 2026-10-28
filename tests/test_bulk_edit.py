"""R107 increment 6: grid edits (design/bulk-import.md §7, §8a; criteria 7 and 11).

Done when repricing 300 items by 5 percent previews 300 before-and-after pairs and saves 300 audit events; another
user's edit to a different field of item 17 merges and to the same field refuses only item 17; `bulk-edit reverse`
restores all 300 prices, or is refused naming item 17 when it was edited again, and `skip_changed` restores the other
299; and a seeded run of random grid edits, edits in between and reversals keeps every record equal to a replay of
the writes that survive.
"""
import random
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import bookflow
from bookflow import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'grid'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Grid organization')
    company = client.company.new(legal_name='Grid', home_currency='USD', timezone='UTC',
                                 organization='Grid organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)
    income = next(r['id'] for r in run('account list')['items'] if r['type'] == 'income')
    return SimpleNamespace(client=client, company=company, run=run, root=data_root, income=income)


def make_items(books, n, price=lambda i: f'{i}.10'):
    for i in range(1, n + 1):
        books.run('item create', {'name': f'Item {i:03d}', 'type': 'service', 'sales_enabled': True,
                                  'income_account_id': books.income, 'price': price(i), 'description': f'D{i}'})


def prices(books):
    with sqlite3.connect(Path(books.run('company show')['path']) / 'company.db') as db:
        return {name: (units, fraction) for name, units, fraction in db.execute(
            'SELECT full_name, price_minor_units, price_fraction FROM items')}


def shown_price(books, name):
    price = books.run('item show', {'item': name})['price']
    return price['amount'] if price else None


def clerk(books):
    from tests.conftest import as_user, make_actor
    make_actor(Path(books.root), 'clerk')
    books.client.run('membership grant', {'user': 'clerk', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0})
    return as_user(Path(books.root), 'clerk')


def blocked(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


RAISE = [{'op': 'percent', 'field': 'price', 'by': '5'}]


def test_reprice_three_hundred_merge_conflict_reverse_and_skip_changed(books):
    make_items(books, 300)
    snapshot = prices(books)
    other = clerk(books)
    # A clean repricing and its reversal: every price equals the snapshot (criterion 11).
    first = books.run('bulk-edit preview', {'kind': 'item', 'operations': RAISE}, reason='Prices up 5 percent')
    assert first['counts'] == {'selected': 300, 'unchanged': 0, 'change': 300, 'refused': 0}
    books.run('bulk-edit apply', {'run': first['run']['id']})
    assert prices(books) != snapshot
    undone = books.run('bulk-edit reverse', {'run': first['run']['id']}, reason='Wrong list')
    assert (undone['state'], undone['reversed_now'], undone['skipped']) == ('reversed', 300, [])
    assert prices(books) == snapshot

    # Criterion 7: 300 pairs, another user's edit to another field of item 17 merges.
    preview = books.run('bulk-edit preview', {'kind': 'item', 'operations': RAISE}, reason='Prices up 5 percent')
    run = preview['run']['id']
    full = books.run('bulk-edit show', {'run': run, 'limit': 5000})
    pairs = {r['label']: (r['changes'][0]['before'], r['changes'][0]['after']) for r in full['rows']}
    assert len(pairs) == 300 and pairs['Item 017'] == ('17.10', '17.96') and pairs['Item 001'] == ('1.10', '1.16')
    assert preview['totals']['price']['before'] == '45180.00'  # sum of i + 0.10 for i in 1..300, exact
    other.run('item update', {'item': 'Item 017', 'description': 'Clerk note'}, company=books.company)
    applied = books.run('bulk-edit apply', {'run': run})
    assert applied['counts']['updated'] == 299 and applied['counts']['merged'] == 1
    merged = next(r for r in books.run('bulk-edit show', {'run': run, 'outcome': 'merged'})['rows'])
    assert merged['label'] == 'Item 017' and merged['merged_over'] and 'other fields' in merged['outcome_message']
    assert books.run('item show', {'item': 'Item 017'})['description'] == 'Clerk note'
    with sqlite3.connect(Path(books.run('company show')['path']) / 'company.db') as db:
        events = db.execute('SELECT count(*), count(DISTINCT command) FROM audit_events WHERE source_ref = ?',
                            (f'bulk-edit:{run}',)).fetchone()
    assert events == (300, 1)

    # Edited again since: the reversal is refused naming item 17, and nothing changes.
    other.run('item update', {'item': 'Item 017', 'price': '99.00'}, company=books.company)
    raised = prices(books)
    refused = blocked(lambda: books.run('bulk-edit reverse', {'run': run}))
    assert refused.code == 'E_BATCH_BLOCKED' and refused.details['total'] == 1
    assert 'Item 017' in refused.details['blockers'][0]['message'] and prices(books) == raised
    partly = books.run('bulk-edit reverse', {'run': run, 'skip_changed': True}, reason='Undo but keep the clerk fix')
    assert (partly['state'], partly['reversed_now']) == ('reversed_partly', 299)
    assert [s['record_id'] for s in partly['skipped']] == [merged['record_id']]
    after = prices(books)
    assert all(after[name] == snapshot[name] for name in snapshot if name != 'Item 017')
    assert shown_price(books, 'Item 017') == '99.00'
    assert [s['label'] for s in books.run('bulk-edit show', {'run': run})['skipped']] == ['Item 017']
    assert 'already reversed_partly' in blocked(lambda: books.run('bulk-edit reverse', {'run': run})).message

    # The same field changed meanwhile refuses only that record, naming who; the rest apply.
    twenty = [f'Item {i:03d}' for i in range(1, 21)]
    preview = books.run('bulk-edit preview', {'kind': 'item', 'records': twenty,
                                              'operations': [{'op': 'add', 'field': 'price', 'by': '1'}]})
    other.run('item update', {'item': 'Item 017', 'price': '50.00'}, company=books.company)
    applied = books.run('bulk-edit apply', {'run': preview['run']['id']})
    assert applied['run']['state'] == 'applied_partly'
    assert (applied['counts']['updated'], applied['counts']['conflict']) == (19, 1)
    conflict = books.run('bulk-edit show', {'run': preview['run']['id'], 'outcome': 'conflict'})['rows']
    assert [r['label'] for r in conflict] == ['Item 017'] and conflict[0]['outcome_code'] == 'E_VERSION_CONFLICT'
    assert shown_price(books, 'Item 017') == '50.00' and shown_price(books, 'Item 018') == '19.10'

    # All or nothing: a conflict puts back what was already changed.
    before = prices(books)
    preview = books.run('bulk-edit preview', {'kind': 'item', 'records': twenty, 'atomic': True,
                                              'operations': [{'op': 'add', 'field': 'price', 'by': '2'}]})
    other.run('item update', {'item': 'Item 017', 'price': '51.00'}, company=books.company)
    rolled = books.run('bulk-edit apply', {'run': preview['run']['id']})
    assert rolled['run']['state'] == 'rolled_back' and rolled['counts']['not_run'] == 3
    after = prices(books)
    assert {k: v for k, v in after.items() if k != 'Item 017'} == {k: v for k, v in before.items() if k != 'Item 017'}


def test_rounding_is_half_up_on_the_currency_places_and_to_a_step(books):
    for name, price in (('Half', '0.30'), ('Odd', '10.05'), ('Tiny', '0.01'), ('Blank', None)):
        books.run('item create', {'name': name, 'type': 'service', 'sales_enabled': True,
                                  'income_account_id': books.income, **({'price': price} if price else {})})
    names = ['Half', 'Odd', 'Tiny', 'Blank']

    def after(operations, records=('Half', 'Odd', 'Tiny')):
        out = books.run('bulk-edit preview', {'kind': 'item', 'records': list(records), 'operations': operations})
        return {r['label']: r['changes'][0]['after'] for r in out['rows']}
    # 0.315 rounds up to 0.32 (half-up, not to even); 10.5525 to 10.55; 0.0105 to 0.01, unchanged, so not in the diff.
    assert after([{'op': 'percent', 'field': 'price', 'by': '5'}]) == {'Half': '0.32', 'Odd': '10.55'}
    # To a step of 0.05: 0.315 to 0.30 (unchanged), 10.5525 to 10.55, 0.0105 to 0.00.
    assert after([{'op': 'percent', 'field': 'price', 'by': '5', 'round': '0.05'}]) == {'Odd': '10.55', 'Tiny': '0.00'}
    # 5.025 to 5.03; 0.005 to 0.01 (unchanged).
    assert after([{'op': 'multiply', 'field': 'price', 'by': '0.5'}]) == {'Half': '0.15', 'Odd': '5.03'}
    # 0.295 to 0.30, 10.045 to 10.05, 0.005 to 0.01: half-up leaves all three as they were.
    assert after([{'op': 'add', 'field': 'price', 'by': '-0.005'}]) == {}
    # Below zero the half rounds away from zero (a credit's -0.315 to -0.32), exactly, in Decimals.
    from bookflow.company.bulk_edit import rounded
    money = {'type': 'money'}
    assert [str(rounded(Decimal(v), Decimal('0.01'), money, 2)) for v in ('-0.315', '-0.3149', '2.675', '1E+2')] == [
        '-0.32', '-0.31', '2.68', '100.00']
    assert str(rounded(Decimal('0.125'), Decimal('0.05'), money, 2)) == '0.15'
    assert books.run('item show', {'item': 'Blank'})['cost'] is None
    # A blank stays blank (no item here has a cost): nothing to add to or multiply.
    assert after([{'op': 'add', 'field': 'cost', 'by': '1'}], names) == {}
    # Typed values are exact or refused, never floats.
    assert blocked(lambda: books.run('bulk-edit preview', {'kind': 'item', 'records': names, 'operations': [
        {'op': 'set', 'field': 'price', 'value': '1.2.3'}]})).code == 'E_VALIDATION'
    assert blocked(lambda: books.run('bulk-edit preview', {'kind': 'item', 'records': names, 'operations': [
        {'op': 'percent', 'field': 'price', 'by': '5', 'round': '-1'}]})).code == 'E_VALIDATION'


def test_a_refused_record_in_the_middle_and_the_other_operations(books):
    make_items(books, 5)
    preview = books.run('bulk-edit preview', {'kind': 'item', 'changes': [
        {'record': 'Item 003', 'field': 'name', 'value': 'Item 001'}],
        'operations': [{'op': 'replace', 'field': 'description', 'find': 'D', 'replace': 'Desc '}]})
    rows = {r['label']: r for r in preview['rows']}
    assert rows['Item 003']['status'] == 'refused' and rows['Item 003']['code'] == 'E_NAME_TAKEN'
    assert preview['counts'] == {'selected': 5, 'unchanged': 0, 'change': 4, 'refused': 1}
    # All or nothing refuses a preview with a refused record; by default the others apply.
    atomic = blocked(lambda: books.run('bulk-edit preview', {'kind': 'item', 'atomic': True, 'changes': [
        {'record': 'Item 003', 'field': 'name', 'value': 'Item 001'}]}))
    assert atomic.code == 'E_VALIDATION' and atomic.details['refused'][0]['record'] == 'Item 003'
    applied = books.run('bulk-edit apply', {'run': preview['run']['id']})
    assert applied['run']['state'] == 'applied_partly' and applied['counts']['updated'] == 4
    assert books.run('item show', {'item': 'Item 004'})['description'] == 'Desc 4'
    assert books.run('item show', {'item': 'Item 003'})['description'] == 'D3'
    # copy_down, set_from and clear; a paste by header and by position.
    out = books.run('bulk-edit preview', {'kind': 'item', 'records': ['Item 002', 'Item 004', 'Item 005'],
                                          'operations': [{'op': 'copy_down', 'field': 'price'},
                                                         {'op': 'set_from', 'field': 'purchase_description',
                                                          'from': 'description'}]})
    got = {r['label']: {c['field']: c['after'] for c in r['changes']} for r in out['rows']}
    assert got['Item 004']['price'] == '2.10' and got['Item 005']['purchase_description'] == 'Desc 5'
    pasted = books.run('bulk-edit preview', {'kind': 'item', 'paste': 'Name\tPrice\nItem 001\t7.00\nItem 005\t8.5\n'})
    assert {r['label']: r['changes'][0]['after'] for r in pasted['rows']} == {'Item 001': '7.00', 'Item 005': '8.5'}
    by_position = books.run('bulk-edit preview', {'kind': 'item', 'records': ['Item 002', 'Item 001'],
                                                  'paste': '3.00\n4.00', 'paste_columns': ['price']})
    assert {r['label']: r['changes'][0]['after'] for r in by_position['rows']} == {'Item 002': '3.00',
                                                                                   'Item 001': '4.00'}
    assert blocked(lambda: books.run('bulk-edit preview', {'kind': 'item', 'paste': 'Name\tColour\nItem 001\tred'}
                                     )).details['unknown'] == ['Colour']
    assert blocked(lambda: books.run('bulk-edit preview', {'kind': 'account', 'operations': [
        {'op': 'set', 'field': 'type', 'value': 'bank'}]})).code == 'E_VALIDATION'  # §7: never an account's type


def test_customer_terms_by_filter_and_reference_names(books):
    for name in ('Acme', 'Birch', 'Cobalt'):
        books.run('customer create', {'name': name, 'credit_limit': '1000.00'})
    columns = {c['field']: c for c in books.run('bulk-edit columns', {'kind': 'customer'})['columns']}
    assert columns['terms_id']['type'] == 'reference' and columns['terms_id']['target'] == 'term'
    assert 'billing_address' not in columns and 'contacts' not in columns
    preview = books.run('bulk-edit preview', {'kind': 'customer', 'query': 'Acme', 'operations': [
        {'op': 'set', 'field': 'terms_id', 'value': 'Net 30'}, {'op': 'clear', 'field': 'credit_limit'}]})
    row = preview['rows'][0]
    assert row['label'] == 'Acme' and {c['field']: c['after_text'] for c in row['changes']} == {
        'terms_id': 'Net 30', 'credit_limit': ''}
    unknown = blocked(lambda: books.run('bulk-edit preview', {'kind': 'customer', 'operations': [
        {'op': 'set', 'field': 'terms_id', 'value': 'Net 99'}]}))
    assert unknown.details['problem'] == 'unknown_reference'
    books.run('bulk-edit apply', {'run': preview['run']['id']})
    grid = books.run('bulk-edit grid', {'kind': 'customer', 'columns': ['terms_id', 'credit_limit']})
    values = {r['label']: r['values'] for r in grid['records']}
    assert values['Acme'] == {'terms_id': 'Net 30', 'credit_limit': ''} and values['Birch']['credit_limit'] == '1000.00'
    books.run('bulk-edit reverse', {'run': preview['run']['id']})
    shown = books.run('customer show', {'customer': 'Acme'})
    assert shown['terms_id'] is None and shown['credit_limit']['amount'] == '1000.00'


def test_reversal_needs_the_callers_own_grants(books):
    make_items(books, 3)
    preview = books.run('bulk-edit preview', {'kind': 'item', 'operations': RAISE})
    books.run('bulk-edit apply', {'run': preview['run']['id']})
    from tests.conftest import as_user, make_actor
    make_actor(Path(books.root), 'nopricing')
    books.client.run('membership grant', {'user': 'nopricing', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0, 'denies': ['item']})
    denied = as_user(Path(books.root), 'nopricing')
    before = prices(books)
    assert blocked(lambda: denied.run('bulk-edit reverse', {'run': preview['run']['id']},
                                      company=books.company)).code == 'E_PERMISSION'
    assert blocked(lambda: denied.run('bulk-edit preview', {'kind': 'item', 'operations': RAISE},
                                      company=books.company)).code == 'E_PERMISSION'
    assert prices(books) == before
    make_actor(Path(books.root), 'viewer')
    books.client.run('membership grant', {'user': 'viewer', 'company': books.company, 'role': 'readonly',
                                          'expected_version': 0})
    viewer = as_user(Path(books.root), 'viewer')
    assert viewer.run('bulk-edit query', {}, company=books.company)['items']  # reads are member
    assert blocked(lambda: viewer.run('bulk-edit apply', {'run': preview['run']['id']},
                                      company=books.company)).code == 'E_PERMISSION'


def test_an_edit_between_two_batches_still_blocks_the_earlier_one(books):
    """Found by the seeded test (seed 107): an edit no batch recorded, made between two grid edits, stayed invisible once
    the later one was reversed (its reversal left the version the plan compared with), so reversing the earlier one
    wrote over it. The writes since a batch must now follow each other version by version."""
    make_items(books, 2)
    first = books.run('bulk-edit preview', {'kind': 'item', 'operations': [
        {'op': 'set', 'field': 'description', 'value': 'first'}]})
    books.run('bulk-edit apply', {'run': first['run']['id']})
    books.run('item update', {'item': 'Item 001', 'description': 'typed between'})
    second = books.run('bulk-edit preview', {'kind': 'item', 'records': ['Item 001'], 'operations': [
        {'op': 'set', 'field': 'description', 'value': 'second'}]})
    books.run('bulk-edit apply', {'run': second['run']['id']})
    books.run('bulk-edit reverse', {'run': second['run']['id']})
    assert books.run('item show', {'item': 'Item 001'})['description'] == 'typed between'
    refused = blocked(lambda: books.run('bulk-edit reverse', {'run': first['run']['id']}))
    assert [b['code'] for b in refused.details['blockers']] == ['edited_again']
    assert 'Item 001' in refused.details['blockers'][0]['message']
    out = books.run('bulk-edit reverse', {'run': first['run']['id'], 'skip_changed': True})
    assert out['state'] == 'reversed_partly' and books.run('item show', {'item': 'Item 002'})['description'] == 'D2'
    assert books.run('item show', {'item': 'Item 001'})['description'] == 'typed between'


def test_a_later_batch_partly_reversed_still_stands_where_it_skipped(books):
    make_items(books, 2)
    first = books.run('bulk-edit preview', {'kind': 'item', 'operations': RAISE})
    books.run('bulk-edit apply', {'run': first['run']['id']})
    later = books.run('bulk-edit preview', {'kind': 'item', 'operations': [
        {'op': 'add', 'field': 'price', 'by': '1'}]})
    books.run('bulk-edit apply', {'run': later['run']['id']})
    books.run('item update', {'item': 'Item 002', 'description': 'typed'})
    books.run('bulk-edit reverse', {'run': later['run']['id'], 'skip_changed': True})  # leaves Item 002
    refused = blocked(lambda: books.run('bulk-edit reverse', {'run': first['run']['id']}))
    codes = {b['record_id']: b['code'] for b in refused.details['blockers']}
    assert list(codes.values()) == ['later_batch']  # Item 002: the later edit's price still stands


@pytest.mark.parametrize('seed', (6, 107, 2026))
def test_seeded_grid_edits_edits_between_and_reversals_equal_a_replay(books, seed):
    """Random grid edits (with an edit by someone else between preview and apply, sometimes to the same field),
    single edits and reversals (sometimes skip_changed): after every step each item equals the replay of every write
    still standing, in order -- the reversed batches' writes left out, except on the records a reversal skipped."""
    rng = random.Random(seed)
    names = [f'Item {i:03d}' for i in range(1, 9)]
    make_items(books, 8, price=lambda i: f'{i * 3}.{i:02d}')
    other = clerk(books)
    initial = {n: {'price': shown_price(books, n), 'description': f'D{int(n[-3:])}'} for n in names}
    writes = []  # [event, record, field, value, standing]
    live = []
    totals = {'grid edits': 0, 'merged': 0, 'conflicts': 0, 'single edits': 0, 'reversed': 0, 'partly': 0,
              'blocked': 0}

    def norm(field, value):
        if field == 'price' and value not in (None, ''):
            return Decimal(str(value))
        return value or None

    def replay():
        state = {n: dict(v) for n, v in initial.items()}
        for _, record, field, value, standing in writes:
            if standing:
                state[record][field] = value
        return {n: {f: norm(f, v) for f, v in fields.items()} for n, fields in state.items()}

    def books_now():
        out = {}
        for n in names:
            shown = books.run('item show', {'item': n})
            out[n] = {'price': norm('price', shown['price']['amount'] if shown['price'] else None),
                      'description': norm('description', shown['description'])}
        return out

    def single(record, field, by_other=False):
        value = (f'{rng.randint(1, 500)}.{rng.randint(0, 99):02d}' if field == 'price'
                 else rng.choice(['red', 'blue', 'note ' + str(rng.randint(1, 9))]))
        (other.run if by_other else books.client.run)('item update', {'item': record, field: value},
                                                     company=books.company)
        writes.append([('single', len(writes)), record, field, value, True])
        totals['single edits'] += 1

    for step in range(18):
        roll = rng.random()
        if roll < 0.45 or not live:
            chosen = rng.sample(names, rng.randint(1, len(names)))
            op = rng.choice([
                {'op': 'percent', 'field': 'price', 'by': str(rng.randint(-10, 25)),
                 **({'round': rng.choice(['0.05', '0.01', '1'])} if rng.random() < 0.6 else {})},
                {'op': 'add', 'field': 'price', 'by': rng.choice(['1.25', '-0.50', '10'])},
                {'op': 'set', 'field': 'description', 'value': f'step {step}'},
                {'op': 'replace', 'field': 'description', 'find': 'D', 'replace': 'd'}])
            preview = books.run('bulk-edit preview', {'kind': 'item', 'records': chosen, 'operations': [op]})
            if not preview['counts']['change']:
                continue
            if rng.random() < 0.4:  # someone else edits a selected record between preview and apply
                single(rng.choice(chosen), rng.choice(['price', 'description']), by_other=True)
            applied = books.run('bulk-edit apply', {'run': preview['run']['id']})
            run = preview['run']['id']
            for row in books.run('bulk-edit show', {'run': run, 'limit': 5000})['rows']:
                if row['outcome'] in ('updated', 'merged'):
                    for change in row['changes']:
                        writes.append([run, row['label'], change['field'], change['after'], True])
            totals['merged'] += applied['counts'].get('merged', 0)
            totals['conflicts'] += applied['counts'].get('conflict', 0)
            totals['grid edits'] += 1
            live.append(run)
        elif roll < 0.7:
            single(rng.choice(names), rng.choice(['price', 'description']), by_other=rng.random() < 0.5)
        else:
            run = rng.choice(live)
            skip = rng.random() < 0.5
            before = books_now()
            try:
                out = books.run('bulk-edit reverse', {'run': run, 'skip_changed': skip})
            except BookflowError as error:
                assert error.code == 'E_BATCH_BLOCKED' and not skip and books_now() == before
                assert all(b['code'] in ('edited_again', 'later_batch') for b in error.details['blockers'])
                totals['blocked'] += 1
                continue
            skipped = {s['record_id'] for s in out['skipped']}
            labels = {r['record_id']: r['label'] for r in books.run('bulk-edit show', {'run': run,
                                                                                      'limit': 5000})['rows']}
            left = {labels[i] for i in skipped}
            for w in writes:
                if w[0] == run and w[1] not in left:
                    w[4] = False
            totals['partly' if skipped else 'reversed'] += 1
            live.remove(run)
        assert books_now() == replay(), (seed, step)
    print(seed, totals)
    assert totals['grid edits'] and (totals['reversed'] + totals['partly'])
