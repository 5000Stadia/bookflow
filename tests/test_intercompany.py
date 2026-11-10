"""R123 increment 1: isolated setup and untargeted work never post money."""
import json
import sqlite3
from pathlib import Path

import pytest

SURFACES = ('python',)
COMMANDS = frozenset(('intercompany-settings show', 'intercompany-settings update', 'intercompany-draft create', 'intercompany-draft update', 'intercompany-draft show', 'intercompany-draft list', 'intercompany-mapping set', 'intercompany-relationship create', 'intercompany-relationship update', 'intercompany-relationship show', 'intercompany-relationship list'))

from bookflow.core.errors import BookflowError
from bookflow.core.audit import decode_snapshot
from bookflow.core import registry


@pytest.fixture
def books(tmp_path, monkeypatch):
    from tests.crm_support import company
    return company(tmp_path, monkeypatch, 'UTC')


def call(books, command, raw=None, *, company=None, **context):
    cmd = registry.get(command)
    return books.client.run(command, raw or {},
        **({'company': company or books.company} if cmd.scope == 'company' else {}),
        **({'reason': 'Intercompany isolation witness'} if cmd.is_write else {}), **context)


def another(books, name='Counterpart hidden', currency='USD', organization='CRM organization'):
    return books.client.company.new(legal_name=name, home_currency=currency, timezone='UTC',
        organization=organization, chart='none')['company_id']


def enable(books, company=None, enabled=True):
    version = call(books, 'intercompany-settings show', company=company)['version']
    return call(books, 'intercompany-settings update', {'expected_version': version,
        'linked_entries_enabled': enabled}, company=company)


def accounts(books, company=None, prefix='Control'):
    return tuple(call(books, 'account create', {'name': prefix + suffix, 'type': kind}, company=company)['id']
                 for suffix, kind in ((' receivable', 'other_current_asset'), (' payable', 'other_current_liability')))


def mapping(books, peer, mapped, company=None, version=0):
    return call(books, 'intercompany-mapping set', {'peer': peer, 'due_from': mapped[0],
        'due_to': mapped[1], 'expected_version': version}, company=company)


def pair(books):
    peer = another(books)
    enable(books)
    enable(books, peer)
    local, remote = accounts(books), accounts(books, peer, 'Peer private')
    first = mapping(books, peer, local)
    second = mapping(books, books.company, remote, peer)
    relation = call(books, 'intercompany-relationship create', {'peer': peer})
    return peer, local, remote, first, second, relation


def path(books, company=None):
    return Path(call(books, 'company show', company=company)['path']) / 'company.db'


def rows(dbpath, sql, args=()):
    with sqlite3.connect(dbpath) as db:
        return db.execute(sql, args).fetchall()


def rejected(fn, code=None):
    with pytest.raises(BookflowError) as exc:
        fn()
    if code:
        assert exc.value.code == code, exc.value
    return {'code': exc.value.code, 'message': exc.value.message, 'details': exc.value.details}


