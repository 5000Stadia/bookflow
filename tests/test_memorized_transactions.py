"""Memorized transactions: what is entered, when, once, and what happens when it fails.

The property this file exists for is **idempotence per slot**. A memorized transaction is
identified by its template and its nominal scheduled date, and no amount of running, rerunning
or crashing may put that slot in the books twice. Three tests come at that from three
directions: running the same date twice, running a host that died between the post and the
record, and editing the template after a slot has entered.

Every date in this file is written out. The rent schedule starts on 31 January 2026, which is
the month-end case: the anchor day is kept and clamped, so the slots are 31 January, 28
February, 31 March -- never 28 February, 28 March, 28 April.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import bookflow
from bookflow.core.errors import BookflowError

COMMANDS = frozenset((
    'memorized create', 'memorized update', 'memorized delete', 'memorized show',
    'memorized list', 'memorized enter', 'memorized process', 'memorized retry',
    'memorized skip', 'memorized-group create', 'memorized-group update',
    'memorized-group delete', 'memorized-group show', 'memorized-group list',
    'memorized-group enter'))

RENT = '1800.00'
TOOLS = '50.00'


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every entry counted here is only this test's."""
    return _company(tmp_path, monkeypatch, 'UTC')


def _company(tmp_path, monkeypatch, zone):
    data_root = tmp_path / ('books-' + zone.replace('/', '-'))
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Recurring organization')
    company = client.company.new(legal_name='Recurring', home_currency='USD', timezone=zone,
                                 organization='Recurring organization', chart='general')['company_id']
    client.account.create(company=company, name='Office Rent', type='expense')
    client.account.create(company=company, name='Tool Hire', type='expense')
    client.customer.create(company=company, name='Rivera Construction')
    client.vendor.create(company=company, name='Harbor Property')
    client.vendor.create(company=company, name='Tool Depot')

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    return type('Books', (), {'client': client, 'company': company, 'run': staticmethod(run),
                              'root': data_root})


def rent_payload(account='Office Rent', vendor='Harbor Property', amount=RENT):
    return {'vendor': vendor, 'expenses': [{'account': account, 'amount': amount, 'memo': 'Rent'}]}


def memorize(books, name='Monthly office rent', **extra):
    body = dict(name=name, command='bill post', payload=rent_payload(),
                frequency='monthly', start_date='2026-01-31', mode='enter_automatically')
    body.update(extra)
    return books.run('memorized create', body, reason='Memorize ' + name)


def numbers(books):
    return sorted(row['number'] for row in books.run('bill query', {'limit': 100})['items'])


def dates(books):
    return sorted(row['date'] for row in books.run('bill query', {'limit': 100})['items'])


# ---------------------------------------------------------------- the schedule itself

def test_month_end_keeps_the_anchor_day_through_a_short_month():
    """The 31st in a 30-day month is a decision, and it is this one."""
    from bookflow.company import memorized_schedule as s
    assert s.anchor_day_for('monthly', '2026-01-31') == 31
    slot = '2026-01-31'
    seen = []
    for _ in range(5):
        slot = s.advance('monthly', slot, 31)
        seen.append(slot)
    assert seen == ['2026-02-28', '2026-03-31', '2026-04-30', '2026-05-31', '2026-06-30']
    # February of a leap year clamps to the 29th, and the anchor is still not lost.
    assert s.advance('monthly', '2028-01-31', 31) == '2028-02-29'
    assert s.advance('monthly', '2028-02-29', 31) == '2028-03-31'
    assert s.advance('annually', '2028-02-29', 29) == '2029-02-28'


def test_twice_a_month_is_the_anchor_day_and_the_day_fifteen_later():
    from bookflow.company import memorized_schedule as s
    assert s.advance('twice_a_month', '2026-03-01', 1) == '2026-03-16'
    assert s.advance('twice_a_month', '2026-03-16', 1) == '2026-04-01'
    # An anchor whose pair clamps onto the same day yields one slot that month, not two.
    assert s.advance('twice_a_month', '2026-02-28', 28) == '2026-03-28'
    assert s.advance('twice_a_month', '2026-01-20', 20) == '2026-01-31'


