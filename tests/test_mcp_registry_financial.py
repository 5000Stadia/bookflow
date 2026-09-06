"""Financial revisions, previews, retries and rejections through four adapters."""

from copy import deepcopy
import re
import sqlite3

import anyio
import pytest

from bookflow.core import registry
from bookflow.documentation.examples import EXAMPLES
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_service_sales_lifecycle import sale

GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


@pytest.mark.timeout(240)
@pytest.mark.parametrize('noun', ['journal', 'invoice', 'sales-receipt'])
def test_financial_lifecycle_full_documents_and_ledger_parity(root, tmp_path, noun, sale):
    registry.load_all()
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for row in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', row))

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            ids = {}
            for surface in matrix.documents:
                raw = deepcopy(EXAMPLES[noun + ' post'].input)
                if noun != 'journal':
                    raw['customer'] = sale['customer']
                    raw['lines'] = [{'item': sale['item'], 'quantity': '1', 'unit_price': '125.00'}]
                preview = await matrix.call(surface, noun + ' post', raw, dry_run=True)
                assert preview['dry_run'] is True
                if 'facts_fingerprint' in preview:
                    raw['expected_facts_fingerprint'] = preview['facts_fingerprint']
                posted = await matrix.call(surface, noun + ' post', raw, idempotency_key='matrix-post')
                replay = await matrix.call(surface, noun + ' post', raw, idempotency_key='matrix-post')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                ids[surface] = posted['id']
                selector = {noun.replace('-', '_'): posted['id']}
                update = {**selector, 'expected_version': posted['version'], 'memo': 'Corrected matrix memo'}
                preview = await matrix.call(surface, noun + ' update', update, dry_run=True)
                assert preview['dry_run'] is True
                if 'facts_fingerprint' in preview:
                    update['expected_facts_fingerprint'] = preview['facts_fingerprint']
                changed = await matrix.call(surface, noun + ' update', update)
                conflict = await matrix.call(surface, noun + ' update', update, rejected=True)
                assert conflict['code'] == 'E_VERSION_CONFLICT'
                await matrix.call(surface, noun + ' show', selector)
                await matrix.call(surface, noun + ' history', {**selector, 'limit': 200})
                await matrix.call(surface, noun + ' query', {'limit': 200})
                void = {**selector, 'expected_version': changed['version']}
                assert (await matrix.call(surface, noun + ' void', void, dry_run=True))['dry_run']
                voided = await matrix.call(surface, noun + ' void', void)
                assert voided['status'] == 'voided'
                for verb, data in [('post', raw), ('update', update), ('show', selector), ('history', selector), ('query', {}), ('void', void)]:
                    error = await matrix.call(surface, noun + ' ' + verb, data, company=GHOST, rejected=True)
                    assert error['code'] == 'E_COMPANY_NOT_FOUND'
                await matrix.call(surface, noun + ' history', {**selector, 'limit': 200})
            baseline = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                for index, (expected, got) in enumerate(zip(baseline, actual)):
                    assert got == expected, (surface, index, got, expected)
                assert len(actual) == len(baseline)
            for surface, root_copy in matrix.roots.items():
                with sqlite3.connect((root_copy / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((root_copy / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
                    totals = db.execute('SELECT batch_id,sum(debit_minor_units),sum(credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY batch_id', (ids[surface],)).fetchall()
                    assert totals and all(debit == credit for _, debit, credit in totals)
                    assert all(net == 0 for _, net in db.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id', (ids[surface],)))
                    assert db.execute("SELECT count(*) FROM audit_events WHERE reason='Registry parity'").fetchone() == (3,)
        finally:
            await matrix.close()
    anyio.run(witness)