def test_relationship_lifecycle_local_drafts_and_audit_have_no_ledger_effect(books):
    peer, local, remote, first, second, relation = pair(books)
    a, b = path(books), path(books, peer)
    assert relation['peer_id'] == peer and relation['enabled'] and relation['version'] == 1
    assert relation['local_mapping_digest'] == first['digest']
    assert relation['peer_mapping_digest'] == second['digest']
    shown = call(books, 'intercompany-relationship show', {'peer': books.company,
        'relationship': relation['id']}, company=peer)
    assert shown['local_mapping_digest'] == second['digest']
    assert call(books, 'intercompany-relationship list')['items'] == [relation]
    draft = call(books, 'intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': 10000,
        'memo': 'Proposal only'}, idempotency_key='local-proposal')
    replay = call(books, 'intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': 10000,
        'memo': 'Proposal only'}, idempotency_key='local-proposal')
    assert replay['id'] == draft['id'] and replay['idempotent_replay']
    updated = call(books, 'intercompany-draft update', {'draft': draft['id'], 'expected_version': 1,
        'date': '2026-11-02', 'amount_minor_units': 2500})
    assert updated['version'] == 2 and updated['created_by'] == draft['created_by']
    assert call(books, 'intercompany-draft show', {'draft': draft['id']}) == updated
    assert call(books, 'intercompany-draft list')['items'] == [updated]
    rejected(lambda: call(books, 'intercompany-draft update', {'draft': draft['id'], 'expected_version': 1,
        'date': '2026-11-02', 'amount_minor_units': 2500}), 'E_VERSION_CONFLICT')
    disabled = call(books, 'intercompany-relationship update', {'peer': peer, 'relationship': relation['id'],
        'expected_version': 1, 'enabled': False})
    restored = call(books, 'intercompany-relationship update', {'peer': peer, 'relationship': relation['id'],
        'expected_version': 2, 'enabled': True})
    assert not disabled['enabled'] and restored['enabled'] and restored['id'] == relation['id']
    assert rows(books.root / 'hub.db', 'SELECT count(*) FROM intercompany_relationship_revisions') == [(3,)]
    for dbpath in (a, b):
        assert rows(dbpath, 'SELECT count(*) FROM posting_lines') == [(0,)]
        assert rows(dbpath, 'SELECT count(*) FROM posting_batches') == [(0,)]
        assert rows(dbpath, 'SELECT count(*) FROM transactions') == [(0,)]
    assert peer.encode() not in a.read_bytes() and books.company.encode() not in b.read_bytes()
    assert rows(b, 'SELECT count(*) FROM intercompany_drafts') == [(0,)]
    assert rows(b, "SELECT count(*) FROM audit_events WHERE command LIKE 'intercompany-draft %'") == [(0,)]
    for dbpath, forbidden in ((a, [peer, *remote]), (b, [books.company, *local])):
        evidence = json.dumps(rows(dbpath, "SELECT e.command,e.summary,e.reason,a.after FROM audit_events e JOIN audit_entries a ON a.event_id=e.id WHERE e.command LIKE 'intercompany-%'"), default=decode_snapshot)
        assert all(value not in evidence for value in forbidden), evidence
    registry = json.dumps(rows(books.root / 'hub.db', 'SELECT * FROM intercompany_relationships'))
    assert all(identifier not in registry for identifier in (*local, *remote))