def test_the_other_frequencies_advance_by_their_own_step():
    from bookflow.company import memorized_schedule as s
    assert s.advance('daily', '2026-02-28', None) == '2026-03-01'
    assert s.advance('weekly', '2026-02-28', None) == '2026-03-07'
    assert s.advance('every_other_week', '2026-02-28', None) == '2026-03-14'
    assert s.advance('every_four_weeks', '2026-02-28', None) == '2026-03-28'
    assert s.advance('quarterly', '2026-01-31', 31) == '2026-04-30'
    assert s.advance('twice_a_year', '2026-01-31', 31) == '2026-07-31'
    assert s.advance('annually', '2026-01-31', 31) == '2027-01-31'
    assert s.advance('every_other_year', '2026-01-31', 31) == '2028-01-31'
    assert s.advance('never', '2026-01-31', None) is None


def test_today_is_the_company_s_today_and_not_the_host_s(tmp_path, monkeypatch):
    """A host in UTC must not enter a Los Angeles company's transaction a day early."""
    from bookflow.core import clock
    from bookflow.company import memorized_schedule as s
    moment = datetime(2026, 3, 1, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: moment)
    assert s.today('UTC') == '2026-03-01'
    assert s.today('America/Los_Angeles') == '2026-02-28'

    books = _company(tmp_path, monkeypatch, 'America/Los_Angeles')
    memorize(books, frequency='daily', start_date='2026-03-01')
    out = books.run('memorized process', {}, reason='Run it')
    assert out['as_of'] == '2026-02-28'
    assert out['entered_count'] == 0, 'the company is still on 28 February'
    assert numbers(books) == []


# ---------------------------------------------------------------- entering, once

def test_a_backlog_enters_each_slot_at_its_own_accounting_date(books):
    memorize(books)
    out = books.run('memorized process', {'as_of': '2026-03-15'}, reason='Catch up')
    assert out['entered_count'] == 2 and out['blocked_count'] == 0
    assert [row['slot_date'] for row in out['entered']] == ['2026-01-31', '2026-02-28']
    assert dates(books) == ['2026-01-31', '2026-02-28'], 'a missed slot is not collapsed onto today'
    assert books.run('memorized show', {'memorized': 'Monthly office rent'})['schedule']['next_date'] == '2026-03-31'


def test_processing_the_same_day_twice_enters_nothing_the_second_time(books):
    memorize(books)
    first = books.run('memorized process', {'as_of': '2026-01-31'}, reason='First run')
    second = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Second run')
    third = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Third run')
    assert first['entered_count'] == 1
    assert second['entered_count'] == 0 and third['entered_count'] == 0
    assert numbers(books) == ['1'], 'one slot, one document, however often the host runs'


def test_a_crash_between_the_post_and_the_record_adopts_the_document_it_posted(books, monkeypatch):
    """The window the design allows, and the recovery that closes it.

    The entry commits in its own transaction and the outcome is recorded in the next one. A
    host killed in between has posted a bill and not yet said so, which leaves a *pending*
    occurrence whose document already exists. The next run must adopt that document, not post
    a second one.
    """
    from bookflow.company import memorized_entry
    memorize(books)
    real = memorized_entry._record
    monkeypatch.setattr(memorized_entry, '_record',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('host died')))
    with pytest.raises(Exception):
        books.run('memorized process', {'as_of': '2026-01-31'}, reason='Interrupted run')
    monkeypatch.setattr(memorized_entry, '_record', real)

    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    waiting = shown['occurrences'][0]
    assert waiting['status'] == 'pending' and waiting['slot_date'] == '2026-01-31'
    assert numbers(books) == ['1'], 'the bill really did post before the host died'

    recovered = books.run('memorized process', {'as_of': '2026-01-31'}, reason='After the restart')
    assert recovered['entered_count'] == 1
    assert numbers(books) == ['1'], 'recovery adopts the document; it never posts a second one'
    after = books.run('memorized show', {'memorized': 'Monthly office rent'})['occurrences'][0]
    assert after['status'] == 'entered' and after['id'] == waiting['id']
    assert after['slot_date'] == '2026-01-31'


def test_recovery_works_from_the_audit_trail_when_the_retry_receipt_is_gone(books, monkeypatch):
    """The receipt expires after thirty days; the company audit event never does."""
    import sqlite3
    from bookflow.company import memorized_entry
    memorize(books)
    monkeypatch.setattr(memorized_entry, '_record',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('host died')))
    with pytest.raises(Exception):
        books.run('memorized process', {'as_of': '2026-01-31'}, reason='Interrupted run')
    monkeypatch.undo()

    path = books.run('company show', {})['path']
    with sqlite3.connect(path + '/company.db') as raw:
        assert raw.execute('DELETE FROM idempotency_keys').rowcount >= 0
        raw.commit()

    recovered = books.run('memorized process', {'as_of': '2026-01-31'}, reason='After the restart')
    assert recovered['entered_count'] == 1
    assert numbers(books) == ['1'], 'the audit trail is enough to recognise the entry that happened'


