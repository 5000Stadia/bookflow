"""Inbox copies across certification, period boundaries and permission changes."""
from pathlib import Path
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.test_bank_feeds import csv_file, run, table
from tests.test_reconciliation_adapters import account
from tests.test_service_sales_lifecycle import COMPANY


def _first_statement(client):
    bank = account(client, 'Inbox lifecycle bank')
    run(client, 'reconcile opening start', dict(operation_key=new_id(), account=bank,
        opening_date='2026-08-31', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='New account'), references=[]))
    run(client, 'journal post', dict(date='2026-09-30', memo='September bank fee', lines=[
        dict(account='Bank Fees', side='debit', amount='12.00'),
        dict(account=bank, side='credit', amount='12.00')]))
    imported = run(client, 'bank-feed import', dict(account=bank,
        content=csv_file([('2026-09-30', 'September bank fee', '-12.00')])))
    copied = run(client, 'reconcile import', dict(account=bank, from_inbox=True, start=True,
        statement_date='2026-09-30', ending_balance='-12.00'))
    draft = copied['draft']
    preview = run(client, 'reconcile preview', dict(draft=draft['id'], expected_version=draft['version']))
    assert preview['balanced'] and preview['totals']['difference'] == 0
    done = run(client, 'reconcile finish', dict(operation_key=new_id(), draft=draft['id'],
        expected_version=draft['version'], expected_facts_fingerprint=preview['expected_facts_fingerprint'],
        dependency_guard=preview['dependency_guard']))
    assert done['totals']['difference'] == 0
    return bank, imported, draft, done


def test_finished_statement_keeps_its_inbox_copy_blocker(client):
    bank, imported, draft, done = _first_statement(client)
    assert table(client, 'SELECT state FROM reconciliation_drafts WHERE id=?', draft['id']) == [('consumed',)]
    assert table(client, 'SELECT certificate_id FROM reconciliation_active_certificates WHERE account_id=?', bank) == [
        (done['certificate_id'],)]
    with pytest.raises(BookflowError) as caught:
        run(client, 'bank-feed reverse', dict(import_id=imported['import_id']))
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert any(b['code'] == 'statement_copy' and b['draft'] == draft['id']
               for b in caught.value.details['blockers'])
    assert table(client, 'SELECT live FROM bank_lines WHERE import_id=?', imported['import_id']) == [(1,)]


def test_next_inbox_statement_starts_after_previous_certificate_date(client):
    bank, imported, draft, done = _first_statement(client)
    # Include the closing day again and both ends of the next period. Only October belongs.
    run(client, 'bank-feed import', dict(account=bank, content=csv_file([
        ('2026-09-30', 'Another September item', '-1.23'),
        ('2026-10-01', 'First October item', '-2.34'),
        ('2026-10-31', 'Last October item', '-3.45'),
        ('2026-11-01', 'November item', '-4.56')])))
    request = dict(account=bank, from_inbox=True, statement_date='2026-10-31')
    review = run(client, 'reconcile import', request)
    assert [(v['date'], v['amount']) for v in review['lines']] == [
        ('2026-10-01', -234), ('2026-10-31', -345)]
    started = run(client, 'reconcile import', dict(request, start=True, ending_balance='-17.79'))
    assert started['draft']['base_head_id'] == done['certificate_id']
    assert [(v['line_id'], v['amount']) for v in started['lines']] == [
        (v['line_id'], v['amount']) for v in review['lines']]
    assert table(client, 'SELECT date FROM statement_lines WHERE draft_id=? ORDER BY date',
                 started['draft']['id']) == [('2026-10-01',), ('2026-10-31',)]


@pytest.mark.parametrize('dry_run', [False, True])
def test_bank_feed_denial_blocks_only_inbox_source(client, root, dry_run):
    from tests.conftest import as_user, make_actor
    content = csv_file([('2026-09-30', 'Private inbox detail', '-12.00')])
    run(client, 'bank-feed import', dict(account='Checking', content=content))
    make_actor(Path(root), 'reconcile-clerk')
    client.run('membership grant', dict(user='reconcile-clerk', company=COMPANY, role='standard',
                                        expected_version=0, denies=['bank-feed']))
    clerk = as_user(Path(root), 'reconcile-clerk')
    direct = run(clerk, 'reconcile import', dict(account='Checking', content=content), dry_run=dry_run)
    assert direct['counts']['lines'] == 1 and direct['lines'][0]['amount'] == -1200
    with pytest.raises(BookflowError) as caught:
        run(clerk, 'reconcile import', dict(account='Checking', from_inbox=True,
                                            statement_date='2026-09-30'), dry_run=dry_run)
    assert caught.value.code == 'E_PERMISSION'
    assert caught.value.details['capability'] == 'bank-feed'
    assert table(client, 'SELECT count(*) FROM statement_lines') == [(0,)]


def test_populated_statement_upgrade_retains_rows_and_append_only_guards(client, tmp_path):
    """Recreate the predecessor's frozen statement tables around real command-created facts."""
    import importlib
    from tests.test_reconcile_import import opening
    from tests.test_bank_feeds import sample
    from tests.test_row8_journal import database_path
    from tests.test_account_cash_flow_section_migration import _at

    opening(client)
    run(client, 'reconcile import', dict(account='Checking', content=sample('checking-2026-09.csv'),
        start=True, statement_date='2026-09-30', save_mapping='kept mapping'))
    prior = importlib.import_module('bookflow.storage.company_migrations.versions.0066_statement_imports')
    path = tmp_path / 'populated-co0120.db'
    with sqlite3.connect(database_path(client)) as source, sqlite3.connect(path) as raw:
        source.backup(raw)
        raw.execute('PRAGMA foreign_keys=OFF')
        names = ('statement_imports', 'statement_lines', 'statement_csv_mappings')
        before = {}
        for name in names:
            columns = [r[1] for r in raw.execute(f'PRAGMA table_info({name})') if r[1] != 'bank_line_id']
            before[name] = (columns, raw.execute(f'SELECT {",".join(columns)} FROM {name} ORDER BY rowid').fetchall())
        for name in reversed(names):
            raw.execute(f'DROP TABLE {name}')
        for ddl in prior.DDL:
            raw.execute(ddl)
        for name, (columns, rows) in before.items():
            raw.executemany(f'INSERT INTO {name} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', rows)
        for ddl in prior.TRIGGERS:
            raw.execute(ddl)
        raw.execute('CREATE INDEX local_statement_format ON statement_imports(format)')
        raw.execute('CREATE TRIGGER local_statement_insert AFTER INSERT ON statement_imports BEGIN SELECT 1; END')
        objects = raw.execute("SELECT name, sql FROM sqlite_schema WHERE tbl_name='statement_imports' "
                              "AND type IN ('index', 'trigger') ORDER BY name").fetchall()
        raw.execute("UPDATE alembic_version SET version_num='co0120'")
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
    _at(path, 'co0121')
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT name, sql FROM sqlite_schema WHERE tbl_name='statement_imports' "
                           "AND type IN ('index', 'trigger') ORDER BY name").fetchall() == objects
        for name, (columns, rows) in before.items():
            assert raw.execute(f'SELECT {",".join(columns)} FROM {name} ORDER BY rowid').fetchall() == rows
            for action in (f'UPDATE {name} SET {columns[0]}={columns[0]}', f'DELETE FROM {name}'):
                with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                    raw.execute(action)
        assert raw.execute('SELECT count(*), count(bank_line_id) FROM statement_lines').fetchone() == (10, 0)
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