def test_disabled_mismatched_duplicate_and_owned_accounts_refuse_atomically(books):
    peer = another(books)
    local, remote = accounts(books), accounts(books, peer)
    assert call(books, 'intercompany-settings show') == {'version': 0, 'linked_entries_enabled': False,
        'templates_enabled': False, 'bulk_enabled': False}
    rejected(lambda: call(books, 'intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': 100}), 'E_FEATURE_DISABLED')
    rejected(lambda: mapping(books, peer, local), 'E_FEATURE_DISABLED')
    enable(books)
    rejected(lambda: mapping(books, peer, local), 'E_FEATURE_DISABLED')
    enable(books, peer)
    rejected(lambda: mapping(books, peer, remote), 'E_RECORD_NOT_FOUND')
    rejected(lambda: mapping(books, peer, tuple(reversed(local))), 'E_VALIDATION')
    used = accounts(books, prefix='Historical')
    bank = call(books, 'account create', {'name': 'Ordinary bank', 'type': 'bank'})['id']
    def journal(amount, backwards=False):
        return call(books, 'journal post', {'date': '2026-11-01', 'lines': [
            {'account': used[0], 'side': 'credit' if backwards else 'debit', 'amount': amount},
            {'account': bank, 'side': 'debit' if backwards else 'credit', 'amount': amount}]})
    journal('1.00')
    rejected(lambda: mapping(books, peer, used), 'E_VALIDATION')
    journal('1.00', True)
    assert rows(path(books), 'SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?', (used[0],)) == [(0,)]
    rejected(lambda: mapping(books, peer, used), 'E_VALIDATION')
    mapping(books, peer, local)
    mapping(books, books.company, remote, peer)
    relation = call(books, 'intercompany-relationship create', {'peer': peer})
    rejected(lambda: call(books, 'intercompany-relationship create', {'peer': peer}), 'E_VALIDATION')
    rejected(lambda: call(books, 'intercompany-relationship create', {'peer': books.company}, company=peer), 'E_VALIDATION')
    third = another(books, 'Second permitted counterpart')
    enable(books, third)
    rejected(lambda: mapping(books, third, local), 'E_VALIDATION')
    foreign = another(books, 'Different currency', 'EUR')
    enable(books, foreign)
    rejected(lambda: mapping(books, foreign, accounts(books, prefix='Other currency')), 'E_VALIDATION')
    books.client.organization.new(name='Separate organization')
    outside = another(books, 'Separate books', organization='Separate organization')
    rejected(lambda: mapping(books, outside, local), 'E_VALIDATION')
    for bad in (0, -1, 1.5, True, 2**63):
        rejected(lambda: call(books, 'intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': bad}), 'E_VALIDATION')
    rejected(lambda: call(books, 'intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': 100,
        'currency': 'EUR'}), 'E_VALIDATION')
    dbpath = path(books)
    ledger_before = rows(dbpath, 'SELECT * FROM posting_lines')
    rejected(lambda: call(books, 'journal post', {'date': '2026-11-01', 'lines': [
        {'account': local[0], 'side': 'debit', 'amount': '1.00'}, {'account': bank, 'side': 'credit', 'amount': '1.00'}]}))
    rejected(lambda: call(books, 'account merge', {'merged': used[0], 'into': local[0]}))
    rejected(lambda: call(books, 'account update', {'account': local[0], 'type': 'other_asset'}))
    assert rows(dbpath, 'SELECT * FROM posting_lines') == ledger_before
    assert rows(books.root / 'hub.db', 'SELECT id FROM intercompany_relationships') == [(relation['id'],)]
    enable(books, enabled=False)
    assert call(books, 'intercompany-relationship show', {'peer': peer, 'relationship': relation['id']})['id'] == relation['id']
    rejected(lambda: call(books, 'intercompany-relationship create', {'peer': peer}), 'E_FEATURE_DISABLED')


