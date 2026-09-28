"""On an activated install, a person given the delete grant in user setup can delete.

A new install starts activated (policy_v1), and no role includes Delete: the grant has to be
given to the person explicitly, with `membership grant`. This witness walks that path the way
the person or their agent does -- refused, granted, then deleting -- for every deletable
document type, over HTTP and over the actual MCP adapter, on the demo company.

The demo company has two members (its owner and the Demo Assistant agent). A grant reaches
only the member it is given to, so each surface grants the signed-in person's own membership.
"""
import asyncio
import shutil
from pathlib import Path

import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.config import os_login
from bookflow.core.deletion_families import FAMILIES, capability
from tests.mcp_matrix_support import Matrix

#: The surfaces this test drives with the real grant path.
SURFACES = ('http', 'mcp')
_GUARDS = ('expected_version', 'operation_key', 'dependency_guard', 'expected_facts_fingerprint')


def delete_commands():
    """Each deletion family's Delete command, keyed by its noun, and the input field naming it."""
    registry.load_all()
    found = {}
    for cmd in registry.all_commands(include_standalone=True):
        if cmd.requires_explicit_grant:
            noun = cmd.name.rsplit(' ', 1)[0]
            found[noun] = (cmd, next(f for f in cmd.input_model.model_fields if f not in _GUARDS))
    assert sorted(cmd.capability for cmd, _ in found.values()) == sorted(capability(f) for f in FAMILIES)
    return found


def rows(client, company, noun):
    """(id, version) of every listed document; a deposit lists its current revision nested."""
    out = []
    for row in client.run(noun + ' query', {}, company=company)['items']:
        row = row.get('current', row)
        out.append((row.get('id') or row['deposit_id'], row['version']))
    return out


def a_credit_memo(client, company):
    """The demo's one credit memo is applied; give this test an unapplied one of its own."""
    customer = client.run('customer query', {}, company=company)['items'][0]['id']
    item = next(row['id'] for row in client.run('item query', {}, company=company)['items']
                if row['type'] == 'service')
    client.run('credit-memo post', {'customer': customer, 'date': '2026-09-01', 'customer_tax_code': 'Non',
                                    'lines': [{'item': item, 'quantity': '1', 'unit_price': '10.00'}]},
               company=company, reason='A credit to remove again')


def saved_after(preview, cmd, raw):
    """The save a person makes after previewing: a deposit's carries the guard its preview gave."""
    if 'dependency_guard' in cmd.input_model.model_fields and preview.get('dependency_guard'):
        return {**raw, 'dependency_guard': preview['dependency_guard']}
    return raw


def grant_myself(client, company, grants):
    me = next(row for row in client.run('membership list', {'company': company})['items']
              if row['scope_type'] == 'company' and row['username'] == os_login())
    return me, dict(user=me['user_id'], company=company, role=me['role'], expected_version=me['version'],
                    grants=sorted(set(me['grants']) | set(grants)))


def choose(root, company, commands):
    """One deletable document per family, found by deleting it on a private copy.

    Deleting in this order on a copy proves the same sequence is deletable on every surface,
    whose roots are byte copies of the same baseline.
    """
    client = bookflow.connect(data_root=str(root))
    _, grant = grant_myself(client, company, [cmd.capability for cmd, _ in commands.values()])
    client.run('membership grant', grant, reason='Choose the documents to delete')
    chosen = {}
    for noun, (cmd, field) in commands.items():
        for record, version in rows(client, company, noun):
            raw = {field: record, 'expected_version': version, 'operation_key': 'choose-' + noun}
            try:
                preview = client.run(noun + ' delete', raw, company=company, reason='Choose', dry_run=True)
                client.run(noun + ' delete', saved_after(preview, cmd, raw), company=company, reason='Choose')
            except bookflow.BookflowError as exc:
                assert exc.code != 'E_PERMISSION', (noun, exc.to_dict())
                continue
            chosen[noun] = (record, version)
            break
        assert noun in chosen, f'the demo has no deletable {noun}'
    return chosen


@pytest.mark.timeout(600)
def test_a_granted_person_deletes_every_document_type_over_http_and_mcp(_seeded_template, tmp_path, monkeypatch):
    pytest.importorskip('mcp')
    baseline = shutil.copytree(_seeded_template, tmp_path / 'baseline')
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(baseline))
    seed = bookflow.connect(data_root=str(baseline))
    company = seed.company.list()['items'][0]['company_id']
    assert seed.run('permission show', {})['mode'] == 'policy_v1', 'a new install starts activated'
    a_credit_memo(seed, company)
    commands = delete_commands()
    chosen = choose(shutil.copytree(baseline, tmp_path / 'chooser'), company, commands)

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(Path(baseline), tmp_path / 'surfaces')
            for surface in SURFACES:
                async def call(name, raw, **ctx):
                    return await matrix.call(surface, name, raw, **ctx)

                def request(noun):
                    field = commands[noun][1]
                    record, version = chosen[noun]
                    return {field: record, 'expected_version': version, 'operation_key': surface + '-' + noun}

                # Refused before the grant, and the refusal says what is missing.
                for noun, (cmd, _) in commands.items():
                    refused = await call(noun + ' delete', request(noun), dry_run=True, rejected=True)
                    assert refused['code'] == 'E_PERMISSION', (surface, noun, refused)
                    assert f'explicit {cmd.capability} grant' in refused['message'], refused

                rows_before = (await call('membership list', {'company': company}))['items']
                me = next(row for row in rows_before if row['username'] == os_login()
                          and row['scope_type'] == 'company')
                granted = await call('membership grant', dict(
                    user=me['user_id'], company=company, role=me['role'], expected_version=me['version'],
                    grants=sorted(cmd.capability for cmd, _ in commands.values())))
                assert granted['role'] == me['role']

                for noun, (cmd, _) in commands.items():
                    preview = await call(noun + ' delete', request(noun), dry_run=True)
                    deleted = await call(noun + ' delete', saved_after(preview, cmd, request(noun)))
                    assert deleted['status'] == 'deleted', (surface, noun, deleted)
                    field = commands[noun][1]
                    shown = await call(noun + ' show', {field: chosen[noun][0], 'include_deleted': True})
                    assert shown.get('status', shown.get('current', {}).get('status')) == 'deleted', (surface, noun)
        finally:
            await matrix.close()
    asyncio.run(witness())
