"""The person-visible half of bill deletion: the grant, its own page, the journey."""
from pathlib import Path
import sqlite3

from bookflow.adapters.workbench.permissions import CAPS, DELETABLE, FIELDS, grant_controls
from bookflow.core.deletion_families import capability
from tests.test_bill_item_lines import books
from tests.test_identity_commands import WB
from tests.test_purchase_deletion_http import office
from tests.payment_raw_evidence import database

BILL_DELETE = capability('bill')


def activate(office):
    state = office.admin('permission.show')
    office.admin('permission.activate', dict(expected_generation=state['generation'],
                                             expected_catalog_sha256=state['catalog_sha256']))


def enter(office, books):
    return office.admin('bill.post', dict(vendor=books['vendor'], date='2017-01-02',
        supplier_reference='JOURNEY-1', memo='Entered twice',
        expenses=[dict(account=books['freight'], amount='4.83')],
        items=[dict(item=books['delete_stock'], quantity='2', unit_cost='8.00')]),
        company=books['company'], headers={'X-Bookflow-Reason': 'Enter the bill'})


def test_users_and_permissions_offers_the_bill_delete_grant(office, books):
    """The grant is reachable in setup, derived from the one owner of the families."""
    assert 'bill' in DELETABLE and BILL_DELETE in CAPS and 'bill_delete' in FIELDS
    company = books['company']
    activate(office)
    person = office.admin('user.add', dict(username='grantee', password='grantee fixture',
                                           company=company, role='standard'))
    label = dict(grant_controls())['bill_delete']
    page = office.installer.get(f'/c/{company}/users?user=' + person['user_id'])
    assert page.status_code == 200, page.text
    assert 'name="bill_delete"' in page.text and 'Grant ' + label.lower() + ' deletion' in page.text
    # The same page saves it, and the effective state then admits the family.
    saved = office.installer.post(f'/c/{company}/users', headers=WB, data=dict(user=person['user_id'],
        role='standard', expected_version='1', other_grants='[]', other_denies='[]',
        bill_delete='on', allow_read='on', reason='Let them remove duplicate bills', action='save'))
    assert saved.status_code == 200, saved.text
    effective = office.admin('membership.effective', dict(company=company, user=person['user_id']))
    admitted = {x['requirement']['capability']: x['admitted'] for x in effective['permissions']}
    assert admitted[BILL_DELETE] is True


