"""R112 increment 1: messages and the outbox (design/document-delivery.md §2, §3, §11).

A person drafts, edits, exports and marks sent an invoice, sales receipt, estimate, statement and overdue notice;
the attached bytes are exactly what `documents.render` gives; the outbox shows each message's history. Nothing
leaves the machine: no sending connection exists in this build.
"""
import base64
import email
import email.policy
import hashlib
import json
import time

import pytest

from bookflow.documents import render
from tests.test_document_print import books, commands  # noqa: F401 - fixture
from tests.test_row3_host import hosted  # noqa: F401 - fixture
from tests.test_delivery_close import own

PERIOD = {'date_from': '2026-06-01', 'date_to': '2026-06-30'}


def _refused(hosted, command, body, headers=None):
    r = hosted.call(command.replace(' ', '.'), body, company=hosted.company_id,
                    headers=headers if headers is not None else hosted.bearer)
    assert r.status_code >= 400, r.text
    return r.json()


def _eml(run, numbers):
    out = run('delivery export', {'messages': numbers})
    return out, email.message_from_bytes(base64.b64decode(out['content_base64']), policy=email.policy.default)


@pytest.fixture
def mail(books, hosted):  # noqa: F811
    run = books['run']
    run('customer update', {'customer': books['customer'], 'email': 'accounts@harbourmills.example',
                            'cc_email': 'owner@harbourmills.example'})
    run('company update', {'delivery_reply_to': 'office@demoplumbing.example', 'overdue_notice_days': 1,
                           'overdue_notice_template': 'letter'})
    notice = run('notice generate', {'as_of': '2026-08-10'})['generated']
    books['notice'] = next(n for n in notice if n['customer_id'] == books['customer'])
    return books


def _reader(run):
    return lambda name, raw, company: run(name, raw)


def test_a_draft_of_each_kind_carries_exactly_what_render_gives(mail, hosted):  # noqa: F811
    run, cid = mail['run'], hosted.company_id
    cases = (
        ('invoice', {'document': 'DOC-100'}, {'document': mail['invoice']['id']}, 'Invoice DOC-100 from '),
        ('sales-receipt', {'document': 'DOC-200'}, {'document': mail['receipt']['id']}, 'Receipt DOC-200 from '),
        ('estimate', {'document': mail['estimate']['number']}, {'document': mail['estimate']['id']}, 'Estimate '),
        ('statement', {'customer': 'Harbour Mills', **PERIOD}, {'customer': mail['customer'], **PERIOD},
         'Statement from '),
        ('notice', {'document': mail['notice']['id']}, {'document': mail['notice']['id']}, 'Past due: Harbour Mills'),
    )
    timings = {}
    for kind, selector, identity, subject in cases:
        started = time.perf_counter()
        message = run('delivery draft', {'kind': kind, **selector})
        timings[kind] = round(time.perf_counter() - started, 3)
        expected = render(_reader(run), cid, kind, identity)
        [attached] = message['documents']
        assert attached['file_name'] == expected.filename and attached['size_bytes'] == len(expected.content)
        assert attached['sha256'] == hashlib.sha256(expected.content).hexdigest(), kind
        assert message['to'] == ['accounts@harbourmills.example'] and message['cc'] == ['owner@harbourmills.example']
        assert message['reply_to'] == 'office@demoplumbing.example' and message['status'] == 'draft'
        assert message['subject'].startswith(subject), message['subject']
        assert message['body'].startswith('Dear Harbour Mills'), message['body']
        assert message['connection'] == 'none' and message['status_label'] == 'Draft'
        # The frozen file is the one the export attaches, byte for byte.
        _, parsed = _eml(run, [message['number']])
        [part] = list(parsed.iter_attachments())
        assert part.get_filename() == expected.filename and part.get_content() == expected.content
    print('draft seconds by kind', json.dumps(timings))
    invoice = run('delivery query', {'kind': 'invoice'})['rows'][0]
    assert invoice['documents'] == ['Invoice DOC-100'] and invoice['status_label'] == 'Draft'
    # The invoice body names its total and due date from the document's own strings.
    shown = run('delivery show', {'message': invoice['number']})
    assert 'DOC-100' in shown['body'] and '2026-07-03' in shown['body']


