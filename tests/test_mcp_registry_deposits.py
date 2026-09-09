"""Banking undeposited receipts is the same command on all four real interfaces."""
import base64
from copy import deepcopy
import json
import re
import sqlite3

import anyio
import pytest

from bookflow.core import registry
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import GHOST
from tests.test_payment_receipts import posted, method
from tests.test_service_sales_lifecycle import sale, COMPANY

COMMANDS = frozenset({'deposit sources', 'deposit post', 'deposit update', 'deposit void'})


def _guards(value, key=None):
    """Decode the authenticated guards so independently signed tokens compare by content."""
    if isinstance(value, dict):
        return {name: _guards(item, name) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_guards(item) for item in value]
    if key == 'dependency_guard' and isinstance(value, str):
        # One base64url string of payload bytes followed by a 32-byte signature — not a
        # dotted JWT. deposit_dependency_history.issue encodes b64(raw + signature) and its
        # reader checks len(raw) > 32; this test asserted a '.' separator that never existed
        # and could not have been noticed, because it had never been executed.
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        assert len(raw) > 32, 'guard carries a payload and a 32-byte signature'
        payload = json.loads(raw[:-32])
        assert set(payload) == {'v', 'mode', 'company_id', 'actor_id', 'actor_kind', 'principal_id',
                                'root', 'endpoint', 'issuer_entry', 'intent_digest', 'read_digest'}
        return {name: '<graph-digest>' if name.endswith('_digest') else item
                for name, item in sorted(payload.items())}
    return value


def _provenance(value):
    """Drop the MCP adapter's own error annotation before comparing surfaces.

    `adapters/mcp/responses.annotate` adds operation / stage / outcome / operation_ref to an
    error's details, documented in design/specs/9-mcp-adapter.md as adapter provenance: it tells
    an agent whether its write was submitted before the failure, which is what makes a safe retry
    possible. That is a deliberate MCP-only annotation, not a divergence in the command's answer,
    so it is excluded from the cross-surface comparison rather than asserted away.
    """
    if isinstance(value, dict):
        if set(value) >= {'code', 'message', 'details'} and isinstance(value.get('details'), dict):
            details = {k: v for k, v in value['details'].items()
                       if k not in ('operation', 'stage', 'outcome', 'operation_ref')}
            return {**{k: _provenance(v) for k, v in value.items()}, 'details': details}
        return {k: _provenance(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_provenance(item) for item in value]
    return value


def normalize_deposit(documents, root, baseline_ids):
    return normalize(_provenance(_guards(documents)), root, baseline_ids)


@pytest.fixture
def undeposited(client, sale):
    """A customer payment and a counter sale, both waiting in Undeposited Funds."""
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'MCP-DEP-ONE')
    payment_method = method(client)
    uf = next(row['id'] for row in client.run('account list', {}, company=COMPANY)['items']
              if row['system_role'] == 'undeposited_funds')
    client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='100',
        payment_method=payment_method, operation_key='matrix-deposit-cash', applications=dict(mode='inline',
            items=[dict(invoice=invoice['id'], expected_version=1, amount='100')])), company=COMPANY)
    client.run('sales-receipt post', dict(customer=sale['customer'], deposit_to=uf,
        payment_method=payment_method, date='2026-06-02',
        lines=[dict(item=sale['item'], quantity='1', unit_price='60')]), company=COMPANY)
    return dict(bank=client.account.create(name='Deposit parity bank', type='bank', company=COMPANY)['id'],
                fee=client.account.create(name='Deposit parity fee', type='expense', company=COMPANY)['id'],
                till=client.account.create(name='Deposit parity till', type='other_current_asset', company=COMPANY)['id'],
                customer=sale['customer'], uf=uf)


