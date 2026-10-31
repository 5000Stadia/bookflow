"""R186 item batch before-images, atomic blockers, resumable inverses and caller authority."""
import os
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import batches
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_bins_cycle_counts import _part
from tests.test_inventory_sites import _db


def _setup(books, kind):
    run = books['run']
    ids = [_part(books, name) for name in ('Batch Elbow', 'Batch Tee')]
    if kind == 'cycle-count classify':
        run('bill post', {'vendor': books['vendor'], 'date': '2017-03-01',
                          'items': [{'item': i, 'quantity': '10', 'unit_cost': '2.00'} for i in ids]})
        run('invoice post', {'customer': books['customer'], 'date': '2017-03-02',
                             'lines': [{'item': ids[0], 'quantity': '2'}]})
        payload = {'date_from': '2017-01-01', 'date_to': '2017-12-31'}
    else:
        payload = {'items': ids}
    return ids, payload


def _fields(books):
    with sqlite3.connect(_db(books)) as db:
        return db.execute('SELECT id, barcode, cycle_class_id, description FROM items ORDER BY id').fetchall()


def _snapshot(books):
    with sqlite3.connect(_db(books)) as db:
        return tuple(db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                     for table in ('items', 'cycle_classes', 'batches', 'batch_effects', 'audit_events', 'sequences'))


@pytest.mark.parametrize('kind', ['barcode generate', 'cycle-count classify'])
def test_restores_fields_exact_plan_and_resumes_interruption(books, monkeypatch, kind):
    ids, payload = _setup(books, kind)
    before = _fields(books)
    made = books['run'](kind, payload)
    batch = made['batch_id']
    assert batch in made['next'] and 'batch reverse' in made['next']
    stable = _snapshot(books)
    preview = books['run']('batch reverse', {'batch': batch}, dry_run=True)
    assert _snapshot(books) == stable
    expected = [('item', ids[1], ['item update']), ('item', ids[0], ['item update'])]
    if kind == 'cycle-count classify':
        with sqlite3.connect(_db(books)) as db:
            seeded = dict(db.execute('SELECT name, id FROM cycle_classes'))
        expected += [('cycle_class', seeded[name], ['cycle-class update', 'cycle-class deactivate'])
                     for name in ('C', 'B', 'A')]
    assert [(s['record_type'], s['record_id'], s['steps']) for s in preview['steps']] == expected
    real = batches.run_nested
    killed = []
    def interrupt(s, ctx, name, body, **kw):
        out = real(s, ctx, name, body, **kw)
        if name == 'item update' and not kw.get('dry_run') and not killed:
            killed.append(body['item'])
            raise KeyboardInterrupt('interrupted after durable inverse')
        return out
    monkeypatch.setattr(batches, 'run_nested', interrupt)
    with pytest.raises(KeyboardInterrupt):
        books['run']('batch reverse', {'batch': batch})
    monkeypatch.setattr(batches, 'run_nested', real)
    done = books['run']('batch reverse', {'batch': batch})
    assert done['state'] == 'reversed'
    assert _fields(books) == before
    with sqlite3.connect(_db(books)) as db:
        assert db.execute("SELECT count(*) FROM batch_effects WHERE batch_id=? AND record_type='item'",
                          (done['reversal_batch'],)).fetchone()[0] == 2
        if kind == 'cycle-count classify':
            assert db.execute('SELECT count(*) FROM cycle_classes WHERE active=1').fetchone()[0] == 0
    if kind == 'cycle-count classify':
        corrected = books['run'](kind, payload)
        assert corrected['changes'] == 2 and corrected['batch_id'] != batch