def test_one_company_actor_and_agent_principal_do_not_discover_peer(books, monkeypatch):
    from bookflow.core import dispatch
    from bookflow.core.ids import new_id
    from tests.test_scheduling import member
    from tests.conftest import as_user
    from tests.test_scheduling_authority import local_agent, run_as
    peer, local, remote, first, second, relation = pair(books)
    a, b = path(books), path(books, peer)
    event = rows(a, "SELECT id FROM audit_events WHERE command='intercompany-mapping set'")[0][0]
    uid, only_a = member(books, 'local_worker', role='admin')
    local_client = as_user(books.root, 'local_worker')
    opened = []
    original = dispatch.open_company
    def observe(session, ctx, writable):
        opened.append(session.company_row['id'])
        return original(session, ctx, writable)
    monkeypatch.setattr(dispatch, 'open_company', observe)
    draft = only_a('intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': 10000}, reason='Local proposal')
    assert draft['amount_minor_units'] == 10000
    projection = only_a('intercompany-relationship list')
    assert projection == {'items': [], 'next_cursor': None}
    unknown = new_id()
    for command, payload in (
        ('intercompany-relationship create', {}),
        ('intercompany-relationship show', {'relationship': relation['id']}),
        ('intercompany-mapping set', {'due_from': local[0], 'due_to': local[1], 'expected_version': 1}),
    ):
        denied = rejected(lambda: only_a(command, dict(payload, peer=peer), **({'reason': 'Try unavailable counterpart'} if registry.get(command).is_write else {})))
        missing = rejected(lambda: only_a(command, dict(payload, peer=unknown), **({'reason': 'Try unavailable counterpart'} if registry.get(command).is_write else {})))
        assert denied == missing and denied['code'] == 'E_COMPANY_NOT_FOUND'
    evidence = [draft, projection, only_a('intercompany-draft list'), only_a('audit list', {'limit': 1000}),
                only_a('audit tail', {'after': 0, 'limit': 1000}),
                local_client.run('hub audit list', {'command': 'intercompany-relationship create'})]
    assert only_a('audit list', {'command': 'intercompany-mapping set'})['count'] == 0
    hidden = rejected(lambda: only_a('audit show', {'event': event}), 'E_EVENT_NOT_FOUND')
    absent = rejected(lambda: only_a('audit show', {'event': unknown}), 'E_EVENT_NOT_FOUND')
    assert hidden == absent
    rejected(lambda: only_a('undo', {'event_id': event}, reason='Attempt hidden undo'), 'E_EVENT_NOT_FOUND')
    rejected(lambda: only_a('note list', {'record_type': 'audit_event', 'record_id': event}), 'E_RECORD_NOT_FOUND')
    serialized = json.dumps(evidence)
    for secret in (peer, 'Counterpart hidden', *remote, first['id'], relation['id']):
        assert secret not in serialized
    assert peer not in opened
    assert rows(b, 'SELECT count(*) FROM intercompany_drafts') == [(0,)]
    _, denied_actor = member(books, 'denied_worker', denies=['intercompany.post'])
    rejected(lambda: denied_actor('intercompany-draft create', {'date': '2026-11-01', 'amount_minor_units': 100}, reason='Denied draft'), 'E_PERMISSION')
    agent = local_agent(books, 'local_helper', 'local-helper-login', uid)
    body = {'date': '2026-11-01', 'amount_minor_units': 100}
    execute = lambda: run_as(books, 'local-helper-login', 'intercompany-draft create', body,
                             on_behalf_of=uid, reason='Delegated local proposal')
    rejected(execute, 'E_PERMISSION')
    with sqlite3.connect(books.root / 'hub.db') as db:
        version = db.execute('SELECT version FROM memberships WHERE user_id=? AND scope_id=?', (agent, books.company)).fetchone()[0]
    books.client.run('membership grant', {'user': agent, 'company': books.company, 'role': 'standard',
        'expected_version': version, 'grants': ['intercompany.post', 'ledger.post']})
    authority = books.client.run('agent show', {'agent': agent})['authority']
    if authority['suspended']:
        books.client.run('agent authorize', {'agent': agent, 'confirm_permitted_use': True,
            'acknowledge_fresh_context': True, 'expected_version': authority['version']})
    made = execute()
    assert made['created_by'] == agent
    # The agent itself holds both scopes, but its principal still holds only A.
    # Explicit grants cannot turn that actor's B membership into delegated visibility.
    grants = ['intercompany.post', 'ledger.post', 'intercompany.manage', 'intercompany.read',
              'ledger.read', 'account', 'company']
    for company_id in (books.company, peer):
        with sqlite3.connect(books.root / 'hub.db') as db:
            current = db.execute('SELECT version FROM memberships WHERE user_id=? AND scope_id=?', (agent, company_id)).fetchone()
        books.client.run('membership grant', {'user': agent, 'company': company_id, 'role': 'admin',
            'grants': grants, 'expected_version': current[0] if current else 0})
    authority = books.client.run('agent show', {'agent': agent})['authority']
    if authority['suspended']:
        books.client.run('agent authorize', {'agent': agent, 'confirm_permitted_use': True,
            'acknowledge_fresh_context': True, 'expected_version': authority['version']})
    rejected(lambda: run_as(books, 'local-helper-login', 'intercompany-mapping set',
        {'peer': peer, 'due_from': local[0], 'due_to': local[1], 'expected_version': 1},
        on_behalf_of=uid, reason='Principal has only local authority'), 'E_COMPANY_NOT_FOUND')
    assert peer not in opened
    # Narrow the principal directly as an authority-fixture change; keep the agent credential alive to
    # demonstrate intersection itself, rather than an earlier suspended-credential refusal.
    with sqlite3.connect(books.root / 'hub.db') as db:
        db.execute('UPDATE memberships SET denies=? WHERE user_id=? AND scope_id=?',
                   (json.dumps(['intercompany.post']), uid, books.company))
    rejected(execute, 'E_PERMISSION')
    assert rows(a, 'SELECT count(*) FROM intercompany_drafts') == [(2,)]
    assert rows(a, 'SELECT count(*) FROM posting_lines') == [(0,)]