def test_days_in_advance_enters_early_and_never_moves_the_accounting_date(books):
    memorize(books, days_in_advance=5)
    early = books.run('memorized process', {'as_of': '2026-01-27'}, reason='Five days early')
    assert early['entered_count'] == 1
    assert early['entered'][0]['slot_date'] == '2026-01-31'
    assert early['entered'][0]['due_date'] == '2026-01-26'
    assert dates(books) == ['2026-01-31'], 'entering early is not dating early'
    assert books.run('memorized process', {'as_of': '2026-01-31'}, reason='On the day')['entered_count'] == 0


def test_a_reminder_waits_where_a_person_can_see_it_instead_of_entering(books):
    memorize(books, mode='remind')
    out = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Remind me')
    assert out['entered_count'] == 0 and out['pending_count'] == 1
    assert numbers(books) == []
    listed = books.run('memorized list', {'due_only': True})
    assert [row['name'] for row in listed['items']] == ['Monthly office rent']
    waiting = books.run('memorized show', {'memorized': 'Monthly office rent'})['occurrences'][0]
    assert waiting['status'] == 'pending' and waiting['slot_date'] == '2026-01-31'
    # It stays waiting, and running again does not mint another one for the same slot.
    assert books.run('memorized process', {'as_of': '2026-02-28'}, reason='Again')['entered_count'] == 0
    slots = [row['slot_date'] for row in books.run('memorized show', {'memorized': 'Monthly office rent'})['occurrences']]
    assert sorted(slots) == ['2026-01-31', '2026-02-28'] and len(slots) == len(set(slots))


def test_an_on_demand_entry_gets_its_own_identity_every_time(books):
    memorize(books, name='Extra', frequency='never', start_date=None, mode='on_demand')
    first = books.run('memorized enter', {'memorized': 'Extra', 'date': '2026-04-01'}, reason='One')
    second = books.run('memorized enter', {'memorized': 'Extra', 'date': '2026-04-01'}, reason='Another')
    assert first['entered_count'] == second['entered_count'] == 1
    assert first['entered'][0]['id'] != second['entered'][0]['id']
    assert [row['origin'] for row in (first['entered'] + second['entered'])] == ['manual', 'manual']
    assert numbers(books) == ['1', '2'], 'an extra entry made by hand is an extra document'


# ---------------------------------------------------------------- editing and identity

def test_a_template_edit_does_not_mint_a_second_occurrence_for_an_entered_slot(books):
    memorize(books)
    books.run('memorized process', {'as_of': '2026-01-31'}, reason='Enter January')
    template = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert numbers(books) == ['1']

    books.run('memorized update', {
        'memorized': template['id'], 'expected_version': template['version'],
        'payload': rent_payload(amount='2000.00'), 'start_date': '2026-01-31',
        'frequency': 'monthly'}, reason='The rent went up')

    again = books.run('memorized process', {'as_of': '2026-01-31'}, reason='After the edit')
    assert again['entered_count'] == 0
    assert numbers(books) == ['1'], 'January already entered; an edit does not re-enter it'
    edited = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert edited['revision_version'] == 2 and edited['current_revision_id'] != template['current_revision_id']
    assert [row['slot_date'] for row in edited['occurrences']] == ['2026-01-31']
    # The entered occurrence still points at the revision it captured, not at the new one.
    assert edited['occurrences'][0]['revision_id'] == template['current_revision_id']


def test_a_retry_replays_the_captured_revision_and_not_a_newer_one(books):
    """A retry must never silently substitute a newly edited template."""
    memorize(books)
    account = books.run('account show', {'account': 'Office Rent'})
    books.run('account deactivate', {'account': account['id'], 'expected_version': account['version']},
              reason='Close the account')
    blocked = books.run('memorized process', {'as_of': '2026-01-31'}, reason='It will fail')
    assert blocked['blocked_count'] == 1
    occurrence = blocked['blocked'][0]
    captured = occurrence['revision_id']

    template = books.run('memorized show', {'memorized': 'Monthly office rent'})
    books.run('memorized update', {'memorized': template['id'], 'expected_version': template['version'],
                                   'payload': rent_payload(account='Tool Hire', amount='9999.00')},
              reason='Point it somewhere else')
    books.run('account activate', {'account': account['id'], 'expected_version': account['version'] + 1},
              reason='Reopen the account')

    retried = books.run('memorized retry', {'occurrence': occurrence['id'],
                                            'expected_version': occurrence['version']}, reason='Try again')
    assert retried['occurrence']['status'] == 'entered'
    assert retried['occurrence']['revision_id'] == captured
    assert retried['occurrence']['slot_date'] == '2026-01-31'
    bill = books.run('bill show', {'bill': retried['occurrence']['transaction_id']})
    assert bill['revision']['total']['amount'] == RENT, 'the retry replayed what was due, not the edit'


