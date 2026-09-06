"""Payment drafts and preparation retain the same full four-interface contract."""
import base64
from copy import deepcopy
import json
import re
import sqlite3
import anyio
import pytest

from bookflow.core import registry
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted

FAMILIES = {
    'selection': ('payment selection create', 'payment selection update', 'payment selection clear',
                  'payment selection show', 'payment selection items', 'payment selection query'),
    'preparation': ('payment invoices', 'payment suggest', 'payment calculate'),
}
GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


def payment_operation_rows(root, company):
    """Retain every seeded operation field before exercising another surface."""
    with sqlite3.connect((root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        relative = db.execute('SELECT path FROM companies WHERE id=?', (company,)).fetchone()[0]
    with sqlite3.connect((root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {row['id']: dict(row) for row in db.execute('SELECT * FROM payment_operations')}


def normalize_payment(documents, root, baseline_ids):
    def visit(value, key=None):
        if isinstance(value, dict):
            return {k: visit(v, k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [visit(v) for v in value]
        if key == 'age_seconds' and value is not None:
            assert isinstance(value, (int, float)) and value >= 0
            return '<elapsed>'
        if key in {'canonical_hash', 'canonical_intent_hash', 'graph_digest', 'expected_facts_fingerprint', 'manifest_hash'} and value is not None:
            assert isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
            return '<verified-facts-digest>'
        if key == 'facts_snapshot' and isinstance(value, str):
            return visit(json.loads(value))
        if key == 'settlement_guard' and value is not None:
            body, signature = value.split('.')
            payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
            assert set(payload) == {'v', 'company_id', 'owner_type', 'owner_id', 'owner_version', 'baseline_audit_seq', 'issued_at', 'graph_digest'}
            assert len(base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))) == 32
            return visit(payload)
        if key == 'next_cursor' and value is not None:
            body, signature = value.split('.')
            payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
            assert payload['v'] == 1
            assert len(base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))) == 32
            # Each scenario consumes every cursor through its owning command.
            if 'offset' in payload:
                assert set(payload) == {'v', 'fp', 'offset'}
                assert type(payload['offset']) is int and payload['offset'] > 0
                return dict(version=payload['v'], offset=payload['offset'], facts_fingerprint=payload['fp'])
            assert set(payload) == {'v', 'company_id', 'original_command', 'canonical_intent_hash',
                                    'facts_fingerprint', 'kind', 'limit', 'last_logical_sort_key'}
            assert isinstance(payload['last_logical_sort_key'], list) and payload['last_logical_sort_key']
            return visit(payload)
        return value
    return normalize(visit(documents), root, baseline_ids)


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.timeout(300)
def test_payment_preparation_four_surface_documents_context_and_rejections(root, client, sale, tmp_path, family):
    invoices = [posted(client, sale['customer'], sale['item'], amount, number) for amount, number in
                [('1.00', 'MCP-PREP-ONE'), ('2.00', 'MCP-PREP-TWO')]]
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            operation_baselines = {surface: payment_operation_rows(path, matrix.company)
                                   for surface, path in matrix.roots.items()}
            for surface in matrix.documents:
                calls = {}
                async def call(name, data, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(data)
                    return await matrix.call(surface, name, data, **ctx)
                async def pages(name, data):
                    page = await call(name, data)
                    total, seen = page['total_count'], list(page['items'])
                    while page['next_cursor']:
                        page = await call(name, dict(data, cursor=page['next_cursor']))
                        seen.extend(page['items'])
                    assert len(seen) == total
                    return seen
                base = dict(mode='new_receipt', customer=sale['customer'], date='2026-06-01')
                if family == 'selection':
                    assert (await call('payment selection create', dict(base, amount='1.00'), dry_run=True))['dry_run']
                    created = await call('payment selection create', dict(base, amount='1.00'), idempotency_key='draft-create')
                    assert (await call('payment selection create', dict(base, amount='1.00'), idempotency_key='draft-create'))['idempotent_replay']
                    patch = dict(selection=created['id'], expected_version=created['version'], set_items=[
                        dict(invoice=row['id'], expected_version=row['version'], amount=amount)
                        for row, amount in zip(invoices, ('0.50', '0.50'))])
                    assert (await call('payment selection update', patch, dry_run=True))['dry_run']
                    changed = await call('payment selection update', patch)
                    assert (await call('payment selection update', patch, rejected=True))['code'] == 'E_VERSION_CONFLICT'
                    await call('payment selection show', {'selection': created['id'], 'revision': 1})
                    rows = await pages('payment selection items', {'selection': created['id'], 'limit': 1})
                    assert {row['invoice_id'] for row in rows} == {row['id'] for row in invoices}
                    await pages('payment selection query', {'state': 'open', 'limit': 1})
                    clear = {'selection': created['id'], 'expected_version': changed['version']}
                    assert (await call('payment selection clear', clear, dry_run=True))['dry_run']
                    await call('payment selection clear', clear)
                    assert (await pages('payment selection items', {'selection': created['id'], 'limit': 1})) == []
                else:
                    rows = await pages('payment invoices', dict(base, limit=1))
                    assert {row['invoice_id'] for row in rows} == {row['id'] for row in invoices}
                    await pages('payment suggest', dict(base, amount='1.50', limit=1))
                    await pages('payment calculate', dict(base, amount='1.50', limit=1,
                        applications=dict(mode='inline', items=[dict(invoice=row['id'], expected_version=row['version'],
                            amount='0.75') for row in invoices])))
                    assert (await call('payment invoices', dict(base, date='invalid'), rejected=True))['code'] == 'E_VALIDATION'
                assert set(calls) == set(FAMILIES[family])
                for name, data in calls.items():
                    assert (await matrix.call(surface, name, data, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    if not registry.get(name).is_write:
                        assert (await matrix.call(surface, name, data, dry_run=True, rejected=True))['code'] == 'E_USAGE'
            expected = normalize_payment(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize_payment(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a, b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface, index, a, b)
            for surface, path in matrix.roots.items():
                with sqlite3.connect((path/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((path/relative/'company.db').as_uri()+'?mode=ro', uri=True) as db:
                    assert db.execute("SELECT interface FROM audit_events WHERE reason='Registry parity'").fetchall() == [(surface,)] * (3 if family == 'selection' else 0)
                assert payment_operation_rows(path, matrix.company) == operation_baselines[surface]
        finally:
            await matrix.close()
    anyio.run(witness)
