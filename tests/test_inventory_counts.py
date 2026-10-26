"""R104 increment 2: physical counts are recorded inputs, and their differences are derived (design §6).

The plumber's van is counted at four o'clock: Widget, Elbow and Valve. The agent enters the day's jobs
at nine with the default position, so they land before the count, and the van ends exactly as counted
with only the true shrinkage posted. A sale stated ``after`` takes the van below the count; a transfer
backdated before the count turns "missing" units into moved ones and reverses that loss; found units
are recosted by a purchase backdated before the count. Blind sheets never show expected; two counters
entering at once both land; retries land once; a stale preview is refused.
"""
import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _held, _part, _tie_every_date
from bookflow.company.inventory_costing import replay


def _account(books, name):
    rows = books['client'].account.query(company=books['company'], limit=200)['items']
    found = [row['id'] for row in rows if name in (row.get('name'), row.get('full_name'))]
    return found[0] if found else books['client'].account.create(company=books['company'], name=name,
                                                                 type='cost_of_goods_sold')['id']


@pytest.fixture
def van(books):
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse'), reason='Our warehouse')
    run('inventory-site create', dict(name='Van 1'), reason='The van')
    items = {name: _part(books, name, cost) for name, cost in (('Widget', '2.00'), ('Elbow', '1.00'), ('Valve', '5.00'))}
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=items['Widget'], quantity='20', unit_cost='2.00'),
        dict(item=items['Elbow'], quantity='10', unit_cost='1.00'),
        dict(item=items['Valve'], quantity='6', unit_cost='5.00')]), reason='Stock in')
    run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Widget', quantity='5'), dict(item='Elbow', quantity='4'), dict(item='Valve', quantity='2')]),
        reason='Load the van')
    shrinkage = _account(books, 'Inventory Shrinkage')
    return dict(books=books, run=run, items=items, shrinkage=shrinkage)


def _jobs(run, books, position=None, **quantities):
    lines = [dict(item=name, quantity=str(quantity)) for name, quantity in quantities.items()]
    payload = dict(customer=books['customer'], date='2017-03-03', site='Van 1', lines=lines)
    if position:
        payload['count_position'] = position
    return run('invoice post', payload, reason="The day's jobs")


def _record(run, count, account, **counted):
    run('inventory-count enter', dict(count=count['number'], entries=[
        dict(item=name, quantity=str(quantity)) for name, quantity in counted.items()]), reason='Counted')
    preview = run('inventory-count post', dict(count=count['number'], expected_version=count['version'],
                                               adjustment_account=account), reason='Van count', dry_run=True)
    return preview, run('inventory-count post', dict(count=count['number'], expected_version=count['version'],
                                                     adjustment_account=account,
                                                     preview_fingerprint=preview['preview_fingerprint']),
                        reason='Van count')


def _differences(result):
    return {row['item_name']: (row['difference'], row['value']['amount']) for row in result['differences']
            if row['difference'] != '0'}


def _shrinkage(books, account):
    return _net(books).get(account, 0)