# ---------------------------------------------------------------- failure

def test_a_failure_is_a_visible_blocked_occurrence_that_keeps_its_own_date(books):
    memorize(books)
    account = books.run('account show', {'account': 'Office Rent'})
    books.run('account deactivate', {'account': account['id'], 'expected_version': account['version']},
              reason='Close the account')

    out = books.run('memorized process', {'as_of': '2026-02-28'}, reason='Run the schedule')
    assert out['entered_count'] == 0 and out['blocked_count'] == 2
    assert [row['slot_date'] for row in out['blocked']] == ['2026-01-31', '2026-02-28']
    assert {row['error_code'] for row in out['blocked']} == {'E_INACTIVE_REFERENCE'}
    assert all(row['error_message'] for row in out['blocked'])
    assert numbers(books) == [], 'nothing posted, and no number was taken'

    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert shown['schedule']['status'] == 'active', 'one failure does not disable the template'
    assert shown['schedule']['next_date'] == '2026-03-31', 'the next slot still comes due on time'
    listed = books.run('memorized list', {'due_only': True})
    assert listed['blocked_total'] == 2 and listed['items'][0]['blocked_count'] == 2
    # The index shows the breakage that caused it, without having decided anything.
    assert [(row['field_path'], row['active']) for row in shown['references'] if not row['active']] \
        == [('expenses.account', False)]


def test_one_template_failing_leaves_every_other_template_running(books):
    memorize(books, name='Rent')
    books.run('memorized create', {
        'name': 'Tools', 'command': 'bill post',
        'payload': {'vendor': 'Tool Depot', 'expenses': [{'account': 'Tool Hire', 'amount': TOOLS}]},
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'},
        reason='Memorize the tool hire')
    account = books.run('account show', {'account': 'Office Rent'})
    books.run('account deactivate', {'account': account['id'], 'expected_version': account['version']},
              reason='Close the account')
    out = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Run both')
    assert out['entered_count'] == 1 and out['blocked_count'] == 1
    assert out['entered'][0]['template_name'] == 'Tools'
    assert out['blocked'][0]['template_name'] == 'Rent'
    assert numbers(books) == ['1']


def test_retry_enters_the_blocked_slot_and_skip_sets_it_aside(books):
    memorize(books)
    account = books.run('account show', {'account': 'Office Rent'})
    books.run('account deactivate', {'account': account['id'], 'expected_version': account['version']},
              reason='Close the account')
    blocked = books.run('memorized process', {'as_of': '2026-02-28'}, reason='Both fail')['blocked']
    january, february = blocked[0], blocked[1]

    skipped = books.run('memorized skip', {'occurrence': february['id'],
                                           'expected_version': february['version']}, reason='No rent that month')
    assert skipped['occurrence']['status'] == 'skipped'
    assert skipped['occurrence']['slot_date'] == '2026-02-28'
    assert skipped['occurrence']['error_code'] is None

    books.run('account activate', {'account': account['id'], 'expected_version': account['version'] + 1},
              reason='Reopen it')
    retried = books.run('memorized retry', {'occurrence': january['id'],
                                            'expected_version': january['version']}, reason='Try January again')
    assert retried['occurrence']['status'] == 'entered'
    assert dates(books) == ['2026-01-31'], 'the retry kept the date it was always for'
    assert numbers(books) == ['1']

    with pytest.raises(BookflowError) as refused:
        books.run('memorized retry', {'occurrence': january['id'],
                                      'expected_version': retried['occurrence']['version']}, reason='Once more')
    assert refused.value.code == 'E_VALIDATION'


def test_a_number_is_never_taken_by_a_template_that_has_not_entered(books):
    """Allocation happens on entry, through the ordinary allocator, and nowhere else."""
    memorize(books)
    memorize(books, name='Second rent')
    assert books.run('bill query', {'limit': 10})['items'] == []
    books.run('bill post', {'vendor': 'Tool Depot', 'date': '2026-01-02',
                            'expenses': [{'account': 'Tool Hire', 'amount': TOOLS}]}, reason='An ordinary bill')
    assert numbers(books) == ['1'], 'the memorized templates reserved nothing'
    books.run('memorized process', {'as_of': '2026-01-31'}, reason='Now enter them')
    assert numbers(books) == ['1', '2', '3']


# ---------------------------------------------------------------- groups

def _group(books):
    books.run('memorized-group create', {'name': 'Month end', 'frequency': 'monthly',
                                         'start_date': '2026-01-31', 'mode': 'enter_automatically'},
              reason='Create the group')
    for ordinal, (name, vendor, account, amount) in enumerate(
            (('Rent', 'Harbor Property', 'Office Rent', RENT),
             ('Tools', 'Tool Depot', 'Tool Hire', TOOLS)), start=1):
        books.run('memorized create', {
            'name': name, 'command': 'bill post',
            'payload': {'vendor': vendor, 'expenses': [{'account': account, 'amount': amount}]},
            'group': 'Month end', 'group_ordinal': ordinal}, reason='Add ' + name)


def test_one_member_failing_does_not_roll_back_the_members_that_posted(books):
    _group(books)
    account = books.run('account show', {'account': 'Office Rent'})
    books.run('account deactivate', {'account': account['id'], 'expected_version': account['version']},
              reason='Close the account')

    out = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Run the group')
    assert out['entered_count'] == 1 and out['blocked_count'] == 1
    assert out['entered'][0]['template_name'] == 'Tools'
    assert out['blocked'][0]['template_name'] == 'Rent'
    assert numbers(books) == ['1'], 'the member that posted stayed posted'

    books.run('account activate', {'account': account['id'], 'expected_version': account['version'] + 1},
              reason='Reopen it')
    again = books.run('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-01-31'},
                      reason='Run the failures again')
    assert again['entered_count'] == 1 and again['entered'][0]['template_name'] == 'Rent'
    assert numbers(books) == ['1', '2'], 'a retry runs only the member that failed'
    shown = books.run('memorized-group show', {'memorized_group': 'Month end'})
    run = shown['runs'][0]
    assert run['slot_date'] == '2026-01-31'
    assert [(row['template_name'], row['status'], row['group_ordinal']) for row in run['members']] \
        == [('Rent', 'entered', 1), ('Tools', 'entered', 2)]


