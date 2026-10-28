"""R112 increment 3: flags, the customer's method and batches (design/document-delivery.md §5, §11).

Every selection drafts exactly the hand-worked set, skips are named with why, a rerun duplicates nothing, a job takes
its parent's method, the auto-flag date and an explicit opt-out decide the queue, a batch goes in chunks and resumes,
and a batch is cancelled as one unit with the messages that block named. The demo company is batched too.
BF_DELIVERY_LARGE=1000 runs the measurement on 1,000 documents (selection, chunk and whole-batch times).
"""
import json
import os
import time

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401


def _names(items):
    return sorted((i['customer_name'], tuple(i['documents'])) for i in items)


def _skips(items):
    return sorted((i['customer_name'], tuple(i['documents']), i['reason']) for i in items)


@pytest.fixture
def shop(books):  # noqa: F811
    client, company, run = books['client'], books['company'], books['run']
    visit = client.item.create(company=company, name='Site visit', type='service', income_account_id=books['income'],
                               price='50.00')['id']
    trade = run('customer-type create', {'name': 'Trade'})['id']
    adams = books['customer']
    run('customer update', {'customer': adams, 'email': 'pay@adams.example', 'preferred_delivery_method': 'email',
                            'customer_type_id': trade})
    kitchen = client.customer.create(company=company, name='Kitchen', parent_id=adams)['id']
    baker = client.customer.create(company=company, name='Baker Homes', email='ap@baker.example',
                                   preferred_delivery_method='mail')['id']
    cole = client.customer.create(company=company, name='Cole Bros', preferred_delivery_method='email')['id']
    dunn = client.customer.create(company=company, name='Dunn Ltd', email='ap@dunn.example',
                                  preferred_delivery_method='none')['id']
    posted = {}
    for number, customer, date in (('I-1', adams, '2026-03-01'), ('I-2', adams, '2026-04-01'),
                                   ('I-3', kitchen, '2026-04-02'), ('I-4', baker, '2026-04-03'),
                                   ('I-5', cole, '2026-04-04'), ('I-6', dunn, '2026-04-05')):
        posted[number] = run('invoice post', {'number': number, 'date': date, 'customer': customer,
                                              'lines': [{'item': visit, 'quantity': '1'}]}, reason='Seed')['id']
    return dict(books, visit=visit, adams=adams, kitchen=kitchen, baker=baker, cole=cole, dunn=dunn, posted=posted,
                trade=trade)


def test_the_queue_follows_flags_the_auto_flag_date_and_a_jobs_inherited_method(shop):
    run = shop['run']
    assert run('delivery queue', {})['rows'] == []
    run('company update', {'delivery_auto_flag_from': '2026-04-01'})
    queue = run('delivery queue', {})
    got = {(r['number'], r['why'], r['method'], r['problem']) for r in queue['rows']}
    # I-1 predates the date, I-4's customer prefers mail, I-6's has no preference; the job takes Adams's email.
    assert got == {('I-2', 'auto', 'email', None), ('I-3', 'auto', 'email', None), ('I-5', 'auto', 'email',
                                                                                       'no address')}
    assert next(r for r in queue['rows'] if r['number'] == 'I-3')['to'] == ['pay@adams.example']
    assert run('delivery flag', {'kind': 'invoice', 'document': 'I-6'})['queued'] is True
    out = run('delivery unflag', {'kind': 'invoice', 'document': 'I-2'})
    assert out['flag'] == 'not-to-email' and out['queued'] is False and 'not to email' in out['why']
    assert {r['number'] for r in run('delivery queue', {})['rows']} == {'I-3', 'I-5', 'I-6'}
    # Flagging twice changes nothing; a flag on a document already drafted waits for that message.
    again = run('delivery flag', {'kind': 'invoice', 'document': 'I-6'})
    assert again['version'] == 1
    run('delivery draft', {'kind': 'invoice', 'document': 'I-3'})
    assert {r['number'] for r in run('delivery queue', {})['rows']} == {'I-5', 'I-6'}
    assert [c['customer_name'] for c in run('delivery queue', {'method': 'mail'})['customers']] == ['Baker Homes']
    with pytest.raises(BookflowError) as refused:
        run('company update', {'delivery_auto_flag_from': '2026-13-01'})
    assert refused.value.code == 'E_VALIDATION'


