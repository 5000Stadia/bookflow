"""Actual MCP credential permissions and the original self-revocation receipt."""

import os
from pathlib import Path
import sys

import anyio
import pytest

from tests.test_row3_host import hosted, live


@pytest.mark.timeout(90)
def test_real_mcp_authorized_secret_and_lowercase_self_revocation_receipt(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.core.config import Config
    user = Config.load(hosted.root / 'config.toml').user_table(hosted.login)['user_id']

    async def witness():
        params = StdioServerParameters(command=os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow'))),
            args=['mcp', '--url', live], env={'BOOKFLOW_TOKEN': hosted.secret,
                'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                issued = await session.call_tool('bookflow_run', {'command': 'token issue', 'input': {'label': 'MCP authorized secret'}})
                assert not issued.is_error, 'Authorized credential issuance was denied'
                document = issued.structured_content
                assert isinstance(document.get('secret'), str), 'Authorized one-time secret was lost'
                assert document['secret'] != hosted.secret, 'Configured bearer was echoed'
                assert issued.content[0].text.count(document['secret']) == 1
                changed = await session.call_tool('bookflow_run', {'command': 'user set-password',
                    'input': {'username': user, 'password': 'Disposable-MCP-password-8491'}})
                assert not changed.is_error, 'Authorized password change was denied'
                revoked = await session.call_tool('bookflow_run', {'command': 'token revoke',
                    'input': {'token': hosted.token.lower()}})
                assert not revoked.is_error, 'Original own-revocation receipt was suppressed'
                denied = await session.call_tool('bookflow_run', {'command': 'company list', 'input': {}})
                assert denied.is_error and denied.structured_content['code'] == 'E_UNAUTHENTICATED'
    anyio.run(witness)
