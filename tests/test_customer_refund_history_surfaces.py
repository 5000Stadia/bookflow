"""Activated refund history is the same retained document over all four real adapters."""
import anyio
import pytest

from tests.credit_support import books  # noqa: F401
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database
from tests.test_customer_refund_history import retained

COMMANDS = frozenset({'customer-refund history'})
SURFACES = ('python', 'cli', 'http', 'mcp')
GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


@pytest.mark.timeout(450)
def test_retained_refund_history_on_four_actual_surfaces(books, tmp_path):
    paid, updated = retained(books)
    books['run']('customer-refund void', {'refund': paid['id'], 'expected_version': 2}, reason='Void retained witness')
    client = books['client']
    state = client.run('permission show', {})
    client.run('permission activate', {'expected_generation': state['generation'],
                                     'expected_catalog_sha256': state['catalog_sha256']})

    async def witness():
        matrix = Matrix()
        documents = {}
        try:
            await matrix.open(tmp_path / 'credits', tmp_path / 'surfaces')
            help_result = await matrix.mcp.call_tool('bookflow_help', {'command': 'customer-refund history'})
            assert not help_result.is_error, help_result
            assert 'customer-refund history' in str(help_result)
            for surface in SURFACES:
                location = next(matrix.roots[surface].rglob('company.db'))
                before = database(location)
                first = await matrix.call(surface, 'customer-refund history', {'refund': paid['id'], 'limit': 1})
                second = await matrix.call(surface, 'customer-refund history', {'refund': paid['id'], 'limit': 1,
                                                                           'cursor': first['next_cursor']})
                assert [r['total_minor_units'] for r in first['items'] + second['items']] == [1200, 2000]
                assert first['items'][0]['profile'] == paid['revision']['profile']
                assert second['items'][0]['profile'] == updated['revision']['profile']
                assert second['items'][0]['events'][-1]['reason'] == 'Void retained witness'
                assert first['status'] == second['status'] == 'voided'
                assert not second['has_more']
                missing = await matrix.call(surface, 'customer-refund history', {'refund': GHOST}, rejected=True)
                assert missing['code'] == 'E_RECORD_NOT_FOUND'
                hidden = await matrix.call(surface, 'customer-refund history', {'refund': paid['id']},
                                           company=GHOST, rejected=True)
                assert hidden['code'] == 'E_COMPANY_NOT_FOUND'
                assert database(location) == before
                documents[surface] = [first, second, missing, hidden]
            assert all(documents[surface] == documents['python'] for surface in SURFACES)
        finally:
            await matrix.close()
    anyio.run(witness)
