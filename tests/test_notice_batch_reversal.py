"""Ready notice batches reverse atomically and release only their own period dedupe."""
import anyio
import pytest

from bookflow.core.errors import BookflowError
from tests.test_scheduling import books, _invoice, company_db, member


def generated(books, count=2):
    books.run('company update', {'overdue_notice_days': 1, 'overdue_notice_template': 'letter'})
    for i in range(count):
        name = f'Overdue customer {i}'
        books.run('customer create', {'name': name})
        _invoice(books, name, '2026-01-01', str(100 + i))
    return books.run('notice generate', {'as_of': '2026-03-05'})


def snapshot(books):
    with company_db(books) as db:
        return db.execute('SELECT * FROM overdue_notices ORDER BY id').fetchall()


def test_notice_batch_plan_restores_ready_queue_and_corrected_run_regenerates(books):
    before = books.run('notice list', {})['items']
    out = generated(books)
    batch = out['batch_id']
    assert out['next'] == f'batch reverse {batch}'
    original = {n['id']: n for n in out['generated']}
    plan = books.run('batch reverse', {'batch': batch}, dry_run=True)
    assert [(s['record_id'], s['steps']) for s in plan['steps']] == [
        (n['id'], ['notice reverse']) for n in reversed(out['generated'])]
    assert all(n['status'] == 'ready' for n in books.run('notice list', {})['items'])
    done = books.run('batch reverse', {'batch': batch})
    assert done['state'] == 'reversed'
    assert books.run('notice list', {})['items'] == before
    for notice in books.run('notice list', {'status': 'all'})['items']:
        prior = original[notice['id']]
        assert notice['status'] == 'dismissed' and notice['period_released']
        for field in ('customer_id', 'period_key', 'as_of', 'documents', 'overdue', 'balance', 'sent_at', 'sent_by', 'sent_method'):
            assert notice[field] == prior[field]
    books.run('company update', {'overdue_notice_template': 'statement'})
    corrected = books.run('notice generate', {'as_of': '2026-03-05'})
    assert all(n['template'] == 'statement' for n in corrected['generated'])
    assert corrected['generated_count'] == out['generated_count'] == 2
    assert corrected['already_for_period'] == 0
    assert {n['id'] for n in corrected['generated']}.isdisjoint(original)


@pytest.mark.parametrize('verb', ['mark-sent', 'dismiss'])
def test_changed_notice_blocks_whole_batch_without_mutation(books, verb):
    out = generated(books)
    notice = out['generated'][0]
    books.run(f'notice {verb}', {'notice': notice['id']})
    before = snapshot(books)
    with pytest.raises(BookflowError) as err:
        books.run('batch reverse', {'batch': out['batch_id']})
    assert err.value.code == 'E_BATCH_BLOCKED'
    assert notice['id'] in str(err.value.details)
    assert notice['customer_name'] in str(err.value.details)
    if verb == 'mark-sent':
        assert 'sent mail cannot be recalled' in str(err.value.details)
    assert snapshot(books) == before


def test_interrupted_notice_reversal_resumes_without_repeating(books, monkeypatch):
    from bookflow.company import notices
    out = generated(books, 3)
    adapter = notices._batches.ADAPTERS['notice-generate']
    from dataclasses import replace
    calls = 0
    def interrupt(s, ctx, batch, reversal_id, item):
        nonlocal calls
        if calls == 1:
            raise RuntimeError('interrupted')
        adapter.runner(s, ctx, batch, reversal_id, item)
        calls += 1
    monkeypatch.setitem(notices._batches.ADAPTERS, 'notice-generate', replace(adapter, runner=interrupt))
    with pytest.raises(RuntimeError, match='interrupted'):
        books.run('batch reverse', {'batch': out['batch_id']})
    assert len(books.run('notice list', {})['items']) == 2
    monkeypatch.setitem(notices._batches.ADAPTERS, 'notice-generate', adapter)
    done = books.run('batch reverse', {'batch': out['batch_id']})
    assert done['state'] == 'reversed' and done['reversed_now'] == 2
    assert [n['version'] for n in books.run('notice list', {'status': 'all'})['items']] == [2, 2, 2]