def test_a_group_run_freezes_its_membership_so_a_later_edit_cannot_rewrite_it(books):
    _group(books)
    books.run('memorized process', {'as_of': '2026-01-31'}, reason='Run the group')
    tools = books.run('memorized show', {'memorized': 'Tools'})
    books.run('memorized delete', {'memorized': tools['id'], 'expected_version': tools['version']},
              reason='Tools are no longer hired')

    shown = books.run('memorized-group show', {'memorized_group': 'Month end'})
    assert [row['name'] for row in shown['members']] == ['Rent'], 'the group has one member now'
    run = shown['runs'][0]
    assert [(row['template_name'], row['status']) for row in run['members']] \
        == [('Rent', 'entered'), ('Tools', 'entered')], 'what January did is what January did'
    assert numbers(books) == ['1', '2']


def test_clearing_the_group_takes_a_template_out_of_it(books):
    _group(books)
    tools = books.run('memorized show', {'memorized': 'Tools'})
    assert tools['group_name'] == 'Month end' and tools['group_ordinal'] == 2
    books.run('memorized update', {'memorized': tools['id'], 'expected_version': tools['version']},
              clear=['group'], reason='It stands alone now')
    alone = books.run('memorized show', {'memorized': 'Tools'})
    assert alone['group_id'] is None and alone['group_ordinal'] is None
    assert [row['name'] for row in books.run('memorized-group show',
                                             {'memorized_group': 'Month end'})['members']] == ['Rent']
    entered = books.run('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-05-31'},
                        reason='Only what is in it')
    assert [row['template_name'] for row in entered['entered']] == ['Rent']


def test_a_group_member_may_not_carry_its_own_schedule(books):
    books.run('memorized-group create', {'name': 'Month end', 'frequency': 'monthly',
                                         'start_date': '2026-01-31'}, reason='Create the group')
    with pytest.raises(BookflowError) as refused:
        books.run('memorized create', {
            'name': 'Rent', 'command': 'bill post', 'payload': rent_payload(),
            'frequency': 'weekly', 'start_date': '2026-01-31',
            'group': 'Month end', 'group_ordinal': 1}, reason='Try it')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'][0]['field'] == 'frequency'