def test_counted_at_four_jobs_entered_at_nine_leave_the_van_as_counted(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03'), reason='Four o\'clock count')
    assert [(line['item_name'], line['expected']) for line in count['lines']] == [
        ('Elbow', '4'), ('Valve', '2'), ('Widget', '5')]
    preview, recorded = _record(run, count, shrinkage, Widget=2, Elbow=2, Valve=2)
    # Before the day's paperwork the books still hold the morning's load.
    assert _differences(preview) == {'Widget': ('-3', '-6.00'), 'Elbow': ('-2', '-2.00')}
    assert recorded['status'] == 'recorded' and len(recorded['documents']) == 1
    assert _shrinkage(books, shrinkage) == 800
    # Nine o'clock: the jobs done before the count, default position.
    sale = _jobs(run, books, Widget=2, Elbow=1, Valve=1)
    assert any('derived again' in line for line in sale['warnings'])
    assert any('Placed before count C-1' in line for line in sale['warnings'])
    held = _held(books, '2017-03-03')
    assert (held[('Widget', 'Van 1')], held[('Elbow', 'Van 1')], held[('Valve', 'Van 1')]) == ('2', '2', '2')
    # Only the true shrinkage: a Widget (2.00) and an Elbow (1.00) lost, a Valve (5.00) found.
    assert _shrinkage(books, shrinkage) == 300 - 500
    shown = run('inventory-count show', dict(count='C-1'))
    assert {line['item_name']: line['difference'] for line in shown['lines']} == {
        'Widget': '-1', 'Elbow': '-1', 'Valve': '1'}
    # The recorded entry (Widget and Elbow) was corrected by revision; the Valve, which had no difference
    # when the count was recorded, posts in an entry of its own.
    assert [d['purpose'] for d in shown['documents']] == ['record', 'rederive']
    _tie_every_date(books)
    # The asset ties to the valuation.
    valuation = run('report inventory-valuation', dict(as_of='2017-12-31'))
    # 80.00 bought, 10.00 sold on the jobs (2 Widgets, an Elbow, a Valve), 3.00 lost and 5.00 found.
    assert valuation['totals']['asset_value']['minor_units'] == 8000 - 1000 - 300 + 500


def test_a_sale_stated_after_takes_the_van_below_the_count(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    _record(run, count, shrinkage, Widget=5)
    after = _jobs(run, books, position='after', Widget=7)
    assert not any('derived again' in line for line in after['warnings'])
    assert any('Placed after count C-1' in line for line in after['warnings'])
    assert _held(books, '2017-03-03')[('Widget', 'Van 1')] == '-2'
    assert run('inventory-count show', dict(count='C-1'))['lines'][0]['difference'] == '0'
    assert _shrinkage(books, shrinkage) == 0


def test_a_transfer_backdated_before_the_count_turns_missing_units_into_moved_ones(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03'), reason='Count')
    _, recorded = _record(run, count, shrinkage, Widget=5, Elbow=3, Valve=2)
    assert _differences(recorded) == {'Elbow': ('-1', '-1.00')}
    assert _shrinkage(books, shrinkage) == 100
    # The missing Elbow went back to the warehouse the day before; the paperwork comes in now.
    moved = run('inventory-transfer post', dict(date='2017-03-02', from_site='Van 1', to_site='Warehouse',
                                                lines=[dict(item='Elbow', quantity='1')]), reason='Unloaded')
    assert any('derived again' in line for line in moved['warnings'])
    assert _shrinkage(books, shrinkage) == 0
    held = _held(books, '2017-03-03')
    assert held[('Elbow', 'Van 1')] == '3' and held[('Elbow', 'Warehouse')] == '7'
    shown = run('inventory-count show', dict(count='C-1'))
    assert {line['item_name']: line['difference'] for line in shown['lines']}['Elbow'] == '0'
    # Nothing is left to post, so the count's entry is voided rather than emptied.
    assert run('journal show', dict(journal=shown['documents'][0]['transaction_id']))['status'] == 'voided'
    _tie_every_date(books)


def test_found_units_are_recosted_by_a_purchase_backdated_before_the_count(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Valve']), reason='Count')
    _, recorded = _record(run, count, shrinkage, Valve=3)
    assert _differences(recorded) == {'Valve': ('1', '5.00')}
    # Six more valves at 8.00, bought on the first and billed late: 12 worth 78.00, 6.50 each at the count.
    bill = run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=van['items']['Valve'], quantity='6', unit_cost='8.00')]), reason='Late bill')
    assert bill
    assert run('inventory-count show', dict(count='C-1'))['lines'][0]['difference'] == '1'
    assert _shrinkage(books, shrinkage) == -650
    valuation = run('report inventory-valuation', dict(as_of='2017-03-03'))
    valve = next(row for row in valuation['rows'] if row['item_name'] == 'Valve')
    assert valve['asset_value']['amount'] == '84.50' and valve['quantity_on_hand'] == '13'


