"""R152: Print Checks through Python, the CLI, HTTP and MCP; the Print Checks page; agents refused.

Each surface gets an identical copy of the demo. It sets the printer up, prints an alignment sample,
writes two checks to print later, prints them from 8001, says the second misprinted, prints it again
(8003), reprints the first under a new number (8004), and reads the run, its PDF and the queue back.
Every surface must return the same numbers.
"""
import base64
import re

import anyio
import pytest
from fastapi.testclient import TestClient

from tests.conftest import hosted_call, make_agent
from tests.mcp_matrix_support import Matrix
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('check print', 'check print-alignment', 'check print-cancel', 'check print-confirm',
                      'check print-pdf', 'check print-queue', 'check print-run', 'check print-setup', 'check reprint'))
HEADERS = {'X-Bookflow-Workbench': '1', 'X-Bookflow-Client-Name': 'bookflow-workbench'}
REASON = {'X-Bookflow-Reason': 'check printing witness'}


@pytest.mark.timeout(900)
def test_the_same_check_printing_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                bank = await call('account create', {'name': 'Print Checking', 'type': 'bank'})
                expense = await call('account create', {'name': 'Print Supplies', 'type': 'expense'})
                vendor = await call('vendor create', {'name': 'Print Paper Co'})
                setup = await call('check print-setup', {'account': bank['id'], 'style': 'standard',
                                                         'offset_x_hundredths': 4, 'offset_y_hundredths': -2})
                sample = await call('check print-alignment', {'account': bank['id']})
                written = []
                for amount in ('120.00', '0.05'):
                    written.append(await call('check post', {
                        'account': bank['id'], 'pay_to': {'name_type': 'vendor', 'name_id': vendor['id']},
                        'date': '2026-10-20', 'amount': amount, 'to_be_printed': True,
                        'expenses': [{'account': expense['id'], 'amount': amount}]}))
                queue = await call('check print-queue', {'account': bank['id']})
                printed = await call('check print', {'account': bank['id'], 'first_number': '8001'})
                pdf = await call('check print-pdf', {'run': printed['id']})
                answered = await call('check print-confirm', {'run': printed['id'], 'failed': ['8002']})
                again = await call('check print', {'account': bank['id']})
                cancelled = await call('check print-cancel', {'run': again['id']})
                again = await call('check print', {'account': bank['id']})
                await call('check print-confirm', {'run': again['id']})
                reprinted = await call('check reprint', {'check': written[0]['id'], 'new_number': '8004'})
                shown = await call('check print-run', {'run': reprinted['id']})
                results[surface] = (setup, sample, queue, printed, pdf, answered, cancelled, again, reprinted, shown)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (setup, sample, queue, printed, pdf, answered, cancelled, again, reprinted, shown) in results.items():
        assert (setup['style'], setup['offset_x_hundredths'], setup['offset_y_hundredths']) == ('standard', 4, -2), surface
        assert base64.b64decode(sample['content_base64']).startswith(b'%PDF'), surface
        assert queue['count'] == 2 and queue['accounts'][0]['total']['amount'] == '120.05', surface
        assert [i['check_number'] for i in printed['items']] == ['8001', '8002'], surface
        assert pdf['checks'] == 2 and pdf['pages'] == 1, surface
        assert answered['spoiled_numbers'] == ['8002'], surface
        assert cancelled['status'] == 'cancelled', surface
        assert [i['check_number'] for i in again['items']] == ['8003'], surface
        assert reprinted['spoiled_numbers'] == ['8001'] and shown['items'][0]['check_number'] == '8004', surface
        shapes.add(repr(([i['amount_in_words'] for i in printed['items']], answered['status'], shown['kind'])))
    assert len(shapes) == 1


