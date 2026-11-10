"""Source competition, alternative posting doors and effective reviewer identity."""
import csv
import io
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.conftest import make_agent
from tests.test_bill_approvals import setup, publish, saved, approve, commit, run
from tests.test_purchase_drafts import financial
from tests.test_row8_journal import COMPANY, database_path

SURFACES = ('python',)


def test_approved_source_competition_revalidates_without_consuming_twice(root, client):
    other, payload, policy = setup(root, client)
    accounts = client.account.query(company=COMPANY, limit=200)['items']
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    cogs = next(row['id'] for row in accounts if row['type'] == 'cost_of_goods_sold')
    item = client.item.create(company=COMPANY, name='Approval source part', type='inventory_part',
        description='Approval source part', purchase_description='Approval source part',
        price='15.00', cost='12.00', cogs_account_id=cogs, income_account_id=income)['id']
    order = run(client, 'purchase-order post', dict(date='2026-06-01', vendor=payload['vendor'],
        lines=[dict(item=item, quantity='10', rate='12.00')]))
    line = order['revision']['lines'][0]['line_id']
    publish(client, policy)
    before = financial(client)
    six = saved(client, dict(date='2026-06-15', purchase_order=order['id'],
        order_lines=[dict(line=line, quantity='6')]), 'six')
    five = saved(client, dict(date='2026-06-15', purchase_order=order['id'],
        order_lines=[dict(line=line, quantity='5')]), 'five')
    assert six['preview']['total']['minor_units'] == 7200
    assert five['preview']['total']['minor_units'] == 6000
    assert six['preview']['purchase_order']['name'] == order['number']
    assert six['preview']['items'][0]['item']['name'] == 'Approval source part'
    assert six['preview']['items'][0]['quantity'] == '6'
    assert six['preview']['items'][0]['amount']['minor_units'] == 7200
    assert six['history'][0]['preview'] == six['preview']
    six_request = approve(client, other, six, 'submit-six')
    five_request = approve(client, other, five, 'submit-five')
    assert financial(client) == before
    posted = commit(client, six, six_request, 'commit-six')
    bill = run(client, 'bill show', dict(bill=posted['document_id']))
    assert bill['total_minor_units'] == 7200
    before_refusal = financial(client)
    with pytest.raises(BookflowError) as consumed:
        commit(client, five, five_request, 'commit-five')
    assert consumed.value.code in ('E_APPROVAL_STALE', 'E_VALIDATION', 'E_VERSION_CONFLICT')
    assert financial(client) == before_refusal
    tracking = run(client, 'purchase-order show', dict(purchase_order=order['id']))['line_tracking'][0]
    assert tracking['open_quantity'] == '4'
    assert tracking['billed_quantity'] == '6'
    with sqlite3.connect(database_path(client)) as db:
        legs = db.execute('SELECT debit_minor_units, credit_minor_units FROM posting_lines '
            'WHERE transaction_id=?', (posted['document_id'],)).fetchall()
        assert sum(row[0] for row in legs) == sum(row[1] for row in legs) == 7200
        assert db.execute('SELECT count(*) FROM approval_commits WHERE draft_id=?', (five['id'],)).fetchone() == (0,)
    assert run(client, 'purchase-draft show', dict(draft=five['id']))['state'] == 'approved'
    receipt = run(client, 'item-receipt post', dict(date='2026-06-15', vendor=payload['vendor'],
        items=[dict(item=item, quantity='3', unit_cost='12.00')]))
    received = saved(client, dict(date='2026-06-15', receipts=[dict(receipt_line=receipt['items'][0]['id'],
        expected_receipt_version=receipt['version'], quantity='2')]), 'received')
    assert received['preview']['receipts'] == [dict(id=receipt['id'], number=receipt['number'], quantity='2')]
    assert received['preview']['items'][0]['amount']['minor_units'] == 2400
    assert client.run('purchase-draft preview', dict(draft=received['id']), company=COMPANY)['preview'] == received['preview']



