"""R111: the host enters memorized transactions on schedule, assigns to-dos and generates overdue notices.

Every scheduler run here is `Host.schedule_now(now=...)` with a written-out instant: the tick
function the host's timer calls once a minute, under an injected clock. Nothing sleeps. A
"restart" is stopping one host and starting another on the same data root.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bookflow.core.errors import BookflowError
from tests.test_memorized_transactions import _company, dates, memorize, rent_payload


class Clock:
    """The wall clock these tests run on: it starts on 1 January 2026 and moves with each tick, so a
    template saved before a tick was saved before that day, as it would be on a real host."""

    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def set(self, day, hour=0):
        self.value = datetime.fromisoformat(f'{day}T{hour:02d}:00:00').replace(tzinfo=timezone.utc)

    def advance_to(self, moment):
        self.value = max(self.value, moment)


@pytest.fixture
def books(tmp_path, monkeypatch):
    from bookflow.core import clock as core_clock
    wall = Clock()
    monkeypatch.setattr(core_clock, 'now', wall)
    made = _company(tmp_path, monkeypatch, 'UTC')
    made.clock = wall
    return made


def at(day, hour=12):
    return datetime.fromisoformat(f'{day}T{hour:02d}:00:00').replace(tzinfo=timezone.utc)


@contextmanager
def host(books):
    from bookflow.core.context import client_version
    from bookflow.core.host import Host
    running = Host(Path(books.root), version=client_version(), backup_check_seconds=3600, schedule_check_seconds=3600)
    running.start()
    try:
        yield running
    finally:
        running.stop()


def tick(books, day, hour=12):
    moment = at(day, hour)
    if getattr(books, 'clock', None) is not None:
        books.clock.advance_to(moment)
    with host(books) as running:
        return running.schedule_now(now=moment)[books.company]


def company_db(books):
    folder = Path(books.run('company show', {})['path'])
    return sqlite3.connect(folder / 'company.db')


def occurrences(books, name='Monthly office rent'):
    return books.run('memorized show', {'memorized': name})['occurrences']


def events(books, command=None):
    with company_db(books) as db:
        rows = db.execute('SELECT command, actor_kind, on_behalf_of, client_name, interface, reason FROM audit_events'
                          + (' WHERE command = ?' if command else '') + ' ORDER BY seq', (command,) if command else ()).fetchall()
    return rows


# ---------------------------------------------------------------- scheduled entry

def test_the_scheduler_enters_a_due_slot_once_across_ticks_and_a_restart(books):
    memorize(books)
    with host(books) as running:
        assert running.schedule_now(now=at('2026-01-30'))[books.company]['memorized']['entered'] == 0
        first = running.schedule_now(now=at('2026-02-28'))[books.company]
        assert first['memorized']['entered'] == 2, first  # the January backlog and February, each at its own date
        again = running.schedule_now(now=at('2026-02-28', 18))[books.company]
        assert again['memorized']['entered'] == 0
    # a second host on the same books, the same day: nothing more
    assert tick(books, '2026-02-28', 20)['memorized']['entered'] == 0
    assert dates(books) == ['2026-01-31', '2026-02-28']
    assert [o['status'] for o in occurrences(books)] == ['entered', 'entered']

    # Each posting is its author's write, made through the scheduler, with the schedule as the reason;
    # the bookkeeping around it is the host's own.
    posted = events(books, 'bill post')
    assert len(posted) == 2
    for command, kind, behalf, client, interface, reason in posted:
        assert (kind, behalf, client, interface) == ('human', None, 'scheduler', 'system')
        assert reason.startswith('On schedule: Monthly office rent (monthly) for 2026-')
    owner = books.run('memorized show', {'memorized': 'Monthly office rent'})['created_by']
    assert actors(books, 'bill post') == {owner}
    claims = events(books, 'memorized process')
    assert claims and all(row[1:5] == ('system', None, 'scheduler', 'system') for row in claims)
    # Quiet ticks write nothing: no audit event per minute.
    before = len(events(books))
    tick(books, '2026-03-01')
    assert len(events(books)) == before


def test_a_closed_period_is_a_blocked_occurrence_with_a_to_do_and_is_not_retried(books):
    memorize(books)
    books.run('company update', {'closing_date': '2026-01-31'}, reason='Close January')
    out = tick(books, '2026-02-28')
    assert out['memorized']['entered'] == 1 and out['memorized']['blocked'] == 1, out
    assert out['todos_assigned'] == 1
    blocked = [o for o in occurrences(books) if o['status'] == 'blocked']
    assert [(o['slot_date'], o['error_code']) for o in blocked] == [('2026-01-31', 'E_PERIOD_CLOSED')]
    assert dates(books) == ['2026-02-28']

    owner = books.run('memorized show', {'memorized': 'Monthly office rent'})['created_by']
    todos = books.run('todo list', {})['items']
    assert len(todos) == 1 and todos[0]['assignee_id'] == owner and todos[0]['link_type'] == 'memorized'
    assert todos[0]['source_ref'] == 'memorized-occurrence:' + blocked[0]['id']
    assert 'E_PERIOD_CLOSED' in todos[0]['notes']

    # Later ticks neither retry it nor assign a second to-do.
    again = tick(books, '2026-03-02')
    assert again['memorized']['entered'] == 0 and again['todos_assigned'] == 0
    assert [o['attempt_count'] for o in occurrences(books) if o['status'] == 'blocked'] == [1]
    reminders = books.run('reminders', {'as_of': '2026-03-02'})
    assert [(r['slot_date'], r['error_code']) for r in reminders['memorized_failed']] == [('2026-01-31', 'E_PERIOD_CLOSED')]


def actors(books, command):
    with company_db(books) as db:
        return {row[0] for row in db.execute('SELECT actor_id FROM audit_events WHERE command = ?', (command,))}


def member(books, name, role='standard', denies=None):
    """A second person of the company with their own login: (user id, a client acting as them)."""
    from tests.conftest import as_user, make_actor
    uid = make_actor(Path(books.root), name)
    books.client.run('membership grant', {'user': name, 'company': books.company, 'role': role, 'expected_version': 0,
                                          **({'denies': denies} if denies else {})})
    client = as_user(Path(books.root), name)
    return uid, lambda command, raw=None, **context: client.run(command, raw or {}, company=books.company, **context)


def all_bills(books):
    found, cursor = [], None
    while True:
        page = books.run('bill query', {'limit': 200, **({'cursor': cursor} if cursor else {})})
        found += page['items']
        cursor = page.get('next_cursor')
        if not cursor:
            return found


def amounts(books):
    return sorted(b['total_minor_units'] for b in all_bills(books))


def hub_set(books, sql, *args):
    with sqlite3.connect(Path(books.root) / 'hub.db') as hub:
        hub.execute(sql, args)


def admin_id(books):
    return books.client.run('company show', {}, company=books.company)['created_by']


def todo_for(books, occurrence_id):
    return next(t for t in books.run('todo list', {'status': 'all'})['items']
                if t['source_ref'] == 'memorized-occurrence:' + occurrence_id)


def test_D_a_deactivated_author_is_a_clear_blocked_reason_and_an_admins_to_do(books):
    bob, as_bob = member(books, 'bob')
    as_bob('memorized create', dict(name='Bob rent', command='bill post', payload=rent_payload(),
                                    frequency='monthly', start_date='2026-01-31', mode='enter_automatically'))
    hub_set(books, 'UPDATE users SET active = 0 WHERE id = ?', bob)
    out = tick(books, '2026-01-31')
    assert out['memorized']['blocked'] == 1 and out['memorized']['entered'] == 0, out
    [occurrence] = occurrences(books, 'Bob rent')
    assert occurrence['error_code'] == 'E_PERMISSION' and 'deactivated' in occurrence['error_message']
    assert dates(books) == []
    todo = todo_for(books, occurrence['id'])
    assert todo['assignee_id'] == admin_id(books) != bob and 'can no longer post it' in todo['title']


def test_an_author_who_left_the_company_is_blocked_not_company_not_found(books):
    bob, as_bob = member(books, 'bob')
    as_bob('memorized create', dict(name='Bob rent', command='bill post', payload=rent_payload(),
                                    frequency='monthly', start_date='2026-01-31', mode='enter_automatically'))
    hub_set(books, "UPDATE memberships SET revoked_at = '2026-01-01T00:00:00Z' WHERE user_id = ?", bob)
    out = tick(books, '2026-01-31')
    assert out['memorized']['blocked'] == 1, out
    [occurrence] = occurrences(books, 'Bob rent')
    assert occurrence['error_code'] == 'E_PERMISSION' and 'no longer a member' in occurrence['error_message']
    assert todo_for(books, occurrence['id'])['assignee_id'] == admin_id(books)
    assert dates(books) == []


def _admin_group_with_bobs_member(books, as_bob):
    books.run('memorized-group create', {'name': 'Month end', 'frequency': 'monthly', 'start_date': '2026-01-31',
                                         'mode': 'enter_automatically'}, reason='Create the group')
    books.run('memorized create', {'name': 'Rent', 'command': 'bill post', 'payload': rent_payload(),
                                   'group': 'Month end', 'group_ordinal': 1}, reason='Add rent')
    as_bob('memorized create', {'name': 'Bob extra', 'command': 'bill post',
                                'payload': rent_payload(vendor='Tool Depot', account='Tool Hire', amount='9999.00'),
                                'group': 'Month end', 'group_ordinal': 2})


def test_A_a_group_member_runs_on_its_own_authors_authority_not_the_groups(books):
    bob, as_bob = member(books, 'bob')
    _admin_group_with_bobs_member(books, as_bob)
    hub_set(books, 'UPDATE users SET active = 0 WHERE id = ?', bob)
    out = tick(books, '2026-01-31')
    assert out['memorized']['entered'] == 1 and out['memorized']['blocked'] == 1, out
    assert amounts(books) == [180000], 'the deactivated member\'s 9,999 never posts under the admin'
    assert actors(books, 'bill post') == {admin_id(books)}
    [blocked] = [o for o in occurrences(books, 'Bob extra')]
    assert blocked['status'] == 'blocked' and todo_for(books, blocked['id'])['assignee_id'] == admin_id(books)


def test_B_content_added_to_someone_elses_scheduled_group_waits_for_a_person(books):
    bob, as_bob = member(books, 'bob')
    _admin_group_with_bobs_member(books, as_bob)
    out = tick(books, '2026-01-31')
    assert out['memorized']['entered'] == 1 and out['memorized']['held'] == 1, out
    assert amounts(books) == [180000], "Bob's 9,999 never posts under the admin's schedule"
    [held] = occurrences(books, 'Bob extra')
    assert held['status'] == 'pending'
    due = books.run('reminders', {'as_of': '2026-01-31'})['memorized_due']
    [row] = [r for r in due if r['template_name'] == 'Bob extra']
    assert row['waiting_reason'].startswith('memorized by Bob, scheduled by ')
    before = len(events(books))
    tick(books, '2026-02-01')
    assert len(events(books)) == before, 'a held slot is not touched again'
    entered = books.run('memorized process', {'as_of': '2026-01-31'}, reason='The owner enters it by hand')
    assert entered['entered_count'] == 1 and sorted(amounts(books)) == [180000, 999900]
    assert actors(books, 'bill post') == {admin_id(books)}


def test_C_a_waiting_slot_replays_the_current_revision_as_its_authors_write(books):
    bob, as_bob = member(books, 'bob')
    as_bob('memorized create', dict(name='Supplies', command='bill post',
                                    payload=rent_payload(amount='9999.00'), frequency='monthly',
                                    start_date='2026-02-05', days_in_advance=5, mode='remind'))
    assert tick(books, '2026-01-31')['memorized']['waiting'] == 1   # the 02-05 slot, claimed on Bob's revision
    shown = books.run('memorized show', {'memorized': 'Supplies'})
    books.run('memorized update', {'memorized': 'Supplies', 'expected_version': shown['version'],
                                   'payload': rent_payload(amount='120.00'), 'mode': 'enter_automatically'},
              reason='Correct the amount')
    out = tick(books, '2026-02-01')
    assert out['memorized']['entered'] == 1, out
    assert amounts(books) == [12000]
    assert actors(books, 'bill post') == {admin_id(books)}
    [occurrence] = occurrences(books, 'Supplies')
    assert occurrence['revision_id'] == books.run('memorized show', {'memorized': 'Supplies'})['current_revision_id']


def test_F_a_client_calling_itself_scheduler_keeps_its_own_reason(books):
    import bookflow
    memorize(books)
    pretender = bookflow.connect(data_root=str(books.root), client_name='scheduler')
    pretender.run('memorized process', {'as_of': '2026-01-31'}, company=books.company, reason='My own month end')
    with company_db(books) as db:
        reasons = {row[0] for row in db.execute("SELECT reason FROM audit_events WHERE command = 'bill post'")}
    assert reasons == {'My own month end'}


def test_G_waiting_reminders_never_starve_automatic_entries(books):
    memorize(books, name='Daily check', mode='remind', frequency='daily', start_date='2026-01-01')
    memorize(books, name='Rent', start_date='2026-08-31')
    tick(books, '2026-07-31')                       # 200+ reminder slots now wait
    assert len([o for o in occurrences(books, 'Daily check') if o['status'] == 'pending']) >= 50
    out = tick(books, '2026-09-01')
    assert out['memorized']['entered'] == 1 and dates(books) == ['2026-08-31'], out


def test_H_a_group_backlog_past_the_batch_limit_loses_no_members_slot(books):
    books.clock.set('2025-12-31')                   # scheduled the day before its first slot
    books.run('memorized-group create', {'name': 'Daily three', 'frequency': 'daily', 'start_date': '2026-01-01',
                                         'mode': 'enter_automatically'}, reason='Create the group')
    for ordinal in (1, 2, 3):
        books.run('memorized create', {'name': f'Part {ordinal}', 'command': 'bill post',
                                       'payload': rent_payload(amount=f'{ordinal}.00'),
                                       'group': 'Daily three', 'group_ordinal': ordinal}, reason='Add a part')
    entered = []
    with host(books) as running:
        for minute in range(12):                    # 69 slots of 3, a group slot never split by a batch
            out = running.schedule_now(now=at('2026-03-10').replace(minute=minute))[books.company]['memorized']
            assert out['entered'] % 3 == 0, out
            entered.append(out['entered'])
    assert entered[0] == 24 and sum(entered) == 207, entered
    bills = all_bills(books)
    for ordinal in (1, 2, 3):
        days = sorted(b['date'] for b in bills if b['total_minor_units'] == ordinal * 100)
        assert len(days) == 69 and days[0] == '2026-01-01' and days[-1] == '2026-03-10', ordinal


def test_K_deleting_a_template_sets_its_waiting_slots_aside(books):
    memorize(books, mode='remind')
    tick(books, '2026-02-28')
    assert [o['status'] for o in occurrences(books)] == ['pending', 'pending']
    shown = books.run('memorized show', {'memorized': 'Monthly office rent'})
    books.run('memorized delete', {'memorized': shown['id'], 'expected_version': shown['version']}, reason='No longer rented')
    assert books.run('memorized process', {'as_of': '2026-03-31'}, reason='Run')['entered_count'] == 0
    tick(books, '2026-03-31')
    assert dates(books) == []
    assert books.run('reminders', {'as_of': '2026-03-31'})['memorized_due'] == []


def test_L_an_unknown_user_and_a_non_member_get_the_same_answer(books):
    from tests.conftest import make_actor
    make_actor(Path(books.root), 'stranger')
    answers = []
    for who in ('stranger', 'nobody-at-all', '01ZZZZZZZZZZZZZZZZZZZZZZZZ'):
        with pytest.raises(BookflowError) as refused:
            books.run('todo create', {'title': 'x', 'assignee': who})
        answers.append((refused.value.code, json.dumps(refused.value.details, sort_keys=True), refused.value.message))
    assert len(set(answers)) == 1 and answers[0][0] == 'E_VALIDATION'


def test_M_reminders_leave_out_what_the_reader_may_not_list(books):
    _invoice(books, 'Rivera Construction', '2026-01-02', '500.00')
    books.run('todo create', {'title': 'HR: review salaries', 'due_date': '2026-03-01'})
    books.run('company update', {'overdue_notice_days': 1, 'overdue_notice_template': 'letter'}, reason='Notices on')
    books.run('notice generate', {'as_of': '2026-03-05'}, reason='Generate')
    mallory, as_mallory = member(books, 'mallory', denies=['note', 'customer'])
    for refused_command in ('todo list', 'notice list'):
        with pytest.raises(BookflowError) as refused:
            as_mallory(refused_command, {})
        assert refused.value.code == 'E_PERMISSION'
    seen = as_mallory('reminders', {'as_of': '2026-03-05'})
    assert {s['key'] for s in seen['sections']} == {'memorized_due', 'memorized_failed', 'overdue_invoices', 'bills_to_pay'}
    assert seen['todos_due'] == [] and seen['notices_ready'] == []
    assert 'HR: review salaries' not in json.dumps(seen) and 'Dear' not in json.dumps(seen)
    full = books.run('reminders', {'as_of': '2026-03-05'})
    assert {'todos_due', 'notices_ready'} <= {s['key'] for s in full['sections']}


def test_catch_up_enters_recent_missed_slots_and_leaves_older_ones_for_a_person(books):
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='Catch up ten days at most')
    memorize(books)
    out = tick(books, '2026-03-31')
    assert out['memorized'] == {'entered': 1, 'blocked': 0, 'waiting': 2, 'left_for_a_person': 2}, out
    assert dates(books) == ['2026-03-31']
    waiting = sorted(o['slot_date'] for o in occurrences(books) if o['status'] == 'pending')
    assert waiting == ['2026-01-31', '2026-02-28']
    due = books.run('reminders', {'as_of': '2026-03-31'})['memorized_due']
    assert [r['slot_date'] for r in due] == ['2026-01-31', '2026-02-28']
    # The next tick leaves them where they are, and writes nothing.
    before = len(events(books))
    assert tick(books, '2026-04-01')['memorized']['entered'] == 0
    assert len(events(books)) == before
    # A person enters them on purpose.
    entered = books.run('memorized process', {'as_of': '2026-04-01'}, reason='Enter the missed rent')
    assert entered['entered_count'] == 2
    assert dates(books) == ['2026-01-31', '2026-02-28', '2026-03-31']


def test_remind_only_lists_the_slot_for_a_person_and_enters_nothing(books):
    memorize(books, mode='remind')
    before = books.run('reminders', {'as_of': '2026-02-01'})['memorized_due']
    assert [(r['slot_date'], r['occurrence_id']) for r in before] == [('2026-01-31', None)]  # from the schedule
    out = tick(books, '2026-02-01')
    assert out['memorized']['entered'] == 0 and out['memorized']['waiting'] == 1
    assert dates(books) == []
    due = books.run('reminders', {'as_of': '2026-02-01'})['memorized_due']
    assert len(due) == 1 and due[0]['occurrence_id'] and due[0]['mode'] == 'remind'
    assert books.run('todo list', {})['items'] == []
    before = len(events(books))
    tick(books, '2026-02-02')
    assert len(events(books)) == before


# ---------------------------------------------------------------- to-dos

def test_a_to_do_is_created_assigned_listed_and_completed(books):
    from tests.conftest import as_user, make_actor
    bob = make_actor(Path(books.root), 'bob', company_role=(books.company, 'standard'))
    outsider = make_actor(Path(books.root), 'stranger')
    made = books.run('todo create', {'title': 'Call Rivera about the deposit', 'due_date': '2026-03-10',
                                     'assignee': 'bob', 'priority': 'high', 'link_type': 'customer',
                                     'link': 'Rivera Construction', 'notes': 'They asked for a call back.'})
    assert made['assignee_id'] == bob and made['status'] == 'open' and made['link_label'] == 'Rivera Construction'
    mine = books.run('todo create', {'title': 'Review the bills', 'due_date': '2026-03-01'})
    assert mine['assignee_id'] == mine['created_by']
    with pytest.raises(BookflowError) as refused:
        books.run('todo create', {'title': 'Nobody can see this', 'assignee': 'stranger'})
    assert refused.value.code == 'E_VALIDATION'

    bob_client = as_user(Path(books.root), 'bob')
    listed = bob_client.run('todo list', {'mine': True}, company=books.company)
    assert [t['title'] for t in listed['items']] == ['Call Rivera about the deposit']
    everyone = books.run('todo list', {'due_by': '2026-03-05'})
    assert [t['title'] for t in everyone['items']] == ['Review the bills']

    moved = books.run('todo update', {'todo': made['id'], 'due_date': '2026-03-12', 'expected_version': 1})
    assert moved['due_date'] == '2026-03-12' and moved['version'] == 2
    with pytest.raises(BookflowError) as stale:
        books.run('todo update', {'todo': made['id'], 'title': 'x', 'expected_version': 1})
    assert stale.value.code == 'E_VERSION_CONFLICT'
    done = bob_client.run('todo complete', {'todo': made['id']}, company=books.company)
    assert done['status'] == 'done' and done['completed_by'] == bob and done['completed_at']
    assert bob_client.run('todo list', {'mine': True}, company=books.company)['items'] == []
    assert [t['id'] for t in books.run('todo list', {'status': 'done'})['items']] == [made['id']]
    reopened = books.run('todo update', {'todo': made['id'], 'status': 'open'})
    assert reopened['status'] == 'open' and reopened['completed_at'] is None
    due = books.run('reminders', {'as_of': '2026-03-12'})
    assert {t['title'] for t in due['todos_due']} == {'Call Rivera about the deposit', 'Review the bills'}
    assert [t['title'] for t in books.run('reminders', {'as_of': '2026-03-12', 'mine': True})['todos_due']] == ['Review the bills']


# ---------------------------------------------------------------- overdue notices

def _service(books):
    if getattr(books, 'service', None):
        return books.service
    income = books.client.account.create(company=books.company, name='Fees', type='income')['id']
    code = next(row['id'] for row in books.run('sales-tax-code list', {})['items'] if not row['taxable'])
    books.service = books.run('item create', dict(name='Work', type='service', sales_enabled=True, description='Work',
                                                  income_account_id=income, price='100.00', sales_tax_code_id=code))['id']
    return books.service


def _invoice(books, customer, date, amount, terms_days=30):
    from datetime import date as _d, timedelta
    due = (_d.fromisoformat(date) + timedelta(days=terms_days)).isoformat()
    return books.run('invoice post', {'customer': customer, 'date': date, 'due_date': due,
                                      'lines': [{'item': _service(books), 'quantity': '1', 'unit_price': amount}]})


def test_notices_follow_the_policy_once_per_period_and_a_dismissed_one_stays_dismissed(books):
    books.run('customer create', {'name': 'Small Balance Co'})
    books.run('customer create', {'name': 'Paid Up Ltd'})
    _invoice(books, 'Rivera Construction', '2026-01-02', '500.00')      # due 02-01
    _invoice(books, 'Small Balance Co', '2026-01-02', '10.00')           # under the minimum
    _invoice(books, 'Paid Up Ltd', '2026-02-20', '300.00')                # due 03-22: not yet overdue
    assert tick(books, '2026-03-05')['notices'] == {'enabled': False, 'generated': 0}
    books.run('company update', {'overdue_notice_days': 30, 'overdue_notice_minimum': '25',
                                 'overdue_notice_frequency': 'monthly', 'overdue_notice_template': 'letter'},
              reason='Turn on collection notices')

    first = tick(books, '2026-03-05')['notices']
    assert first == {'enabled': True, 'period_key': '2026-03-01', 'generated': 1, 'already_for_period': 0}
    [notice] = books.run('notice list', {})['items']
    assert notice['customer_name'] == 'Rivera Construction' and notice['overdue']['amount'] == '500.00'
    assert notice['days_overdue'] == 32 and notice['template'] == 'letter' and notice['status'] == 'ready'
    assert 'Dear Rivera Construction' in notice['letter'] and notice['print_path'].endswith('/print')

    # The same period again, on another day or another host: no duplicate.
    assert tick(books, '2026-03-20')['notices']['generated'] == 0
    dismissed = books.run('notice dismiss', {'notice': notice['id'], 'note': 'Called them; paying Friday'})
    assert dismissed['status'] == 'dismissed' and dismissed['dismissed_reason'] == 'Called them; paying Friday'
    again = tick(books, '2026-03-28')['notices']
    assert again['generated'] == 0 and again['already_for_period'] == 1
    assert books.run('notice list', {})['items'] == []
    # The next period, still overdue: a new notice, which a person marks sent.
    april = tick(books, '2026-04-02')['notices']
    assert april['generated'] == 1 and april['period_key'] == '2026-04-01'
    [fresh] = books.run('notice list', {})['items']
    assert fresh['days_overdue'] == 60
    reminded = books.run('reminders', {'as_of': '2026-04-02'})
    assert [n['id'] for n in reminded['notices_ready']] == [fresh['id']]
    sent = books.run('notice mark-sent', {'notice': fresh['id'], 'method': 'email'})
    assert sent['status'] == 'sent' and sent['sent_method'] == 'email'
    with pytest.raises(BookflowError):
        books.run('notice dismiss', {'notice': fresh['id']})
    assert [n['status'] for n in books.run('notice list', {'status': 'all'})['items']] == ['sent', 'dismissed']
    # The notice audit names the host for the generated ones.
    generated = events(books, 'notice generate')
    assert generated and all(row[1] == 'system' and row[3] == 'scheduler' for row in generated)


def test_reminders_summarise_overdue_invoices_and_bills_to_pay(books):
    _invoice(books, 'Rivera Construction', '2026-01-02', '200.00')
    books.run('bill post', dict(rent_payload(), date='2026-02-20', due_date='2026-03-05'))
    out = books.run('reminders', {'as_of': '2026-03-01'})
    sections = {s['key']: s for s in out['sections']}
    assert sections['overdue_invoices']['count'] == 1 and sections['overdue_invoices']['total']['amount'] == '200.00'
    assert sections['bills_to_pay']['count'] == 1 and out['bills_to_pay'][0]['overdue'] is False
    assert out['total_count'] == 2


# ---------------------------------------------------------------- storage

def test_co0085_is_exactly_the_current_metadata_and_a_fresh_company_reaches_it(tmp_path):
    import importlib

    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import HEADS, migrate_to_head
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0085_scheduling')
    tables = ('todos', 'overdue_notices', 'scheduled_revision_authority', 'scheduled_group_authority', 'scheduled_holds')
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())) for name in tables)
    indexes = tuple(str(CreateIndex(index).compile(dialect=dialect())) for name in tables
                    for index in sorted(c.metadata.tables[name].indexes, key=lambda index: index.name))
    assert m.DDL == compiled and m.INDEXES == indexes
    assert set(m.COLUMNS) <= set(c.company_info.c.keys())
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        columns = {row[1] for row in db.raw.execute('PRAGMA table_info(company_info)')}
        assert set(m.COLUMNS) <= columns
        assert db.raw.execute('SELECT overdue_notice_frequency, overdue_notice_template, overdue_notice_days '
                              'FROM company_info').fetchall() in ([], [('monthly', 'statement', None)])
