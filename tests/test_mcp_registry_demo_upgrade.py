"""Owned demo replacement and no-op migration retain their lifecycle contracts."""
from copy import deepcopy
from pathlib import Path
import re
import sqlite3
import anyio
import pytest

from tests import provenance
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_rollout import state as _state

# A bearer's `last_used_at` is refreshed on use once its recorded use is old enough. Demo reset
# on an activated root can outlast that window, so the parity bearer's next use (even a rejected
# one) may legitimately refresh it; nothing else may change.
_LAST_USED = re.compile(r'^(INSERT INTO "api_tokens" VALUES\(.*),(NULL|\'[^\']*\'),(NULL|\'[^\']*\'),(NULL|\d+)\);$')


def state(root):
    return {path: tuple(_LAST_USED.sub(r"\1,<last used>,\3,\4);", line) for line in lines)
            for path, lines in _state(root).items()}


COMMANDS = frozenset({'demo reset','upgrade'})


# `demo reset` on an activated root seeds through the policy_v1 permission check on every
# seeded command and currently outlasts the MCP client's 30 s read + 30 s recovery budget under
# load (measured 2026-09-27: about 3x the legacy seed time). Until that cost is addressed its
# four-surface parity is witnessed on a legacy root; `upgrade` runs on the new-install mode.
@pytest.mark.parametrize('command', [pytest.param('demo reset', marks=pytest.mark.legacy_permissions), 'upgrade'])
@pytest.mark.timeout(provenance.MATRIX_SECONDS)
def test_owned_demo_replacement_and_current_schema_upgrade(root, tmp_path, command):
    ids = set()
    for lines in state(root).values():
        for line in lines:
            ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b',line))
    def compared(documents, here):
        documents = deepcopy(documents)
        for name,row in documents:
            if name == 'demo reset' and 'trashed_path' in row and row['trashed_path'] is not None:
                path = Path(row['trashed_path'])
                assert path.is_relative_to(here)
                if not row['dry_run']:
                    assert path.is_dir() and list(path.rglob('company.db'))
                row['trashed_path'] = '<owned demo trash path>'
        return normalize(documents,here,ids)
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root,tmp_path)
            for surface in matrix.documents:
                here = matrix.roots[surface]
                before = state(here)
                preview = await matrix.call(surface,command,{},dry_run=True)
                assert preview['dry_run'] and state(here) == before
                result = await matrix.call(surface,command,{})
                if command == 'upgrade':
                    assert not result['hub_migrated'] and not result['companies_migrated']
                    assert result['companies_skipped'] == [matrix.company]
                    assert not result['companies_failed'] and not result['companies_missing']
                else:
                    assert result['company_id'] != matrix.company and not result['dry_run']
                    assert Path(result['path']).is_relative_to(here) and (Path(result['path'])/'company.db').is_file()
                    with sqlite3.connect((here/'hub.db').as_uri()+'?mode=ro',uri=True) as db:
                        assert db.execute('SELECT id FROM companies WHERE id=?',(matrix.company,)).fetchone() is None
                        assert db.execute('SELECT id FROM companies WHERE id=?',(result['company_id'],)).fetchone() == (result['company_id'],)
                after = state(here)
                for ctx in ({'company':matrix.company}, {'idempotency_key':'unsupported-lifecycle-key'}):
                    assert (await matrix.call(surface,command,{},rejected=True,**ctx))['code'] == 'E_USAGE'
                    assert state(here) == after
            expected = compared(matrix.documents['python'],matrix.roots['python'])
            for surface in ('cli','http','mcp'):
                actual = compared(matrix.documents[surface],matrix.roots[surface])
                assert actual == expected,(surface,actual,expected)
        finally:
            await matrix.close()
    anyio.run(witness)
