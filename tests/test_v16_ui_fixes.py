"""v1.6 browser fixes, server side: the statement import page and the backups list.

A pasted statement keeps its line breaks (a text area, with a file chooser beside it); Preview and
Submit both show the line-by-line outcome with money as money; Submit lands back on the import
page holding that outcome, not on the Overview with only "Saved successfully"; and the backups
list carries the phone card labels other lists use.
"""
import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version

PASSWORD = 'correct-horse-battery'
WB = {'X-Bookflow-Workbench': '1'}
CSV = (Path(__file__).parent / 'fixtures' / 'statements' / 'checking-2026-09.csv').read_text()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    root = tmp_path / 'v16-ui'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.demo.reset()
    login = os_login()
    client.run('user set-password', {'username': login, 'password': PASSWORD})
    company = client.company.list()['items'][0]['company_id']
    client.run('reconcile opening start', {
        'operation_key': 'v16-open', 'account': 'Checking', 'opening_date': '2025-12-31',
        'entered_balance': '0.00', 'evidence': {'format': 1, 'statement_reference': None,
                                                'entered_text': 'First statement'}, 'references': []},
        company=company, reason='Adopt checking')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.close()
    handle = start_serving(root, client_version(), bind=f'127.0.0.1:{port}',
                           secure_cookies=False, publish_descriptor=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post('/login', json={'username': login, 'password': PASSWORD}).status_code == 200
        yield type('Demo', (), {'browser': browser, 'company': company, 'client': client})
    finally:
        handle.stop()


def _form(action):
    return {'originals': '{}', 'f:account': 'Checking', 'f:content': CSV, 'f:start': 'true',
            'ctx:reason': 'Import the September statement', 'action': action}


def _outcome(page):
    section = page[page.index('data-statement-import-result'):]
    return section[:section.index('</section>')]


def test_the_statement_field_is_a_text_area_with_a_file_chooser(demo):
    page = demo.browser.get(f'/c/{demo.company}/reconcile/import').text
    assert re.search(r'<textarea[^>]*name="f:content"', page), 'pasted statement loses its line breaks'
    assert re.search(r'<input type="file"[^>]*data-text-file-into=', page)
    # Only the hinted field changes: the account is still an ordinary control.
    assert not re.search(r'<textarea[^>]*name="f:account"', page)


def test_preview_and_submit_show_each_line_and_money_as_money(demo):
    url = f'/c/{demo.company}/reconcile/import'
    preview = demo.browser.post(url, headers=WB, data=_form('preview'))
    assert preview.status_code == 200, preview.text[:2000]
    shown = _outcome(preview.text)
    assert 'Preview (nothing written)' in shown
    assert '$6,236.95' in shown and '623695' not in shown.split('Technical result')[0]
    assert shown.count('data-import-status="matched"') == 8
    assert shown.count('data-import-status="suggested"') == 1
    assert shown.count('data-import-status="unmatched"') == 1
    assert 'Could be:' in shown and 'Not in the books' in shown

    done = demo.browser.post(url, headers=WB, data=_form('submit'), follow_redirects=False)
    assert done.status_code == 303, done.text[:2000]
    assert done.headers['location'].startswith(url + '?flash='), done.headers['location']
    landed = demo.browser.get(done.headers['location']).text
    shown = _outcome(landed)
    assert 'Statement imported' in shown and '$6,236.95' in shown
    assert shown.count('data-import-status="matched"') == 8
    assert 'Ticked by this import' in shown
    assert re.search(r'href="/c/[^"]+/reconcile/mark\?f:draft=[^"]+"[^>]*data-import-open-draft', shown)


def test_the_backups_list_folds_into_cards_on_a_phone(demo):
    assert demo.browser.post(f'/c/{demo.company}/company/backup', headers=WB).status_code == 200
    page = demo.browser.get(f'/c/{demo.company}/company/backup').text
    table = page[page.index('<table'):page.index('</table>')]
    assert 'class="list-table' in table
    assert 'data-label="File"' in table and 'data-label="Checkpoint"' in table
