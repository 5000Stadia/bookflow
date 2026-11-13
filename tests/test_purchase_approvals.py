"""Purchase release evidence, independent review, and exact source consumption."""
import csv
import io
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.conftest import make_actor, as_user, make_agent
from tests.test_bill_approvals import approve, commit, run, saved
from tests.test_bill_approval_sources import ApprovalAgent
from tests.test_purchase_drafts import financial
from tests.test_row8_journal import COMPANY, database_path

SURFACES = ('python',)


def setup_purchase(root, client):
    company = client.company.show(company=COMPANY)['company_id']
    reviewer = make_actor(root, 'purchase_reviewer', company_role=(company, 'admin'))
    other = as_user(root, 'purchase_reviewer')
    vendor = client.vendor.create(name='Release supplier', company=COMPANY)['id']
    accounts = client.account.query(company=COMPANY, limit=200)['items']
    income = next(x['id'] for x in accounts if x['type'] == 'income')
    cogs = next(x['id'] for x in accounts if x['type'] == 'cost_of_goods_sold')
    item = client.item.create(company=COMPANY, name='Release inventory part', type='inventory_part',
        description='Ordered part', purchase_description='Ordered part', price='15.00', cost='12.00',
        cogs_account_id=cogs, income_account_id=income)['id']
    payload = dict(date='2026-06-01', vendor=vendor, lines=[dict(item=item, quantity='10', rate='12.00')])
    policy = run(client, 'approval-policy create', dict(target='purchase_order', eligible_members=[reviewer],
                                                      operation_key='purchase-policy'))
    return other, payload, policy


def publish(client, policy):
    return run(client, 'approval-policy publish', dict(policy=policy['id'], expected_version=policy['version'],
        operation_key='publish-' + policy['id'] + '-' + str(policy['version']), confirm=True))


def order_draft(client, payload, key='order-draft', **kw):
    return saved(client, payload, key, kind='purchase_order', **kw)