def test_each_selection_drafts_the_hand_worked_set_and_names_every_skip(shop):
    run = shop['run']
    run('company update', {'delivery_auto_flag_from': '2026-04-01'})
    # --queued: I-2 and I-3 (the job inherits Adams's email); I-5 has no address.
    preview = run('delivery batch', {'queued': True}, dry_run=True)
    assert preview['state'] == 'preview' and preview['planned'] == 2
    assert _names(preview['would_draft']) == [('Adams Plumbing', ('Invoice I-2',)),
                                              ('Adams Plumbing:Kitchen', ('Invoice I-3',))]
    assert _skips(preview['skipped']) == [('Cole Bros', ('Invoice I-5',), 'no_address')]
    assert run('delivery query', {'status': 'all'})['rows'] == []  # a dry run drafts nothing
    queued = run('delivery batch', {'queued': True}, reason='Email the queue')
    assert queued['state'] == 'done' and len(queued['messages']) == 2 and queued['next'] is None
    rerun = run('delivery batch', {'queued': True}, reason='Again')
    assert rerun['state'] == 'nothing' and rerun['messages'] == [] and rerun['batch_id'] is None
    # --unsent with dates and a customer type: only Adams is Trade (its job inherits the type).
    typed = run('delivery batch', {'kind': 'invoice', 'unsent': True, 'customer_type': 'Trade',
                                   'date_from': '2026-03-01', 'date_to': '2026-04-30'}, dry_run=True)
    assert _names(typed['would_draft']) == [('Adams Plumbing', ('Invoice I-1',))]
    assert _skips(typed['skipped']) == [('Adams Plumbing', ('Invoice I-2',), 'in_outbox'),
                                        ('Adams Plumbing:Kitchen', ('Invoice I-3',), 'in_outbox')]
    assert all('already in the outbox as message M-' in s['reason_text'] for s in typed['skipped'])
    unsent = run('delivery batch', {'kind': 'invoice', 'unsent': True}, dry_run=True)
    assert _names(unsent['would_draft']) == [('Adams Plumbing', ('Invoice I-1',)), ('Dunn Ltd', ('Invoice I-6',))]
    assert {(s['customer_name'], s['reason']) for s in unsent['skipped']} == {
        ('Adams Plumbing', 'in_outbox'), ('Adams Plumbing:Kitchen', 'in_outbox'), ('Baker Homes', 'prefers_mail'),
        ('Cole Bros', 'no_address')}
    # Left out from the preview, by key.
    fewer = run('delivery batch', {'kind': 'invoice', 'unsent': True,
                                   'exclude': [shop['posted']['I-6']]}, reason='Unsent invoices')
    assert len(fewer['messages']) == 1 and ('Dunn Ltd', 'excluded') in {(s['customer_name'], s['reason'])
                                                                         for s in fewer['skipped']}
    message = run('delivery show', {'message': fewer['messages'][0]})
    assert message['batch'] == fewer['number'] and message['documents'][0]['source_number'] == 'I-1'
    assert [r['number'] for r in run('delivery query', {'batch': fewer['number']})['rows']] == fewer['messages']


