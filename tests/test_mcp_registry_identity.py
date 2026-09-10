"""Adding a person and moving their access is the same command on all four interfaces.

tests/test_identity_commands.py drives these commands over HTTP and the workbench and
tests/test_identity_mcp.py drives a real MCP client end to end; neither puts the library
and the packaged CLI beside them on identical books. This does, so the identity rows in
the execution ledger rest on one executed four-surface scenario like every other family.
"""
from copy import deepcopy
import re
import sqlite3

import anyio
import pytest
from argon2 import PasswordHasher

import bookflow
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import GHOST

COMMANDS = frozenset(('user add', 'user list', 'membership grant', 'membership list',
                      'membership revoke'))
NEWCOMER = 'matrix-morgan'


def hub_snapshot(root):
    with sqlite3.connect((root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        return tuple(db.iterdump())


@pytest.mark.timeout(300)
def test_identity_lifecycle_full_documents_owned_password_and_rejected_state(root, tmp_path):
    seed = bookflow.connect(data_root=str(root))
    first = seed.company.list()['items'][0]
    organization, company = first['organization_id'], first['company_id']
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    before = hub_snapshot(matrix.roots[surface]) if ctx.get('rejected') or ctx.get('dry_run') else None
                    result = await matrix.call(surface, name, raw, **ctx)
                    if before is not None:
                        assert hub_snapshot(matrix.roots[surface]) == before, (surface, name)
                    return result

                add = dict(username=NEWCOMER, display_name='Morgan Ellis',
                           organization=organization, role='standard')
                preview = await call('user add', add, dry_run=True)
                assert preview['dry_run'] and preview['password'] is None
                assert preview['membership']['role'] == 'standard'
                added = await call('user add', add)
                password = added['password']
                assert password and added['username'] == NEWCOMER
                with sqlite3.connect((matrix.roots[surface] / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
                    stored = db.execute('SELECT password_hash FROM users WHERE id=?',
                                        (added['user_id'],)).fetchone()[0]
                assert PasswordHasher().verify(stored, password)
                assert stored not in str(added)

                people = await call('user list', dict(organization=organization))
                assert NEWCOMER in {row['username'] for row in people['items']}
                assert all(row['kind'] and row['added_at'] for row in people['items'])

                granted = await call('membership grant',
                                     dict(user=NEWCOMER, organization=organization, role='readonly'))
                assert granted['changed'] and granted['role'] == 'readonly'
                assert granted['scope_type'] == 'organization' and granted['revoked_at'] is None
                held = await call('membership list', dict(user=NEWCOMER))
                assert [(row['scope_type'], row['role']) for row in held['items']] == [('organization', 'readonly')]

                revoke = dict(user=NEWCOMER, organization=organization)
                revoked = await call('membership revoke', revoke)
                assert revoked['changed'] and revoked['revoked_at']
                again = await call('membership revoke', revoke)
                assert not again['changed'] and again['revoked_at'] == revoked['revoked_at']
                assert (await call('membership list', dict(user=NEWCOMER)))['count'] == 0
                withdrawn = await call('membership list', dict(user=NEWCOMER, include_inactive=True))
                assert [row['active'] for row in withdrawn['items']] == [False]

                # A name already in use, whatever its case; nobody to grant to; and a
                # scope this person was never given.
                assert (await call('user add', dict(add, username=NEWCOMER.upper()),
                                   rejected=True))['code'] == 'E_VALIDATION'
                assert (await call('membership grant', dict(user='nobody-at-all', organization=organization),
                                   rejected=True))['code'] == 'E_USER_NOT_FOUND'
                assert (await call('membership revoke', dict(user=NEWCOMER, company=company),
                                   rejected=True))['code'] == 'E_RECORD_NOT_FOUND'
                assert (await call('user list', dict(organization=GHOST),
                                   rejected=True))['code'] == 'E_ORGANIZATION_NOT_FOUND'

                assert set(calls) == COMMANDS
                # These are hub commands: naming a company at all is a usage error.
                for name, data in calls.items():
                    assert (await call(name, data, company=GHOST, rejected=True))['code'] == 'E_USAGE'

                with sqlite3.connect((matrix.roots[surface] / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
                    attributed = [row[0] for row in db.execute(
                        "SELECT command FROM audit_events WHERE reason='Registry parity' ORDER BY id").fetchall()]
                # Three attributed writes and no more: previews, rejections and the
                # second revoke — which reported `changed` false — changed nothing, so
                # none of them left an attributed row behind.
                assert attributed == ['user add', 'membership grant', 'membership revoke']

                # Only the generated password differs by clone; it was verified against
                # this clone's own stored hash above.
                for name, document in matrix.documents[surface]:
                    if name == 'user add' and document.get('password'):
                        assert document['password'] == password
                        document['password'] = '<verified-generated-password>'

            expected = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a, b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface, index, a, b)
        finally:
            await matrix.close()

    anyio.run(witness)