def test_purchase_release_correction_revision_evidence_and_inverse(root, client):
    other, payload, policy = setup_purchase(root, client)
    legacy = run(client, 'purchase-order post', payload)
    assert legacy['total_minor_units'] == 12000
    assert legacy['approval_origin'] == 'Created before approvals were enabled'
    publish(client, policy)
    for name, data in [('purchase-order post', payload), ('purchase-order update',
            dict(purchase_order=legacy['id'], expected_version=legacy['version'], memo='Direct correction'))]:
        with pytest.raises(BookflowError) as denied:
            run(client, name, data)
        assert denied.value.code == 'E_APPROVAL_REQUIRED'
        assert denied.value.details['next_command'] == 'purchase-draft create'
    before = financial(client)
    draft = order_draft(client, payload)
    req = approve(client, other, draft, 'release-submit')
    assert financial(client) == before
    for changes in ({'vendor': payload['vendor']}, {'date': '2026-06-02'}, {'memo': 'New evidence'},
                    {'lines': [dict(payload['lines'][0], rate='12.01')]}):
        with pytest.raises(BookflowError) as frozen:
            run(client, 'purchase-draft update', {**payload, 'draft': draft['id'],
                'kind': 'purchase_order', 'expected_version': 3, 'operation_key': 'frozen', **changes})
        assert frozen.value.code == 'E_APPROVAL_STATE'
    released = commit(client, draft, req, 'release')
    order = run(client, 'purchase-order show', dict(purchase_order=released['document_id']))
    assert order['status'] == 'open' and order['total_minor_units'] == 12000
    assert order['approval_origin'] == 'Released through approval'
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM posting_lines WHERE transaction_id=?', (order['id'],)).fetchone() == (0,)
    correction_fields = dict(date=payload['date'], vendor=payload['vendor'], memo='Corrected commercial evidence')
    correction = order_draft(client, correction_fields, 'correction',
        operation='correct', original_id=order['id'], original_version=order['version'])
    pending = run(client, 'purchase-draft submit', dict(draft=correction['id'], expected_version=1,
                                                      operation_key='correction-submit'))
    rejected = run(other, 'approval decide', dict(request=pending['id'], expected_version=1,
        decision='reject', comment='Confirm the supplier reference', operation_key='reject'))
    assert rejected['state'] == 'rejected'
    revised = run(client, 'purchase-draft update', dict(correction_fields, kind='purchase_order',
        reference='Confirmed reference', draft=correction['id'],
        expected_version=3, operation_key='revise'))
    req = approve(client, other, revised, 'correction-resubmit')
    history = run(client, 'approval history', dict(request=req['id']))['events']
    assert any(event['action'] == 'reject' and event['data']['comment'] == 'Confirm the supplier reference' for event in history)
    with pytest.raises(BookflowError) as lost_race:
        run(other, 'approval decide', dict(request=req['id'], expected_version=1, decision='reject', operation_key='lost-race'))
    assert lost_race.value.code == 'E_VERSION_CONFLICT'
    current_policy = run(client, 'approval-policy show', dict(policy=policy['id']))
    disabled = run(client, 'approval-policy update', dict(policy=policy['id'], expected_version=current_policy['version'],
        target='purchase_order', eligible_members=policy['eligible_members'], enabled=False, operation_key='disable-purchases'))
    publish(client, disabled)
    current = run(client, 'purchase-draft show', dict(draft=correction['id']))
    withdrawn = run(client, 'purchase-draft withdraw', dict(draft=correction['id'], expected_version=current['version'],
        expected_request_version=req['version'], operation_key='withdraw-disabled', confirm=True))
    with pytest.raises(BookflowError) as bypass:
        commit(client, withdrawn, key='disabled-direct-commit')
    assert bypass.value.code == 'E_APPROVAL_REQUIRED'
    req = approve(client, other, withdrawn, 'captured-policy-resubmit')
    corrected = commit(client, correction, req, 'correction-commit')
    assert run(client, 'purchase-order show', dict(purchase_order=order['id']))['memo'] == 'Corrected commercial evidence'
    run(client, 'batch reverse', dict(batch=corrected['batch_id']))
    restored = run(client, 'purchase-order show', dict(purchase_order=order['id']))
    assert restored['status'] == 'open' and restored['memo'] == order['memo']
    assert restored['total_minor_units'] == 12000
    assert run(client, 'purchase-draft show', dict(draft=correction['id']))['original_version'] == restored['version']
    enabled = run(client, 'approval-policy update', dict(policy=policy['id'], expected_version=disabled['version'] + 1,
        target='purchase_order', eligible_members=policy['eligible_members'], enabled=True, operation_key='reenable-purchases'))
    publish(client, enabled)
    independent = order_draft(client, payload, 'reversible')
    independent_req = approve(client, other, independent, 'reversible-submit')
    independent_commit = commit(client, independent, independent_req, 'reversible-commit')
    run(client, 'batch reverse', dict(batch=independent_commit['batch_id']))
    assert run(client, 'purchase-order show', dict(purchase_order=independent_commit['document_id']))['status'] == 'voided'
    retarget = order_draft(client, correction_fields, 'retarget', operation='correct',
        original_id=legacy['id'], original_version=legacy['version'])
    update = dict(payload, kind='purchase_order', operation='create', draft=retarget['id'],
        expected_version=1, operation_key='retarget-create')
    changed = run(client, 'purchase-draft update', update)
    assert changed['original_id'] is None and changed['operation'] == 'create'
    assert run(client, 'purchase-draft update', update)['original_id'] is None
    assert run(client, 'purchase-draft show', dict(draft=retarget['id']))['original_id'] is None
    roundtrip = run(client, 'purchase-draft update', dict(changed['payload'], kind='purchase_order',
        draft=retarget['id'], expected_version=changed['version'], operation_key='roundtrip-payload'))
    assert roundtrip['preview']['total']['minor_units'] == 12000


