"""co0091 adds one nullable fraction column beside each stored unit price or cost, and nothing else (R150).

Every existing price is whole minor units, so NULL is the true value for every existing row: the
upgrade rebuilds nothing, and a company's whole-cent documents read byte-identical afterwards.
(The same was checked once against books made by the base checkout itself and opened with this
code: every whole-cent invoice, bill, estimate, purchase order, item and price level showed
identically, and every stored row matched.)
"""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from bookflow.company import schema as c
from bookflow.company.unit_price_schema import fraction_check_sql
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_bill_item_lines import books  # noqa: F401  (fixture)
from tests.test_estimate_void_migration import _chain

pytestmark = pytest.mark.timeout(900)
M = importlib.import_module('bookflow.storage.company_migrations.versions.0091_subcent_unit_prices')


def test_the_frozen_columns_are_what_the_metadata_declares():
    assert M.revision == 'co0091' and M.revision in _chain() and HEADS['company'] == 'co0091'
    declared = {(table.name, column.name) for table in c.metadata.tables.values()
                for column in table.columns if column.name.endswith('_fraction')}
    assert declared == set(M.FRACTIONS)
    for (table, name), statement in zip(M.FRACTIONS, M.COLUMNS, strict=True):
        assert statement == (f'ALTER TABLE {table} ADD COLUMN {name} BIGINT CONSTRAINT ck_{name} '
                             f'CHECK ({fraction_check_sql(name)})')


def test_a_fresh_database_reaches_the_head_and_a_fraction_holds_its_range(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before, 'reaching the head twice must change nothing'
    with sqlite3.connect(path) as raw:
        check = lambda value: raw.execute(f'SELECT {fraction_check_sql("?")}'.replace('?', str(value))).fetchone()[0]  # noqa: E731
        assert [check(v) for v in ('NULL', 1, -500, 500)] == [1, 1, 1, 1]
        assert [check(v) for v in (0, 501, -501, 1.5)] == [0, 0, 0, 0]


def _shown(run, docs):
    shown = {noun: run(f'{noun} show', {selector: identity}) for noun, (selector, identity) in docs.items()}
    for value in shown.values():
        value.pop('links_audit_watermark', None)  # the upgrade's own audit event moves it
    return json.dumps(shown, sort_keys=True)


def _rows(path):
    with sqlite3.connect(path) as raw:
        found = {}
        for table, _ in M.FRACTIONS:
            columns = [row[1] for row in raw.execute(f'PRAGMA table_info({table})') if not row[1].endswith('_fraction')]
            found[table] = raw.execute(f'SELECT {", ".join(columns)} FROM {table} ORDER BY rowid').fetchall()
        return found


def test_whole_cent_books_read_byte_identical_after_the_upgrade(books):  # noqa: F811
    run, client, co = books['run'], books['client'], books['company']
    code = next(row['id'] for row in run('sales-tax-code query', {'limit': 50})['items'] if not row['taxable'])
    item = run('item create', dict(name='Valve', type='non_inventory_part', sales_enabled=True, description='Valve',
                                   price='12.50', cost='7.33', sales_tax_code_id=code, income_account_id=books['income'],
                                   purchase_enabled=True, purchase_description='Valve',
                                   expense_account_id=books['supplies']))['id']
    docs = dict(
        invoice=('invoice', run('invoice post', dict(customer=books['customer'], date='2017-03-02', lines=[
            dict(item=item, quantity='3'), dict(item=item, quantity='1.5', unit_price='7.33')]), reason='Sell')['id']),
        bill=('bill', run('bill post', dict(vendor=books['vendor'], date='2017-03-02', items=[
            dict(item=item, quantity='4'), dict(item=books['valve'], quantity='12', unit_cost='3.10')]), reason='Buy')['id']),
        estimate=('estimate', run('estimate create', dict(customer=books['customer'], date='2017-03-02', title='Job',
                                                          lines=[dict(item=item, quantity='2', estimated_unit_cost='7.33')]),
                                  reason='Quote')['id']),
        item=('item', item))
    docs['purchase-order'] = ('purchase_order', run('purchase-order post', dict(vendor=books['vendor'], date='2017-03-02',
                                                    lines=[dict(item=books['valve'], quantity='5', rate='3.10')]),
                                                    reason='Order')['id'])
    path = Path(client.company.show(company=co)['path']) / 'company.db'
    shown, rows = _shown(run, docs), _rows(path)
    with sqlite3.connect(path) as raw:
        assert all(raw.execute(f'SELECT count(*) FROM {t} WHERE {n} IS NOT NULL').fetchone()[0] == 0
                   for t, n in M.FRACTIONS), 'a whole-cent price stores no fraction'
        for table, name in M.FRACTIONS:  # what the base revision's database is
            raw.execute(f'ALTER TABLE {table} DROP COLUMN {name}')
        raw.execute("UPDATE alembic_version SET version_num = 'co0084'")
    assert client.run('upgrade', {})['companies_migrated'] == [co]
    assert _shown(run, docs) == shown
    assert _rows(path) == rows
    invoice = run('invoice show', {'invoice': docs['invoice'][1]})
    edited = run('invoice update', dict(invoice=docs['invoice'][1], expected_version=invoice['version'],
                                        memo='After the upgrade'), reason='Memo')
    assert [line['net'] for line in edited['revision']['lines']] == [line['net'] for line in invoice['revision']['lines']]
    # A whole-cent price raised below the cent after the upgrade is a real change: saved, versioned, exact.
    before = run('item show', {'item': item})
    run('item update', dict(item=item, expected_version=before['version'], price='12.5025'), reason='Reprice')
    after = run('item show', {'item': item})
    assert after['price']['amount'] == '12.5025' and after['version'] == before['version'] + 1
