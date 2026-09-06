"""Company corrections and owned folder moves retain command parity."""
import re
import sqlite3
from pathlib import Path
import anyio
import pytest
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import company_snapshot, GHOST

COMMANDS = frozenset({'company update', 'company rename'})


def clean(documents, root, ids):
    def visit(value, key=None):
        if isinstance(value, dict):
            return {k: visit(v, k) for k,v in value.items()}
        if isinstance(value, (list, tuple)):
            return [visit(v) for v in value]
        if key == 'seconds_since_previous_update' and value is not None:
            assert isinstance(value, (int, float)) and value >= 0
            return '<elapsed>'
        if key == 'previous_updated_via' and value in ('python', 'cli', 'http', 'mcp'):
            return '<interface>'
        return value
    return normalize(visit(documents), root, ids)


@pytest.mark.parametrize('command', sorted(COMMANDS))
@pytest.mark.timeout(180)
def test_company_maintenance_valid_preview_rejections_and_owned_move(root, tmp_path, command):
    ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for line in db.iterdump():
                ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                here = matrix.roots[surface]
                original = await matrix.call(surface, 'company show', {})
                raw = (dict(fax='555-0100', expected_version=original['info_version'], use_classes=False)
                       if command == 'company update' else dict(name='Renamed company — owned fixture'))
                before = company_snapshot(here)
                preview = await matrix.call(surface, command, raw, dry_run=True)
                assert preview['dry_run'] and company_snapshot(here) == before
                saved = await matrix.call(surface, command, raw)
                current = await matrix.call(surface, 'company show', {})
                assert current['id'] == original['id']
                if command == 'company update':
                    assert current['info']['fax'] == '555-0100' and current['info']['use_classes'] is False
                    assert saved['version'] == original['info_version'] + 1
                    stale = dict(raw, fax='555-0101')
                    after = company_snapshot(here)
                    rejected = await matrix.call(surface, command, stale, rejected=True)
                    assert rejected['code'] == 'E_VERSION_CONFLICT' and company_snapshot(here) == after
                else:
                    assert current['display_name'] == raw['name'] and current['path'] == original['path']
                    old_path = Path(current['path'])
                    moved = await matrix.call(surface, command, dict(name='Moved owned company', move=True))
                    assert moved['moved'] and not old_path.exists()
                    final = await matrix.call(surface, 'company show', {})
                    assert Path(final['path']).is_relative_to(here) and (Path(final['path'])/'company.db').is_file()
                    assert final['id'] == original['id'] and final['display_name'] == 'Moved owned company'
                after = company_snapshot(here)
                assert (await matrix.call(surface, command, raw, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                assert company_snapshot(here) == after
                invalid = {'email':'invalid'} if command == 'company update' else {'name':''}
                await matrix.call(surface, command, invalid, rejected=True)
                assert company_snapshot(here) == after
            expected = clean(matrix.documents['python'], matrix.roots['python'], ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = clean(matrix.documents[surface], matrix.roots[surface], ids)
                for index, (a,b) in enumerate(zip(expected,actual)):
                    assert a == b, (surface,index,a,b)
                assert len(actual) == len(expected)
        finally:
            await matrix.close()
    anyio.run(witness)