def test_released_ten_at_twelve_source_race_and_receipt_clearing(root, client):
    other, payload, policy = setup_purchase(root, client)
    publish(client, policy)
    draft = order_draft(client, payload)
    req = approve(client, other, draft, 'order-submit')
    release = commit(client, draft, req, 'order-release')
    order = run(client, 'purchase-order show', dict(purchase_order=release['document_id']))
    assert order['total_minor_units'] == 12000
    line = order['revision']['lines'][0]['line_id']
    bill_policy = run(client, 'approval-policy create', dict(target='bill', eligible_members=policy['eligible_members'],
                                                           operation_key='bill-policy'))
    publish(client, bill_policy)
    with pytest.raises(BookflowError) as distinct:
        run(client, 'bill post', dict(date='2026-06-15', purchase_order=order['id'], order_lines=[dict(line=line, quantity='6')]))
    assert distinct.value.code == 'E_APPROVAL_REQUIRED'
    six = saved(client, dict(date='2026-06-15', purchase_order=order['id'], order_lines=[dict(line=line, quantity='6')]), 'six')
    five = saved(client, dict(date='2026-06-15', purchase_order=order['id'], order_lines=[dict(line=line, quantity='5')]), 'five')
    six_req = approve(client, other, six, 'six-submit')
    five_req = approve(client, other, five, 'five-submit')
    posted_six = commit(client, six, six_req, 'six-commit')
    assert posted_six['total_minor_units'] == 7200
    before = financial(client)
    with pytest.raises(BookflowError) as race:
        commit(client, five, five_req, 'five-commit')
    assert race.value.code in ('E_APPROVAL_STALE', 'E_VALIDATION', 'E_VERSION_CONFLICT')
    assert financial(client) == before
    assert run(client, 'purchase-order show', dict(purchase_order=order['id']))['line_tracking'][0]['open_quantity'] == '4'
    current = run(client, 'purchase-draft show', dict(draft=five['id']))
    withdrawn = run(client, 'purchase-draft withdraw', dict(draft=five['id'], expected_version=current['version'],
        expected_request_version=five_req['version'], operation_key='five-withdraw', confirm=True))
    four = run(client, 'purchase-draft update', dict(draft=five['id'], expected_version=withdrawn['version'],
        operation_key='four-revise', date='2026-06-15', purchase_order=order['id'], order_lines=[dict(line=line, quantity='4')]))
    four_req = approve(client, other, four, 'four-submit')
    posted_four = commit(client, four, four_req, 'four-commit')
    assert posted_four['total_minor_units'] == 4800
    item = payload['lines'][0]['item']
    with sqlite3.connect(database_path(client)) as db:
        for identity, amount in ((posted_six['document_id'], 7200), (posted_four['document_id'], 4800)):
            legs = db.execute('SELECT a.system_role,a.type,p.debit_minor_units,p.credit_minor_units FROM posting_lines p '
                'JOIN accounts a ON a.id=p.account_id WHERE p.transaction_id=?', (identity,)).fetchall()
            assert sorted((row[0] == 'inventory_asset', row[1] == 'accounts_payable', row[2], row[3]) for row in legs) == [
                (False, True, 0, amount), (True, False, amount, 0)]
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?', (item,)).fetchone() == (10000000, 12000)
    assert run(client, 'purchase-order show', dict(purchase_order=order['id']))['line_tracking'][0]['open_quantity'] == '0'
    with pytest.raises(BookflowError) as used:
        run(client, 'batch reverse', dict(batch=release['batch_id']))
    assert used.value.code == 'E_BATCH_BLOCKED'
    received_draft = order_draft(client, payload, 'receipt-order')
    received_req = approve(client, other, received_draft, 'receipt-order-submit')
    received_release = commit(client, received_draft, received_req, 'receipt-order-release')
    received_order = run(client, 'purchase-order show', dict(purchase_order=received_release['document_id']))
    receipt = run(client, 'item-receipt post', dict(date='2026-06-16', vendor=payload['vendor'],
        purchase_order=received_order['id'], purchase_order_version=received_order['version'],
        items=[dict(item=item, order_line_id=received_order['revision']['lines'][0]['line_id'], quantity='3', unit_cost='12.00')]))
    bill = saved(client, dict(date='2026-06-16', receipts=[dict(receipt_line=receipt['items'][0]['id'],
        expected_receipt_version=receipt['version'], quantity='3')]), 'receipt-bill')
    bill_req = approve(client, other, bill, 'receipt-bill-submit')
    posted = commit(client, bill, bill_req, 'receipt-bill-commit')
    assert posted['total_minor_units'] == 3600
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?', (item,)).fetchone() == (13000000, 15600)
        legs = db.execute('SELECT a.type,p.debit_minor_units,p.credit_minor_units FROM posting_lines p '
            'JOIN accounts a ON a.id=p.account_id WHERE p.transaction_id=?', (posted['document_id'],)).fetchall()
        receipt_legs = db.execute('SELECT a.system_role,a.type,p.debit_minor_units,p.credit_minor_units FROM posting_lines p '
            'JOIN accounts a ON a.id=p.account_id WHERE p.transaction_id=?', (receipt['transaction_id'],)).fetchall()
        assert sorted((row[0] == 'inventory_asset', row[1] == 'accounts_payable', row[2], row[3]) for row in receipt_legs) == [
            (False, True, 0, 3600), (True, False, 3600, 0)]
        # Native receipt liability uses AP; the bill clears the receipt-owned AP and creates bill-owned AP.
        assert sorted(legs) == [('accounts_payable', 0, 3600), ('accounts_payable', 3600, 0)]
    assert run(client, 'item-receipt show', dict(receipt=receipt['id']))['receipt_liability_current']['minor_units'] == 0


