"""Saved bills are shared, versioned records; saving never changes the books."""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.conftest import make_actor, as_user
from tests.test_row8_journal import COMPANY, database_path


@pytest.fixture
def draft_books(client):
    vendor = client.vendor.create(name='Draft supplier', company=COMPANY)['id']
    expense = client.account.create(name='Draft expense', type='expense', company=COMPANY)['id']
    def payload(**overrides):
        value = dict(kind='bill', operation='create', date='2026-06-15', vendor=vendor,
                     supplier_reference='SAVED-80', memo='Awaiting review',
                     expenses=[dict(account=expense, amount='60.25', memo='Parts'),
                               dict(account=expense, amount='19.75', memo='Freight')])
        value.update(overrides)
        return value
    def run(verb, data, **context):
        return client.run('purchase-draft ' + verb, data, company=COMPANY,
                          reason=context.pop('reason', 'Saved bill witness'), **context)
    return payload, run


def financial(client):
    with sqlite3.connect(database_path(client)) as db:
        return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                for name in ('transactions', 'transaction_revisions', 'posting_batches',
                             'posting_lines', 'purchase_expense_lines', 'ap_obligation_keys',
                             'ap_obligation_components', 'sequences')}


def test_exact_eighty_dry_run_and_save_have_no_accounting_effect(client, draft_books):
    payload, run = draft_books
    before = financial(client)
    initial = run('create', payload(operation_key='preview-eighty'), dry_run=True)
    assert initial['preview']['total'] == dict(amount='80.00', currency='USD', minor_units=8000)
    assert initial['preview']['accounting_effect'] == 'none'
    saved = run('create', payload(operation_key='save-eighty'))
    assert saved['state'] == 'draft' and saved['version'] == 1
    assert saved['preview']['total']['minor_units'] == 8000
    assert [x['amount']['minor_units'] for x in saved['preview']['expenses']] == [6025, 1975]
    preview = run('preview', dict(draft=saved['id']))
    assert preview['preview']['total']['minor_units'] == 8000
    assert financial(client) == before
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT purchase_approvals_enabled, bill_approvals_enabled, '
                          'approval_routing_enabled, approval_reminders_enabled FROM company_info').fetchone() == (0, 0, 0, 0)
    assert run('show', dict(draft=saved['id']))['payload'] == saved['payload']
    assert saved['id'] in [x['id'] for x in run('list', dict(state='draft'))['items']]


def test_another_bookkeeper_resumes_and_stale_edit_preserves_the_winner(root, client, draft_books):
    payload, run = draft_books
    saved = run('create', payload(operation_key='shared-save'))
    company = client.company.show(company=COMPANY)['company_id']
    actor_id = make_actor(root, 'draft_bookkeeper', company_role=(company, 'standard'))
    client.membership.grant(user=actor_id, company=company, expected_version=1, denies=['ledger.post'])
    other = as_user(root, 'draft_bookkeeper')
    assert other.run('purchase-draft show', dict(draft=saved['id']), company=COMPANY)['id'] == saved['id']
    before = financial(client)
    edited = other.run('purchase-draft update', payload(draft=saved['id'], expected_version=1,
                       operation_key='other-edit', memo='Resumed by another bookkeeper'),
                       company=COMPANY, reason='Continue the saved bill')
    assert edited['version'] == 2
    assert edited['history'][-1]['actor'] == actor_id
    with pytest.raises(BookflowError) as stale:
        run('update', payload(draft=saved['id'], expected_version=1,
                             operation_key='stale-edit', memo='Must not overwrite'))
    assert stale.value.code == 'E_VERSION_CONFLICT'
    assert run('show', dict(draft=saved['id'])) == edited
    assert financial(client) == before
    client.membership.grant(user=actor_id, company=company, expected_version=2, denies=['ledger.read'])
    with pytest.raises(BookflowError) as hidden:
        other.run('purchase-draft show', dict(draft=saved['id']), company=COMPANY)
    assert hidden.value.code == 'E_PERMISSION'


def test_cancel_preserves_revisions_and_cannot_be_edited(client, draft_books):
    payload, run = draft_books
    before = financial(client)
    saved = run('create', payload(operation_key='cancel-save'))
    updated = run('update', payload(draft=saved['id'], expected_version=1,
                                   operation_key='cancel-update', memo='Correction'))
    cancelled = run('cancel', dict(draft=saved['id'], expected_version=2, operation_key='cancel-it'))
    assert cancelled['state'] == 'cancelled' and cancelled['version'] == 3
    assert cancelled['history'][:2] == updated['history']
    assert [x['state'] for x in cancelled['history']] == ['draft', 'draft', 'cancelled']
    assert saved['id'] in [x['id'] for x in run('list', dict(state='cancelled'))['items']]
    with pytest.raises(BookflowError) as terminal:
        run('update', payload(draft=saved['id'], expected_version=3, operation_key='after-cancel'))
    assert terminal.value.code == 'E_APPROVAL_STATE'
    with sqlite3.connect(database_path(client)) as db:
        for statement in ('UPDATE purchase_draft_revisions SET version=version+1',
                          'DELETE FROM purchase_draft_revisions'):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(statement)
    assert run('show', dict(draft=saved['id'])) == cancelled
    assert financial(client) == before


def test_permanent_retry_replays_original_and_changed_input_refuses(draft_books):
    payload, run = draft_books
    data = payload(operation_key='permanent-save')
    first = run('create', data)
    run('update', payload(draft=first['id'], expected_version=1, operation_key='later-edit', memo='Later'))
    replay = run('create', data)
    assert replay.pop('idempotent_replay') is True
    assert replay == first
    with pytest.raises(BookflowError):
        run('create', payload(operation_key='permanent-save', memo='Different request'))
    assert run('show', dict(draft=first['id']))['version'] == 2


