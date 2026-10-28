"""R107 increment 0: the batch ledger and its reversal (design/bulk-import.md §8a).

Done when a batch reverses whole, is refused with its blocker named (changing nothing), and resumes after a kill
between slices. Tests: ordering `strict` and `record`, edited-again, inverse refusals reported by name (in use, no
authority), atomicity, and no effect without its write.
"""
import dataclasses
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import batches
from tests.test_imports import active_customers, books, company_db, rows_file, run_all  # noqa: F401


def imported(books, n=6, start=1, source=None):
    raw = {'kind': 'customer', 'content': rows_file(n, start=start)}
    if source:
        raw['source'] = source
    preview = books.run('import preview', raw)
    run_all(books, preview['run']['id'])
    return preview['run']['id']


def snapshot(books):
    with company_db(books) as db:
        return (db.execute('SELECT id, version, active, full_name FROM customers ORDER BY id').fetchall(),
                db.execute('SELECT batch_id, seq FROM batch_effects ORDER BY 1, 2').fetchall(),
                db.execute('SELECT id, state FROM batches ORDER BY id').fetchall(),
                db.execute('SELECT id, released_by FROM import_keys ORDER BY id').fetchall())


def refusal(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def test_record_ordering_edited_again_and_in_use_are_named_and_change_nothing(books):
    first = imported(books, 4)
    second = imported(books, 3, start=50)
    # `record` ordering: two batches of different records reverse independently, oldest first if wished.
    books.run('customer update', {'customer': 'Customer 002', 'phone': '555'}, reason='Edited after the import')
    item = books.client.item.create(company=books.company, name='Labor', type='service', description='Labor',
                                    price='50.00',
                                    income_account_id=books.run('account show', {'account': 'Service Income'})['id'])['id']
    books.run('invoice post', {'customer': 'Customer 003', 'date': '2026-01-10',
                               'lines': [{'item': item, 'quantity': '1'}]}, reason='Sold')
    before = snapshot(books)
    error = refusal(lambda: books.run('import reverse', {'run': first}))
    assert error.code == 'E_BATCH_BLOCKED' and error.details['total'] == 2
    by_code = {b['code']: b for b in error.details['blockers']}
    assert set(by_code) == {'edited_again', 'in_use'}
    assert 'Customer 002' in by_code['edited_again']['message'] and by_code['edited_again']['changed_by']
    assert 'Customer 003' in by_code['in_use']['message'] and 'invoice 1 refer' in by_code['in_use']['message']
    assert all(b['clears'] for b in error.details['blockers'])
    assert snapshot(books) == before  # a blocked reversal changes nothing
    shown = books.run('batch query', {'kind': 'import'})['items']
    assert [(b['reversible'], (b['first_blocker'] or {}).get('code')) for b in shown] == [(True, None),
                                                                                          (False, 'in_use')]
    out = books.run('import reverse', {'run': second}, reason='Second file was wrong')
    assert out['state'] == 'reversed' and out['reversed_now'] == 3
    assert active_customers(books) == {'Customer 001', 'Customer 002', 'Customer 003', 'Customer 004'}


def test_strict_ordering_refuses_while_a_later_batch_in_the_scope_is_live(books, monkeypatch):
    adapter = batches.ADAPTERS['import']
    monkeypatch.setitem(batches.ADAPTERS, 'import', dataclasses.replace(adapter, ordering='strict'))
    first = imported(books, 2)
    second = imported(books, 2, start=10)
    error = refusal(lambda: books.run('import reverse', {'run': first}))
    assert error.code == 'E_BATCH_BLOCKED' and error.details['blockers'][0]['code'] == 'later_batch'
    later = books.run('import show', {'run': second})['run']['batch']['id']
    assert error.details['blockers'][0]['record_id'] == later
    books.run('import reverse', {'run': second})
    assert books.run('import reverse', {'run': first})['state'] == 'reversed'
    assert active_customers(books) == set()


def test_a_reversal_resumes_after_a_kill_between_slices_and_within_a_record(books, monkeypatch):
    run = imported(books, 7)
    out = books.run('import reverse', {'run': run, 'limit': 3}, reason='Wrong file')
    assert (out['state'], out['reversed_now'], out['remaining']) == ('reversing', 3, 4)
    assert out['next'] == f'import reverse {run}'
    assert len(active_customers(books)) == 4
    # Killed after the rename of the next record and before its deactivate.
    real = batches.run_nested

    def dying(s, ctx, name, payload, **kw):
        result = real(s, ctx, name, payload, **kw)
        if name == 'customer update' and not kw.get('dry_run'):
            raise KeyboardInterrupt('killed mid-record')
        return result
    monkeypatch.setattr(batches, 'run_nested', dying)
    with pytest.raises(KeyboardInterrupt):
        books.run('import reverse', {'run': run})
    monkeypatch.setattr(batches, 'run_nested', real)
    shown = books.run('import show', {'run': run})['run']['batch']
    assert shown['state'] == 'reversing'
    done = books.run('import reverse', {'run': run})
    assert done['state'] == 'reversed' and done['reversed_now'] == 4 and active_customers(books) == set()
    with company_db(books) as db:
        reversal = db.execute("SELECT id FROM batches WHERE kind = 'reversal'").fetchone()[0]
        steps = db.execute('SELECT record_id, count(*) FROM batch_effects WHERE batch_id = ? GROUP BY 1',
                           (reversal,)).fetchall()
        names = [r[0] for r in db.execute('SELECT name FROM customers')]
    assert len(steps) == 7 and all(n == 2 for _, n in steps)  # rename and deactivate, each once
    assert all(name.endswith(f'(reversed {done["batch"]["id"][-6:].lower()})') for name in names)


def test_no_effect_without_its_write_and_the_reverse(books, monkeypatch):
    preview = books.run('import preview', {'kind': 'customer', 'content': rows_file(3)})
    real = batches.record_effect
    calls = {'n': 0}

    def failing(s, batch_id, **kw):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('ledger write failed')
        return real(s, batch_id, **kw)
    monkeypatch.setattr(batches, 'record_effect', failing)
    with pytest.raises(Exception):  # noqa: B017 - whatever the failure, the transaction is what matters
        books.run('import run', {'run': preview['run']['id']})
    monkeypatch.setattr(batches, 'record_effect', real)
    # The second customer's write rolled back with its ledger rows: one customer, one key, one effect.
    assert active_customers(books) == {'Customer 001'}
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM import_keys').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM batch_effects').fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM audit_events WHERE command = 'customer create'").fetchone()[0] == 1
        guard = None
        try:
            db.execute('DELETE FROM batch_effects')
        except Exception as error:  # noqa: BLE001 - the guard's own refusal
            guard = str(error)
    assert guard and 'immutable' in guard
    assert run_all(books, preview['run']['id'])['counts'] == {'created': 3, 'not_run': 0}