def test_a_group_with_members_cannot_be_deleted(books):
    _group(books)
    group = books.run('memorized-group show', {'memorized_group': 'Month end'})
    with pytest.raises(BookflowError) as refused:
        books.run('memorized-group delete', {'memorized_group': group['id'],
                                             'expected_version': group['version']}, reason='Delete it')
    assert refused.value.code == 'E_ACTIVE_DEPENDENTS'
    assert {row['name'] for row in refused.value.details['dependents']} == {'Rent', 'Tools'}


# ---------------------------------------------------------------- the derived index

def test_the_dependency_index_follows_the_command_s_own_declarations(books):
    """Derived from the same reference declarations the ordinary form resolves through."""
    from bookflow.company import memorized_references as edges
    from bookflow.core import registry
    created = memorize(books)
    declared = {reference.field for reference in edges.declarations(registry.get('bill post'))}
    assert {row['field_path'] for row in created['references']} <= declared
    assert {row['field_path'] for row in created['references']} == {'vendor', 'expenses.account'}
    assert created['reference_schema'] == 'declared'
    assert all(row['present'] and row['active'] for row in created['references'])
    # Stable ids, not names: what is stored is what the selector resolved to.
    vendor = books.run('vendor show', {'vendor': 'Harbor Property'})
    stored = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert stored['payload']['vendor'] == vendor['id']
    assert [row['target_id'] for row in stored['references'] if row['field_path'] == 'vendor'] == [vendor['id']]


def test_a_renamed_vendor_does_not_break_a_template(books):
    memorize(books)
    vendor = books.run('vendor show', {'vendor': 'Harbor Property'})
    books.run('vendor update', {'vendor': vendor['id'], 'expected_version': vendor['version'],
                                'name': 'Harbor Property Group'}, reason='They renamed')
    out = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Still works')
    assert out['entered_count'] == 1
    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert [row['name'] for row in shown['references'] if row['field_path'] == 'vendor'] == ['Harbor Property Group']


def test_which_fields_are_fixed_and_which_are_recomputed_is_stated(books):
    memorize(books)
    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert shown['fixed_fields'] == ['expenses', 'vendor']
    assert 'date' in shown['recomputed_fields'] and 'number' in shown['recomputed_fields']
    assert 'due_date' in shown['recomputed_fields'] and 'terms' in shown['recomputed_fields']
    assert not set(shown['fixed_fields']) & set(shown['recomputed_fields'])


def test_identities_of_one_entry_are_never_memorized(books):
    created = memorize(books, payload=dict(rent_payload(), number='RENT-1'))
    assert 'number' not in created['references'] and 'number' not in str(created['references'])
    assert any('number' in warning for warning in created['warnings'])
    stored = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert 'number' not in stored['payload']
    out = books.run('memorized process', {'as_of': '2026-02-28'}, reason='Two slots')
    assert [row['document_number'] for row in out['entered']] == ['1', '2'], 'each entry takes the next number'


def test_only_create_and_post_commands_can_be_memorized(books):
    with pytest.raises(BookflowError) as refused:
        books.run('memorized create', {'name': 'A void', 'command': 'bill void',
                                       'payload': {'bill': 'x', 'expected_version': 1}}, reason='Try it')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'][0]['field'] == 'command'
    allowed = refused.value.details['allowed']
    assert 'bill post' in allowed and 'invoice post' in allowed
    assert not any(name.endswith(' void') or name.endswith(' update') for name in allowed)
    # A command that takes no accounting date cannot promise the slot date is the entry's date.
    from bookflow.core import registry
    registry.load_all()
    assert 'deposit post' not in allowed
    assert all('date' in registry.get(name).input_model.model_fields for name in allowed)


def test_a_payload_the_command_would_reject_is_refused_at_capture(books):
    with pytest.raises(BookflowError) as refused:
        books.run('memorized create', {'name': 'Broken', 'command': 'bill post',
                                       'payload': {'vendor': 'Harbor Property'}}, reason='Try it')
    assert refused.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as missing:
        books.run('memorized create', {'name': 'Missing', 'command': 'bill post',
                                       'payload': rent_payload(vendor='Nobody At All')}, reason='Try it')
    assert missing.value.code == 'E_RECORD_NOT_FOUND'
    assert missing.value.details['field'] == 'vendor' and missing.value.details['target'] == 'vendor'
    assert books.run('memorized list', {})['items'] == [], 'nothing broken was written'


# ---------------------------------------------------------------- stopping