def test_a_standard_user_deletes_a_bill_from_its_own_page(office, books):
    company = books['company']
    url = f'/c/{company}/bill'
    post = enter(office, books)
    activate(office)
    person = office.admin('user.add', dict(username='bill-deleter', password='deleter fixture',
                                           company=company, role='standard'))
    clerk = office.login_as('bill-deleter', 'deleter fixture')
    path = Path(office.admin('company.show', company=company)['path']) / 'company.db'
    before = database(path)
    detail = f'{url}/{post["id"]}'
    confirm = f'{detail}/delete'

    # Without the explicit grant the command refuses over the transport, the page refuses,
    # and the bill's own page offers no way in.
    refused = office.call(clerk, 'bill.delete', dict(bill=post['id'], expected_version=post['version']),
                          company=company, headers={'X-Bookflow-Reason': 'No grant yet'})
    assert refused.json()['code'] == 'E_PERMISSION', refused.text
    assert clerk.get(confirm).status_code != 200
    ungranted = clerk.get(detail)
    assert ungranted.status_code == 200 and f'{detail}/delete' not in ungranted.text
    assert database(path) == before

    office.admin('membership.grant', dict(user=person['user_id'], company=company,
        expected_version=1, grants=[BILL_DELETE], denies=['ledger.post']))
    granted = clerk.get(detail)
    assert granted.status_code == 200 and f'{detail}/delete' in granted.text
    # Posting is denied, so the writing verbs it would need are not offered either.
    assert f'{detail}/void' not in granted.text and f'{detail}/update' not in granted.text

    page = clerk.get(confirm)
    assert page.status_code == 200, page.text
    assert 'Cancel this bill and retain its history' in page.text
    assert 'name="reason"' in page.text and 'name="confirmed"' in page.text

    form = dict(expected_version=str(post['version']), operation_key='page-bill-delete',
                reason='Entered twice from the same invoice')
    unconfirmed = clerk.post(confirm, headers=WB, data=dict(form, action='delete'))
    assert unconfirmed.status_code == 400, unconfirmed.text
    assert 'Confirm cancellation' in unconfirmed.text
    assert database(path) == before

    preview = clerk.post(confirm, headers=WB, data=dict(form, action='preview', confirmed='yes'))
    assert preview.status_code == 200 and 'Preview only' in preview.text
    assert '1 stock movement(s) will be reversed' in preview.text
    assert database(path) == before

    done = clerk.post(confirm, headers=WB, data=dict(form, action='delete', confirmed='yes'),
                      follow_redirects=False)
    assert done.status_code == 303, done.text
    assert done.headers['location'] == f'{url}?deleted={post["id"]}'

    listed = clerk.get(f'{url}?deleted={post["id"]}')
    assert listed.status_code == 200 and 'Bill deleted.' in listed.text
    # The ordinary list no longer links the row; the retained-records view still does.
    assert f'href="{url}/{post["id"]}"' not in listed.text
    assert 'Include deleted records' in listed.text
    assert f'{url}?include_deleted=true' in listed.text
    retained_list = clerk.get(f'{url}?include_deleted=true')
    assert f'href="{url}/{post["id"]}?include_deleted=1"' in retained_list.text
    assert clerk.get(detail).status_code == 404
    retained = clerk.get(f'{detail}?include_deleted=1')
    assert retained.status_code == 200 and 'Entered twice from the same invoice' in retained.text
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT family,reason,principal_id FROM bill_deletions').fetchall() == [
            ('bill', 'Entered twice from the same invoice', None)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database(path)
    # The same permanent key, through the page again, acts once.
    again = clerk.post(confirm, headers=WB, data=dict(form, action='delete', confirmed='yes'),
                       follow_redirects=False)
    assert again.status_code == 303 and database(path) == after


def test_the_transport_enforces_the_exact_grant_and_replays_once(office, books):
    company = books['company']
    reason = {'X-Bookflow-Reason': 'Remove duplicate bill'}
    post = enter(office, books)
    activate(office)
    person = office.admin('user.add', dict(username='bill-clerk', password='clerk fixture',
                                           company=company, role='standard'))
    clerk = office.login_as('bill-clerk', 'clerk fixture')
    path = Path(office.admin('company.show', company=company)['path']) / 'company.db'
    raw = dict(bill=post['id'], expected_version=post['version'], operation_key='http-bill-delete')
    before = database(path)

    def grant(version, grants, denies):
        return office.admin('membership.grant', dict(user=person['user_id'], company=company,
            expected_version=version, grants=grants, denies=denies))

    # A different family's Delete is not this one's, and the family grant without
    # ledger.read is not enough either.
    grant(1, ['transaction.check.delete'], ['ledger.post'])
    assert office.call(clerk, 'bill.delete', raw, company=company, headers=reason).json()['code'] == 'E_PERMISSION'
    grant(2, [BILL_DELETE], ['ledger.post', 'ledger.read'])
    assert office.call(clerk, 'bill.delete', raw, company=company, headers=reason).json()['code'] == 'E_PERMISSION'
    grant(3, [BILL_DELETE], ['ledger.post'])
    for name in ('bill.update', 'bill.void'):
        denied = office.call(clerk, name, dict(bill=post['id'], expected_version=post['version']),
                             company=company, headers=reason)
        assert denied.json()['code'] == 'E_PERMISSION', name
    assert database(path) == before
    deleted = office.ok(clerk, 'bill.delete', raw, company=company, headers=reason)
    assert deleted['status'] == 'deleted' and deleted['cancelled_stock_movements'] == 1
    after = database(path)
    replay = office.ok(clerk, 'bill.delete', raw, company=company, headers=reason)
    assert replay['idempotent_replay'] and not replay['changed']
    assert database(path) == after
    office.admin('membership.revoke', dict(user=person['user_id'], company=company, expected_version=4))
    gone = office.call(clerk, 'bill.delete', raw, company=company, headers=reason)
    assert gone.json()['code'] == 'E_COMPANY_NOT_FOUND', gone.text
    assert database(path) == after
