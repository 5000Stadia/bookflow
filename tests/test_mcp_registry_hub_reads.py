"""Hub metadata, visibility and finite audit reads preserve adapter semantics."""
from copy import deepcopy
import re
import sqlite3
import bookflow
import anyio
import pytest
from bookflow.core import registry
from bookflow.documentation.examples import EXAMPLES
from tests.mcp_matrix_support import Matrix, normalize

FAMILIES = {
    'metadata': ('chart list', 'chart show', 'profile list', 'profile show'),
    'visibility': ('organization list', 'organization show', 'company list', 'company show'),
    'hub_audit': ('hub audit list', 'hub audit show', 'hub audit tail'),
}
GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.timeout(240)
def test_hub_read_full_documents_and_scope_boundaries(root, tmp_path, family):
    if family == "visibility":
        bookflow.connect(data_root=str(root)).company.new(legal_name="Parity sibling", home_currency="USD", chart="none")
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
                async def call(name, data, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(data)
                    return await matrix.call(surface, name, data, **ctx)
                if family == 'metadata':
                    for name in FAMILIES[family]:
                        await call(name, EXAMPLES[name].input)
                    await call('chart show', {'template_id': 'missing-template'}, rejected=True)
                    await call('profile show', {'profile_id': 'missing-profile'}, rejected=True)
                elif family == 'visibility':
                    organizations = await call('organization list', {})
                    organization = organizations['items'][0]['organization_id']
                    await call('organization show', {'organization': organization})
                    await call('organization show', {'organization': GHOST}, rejected=True)
                    companies = await call('company list', {})
                    assert len(companies['items']) > 1, 'fixture must retain sibling companies'
                    for company in companies['items'][:2]:
                        await call('company show', {}, company=company['company_id'])
                else:
                    page = await call('hub audit list', {'limit': 2})
                    assert len(page['items']) == 2
                    await call('hub audit show', {'event': page['items'][0]['id']})
                    await call('hub audit show', {'event': GHOST}, rejected=True)
                    if page['next_before']:
                        await call('hub audit list', {'limit': 2, 'before': page['next_before']})
                    tail = await call('hub audit tail', {'after': 0, 'limit': 2})
                    assert tail['items']
                    # This is the finite polling contract; process-local --follow
                    # is separately classified, not expected to execute over MCP.
                    assert (await call('hub audit tail', {'limit': 0}, rejected=True))['code'] == 'E_VALIDATION'
                assert set(calls) == set(FAMILIES[family])
                for name, data in calls.items():
                    assert (await matrix.call(surface, name, data, dry_run=True, rejected=True))['code'] == 'E_USAGE'
                    error = await matrix.call(surface, name, data, company=GHOST, rejected=True)
                    assert error['code'] == ('E_COMPANY_NOT_FOUND' if registry.get(name).scope == 'company' else 'E_USAGE')
            expected = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a,b) in enumerate(zip(expected,actual)):
                    assert a == b, (surface,index,a,b)
            for path in matrix.roots.values():
                with sqlite3.connect((path/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    assert db.execute("SELECT count(*) FROM audit_events WHERE reason='Registry parity'").fetchone() == (0,)
        finally:
            await matrix.close()
    anyio.run(witness)
