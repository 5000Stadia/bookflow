"""Restoring a deleted document: a new document, posted through its own writer, linked by audit.

Deletion cancels and keeps the facts; restoration reads those facts and posts them again as a
new document. Nothing about the deleted document changes -- it stays deleted, with its number,
revisions, lines and cancellation -- and the books come back to exactly where they stood
before the deletion.
"""
from pathlib import Path
import sqlite3

import pytest

import bookflow
from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import database

COMMANDS = frozenset(('journal restore', 'invoice restore'))
SURFACES = ('python', 'http')
GRANTS = ['transaction.journal_entry.delete', 'transaction.invoice.delete']
RETAINED = ('transaction_revisions', 'document_lines', 'posting_batches', 'posting_lines',
            'journal_deletions', 'sales_deletions')


@pytest.fixture(scope='module')
def ledger(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        root = tmp_path_factory.mktemp('restore-deleted')
        patch.setenv('BOOKFLOW_DATA_ROOT', str(root))
        patch.delenv('BOOKFLOW_COMPANY', raising=False)
        client = bookflow.connect(data_root=str(root))
        client.init()
        client.organization.new(name='Restore organization')
        company = client.company.new(legal_name='Restore', home_currency='USD', timezone='UTC',
            organization='Restore organization', chart='general')['company_id']
        rows = client.account.query(company=company, limit=200)['items']
        bank = next(row['id'] for row in rows if row['type'] == 'bank')
        income = next(row['id'] for row in rows if row['type'] == 'income')
        freight = client.account.create(company=company, name='Freight In', type='expense')['id']
        customer = client.customer.create(company=company, name='Adams Plumbing')['id']

        def run(name, raw, **context):
            return client.run(name, raw, company=company, **context)

        exempt = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
        service = run('item create', dict(name='Drain clearing', type='service', sales_enabled=True,
            description='Drain clearing', sales_tax_code_id=exempt, income_account_id=income, price='95'))['id']
        terms = {row['name']: row['id'] for row in client.term.query(company=company, limit=50)['items']}
        state = client.permission.show()
        client.permission.activate(expected_generation=state['generation'],
                                   expected_catalog_sha256=state['catalog_sha256'])
        member = next(row for row in client.membership.list(company=company)['items']
                      if row['scope_type'] == 'company' and row['scope_id'] == company)
        client.membership.grant(user=member['user_id'], company=company, role='owner',
                                expected_version=member['version'], grants=GRANTS, denies=[])
        yield dict(client=client, company=company, bank=bank, income=income, freight=freight,
                   customer=customer, service=service, terms=terms, run=run)


def location(ledger):
    return Path(ledger['client'].company.show(company=ledger['company'])['path']) / 'company.db'


def net(ledger):
    rows = ledger['run']('report general-ledger', {'date_from': '2017-01-01', 'date_to': '2017-12-31', 'limit': 200})['rows']
    totals = {}
    for line in rows:
        if line['kind'] == 'posting':
            totals[line['account_id']] = totals.get(line['account_id'], 0) + (
                line['debit']['minor_units'] - line['credit']['minor_units'])
    return {account: value for account, value in totals.items() if value}


def retained(path, identity):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f'SELECT * FROM {table} WHERE transaction_id=? ORDER BY rowid', (identity,)).fetchall()
                for table in RETAINED}


def events(ledger, identity):
    with sqlite3.connect(location(ledger)) as db:
        return [row[0] for row in db.execute(
            'SELECT DISTINCT e.command FROM audit_events e JOIN audit_entries a ON a.event_id=e.id '
            'WHERE a.record_id=? ORDER BY e.seq', (identity,))]


IDENTITY = {'id', 'transaction_id', 'revision_id', 'line_id', 'document_line_id', 'created_at', 'created_by', 'created_via'}


def lines(document):
    """Every fact a line carries except its own identity and when it was written."""
    return [{k: v for k, v in line.items() if k not in IDENTITY} for line in document['revision']['lines']]