@pytest.mark.timeout(420)
def test_deposit_lifecycle_full_documents_and_exact_ledger(root, undeposited, tmp_path):
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))

    async def witness():
        matrix, deposits = Matrix(), {}
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, data, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(data)
                    return await matrix.call(surface, name, data, **ctx)

                available = await call('deposit sources', dict(date='2026-06-03', limit=200))
                assert available['total_count'] == 2 and available['subtotal']['minor_units'] == 16000
                rows = [dict(source_type=row['source_type'], source=row['source'],
                             expected_version=row['expected_version']) for row in available['items']]
                document = dict(mode='inline', deposit_to=undeposited['bank'], date='2026-06-03',
                    memo='Saturday receipts', sources=rows,
                    additional=[dict(received_from=dict(kind='customer', id=undeposited['customer']),
                                     from_account=undeposited['fee'], amount='-3.00', memo='Processor fee')])
                post = dict(operation_key='matrix-deposit-post', document=document)
                preview = await call('deposit post', post, dry_run=True)
                assert preview['dry_run'] and preview['deposit']['id'] == 'new-deposit'
                banked = await call('deposit post', dict(post, expected_facts_fingerprint=preview['facts_fingerprint']))
                assert banked['deposit']['bank_total']['minor_units'] == 15700
                assert (await call('deposit post', post))['idempotent_replay']
                assert (await call('deposit post', dict(post, document=dict(document, memo='Other')),
                                   rejected=True))['code'] == 'E_DEPOSIT_OPERATION_KEY_REUSED'
                deposits[surface] = banked['deposit']['id']

                # Claiming a receipt bumps its header, so a correction reads the members back.
                members = await call('deposit sources', dict(date='2026-06-03', limit=200,
                                                             for_deposit=banked['deposit']['id']))
                assert all(row['deposited'] for row in members['items'])
                replacement = dict(document, number=banked['deposit']['number'], custom_fields={},
                    expected_custom_field_kinds={}, cash_back=dict(account=undeposited['till'],
                        amount='5.00', memo='Van float'),
                    sources=[dict(source_type=row['source_type'], source=row['source'],
                                  expected_version=row['expected_version'])
                             for row in members['items'] if row['source_type'] == 'payment'])
                update = dict(deposit=banked['deposit']['id'], expected_version=banked['deposit']['version'],
                              operation_key='matrix-deposit-update', document=replacement)
                preview = await call('deposit update', update, dry_run=True)
                corrected = await call('deposit update', dict(update, dependency_guard=preview['dependency_guard']))
                assert corrected['deposit']['bank_total']['minor_units'] == 9200
                assert [row['source'] for row in corrected['receipts']] == [rows[0]['source']]

                void = dict(deposit=banked['deposit']['id'], expected_version=corrected['deposit']['version'],
                            operation_key='matrix-deposit-void')
                preview = await call('deposit void', void, dry_run=True)
                assert preview['dry_run'] and preview['deposit']['status'] == 'voided'
                cancelled = await call('deposit void', dict(void, dependency_guard=preview['dependency_guard']))
                assert cancelled['deposit']['status'] == 'voided'

                assert set(calls) == COMMANDS
                for name, data in calls.items():
                    assert (await matrix.call(surface, name, data, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    if not registry.get(name).is_write:
                        assert (await matrix.call(surface, name, data, dry_run=True, rejected=True))['code'] == 'E_USAGE'

            expected = normalize_deposit(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize_deposit(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a, b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface, index, a, b)

            for surface, path in matrix.roots.items():
                with sqlite3.connect((path / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((path / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
                    assert db.execute("SELECT interface FROM audit_events WHERE command LIKE 'deposit %'").fetchall() == [(surface,)] * 3
                    batches = db.execute('SELECT sum(debit_minor_units),sum(credit_minor_units) FROM posting_lines '
                                         'WHERE transaction_id=? GROUP BY batch_id', (deposits[surface],)).fetchall()
                    assert len(batches) == 4 and all(debit == credit for debit, credit in batches)
                    assert db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines '
                                      'WHERE transaction_id=?', (deposits[surface],)).fetchone() == (0,)
                    assert db.execute('SELECT count(*) FROM deposit_current_memberships '
                                      'WHERE transaction_id=?', (deposits[surface],)).fetchone() == (0,)
                    assert db.execute('SELECT count(*) FROM deposit_operations '
                                      'WHERE transaction_id=?', (deposits[surface],)).fetchone() == (3,)
        finally:
            await matrix.close()

    anyio.run(witness)