def test_notice_reversal_needs_inverse_write_grant(books):
    out = generated(books)
    _, call = member(books, 'notice_reader', role='readonly')
    before = snapshot(books)
    with pytest.raises(BookflowError) as err:
        call('batch reverse', {'batch': out['batch_id']})
    assert err.value.code == 'E_BATCH_BLOCKED'
    assert all(b['code'] == 'E_PERMISSION' for b in err.value.details['blockers'])
    assert snapshot(books) == before


def test_notice_generation_effect_failure_rolls_back_notices(books, monkeypatch):
    from bookflow.company import batches
    generated(books, 1)
    before = snapshot(books)
    def fail(*args, **kwargs):
        raise RuntimeError('effect failure')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect failure'):
        books.run('notice generate', {'as_of': '2026-04-05'})
    assert snapshot(books) == before


@pytest.mark.timeout(900)
def test_notice_reverse_through_python_cli_http_and_mcp(root, tmp_path):
    from tests.mcp_matrix_support import Matrix
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in ('python', 'cli', 'http', 'mcp'):
                async def call(name, raw, **kw):
                    return await matrix.call(surface, name, raw, **kw)
                await call('company update', {'overdue_notice_days': 1})
                out = await call('notice generate', {'as_of': '2099-06-15'})
                notice = out['generated'][0]
                preview = await call('notice reverse', {'notice': notice['id']}, dry_run=True)
                assert preview['status'] == 'dismissed' and preview['period_released']
                result = await call('notice reverse', {'notice': notice['id'], 'expected_version': 1})
                assert result['status'] == 'dismissed' and result['period_released']
                corrected = await call('notice generate', {'as_of': '2099-06-15'})
                assert corrected['generated_count'] == 1
                assert corrected['generated'][0]['customer_id'] == notice['customer_id']
        finally:
            await matrix.close()
    anyio.run(witness)


def test_live_notice_delivery_blocks_and_populated_co0127_upgrade_keeps_references(books):
    import importlib
    from pathlib import Path
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import migrate_to_head
    out = generated(books, 1)
    notice = out['generated'][0]
    message = books.run('delivery draft', {'kind': 'notice', 'document': notice['id'], 'to': ['books@example.com']})
    before = snapshot(books)
    shown = books.run('batch show', {'batch': out['batch_id']})['batch']
    assert not shown['reversible'] and message['number'] in shown['first_blocker']['message']
    with pytest.raises(BookflowError) as err:
        books.run('batch reverse', {'batch': out['batch_id']})
    assert err.value.code == 'E_BATCH_BLOCKED'
    assert message['number'] in str(err.value.details)
    assert snapshot(books) == before
    path = Path(books.run('company show', {})['path']) / 'company.db'
    frozen = importlib.import_module('bookflow.storage.company_migrations.versions.0085_scheduling')
    old_ddl = next(ddl for ddl in frozen.DDL if 'CREATE TABLE overdue_notices' in ddl)
    # Recreate the actual co0127 table definition, preserving a notice with a live inbound delivery FK.
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        columns = ', '.join(row[1] for row in db.execute('PRAGMA table_info(overdue_notices)') if row[1] != 'period_released')
        db.execute(old_ddl.replace('CREATE TABLE overdue_notices', 'CREATE TABLE overdue_notices_old'))
        db.execute(f'INSERT INTO overdue_notices_old ({columns}) SELECT {columns} FROM overdue_notices')
        db.execute('DROP TABLE overdue_notices')
        db.execute('ALTER TABLE overdue_notices_old RENAME TO overdue_notices')
        for ddl in frozen.INDEXES:
            if ' ON overdue_notices ' in ddl:
                db.execute(ddl)
        db.execute("UPDATE alembic_version SET version_num='co0127'")
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', None) == ('co0127', 'co0128')
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('SELECT period_released FROM overdue_notices WHERE id=?', (notice['id'],)).fetchone() == (0,)
    assert books.run('delivery show', {'message': message['id']})['id'] == message['id']
    assert snapshot(books) == before


def test_notice_reversal_migration_matches_metadata():
    import importlib
    from sqlalchemy.schema import CreateIndex, CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0128_notice_reversal')
    assert migration.DDL == str(CreateTable(c.overdue_notices).compile(dialect=dialect()))
    assert migration.INDEXES == tuple(str(CreateIndex(index).compile(dialect=dialect()))
                                      for index in sorted(c.overdue_notices.indexes, key=lambda i: i.name))
