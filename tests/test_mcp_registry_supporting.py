"""Remaining annotation, rate, report and linked-name command families."""
from copy import deepcopy
import re
import sqlite3

import anyio
import pytest

import bookflow
from bookflow.core import registry
from bookflow.documentation.examples import EXAMPLES
from tests.mcp_matrix_support import Matrix, normalize

GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'
FAMILIES = {
    'rates': ('rate set', 'rate show', 'rate query'),
    'annotations': ('directive add', 'directive show', 'directive list', 'directive deactivate',
                    'note add', 'note show', 'note list', 'note edit'),
    'links': ('customer link-vendor', 'customer unlink-vendor', 'other-name convert'),
    'reports': ('report trial-balance', 'report general-ledger', 'report transaction-detail',
                'report balance-sheet', 'report profit-and-loss',
                'report cash-flows', 'report income-tax-summary',
                'report ar-aging', 'report open-invoices', 'report statement',
                'report ap-aging', 'report unpaid-bills', 'report missing-checks'),
}


@pytest.mark.timeout(240)
@pytest.mark.parametrize('family', FAMILIES)
def test_supporting_family_full_documents_and_rejections(root, tmp_path, family):
    registry.load_all()
    seed = bookflow.connect(data_root=str(root))
    company = seed.company.list()['items'][0]['company_id']
    customer = seed.customer.create(name='Supporting matrix customer', company=company)
    vendor = seed.vendor.create(name='Supporting matrix vendor', company=company)
    other = seed.run('other-name create', {'name': 'Supporting matrix conversion'}, company=company)
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for row in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', row))

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}
                async def call(command, raw, **context):
                    if not context.get("rejected"):
                        calls[command] = deepcopy(raw)
                    return await matrix.call(surface, command, raw, **context)
                async def write(command, raw, *, key=None, **context):
                    assert (await call(command, raw, dry_run=True, **context))['dry_run']
                    result = await call(command, raw, **({'idempotency_key': key} if key else {}), **context)
                    if key:
                        replay = await call(command, raw, idempotency_key=key, **context)
                        assert replay['idempotent_replay']
                    return result
                if family == 'rates':
                    raw = {'date': '2026-02-12', 'from_currency': 'JPY', 'rate': '0.0068', 'expected_version': 0}
                    first = await write('rate set', raw, key='support-rate')
                    await call('rate show', {'rate_id': first['id']})
                    await call('rate show', {'date': raw['date'], 'from_currency': 'JPY'})
                    await call('rate query', {'from_currency': 'JPY', 'limit': 200})
                    await write('rate set', {**raw, 'rate': '0.007', 'expected_version': first['version']})
                    error = await call('rate set', raw, rejected=True)
                    assert error['code'] == 'E_VERSION_CONFLICT'
                    assert (await call('rate show', {'date': '2031-01-01', 'from_currency': 'JPY'}, rejected=True))['code'] == 'E_RECORD_NOT_FOUND'
                elif family == 'annotations':
                    directive = (await write('directive add', {'text': 'Record the customer access instructions'}, key='support-directive'))['directive']
                    await call('directive show', {'directive': directive['id']})
                    await call('directive list', {'include_inactive': True})
                    target = {'record_type': 'customer', 'record_id': customer['id']}
                    note = (await write('note add', {**target, 'body': 'Use the side gate'}, key='support-note', directive=directive['code']))['note']
                    await call('note show', {'note': note['id']})
                    await call('note list', {**target, 'limit': 200})
                    update = {'note': note['id'], 'expected_version': note['version'], 'body': 'Ring the side gate bell'}
                    await write('note edit', update, directive=directive['code'])
                    assert (await call('note edit', update, rejected=True))['code'] == 'E_VERSION_CONFLICT'
                    assert (await call('note edit', update, idempotency_key='unsupported-key', rejected=True))['code'] == 'E_USAGE'
                    await write('directive deactivate', {'directive': directive['id']})
                    assert (await call('note add', {**target, 'body': 'Inactive instruction'}, directive=directive['code'], rejected=True))['code'] == 'E_DIRECTIVE_INACTIVE'
                elif family == 'links':
                    raw = {'customer': customer['id'], 'vendor': vendor['id'], 'expected_customer_version': customer['version'], 'expected_vendor_version': vendor['version']}
                    linked = await write('customer link-vendor', raw)
                    await write('customer unlink-vendor', {'customer': customer['id'], 'expected_customer_version': linked['customer_version'], 'expected_vendor_version': linked['vendor_version'], 'expected_link_version': linked['link_version']})
                    assert (await call('customer link-vendor', raw, rejected=True))['code'] == 'E_VERSION_CONFLICT'
                    converted = await write('other-name convert', {'other_name': other['id'], 'to': 'vendor', 'expected_version': other['version']})
                    assert converted['target_type'] == 'vendor'
                else:
                    for command in FAMILIES[family]:
                        raw = {**EXAMPLES[command].input, 'limit': 200}
                        if command == 'report statement':
                            # One customer's own account: the documented example names a
                            # seeded customer, and this scenario reads its own instead.
                            raw['customer'] = customer['id']
                        result = await call(command, raw, dry_run=False)
                        assert result['next_cursor'] is None
                        # Each report names its own inclusive bound; reject a bad one there.
                        bound = 'date_to' if 'date_to' in raw else 'as_of'
                        assert (await call(command, {**raw, bound: 'invalid-date'}, rejected=True))['code'] == 'E_VALIDATION'
                assert set(calls) == set(FAMILIES[family])
                # Valid input shapes with an inaccessible company exercise routing
                # rejection without conflating CLI syntax with business validation.
                for command, raw in calls.items():
                    assert (await matrix.call(surface, command, raw, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    if not registry.get(command).is_write:
                        assert (await matrix.call(surface, command, raw, dry_run=True, rejected=True))['code'] == 'E_USAGE'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a, b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface, index, a, b)
            # Preview/replay/rejections must not create extra attributed writes.
            expected_audits = {'rates': 2, 'annotations': 4, 'links': 3, 'reports': 0}[family]
            for surface, data_root in matrix.roots.items():
                with sqlite3.connect((data_root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (company,)).fetchone()[0]
                with sqlite3.connect((data_root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
                    rows = db.execute("SELECT interface FROM audit_events WHERE reason='Registry parity'").fetchall()
                    assert rows == [(surface,)] * expected_audits
        finally:
            await matrix.close()
    anyio.run(witness)