def test_journal_entry_deleted_then_restored_returns_the_books(ledger):
    run, path = ledger['run'], location(ledger)
    before_post = net(ledger)
    post = run('journal post', dict(date='2017-03-03', memo='Carrier invoice 4411', lines=[
        dict(account=ledger['freight'], side='debit', amount='40.00', description='Freight'),
        dict(account=ledger['bank'], side='credit', amount='40.00')]), reason='Enter the entry')
    posted = net(ledger)
    run('journal delete', dict(journal=post['id'], expected_version=post['version']), reason='Entered by mistake')
    assert net(ledger) == before_post
    kept = retained(path, post['id'])

    untouched = database(path)
    preview = run('journal restore', dict(journal=post['id']), reason='Was right after all', dry_run=True)
    assert preview['changed'] and preview['deleted_id'] == post['id'] and preview['date'] == '2017-03-03'
    assert preview['total_minor_units'] == 4000 and database(path) == untouched

    done = run('journal restore', dict(journal=post['id']), reason='Was right after all')
    assert done['restored_id'] != post['id'] and done['restored_number'] != post['number']
    assert net(ledger) == posted
    restored = run('journal show', dict(journal=done['restored_id']))
    original = run('journal show', dict(journal=post['id'], include_deleted=True))
    assert restored['status'] == 'posted' and original['status'] == 'deleted'
    assert lines(restored) == lines(original) and restored['revision']['memo'] == 'Carrier invoice 4411'
    # The deleted entry is exactly as the deletion left it; history is never rewritten.
    assert retained(path, post['id']) == kept
    assert events(ledger, post['id']) == ['journal post', 'journal delete', 'journal restore']
    audit = run('audit list', dict(limit=50))
    commands = [row['command'] for row in audit['items']]
    assert 'journal delete' in commands and 'journal restore' in commands

    after = database(path)
    again = run('journal restore', dict(journal=post['id']), reason='Clicked twice')
    assert again['idempotent_replay'] and not again['changed'] and again['restored_id'] == done['restored_id']
    assert database(path) == after


def test_invoice_deleted_then_restored_returns_the_books(ledger):
    run, path = ledger['run'], location(ledger)
    before_post = net(ledger)
    post = run('invoice post', dict(customer=ledger['customer'], date='2017-04-05', terms=ledger['terms']['Net 30'],
        memo='Kitchen and basement', customer_purchase_order='PO-77', lines=[
            dict(item=ledger['service'], quantity='2', description='Kitchen drain'),
            dict(item=ledger['service'], quantity='1', unit_price='120.50', description='Basement drain')]),
        reason='Bill the job')
    posted = net(ledger)
    run('invoice delete', dict(invoice=post['id'], expected_version=post['version']), reason='Wrong customer, I thought')
    assert net(ledger) == before_post
    kept = retained(path, post['id'])

    done = run('invoice restore', dict(invoice=post['id']), reason='It was the right customer')
    assert done['family'] == 'invoice' and done['total_minor_units'] == 31050 and done['left_out'] == []
    assert net(ledger) == posted
    restored = run('invoice show', dict(invoice=done['restored_id']))
    original = run('invoice show', dict(invoice=post['id'], include_deleted=True))
    # Same facts line for line. Only `origins` differs: the restore entered every value
    # explicitly, where the original took the item's defaults.
    plain = lambda document: [dict(line, item_snapshot={k: v for k, v in line['item_snapshot'].items() if k != 'origins'})
                              for line in lines(document)]
    assert plain(restored) == plain(original)
    for key in ('customer_id', 'date', 'due_date', 'memo', 'subtotal_minor_units', 'tax_minor_units', 'total_minor_units'):
        assert restored[key] == original[key], key
    drop = lambda profile: {k: v for k, v in profile.items() if k not in ('origins', 'tax_policy_origin')}
    assert drop(restored['revision']['profile']) == drop(original['revision']['profile'])
    assert retained(path, post['id']) == kept
    assert events(ledger, post['id']) == ['invoice post', 'invoice delete', 'invoice restore']
    after = database(path)
    assert run('invoice restore', dict(invoice=post['id']), reason='Again')['idempotent_replay']
    assert database(path) == after


def test_only_a_deleted_document_is_restored(ledger):
    run = ledger['run']
    post = run('journal post', dict(date='2017-05-01', memo='Live', lines=[
        dict(account=ledger['freight'], side='debit', amount='5.00'),
        dict(account=ledger['bank'], side='credit', amount='5.00')]), reason='Enter')
    with pytest.raises(BookflowError) as refused:
        run('journal restore', dict(journal=post['id']), reason='Not deleted')
    assert refused.value.code == 'E_VALIDATION' and 'not deleted' in refused.value.message


def test_an_agent_is_refused(ledger, monkeypatch):
    """The people-only gate itself; the transport witness is in the office test below."""
    from types import SimpleNamespace
    from bookflow.company import restorations
    with pytest.raises(BookflowError) as refused:
        restorations.admit(SimpleNamespace(actor=SimpleNamespace(kind='agent', id='x')), 'journal_entry')
    assert refused.value.code == 'E_PERMISSION' and refused.value.details['reason'] == 'people_only'


