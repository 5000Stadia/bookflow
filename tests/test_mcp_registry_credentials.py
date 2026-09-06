"""Credential commands keep shared permissions and one-time secret semantics."""
import base64
from copy import deepcopy
import hashlib
import re
import sqlite3
import anyio
import pytest
from argon2 import PasswordHasher
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import GHOST

COMMANDS = frozenset(('token issue', 'token list', 'token revoke', 'user set-password'))
PASSWORD = 'Owned matrix password 2026!'


def hub_snapshot(root):
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
        return tuple(db.iterdump())


@pytest.mark.timeout(240)
def test_credentials_full_documents_owned_hashes_and_rejected_state(root, tmp_path):
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
        username = db.execute('SELECT username FROM users WHERE hub_admin=1 ORDER BY id LIMIT 1').fetchone()[0]
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
                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    before = hub_snapshot(matrix.roots[surface]) if ctx.get('rejected') or ctx.get('dry_run') else None
                    result = await matrix.call(surface,name,raw,**ctx)
                    if before is not None:
                        assert hub_snapshot(matrix.roots[surface]) == before
                    return result
                password_input = dict(username=username,password=PASSWORD)
                assert (await call('user set-password',password_input,dry_run=True))['dry_run']
                changed = await call('user set-password',password_input)
                with sqlite3.connect((matrix.roots[surface]/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    stored = db.execute('SELECT password_hash FROM users WHERE id=?',(changed['user_id'],)).fetchone()[0]
                    assert PasswordHasher().verify(stored,PASSWORD)
                assert PASSWORD not in str(changed) and stored not in str(changed)
                preview = await call('token issue',dict(label='Credential parity',days=1),dry_run=True)
                assert preview['secret'] == '' and preview['token_id'] == ''
                issued = await call('token issue',dict(label='Credential parity',days=1))
                secret = issued['secret']
                assert len(base64.urlsafe_b64decode(secret+'='*(-len(secret)%4))) == 32
                with sqlite3.connect((matrix.roots[surface]/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    stored = db.execute('SELECT token_hash FROM api_tokens WHERE id=?',(issued['token_id'],)).fetchone()[0]
                    assert stored == hashlib.sha256(secret.encode()).hexdigest()
                listing = await call('token list',dict(include_revoked=True))
                assert any(row['token_id'] == issued['token_id'] for row in listing['items'])
                assert secret not in str(listing) and stored not in str(listing)
                revoke = {'token': issued['token_id']}
                assert (await call('token revoke',revoke,dry_run=True))['dry_run']
                assert (await call('token revoke',revoke))['changed']
                assert not (await call('token revoke',revoke))['changed']
                assert issued['token_id'] not in [row['token_id'] for row in (await call('token list',{}))['items']]
                await call('token list',dict(include_revoked=True))
                assert (await call('token revoke',{'token':GHOST},rejected=True))['code'] == 'E_TOKEN_NOT_FOUND'
                assert (await call('token issue',{'user':GHOST,'label':'Missing user'},rejected=True))['code'] == 'E_USER_NOT_FOUND'
                assert (await call('user set-password',dict(username='absent-matrix-user',password=PASSWORD),rejected=True))['code'] == 'E_USER_NOT_FOUND'
                for name,data in list(calls.items()):
                    assert (await call(name,data,company=GHOST,rejected=True))['code'] == 'E_USAGE'
                assert set(calls) == COMMANDS
                with sqlite3.connect((matrix.roots[surface]/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    assert db.execute("SELECT count(*) FROM audit_events WHERE reason='Registry parity'").fetchone() == (3,)
                # Only the newly issued opaque secret differs by clone. Its
                # format and stored SHA256 were independently verified above.
                for name,document in matrix.documents[surface]:
                    if name == 'token issue' and document.get('secret'):
                        assert document['secret'] == secret
                        document['secret'] = '<verified-one-time-secret>'
            expected = normalize(matrix.documents['python'],matrix.roots['python'],baseline_ids)
            for surface in ('cli','http','mcp'):
                actual = normalize(matrix.documents[surface],matrix.roots[surface],baseline_ids)
                assert len(actual) == len(expected)
                for i,(a,b) in enumerate(zip(expected,actual)):
                    assert a == b,(surface,i,a,b)
        finally:
            await matrix.close()
    anyio.run(witness)