def test_combine_puts_a_customers_documents_in_one_message(shop):
    run = shop['run']
    out = run('delivery batch', {'kind': 'invoice', 'unsent': True, 'combine': True, 'customer': 'Adams Plumbing'},
              reason='One email each')
    # Adams's two invoices go together; the job is its own customer.
    assert out['planned'] == 2 and len(out['messages']) == 2
    rows = {r['customer_name']: r for r in run('delivery query', {'batch': out['number']})['rows']}
    assert rows['Adams Plumbing']['documents'] == ['Invoice I-1', 'Invoice I-2']
    combined = run('delivery show', {'message': rows['Adams Plumbing']['number']})
    assert [d['file_name'] for d in combined['documents']] == ['invoice-I-1.pdf', 'invoice-I-2.pdf']
    assert 'I-1, I-2' in combined['subject']
    # Drafting it again returns it; drafting one of its invoices alone is refused, naming it.
    same = run('delivery draft', {'kind': 'invoice', 'document': 'I-1', 'documents': ['I-2'], 'combine': True})
    assert same['existing'] and same['number'] == combined['number']
    with pytest.raises(BookflowError) as refused:
        run('delivery draft', {'kind': 'invoice', 'document': 'I-2'})
    assert refused.value.details['number'] == combined['number']
    with pytest.raises(BookflowError) as other:
        run('delivery draft', {'kind': 'invoice', 'document': 'I-4', 'documents': ['I-6'], 'combine': True})
    assert other.value.code == 'E_VALIDATION'
    # An edit to one invoice makes the message stale; the refresh redraws both and keeps one message.
    invoice = run('invoice show', {'invoice': 'I-2'})
    run('invoice update', {'invoice': invoice['id'], 'expected_version': invoice['version'], 'memo': 'Later'},
        reason='Correction')
    assert run('delivery show', {'message': combined['number']})['stale'] is True
    refreshed = run('delivery update', {'message': combined['number'], 'refresh': True}, reason='Now')
    assert refreshed['stale'] is False and len(refreshed['documents']) == 2


def test_statements_and_notices_select_one_message_per_customer(shop):
    run = shop['run']
    out = run('delivery batch', {'kind': 'statement', 'period': '2026-04', 'balance_over': '0', 'method': 'email'},
              dry_run=True)
    assert _names(out['would_draft']) == [('Adams Plumbing', ('Statement 2026-04-01 to 2026-04-30',)),
                                          ('Adams Plumbing:Kitchen', ('Statement 2026-04-01 to 2026-04-30',))]
    assert {(s['customer_name'], s['reason']) for s in out['skipped']} == {
        ('Baker Homes', 'prefers_mail'), ('Cole Bros', 'no_address'), ('Dunn Ltd', 'not_email')}
    over = run('delivery batch', {'kind': 'statement', 'period': '2026-04', 'balance_over': '100'}, dry_run=True)
    assert over['planned'] == 0 and over['skipped'] == []  # every balance is 50.00
    done = run('delivery batch', {'kind': 'statement', 'period': '2026-04', 'method': 'email'}, reason='Statements')
    assert len(done['messages']) == 2
    rerun = run('delivery batch', {'kind': 'statement', 'period': '2026-04', 'method': 'email'}, dry_run=True)
    assert rerun['planned'] == 0 and {s['reason'] for s in rerun['skipped']} >= {'in_outbox'}
    # Missing specifics are asked for, never guessed.
    for raw, field in (({'kind': 'statement'}, 'period'), ({}, 'selection'), ({'unsent': True}, 'kind'),
                       ({'kind': 'notice'}, 'ready'), ({'queued': True, 'balance_over': '5'}, 'balance_over')):
        with pytest.raises(BookflowError) as refused:
            run('delivery batch', raw, dry_run=True)
        assert refused.value.details['fields'][0]['field'] == field, raw
    run('company update', {'overdue_notice_days': 1, 'overdue_notice_template': 'letter'})
    run('notice generate', {'as_of': '2026-08-10'})
    notices = run('delivery batch', {'kind': 'notice', 'ready': True}, reason='Overdue notices')
    assert _names([*notices['skipped']]) and {s['reason'] for s in notices['skipped']} <= {
        'prefers_mail', 'no_address'}
    assert {r['customer_name'] for r in run('delivery query', {'batch': notices['number']})['rows']} == {
        'Adams Plumbing', 'Adams Plumbing:Kitchen', 'Dunn Ltd'}


