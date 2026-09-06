"""Nonposting work lifecycles and permanent lineage through all four adapters."""
from copy import deepcopy
import re
import base64
import hashlib
import json
import sqlite3
import anyio
import pytest
from bookflow.core import registry
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_service_sales_lifecycle import sale

FAMILIES = {noun: tuple(noun+' '+verb for verb in ('create', 'copy', 'show', 'query', 'update', 'history', last))
            for noun, last in [('proposal', 'estimate'), ('estimate', 'work-order'), ('work-order', 'complete')]}
GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


def ledger(root):
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
        path = db.execute('SELECT path FROM companies ORDER BY id LIMIT 1').fetchone()[0]
    with sqlite3.connect((root/path/'company.db').as_uri()+'?mode=ro', uri=True) as db:
        return {table: db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                for table in ('transactions', 'posting_batches', 'posting_lines')}


def company_snapshot(root):
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
        path = db.execute('SELECT path FROM companies ORDER BY id LIMIT 1').fetchone()[0]
    with sqlite3.connect((root/path/'company.db').as_uri()+'?mode=ro', uri=True) as db:
        return tuple(db.iterdump())


@pytest.mark.parametrize('noun', FAMILIES)
@pytest.mark.timeout(300)
def test_nonposting_work_lifecycle_full_documents_and_lineage(root, tmp_path, sale, noun):
    registry.load_all()
    before = ledger(root)
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            cursors = {}
            for surface in matrix.documents:
                cursors[surface] = {}
                calls = {}
                async def call(verb, raw, **ctx):
                    name = noun+' '+verb
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    before_rejection = company_snapshot(matrix.roots[surface]) if ctx.get('rejected') else None
                    result = await matrix.call(surface, name, raw, **ctx)
                    if before_rejection is not None:
                        assert company_snapshot(matrix.roots[surface]) == before_rejection
                    return result
                async def write(verb, raw, **ctx):
                    preview = await call(verb, raw, dry_run=True, **ctx)
                    assert preview['dry_run']
                    return await call(verb, raw, **ctx)
                raw = dict(date='2026-01-12', title='Four interface work', customer=sale['customer'],
                           scope='Replace the tap', lines=[dict(item=sale['item'], quantity='2')])
                original = await write('create', raw, idempotency_key='work-create')
                repeated = await call('create', raw, idempotency_key='work-create')
                assert repeated['id'] == original['id'] and repeated['idempotent_replay']
                selector = {noun.replace('-', '_'): original['id']}
                await call('show', selector)
                copy = await write('copy', {**selector, 'expected_version': 1, 'date': '2026-01-13'})
                assert copy['id'] != original['id']
                update = {**selector, 'expected_version': 1, 'memo': 'Corrected site instructions'}
                current = await write('update', update)
                assert current['version'] == 2
                assert (await call('update', update, rejected=True))['code'] == 'E_VERSION_CONFLICT'
                old = await call('show', {**selector, 'revision_number': 1})
                assert old['revision']['facts']['memo'] is None
                history_input = {**selector, 'limit': 1}
                page = await call('history', history_input)
                revisions = list(page['items'])
                while page['next_cursor']:
                    token = page['next_cursor']
                    payload = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
                    assert set(payload) == {'v', 'company', 'noun', 'fingerprint', 'permissions', 'sequence', 'offset'}
                    contract = registry.get(noun+' history').input_model.model_validate(history_input).model_dump(exclude={'cursor'})
                    contract['query'] = None
                    expected_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    assert payload['fingerprint'] == expected_hash
                    cursors[surface][token] = {**payload, 'fingerprint': contract}
                    page = await call('history', {**history_input, 'cursor': token})
                    revisions.extend(page['items'])
                assert [row['revision_number'] for row in revisions] == [1, 2]
                await call('query', {'customer': sale['customer'], 'limit': 200})
                if noun == 'estimate':
                    current = await write('update', {**selector, 'expected_version': current['version'],
                        'status': 'accepted', 'decision_note': 'Customer selected this option'})
                if noun == 'work-order':
                    final = await write('complete', {**selector, 'expected_version': current['version'],
                        'actual_start': '2026-01-14T10:00:00Z', 'actual_end': '2026-01-14T11:00:00Z'})
                    assert final['status'] == 'complete'
                else:
                    verb = 'estimate' if noun == 'proposal' else 'work-order'
                    conversion = {**selector, 'expected_version': current['version'],
                                  'conversion_key': 'work-lineage', 'date': '2026-01-14'}
                    final = await write(verb, conversion)
                    repeated = await call(verb, conversion)
                    assert repeated['id'] == final['id'] and repeated['idempotent_replay']
                    assert (await call(verb, {**conversion, 'date': '2026-01-15'}, rejected=True))['code'] == 'E_CONVERSION_KEY_REUSED'
                await call('history', {**selector, 'limit': 200})
                assert set(calls) == set(FAMILIES[noun])
                for name, data in calls.items():
                    before_rejection = company_snapshot(matrix.roots[surface])
                    error = await matrix.call(surface, name, data, company=GHOST, rejected=True)
                    assert error['code'] == 'E_COMPANY_NOT_FOUND'
                    if not registry.get(name).is_write:
                        assert (await matrix.call(surface, name, data, dry_run=True, rejected=True))['code'] == 'E_USAGE'
                    assert company_snapshot(matrix.roots[surface]) == before_rejection
                assert ledger(matrix.roots[surface]) == before, 'nonposting work or rejected call changed the ledger'
            def documents(surface):
                def expand(value):
                    if isinstance(value, dict):
                        return {k: [path.split('.') for path in v] if k == 'changed_fields' else expand(v) for k,v in value.items()}
                    if isinstance(value, (tuple, list)):
                        return [expand(v) for v in value]
                    if isinstance(value, str) and value in cursors[surface]:
                        return cursors[surface][value]
                    return value
                return normalize(expand(matrix.documents[surface]), matrix.roots[surface], baseline_ids)
            expected = documents('python')
            for surface in ('cli', 'http', 'mcp'):
                actual = documents(surface)
                assert len(actual) == len(expected)
                for index, (a,b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface,index,a,b)
        finally:
            await matrix.close()
    anyio.run(witness)