def test_purchase_import_memorized_and_same_principal_agent_refusals(root, client):
    from bookflow.core.config import Config
    other, payload, policy = setup_purchase(root, client)
    company = client.company.show(company=COMPANY)['company_id']
    principal = policy['eligible_members'][0]
    agent = make_agent(lambda name, body: client.run(name, body), 'purchase_review_agent', principals=[principal], company=company)
    config = Config.load(root / 'config.toml')
    config.set_user('purchase_review_agent', agent)
    config.save()
    bot = ApprovalAgent(root, 'purchase_review_agent', principal)
    client.membership.grant(user=agent, company=company, expected_version=1, grants=['approval.read', 'approval.decide'])
    policy = run(client, 'approval-policy update', dict(policy=policy['id'], expected_version=1, target='purchase_order',
        eligible_members=[principal, agent], operation_key='assign-agent'))
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(['Vendor', 'PO No', 'Date', 'Item', 'Qty', 'Rate'])
    writer.writerow(['Release supplier', 'RELEASE-IMPORT', '2026-06-01', 'Release inventory part', '10', '12.00'])
    preview = run(client, 'import preview', dict(kind='purchase-order', content=content.getvalue(), key_by_content=True, partial=True))
    assert preview['counts'] == {'create': 1}
    publish(client, policy)
    imported = run(client, 'import run', dict(run=preview['run']['id'], key_by_content=True))
    assert imported['counts'].get('created', 0) == 0
    assert imported['refused'][0]['code'] == 'E_APPROVAL_REQUIRED'
    assert 'purchase-draft' in imported['refused'][0]['message']
    with pytest.raises(BookflowError) as scheduled:
        run(client, 'memorized create', dict(name='Scheduled purchase release', command='purchase-order post',
            payload={key: value for key, value in payload.items() if key != 'date'}, frequency='monthly',
            start_date='2026-06-01', mode='enter_automatically'))
    assert scheduled.value.code == 'E_APPROVAL_REQUIRED'
    assert scheduled.value.details['next_command'] == 'purchase-draft create'
    own = order_draft(other, payload, 'principal-draft')
    req = run(other, 'purchase-draft submit', dict(draft=own['id'], expected_version=1, operation_key='principal-submit'))
    for reviewer in (other, bot):
        with pytest.raises(BookflowError) as self_review:
            run(reviewer, 'approval decide', dict(request=req['id'], expected_version=1, decision='approve', operation_key='principal-decide'))
        assert self_review.value.code == 'E_APPROVAL_SELF'
    assert run(client, 'approval show', dict(request=req['id']))['state'] == 'pending'


def test_direct_purchase_release_gate_through_cli_http_and_actual_mcp(root, client, tmp_path):
    import anyio
    from tests.mcp_matrix_support import Matrix
    _, payload, policy = setup_purchase(root, client)
    publish(client, policy)

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in ('cli', 'http', 'mcp'):
                error = await matrix.call(surface, 'purchase-order post', payload, rejected=True)
                assert error['code'] == 'E_APPROVAL_REQUIRED', (surface, error)
                assert 'purchase-draft' in error['message'], (surface, error)
        finally:
            await matrix.close()
    anyio.run(witness)