def test_a_batch_goes_in_chunks_resumes_and_cancels_as_one_unit(shop, monkeypatch):
    from bookflow.company import delivery_batches
    monkeypatch.setattr(delivery_batches, 'CHUNK', 2)
    run = shop['run']
    first = run('delivery batch', {'kind': 'invoice', 'unsent': True, 'send': False}, reason='All unsent')
    assert first['planned'] == 4 and len(first['messages']) == 2 and first['state'] == 'drafting'
    assert first['next'] == {'batch': first['number']} and first['remaining'] == 2
    # Something drafted by hand meanwhile is skipped then, naming its message.
    by_hand = run('delivery draft', {'kind': 'invoice', 'document': 'I-6'})
    second = run('delivery batch', first['next'], reason='Go on')
    assert second['state'] == 'done' and len(second['messages']) == 1 and second['next'] is None
    assert any(s['reason'] == 'in_outbox' and s['message'] == by_hand['number'] for s in second['skipped'])
    assert run('delivery batch', first['next'], reason='Done already')['messages'] == []
    numbers = [*first['messages'], *second['messages']]
    # Release one and export another: both block the cancel and are named; the rest are cancelled.
    run('delivery send', {'messages': [numbers[0]]}, reason='Send it')
    run('delivery export', {'messages': [numbers[1]]}, reason='By hand')
    with pytest.raises(BookflowError) as reasonless:
        run('delivery batch-cancel', {'batch': first['number']})
    assert reasonless.value.code == 'E_REASON_REQUIRED'
    cancel = run('delivery batch-cancel', {'batch': first['number']}, reason='Wrong month')
    assert cancel['state'] == 'cancelled' and cancel['cancelled'] == numbers[2:]
    assert {(k['message'], k['reason']) for k in cancel['kept']} == {(numbers[0], 'released'),
                                                                     (numbers[1], 'exported')}
    assert run('delivery show', {'message': numbers[2]})['status'] == 'cancelled'
    assert run('delivery show', {'message': by_hand['number']})['status'] == 'draft'
    history = run('delivery history', {'message': numbers[2]})['events']
    assert history[-1]['event'] == 'cancelled' and history[-1]['details']['batch'] == first['batch_id']
    assert run('delivery batch', first['next'], reason='After cancel')['state'] == 'cancelled'


def test_a_batch_with_send_releases_and_the_batch_releases_and_approves(shop):
    run = shop['run']
    sent = run('delivery batch', {'kind': 'invoice', 'unsent': True, 'send': True}, reason='Send all')
    assert sent['released'] == len(sent['messages']) == 4
    rows = run('delivery query', {'batch': sent['number'], 'status': 'all'})['rows']
    assert {r['status'] for r in rows} == {'ready'} and not any(r['waiting_for_go'] for r in rows)
    drafts = run('delivery batch', {'kind': 'statement', 'period': '2026-04'}, reason='Statements')
    out = run('delivery send', {'batch': drafts['number']}, reason='Send the batch')
    assert out['count'] == len(drafts['messages'])
    assert run('delivery approve', {'batch': drafts['number']}, reason='Go')['count'] == 0  # a person's release is the go


def test_the_demo_company_batches_its_queue_and_reruns_nothing(client):
    company = client.company.list()['items'][0]['company_id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    for name, address, method in (('Lakeside Dental', 'ap@lakeside.example', 'email'),
                                  ('Riverside Apartments', 'office@riverside.example', 'email'),
                                  ('Commercial Example Customer', 'ap@commercial.example', 'mail'),
                                  ('Billing Example Customer', None, 'email')):
        run('customer update', {'customer': name, 'preferred_delivery_method': method,
                                **({'email': address} if address else {})})
    run('company update', {'delivery_auto_flag_from': '2026-01-01'})
    queue = run('delivery queue', {})
    assert {r['customer_name'] for r in queue['rows'] if not r['problem']} == {'Lakeside Dental',
                                                                                'Riverside Apartments'}
    preview = run('delivery batch', {'queued': True}, dry_run=True)
    assert preview['planned'] == len([r for r in queue['rows'] if not r['problem']])
    assert {s['reason'] for s in preview['skipped']} == {'no_address'}
    done = run('delivery batch', {'queued': True}, reason='Email the queue')
    assert len(done['messages']) == preview['planned']
    assert run('delivery batch', {'queued': True}, reason='Again')['messages'] == []
    unsent = run('delivery batch', {'kind': 'invoice', 'unsent': True}, dry_run=True)
    assert {s['reason'] for s in unsent['skipped']} <= {'in_outbox', 'prefers_mail', 'no_address'}
    assert 'prefers_mail' in {s['reason'] for s in unsent['skipped']}