def test_templates_refuse_unknown_fields_fill_conditionals_and_warn_on_empty_ones(mail, hosted):  # noqa: F811
    run = mail['run']
    refused = _refused(hosted, 'delivery-template create', {'kind': 'invoice', 'name': 'Bad', 'subject': 'Hi',
                                                            'body': 'Total {{document.totl}}'})
    assert refused['code'] == 'E_VALIDATION' and 'document.totl' in refused['message']
    refused = _refused(hosted, 'delivery-template create', {'kind': 'statement', 'name': 'Bad', 'subject': 'Hi',
                                                            'body': '{{document.number}}'})
    assert 'unknown merge field' in refused['message']
    nested = _refused(hosted, 'delivery-template create', {'kind': 'invoice', 'name': 'Bad', 'subject': 'x',
                      'body': '{{if document.po_number}}{{if document.terms}}{{end}}{{end}}'})
    assert 'nested' in nested['message']
    made = run('delivery-template create', {
        'kind': 'invoice', 'name': 'Short', 'is_default': True,
        'subject': 'Inv {{document.number}}{{if document.po_number}} PO {{document.po_number}}{{end}}',
        'body': 'Acct {{customer.account_number}} owes {{document.balance_due}}.'})
    templates = run('delivery-template query', {'kind': 'invoice'})['rows']
    assert [t['name'] for t in templates if t['is_default']] == ['Short'] and len(templates) == 2
    preview = run('delivery-template preview', {'kind': 'invoice', 'document': 'DOC-100'})
    assert preview['subject'] == 'Inv DOC-100' and preview['template_name'] == 'Short'
    assert preview['empty_fields'] == ['customer.account_number'] and 'Acct  owes 200.00.' == preview['body']
    draft = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    assert any('customer.account_number' in w for w in draft['warnings'])
    refused = _refused(hosted, 'delivery-template deactivate', {'template': made['id']})
    assert 'default' in refused['message']
    run('delivery-template update', {'template': 'Invoice', 'kind': 'invoice', 'is_default': True})
    assert run('delivery-template deactivate', {'template': made['id']})['active'] is False
    assert {f['field'] for f in run('delivery-template fields', {'kind': 'notice'})['fields']} >= {'notice.letter'}


