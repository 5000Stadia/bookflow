"""The person-visible half of journal-entry deletion: the grant, its own page, the journey.

The alias half has a visible side of its own, and it is the last assertion here: the record
page of a transaction that is really a cheque offers the cheque's commands and never the
journal's, so a person is never shown a button the core would refuse.
"""
from pathlib import Path
import sqlite3

import pytest

from bookflow.adapters.workbench.permissions import CAPS, DELETABLE, FIELDS, NOUNS, grant_controls
from bookflow.core.deletion_families import capability
from tests.test_identity_commands import WB, office  # noqa: F401  (office is a fixture)
from tests.payment_raw_evidence import database

JOURNAL_DELETE = capability('journal_entry')


@pytest.fixture
def admitted(monkeypatch):
    """Stand in for the one catalog descriptor this family's delta has not landed yet.

    See ``tests/test_journal_deletion.py`` for what is and is not stood in for. Nothing else
    on the page, the transport or the command path is changed.
    """
    from bookflow.hub import access
    original = access.require_command_activation
    monkeypatch.setattr(access, 'require_command_activation',
                        lambda s, cmd: None if cmd.name == 'journal delete' else original(s, cmd))


def activate(office):
    state = office.admin('permission.show')
    office.admin('permission.activate', dict(expected_generation=state['generation'],
                                             expected_catalog_sha256=state['catalog_sha256']))


def books(office):
    """A bank account and an expense account of the demo company, by their own types."""
    rows = office.admin('account.query', dict(limit=200), company=office.first)['items']
    return (next(row['id'] for row in rows if row['type'] == 'bank'),
            next(row['id'] for row in rows if row['type'] == 'expense'))


def enter(office, bank, expense, *, memo='Entered twice', amount='40.00'):
    return office.admin('journal.post', dict(date='2017-01-02', memo=memo, lines=[
        dict(account=expense, side='debit', amount=amount),
        dict(account=bank, side='credit', amount=amount)]),
        company=office.first, headers={'X-Bookflow-Reason': 'Enter the entry'})


def test_users_and_permissions_offers_the_journal_delete_grant(office):
    """The grant is reachable in setup, derived from the one owner of the families."""
    assert 'journal_entry' in DELETABLE and JOURNAL_DELETE in CAPS
    assert 'journal_entry_delete' in FIELDS and 'journal' in NOUNS
    company = office.first
    activate(office)
    person = office.admin('user.add', dict(username='grantee', password='grantee fixture',
                                           company=company, role='standard'))
    label = dict(grant_controls())['journal_entry_delete']
    page = office.installer.get(f'/c/{company}/users?user=' + person['user_id'])
    assert page.status_code == 200, page.text
    assert 'name="journal_entry_delete"' in page.text
    assert 'Grant ' + label.lower() + ' deletion' in page.text
    saved = office.installer.post(f'/c/{company}/users', headers=WB, data=dict(user=person['user_id'],
        role='standard', expected_version='1', other_grants='[]', other_denies='[]',
        journal_entry_delete='on', allow_read='on',
        reason='Let them remove duplicate entries', action='save'))
    assert saved.status_code == 200, saved.text
    effective = office.admin('membership.effective', dict(company=company, user=person['user_id']))
    admitted_now = {x['requirement']['capability']: x['admitted'] for x in effective['permissions']}
    assert admitted_now[JOURNAL_DELETE] is True


