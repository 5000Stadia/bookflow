"""R186 increment 3: a memorized run's books and scheduling state reverse together."""
import pytest

from bookflow import BookflowError
from bookflow.company import batches
from tests.test_import_documents import books, db, _nets, _due  # noqa: F401

CUSTOMERS = ('Acme Plumbing', 'Bolt Electric', 'Cedar Homes')


def setup_group(books):
    books.run('memorized-group create', {
        'name': 'Monthly service', 'frequency': 'monthly', 'start_date': '2026-01-31',
        'remaining_count': 4, 'mode': 'enter_automatically'})
    templates = []
    for index, customer in enumerate(CUSTOMERS, 1):
        templates.append(books.run('memorized create', {
            'name': 'Service ' + customer, 'command': 'invoice post',
            'payload': {'customer': customer, 'lines': [{'item': 'Labor', 'quantity': str(index)}]},
            'group': 'Monthly service', 'group_ordinal': index}))
    return templates


def process(books, date='2026-01-31'):
    return books.run('memorized process', {'as_of': date}, reason='Enter the monthly service run')


def reverse(books, batch, **context):
    return books.run('batch reverse', {'batch': batch}, reason='Reverse this mistaken memorized run', **context)


def state(books):
    with db(books) as conn:
        return {
            'groups': conn.execute('SELECT id, next_date, remaining_count, status FROM memorized_groups ORDER BY id').fetchall(),
            'templates': conn.execute('SELECT id, next_date, remaining_count, status FROM memorized_transactions ORDER BY id').fetchall(),
            'occurrences': conn.execute('SELECT * FROM memorized_occurrences ORDER BY id').fetchall(),
            'holds': conn.execute('SELECT * FROM scheduled_holds ORDER BY occurrence_id').fetchall(),
        }


def snapshot(books):
    with db(books) as conn:
        return tuple(conn.iterdump())


def ids(out):
    return [row['transaction_id'] for row in out['entered']]


def test_group_process_exact_plan_books_dates_and_corrected_run(books):
    templates = setup_group(books)
    before = state(books)
    out = process(books)
    assert out['entered_count'] == 3
    batch = out['batch_id']
    assert out['next'] == f'batch reverse {batch} --dry-run'
    assert [_due(books, identifier) for identifier in ids(out)] == [8500, 17000, 25500]
    # Three service invoices: receivables debit 510.00 and income credit 510.00.
    assert sorted(_nets(books).values()) == [-51000, 51000]
    assert books.run('memorized-group show', {'memorized_group': 'Monthly service'})['schedule']['next_date'] == '2026-02-28'
    stable = snapshot(books)
    preview = reverse(books, batch, dry_run=True)
    assert snapshot(books) == stable
    document_steps = [step for step in preview['steps'] if step['record_type'] == 'invoice']
    assert [step['record_id'] for step in document_steps] == list(reversed(ids(out)))
    assert all(step['steps'] == ['invoice delete'] for step in document_steps)
    restore_steps = [step for step in preview['steps'] if step['record_type'] != 'invoice']
    assert len(restore_steps) == 1
    assert restore_steps[0]['record_type'] == 'memorized_run'
    changes = restore_steps[0]['changes']
    assert {(row['id'], row['action']) for row in changes['occurrences']} == {
        (row['id'], 'remove') for row in out['entered']}
    assert changes['holds'] == []
    assert len(changes['schedules']) == 1
    assert changes['schedules'][0]['values'] == {'next_date': '2026-01-31', 'remaining_count': 4}
    assert len(preview['steps']) == 4
    assert reverse(books, batch)['state'] == 'reversed'
    assert state(books) == before
    assert _nets(books) == {}
    for identifier in ids(out):
        shown = books.run('invoice show', {'invoice': identifier, 'include_deleted': True})
        assert shown['status'] in ('deleted', 'voided')
        assert shown['settlement_current']['due_minor_units'] == 0

    # Correct the first template, then recreate precisely the January slot, once.
    current = books.run('memorized show', {'memorized': templates[0]['id']})
    books.run('memorized update', {
        'memorized': current['id'], 'expected_version': current['version'],
        'payload': {'customer': CUSTOMERS[0], 'lines': [{'item': 'Labor', 'quantity': '4'}]}})
    corrected = process(books)
    assert corrected['batch_id'] != batch
    assert corrected['entered_count'] == 3
    assert sorted(_due(books, identifier) for identifier in ids(corrected)) == [17000, 25500, 34000]
    assert sorted(_nets(books).values()) == [-76500, 76500]
    assert {row['slot_date'] for row in corrected['entered']} == {'2026-01-31'}
    assert books.run('memorized-group show', {'memorized_group': 'Monthly service'})['schedule']['next_date'] == '2026-02-28'
    assert process(books)['entered_count'] == 0