def test_one_live_message_per_document_and_again_names_the_earlier_one(mail, hosted):  # noqa: F811
    run = mail['run']
    first = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    same = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    assert same['existing'] and same['number'] == first['number']
    other = _refused(hosted, 'delivery draft', {'kind': 'invoice', 'document': 'DOC-100', 'to': ['x@example.com']})
    assert other['code'] == 'E_WORK_DEPENDENCY' and other['details']['number'] == first['number']
    run('delivery send', {'messages': [first['number']]})
    run('delivery mark-sent', {'message': first['number'], 'method': 'webmail'}, )
    sent = _refused(hosted, 'delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    assert sent['code'] == 'E_WORK_DEPENDENCY' and sent['details']['status'] == 'sent'
    again = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100', 'again': True})
    assert again['resend_of'] == first['number'] and again['number'] != first['number']
    assert run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100', 'again': True})['existing']


def test_a_changed_or_voided_document_holds_its_message(mail, hosted):  # noqa: F811
    run = mail['run']
    run('invoice post', {'number': 'DOC-101', 'date': '2026-06-04', 'due_date': '2026-07-04',
                         'customer': mail['customer'], 'lines': [{'item': mail['labour'], 'quantity': '1'}]})
    changed = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-101'})
    voided = run('delivery draft', {'kind': 'sales-receipt', 'document': 'DOC-200'})
    assert not changed['stale']
    invoice = run('invoice show', {'invoice': 'DOC-101'})
    hosted.ok('invoice.update', {'invoice': 'DOC-101', 'expected_version': invoice['version'], 'memo': 'Revised'},
              company=hosted.company_id, headers={'X-Bookflow-Reason': 'memo fix'})
    shown = run('delivery show', {'message': changed['number']})
    assert shown['stale'] and shown['stale_reason'] == 'document_changed'
    released = run('delivery send', {'messages': [changed['number']]})
    assert released['messages'][0]['status'] == 'held' and released['messages'][0]['hold_reason'] == 'document_changed'
    receipt = run('sales-receipt show', {'sales_receipt': 'DOC-200'})
    hosted.ok('sales-receipt.void', {'sales_receipt': 'DOC-200', 'expected_version': receipt['version']},
              company=hosted.company_id, headers={'X-Bookflow-Reason': 'rung up twice'})
    exported = run('delivery export', {'messages': [voided['number']]})
    assert exported['held'] == [voided['number']] and exported['content_base64'] is None
    held = run('delivery show', {'message': voided['number']})
    assert held['status'] == 'held' and held['hold_reason'] == 'document_voided'
    assert _refused(hosted, 'delivery draft', {'kind': 'sales-receipt', 'document': 'DOC-200'})['code'] == \
        'E_VALIDATION'
    events = [e['event'] for e in run('delivery history', {'message': voided['number']})['events']]
    assert events == ['drafted', 'held']


def test_the_eml_parses_with_the_standard_library(mail, hosted):  # noqa: F811
    run = mail['run']
    one = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100', 'bcc': ['books@demoplumbing.example']})
    two = run('delivery draft', {'kind': 'estimate', 'document': mail['estimate']['number']})
    run('delivery update', {'message': one['number'], 'body': 'Attached, thanks.\n\nPat'})
    out, parsed = _eml(run, [one['number']])
    assert out['filename'] == f"message-{one['number']}.eml" and out['media_type'] == 'message/rfc822'
    assert parsed['To'] == 'accounts@harbourmills.example' and parsed['Cc'] == 'owner@harbourmills.example'
    assert parsed['Bcc'] == 'books@demoplumbing.example' and parsed['X-Unsent'] == '1'
    assert parsed['Message-ID'] == one['message_id'] and parsed['Subject'] == one['subject']
    assert parsed.get_body(('plain',)).get_content().replace('\r\n', '\n').strip() == 'Attached, thanks.\n\nPat'
    both = run('delivery export', {'messages': [one['number'], two['number']]})
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(base64.b64decode(both['content_base64']))).namelist()
    assert both['media_type'] == 'application/zip' and sorted(names) == sorted(
        f'message-{n}.eml' for n in (one['number'], two['number']))
    assert [e['event'] for e in run('delivery history', {'message': one['number']})['events']] == [
        'drafted', 'edited', 'exported', 'exported']