def test_a_standard_user_deletes_a_journal_entry_from_its_own_page(office, admitted):
    company = office.first
    url = f'/c/{company}/journal'
    bank, expense = books(office)
    post = enter(office, bank, expense)
    activate(office)
    person = office.admin('user.add', dict(username='journal-deleter', password='deleter fixture',
                                           company=company, role='standard'))
    clerk = office.login_as('journal-deleter', 'deleter fixture')
    path = Path(office.admin('company.show', company=company)['path']) / 'company.db'
    before = database(path)
    detail = f'{url}/{post["id"]}'
    confirm = f'{detail}/delete'

    # Without the explicit grant the command refuses over the transport, the page refuses,
    # and the entry's own page offers no way in.
    refused = office.call(clerk, 'journal.delete', dict(journal=post['id'], expected_version=post['version']),
                          company=company, headers={'X-Bookflow-Reason': 'No grant yet'})
    assert refused.json()['code'] == 'E_PERMISSION', refused.text
    assert clerk.get(confirm).status_code != 200
    ungranted = clerk.get(detail)
    assert ungranted.status_code == 200 and f'{detail}/delete' not in ungranted.text
    assert database(path) == before

    office.admin('membership.grant', dict(user=person['user_id'], company=company,
        expected_version=1, grants=[JOURNAL_DELETE], denies=['ledger.post']))
    granted = clerk.get(detail)
    assert granted.status_code == 200 and f'{detail}/delete' in granted.text
    # Posting is denied, so the writing verbs it would need are not offered either.
    assert f'{detail}/void' not in granted.text and f'{detail}/update' not in granted.text

    page = clerk.get(confirm)
    assert page.status_code == 200, page.text
    assert 'Cancel this journal entry and retain its history' in page.text
    assert 'name="reason"' in page.text and 'name="confirmed"' in page.text

    form = dict(expected_version=str(post['version']), operation_key='page-journal-delete',
                reason='Entered twice from the same receipt')
    unconfirmed = clerk.post(confirm, headers=WB, data=dict(form, action='delete'))
    assert unconfirmed.status_code == 400, unconfirmed.text
    assert 'Confirm cancellation' in unconfirmed.text
    assert database(path) == before

    preview = clerk.post(confirm, headers=WB, data=dict(form, action='preview', confirmed='yes'))
    assert preview.status_code == 200 and 'Preview only' in preview.text
    assert 'reversing batch will be posted at the entry' in preview.text
    assert database(path) == before

    done = clerk.post(confirm, headers=WB, data=dict(form, action='delete', confirmed='yes'),
                      follow_redirects=False)
    assert done.status_code == 303, done.text
    assert done.headers['location'] == f'{url}?deleted={post["id"]}'

    listed = clerk.get(f'{url}?deleted={post["id"]}')
    assert listed.status_code == 200 and 'deleted.' in listed.text
    # The ordinary list no longer links the row; the retained-records view still does.
    assert f'href="{url}/{post["id"]}"' not in listed.text
    assert 'Include deleted records' in listed.text
    retained_list = clerk.get(f'{url}?include_deleted=true')
    assert f'href="{url}/{post["id"]}?include_deleted=1"' in retained_list.text
    assert clerk.get(detail).status_code == 404
    retained = clerk.get(f'{detail}?include_deleted=1')
    assert retained.status_code == 200 and 'Entered twice from the same receipt' in retained.text
    # The list's own "retained record and history" link is answered by the entry's revisions.
    history = clerk.get(f'{detail}?include_deleted=1&history=1')
    assert history.status_code == 200 and 'Journal history' in history.text
    assert f'{detail}?include_deleted=1&revision_number=1' in history.text
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT family,reason,principal_id FROM journal_deletions').fetchall() == [
            ('journal_entry', 'Entered twice from the same receipt', None)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database(path)
    # The same permanent key, through the page again, acts once.
    again = clerk.post(confirm, headers=WB, data=dict(form, action='delete', confirmed='yes'),
                       follow_redirects=False)
    assert again.status_code == 303 and database(path) == after


def test_a_cheques_journal_page_offers_the_cheques_commands_and_never_the_journals(office, admitted):
    """The visible half of the alias resolution: no button the core would refuse."""
    company = office.first
    bank, expense = books(office)
    cheque = office.admin('check.post', dict(account=bank, date='2017-01-03', amount='18.00',
        expenses=[dict(account=expense, amount='18.00')]), company=company,
        headers={'X-Bookflow-Reason': 'Pay the carrier'})
    activate(office)
    member = next(row for row in office.admin('membership.list', dict(company=company))['items']
                  if row['scope_type'] == 'company' and row['scope_id'] == company)
    office.admin('membership.grant', dict(user=member['user_id'], company=company, role='owner',
        expected_version=member['version'], grants=[JOURNAL_DELETE, capability('check')], denies=[]))

    page = office.installer.get(f'/c/{company}/journal/{cheque["id"]}')
    assert page.status_code == 200, page.text
    assert f'/c/{company}/check/{cheque["id"]}/delete' in page.text
    assert f'/c/{company}/check/{cheque["id"]}/update' in page.text
    assert f'/c/{company}/journal/{cheque["id"]}/delete' not in page.text
    assert f'/c/{company}/journal/{cheque["id"]}/void' not in page.text