def test_purchase_migration_preserves_bill_commit_history_and_old_order(root, client, monkeypatch):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import HEADS, migrate_to_head
    from tests.test_bill_approvals import setup
    other, payload, policy = setup(root, client)
    publish(client, policy)
    bill_draft = saved(client, payload, 'migration-bill')
    req = approve(client, other, bill_draft, 'migration-submit')
    committed = commit(client, bill_draft, req, 'migration-commit')
    old_order = run(client, 'purchase-order post', dict(date='2026-06-01', vendor=payload['vendor'],
        lines=[dict(account=payload['expenses'][0]['account'], amount='120.00')]))
    path = database_path(client)
    head = HEADS['company']
    tables = ('purchase_drafts', 'purchase_draft_revisions', 'approval_commits', 'approval_events',
              'approval_requests', 'purchase_orders', 'purchase_order_revisions', 'posting_lines')
    with sqlite3.connect(path) as db:
        # A historical fixture predates the newly seeded PO draft. Remove only that new-kind seed evidence.
        draft_triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN "
            "('purchase_drafts','purchase_draft_revisions','purchase_draft_operations')").fetchall()
        for name, _ in draft_triggers:
            db.execute('DROP TRIGGER ' + name)
        new_kind = "SELECT id FROM purchase_drafts WHERE kind='purchase_order'"
        for table in ('purchase_draft_operations', 'purchase_draft_revisions'):
            db.execute('DELETE FROM ' + table + ' WHERE draft_id IN (' + new_kind + ')')
        db.execute("DELETE FROM purchase_drafts WHERE kind='purchase_order'")
        for _, ddl in draft_triggers:
            db.execute(ddl)
        columns = {table: [x[1] for x in db.execute('PRAGMA table_info(' + table + ')')
                          if x[1] not in ('original_order_id', 'order_id')] for table in tables}
        before = {table: db.execute('SELECT ' + ','.join(columns[table]) + ' FROM ' + table + ' ORDER BY id').fetchall()
                  for table in tables}
        triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('purchase_drafts','approval_commits')").fetchall()
    engine = sa.create_engine('sqlite:///' + str(path))
    try:
        with engine.begin() as connection:
            op = Operations(MigrationContext.configure(connection))
            for name, _ in triggers:
                op.execute('DROP TRIGGER ' + name)
            with op.batch_alter_table('purchase_drafts') as batch:
                batch.drop_constraint('ck_purchase_draft_original_kind', type_='check')
                batch.drop_constraint('fk_purchase_draft_original_order', type_='foreignkey')
                batch.drop_constraint('ck_purchase_draft_scope', type_='check')
                batch.create_check_constraint('ck_purchase_draft_scope', "kind = 'bill' AND operation IN ('create','correct','restore') AND state IN ('draft','pending','approved','rejected','committed','cancelled')")
                batch.drop_column('original_order_id')
            with op.batch_alter_table('approval_commits') as batch:
                batch.drop_constraint('ck_approval_commit_document', type_='check')
                batch.drop_constraint('fk_approval_commit_order', type_='foreignkey')
                batch.drop_column('order_id')
                batch.alter_column('document_id', existing_type=sa.String(26), nullable=False)
            for _, ddl in triggers:
                op.execute(ddl)
            op.execute("UPDATE alembic_version SET version_num='co0138'")
    finally:
        engine.dispose()
    # Only this migration was rewound. Later migrations must never run a second time.
    with monkeypatch.context() as patch:
        patch.setitem(HEADS, 'company', 'co0139')
        with open_database(path, writable=True) as db:
            assert migrate_to_head(db, 'company', None) == ('co0138', 'co0139')
            for table in tables:
                assert db.raw.execute('SELECT ' + ','.join(columns[table]) + ' FROM ' + table + ' ORDER BY id').fetchall() == before[table]
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
            assert db.raw.execute('SELECT purchase_approvals_enabled FROM company_info').fetchone() == (0,)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE alembic_version SET version_num=?', (head,))
    assert run(client, 'bill show', dict(bill=committed['document_id']))['total_minor_units'] == 8000
    assert run(client, 'purchase-order show', dict(purchase_order=old_order['id']))['total_minor_units'] == 12000
    assert run(client, 'purchase-draft show', dict(draft=bill_draft['id']))['state'] == 'committed'