def test_a_stop_count_makes_exactly_that_many_slots(books):
    memorize(books, remaining_count=3)
    out = books.run('memorized process', {'as_of': '2027-01-31'}, reason='Run it out')
    assert [row['slot_date'] for row in out['entered']] == ['2026-01-31', '2026-02-28', '2026-03-31']
    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert shown['schedule']['remaining_count'] == 0
    assert shown['schedule']['next_date'] is None and shown['schedule']['status'] == 'finished'
    assert books.run('memorized process', {'as_of': '2028-01-31'}, reason='Nothing left')['entered_count'] == 0
    assert numbers(books) == ['1', '2', '3']


def test_a_stop_date_ends_the_schedule_on_the_last_slot_that_fits(books):
    memorize(books, stop_date='2026-03-15')
    out = books.run('memorized process', {'as_of': '2027-01-31'}, reason='Run it out')
    assert [row['slot_date'] for row in out['entered']] == ['2026-01-31', '2026-02-28']
    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert shown['schedule']['next_date'] is None and shown['schedule']['status'] == 'finished'


def test_a_paused_template_mints_no_new_slots_and_a_deleted_one_leaves_the_list(books):
    memorize(books)
    template = books.run('memorized show', {'memorized': 'Monthly office rent'})
    books.run('memorized update', {'memorized': template['id'], 'expected_version': template['version'],
                                   'status': 'paused'}, reason='Pause it')
    assert books.run('memorized process', {'as_of': '2026-06-30'}, reason='Paused')['entered_count'] == 0
    resumed = books.run('memorized show', {'memorized': 'Monthly office rent'})
    books.run('memorized update', {'memorized': resumed['id'], 'expected_version': resumed['version'],
                                   'status': 'active'}, reason='Resume it')
    assert books.run('memorized process', {'as_of': '2026-02-28'}, reason='Resumed')['entered_count'] == 2

    current = books.run('memorized show', {'memorized': 'Monthly office rent'})
    deleted = books.run('memorized delete', {'memorized': current['id'],
                                             'expected_version': current['version']}, reason='Done with it')
    assert deleted['deleted'] and deleted['schedule']['status'] == 'deleted'
    assert books.run('memorized list', {})['items'] == []
    assert numbers(books) == ['1', '2'], 'what it entered is history and stays'
    assert books.run('memorized process', {'as_of': '2026-12-31'}, reason='Nothing left')['entered_count'] == 0


def test_an_edit_that_leaves_the_schedule_alone_never_replays_a_finished_one(books):
    """Renaming a template that has run out must not put its whole history back on the board."""
    memorize(books, remaining_count=2)
    books.run('memorized process', {'as_of': '2026-02-28'}, reason='Run it out')
    finished = books.run('memorized show', {'memorized': 'Monthly office rent'})
    assert finished['schedule']['status'] == 'finished' and finished['schedule']['next_date'] is None

    books.run('memorized update', {'memorized': finished['id'], 'expected_version': finished['version'],
                                   'name': 'Rent, as was'}, reason='Just a rename')
    renamed = books.run('memorized show', {'memorized': 'Rent, as was'})
    assert renamed['schedule']['status'] == 'finished' and renamed['schedule']['next_date'] is None
    assert books.run('memorized process', {'as_of': '2026-12-31'}, reason='Nothing to do')['entered_count'] == 0
    assert numbers(books) == ['1', '2']

    # Giving it a new start date is asking for it to run again, and it says so.
    books.run('memorized update', {'memorized': renamed['id'], 'expected_version': renamed['version'],
                                   'start_date': '2026-06-30', 'remaining_count': 1}, reason='Start it again')
    restarted = books.run('memorized show', {'memorized': 'Rent, as was'})
    assert restarted['schedule']['status'] == 'active' and restarted['schedule']['next_date'] == '2026-06-30'
    assert books.run('memorized process', {'as_of': '2026-06-30'}, reason='Run it')['entered_count'] == 1
    assert dates(books) == ['2026-01-31', '2026-02-28', '2026-06-30']


def test_a_stale_expected_version_is_refused(books):
    memorize(books)
    template = books.run('memorized show', {'memorized': 'Monthly office rent'})
    books.run('memorized update', {'memorized': template['id'], 'expected_version': template['version'],
                                   'name': 'Rent'}, reason='Rename it')
    with pytest.raises(BookflowError) as refused:
        books.run('memorized update', {'memorized': template['id'],
                                       'expected_version': template['version'],
                                       'name': 'Rent again'}, reason='Race')
    assert refused.value.code == 'E_VERSION_CONFLICT'


