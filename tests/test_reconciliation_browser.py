"""The availability half: a person can open the reconciliation their books are waiting for.

Registration is not callability. This navigates to the destination the home board declares for
the Reconcile tile and asserts the page that comes back is one a person can use, and that the
form on it is the form the command actually takes.
"""
import socket

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.adapters.workbench import home
from bookflow.commands.host_cmds import start_serving
from bookflow.core import registry
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.test_home_window import navigate_witness

PASSWORD = 'correct-horse-battery'


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    """One company with a bank account that has something on it to reconcile."""
    data_root = tmp_path / 'reconcile-workbench'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Workbench organization')
    company = client.company.new(legal_name='Workbench', home_currency='USD', timezone='UTC',
                                 organization='Workbench organization', chart='general')['company_id']
    bank = client.account.create(company=company, name='Statement checking', type='bank')['id']
    equity = client.account.create(company=company, name='Statement equity', type='equity')['id']
    client.run('journal post', {'date': '2026-01-10', 'lines': [
        {'account': bank, 'side': 'debit', 'amount': '250.00'},
        {'account': equity, 'side': 'credit', 'amount': '250.00'}]},
        company=company, reason='Seed something to reconcile')
    login = os_login()
    client.run('user set-password', {'username': login, 'password': PASSWORD})

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.close()
    handle = start_serving(data_root, client_version(), bind=f'127.0.0.1:{port}',
                           secure_cookies=False, publish_descriptor=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post('/login', json={'username': login, 'password': PASSWORD}).status_code == 200
        yield type('Workbench', (), {'browser': browser, 'company_id': company, 'bank': bank})
    finally:
        handle.stop()


def _steps(company_id):
    return {item.step.id: item for panel in home.resolve(company_id) for item in panel.steps}


def test_the_reconcile_tile_is_live_and_lands_on_a_usable_page(workbench):
    """The whole availability contract, end to end: registered, routed, declared, navigated."""
    item = _steps(workbench.company_id)['reconcile']
    assert item.live, item.reason
    assert item.href == f'/c/{workbench.company_id}/reconcile-opening/start'
    navigate_witness(workbench.browser, item, 'reconcile')
    # A write-labelled tile has to be backed by a command that writes -- at least one, since
    # seeing what can be cleared is as much a part of this errand as clearing it.
    assert any(registry.get(name).is_write for name in item.step.action.commands)
    assert all(registry.get(name) is not None for name in item.step.action.commands)


def test_the_page_asks_for_what_the_command_takes(workbench):
    page = workbench.browser.get(f'/c/{workbench.company_id}/reconcile-opening/start')
    assert page.status_code == 200
    # The three things adopting an account actually needs from a person.
    for field in ('f:account', 'f:opening_date', 'f:entered_balance'):
        assert f'name="{field}"' in page.text, field
    assert '<form' in page.text and 'method="post"' in page.text.lower()


def test_every_reconcile_command_has_a_page_that_answers(workbench):
    """Each of the four, reached the way the workbench reaches any command."""
    for name in ('reconcile opening start', 'reconcile start', 'reconcile mark', 'reconcile finish'):
        cmd = registry.get(name)
        assert cmd is not None and not cmd.local_only, name
        url = f"/c/{workbench.company_id}/{cmd.noun.replace(' ', '-')}/{cmd.verb}"
        page = workbench.browser.get(url, follow_redirects=False)
        assert page.status_code == 200, (name, url, page.status_code)
        assert 'name="password"' not in page.text, (name, 'sent a signed-in reader to log in')
