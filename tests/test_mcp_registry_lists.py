"""Registered list lifecycle execution parity on four cloned private books."""

from copy import deepcopy
import re
import sqlite3

import anyio
import pytest

import bookflow
from bookflow.core import registry
from bookflow.documentation.examples import EXAMPLES, _SUPPORTING_CREATE_INPUTS
from tests.mcp_matrix_support import Matrix, normalize

GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'
VERBS = ('create', 'show', 'list', 'query', 'update', 'deactivate', 'activate')


@pytest.mark.timeout(180)
@pytest.mark.parametrize('noun,variant', [(noun, None) for noun in sorted(_SUPPORTING_CREATE_INPUTS)] + [('custom-field', 'bool'), ('custom-field', 'text')], ids=[*sorted(_SUPPORTING_CREATE_INPUTS), 'boolean-default', 'text-false-default'])
def test_each_list_lifecycle_valid_rejected_and_preview_parity(root, tmp_path, noun, variant):
    registry.load_all()
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for row in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', row))
    raw = deepcopy(EXAMPLES[noun + ' create'].input)
    if variant:
        raw.update(kind=variant, default=False if variant == 'bool' else 'false')
    # Examples with domain-common labels may already exist in the rich demo.
    if 'name' in raw:
        raw['name'] = 'Parity ' + noun
    if noun == 'sales-tax-code':
        raw['code'] = 'PX9'
    if noun == 'sales-rep':
        setup = bookflow.connect(data_root=str(root))
        company = setup.company.list()['items'][0]['company_id']
        raw['name_id'] = setup.run('employee list', {}, company=company)['items'][0]['id']

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                create = await matrix.call(surface, noun + ' create', raw, dry_run=True)
                assert create['dry_run'] is True
                created = await matrix.call(surface, noun + ' create', raw)
                selected = {noun.replace('-', '_'): created['id']}
                inputs = {'create': raw, 'show': selected, 'list': {}, 'query': {'limit': 200},
                          'update': {**selected, 'expected_version': created['version'],
                                     **({'description': 'Parity changed'} if noun == 'sales-tax-code' else {'name': 'Parity changed ' + noun})}}
                covered = {'create'}
                for verb in ('show', 'list', 'query', 'update', 'deactivate', 'activate'):
                    command = noun + ' ' + verb
                    if verb in ('deactivate', 'activate'):
                        inputs[verb] = {**selected, 'expected_version': version}
                    if registry.get(command).is_write:
                        preview = await matrix.call(surface, command, inputs[verb], dry_run=True)
                        assert preview['dry_run'] is True
                    output = await matrix.call(surface, command, inputs[verb])
                    if verb in ('update', 'deactivate', 'activate'):
                        version = output['version']
                    covered.add(verb)
                assert covered == set(VERBS)
                for verb in VERBS:
                    error = await matrix.call(surface, noun + ' ' + verb, inputs[verb], company=GHOST, rejected=True)
                    assert error['code'] == 'E_COMPANY_NOT_FOUND'
                # A semantic domain rejection is distinct from route isolation.
                duplicate = await matrix.call(surface, noun + ' create',
                    {**raw, **({'name': 'Parity changed ' + noun} if 'name' in raw else {})}, rejected=True)
                assert duplicate['code'] == 'E_NAME_TAKEN'
                if registry.get(noun + ' update').clearable:
                    invalid_clear = {**selected, 'expected_version': version,
                                     ('code' if noun == 'sales-tax-code' else 'name'): None}
                    error = await matrix.call(surface, noun + ' update', invalid_clear, rejected=True)
                    assert error['code'] == 'E_VALIDATION'
            baseline = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                assert normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids) == baseline, surface
            counts = []
            for surface, path in matrix.roots.items():
                with sqlite3.connect((path / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((path / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
                    events = db.execute("SELECT command,interface FROM audit_events WHERE reason='Registry parity' ORDER BY seq").fetchall()
                    assert len(events) == 4, (surface, events)
                    assert all(via == surface for _, via in events)
                    counts.append([command for command, _ in events])
            assert all(count == counts[0] for count in counts)
        finally:
            await matrix.close()
    anyio.run(witness)
