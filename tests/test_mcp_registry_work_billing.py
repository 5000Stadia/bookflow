"""Work billing creates exact shared financial effects through every adapter."""
import re
import sqlite3
import anyio
import pytest
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import GHOST, company_snapshot
from tests.test_service_sales_lifecycle import sale, COMPANY

FAMILIES = {noun: tuple(noun+' '+verb for verb in ('invoice', 'sales-receipt', 'billing'))
            for noun in ('estimate', 'work-order')}


def settled(documents):
    """The documents, with each forecast fingerprint checked and then set aside.

    `forecast_fingerprint` is a sha256 over the forecast's own content -- and that content names
    the source revision, the line ids and the per-line ordinals keyed by them, every one of which
    is minted separately in each surface's own copy of the seed. So four correct surfaces produce
    four different fingerprints for the same forecast, and comparing the values across surfaces
    asks for something that cannot be true. Blanking it outright would hide a surface that
    returned no fingerprint, or a malformed one, so each is required to be a real digest before it
    is set aside.

    What the fingerprint actually promises -- that it changes when the forecast content changes --
    is witnessed in tests/test_tax_policy_work.py, on one database, where it means something.

    This is the same shape as the continuation cursor, whose fingerprint hashes the query: an
    opaque token derived from document content cannot be compared between surfaces that mint their
    own ids. If a third one appears, it belongs in `normalize` rather than in a third test.
    """
    def scrub(value):
        if isinstance(value, dict):
            return {key: ('<content-derived>' if key == 'forecast_fingerprint' and _digest(value[key])
                          else scrub(value[key])) for key in value}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            # normalize() hands back (command name, payload) pairs, so a scrub that walks only
            # dicts and lists never reaches the payload at all.
            return tuple(scrub(item) for item in value)
        return value
    return [scrub(document) for document in documents]


def _digest(value):
    assert isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), (
        f'forecast_fingerprint is not a digest: {value!r}')
    return True


@pytest.mark.parametrize('noun', FAMILIES)
@pytest.mark.timeout(300)
def test_work_billing_full_documents_retries_and_exact_batches(root, client, sale, tmp_path, noun):
    sources = {}
    for verb in ('invoice', 'sales-receipt'):
        source = client.run(noun+' create', dict(date='2026-01-12', title='Billing '+verb,
            customer=sale['customer'], lines=[dict(item=sale['item'], quantity='2')]), company=COMPANY)
        if noun == 'estimate':
            source = client.run('estimate update', dict(estimate=source['id'], expected_version=1,
                status='accepted', decision_note='Customer accepted billing scope'), company=COMPANY)
        sources[verb] = source
    bank = client.account.create(name='Four surface billing bank', type='bank', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Four surface billing cash', kind='cash'), company=COMPANY)['id']
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
                posted_ids = []
                for verb, source in sources.items():
                    name = noun+' '+verb
                    selector = {noun.replace('-', '_'): source['id']}
                    raw = {**selector, 'expected_version': source['version'], 'conversion_key': 'billing-'+verb,
                           'date': '2026-01-13'}
                    if verb == 'sales-receipt':
                        raw.update(deposit_to=bank, payment_method=method, amount_received='24.68')
                    preview = await matrix.call(surface, name, raw, dry_run=True)
                    assert preview['dry_run'] and preview['total_minor_units'] == 2468
                    posted = await matrix.call(surface, name, raw)
                    assert posted['total_minor_units'] == 2468
                    posted_ids.append(posted['id'])
                    repeated = await matrix.call(surface, name, raw)
                    assert repeated['id'] == posted['id'] and repeated['idempotent_replay']
                    before_rejections = company_snapshot(matrix.roots[surface])
                    assert (await matrix.call(surface, name, {**raw, 'date': '2026-01-14'}, rejected=True))['code'] == 'E_CONVERSION_KEY_REUSED'
                    assert (await matrix.call(surface, name, raw, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    state = await matrix.call(surface, noun+' billing', {**selector, 'limit': 200})
                    assert state['remaining_net_minor_units'] == 0
                    assert state['lines'][0]['billed_quantity'] == '2'
                    assert (await matrix.call(surface, noun+' billing', selector, dry_run=True, rejected=True))['code'] == 'E_USAGE'
                    assert (await matrix.call(surface, noun+' billing', selector, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    assert company_snapshot(matrix.roots[surface]) == before_rejections
                path = matrix.roots[surface]
                with sqlite3.connect((path/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((path/relative/'company.db').as_uri()+'?mode=ro', uri=True) as db:
                    for identifier in posted_ids:
                        totals = db.execute('SELECT sum(debit_minor_units),sum(credit_minor_units),count(DISTINCT batch_id) FROM posting_lines WHERE transaction_id=?', (identifier,)).fetchone()
                        assert totals == (2468,2468,1)
                        assert db.execute('SELECT sum(credit_minor_units-debit_minor_units) FROM posting_lines WHERE transaction_id=? AND account_id=?', (identifier,sale['income'])).fetchone() == (2468,)
                    assert db.execute("SELECT count(*) FROM audit_events WHERE reason='Registry parity'").fetchone() == (2,)
            expected = settled(normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids))
            for surface in ('cli', 'http', 'mcp'):
                actual = settled(normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids))
                assert len(actual) == len(expected)
                for index,(a,b) in enumerate(zip(expected,actual)):
                    assert a == b, (surface,index,a,b)
        finally:
            await matrix.close()
    anyio.run(witness)