def test_only_a_person_marks_a_message_sent_and_an_agents_release_waits_for_a_go(mail, hosted):  # noqa: F811
    from tests.test_people_only import REASON, _agent
    _, as_agent = _agent(hosted)
    cid = hosted.company_id
    drafted = hosted.ok('delivery.draft', {'kind': 'invoice', 'document': 'DOC-100'}, company=cid, headers=as_agent)
    released = hosted.ok('delivery.send', {'messages': [drafted['number']]}, company=cid, headers=as_agent)
    [message] = released['messages']
    assert message['status'] == 'ready' and message['waiting_for_go'] and message['status_label'] == \
        'Waiting for your go'
    assert any("person's go" in w for w in released['warnings'])
    r = hosted.call('delivery.mark-sent', {'message': drafted['number']}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['reason'] == 'capability_not_granted', r.text
    assert r.json()['details']['capability'] == 'delivery.mark-sent' and 'sent by hand' in r.json()['message']
    sent = hosted.ok('delivery.mark-sent', {'message': drafted['number'], 'sent_at': '2026-08-11'}, company=cid,
                     headers={**hosted.bearer, **REASON})
    assert sent['status_label'] == 'Sent by hand' and sent['delivery'] == 'unknown' and sent['sent_via'] == 'manual'
    future = _refused(hosted, 'delivery mark-sent', {'message': drafted['number'], 'sent_at': '2999-01-01'})
    assert future['code'] in ('E_VALIDATION', 'E_WORK_DEPENDENCY')
    history = mail['run']('delivery history', {'message': drafted['number']})['events']
    assert [(e['event'], e['actor_kind']) for e in history] == [
        ('drafted', 'agent'), ('released', 'agent'), ('marked_sent', 'human')]


def test_the_notice_hands_off_to_its_message(mail, hosted):  # noqa: F811
    run, notice = mail['run'], mail['notice']
    message = run('delivery draft', {'kind': 'notice', 'document': notice['id']})
    assert notice['letter'].splitlines()[0] in message['body']
    refused = _refused(hosted, 'notice mark-sent', {'notice': notice['id'], 'method': 'print'})
    assert refused['code'] == 'E_WORK_DEPENDENCY' and refused['details']['number'] == message['number']
    run('delivery mark-sent', {'message': message['number']})
    shown = run('notice show', {'notice': notice['id']})
    assert shown['status'] == 'sent' and shown['sent_method'] == 'email'
    assert run('delivery show', {'message': message['number']})['status'] == 'sent'
    # A cancelled message frees nothing it never held; a cancel needs a reason.
    second = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    assert _refused(hosted, 'delivery cancel', {'message': second['number']}, headers={
        k: v for k, v in hosted.bearer.items()})['code'] in ('E_REASON_REQUIRED', 'E_VALIDATION')


def test_the_email_panel_outbox_and_history_in_the_browser(mail, hosted):  # noqa: F811
    from tests.test_document_print import browser
    from tests.test_row3_host import WB
    web, cid = browser(hosted), hosted.company_id
    invoice_page = web.get(f"/c/{cid}/invoice/{mail['invoice']['id']}", headers=WB)
    assert invoice_page.status_code == 200 and 'data-email-document' in invoice_page.text
    panel = web.get(f"/c/{cid}/delivery/compose?kind=invoice&document={mail['invoice']['id']}", headers=WB)
    assert panel.status_code == 200, panel.text[:2000]
    assert 'No sending connection is set up' in panel.text and 'accounts@harbourmills.example' in panel.text
    form = {'kind': 'invoice', 'document': mail['invoice']['id'], 'to': 'accounts@harbourmills.example',
            'cc': '', 'subject': 'Your invoice DOC-100', 'body': 'Hello,\r\nattached.', 'action': 'save'}
    saved = web.post(f'/c/{cid}/delivery/compose', data=form, headers={**WB, 'HX-Request': 'true'})
    assert saved.status_code == 200 and 'saved as a draft' in saved.text, saved.text[:2000]
    [row] = own(mail['run']('delivery query', {})['rows'])
    assert row['subject'] == 'Your invoice DOC-100' and row['to'] == ['accounts@harbourmills.example']
    bad = web.post(f'/c/{cid}/delivery/compose', data=dict(form, to='not-an-address'),
                   headers={**WB, 'HX-Request': 'true'})
    assert bad.status_code == 200 and 'not an email address' in bad.text
    outbox = web.get(f'/c/{cid}/delivery?status=draft', headers=WB)
    assert outbox.status_code == 200 and row['number'] in outbox.text and 'Nothing leaves this machine' in outbox.text
    eml = web.post(f'/c/{cid}/delivery', data={'message': row['number'], 'action': 'export'},
                   headers={**WB, 'HX-Request': 'true'})
    assert eml.status_code == 200 and email.message_from_bytes(_page_file(eml.text))['X-Unsent'] == '1'
    pdf = web.get(f"/c/{cid}/delivery/{row['number']}/file/1", headers=WB)
    shown = mail['run']('delivery show', {'message': row['number']})
    assert hashlib.sha256(pdf.content).hexdigest() == shown['documents'][0]['sha256']
    sent = web.post(f"/c/{cid}/delivery/{row['number']}", data={'action': 'mark-sent', 'method': 'webmail'},
                    headers={**WB, 'HX-Request': 'true'})
    assert sent.status_code == 200 and 'Sent by hand' in sent.text
    for event in ('drafted', 'exported', 'marked_sent'):
        assert f'data-event="{event}"' in sent.text, event
    settings = web.get(f'/c/{cid}/delivery-settings', headers=WB)
    assert settings.status_code == 200 and 'Nothing leaves this machine' in settings.text
    assert 'data-template="Invoice"' in settings.text
    for path in ('/report/statement', '/notice'):
        assert web.get(f'/c/{cid}{path}', headers=WB).status_code == 200


def _page_file(text):
    """The file an export's page carries: its data link, decoded."""
    import re
    found = re.search(r'href="data:[^;"]+;base64,([^"]+)"[^>]*data-export-file', text)
    assert found, text[-2000:]
    return base64.b64decode(found.group(1))


def _pay_in_full(run, invoice, key):
    method = run('payment-method list', {})['items'][0]['id']
    run('payment receive', {'customer': invoice['customer_id'], 'date': '2026-06-25', 'amount': '120.00',
                            'number': 'RCT-' + key, 'operation_key': key, 'payment_method': method,
                            'applications': {'mode': 'inline', 'items': [
                                {'invoice': invoice['id'], 'expected_version': invoice['version'],
                                 'amount': '120.00'}]}})


def test_a_payment_that_changes_the_pdf_holds_the_message_and_refresh_reattaches_it(mail, hosted):  # noqa: F811
    run = mail['run']
    invoice = run('invoice post', {'number': 'DOC-102', 'date': '2026-06-04', 'due_date': '2026-07-04',
                                   'customer': mail['customer'], 'lines': [{'item': mail['labour'], 'quantity': '1'}]})
    drafted = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-102', 'subject': 'Your invoice'})
    old_sha = drafted['documents'][0]['sha256']
    _pay_in_full(run, run('invoice show', {'invoice': 'DOC-102'}), 'pay-102')
    # The revision did not move, but the PDF drawn now (balance due 0) is not the one frozen.
    assert run('invoice show', {'invoice': 'DOC-102'})['current_revision_id'] == \
        drafted['documents'][0]['source_revision']
    shown = run('delivery show', {'message': drafted['number']})
    assert shown['stale'] and shown['stale_reason'] == 'document_changed'
    released = run('delivery send', {'messages': [drafted['number']]})
    [held] = released['messages']
    assert held['status'] == 'held' and held['hold_reason'] == 'document_changed'
    assert any('--refresh' in w for w in released['warnings'])
    refreshed = run('delivery update', {'message': drafted['number'], 'refresh': True})
    assert refreshed['number'] == drafted['number'] and refreshed['status'] == 'draft' and not refreshed['stale']
    new_sha = refreshed['documents'][0]['sha256']
    assert new_sha != old_sha and new_sha == hashlib.sha256(
        render(_reader(run), hosted.company_id, 'invoice', {'document': invoice['id']}).content).hexdigest()
    assert refreshed['subject'] == 'Your invoice'  # written by the person: kept
    events = run('delivery history', {'message': drafted['number']})['events']
    assert [e['event'] for e in events] == ['drafted', 'held', 'edited']
    detail = events[-1]['details']
    assert detail['refreshed'][0]['sha256'] == [old_sha, new_sha] and detail['kept'] == ['subject']
    assert 'body' in detail['refilled'] or detail['refilled'] == []
    assert run('delivery send', {'messages': [drafted['number']]})['messages'][0]['status'] == 'ready'
    # Nothing left to refresh is refused.
    again = run('delivery draft', {'kind': 'estimate', 'document': mail['estimate']['number']})
    assert _refused(hosted, 'delivery update', {'message': again['number'], 'refresh': True})['code'] == \
        'E_VALIDATION'


def test_a_new_revision_points_the_draft_refusal_at_refresh(mail, hosted):  # noqa: F811
    run = mail['run']
    run('invoice post', {'number': 'DOC-103', 'date': '2026-06-04', 'due_date': '2026-07-04',
                         'customer': mail['customer'], 'lines': [{'item': mail['labour'], 'quantity': '1'}]})
    first = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-103'})
    invoice = run('invoice show', {'invoice': 'DOC-103'})
    hosted.ok('invoice.update', {'invoice': 'DOC-103', 'expected_version': invoice['version'], 'memo': 'Revised'},
              company=hosted.company_id, headers={'X-Bookflow-Reason': 'memo fix'})
    refused = _refused(hosted, 'delivery draft', {'kind': 'invoice', 'document': 'DOC-103'})
    assert refused['code'] == 'E_WORK_DEPENDENCY' and f"delivery update {first['number']} --refresh" in \
        refused['details']['next']
    out = run('delivery update', {'message': first['number'], 'refresh': True})
    assert out['documents'][0]['source_revision'] == run('invoice show', {'invoice': 'DOC-103'})[
        'current_revision_id'] and not out['stale']
    assert run('delivery draft', {'kind': 'invoice', 'document': 'DOC-103'})['number'] == first['number']


def test_exporting_a_held_message_again_holds_nothing_more(mail, hosted):  # noqa: F811
    run = mail['run']
    voided = run('delivery draft', {'kind': 'sales-receipt', 'document': 'DOC-200'})
    receipt = run('sales-receipt show', {'sales_receipt': 'DOC-200'})
    hosted.ok('sales-receipt.void', {'sales_receipt': 'DOC-200', 'expected_version': receipt['version']},
              company=hosted.company_id, headers={'X-Bookflow-Reason': 'rung up twice'})
    for _ in range(3):
        assert run('delivery export', {'messages': [voided['number']]})['held'] == [voided['number']]
    assert [e['event'] for e in run('delivery history', {'message': voided['number']})['events']] == [
        'drafted', 'held']
    assert run('delivery show', {'message': voided['number']})['version'] == 2


def test_blank_template_names_are_refused_in_validation(mail, hosted):  # noqa: F811
    run = mail['run']
    blank = _refused(hosted, 'delivery-template create', {'kind': 'invoice', 'name': '  ', 'subject': 's',
                                                          'body': 'b'})
    assert blank['code'] == 'E_VALIDATION' and blank['details']['fields'][0]['field'] == 'name'
    made = run('delivery-template create', {'kind': 'invoice', 'name': 'Short', 'subject': 's', 'body': 'b'})
    assert _refused(hosted, 'delivery-template update', {'template': made['id'], 'name': ' '})['code'] == \
        'E_VALIDATION'


def test_export_needs_the_access_drafting_needs(mail, hosted):  # noqa: F811
    from tests.test_people_only import _agent
    drafted = mail['run']('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    _, readonly = _agent(hosted, role='readonly')
    r = hosted.call('delivery.export', {'messages': [drafted['number']]}, company=hosted.company_id,
                    headers=readonly)
    assert r.status_code == 403, r.text
    assert [e['event'] for e in mail['run']('delivery history', {'message': drafted['number']})['events']] == [
        'drafted']


def test_cancelling_prunes_files_no_standing_message_carries(mail, hosted):  # noqa: F811
    run = mail['run']
    reason = {**hosted.bearer, 'X-Bookflow-Reason': 'not needed'}
    def cancel(number):
        return hosted.ok('delivery.cancel', {'message': number}, company=hosted.company_id, headers=reason)

    lone = run('delivery draft', {'kind': 'estimate', 'document': mail['estimate']['number']})
    exported = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    run('delivery export', {'messages': [exported['number']]})
    cancel(lone['number'])
    cancel(exported['number'])
    gone = run('delivery show', {'message': lone['number']})
    assert gone['documents'][0]['pdf_path'] is None and gone['documents'][0]['sha256'] == lone['documents'][0][
        'sha256']
    assert run('delivery show', {'message': lone['number'], 'include_files': True})['documents'][0][
        'content_base64'] is None
    kept = run('delivery show', {'message': exported['number'], 'include_files': True})['documents'][0]
    assert kept['pdf_path'] and kept['content_base64']
    # Drafting the same document again freezes the same bytes again, under the same hash.
    redrafted = run('delivery draft', {'kind': 'estimate', 'document': mail['estimate']['number']})
    assert redrafted['documents'][0]['sha256'] == lone['documents'][0]['sha256']
    assert run('delivery show', {'message': redrafted['number'], 'include_files': True})['documents'][0][
        'content_base64']
    # A file another standing message carries stays when one of them is cancelled.
    twin = run('delivery draft', {'kind': 'estimate', 'document': mail['estimate']['number'], 'again': True})
    cancel(redrafted['number'])
    assert run('delivery show', {'message': twin['number'], 'include_files': True})['documents'][0]['content_base64']
    assert [e['event'] for e in run('delivery history', {'message': lone['number']})['events']] == [
        'drafted', 'cancelled']


def test_the_panel_sends_again_exports_through_htmx_and_explains_a_hold(mail, hosted):  # noqa: F811
    from tests.test_document_print import browser
    from tests.test_row3_host import WB
    run = mail['run']
    web, cid, hx = browser(hosted), hosted.company_id, {**WB, 'HX-Request': 'true'}
    first = run('delivery draft', {'kind': 'invoice', 'document': 'DOC-100'})
    run('delivery mark-sent', {'message': first['number']})
    panel = web.get(f"/c/{cid}/delivery/compose?kind=invoice&document={mail['invoice']['id']}", headers=WB)
    assert 'name="again" value="on"' in panel.text
    form = {'kind': 'invoice', 'document': mail['invoice']['id'], 'to': 'accounts@harbourmills.example',
            'subject': 'Invoice again', 'body': 'Again.', 'again': 'on', 'action': 'save'}
    saved = web.post(f'/c/{cid}/delivery/compose', data=form, headers=hx)
    assert saved.status_code == 200 and 'saved as a draft' in saved.text, saved.text[-2000:]
    second = next(r for r in run('delivery query', {})['rows'] if r['number'] != first['number'])
    assert run('delivery show', {'message': second['number']})['resend_of'] == first['number']
    # Export from the panel answers with the page carrying the file.
    exported = web.post(f'/c/{cid}/delivery/compose', data=dict(form, action='export'), headers=hx)
    assert exported.status_code == 200 and email.message_from_bytes(_page_file(exported.text))['Subject'] == \
        'Invoice again'
    # A held message: the page says why, with no file and no crash, from the outbox and from the panel.
    voided = run('delivery draft', {'kind': 'sales-receipt', 'document': 'DOC-200'})
    receipt = run('sales-receipt show', {'sales_receipt': 'DOC-200'})
    hosted.ok('sales-receipt.void', {'sales_receipt': 'DOC-200', 'expected_version': receipt['version']},
              company=cid, headers={'X-Bookflow-Reason': 'rung up twice'})
    held = web.post(f'/c/{cid}/delivery', data={'message': voided['number'], 'action': 'export'}, headers=hx)
    assert held.status_code == 200 and 'held, not exported' in held.text and 'data-export-file' not in held.text
    message = web.post(f"/c/{cid}/delivery/{voided['number']}", data={'action': 'export'}, headers=hx)
    assert message.status_code == 200 and 'voided' in message.text and 'data-export-file' not in message.text
