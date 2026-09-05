"""Writer-time register translation, one outer audit, and zero-effect failures."""
import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import registers, schema as c
from bookflow.core import audit, idempotency
from bookflow.storage.engine import open_database
from tests.test_row8_atomicity import state
from tests.test_row8_journal import COMPANY, database_path
from tests.test_row8_register import register_accounts, post, edit


def test_preview_noop_closed_and_stale(client, register_accounts):
    before = state(client)
    assert post(client, register_accounts, dry_run=True)['dry_run']
    assert state(client) == before
    first = post(client, register_accounts)
    before = state(client)
    noop = client.register.update(**edit(first), company=COMPANY)
    assert not noop['changed'] and noop['receipt'] == first['receipt']
    assert state(client) == before
    client.company.update(closing_date='2026-02-28', company=COMPANY)
    before = state(client)
    for call in (lambda: post(client, register_accounts),
                 lambda: client.register.update(**dict(edit(first), date='2026-03-01'), company=COMPANY),
                 lambda: client.register.update(**edit(first), company=COMPANY)):
        with pytest.raises(BookflowError) as err:
            call()
        assert err.value.code == 'E_PERIOD_CLOSED'
        assert state(client) == before


@pytest.mark.parametrize('failure', ['audit', 'row', 'idempotency'])
@pytest.mark.parametrize('operation', ['post', 'update'])
def test_atomic_rollback_and_single_outer_audit(client, register_accounts, monkeypatch, failure, operation):
    first = post(client, register_accounts) if operation == 'update' else None
    def invoke():
        if first:
            return client.register.update(**dict(edit(first), memo='Correction'),
                                          idempotency_key='register-rollback', company=COMPANY)
        return post(client, register_accounts, idempotency_key='register-rollback')
    before = state(client)
    with monkeypatch.context() as patch:
        if failure in ('audit', 'idempotency'):
            owner, name = (audit, 'write_event_to') if failure == 'audit' else (idempotency, 'store')
            original = getattr(owner, name)
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('injected register persistence failure')
            patch.setattr(owner, name, fail)
        else:
            from sqlalchemy.engine import Connection
            original = Connection.execute
            def fail(self, statement, *args, **kwargs):
                result = original(self, statement, *args, **kwargs)
                if getattr(statement, 'is_insert', False) and getattr(getattr(statement, 'table', None), 'name', None) == 'posting_line_sources':
                    raise RuntimeError('injected register source failure')
                return result
            patch.setattr(Connection, 'execute', fail)
        with pytest.raises((RuntimeError, BookflowError)):
            invoke()
    assert state(client) == before
    saved = invoke()
    committed = state(client)
    replay = invoke()
    assert replay['idempotent_replay'] and replay['receipt'] == saved['receipt']
    assert state(client) == committed
    with open_database(database_path(client), writable=False) as db:
        events = list(db.conn.execute(sa.select(c.audit_events).where(
            c.audit_events.c.id == saved['revision']['audit_event_id'])).mappings())
    assert len(events) == 1 and events[0]['command'] == 'register ' + operation
    assert len(committed['audit_events']) == len(before['audit_events']) + 1


@pytest.mark.parametrize('change,code', [('type', 'E_VALIDATION'), ('active', 'E_INACTIVE_REFERENCE'),
                                       ('offset', 'E_INACTIVE_REFERENCE'), ('closed', 'E_PERIOD_CLOSED')])
def test_writer_revalidates_original_register_input(client, register_accounts, monkeypatch, change, code):
    before = state(client)
    original = registers.translate
    def translate(inp, s, operation):
        if s.company.write_transaction:
            if change == 'closed':
                s.company.conn.execute(c.company_info.update().values(closing_date='2026-02-28'))
            else:
                target = register_accounts[1] if change == 'offset' else register_accounts[0]
                values = {'type': 'expense'} if change == 'type' else {'active': False}
                s.company.conn.execute(c.accounts.update().where(c.accounts.c.id == target).values(**values))
        return original(inp, s, operation)
    monkeypatch.setattr(registers, 'translate', translate)
    with pytest.raises(BookflowError) as err:
        post(client, register_accounts)
    assert err.value.code == code
    assert state(client) == before


def test_writer_type_change_changes_sides_and_receipt(client, register_accounts, monkeypatch):
    original = registers.translate
    def translate(inp, s, operation):
        if s.company.write_transaction:
            s.company.conn.execute(c.accounts.update().where(c.accounts.c.id == register_accounts[0]).values(type='credit_card'))
        return original(inp, s, operation)
    monkeypatch.setattr(registers, 'translate', translate)
    saved = post(client, register_accounts)
    assert saved['receipt']['normal_balance'] == 'credit'
    assert saved['revision']['lines'][0]['side'] == 'debit'
    assert saved['revision']['lines'][1]['side'] == 'credit'


@pytest.mark.parametrize('fact', ['original_minor_units', 'original_currency', 'rate_used', 'rate_source'])
def test_foreign_fact_current_journal_rejected_before_mutation(client, register_accounts, monkeypatch, fact):
    from bookflow.company import journals
    first = post(client, register_accounts)
    before = state(client)
    original = journals.rows
    def rows(s, table, *where, **kwargs):
        found = original(s, table, *where, **kwargs)
        if table is c.document_lines:
            found = [dict(line, **{fact: 100 if fact == 'original_minor_units' else 'foreign'}) for line in found]
        return found
    monkeypatch.setattr(journals, 'rows', rows)
    with pytest.raises(BookflowError) as err:
        client.register.update(**edit(first), company=COMPANY)
    assert err.value.code == 'E_VALIDATION' and 'open_journal' in err.value.details
    assert state(client) == before


def test_writer_rejects_offset_that_now_resolves_to_selected(client, register_accounts, monkeypatch):
    from bookflow.company import accounts
    original = accounts.resolve_account
    def resolve(db, selector):
        if db.write_transaction and selector == register_accounts[1]:
            return original(db, register_accounts[0])
        return original(db, selector)
    before = state(client)
    monkeypatch.setattr(accounts, 'resolve_account', resolve)
    with pytest.raises(BookflowError) as err:
        post(client, register_accounts)
    assert err.value.code == 'E_VALIDATION'
    assert state(client) == before
