"""Held tickets have the same durable lifecycle through all four command surfaces."""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_pos_sale import _norm
from tests.test_row3_host import hosted  # noqa: F401 - fixture

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset('pos ticket ' + verb for verb in ('hold', 'list', 'resume', 'discard'))
CART = {'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}]}


@pytest.mark.timeout(900)
def test_tickets_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                async def call(name, body=None, **kw):
                    return await matrix.call(surface, name, body or {}, **kw)
                off = await call('pos ticket list', rejected=True)
                info = await call('company show')
                await call('company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                before = await call('report trial-balance')
                held = await call('pos ticket hold', dict(CART, ticket='surface-hold-1', label='Collect later'))
                listed = await call('pos ticket list')
                assert len(listed['items']) == 1 and listed['items'][0] == held
                resumed = await call('pos ticket resume', {'ticket': held['id'], 'expected_version': held['version']})
                assert (await call('pos ticket list'))['items'] == []
                stale = await call('pos ticket resume', {'ticket': held['id'], 'expected_version': held['version']}, rejected=True)
                reheld = await call('pos ticket hold', dict(CART, ticket=held['id'], label='Collect later',
                                                          expected_version=resumed['version']))
                discarded = await call('pos ticket discard', {'ticket': held['id'], 'expected_version': reheld['version']})
                assert (await call('pos ticket list'))['items'] == []
                assert _norm(await call('report trial-balance')) == _norm(before)
                results[surface] = (
                    off.get('code') or off.get('error', {}).get('code'),
                    held['label'], held['total']['amount'], held['status'], held['version'],
                    resumed['status'], resumed['version'], resumed['lines'][0]['quantity'],
                    stale.get('code') or stale.get('error', {}).get('code'),
                    reheld['status'], reheld['version'], discarded['status'], discarded['version'],
                )
            first = results['python']
            assert first[0] == 'E_FEATURE_DISABLED'
            assert first[1] == 'Collect later' and first[3:5] == ('held', 1)
            assert first[5:8] == ('open', 2, '2')
            assert first[8:] == ('E_VERSION_CONFLICT', 'held', 3, 'discarded', 4)
            assert all(value == first for value in results.values()), results
        finally:
            await matrix.close()
    anyio.run(witness)


def test_plain_clerk_holds_resumes_discards_but_cannot_hold_a_price_override(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON, _password, _pos

    cid = hosted.company_id
    _pos(hosted, True)
    settings = pos_presets.preset('pos-clerk')
    username = 'held-ticket-clerk'
    user = hosted.ok('user.add', {'username': username, 'password': _password(username), 'company': cid,
                                 'role': 'readonly'}, headers=REASON)['user_id']
    hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly', 'grants': settings['grants'],
        'denies': settings['denies'], 'expected_version': _membership_version(hosted, user)}, headers=REASON)
    with TestClient(hosted.handle.app) as till:
        assert till.post('/login', json={'username': username, 'password': _password(username)}).status_code == 200

        def call(verb, body):
            return till.post(f'/companies/{cid}/commands/pos.ticket.{verb}',
                             headers={**H, **(REASON if verb != 'list' else {})}, json=body)

        refused = call('hold', {'ticket': 'no-price-override', 'lines': [{'item': 'Pipe Sealant', 'unit_price': '0.01'}]})
        assert refused.status_code == 403 and refused.json()['details']['capability'] == 'pos.price-override', refused.text
        response = call('hold', dict(CART, ticket='clerk-held', label='Collect after lunch'))
        assert response.status_code == 200, response.text
        held = response.json()
        assert held['clerk_id'] == user
        listed = call('list', {})
        assert listed.status_code == 200 and listed.json()['items'] == [held], listed.text
        resumed = call('resume', {'ticket': held['id'], 'expected_version': held['version']})
        assert resumed.status_code == 200 and resumed.json()['status'] == 'open', resumed.text
        discarded = call('discard', {'ticket': held['id'], 'expected_version': resumed.json()['version']})
        assert discarded.status_code == 200 and discarded.json()['status'] == 'discarded', discarded.text
        assert call('list', {}).json()['items'] == []