def test_existing_company_and_hub_migrate_intact_with_layers_off(books, monkeypatch):
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import HEADS, migrate_to_head
    customer = call(books, 'customer create', {'name': 'Existing local customer'})
    account = call(books, 'account create', {'name': 'Existing balance account', 'type': 'bank'})
    company_path = path(books)
    hub_path = books.root / 'hub.db'
    for chain, dbpath, previous, revision, tables, preserved in (
        ('company', company_path, 'co0134', 'co0135',
         ('intercompany_drafts', 'intercompany_mappings', 'intercompany_settings'),
         ('accounts', 'customers', 'audit_events', 'audit_entries', 'posting_lines')),
        ('hub', hub_path, 'hub0017', 'hub0018',
         ('intercompany_relationship_revisions', 'intercompany_relationships', 'intercompany_identity'),
         ('users', 'organizations', 'companies', 'memberships', 'audit_events', 'audit_entries')),
    ):
        real_head = HEADS[chain]
        with sqlite3.connect(dbpath) as db:
            before = {table: db.execute('SELECT * FROM ' + table).fetchall() for table in preserved}
            db.execute('PRAGMA foreign_keys=OFF')
            triggers = db.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'intercompany_%'").fetchall()
            for (trigger,) in triggers:
                db.execute('DROP TRIGGER ' + trigger)
            for table in tables:
                db.execute('DROP TABLE ' + table)
            db.execute('UPDATE alembic_version SET version_num=?', (previous,))
        with monkeypatch.context() as patch:
            patch.setitem(HEADS, chain, revision)
            with open_database(dbpath, writable=True) as db:
                assert migrate_to_head(db, chain, None) == (previous, revision)
                for table, records in before.items():
                    assert db.raw.execute('SELECT * FROM ' + table).fetchall() == records
                for table in tables:
                    assert db.raw.execute('SELECT count(*) FROM ' + table).fetchone() == ((1,) if table == 'intercompany_identity' else (0,))
                assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        with sqlite3.connect(dbpath) as db:
            db.execute('UPDATE alembic_version SET version_num=?', (real_head,))
    assert call(books, 'customer show', {'customer': customer['id']})['id'] == customer['id']
    assert call(books, 'account show', {'account': account['id']})['id'] == account['id']
    assert call(books, 'intercompany-settings show') == {'version': 0, 'linked_entries_enabled': False,
        'templates_enabled': False, 'bulk_enabled': False}