def test_a_blind_sheet_never_shows_expected(van):
    run = van['run']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', blind=True), reason='Blind count')
    assert all(line['expected'] is None and line['difference'] is None for line in count['lines'])
    entered = run('inventory-count enter', dict(count='C-1', entries=[dict(item='Widget', quantity='4')]),
                  reason='Counted')
    assert all(line['expected'] is None and line['difference'] is None
               for line in entered['entered'] + entered['lines'] + entered['uncounted'])
    for name in ('inventory-count sheet', 'inventory-count show'):
        assert all(line['expected'] is None for line in run(name, dict(count='C-1'))['lines'])
    preview = run('inventory-count post', dict(count='C-1', expected_version=1,
                                               adjustment_account=van['shrinkage']), reason='Post', dry_run=True)
    assert _differences(preview) == {'Widget': ('-1', '-2.00')}


def test_two_counters_at_once_both_land_and_a_retry_lands_once(van):
    run = van['run']
    run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    first = dict(count='C-1', entries=[dict(item='Widget', quantity='2', mode='add')])
    run('inventory-count enter', first, reason='Front shelf', idempotency_key='counter-a-1')
    run('inventory-count enter', first, reason='Front shelf', idempotency_key='counter-a-1')   # a lost response
    run('inventory-count enter', dict(count='C-1', entries=[dict(item='Widget', quantity='3', mode='add')]),
        reason='Back shelf', idempotency_key='counter-b-1')
    shown = run('inventory-count show', dict(count='C-1'))
    assert shown['lines'][0]['counted'] == '5' and shown['lines'][0]['entries'] == 2
    # Two counters at the same moment, each on their own connection, five scans each.
    import os
    import threading
    import time
    import bookflow
    books, errors = van['books'], []

    def counter(name):
        try:
            client = bookflow.connect(data_root=os.environ['BOOKFLOW_DATA_ROOT'])
            for n in range(5):
                # One command runs on a data root at a time; a client told the root is busy tries again,
                # under the same idempotency key, so a retried scan still lands once.
                for _ in range(200):
                    try:
                        client.run('inventory-count enter', dict(count='C-1', entries=[
                            dict(item='Widget', quantity='1', mode='add')]), company=books['company'],
                            reason=f'{name} scan', idempotency_key=f'{name}-{n}')
                        break
                    except bookflow.BookflowError as busy:
                        if busy.code != 'E_DB_BUSY':
                            raise
                        time.sleep(0.05)
        except Exception as error:      # noqa: BLE001 - reported below
            errors.append(error)
    threads = [threading.Thread(target=counter, args=(name,)) for name in ('front', 'back')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    shown = run('inventory-count show', dict(count='C-1'))
    assert shown['lines'][0]['counted'] == '15' and shown['lines'][0]['entries'] == 12


def test_a_stale_preview_is_refused(van):
    run, shrinkage = van['run'], van['shrinkage']
    run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    run('inventory-count enter', dict(count='C-1', entries=[dict(item='Widget', quantity='4')]), reason='Counted')
    preview = run('inventory-count post', dict(count='C-1', expected_version=1, adjustment_account=shrinkage),
                  reason='Post', dry_run=True)
    with pytest.raises(BookflowError) as missing:
        run('inventory-count post', dict(count='C-1', expected_version=1, adjustment_account=shrinkage), reason='Post')
    assert missing.value.code == 'E_PREVIEW_STALE'
    run('inventory-count enter', dict(count='C-1', entries=[dict(item='Widget', quantity='3')]), reason='Recount')
    with pytest.raises(BookflowError) as stale:
        run('inventory-count post', dict(count='C-1', expected_version=1, adjustment_account=shrinkage,
                                         preview_fingerprint=preview['preview_fingerprint']), reason='Post')
    assert stale.value.code == 'E_PREVIEW_STALE' and stale.value.details['preview_fingerprint'] != preview['preview_fingerprint']


def test_void_reverses_the_differences_and_cancel_records_nothing(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    _record(run, count, shrinkage, Widget=1)
    assert _shrinkage(books, shrinkage) == 800
    voided = run('inventory-count void', dict(count='C-1', expected_version=2), reason='Miscounted')
    assert voided['status'] == 'voided' and _shrinkage(books, shrinkage) == 0
    assert _held(books, '2017-03-03')[('Widget', 'Van 1')] == '5'
    # A voided count is never derived again.
    _jobs(run, books, Widget=1)
    assert _shrinkage(books, shrinkage) == 0
    other = run('inventory-count create', dict(site='Van 1', date='2017-03-04', items=['Widget']), reason='Count')
    cancelled = run('inventory-count cancel', dict(count=other['number'], expected_version=1), reason='Not today')
    assert cancelled['status'] == 'cancelled' and cancelled['documents'] == []


def test_the_single_item_adjust_values_an_unpriced_increase_at_the_average(van):
    books, run = van['books'], van['run']
    found = run('inventory adjust', dict(item='Widget', date='2017-03-05', adjustment_account=van['shrinkage'],
                                         quantity_change='3'), reason='Found three')
    assert found['adjustment']['movement_kind'] == 'found'
    assert found['adjustment']['value_change']['amount'] == '6.00'
    assert found['adjustment']['lines'][0]['value_basis'] == 'average'
    stated = run('inventory adjust', dict(item='Widget', date='2017-03-05', adjustment_account=van['shrinkage'],
                                          quantity_change='2', value_change='10.00'), reason='Bought two')
    assert stated['adjustment']['movement_kind'] == 'receipt' and stated['adjustment']['value_change']['amount'] == '10.00'


def test_many_lines_with_new_quantity_and_new_value(van):
    books, run = van['books'], van['run']
    result = run('inventory adjust', dict(date='2017-03-05', adjustment_account=van['shrinkage'], lines=[
        dict(item='Widget', new_quantity='18', site='Warehouse'),        # 15 there: +3 found at 2.00
        dict(item='Elbow', new_quantity='1', site='Van 1'),              # 4 there: -3 at 1.00
        dict(item='Valve', new_value='40.00')]),                          # 6 worth 30.00: +10.00
        reason='Stocktake corrections')
    lines = result['adjustment']['lines']
    assert [(line['item_name'], line['quantity_change'], line['value_change']['amount'], line['movement_kind'])
            for line in lines] == [('Widget', '3', '6.00', 'found'), ('Elbow', '-3', '-3.00', 'issue'),
                                   ('Valve', '0', '10.00', 'value')]
    held = _held(books, '2017-03-05')
    assert held[('Widget', 'Warehouse')] == '18' and held[('Elbow', 'Van 1')] == '1'
    voided = run('inventory void', dict(adjustment=result['id']), reason='Undo')
    assert voided['adjustment']['movement_kind'] == 'reversal'
    assert _held(books, '2017-03-05')[('Widget', 'Warehouse')] == '15'
    _tie_every_date(books)


def test_found_replay_cases():
    base = dict(currency='USD', corrects_movement_id=None, reverses_movement_id=None, returns_movement_id=None,
                filled_by_movement_id=None, fallback_unit_cost_minor_units=None)
    seq = iter(range(1, 100))

    def row(kind, date, quantity, value=0, **extra):
        return dict(base, id=f'M{next(seq):02d}', kind=kind, effective_date=date, sequence=int(f'{next(seq)}'),
                    quantity_microunits=quantity * 10 ** 6, value_minor_units=value, **extra)
    # On hand: ten worth 100.00, two found are worth 20.00.
    receipt = row('receipt', '2017-01-01', 10, 10000)
    found = row('found', '2017-01-03', 2, -1)
    state = replay([receipt, found])
    assert state.targets[found['id']] == 2000 and state.found[0].basis == 'average'
    # At zero with a last average: all ten sold, three found at the 10.00 the item last had.
    sold = row('issue', '2017-01-02', -10, -10000)
    state = replay([receipt, sold, found])
    assert state.targets[found['id']] == 2000
    # Never stocked: the purchase cost captured on the row.
    never = row('found', '2017-01-03', 2, -1, fallback_unit_cost_minor_units=700)
    assert replay([never]).targets[never['id']] == 1400 and replay([never]).found[0].basis == 'purchase_cost'
    # No basis at all: zero, and the basis says so.
    bare = row('found', '2017-01-03', 2, -1)
    assert replay([bare]).targets[bare['id']] == 0 and replay([bare]).found[0].basis == 'none'
    # A purchase backdated in front of it recosts it: 20 worth 400.00 at the count, so 2 are worth 40.00.
    posted = dict(found, value_minor_units=2000)
    late = row('receipt', '2017-01-02', 10, 30000)
    state = replay([receipt, posted, late])
    assert state.targets[found['id']] == 4000
    assert [(c.target_movement['id'], c.delta_minor_units) for c in state.corrections] == [(found['id'], 2000)]


def test_replay_orders_a_day_before_the_count_then_the_count_then_after():
    from bookflow.company.inventory_costing import ordered
    rows = [dict(id='A', effective_date='2017-01-01', sequence=3, count_position='after'),
            dict(id='B', effective_date='2017-01-01', sequence=2, count_position='count'),
            dict(id='C', effective_date='2017-01-01', sequence=9, count_position='before'),
            dict(id='D', effective_date='2017-01-01', sequence=1)]
    assert [row['id'] for row in ordered(rows)] == ['D', 'C', 'B', 'A']


def test_the_worksheet_and_the_valuation_detail_read_the_count(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    sheet = run('report physical-inventory-worksheet', dict(as_of='2017-03-03', site='Van 1'))
    assert [(row['item_name'], row['quantity_on_hand'], row['physical_count']) for row in sheet['rows']] == [
        ('Elbow', '4', None), ('Valve', '2', None), ('Widget', '5', None)]
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Valve']), reason='Count')
    _record(run, count, shrinkage, Valve=3)
    _jobs(run, books, position='after', Valve=1)
    detail = run('report inventory-valuation-detail', dict(date_from='2017-03-01', date_to='2017-03-31', item='Valve'))
    rows = [(row['kind'], row['movement_kind'], row['count_position'], row['quantity_change'],
             row['value_change']['amount'] if row['value_change'] else None, row['quantity_on_hand'],
             row['asset_value']['amount']) for row in detail['rows']]
    # Bought six at 5.00, one found on the count at the 5.00 average, one sold after the count at 5.00.
    assert rows == [('opening', None, None, None, None, '0', '0.00'),
                    ('movement', 'receipt', 'before', '6', '30.00', '6', '30.00'),
                    ('movement', 'found', 'count', '1', '5.00', '7', '35.00'),
                    ('movement', 'issue', 'after', '-1', '-5.00', '6', '30.00'),
                    ('closing', None, None, None, None, '6', '30.00')]
    assert detail['totals']['asset_value']['amount'] == '30.00'


def test_a_long_count_posts_in_several_entries_and_a_late_sale_corrects_only_its_own(van, monkeypatch):
    """A journal entry holds at most 200 lines, so a count posts at most ITEMS_PER_ENTRY items per entry
    (99 in use; one here, so three items make three entries)."""
    from bookflow.company import inventory_counts
    monkeypatch.setattr(inventory_counts, 'ITEMS_PER_ENTRY', 1)
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03'), reason='Count')
    _, recorded = _record(run, count, shrinkage, Widget=4, Elbow=3, Valve=1)
    entries = recorded['documents']
    assert len(entries) == 3
    versions = {d['transaction_id']: run('journal show', dict(journal=d['transaction_id']))['version'] for d in entries}
    _jobs(run, books, Widget=1)
    after = {d['transaction_id']: run('journal show', dict(journal=d['transaction_id']))['version'] for d in entries}
    assert sum(after[key] != versions[key] for key in versions) == 1          # only the Widget entry was corrected
    assert _held(books, '2017-03-03')[('Widget', 'Van 1')] == '4'
    assert _shrinkage(books, shrinkage) == 100 + 500                           # an Elbow and a Valve; the Widget was sold
