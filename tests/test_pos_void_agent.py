"""An actual MCP agent cannot borrow a human principal's POS void authority."""
import anyio

from tests import provenance
from tests.conftest import hosted_call, make_agent
from tests.test_row3_host import hosted, live  # noqa: F401


def test_mcp_seller_without_void_gets_exact_pos_void_refusal(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.core.config import Config
    from tests.test_item_pictures_review import _membership_version

    cid = hosted.company_id
    info = hosted.ok('company.show', {}, company=cid)
    hosted.ok('company.update', {'pos_enabled': True, 'expected_version': info['info_version']}, company=cid)
    cart = {'lines': [{'item': 'Pipe Sealant'}]}
    total = hosted.ok('pos.sale.preview', cart, company=cid)['total']['amount']
    sale = hosted.ok('pos.sale.complete', dict(cart, ticket='agent-void-refusal', expected_total=total,
                                              tender={'payment_method': 'Cash'}), company=cid)
    principal = Config.load(hosted.root / 'config.toml').user_table(hosted.login)['user_id']
    agent = make_agent(hosted_call(hosted), 'pos-seller-agent', principals=principal, company=cid, role='readonly')
    hosted.ok('membership.grant', {'user': agent, 'company': cid, 'role': 'readonly',
        'grants': ['pos.sell'], 'denies': ['pos.void'], 'expected_version': _membership_version(hosted, agent)})
    issued = hosted.ok('token.issue', {'user': agent, 'principal': principal, 'label': 'POS seller MCP witness'})

    async def witness():
        params = StdioServerParameters(command=provenance.launcher(), args=['mcp', '--url', live],
            env=provenance.child_env(BOOKFLOW_TOKEN=issued['secret'], BOOKFLOW_COMPANY=cid,
                                     BOOKFLOW_DATA_ROOT=str(tmp_path / 'absent')), cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                preview = await session.call_tool('bookflow_run', {
                    'command': 'pos sale preview', 'input': cart, 'company': cid})
                assert not preview.is_error, preview.structured_content
                refused = await session.call_tool('bookflow_run', {
                    'command': 'pos sale void', 'company': cid, 'reason': 'Customer canceled',
                    'input': {'sale': sale['sales_receipt']['name'], 'expected_version': 1}})
                assert refused.is_error
                assert refused.structured_content['code'] == 'E_PERMISSION'
                assert refused.structured_content['details']['capability'] == 'pos.void'
    anyio.run(witness)
    receipt = hosted.ok('sales-receipt.show', {'sales_receipt': sale['sales_receipt']['id']}, company=cid)
    assert receipt['status'] == 'posted' and receipt['version'] == 1


def test_legacy_void_seller_guard_names_pos_void(root):
    import pytest
    import bookflow
    from tests.conftest import as_user, make_actor, make_legacy
    from tests.test_pos_sale import Company, _setup
    from tests.test_pos_void import _sale

    co = Company(root)
    _setup(co)
    sale, _ = _sale(co, 'legacy-void-refusal')
    make_actor(root, 'legacy-pos-reader', company_role=(co.cid, 'readonly'))
    make_legacy(root)
    reader = as_user(root, 'legacy-pos-reader')
    with pytest.raises(bookflow.BookflowError) as refused:
        reader.run('pos sale void', {'sale': sale['id'], 'expected_version': 1},
                   company=co.cid, reason='Legacy authority witness')
    assert refused.value.code == 'E_PERMISSION'
    assert refused.value.details['capability'] == 'pos.void'
    assert refused.value.details['required_role'] == 'standard'