@pytest.mark.skipif(not os.environ.get('BF_DELIVERY_LARGE'), reason='measurement: BF_DELIVERY_LARGE=1000')
@pytest.mark.timeout(3600)
def test_measure_a_batch_of_a_thousand_invoices_and_two_hundred_statements(books):  # noqa: F811
    from bookflow.company import delivery_batches
    client, company, run = books['client'], books['company'], books['run']
    size = int(os.environ['BF_DELIVERY_LARGE'])
    visit = client.item.create(company=company, name='Site visit', type='service', income_account_id=books['income'],
                               price='50.00')['id']
    customers = [client.customer.create(company=company, name=f'Customer {n:04d}', email=f'ap{n}@example.com',
                                        preferred_delivery_method='email')['id'] for n in range(max(1, size // 5))]
    for n in range(size):
        run('invoice post', {'number': f'L-{n:05d}', 'date': '2026-04-01', 'customer': customers[n % len(customers)],
                             'lines': [{'item': visit, 'quantity': '1'}]}, reason='Seed')
    timings = {}
    started = time.perf_counter()
    preview = run('delivery batch', {'kind': 'invoice', 'unsent': True}, dry_run=True)
    timings['select_invoices_s'] = round(time.perf_counter() - started, 2)
    assert preview['planned'] == size
    chunks, started = [], time.perf_counter()
    out = run('delivery batch', {'kind': 'invoice', 'unsent': True}, reason='Every unsent invoice')
    chunks.append(time.perf_counter() - started)
    while out['next']:
        started = time.perf_counter()
        out = run('delivery batch', out['next'], reason='Go on')
        chunks.append(time.perf_counter() - started)
    timings.update(invoices=size, chunk=delivery_batches.CHUNK, whole_batch_s=round(sum(chunks), 1),
                   first_call_s=round(chunks[0], 2), chunk_median_s=round(sorted(chunks)[len(chunks) // 2], 2),
                   chunk_max_s=round(max(chunks), 2), per_draft_s=round(sum(chunks) / size, 3))
    assert out['drafted'] == size and out['state'] == 'done'
    assert run('delivery batch', {'kind': 'invoice', 'unsent': True}, dry_run=True)['planned'] == 0
    started = time.perf_counter()
    statements = run('delivery batch', {'kind': 'statement', 'period': '2026-04'}, dry_run=True)
    timings['select_statements_s'] = round(time.perf_counter() - started, 2)
    assert statements['planned'] == len(customers)
    chunks, out = [], {'next': None}
    started = time.perf_counter()
    out = run('delivery batch', {'kind': 'statement', 'period': '2026-04'}, reason='Statements')
    chunks.append(time.perf_counter() - started)
    while out['next']:
        started = time.perf_counter()
        out = run('delivery batch', out['next'], reason='Go on')
        chunks.append(time.perf_counter() - started)
    timings.update(statements=len(customers), statements_whole_s=round(sum(chunks), 1),
                   statement_chunk_median_s=round(sorted(chunks)[len(chunks) // 2], 2))
    started = time.perf_counter()
    cancel = run('delivery batch-cancel', {'batch': out['number']}, reason='Measured')
    timings['cancel_statement_batch_s'] = round(time.perf_counter() - started, 2)
    assert len(cancel['cancelled']) == len(customers)
    print('batch measurements', json.dumps(timings))