def test_a_repeated_request_replays_its_own_answer_instead_of_entering_again(books):
    """The commands that dispatch other commands keep their own retry record."""
    memorize(books, name='Extra', frequency='never', start_date=None, mode='on_demand')
    first = books.run('memorized enter', {'memorized': 'Extra', 'date': '2026-04-01'},
                      reason='Enter it', idempotency_key='enter-1')
    again = books.run('memorized enter', {'memorized': 'Extra', 'date': '2026-04-01'},
                      reason='Enter it', idempotency_key='enter-1')
    assert again['idempotent_replay']
    assert again['entered'][0]['id'] == first['entered'][0]['id']
    assert numbers(books) == ['1'], 'a repeated request is one entry, not two'

    memorize(books)
    run = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Run', idempotency_key='run-1')
    replay = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Run', idempotency_key='run-1')
    assert run['entered_count'] == 1 and replay['idempotent_replay']
    assert replay['entered'] == run['entered']
    assert numbers(books) == ['1', '2']


def test_a_preview_writes_nothing(books):
    memorize(books)
    preview = books.run('memorized process', {'as_of': '2026-02-28'}, dry_run=True, reason='Look first')
    assert preview['dry_run'] and preview['entered_count'] == 2
    assert [row['slot_date'] for row in preview['entered']] == ['2026-01-31', '2026-02-28']
    assert numbers(books) == []
    assert books.run('memorized show', {'memorized': 'Monthly office rent'})['occurrences'] == []
    assert books.run('memorized show', {'memorized': 'Monthly office rent'})['schedule']['next_date'] == '2026-01-31'


# ---------------------------------------------------------------- every surface

@pytest.mark.timeout(600)
def test_the_same_memorized_transaction_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                account = (await matrix.call(surface, 'account create',
                                             dict(name='Parity rent', type='expense')))['id']
                vendor = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Property Co')))['id']
                payload = {'vendor': vendor, 'expenses': [{'account': account, 'amount': RENT}]}
                template = dict(name='Parity rent', command='bill post', payload=payload,
                                frequency='monthly', start_date='2026-01-31',
                                mode='enter_automatically')
                assert (await call('memorized create', template, dry_run=True))['dry_run']
                created = await call('memorized create', template, idempotency_key='memo-1')
                replay = await call('memorized create', template, idempotency_key='memo-1')
                assert replay['id'] == created['id'] and replay['idempotent_replay']

                await call('memorized show', {'memorized': created['id']})
                await call('memorized list', {'limit': 10})
                updated = await call('memorized update', {
                    'memorized': created['id'], 'expected_version': created['version'],
                    'days_in_advance': 2})
                entered = await call('memorized process', {'as_of': '2026-01-31', 'limit': 5})
                assert entered['entered_count'] == 1

                group = await call('memorized-group create', {'name': 'Parity group'})
                await call('memorized-group show', {'memorized_group': group['id']})
                await call('memorized-group list', {'limit': 10})
                joined = await call('memorized update', {
                    'memorized': created['id'], 'expected_version': updated['version'],
                    'group': group['id'], 'group_ordinal': 1, 'frequency': 'never'})
                await call('memorized-group update', {'memorized_group': group['id'],
                                                      'expected_version': group['version'],
                                                      'name': 'Parity month end'})
                run = await call('memorized-group enter', {'memorized_group': group['id'],
                                                           'date': '2026-02-28'})
                assert run['entered_count'] == 1
                occurrence = run['entered'][0]
                await call('memorized enter', {'memorized': created['id'], 'date': '2026-03-31'})
                extra = await call('memorized-group enter', {'memorized_group': group['id'],
                                                             'date': '2026-04-30'})
                waiting = extra['entered'][0]
                await call('memorized skip', {'occurrence': occurrence['id'],
                                              'expected_version': occurrence['version']},
                           rejected=True)
                await call('memorized retry', {'occurrence': waiting['id'],
                                               'expected_version': waiting['version']},
                           rejected=True)
                left = await call('memorized show', {'memorized': created['id']})
                await call('memorized delete', {'memorized': left['id'],
                                                'expected_version': left['version']})
                await call('memorized-group delete', {'memorized_group': group['id'],
                                                      'expected_version': group['version'] + 1})

                refused = await matrix.call(surface, 'memorized create', {
                    'name': 'Not a create', 'command': 'bill void',
                    'payload': {'bill': created['id'], 'expected_version': 1}}, rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert set(calls) >= COMMANDS - {'memorized skip', 'memorized retry'}
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
