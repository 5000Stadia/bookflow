"""The availability half: a person can open their memorized transactions and see what is due.

Registration is not callability. These tests navigate to the destinations the home board
declares and assert the page that comes back is one a person can use -- a heading, no error,
something to do, and the actual names and counts the commands answer with.
"""
import socket

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.adapters.workbench import home, naming
from bookflow.commands.host_cmds import start_serving
from bookflow.core import registry
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.test_home_window import navigate_witness

PASSWORD = 'correct-horse-battery'


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    """One company with a due memorized transaction, served to a browser client."""
    data_root = tmp_path / 'memorized-workbench'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Workbench organization')
    company = client.company.new(legal_name='Workbench', home_currency='USD', timezone='UTC',
                                 organization='Workbench organization', chart='general')['company_id']
    client.account.create(company=company, name='Office Rent', type='expense')
    client.vendor.create(company=company, name='Harbor Property')
    client.run('memorized create', {
        'name': 'Monthly office rent', 'command': 'bill post',
        'payload': {'vendor': 'Harbor Property',
                    'expenses': [{'account': 'Office Rent', 'amount': '1800.00'}]},
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'},
        company=company, reason='Memorize the rent')
    client.run('memorized-group create', {'name': 'Month end'}, company=company, reason='Group it')
    login = os_login()
    client.run('user set-password', {'username': login, 'password': PASSWORD})
    issued = client.token.issue(label='Memorized browser probe')

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.close()
    handle = start_serving(data_root, client_version(), bind=f'127.0.0.1:{port}',
                           secure_cookies=False, publish_descriptor=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post('/login', json={'username': login, 'password': PASSWORD}).status_code == 200

        def call(name, raw=None, **context):
            # The host holds the data-root lock for its whole run, so a reader here goes
            # through the host exactly as any other program would.
            answer = browser.post(
                f"/companies/{company}/commands/{name.replace(' ', '.')}", json=raw or {},
                headers={'Authorization': 'Bearer ' + issued['secret'], **context})
            assert answer.status_code == 200, answer.text
            return answer.json()

        yield type('Workbench', (), {'browser': browser, 'company_id': company,
                                     'call': staticmethod(call)})
    finally:
        handle.stop()


def _steps(company_id):
    return {item.step.id: item for panel in home.resolve(company_id) for item in panel.steps}


def test_both_memorized_tiles_are_live_and_land_on_a_usable_page(workbench):
    """The whole availability contract, end to end: registered, routed, declared, and navigated."""
    steps = _steps(workbench.company_id)
    for step_id, href in (('memorized', f'/c/{workbench.company_id}/memorized'),
                          ('memorized-due', f'/c/{workbench.company_id}/memorized/process')):
        item = steps[step_id]
        assert item.live, (step_id, item.reason)
        assert item.href == href
        navigate_witness(workbench.browser, item, step_id)
    # A write-labelled tile has to be backed by a command that writes.
    assert registry.get('memorized process').is_write
    assert not registry.get('memorized list').is_write


def test_the_list_page_shows_the_memorized_transactions_and_what_they_owe(workbench):
    page = workbench.browser.get(f'/c/{workbench.company_id}/memorized')
    assert page.status_code == 200
    assert '<h1>Memorized transactions</h1>' in page.text
    assert naming.list_heading('memorized', registry.noun_meta('memorized')) == 'Memorized transactions'
    assert 'Monthly office rent' in page.text
    assert 'bill post' in page.text
    # The column a person is actually looking for: how many entries are waiting.
    assert 'Due count' in page.text and 'Blocked count' in page.text
    assert f'href="/c/{workbench.company_id}/memorized/create"' in page.text
    assert f'href="/c/{workbench.company_id}/memorized/process"' in page.text


def test_the_record_page_opens_one_memorized_transaction(workbench):
    listed = workbench.call('memorized list')
    record = listed['items'][0]
    page = workbench.browser.get(f"/c/{workbench.company_id}/memorized/{record['id']}")
    assert page.status_code == 200
    assert 'Monthly office rent' in page.text
    assert 'class="error"' not in page.text.split('<main>', 1)[-1]
    # Its own verbs are offered from the record it belongs to.
    for verb in ('update', 'enter', 'delete'):
        assert f"/c/{workbench.company_id}/memorized/{record['id']}/{verb}" in page.text, verb


def test_entering_what_is_due_from_the_browser_posts_the_documents(workbench):
    """The page is not a decoration: the form on it enters the transactions that are due."""
    form = workbench.browser.get(f'/c/{workbench.company_id}/memorized/process')
    assert form.status_code == 200 and '<form' in form.text
    assert 'Enter every memorized transaction that is due' in form.text
    answer = workbench.browser.post(
        f'/c/{workbench.company_id}/memorized/process',
        data={'f:as_of': '2026-02-28', 'f:limit': '50', 'c:reason': 'Enter what is due',
              'action': 'submit'},
        headers={'X-Bookflow-Workbench': '1'}, follow_redirects=True)
    assert answer.status_code == 200, answer.text[:400]
    assert 'class="error"' not in answer.text.split('<main>', 1)[-1], answer.text[:800]
    posted = workbench.call('bill query', {'limit': 10})['items']
    assert sorted(row['date'] for row in posted) == ['2026-01-31', '2026-02-28']
    listed = workbench.call('memorized list', {'as_of': '2026-02-28'})
    assert listed['items'][0]['last_entered_on'] is not None and listed['due_total'] == 0


def test_the_group_list_page_is_reachable_too(workbench):
    page = workbench.browser.get(f'/c/{workbench.company_id}/memorized-group')
    assert page.status_code == 200
    assert '<h1>Memorized transaction groups</h1>' in page.text
    assert 'Month end' in page.text


def test_the_accounting_menu_lists_the_memorized_nouns(workbench):
    page = workbench.browser.get(f'/c/{workbench.company_id}/_group/accounting')
    assert page.status_code == 200
    assert f'href="/c/{workbench.company_id}/memorized"' in page.text
    assert f'href="/c/{workbench.company_id}/memorized-group"' in page.text