def test_import_and_memorized_bill_routes_name_approval_refusal(root, client):
    _, payload, policy = setup(root, client)
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(['Vendor', 'Ref No', 'Date', 'Account', 'Amount'])
    writer.writerow(['Approval supplier', 'APPROVAL-IMPORT', '2026-06-15', 'Approval supplies', '80.00'])
    preview = run(client, 'import preview', dict(kind='bill', content=content.getvalue(),
        key_by_content=True, partial=True))
    assert preview['counts'] == {'create': 1}
    recurring_payload = {key: value for key, value in payload.items() if key != 'date'}
    run(client, 'memorized create', dict(name='Approval scheduled bill', command='bill post',
        payload=recurring_payload, frequency='monthly', start_date='2026-06-15', mode='enter_automatically'))
    publish(client, policy)
    before = financial(client)
    imported = run(client, 'import run', dict(run=preview['run']['id'], key_by_content=True))
    assert imported['counts'].get('created', 0) == 0
    assert imported['refused'] and imported['refused'][0]['code'] == 'E_APPROVAL_REQUIRED'
    assert 'bill post' in imported['refused'][0]['message']
    automatic = run(client, 'memorized process', dict(as_of='2026-06-15'))
    assert automatic['entered_count'] == 0 and automatic['blocked_count'] == 1
    assert automatic['blocked'][0]['command'] == 'bill post'
    assert automatic['blocked'][0]['error_code'] == 'E_APPROVAL_REQUIRED'
    assert 'bill post' in automatic['blocked'][0]['error_message']
    assert financial(client) == before


class ApprovalAgent:
    def __init__(self, root, login, principal):
        self.root, self.login, self.principal = root, login, principal

    def run(self, name, data, *, company, reason=None, **kw):
        from bookflow.core import registry
        from bookflow.core.context import Context, Interface
        from bookflow.core.dispatch import run as dispatch
        ctx = Context.new(Interface.python, 'approval identity witness', on_behalf_of=self.principal,
                          reason=reason)
        return dispatch(registry.get(name), data, ctx, data_root=str(self.root),
                        company_selector=company, company_source='option', _login=self.login, **kw)


def test_self_review_and_same_principal_agent_refuse_and_retry_rechecks_grants(root, client):
    from bookflow.core.config import Config
    other, payload, policy = setup(root, client)
    principal = policy['eligible_members'][0]
    company = client.company.show(company=COMPANY)['company_id']
    agent = make_agent(lambda name, body: client.run(name, body), 'approval_reviewer_agent',
        principals=[principal], company=company)
    config = Config.load(root / 'config.toml')
    config.set_user('approval_reviewer_agent', agent)
    config.save()
    bot = ApprovalAgent(root, 'approval_reviewer_agent', principal)
    client.membership.grant(user=agent, company=company, expected_version=1,
                            grants=['approval.read', 'approval.decide'])
    policy = run(client, 'approval-policy update', dict(policy=policy['id'], expected_version=policy['version'],
        eligible_members=[principal, agent], operation_key='include-agent'))
    assert policy['allow_self'] is False
    publish(client, policy)
    own = saved(other, payload, 'self-draft')
    own_request = run(other, 'purchase-draft submit', dict(draft=own['id'], expected_version=own['version'],
                                                         operation_key='self-submit'))
    decision = dict(request=own_request['id'], expected_version=own_request['version'],
                    decision='approve', operation_key='self-decide')
    for reviewer in (other, bot):
        with pytest.raises(BookflowError) as self_review:
            run(reviewer, 'approval decide', decision)
        assert self_review.value.code == 'E_APPROVAL_SELF'
    assert run(client, 'approval show', dict(request=own_request['id']))['state'] == 'pending'
    independent = saved(client, payload, 'independent')
    request = run(client, 'purchase-draft submit', dict(draft=independent['id'], expected_version=1,
                                                       operation_key='independent-submit'))
    decision = dict(request=request['id'], expected_version=request['version'],
                    decision='approve', operation_key='independent-decide')
    approved = run(bot, 'approval decide', decision)
    assert approved['state'] == 'approved'
    assert run(bot, 'approval decide', decision)['idempotent_replay']
    client.membership.grant(user=agent, company=company, expected_version=2, grants=[])
    with pytest.raises(BookflowError) as revoked:
        run(bot, 'approval decide', decision)
    assert revoked.value.code in ('E_PERMISSION', 'E_UNAUTHENTICATED')
    client.membership.grant(user=agent, company=company, expected_version=3,
                            grants=['approval.read', 'approval.decide'])
    client.membership.grant(user=principal, company=company, role='admin', expected_version=1,
                            denies=['approval.decide'])
    with pytest.raises(BookflowError) as principal_revoked:
        run(bot, 'approval decide', decision)
    assert principal_revoked.value.code in ('E_PERMISSION', 'E_UNAUTHENTICATED')