def _text(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


def test_the_print_checks_page_prints_opens_the_pdf_and_asks_did_they_print_ok(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post('/login', json={'username': hosted.login, 'password': PASSWORD}).status_code == 200
    base, page = f'/companies/{hosted.company_id}/commands', f'/c/{hosted.company_id}/print-checks'

    def run(name, body):
        reason = {} if name.split()[-1] in ('show', 'query', 'list', 'print-queue') else REASON
        answered = browser.post(f'{base}/{name.replace(" ", ".")}', json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    bank = run('account create', {'name': 'PC Checking', 'type': 'bank'})['id']
    expense = run('account create', {'name': 'PC Supplies', 'type': 'expense'})['id']
    vendor = run('vendor create', {'name': 'PC Vendor'})['id']
    check = run('check post', {'account': bank, 'pay_to': {'name_type': 'vendor', 'name_id': vendor}, 'date': '2026-10-20',
                               'amount': '42.00', 'to_be_printed': True,
                               'expenses': [{'account': expense, 'amount': '42.00'}]})
    assert '/print-checks' in browser.get(f'/c/{hosted.company_id}/_group/banking', headers=HEADERS).text
    shown = browser.get(page + f'?account={bank}', headers=HEADERS)
    assert shown.status_code == 200 and f'data-queued="{check["id"]}"' in shown.text and 'hx-post' in shown.text

    def post(**form):
        answered = browser.post(page, data={'ctx:reason': 'page witness', 'account': bank, **form}, headers=HEADERS)
        assert answered.status_code == 200, _text(answered.text)[:800]
        return answered

    printed = post(action='print', first_number='9001', style='voucher', **{f'pick:{check["id"]}': 'yes'})
    assert 'numbers 9001 to 9001' in _text(printed.text) and 'Did checks print OK?' in printed.text
    run_id = re.search(r'data-confirm-run="([0-9A-Z]+)"', printed.text).group(1)
    pdf = browser.get(f'/c/{hosted.company_id}/print-checks/run/{run_id}.pdf', headers=HEADERS)
    assert pdf.status_code == 200 and pdf.headers['content-type'] == 'application/pdf' and pdf.content.startswith(b'%PDF')
    sample = browser.get(f'/c/{hosted.company_id}/print-checks/alignment.pdf?account={bank}&offset_x_hundredths=5',
                         headers=HEADERS)
    assert sample.status_code == 200 and sample.content.startswith(b'%PDF')
    confirmed = post(action=f'confirm:{run_id}', **{f'ok:{run_id}': 'yes'})
    assert '1 check(s) printed.' in _text(confirmed.text) and 'data-confirm-run' not in confirmed.text
    assert run('check show', {'check': check['id']})['document']['check_number'] == '9001'
    saved = post(action='setup', setup_style='standard', offset_x_hundredths='3', offset_y_hundredths='-1')
    assert 'standard checks, horizontal 3, vertical -1' in _text(saved.text)
    refused = browser.post(page, data={'action': 'print', 'account': bank}, headers=HEADERS)
    assert refused.status_code == 400 and 'Tick at least one check' in _text(refused.text)


def test_an_agent_prepares_a_run_but_only_a_person_confirms_cancels_or_reprints(hosted):
    hosted.ok('user.add', {'username': 'principal-cp', 'company': hosted.company_id, 'role': 'owner',
                           'password': 'pw-principal-cp-12345'}, headers=REASON)
    agent = make_agent(hosted_call(hosted), 'print-agent', principals='principal-cp', company=hosted.company_id,
                       role='admin')
    secret = hosted.ok('token.issue', {'user': agent, 'principal': 'principal-cp', 'label': 'print'},
                       headers=REASON)['secret']
    as_agent = {'Authorization': 'Bearer ' + secret, **REASON}
    cid = hosted.company_id

    def agent_call(name, body):
        return hosted.call(name, body, company=cid, headers=as_agent)

    bank = hosted.ok('account.create', {'name': 'AG Checking', 'type': 'bank'}, company=cid, headers=REASON)['id']
    expense = hosted.ok('account.create', {'name': 'AG Supplies', 'type': 'expense'}, company=cid, headers=REASON)['id']
    vendor = hosted.ok('vendor.create', {'name': 'AG Vendor'}, company=cid, headers=REASON)['id']
    queued = agent_call('check.post', {'account': bank, 'date': '2026-10-20', 'amount': '10.00', 'to_be_printed': True,
                                       'pay_to': {'name_type': 'vendor', 'name_id': vendor},
                                       'expenses': [{'account': expense, 'amount': '10.00'}]})
    assert queued.status_code == 200, queued.text
    prepared = agent_call('check.print', {'account': bank, 'first_number': '501'})
    assert prepared.status_code == 200, prepared.text
    run_id = prepared.json()['id']
    for name, body in (('check.print-confirm', {'run': run_id}), ('check.print-cancel', {'run': run_id})):
        refused = agent_call(name, body)
        assert refused.status_code == 403, refused.text
        assert refused.json()['code'] == 'E_PERMISSION' and refused.json()['details']['reason'] == 'people_only'
    confirmed = hosted.ok('check.print-confirm', {'run': run_id}, company=cid, headers=REASON)
    assert confirmed['status'] == 'confirmed'
    refused = agent_call('check.reprint', {'check': queued.json()['id']})
    assert refused.status_code == 403 and refused.json()['details']['reason'] == 'people_only'
