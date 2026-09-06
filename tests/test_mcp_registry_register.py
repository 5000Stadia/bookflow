"""Register accounting and correction preserve the same command contract."""
from copy import deepcopy
import re
import sqlite3
import anyio
import pytest
from bookflow.core import registry
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import company_snapshot, GHOST
from tests.test_row8_register import register_accounts

COMMANDS = frozenset('register '+verb for verb in ('calculate', 'post', 'query', 'update'))


@pytest.mark.timeout(240)
def test_register_calculate_post_correct_query_and_rejection_parity(root, tmp_path, register_accounts):
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}
                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    before = company_snapshot(matrix.roots[surface]) if ctx.get('rejected') else None
                    out = await matrix.call(surface, name, raw, **ctx)
                    if before is not None:
                        assert company_snapshot(matrix.roots[surface]) == before
                    return out
                bank, expense = register_accounts
                calculation = dict(account=bank, direction='decrease', allocations=[dict(account=expense, amount='100.00')])
                await call('register calculate', calculation)
                raw = dict(account=bank, date='2026-02-12', direction='decrease', amount='100.00', category=expense)
                assert (await call('register post', raw, dry_run=True))['dry_run']
                posted = await call('register post', raw, idempotency_key='register-first')
                replay = await call('register post', raw, idempotency_key='register-first')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                selected, category = posted['revision']['lines']
                update = {**raw, 'amount': '125.00', 'journal': posted['id'], 'expected_version': 1,
                          'selected_line_id': selected['line_id'], 'category_line_id': category['line_id'], 'memo': 'Corrected register amount'}
                assert (await call('register update', update, dry_run=True))['dry_run']
                corrected = await call('register update', update)
                assert corrected['version'] == 2
                assert (await call('register update', update, rejected=True))['code'] == 'E_VERSION_CONFLICT'
                await call('register query', dict(account=bank, date_from='2026-01-01', date_to='2026-12-31', limit=200))
                assert set(calls) == COMMANDS
                for name,data in list(calls.items()):
                    assert (await call(name, data, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    if not registry.get(name).is_write:
                        assert (await call(name, data, dry_run=True, rejected=True))['code'] == 'E_USAGE'
                path = matrix.roots[surface]
                with sqlite3.connect((path/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((path/relative/'company.db').as_uri()+'?mode=ro', uri=True) as db:
                    batches = db.execute('SELECT sum(debit_minor_units),sum(credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY batch_id', (posted['id'],)).fetchall()
                    assert len(batches) == 3 and all(a == b for a,b in batches)
                    net = dict(db.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id', (posted['id'],)))
                    assert net == {bank:-12500, expense:12500}
                    assert db.execute("SELECT count(*) FROM audit_events WHERE reason='Registry parity'").fetchone() == (2,)
            expected = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli','http','mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for i,(a,b) in enumerate(zip(expected,actual)):
                    assert a == b,(surface,i,a,b)
        finally:
            await matrix.close()
    anyio.run(witness)