def test_an_atomic_run_undoes_itself_at_the_first_refusal(books):
    books.run('term create', {'name': 'Net 7', 'kind': 'standard', 'due_days': 7})
    text = 'Name,Terms\nAlpha,Net 30\nBravo,Net 30\nCharlie,Net 7\nDelta,Net 30\n'
    preview = books.run('import preview', {'kind': 'customer', 'content': text, 'atomic': True})
    assert preview['counts'] == {'create': 4}
    books.run('term deactivate', {'term': 'Net 7'})  # the books change between preview and run
    out = books.run('import run', {'run': preview['run']['id']})
    assert out['run']['state'] == 'rolled_back' and out['run']['batch']['state'] == 'reversed'
    assert active_customers(books) == set() and 'undid the 2 record(s)' in out['notes'][-1]
    error = refusal(lambda: books.run('import run', {'run': preview['run']['id']}))
    assert error.code == 'E_VALIDATION'


def test_a_reversal_needs_the_callers_own_grants(books):
    from tests.conftest import as_user, make_actor
    run = imported(books, 2)
    make_actor(Path(books.root), 'clerk')
    books.client.run('membership grant', {'user': 'clerk', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0, 'denies': ['customer']})
    clerk = as_user(Path(books.root), 'clerk')
    error = refusal(lambda: clerk.run('import reverse', {'run': run}, company=books.company))
    # Refused before any plan: the batch's records are not the clerk's to read (nor to change).
    assert error.code == 'E_PERMISSION' and error.details['capability'] == 'customer'
    assert len(active_customers(books)) == 2


def test_co0105_ddl_is_the_metadata():
    import importlib
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable
    from bookflow.company import schema as c
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0105_batches_imports')
    assert m.DDL == tuple(str(CreateTable(c.metadata.tables[t]).compile(dialect=dialect()))
                          for t in reversed(m.TABLES))
    assert set(m.INDEXES) == {str(CreateIndex(i).compile(dialect=dialect())) for t in m.TABLES
                              for i in c.metadata.tables[t].indexes}
    paths = {str(CreateIndex(i).compile(dialect=dialect())) for t in c.metadata.tables.values()
             for i in t.indexes if i.name.endswith('_path')}
    assert set(m.PATH_INDEXES) == paths and len(paths) == 8


def test_a_run_stopped_part_way_and_reversed_is_never_continued(books):
    preview = books.run('import preview', {'kind': 'customer', 'content': rows_file(5)})
    books.run('import run', {'run': preview['run']['id'], 'limit': 2})
    out = books.run('import reverse', {'run': preview['run']['id']})
    assert out['state'] == 'reversed' and out['reversed_now'] == 2
    assert books.run('import show', {'run': preview['run']['id']})['run']['state'] == 'stopped'
    error = refusal(lambda: books.run('import run', {'run': preview['run']['id']}))
    assert error.code == 'E_VALIDATION' and error.details['batch_state'] == 'reversed'
    assert active_customers(books) == set()