def test_local_drafts_cross_cli_http_mcp_and_generic_workbench_form(books, tmp_path):
    """Child CLI/MCP, hosted JSON, and cookie HTML form routes share the local command owner."""
    import anyio
    from fastapi.testclient import TestClient
    from bookflow.core.config import os_login
    from tests.mcp_matrix_support import Matrix
    from tests.test_scheduling import member

    peer, local, remote, first, second, relation = pair(books)
    company_path = path(books).relative_to(books.root)
    login, password = os_login(), 'Intercompany-form-test-2026'
    books.client.run('user set-password', {'username': login, 'password': password})
    local_user, _ = member(books, 'transport_local_worker', role='admin')
    local_token = books.client.run('token issue', {'user': local_user, 'label': 'Local projection witness'})['secret']

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(books.root, tmp_path / 'intercompany-transports')
            # Matrix opens with the first listed company; the test explicitly selects its local side.
            matrix.company = books.company
            discovered = await matrix.mcp.list_tools()
            assert {'bookflow_list_commands', 'bookflow_help', 'bookflow_run'} <= {tool.name for tool in discovered.tools}
            listed = await matrix.mcp.call_tool('bookflow_list_commands', {'prefix': 'intercompany-draft'})
            assert not listed.is_error
            assert 'intercompany-draft create' in {item['name'] for item in listed.structured_content['commands']}
            help_reply = await matrix.mcp.call_tool('bookflow_help', {'command': 'intercompany-draft create', 'view': 'full'})
            assert not help_reply.is_error
            help_doc = help_reply.structured_content
            properties = help_doc['input_schema']['properties']
            assert properties['amount_minor_units']['type'] == 'integer'
            assert 'peer' not in properties and 'relationship' not in properties
            assert 'untargeted' in help_doc['documentation'].lower()

            for surface in ('cli', 'http', 'mcp'):
                before = await matrix.call(surface, 'intercompany-settings show', {})
                assert before['linked_entries_enabled']
                changed = await matrix.call(surface, 'intercompany-settings update', {
                    'expected_version': before['version'], 'linked_entries_enabled': True,
                    'templates_enabled': False, 'bulk_enabled': False})
                assert changed['version'] == before['version'] + 1
                made = await matrix.call(surface, 'intercompany-draft create', {
                    'date': '2026-11-01', 'amount_minor_units': 10000, 'memo': surface + ' local proposal'})
                shown = await matrix.call(surface, 'intercompany-draft show', {'draft': made['id']})
                assert shown == made and shown['amount_minor_units'] == 10000
                refused = await matrix.call(surface, 'intercompany-draft create', {
                    'date': '2026-11-01', 'amount_minor_units': 0}, rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert peer not in json.dumps(made)

            projected = await matrix.call('http', 'intercompany-relationship list', {})
            assert [item['id'] for item in projected['items']] == [relation['id']]
            assert projected['items'][0]['peer_id'] == peer
            assert all(identifier not in json.dumps(projected) for identifier in remote)
            host = matrix.hosts['http']
            restricted = host.api.post(f'/companies/{books.company}/commands/intercompany-relationship.list',
                json={}, headers={'Authorization': 'Bearer ' + local_token})
            assert restricted.status_code == 200, restricted.text
            assert restricted.json() == {'items': [], 'next_cursor': None}
            assert peer not in restricted.text and 'Counterpart hidden' not in restricted.text

            # Exercise the generic browser route and its form translation with a cookie session.
            # This is an HTTP form-route witness, not a Chromium rendering or interaction claim.
            browser = TestClient(host.handle.app)
            logged_in = browser.post('/login', json={'username': login, 'password': password})
            assert logged_in.status_code == 200, logged_in.text
            form_path = f'/c/{books.company}/intercompany-draft/create'
            page = browser.get(form_path)
            assert page.status_code == 200, page.text[:1000]
            assert 'hx-post=' in page.text and 'name="f:amount_minor_units"' in page.text
            saved = browser.post(form_path, data={'f:date': '2026-11-02', 'f:amount_minor_units': '2500',
                'f:memo': 'Cookie form local proposal', 'ctx:reason': 'Save local proposal from form'},
                headers={'X-Bookflow-Workbench': '1', 'HX-Request': 'true'}, follow_redirects=False)
            assert saved.status_code in (200, 303), saved.text[:1000]
            drafts = await matrix.call('http', 'intercompany-draft list', {})
            assert any(item['memo'] == 'Cookie form local proposal' and item['amount_minor_units'] == 2500
                       for item in drafts['items'])
            for surface in ('cli', 'http', 'mcp'):
                assert rows(matrix.roots[surface] / company_path, 'SELECT count(*) FROM posting_lines') == [(0,)]
        finally:
            await matrix.close()
    anyio.run(witness)