def test_later_run_named_blocks_then_newest_first_restores_prior_state(books):
    setup_group(books)
    before = state(books)
    first = process(books)
    after_first = state(books)
    second = process(books, '2026-02-28')
    stable = snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reverse(books, first['batch_id'], dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert second['batch_id'] in str(caught.value.details)
        assert snapshot(books) == stable
    reverse(books, second['batch_id'])
    assert state(books) == after_first
    reverse(books, first['batch_id'])
    assert state(books) == before
    assert _nets(books) == {}


def test_payment_on_one_child_names_payment_and_changes_nothing(books):
    setup_group(books)
    out = process(books)
    child = books.run('invoice show', {'invoice': ids(out)[1]})
    payment = books.run('payment receive', {
        'customer': CUSTOMERS[1], 'date': '2026-02-01', 'amount': '10',
        'deposit_to': 'Undeposited Funds', 'payment_method': 'Check', 'operation_key': 'memorized-blocker',
        'applications': {'mode': 'inline', 'items': [
            {'invoice': child['id'], 'expected_version': child['version'], 'amount': '10'}]}})
    payment = books.run('payment show', {'payment': payment['id']})
    stable = snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reverse(books, out['batch_id'], dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any('payment' in blocker['message'] and payment['number'] in blocker['message']
                   for blocker in caught.value.details['blockers'])
        assert snapshot(books) == stable


def test_interrupted_reversal_resumes_without_repeating_child_inverse(books, monkeypatch):
    setup_group(books)
    before = state(books)
    out = process(books)
    real = batches.run_nested
    stopped = False

    def interrupt(*args, **kwargs):
        nonlocal stopped
        result = real(*args, **kwargs)
        if args[2] == 'invoice delete' and not kwargs.get('dry_run') and not stopped:
            stopped = True
            raise RuntimeError('interrupted after committed child inverse')
        return result

    monkeypatch.setattr(batches, 'run_nested', interrupt)
    with pytest.raises(RuntimeError, match='interrupted after committed'):
        reverse(books, out['batch_id'])
    monkeypatch.setattr(batches, 'run_nested', real)
    assert books.run('batch show', {'batch': out['batch_id']})['batch']['state'] == 'reversing'
    assert reverse(books, out['batch_id'])['state'] == 'reversed'
    assert state(books) == before
    assert _nets(books) == {}
    with db(books) as conn:
        assert conn.execute("SELECT count(*) FROM batch_effects WHERE action = 'reversed' "
                            "AND json_extract(before, '$.command') = 'invoice delete'").fetchone()[0] == 3


def test_reverser_without_child_inverse_grant_is_refused(books):
    from tests.conftest import as_user, make_actor
    setup_group(books)
    out = process(books)
    actor = make_actor(books.root, 'memorized-reader', company_role=(books.company, 'standard'))
    member = next(row for row in books.client.run('membership list', {'company': books.company})['items']
                  if row['user_id'] == actor and row['scope_type'] == 'company')
    books.client.run('membership grant', {
        'user': actor, 'company': books.company, 'expected_version': member['version'],
        'denies': ['transaction.invoice.delete']})
    reader = as_user(books.root, 'memorized-reader')
    stable = snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reader.run('batch reverse', {'batch': out['batch_id']}, company=books.company, dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any(blocker['code'] == 'E_PERMISSION' for blocker in caught.value.details['blockers'])
        assert snapshot(books) == stable


def test_group_enter_reverses_documents_and_occurrences(books):
    setup_group(books)
    before = state(books)
    out = books.run('memorized-group enter', {'memorized_group': 'Monthly service', 'date': '2026-01-31'})
    assert out['entered_count'] == 3
    reverse(books, out['batch_id'])
    assert state(books) == before
    assert _nets(books) == {}
    assert books.run('memorized-group enter', {
        'memorized_group': 'Monthly service', 'date': '2026-01-31'})['entered_count'] == 3


def test_single_enter_reverses_and_can_enter_corrected_slot(books):
    templates = setup_group(books)
    before = state(books)
    out = books.run('memorized enter', {'memorized': templates[0]['id'], 'date': '2026-01-31'})
    assert out['entered_count'] == 1
    reverse(books, out['batch_id'])
    assert state(books) == before
    again = books.run('memorized enter', {'memorized': templates[0]['id'], 'date': '2026-01-31'})
    assert again['entered_count'] == 1
    assert sorted(_nets(books).values()) == [-8500, 8500]


def test_retry_reversal_restores_blocked_occurrence_then_original_run_can_reverse(books):
    setup_group(books)
    books.run('customer deactivate', {'customer': CUSTOMERS[0]})
    first = process(books)
    assert first['entered_count'] == 2 and first['blocked_count'] == 1
    books.run('customer activate', {'customer': CUSTOMERS[0]})
    before_retry = state(books)
    blocked = first['blocked'][0]
    retry = books.run('memorized retry', {'occurrence': blocked['id'], 'expected_version': blocked['version']})
    assert retry['occurrence']['status'] == 'entered'
    assert retry['batch_id'] != first['batch_id']
    assert retry['next'] == f"batch reverse {retry['batch_id']} --dry-run"
    reverse(books, retry['batch_id'])
    assert state(books) == before_retry
    assert sorted(_nets(books).values()) == [-42500, 42500]
    reverse(books, first['batch_id'])
    assert _nets(books) == {}
    assert state(books)['occurrences'] == []


def test_corrected_retry_after_reversal_does_not_replay_deleted_document(books):
    templates = setup_group(books)
    books.run('customer deactivate', {'customer': CUSTOMERS[0]})
    initial = process(books)
    books.run('customer activate', {'customer': CUSTOMERS[0]})
    waiting = initial['blocked'][0]
    first = books.run('memorized retry', {'occurrence': waiting['id'], 'expected_version': waiting['version']})
    old_document = first['occurrence']['transaction_id']
    reverse(books, first['batch_id'])
    template = books.run('memorized show', {'memorized': templates[0]['id']})
    books.run('memorized update', {
        'memorized': template['id'], 'expected_version': template['version'],
        'payload': {'customer': CUSTOMERS[0], 'lines': [{'item': 'Labor', 'quantity': '4'}]}})
    waiting = next(row for row in books.run('memorized show', {'memorized': template['id']})['occurrences']
                   if row['id'] == waiting['id'])
    body = {'occurrence': waiting['id'], 'expected_version': waiting['version']}
    preview = books.run('memorized retry', body, dry_run=True)
    again = books.run('memorized retry', dict(body, expected_facts_fingerprint=preview['facts_fingerprint']))
    assert again['occurrence']['transaction_id'] != old_document
    assert again['occurrence']['slot_date'] == '2026-01-31'
    assert _due(books, again['occurrence']['transaction_id']) == 34000
    assert sorted(_nets(books).values()) == [-76500, 76500]


def scheduler_clock(books, monkeypatch):
    from bookflow.core import clock
    from tests.test_scheduling import Clock
    books.clock = Clock()
    monkeypatch.setattr(clock, 'now', books.clock)


def test_scheduler_run_reverses_documents_occurrences_and_next_date(books, monkeypatch):
    from tests.test_scheduling import tick
    scheduler_clock(books, monkeypatch)
    setup_group(books)
    before = state(books)
    out = tick(books, '2026-01-31')['memorized']
    assert out['entered'] == 3
    assert out['next'] == f"batch reverse {out['batch_id']} --dry-run"
    assert sorted(_nets(books).values()) == [-51000, 51000]
    reverse(books, out['batch_id'])
    assert state(books) == before
    assert _nets(books) == {}
    corrected = tick(books, '2026-01-31', 18)['memorized']
    assert corrected['entered'] == 3
    assert sorted(_nets(books).values()) == [-51000, 51000]


def test_reversing_enter_restores_scheduler_holds_exactly(books, monkeypatch):
    from tests.test_scheduling import tick
    scheduler_clock(books, monkeypatch)
    setup_group(books)
    group = books.run('memorized-group show', {'memorized_group': 'Monthly service'})
    books.run('memorized-group update', {
        'memorized_group': group['id'], 'expected_version': group['version'], 'remaining_count': 1})
    before_scheduler = state(books)
    scheduled = tick(books, '2027-02-01')['memorized']
    assert scheduled['entered'] == 0
    held = state(books)
    assert len(held['holds']) == 3
    assert len(held['occurrences']) == 3
    out = books.run('memorized-group enter', {'memorized_group': 'Monthly service', 'date': '2026-01-31'})
    assert out['entered_count'] == 3
    assert state(books)['holds'] == []
    reverse(books, out['batch_id'])
    assert state(books) == held
    assert _nets(books) == {}
    reverse(books, scheduled['batch_id'])
    assert state(books) == before_scheduler


def test_sent_child_names_message_and_refuses_whole_run(books):
    setup_group(books)
    out = process(books)
    message = books.run('delivery draft', {
        'kind': 'invoice', 'document': ids(out)[0], 'to': ['billing@example.test']})
    books.run('delivery mark-sent', {'message': message['number'], 'method': 'webmail'})
    stable = snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reverse(books, out['batch_id'], dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any('sent' in blocker['message'].lower() and message['number'] in blocker['message']
                   for blocker in caught.value.details['blockers'])
        assert snapshot(books) == stable


@pytest.mark.parametrize('dependency', ['deposited', 'reconciled'])
def test_cash_child_dependency_named_and_atomic(books, dependency):
    from bookflow.core.ids import new_id
    setup_group(books)
    bank = books.run('account create', {'name': 'Batch Bank', 'type': 'bank'})['id']
    books.run('memorized create', {
        'name': 'Counter service', 'command': 'sales-receipt post',
        'payload': {'customer': CUSTOMERS[0], 'deposit_to': 'Undeposited Funds' if dependency == 'deposited' else bank,
                    'payment_method': 'Cash', 'lines': [{'item': 'Labor', 'quantity': '1'}]},
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'})
    out = process(books)
    assert out['entered_count'] == 4
    receipt = next(row['transaction_id'] for row in out['entered'] if row['template_name'] == 'Counter service')
    if dependency == 'deposited':
        deposit = books.run('deposit post', {
            'operation_key': 'memorized-deposit', 'document': {'mode': 'inline', 'date': '2026-02-01',
                'deposit_to': bank, 'additional': [], 'sources': [
                    {'source_type': 'sales_receipt', 'source': receipt, 'expected_version': 1}]}})['deposit']
        blocker_identity = deposit['id']
    else:
        opening = books.run('reconcile opening start', {
            'operation_key': new_id(), 'account': bank, 'opening_date': '2026-01-01', 'entered_balance': '0',
            'references': [], 'evidence': {'format': 1, 'statement_reference': None, 'entered_text': 'Opening zero'}})['draft']
        draft = books.run('reconcile start', {
            'operation_key': new_id(), 'account': bank, 'statement_date': '2026-01-31',
            'ending_balance': '85', 'opening_draft_id': opening['id']})['draft']
        rows = books.run('reconcile candidates', {'draft': draft['id'], 'limit': 200})['items']
        assert len(rows) == 1
        marked = books.run('reconcile mark', {
            'operation_key': new_id(), 'draft': draft['id'], 'expected_version': 1,
            'entries': [{'movement': row['movement'], 'group_fingerprint': row['group_fingerprint'], 'action': 'mark'}
                        for row in rows]})['draft']
        preview = books.run('reconcile preview', {'draft': draft['id'], 'expected_version': marked['version']})
        finished = books.run('reconcile finish', {
            'operation_key': new_id(), 'draft': draft['id'], 'expected_version': marked['version'],
            'expected_facts_fingerprint': preview['expected_facts_fingerprint'], 'dependency_guard': preview['dependency_guard']})
        blocker_identity = finished['certificate_id']
    stable = snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as caught:
            reverse(books, out['batch_id'], dry_run=dry)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert blocker_identity in str(caught.value.details)
        assert snapshot(books) == stable


def test_child_effect_failure_rolls_back_that_document(books, monkeypatch):
    setup_group(books)
    original = batches.record_effect
    failed = False

    def fail_first_invoice(*args, **kwargs):
        nonlocal failed
        result = original(*args, **kwargs)
        if kwargs.get('record_type') == 'invoice' and not failed:
            failed = True
            raise RuntimeError('child effect recording failed')
        return result

    monkeypatch.setattr(batches, 'record_effect', fail_first_invoice)
    out = process(books)
    assert failed
    assert out['entered_count'] == 2 and out['blocked_count'] == 1
    effects = books.run('batch show', {'batch': out['batch_id']})['batch']['effects']
    assert {effect['record_id'] for effect in effects if effect['record_type'] == 'invoice'} == set(ids(out))
    assert len(books.run('invoice query', {'limit': 50})['items']) == 2
    monkeypatch.setattr(batches, 'record_effect', original)
    reverse(books, out['batch_id'])
    assert _nets(books) == {}
    assert state(books)['occurrences'] == []


def test_crash_after_children_before_outcomes_is_still_reversible(books, monkeypatch):
    from bookflow.company import memorized_entry
    setup_group(books)
    before = state(books)
    original = memorized_entry._record

    def interrupt(*args, **kwargs):
        raise RuntimeError('host stopped before outcome recording')

    monkeypatch.setattr(memorized_entry, '_record', interrupt)
    with pytest.raises(RuntimeError, match='host stopped before outcome'):
        process(books)
    monkeypatch.setattr(memorized_entry, '_record', original)
    with db(books) as conn:
        batch = conn.execute("SELECT id FROM batches WHERE kind = 'memorized-run' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    assert sorted(_nets(books).values()) == [-51000, 51000]
    preview = reverse(books, batch, dry_run=True)
    assert len([step for step in preview['steps'] if step['record_type'] == 'invoice']) == 3
    reverse(books, batch)
    assert state(books) == before
    assert _nets(books) == {}


def _assert_no_raw_receipts(value):
    if isinstance(value, dict):
        assert 'result' not in value, 'batch output must not expose nested command receipts'
        for child in value.values():
            _assert_no_raw_receipts(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_raw_receipts(child)


def test_batch_show_and_plan_do_not_expose_occurrence_receipts(books):
    setup_group(books)
    first = process(books)
    second = process(books, '2026-02-28')
    # Both the first run's after-image and the next run's before-image contain
    # receipts internally; neither public surface may reveal those raw payloads.
    with db(books) as conn:
        assert conn.execute('SELECT count(*) FROM memorized_occurrences WHERE result IS NOT NULL').fetchone()[0] == 6
    for out in (first, second):
        shown = books.run('batch show', {'batch': out['batch_id']})
        _assert_no_raw_receipts(shown)
    _assert_no_raw_receipts(reverse(books, second['batch_id'], dry_run=True))


def test_memorized_batch_invoice_records_and_reverses_every_nested_invoice(books):
    books.run('memorized create', {
        'name': 'Monthly customer batch', 'command': 'batch-invoice post',
        'payload': {'customers': list(CUSTOMERS), 'lines': [{'item': 'Labor', 'quantity': '1'}]},
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'})
    before = state(books)
    out = process(books)
    assert out['entered_count'] == 1 and out['blocked_count'] == 0
    documents = books.run('invoice query', {'limit': 50})['items']
    assert len(documents) == 3
    document_ids = {row['id'] for row in documents}
    assert sorted(_nets(books).values()) == [-25500, 25500]
    shown = books.run('batch show', {'batch': out['batch_id']})['batch']
    assert {effect['record_type'] for effect in shown['effects']} == {'invoice', 'memorized_run'}
    assert {effect['record_id'] for effect in shown['effects'] if effect['record_type'] == 'invoice'} == document_ids
    plan = reverse(books, out['batch_id'], dry_run=True)
    assert len(plan['steps']) == 4
    assert {step['record_id'] for step in plan['steps'] if step['record_type'] == 'invoice'} == document_ids
    assert reverse(books, out['batch_id'])['state'] == 'reversed'
    assert state(books) == before
    assert _nets(books) == {}
    # Reversal deletes each nested invoice (its native inverse), so none is shown without include_deleted.
    for document in document_ids:
        with pytest.raises(BookflowError) as gone:
            books.run('invoice show', {'invoice': document})
        assert gone.value.code == 'E_RECORD_NOT_FOUND'
    assert process(books)['entered_count'] == 1
    assert sorted(_nets(books).values()) == [-25500, 25500]


@pytest.mark.parametrize('command,family,inverse', [
    ('check post', 'check', 'check delete'),
    ('transfer post', 'transfer', 'transfer void'),
    ('card-credit post', 'card_credit', 'card-credit void'),
])
def test_memorized_money_movements_use_native_inverse_and_restore_balances(books, command, family, inverse):
    bank = books.run('account create', {'name': 'Recurring Bank', 'type': 'bank'})['id']
    expense = books.run('account create', {'name': 'Recurring Expense', 'type': 'expense'})['id']
    if family == 'transfer':
        savings = books.run('account create', {'name': 'Recurring Savings', 'type': 'bank'})['id']
        payload = {'from_account': bank, 'to_account': savings, 'amount': '42.35'}
        expected_balances = {bank: -4235, savings: 4235}
    elif family == 'card_credit':
        card = books.run('account create', {'name': 'Recurring Card', 'type': 'credit_card'})['id']
        payload = {'account': card, 'amount': '42.35', 'expenses': [{'account': expense, 'amount': '42.35'}]}
        expected_balances = {card: 4235, expense: -4235}
    else:
        payload = {'account': bank, 'amount': '42.35', 'expenses': [{'account': expense, 'amount': '42.35'}],
                   'no_number': True}
        expected_balances = {bank: -4235, expense: 4235}
    template = books.run('memorized create', {
        'name': 'Recurring money movement', 'command': command, 'payload': payload,
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'})
    before = state(books)
    out = process(books)
    assert out['entered_count'] == 1 and out['blocked_count'] == 0
    assert _nets(books) == expected_balances
    plan = reverse(books, out['batch_id'], dry_run=True)
    documents = [step for step in plan['steps'] if step['record_type'] != 'memorized_run']
    assert len(documents) == 1
    assert documents[0]['record_type'] == family
    assert documents[0]['steps'] == [inverse]
    assert reverse(books, out['batch_id'])['state'] == 'reversed'
    assert _nets(books) == {}
    assert state(books) == before
    assert books.run('memorized show', {'memorized': template['id']})['schedule']['next_date'] == '2026-01-31'


def test_reversed_aggregate_entry_with_expired_receipts_posts_new_documents(books):
    template = books.run('memorized create', {
        'name': 'Reminded customer batch', 'command': 'batch-invoice post',
        'payload': {'customers': list(CUSTOMERS), 'lines': [{'item': 'Labor', 'quantity': '1'}]},
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'remind'})
    pending = process(books)
    assert pending['entered_count'] == 0 and pending['pending_count'] == 1
    before_entry = state(books)
    body = {'memorized': template['id'], 'date': '2026-01-31'}
    entered = books.run('memorized enter', body)
    assert entered['entered_count'] == 1
    first_documents = {row['id'] for row in books.run('invoice query', {'limit': 50})['items']}
    assert len(first_documents) == 3
    reverse(books, entered['batch_id'])
    assert state(books) == before_entry
    assert _nets(books) == {}
    # Expiration removes the fast recovery evidence. Audit-only recovery must
    # also recognize that the aggregate's child invoices have been reversed.
    with db(books) as conn:
        conn.execute('DELETE FROM idempotency_keys')
        conn.commit()
    again = books.run('memorized enter', body)
    assert again['entered_count'] == 1 and again['blocked_count'] == 0
    new_documents = {row['id'] for row in books.run('invoice query', {'limit': 50})['items']}
    assert len(new_documents) == 3
    assert not first_documents & new_documents
    assert sorted(_nets(books).values()) == [-25500, 25500]
    shown = books.run('memorized show', {'memorized': template['id']})
    assert [(row['slot_date'], row['status']) for row in shown['occurrences']] == [('2026-01-31', 'entered')]
