"""Native count batch reversal restores the hand-worked stock and refuses later dependents."""
from pathlib import Path
import os

import pytest
from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_counts import van, _record, _jobs  # noqa: F401
from tests.test_inventory_sites import _held


def _count(van, quantity=2):
    count = van['run']('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    _, posted = _record(van['run'], count, van['shrinkage'], Widget=quantity)
    assert posted['batch_id'] == count['batch_id']
    return posted


def test_posted_count_reversal_restores_stock_and_valuation(van):
    run, books = van['run'], van['books']
    before = _net(books)
    count = _count(van)
    # 20 Widgets at $2, ten Elbows at $1, six Valves at $5 = $80.
    # Finding two of five Widgets in the van loses three at $2 = $6.
    assert run('report inventory-valuation', {'as_of': '2017-12-31'})['totals']['asset_value']['minor_units'] == 7400
    preview = run('batch reverse', {'batch': count['batch_id']}, dry_run=True, reason='Wrong count')
    assert len(preview['steps']) == 1
    step = preview['steps'][0]
    assert step['operation'] == 'inventory-count void'
    assert step['record_id'] == count['id']
    assert step['documents'] == [dict(transaction_id=count['documents'][0]['transaction_id'],
                                     number=count['documents'][0]['number'], action='void differences')]
    assert step['items'] == [dict(item_id=van['items']['Widget'], counted=2000000)]
    assert _held(books, '2017-03-03')[('Widget', 'Van 1')] == '2'
    assert run('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')['state'] == 'reversed'
    assert _held(books, '2017-03-03')[('Widget', 'Van 1')] == '5'
    assert run('report inventory-valuation', {'as_of': '2017-12-31'})['totals']['asset_value']['minor_units'] == 8000
    assert _net(books) == before
    ended = run('inventory-count show', {'count': count['id']})
    assert ended['status'] == 'voided' and ended['next'] is None


def test_open_count_cancels_as_one_unit(van):
    run = van['run']
    count = run('inventory-count create', dict(site='Van 1', items=['Widget']), reason='Count')
    run('inventory-count enter', dict(count=count['id'], entries=[dict(item='Widget', quantity='4')]), reason='Count')
    plan = run('batch reverse', {'batch': count['batch_id']}, dry_run=True, reason='Wrong count')
    assert plan['steps'][0]['operation'] == 'inventory-count cancel'
    run('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')
    ended = run('inventory-count show', {'count': count['id']})
    assert ended['status'] == 'cancelled' and ended['next'] is None


@pytest.mark.parametrize('quantity', [2, 5])
def test_later_sale_blocks_even_zero_variance_count(van, quantity):
    run = van['run']
    count = _count(van, quantity)
    sale = _jobs(run, van['books'], position='after', Widget=1)
    before = _net(van['books'])
    for dry in (True, False):
        with pytest.raises(BookflowError) as err:
            run('batch reverse', {'batch': count['batch_id']}, dry_run=dry, reason='Wrong count')
        assert err.value.code == 'E_BATCH_BLOCKED'
        assert sale['id'] in str(err.value.details)
        assert 'Widget' in str(err.value.details)
    assert _net(van['books']) == before


def test_later_count_blocks_then_unwinds(van):
    run = van['run']
    count = _count(van)
    later = run('inventory-count create', dict(site='Van 1', date='2017-03-04', items=['Widget']), reason='Next count')
    _record(run, later, van['shrinkage'], Widget=1)
    with pytest.raises(BookflowError) as err:
        run('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')
    assert err.value.code == 'E_BATCH_BLOCKED'
    assert later['number'] in str(err.value.details)
    run('batch reverse', {'batch': later['batch_id']}, reason='Wrong count')
    run('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')
    assert _held(van['books'], '2017-03-04')[('Widget', 'Van 1')] == '5'


def test_inverse_permission_is_required(van):
    from tests.conftest import as_user, make_actor
    count = _count(van)
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    make_actor(root, 'count_reader')
    van['books']['client'].run('membership grant', dict(user='count_reader', company=van['books']['company'],
        role='readonly', expected_version=0))
    actor = as_user(root, 'count_reader')
    with pytest.raises(BookflowError) as err:
        actor.run('batch reverse', {'batch': count['batch_id']}, company=van['books']['company'], reason='Wrong count')
    assert err.value.code == 'E_BATCH_BLOCKED'
    assert any(b['code'] == 'E_PERMISSION' for b in err.value.details['blockers'])


def test_effect_failure_rolls_back_sheet(van, monkeypatch):
    from bookflow.company import batches
    def fail(*args, **kwargs):
        raise RuntimeError('effect failed')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect failed'):
        van['run']('inventory-count create', dict(site='Van 1', items=['Widget']), reason='Count')
    assert van['run']('inventory-count query', {})['counts'] == []


def test_count_reversal_resumes_after_inverse_committed(van, monkeypatch):
    from bookflow.company import batches
    count = _count(van)
    finish = batches._finish
    def fail(*args, **kwargs):
        raise RuntimeError('interrupted before finish')
    monkeypatch.setattr(batches, '_finish', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        van['run']('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')
    assert van['run']('inventory-count show', {'count': count['id']})['status'] == 'voided'
    monkeypatch.setattr(batches, '_finish', finish)
    result = van['run']('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')
    assert result['state'] == 'reversed' and result['reversed_now'] == 0


def test_count_post_effect_failure_rolls_back_stock(van, monkeypatch):
    from bookflow.company import batches
    run = van['run']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    run('inventory-count enter', dict(count=count['id'], entries=[dict(item='Widget', quantity='2')]), reason='Count')
    payload = dict(count=count['id'], expected_version=count['version'], adjustment_account=van['shrinkage'])
    preview = run('inventory-count post', payload, dry_run=True, reason='Count')
    def fail(*args, **kwargs):
        raise RuntimeError('effect failed')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect failed'):
        run('inventory-count post', dict(payload, preview_fingerprint=preview['preview_fingerprint']), reason='Count')
    assert run('inventory-count show', {'count': count['id']})['status'] == 'open'
    assert _held(van['books'], '2017-03-03')[('Widget', 'Van 1')] == '5'
    assert run('report inventory-valuation', {'as_of': '2017-12-31'})['totals']['asset_value']['minor_units'] == 8000


def test_later_transfer_blocks_count(van):
    count = _count(van)
    van['run']('inventory-transfer post', dict(date='2017-03-04', from_site='Van 1', to_site='Warehouse',
        lines=[dict(item='Widget', quantity='1')]), reason='Unload')
    with pytest.raises(BookflowError) as err:
        van['run']('batch reverse', {'batch': count['batch_id']}, reason='Wrong count')
    assert err.value.code == 'E_BATCH_BLOCKED'
    assert 'transfer' in str(err.value.details) and 'Widget' in str(err.value.details)


def test_open_count_fingerprint_changes_when_entries_change(van):
    run = van['run']
    count = run('inventory-count create', dict(site='Van 1', items=['Widget']), reason='Count')
    plan = run('batch reverse', {'batch': count['batch_id']}, dry_run=True, reason='Wrong count')
    run('inventory-count enter', dict(count=count['id'], entries=[dict(item='Widget', quantity='4')]), reason='Count')
    with pytest.raises(BookflowError) as err:
        run('batch reverse', {'batch': count['batch_id'], 'fingerprint': plan['fingerprint']}, reason='Wrong count')
    assert err.value.code == 'E_VERSION_CONFLICT'