def test_readonly_cannot_prepare_and_unrelated_actor_cannot_read(root, client, draft_books):
    payload, run = draft_books
    saved = run('create', payload(operation_key='permission-save'))
    company = client.company.show(company=COMPANY)['company_id']
    make_actor(root, 'draft_reader', company_role=(company, 'readonly'))
    reader = as_user(root, 'draft_reader')
    with pytest.raises(BookflowError) as unreadable:
        reader.run('purchase-draft show', dict(draft=saved['id']), company=COMPANY)
    assert unreadable.value.code == 'E_PERMISSION'
    before = financial(client)
    for verb, data in [('create', payload(operation_key='reader-create')),
                       ('update', payload(draft=saved['id'], expected_version=1, operation_key='reader-update')),
                       ('cancel', dict(draft=saved['id'], expected_version=1, operation_key='reader-cancel'))]:
        with pytest.raises(BookflowError) as denied:
            reader.run('purchase-draft ' + verb, data, company=COMPANY, reason='Forbidden write')
        assert denied.value.code == 'E_PERMISSION'
    make_actor(root, 'draft_outsider')
    outsider = as_user(root, 'draft_outsider')
    for verb, data in [('show', dict(draft=saved['id'])), ('preview', dict(draft=saved['id'])), ('list', {})]:
        with pytest.raises(BookflowError) as denied:
            outsider.run('purchase-draft ' + verb, data, company=COMPANY)
        assert denied.value.code in ('E_PERMISSION', 'E_COMPANY_NOT_FOUND')
        assert saved['id'] not in str(denied.value.details)
    assert financial(client) == before


def test_predecessor_migration_preserves_existing_data_and_creates_empty_drafts(tmp_path, monkeypatch):
    path = tmp_path / 'old-company.db'
    with monkeypatch.context() as patch:
        patch.setitem(HEADS, 'company', 'co0128')
        with open_database(path, writable=True, create=True) as db:
            migrate_to_head(db, 'company', None)
            db.raw.execute('CREATE TABLE local_purchase_witness(value TEXT)')
            db.raw.execute("INSERT INTO local_purchase_witness VALUES ('preserved')")
            before = {name: db.raw.execute('SELECT * FROM ' + name).fetchall()
                      for name in ('transactions', 'posting_batches', 'posting_lines', 'audit_events')}
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', None) == ('co0128', 'co0129')
        assert db.raw.execute('SELECT * FROM local_purchase_witness').fetchall() == [('preserved',)]
        for name, rows in before.items():
            assert db.raw.execute('SELECT * FROM ' + name).fetchall() == rows
        for name in ('purchase_drafts', 'purchase_draft_revisions', 'purchase_draft_operations'):
            assert db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert migrate_to_head(db, 'company', None) == ('co0129', 'co0129')


def test_agent_needs_explicit_prepare_and_read_grants(root, client, draft_books):
    from bookflow.core.config import Config
    from tests.conftest import make_agent
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch

    class AgentSession:
        def __init__(self, root, login, *, principal):
            self.root, self.login, self.principal = root, login, principal

        def run(self, name, raw, *, company):
            ctx = Context.new(Interface.python, 'draft agent witness', on_behalf_of=self.principal,
                              reason='Prepare the supplier bill')
            return dispatch(registry.get(name), raw, ctx, data_root=str(self.root),
                            company_selector=company, company_source='option', _login=self.login)

    payload, run = draft_books
    company = client.company.show(company=COMPANY)['company_id']
    principal = make_actor(root, 'draft_principal', company_role=(company, 'standard'))
    agent = make_agent(lambda name, data: client.run(name, data), 'draft_agent',
                       principals=[principal], company=company)
    config = Config.load(root / 'config.toml')
    config.set_user('draft_agent', agent)
    config.save()
    bot = AgentSession(root, 'draft_agent', principal=principal)
    saved = run('create', payload(operation_key='human-for-agent'))
    for verb, data in [('create', payload(operation_key='agent-save')),
                       ('show', dict(draft=saved['id']))]:
        with pytest.raises(BookflowError) as denied:
            bot.run('purchase-draft ' + verb, data, company=COMPANY)
        assert denied.value.code == 'E_PERMISSION'
    client.membership.grant(user=agent, company=company, expected_version=1,
                            grants=['approval.prepare', 'approval.read'])
    made = bot.run('purchase-draft create', payload(operation_key='agent-save'), company=COMPANY)
    assert made['history'][-1]['actor'] == agent
    assert made['history'][-1]['principal'] == principal
    assert bot.run('purchase-draft show', dict(draft=made['id']), company=COMPANY)['id'] == made['id']

    client.membership.grant(user=agent, company=company, expected_version=2, grants=[])
    with pytest.raises(BookflowError) as revoked:
        bot.run('purchase-draft create', payload(operation_key='agent-save'), company=COMPANY)
    assert revoked.value.code == 'E_PERMISSION'


def test_invalid_money_and_unsupported_sources_are_atomic(client, draft_books):
    payload, run = draft_books
    before = financial(client)
    for index, amount in enumerate(('0.00', '-1.00', '1.001', 80.0, True)):
        body = payload(operation_key='invalid-money-' + str(index))
        body['expenses'][0]['amount'] = amount
        with pytest.raises(BookflowError):
            run('create', body)
    with pytest.raises(BookflowError):
        run('create', payload(operation_key='unsupported-source', purchase_order='not-a-source'))
    assert financial(client) == before