def test_restore_into_a_closed_period_is_refused(ledger):
    """Last in this module: it closes the books through 2017-06-30."""
    run, path = ledger['run'], location(ledger)
    post = run('journal post', dict(date='2017-06-10', memo='June freight', lines=[
        dict(account=ledger['freight'], side='debit', amount='12.00'),
        dict(account=ledger['bank'], side='credit', amount='12.00')]), reason='Enter')
    run('journal delete', dict(journal=post['id'], expected_version=post['version']), reason='Duplicate')
    run('company update', dict(closing_date='2017-06-30'), reason='Close June')
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        run('journal restore', dict(journal=post['id']), reason='Bring it back')
    assert refused.value.code == 'E_PERIOD_CLOSED' and 'date' in refused.value.details['next']
    assert database(path) == before
    # At an open date it posts, and says where.
    done = run('journal restore', dict(journal=post['id'], date='2017-07-01'), reason='Bring it back in July')
    assert done['date'] == '2017-07-01' and done['total_minor_units'] == 1200


def test_an_agent_is_refused_over_the_transport(office, books):
    from tests.conftest import make_agent
    from bookflow.core.config import Config
    company = books['company']
    principal = Config.load(office.root / 'config.toml').user_table(office.login)['user_id']
    agent = make_agent(lambda name, body: office.admin(name.replace(' ', '.'), body), 'restore-agent',
                       principals=principal, company=company)
    post = office.admin('journal.post', dict(date='2017-01-02', memo='Agent witness', lines=[
        dict(account=books['freight'], side='debit', amount='9.00'),
        dict(account=books['bank'], side='credit', amount='9.00')]), company=company,
        headers={'X-Bookflow-Reason': 'Enter'})
    state = office.admin('permission.show')
    office.admin('permission.activate', dict(expected_generation=state['generation'],
                                             expected_catalog_sha256=state['catalog_sha256']))
    memberships = office.admin('membership.list', dict(company=company))['items']
    for user in (principal, agent):
        member = next(x for x in memberships if x['user_id'] == user and x['scope_type'] == 'company')
        office.admin('membership.grant', dict(user=user, company=company, role=member['role'],
            expected_version=member['version'], grants=GRANTS, denies=[]))
    office.admin('agent.authorize', dict(agent=agent, confirm_permitted_use=True, acknowledge_fresh_context=True))
    office.admin('journal.delete', dict(journal=post['id'], expected_version=post['version']), company=company,
                 headers={'X-Bookflow-Reason': 'Duplicate'})
    issued = office.admin('token.issue', dict(user=agent, principal=principal, label='Restore witness'))
    bearer = office.browser()
    path = Path(office.admin('company.show', company=company)['path']) / 'company.db'
    before = database(path)
    refused = office.call(bearer, 'journal.restore', dict(journal=post['id']), company=company,
        headers={'Authorization': 'Bearer ' + issued['secret'], 'X-Bookflow-Reason': 'Agent tries'})
    assert refused.json()['code'] == 'E_PERMISSION', refused.text
    assert 'person' in refused.json()['message']
    assert database(path) == before
    # The person it acts for may.
    done = office.admin('journal.restore', dict(journal=post['id']), company=company,
                        headers={'X-Bookflow-Reason': 'Person restores'})
    assert done['deleted_id'] == post['id']
    # And an invoice, over the same transport.
    exempt = next(row['id'] for row in office.admin('sales-tax-code.list', {}, company=company)['items'] if not row['taxable'])
    item = office.admin('item.create', dict(name='Transport service', type='service', sales_enabled=True,
        description='Service', sales_tax_code_id=exempt, income_account_id=books['income'], price='12'), company=company)['id']
    invoice = office.admin('invoice.post', dict(customer=books['customer'], date='2017-01-03', lines=[dict(item=item, quantity='3')]),
                           company=company)
    office.admin('invoice.delete', dict(invoice=invoice['id'], expected_version=invoice['version']), company=company,
                 headers={'X-Bookflow-Reason': 'Duplicate'})
    back = office.admin('invoice.restore', dict(invoice=invoice['id']), company=company,
                        headers={'X-Bookflow-Reason': 'Person restores'})
    assert back['total_minor_units'] == 3600 and back['family'] == 'invoice'


from tests.test_bill_item_lines import books  # noqa: E402,F401  (fixtures for the transport witness)
from tests.test_purchase_deletion_http import office  # noqa: E402,F401