@pytest.mark.parametrize('kind', ['barcode generate', 'cycle-count classify'])
def test_later_item_edit_names_blocker_and_changes_nothing(books, kind):
    ids, payload = _setup(books, kind)
    batch = books['run'](kind, payload)['batch_id']
    change = {'barcode': 'RELABELED'} if kind == 'barcode generate' else {'cycle_class': 'B'}
    books['run']('item update', {'item': ids[0], **change})
    before = _snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            books['run']('batch reverse', {'batch': batch}, dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert 'Batch Elbow' in str(caught.value.details)
        assert _snapshot(books) == before


def test_seeded_class_used_by_another_item_blocks_whole_unit(books):
    ids, payload = _setup(books, 'cycle-count classify')
    batch = books['run']('cycle-count classify', payload)['batch_id']
    later = _part(books, 'Later Part')
    books['run']('item update', {'item': later, 'cycle_class': 'A'})
    before = _snapshot(books)
    with pytest.raises(BookflowError) as caught:
        books['run']('batch reverse', {'batch': batch})
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert 'cycle-class A' in str(caught.value.details)
    assert _snapshot(books) == before


@pytest.mark.parametrize('kind', ['barcode generate', 'cycle-count classify'])
def test_effect_recording_failure_rolls_back_writes(books, monkeypatch, kind):
    _, payload = _setup(books, kind)
    before = _snapshot(books)
    real = batches.record_effect
    def fail(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError('effect failure')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect failure'):
        books['run'](kind, payload)
    assert _snapshot(books) == before


@pytest.mark.parametrize('kind', ['barcode generate', 'cycle-count classify'])
def test_readonly_reverser_cannot_run_item_inverse(books, kind):
    from tests.conftest import as_user, make_actor
    _, payload = _setup(books, kind)
    batch = books['run'](kind, payload)['batch_id']
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    make_actor(root, 'reader', company_role=(books['company'], 'readonly'))
    reader = as_user(root, 'reader')
    before = _snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reader.run('batch reverse', {'batch': batch}, company=books['company'], dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any(b['code'] == 'E_PERMISSION' for b in caught.value.details['blockers'])
        assert _snapshot(books) == before


def test_classification_restores_existing_class_ids_and_retains_existing_classes(books):
    ids, payload = _setup(books, 'cycle-count classify')
    for item in ids:
        books['run']('item update', {'item': item, 'cycle_class': 'B'})
    before = _fields(books)
    with sqlite3.connect(_db(books)) as db:
        classes_before = db.execute('SELECT * FROM cycle_classes ORDER BY id').fetchall()
    batch = books['run']('cycle-count classify', payload)['batch_id']
    preview = books['run']('batch reverse', {'batch': batch}, dry_run=True)
    assert len(preview['steps']) == 2
    assert all(s['record_type'] == 'item' for s in preview['steps'])
    books['run']('batch reverse', {'batch': batch})
    assert _fields(books) == before
    with sqlite3.connect(_db(books)) as db:
        assert db.execute('SELECT * FROM cycle_classes ORDER BY id').fetchall() == classes_before


def test_later_classification_reverses_before_earlier_classification(books):
    _, payload = _setup(books, 'cycle-count classify')
    before = _fields(books)
    first = books['run']('cycle-count classify', payload)['batch_id']
    second = books['run']('cycle-count classify', {**payload, 'classes': ['B', 'A', 'C']})['batch_id']
    stable = _snapshot(books)
    with pytest.raises(BookflowError) as caught:
        books['run']('batch reverse', {'batch': first})
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert second in str(caught.value.details)
    assert _snapshot(books) == stable
    books['run']('batch reverse', {'batch': second})
    books['run']('batch reverse', {'batch': first})
    assert _fields(books) == before


def test_resumed_class_retirement_refuses_item_reassigned_after_inverse(books):
    ids, payload = _setup(books, 'cycle-count classify')
    batch = books['run']('cycle-count classify', payload)['batch_id']
    partial = books['run']('batch reverse', {'batch': batch, 'limit': 2})
    assert partial['state'] == 'reversing'
    books['run']('item update', {'item': ids[0], 'cycle_class': 'A'})
    stable = _snapshot(books)
    with pytest.raises(BookflowError) as caught:
        books['run']('batch reverse', {'batch': batch})
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert 'Batch Elbow' in str(caught.value.details)
    assert _snapshot(books) == stable
