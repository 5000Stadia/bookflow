"""The unavailable live-feed seam has the same contract on all four surfaces."""
import anyio

from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('bank-feed-connection add', 'bank-feed-connection enable', 'bank-feed-connection disable',
                      'bank-feed-connection check', 'bank-feed-connection query', 'bank-feed fetch'))


def test_connection_seam_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in ('python', 'cli', 'http', 'mcp'):
                result = await matrix.call(surface, 'bank-feed-connection query', {})
                assert result['rows'] == [] and result['kinds_available'] == []
                error = await matrix.call(surface, 'bank-feed-connection add', {
                    'name': 'Bank connection', 'kind': 'unavailable', 'account': 'Checking',
                    'remote_account': 'remote'}, rejected=True)
                assert 'E_VALIDATION' in str(error)
                for name in COMMANDS - {'bank-feed-connection add', 'bank-feed-connection query'}:
                    error = await matrix.call(surface, name, {'connection': 'absent'}, rejected=True)
                    assert 'E_RECORD_NOT_FOUND' in str(error), (surface, name, error)
                error = await matrix.call(surface, 'bank-feed fetch', {'connection': 'absent'},
                                          dry_run=True, rejected=True)
                assert 'E_RECORD_NOT_FOUND' in str(error)
        finally:
            await matrix.close()
    anyio.run(witness)
