"""R111 third review (X1-X7), turned around: an automatic entry is made unattended only when one
principal decided both its content and its schedule; anything else waits for a person.

Each test is the reviewer's repro with the assertion that now holds.
"""
import sqlite3
from pathlib import Path

import pytest

from bookflow.core.errors import BookflowError
from tests.test_memorized_transactions import memorize, rent_payload
from tests.test_scheduling import (  # noqa: F401 - fixture
    admin_id, at, books, company_db, occurrences, tick, todo_for)
from tests.test_scheduling_authority import audit_rows, bill_totals, local_agent, run_as, tools


def group(books, mode='enter_automatically', name='Month end', via=None):
    body = {'name': name, 'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': mode}
    if via is None:
        books.run('memorized-group create', body, reason='Sets up ' + name)
    else:
        via.run('memorized-group create', body, company=books.company)


def waiting(books, name):
    return [r for r in books.run('reminders', {'as_of': '2026-01-31'})['memorized_due'] if r['template_name'] == name]


def test_X1_a_setter_who_also_wrote_it_posts_as_itself_and_is_blocked_once_it_lost_the_right(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    paula = make_actor(root, 'paula', company_role=(books.company, 'standard'))
    as_paula = as_user(root, 'paula')
    group(books, via=as_paula)
    as_paula.run('memorized create', {'name': 'Draw', 'command': 'bill post', 'payload': tools('500.00'),
                                      'group': 'Month end', 'group_ordinal': 1}, company=books.company)
    tick(books, '2026-01-31')
    assert [r['actor_id'] for r in audit_rows(books, 'bill post')] == [paula]
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute("UPDATE memberships SET role = 'readonly' WHERE user_id = ?", (paula,))
    tick(books, '2026-02-28')
    [feb] = [o for o in occurrences(books, 'Draw') if o['slot_date'] == '2026-02-28']
    assert feb['status'] == 'blocked' and feb['error_code'] == 'E_PERMISSION'
    assert bill_totals(books) == [50000]


def test_X2_an_agents_journal_in_a_persons_scheduled_group_waits_for_a_person(books):
    admin = admin_id(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    group(books)
    bank = books.run('account create', {'name': 'Sweep Checking', 'type': 'bank'})['id']
    journal = {'lines': [{'account': bank, 'side': 'debit', 'amount': '5000.00'},
                         {'account': 'Office Rent', 'side': 'credit', 'amount': '5000.00'}]}
    run_as(books, 'helperlogin', 'memorized create', {'name': 'Agent sweep', 'command': 'journal post', 'payload': journal,
                                                      'group': 'Month end', 'group_ordinal': 1},
           on_behalf_of=admin, reason='Agent adds its sweep')
    out = tick(books, '2026-01-31')
    assert out['memorized']['entered'] == 0 and out['memorized']['held'] == 1, out
    assert audit_rows(books, 'journal post') == []
    [row] = waiting(books, 'Agent sweep')
    assert 'memorized by' in row['waiting_reason'] and 'scheduled by' in row['waiting_reason']


def test_X3_a_members_bill_in_the_admins_group_waits_and_posts_only_as_the_person_who_enters_it(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    group(books)
    as_user(root, 'mallory').run('memorized create', {'name': 'Mallory tools', 'command': 'bill post',
                                                      'payload': tools('9999.00'), 'group': 'Month end',
                                                      'group_ordinal': 1}, company=books.company)
    tick(books, '2026-01-31')
    assert bill_totals(books) == []
    [row] = waiting(books, 'Mallory tools')
    assert row['waiting_reason'].startswith('memorized by Mallory, scheduled by ')
    books.run('memorized process', {'as_of': '2026-01-31'}, reason='The admin checked it and enters it')
    assert [r['actor_id'] for r in audit_rows(books, 'bill post')] == [admin_id(books)]


def _pre_upgrade(books, name='Month end'):
    with company_db(books) as db:
        gid = db.execute('SELECT id FROM memorized_groups WHERE name = ?', (name,)).fetchone()[0]
        db.execute('DELETE FROM scheduled_group_authority WHERE group_id = ?', (gid,))


@pytest.mark.parametrize('renamer', ('mallory', 'agent'))
def test_X4_a_pre_upgrade_group_even_renamed_waits_with_the_re_save_message(books, renamer):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    admin = admin_id(books)
    group(books)
    books.run('memorized create', {'name': 'Rent', 'command': 'bill post', 'payload': rent_payload(),
                                   'group': 'Month end', 'group_ordinal': 1}, reason='admin member')
    _pre_upgrade(books)
    shown = books.run('memorized-group show', {'memorized_group': 'Month end'})
    rename = {'memorized_group': 'Month end', 'expected_version': shown['version'], 'name': 'Month-end bills'}
    if renamer == 'mallory':
        make_actor(root, 'mallory', company_role=(books.company, 'standard'))
        as_user(root, 'mallory').run('memorized-group update', rename, company=books.company)
    else:
        local_agent(books, 'helper', 'helperlogin', admin)
        run_as(books, 'helperlogin', 'memorized-group update', rename, on_behalf_of=admin, reason='tidy name')
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM scheduled_group_authority').fetchone()[0] == 0
    out = tick(books, '2026-01-31')
    assert out['memorized']['held'] == 1 and bill_totals(books) == [], out
    [row] = waiting(books, 'Rent')
    assert row['waiting_reason'] == 'schedule set before this upgrade; open the group and save its schedule to let it run'


def test_X5_a_widening_by_another_principal_leaves_the_slots_it_lets_in_waiting(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'olga', company_role=(books.company, 'admin'))
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')                         # 01-31 and 02-28 left for a person, 03-31 entered
    as_user(root, 'olga').run('company update', {'scheduler_catch_up_days': 300}, company=books.company)
    out = tick(books, '2026-04-01')
    assert out['memorized']['entered'] == 0 and out['memorized']['held'] == 2, out
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert all('catch-up window widened by Olga (300 days), not by its author' in r['waiting_reason'] for r in due) and len(due) == 2


def test_X5_a_widening_by_the_author_lets_them_in_and_names_it_whatever_its_size(books):
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')
    books.run('company update', {'scheduler_catch_up_days': 300}, reason='Admin widens catch-up')
    assert tick(books, '2026-04-01')['memorized']['entered'] == 2
    late = [r for r in audit_rows(books, 'bill post') if '2026-01-31' in r['reason'] or '2026-02-28' in r['reason']]
    assert len(late) == 2 and all('left under 10 days; catch-up window widened by' in r['reason'] and '(300 days)' in r['reason']
                               for r in late)


def test_X6_the_retry_preview_names_the_revision_that_posts(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    mallory = make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    memorize(books)
    books.run('company update', {'closing_date': '2026-01-31'}, reason='close January')
    tick(books, '2026-01-31')
    books.run('company update', {'closing_date': None}, reason='reopen')
    [occ] = occurrences(books)
    show = books.run('memorized show', {'memorized': 'Monthly office rent'})
    as_user(root, 'mallory').run('memorized update', {'memorized': 'Monthly office rent', 'expected_version': show['version'],
                                                      'payload': tools('9999.00')}, company=books.company)
    preview = books.run('memorized retry', {'occurrence': occ['id'], 'expected_version': occ['version']},
                        reason='retry', dry_run=True)
    assert preview['occurrence']['replays_author_id'] == mallory
    done = books.run('memorized retry', {'occurrence': occ['id'], 'expected_version': occ['version'],
                                         'expected_facts_fingerprint': preview['facts_fingerprint']}, reason='retry')
    assert preview['occurrence']['revision_id'] == done['occurrence']['revision_id']


def test_X6b_the_process_preview_names_what_posts_and_a_change_after_it_is_stale(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')
    show = books.run('memorized show', {'memorized': 'Monthly office rent'})
    as_mallory = as_user(root, 'mallory')
    as_mallory.run('memorized update', {'memorized': 'Monthly office rent', 'expected_version': show['version'],
                                        'payload': tools('9999.00')}, company=books.company)
    preview = books.run('memorized process', {'as_of': '2026-03-31'}, reason='enter', dry_run=True)
    show = books.run('memorized show', {'memorized': 'Monthly office rent'})
    as_mallory.run('memorized update', {'memorized': 'Monthly office rent', 'expected_version': show['version'],
                                        'payload': tools('5.00')}, company=books.company)
    with pytest.raises(BookflowError) as stale:
        books.run('memorized process', {'as_of': '2026-03-31', 'expected_facts_fingerprint': preview['facts_fingerprint']},
                  reason='enter')
    assert stale.value.code == 'E_PREVIEW_STALE'
    fresh = books.run('memorized process', {'as_of': '2026-03-31'}, reason='enter', dry_run=True)
    done = books.run('memorized process', {'as_of': '2026-03-31', 'expected_facts_fingerprint': fresh['facts_fingerprint']},
                     reason='enter')
    assert [o['revision_id'] for o in fresh['entered']] == [o['revision_id'] for o in done['entered']]


def test_X7_the_review_to_do_goes_to_an_organization_owner(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    installer = admin_id(books)
    with sqlite3.connect(root / 'hub.db') as hub:
        org = hub.execute('SELECT organization_id FROM companies WHERE id = ?', (books.company,)).fetchone()[0]
    olga = make_actor(root, 'olga', org_role=(org, 'owner'))
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    as_user(root, 'mallory').run('memorized create', {
        'name': 'Mallory tools', 'command': 'bill post', 'payload': tools('12.00'),
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'}, company=books.company)
    books.client.run('user deactivate', {'user': 'mallory'})
    folder = Path(books.run('company show', {})['path'])
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute("UPDATE memberships SET revoked_at = '2026-01-01T00:00:00.000Z' WHERE user_id = ?", (installer,))
    tick(books, '2026-01-31')
    with sqlite3.connect(folder / 'company.db') as db:
        assert [row[0] for row in db.execute('SELECT assignee_id FROM todos')] == [olga]


# ---------------------------------------------------------------- fourth review (Z1-Z3, Z6)

def _mallorys_held_bill(books):
    """Mallory's bill in the admin's scheduled group: held for a person by the scheduler."""
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    group(books)
    as_user(root, 'mallory').run('memorized create', {'name': 'Mallory tools', 'command': 'bill post',
                                                      'payload': tools('9999.00'), 'group': 'Month end',
                                                      'group_ordinal': 1}, company=books.company)
    tick(books, '2026-01-31')
    [occ] = occurrences(books, 'Mallory tools')
    return occ


def test_Z1_an_agent_can_neither_enter_nor_skip_a_slot_held_for_a_person(books):
    admin = admin_id(books)
    occ = _mallorys_held_bill(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    for name, body in (('memorized retry', {'occurrence': occ['id'], 'expected_version': occ['version']}),
                       ('memorized skip', {'occurrence': occ['id'], 'expected_version': occ['version']}),
                       ('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-01-31'})):
        with pytest.raises(BookflowError) as refused:
            run_as(books, 'helperlogin', name, body, on_behalf_of=admin, reason='Agent tries')
        assert refused.value.code == 'E_PERMISSION' and refused.value.details['reason'] == 'held_for_a_person', name
    out = run_as(books, 'helperlogin', 'memorized process', {'as_of': '2026-01-31'}, on_behalf_of=admin, reason='Agent runs')
    assert out['entered_count'] == 0 and any('held for a person' in w for w in out['warnings'])
    assert bill_totals(books) == []
    assert [r['template_name'] for r in waiting(books, 'Mallory tools')] == ['Mallory tools']


def test_Z1_a_person_entering_or_skipping_a_held_slot_releases_the_hold_on_the_record(books):
    occ = _mallorys_held_bill(books)
    books.run('memorized skip', {'occurrence': occ['id'], 'expected_version': occ['version']}, reason='Not this month')
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM scheduled_holds').fetchone()[0] == 0
        [summary] = db.execute("SELECT summary FROM audit_events WHERE summary LIKE '%held for a person'").fetchall()[-1:]
    assert 'skipped 1 entry held for a person' in summary[0]


def test_Z6_enter_and_group_enter_take_a_fingerprint_and_a_superseded_slot_needs_one(books):
    memorize(books, name='Solo', mode='remind')
    preview = books.run('memorized enter', {'memorized': 'Solo', 'date': '2026-01-15'}, reason='Look', dry_run=True)
    show = books.run('memorized show', {'memorized': 'Solo'})
    books.run('memorized update', {'memorized': show['id'], 'expected_version': show['version'], 'payload': tools('3.00')},
              reason='Edit')
    with pytest.raises(BookflowError) as stale:
        books.run('memorized enter', {'memorized': 'Solo', 'date': '2026-01-15',
                                      'expected_facts_fingerprint': preview['facts_fingerprint']}, reason='Enter')
    assert stale.value.code == 'E_PREVIEW_STALE'
    occ = _mallorys_held_bill(books)
    show = books.run('memorized show', {'memorized': 'Mallory tools'})
    books.run('memorized update', {'memorized': show['id'], 'expected_version': show['version'], 'payload': tools('7.00')},
              reason='The admin corrects it')
    with pytest.raises(BookflowError) as unseen:
        books.run('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-01-31'}, reason='Enter unseen')
    assert unseen.value.code == 'E_VALIDATION'
    seen = books.run('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-01-31'}, reason='Look',
                     dry_run=True)
    done = books.run('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-01-31',
                                               'expected_facts_fingerprint': seen['facts_fingerprint']}, reason='Enter')
    assert done['entered_count'] == 1 and bill_totals(books) == [700]
    assert occ['id'] == done['entered'][0]['id']


def test_Z6_the_workbench_process_form_carries_the_previews_fingerprint():
    from pathlib import Path as P
    pages = P(__file__).resolve().parents[1] / 'src/bookflow/adapters/workbench'
    assert "'memorized process', 'memorized retry', 'memorized enter', 'memorized-group enter'" in (pages / 'pages.py').read_text()
    assert '{% if fingerprint_form %}<input type="hidden" name="f:expected_facts_fingerprint"' in \
        (pages / 'templates/form.html').read_text()


def test_Z2_a_narrowing_after_an_agents_widening_still_names_and_honours_it(books):
    admin = admin_id(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')                          # 01-31 and 02-28 left for a person under 10 days
    with pytest.raises(BookflowError):
        run_as(books, 'helperlogin', 'company update', {'scheduler_catch_up_days': 300}, on_behalf_of=admin, reason='widen')
    from tests.conftest import as_user, make_actor
    make_actor(Path(books.root), 'olga', company_role=(books.company, 'admin'))
    as_user(Path(books.root), 'olga').run('company update', {'scheduler_catch_up_days': 300}, company=books.company)
    books.run('company update', {'scheduler_catch_up_days': 200}, reason='The admin narrows it')
    out = tick(books, '2026-04-01')
    # The admin set the current window: the slots enter, naming every widening since they were left.
    assert out['memorized']['entered'] == 2, out
    late = [r for r in audit_rows(books, 'bill post') if '2026-01-31' in r['reason'] or '2026-02-28' in r['reason']]
    assert len(late) == 2 and all('left under 10 days; catch-up window widened by Olga (300 days)' in r['reason'] for r in late)


def test_Z2_slots_stay_held_naming_each_widener_when_the_author_set_neither(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'olga', company_role=(books.company, 'admin'))
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')
    as_user(root, 'olga').run('company update', {'scheduler_catch_up_days': 300}, company=books.company)
    as_user(root, 'olga').run('company update', {'scheduler_catch_up_days': 200}, company=books.company)
    out = tick(books, '2026-04-01')
    assert out['memorized']['entered'] == 0 and out['memorized']['held'] == 2, out
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert len(due) == 2 and all('catch-up window widened by Olga (300 days)' in r['waiting_reason'] for r in due)


def test_Z3_re_anchoring_a_group_with_a_held_slot_keeps_one_hold_and_the_tick_runs(books):
    occ = _mallorys_held_bill(books)
    shown = books.run('memorized-group show', {'memorized_group': 'Month end'})
    books.run('memorized-group update', {'memorized_group': 'Month end', 'expected_version': shown['version'],
                                         'start_date': '2026-01-31'}, reason='Re-anchor')
    out = tick(books, '2026-02-01')
    assert 'error' not in out['memorized'], out
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM scheduled_holds WHERE occurrence_id = ?', (occ['id'],)).fetchone()[0] == 1


def test_Z3_a_failed_company_tick_is_reported_plainly(books, monkeypatch):
    from bookflow.company import scheduler
    memorize(books)

    def broken(*args, **kwargs):
        import sqlite3 as driver
        raise driver.IntegrityError('UNIQUE constraint failed')
    monkeypatch.setattr(scheduler, 'enter_due', broken)
    out = tick(books, '2026-01-31')
    assert out['memorized']['error'] == 'E_INTERNAL' and 'host log' in out['memorized']['message']


def test_an_agent_is_named_with_the_person_it_acted_for(books):
    admin = admin_id(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    group(books)
    run_as(books, 'helperlogin', 'memorized create', {'name': 'Mixed', 'command': 'bill post', 'payload': tools('2.00'),
                                                      'group': 'Month end', 'group_ordinal': 1},
           on_behalf_of=admin, reason='For the admin')
    tick(books, '2026-01-31')
    [row] = waiting(books, 'Mixed')
    memorized_by = row['waiting_reason'].split(', scheduled by')[0]
    assert memorized_by.lower().startswith('memorized by helper for ') and memorized_by.split(' for ')[1], row


def test_the_scheduler_interval_is_documented():
    from bookflow.core import registry
    registry.load_all()
    assert 'BOOKFLOW_SCHEDULE_CHECK_SECONDS' in registry.get('serve').description


def test_Z2_v4_an_agents_widening_then_the_admins_narrowing_keeps_mallorys_slots_waiting(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    admin = admin_id(books)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    make_actor(root, 'olga', company_role=(books.company, 'admin'))
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    as_user(root, 'mallory').run('memorized create', {
        'name': 'Mallory tools', 'command': 'bill post', 'payload': tools('9999.00'),
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'}, company=books.company)
    tick(books, '2026-03-31')
    as_user(root, 'olga').run('company update', {'scheduler_catch_up_days': 300}, company=books.company)
    books.run('company update', {'scheduler_catch_up_days': 200}, reason='Admin pulls it back to 200')
    out = tick(books, '2026-04-01')
    late = [r for r in audit_rows(books, 'bill post') if '2026-01-31' in r['reason'] or '2026-02-28' in r['reason']]
    assert late == [] and out['memorized']['held'] == 2, out
    assert admin


def test_Z2_the_widener_read_fails_closed_when_the_audit_does_not_match(books):
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')
    books.run('company update', {'scheduler_catch_up_days': 300}, reason='The author widens it')
    with company_db(books) as db:   # a window in force that no audited change set
        db.execute('UPDATE company_info SET scheduler_catch_up_days = 250')
    out = tick(books, '2026-04-01')
    assert out['memorized']['entered'] == 0 and out['memorized']['held'] == 2, out
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert all('could not be confirmed' in r['waiting_reason'] for r in due)


def test_Z3_v4_a_held_slot_coming_round_again_does_not_break_later_ticks(books):
    _mallorys_held_bill(books)
    books.run('memorized create', {'name': 'Rent', 'command': 'bill post', 'payload': rent_payload(),
                                   'group': 'Month end', 'group_ordinal': 2}, reason='admin member')
    shown = books.run('memorized-group show', {'memorized_group': 'Month end'})
    books.run('memorized-group update', {'memorized_group': 'Month end', 'expected_version': shown['version'],
                                         'start_date': '2026-01-31'}, reason='Re-save the same start date')
    books.run('memorized create', {'name': 'Fresh rent', 'command': 'bill post', 'payload': tools('7.00'),
                                   'frequency': 'monthly', 'start_date': '2026-02-28', 'mode': 'enter_automatically'},
              reason='an unrelated automatic template')
    second, third = tick(books, '2026-02-01'), tick(books, '2026-02-28')
    assert 'error' not in second['memorized'] and 'error' not in third['memorized'], (second, third)
    assert 700 in bill_totals(books)


def test_Z6_v4_enter_and_group_enter_bind_to_their_preview(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    memorize(books, frequency='never', mode='on_demand', start_date=None)
    books.run('memorized-group create', {'name': 'Ad hoc'}, reason='g')
    books.run('memorized create', {'name': 'Grouped', 'command': 'bill post', 'payload': tools('5.00'),
                                   'group': 'Ad hoc', 'group_ordinal': 1}, reason='m')
    for name, body in (('memorized enter', {'memorized': 'Monthly office rent', 'date': '2026-02-10'}),
                       ('memorized-group enter', {'memorized_group': 'Ad hoc', 'date': '2026-02-10'})):
        preview = books.run(name, body, reason='look first', dry_run=True)
        assert books.run(name, dict(body, expected_facts_fingerprint=preview['facts_fingerprint']),
                         reason='bind', dry_run=True)['dry_run']
    preview = books.run('memorized enter', {'memorized': 'Monthly office rent', 'date': '2026-02-10'}, reason='look',
                        dry_run=True)
    show = books.run('memorized show', {'memorized': 'Monthly office rent'})
    as_user(root, 'mallory').run('memorized update', {'memorized': 'Monthly office rent', 'expected_version': show['version'],
                                                      'payload': tools('9999.00')}, company=books.company)
    with pytest.raises(BookflowError) as stale:
        books.run('memorized enter', {'memorized': 'Monthly office rent', 'date': '2026-02-10',
                                      'expected_facts_fingerprint': preview['facts_fingerprint']}, reason='save what I saw')
    assert stale.value.code == 'E_PREVIEW_STALE' and bill_totals(books) == []


# ---------------------------------------------------------------- fifth review (Z8, Z9, Z12, Z13, Z10 wording)

def test_Z8_entering_a_held_template_by_hand_takes_the_slot_so_it_never_posts_twice(books):
    occ = _mallorys_held_bill(books)
    out = books.run('memorized enter', {'memorized': 'Mallory tools', 'date': '2026-01-31'}, reason='Entering the held one')
    assert out['entered'][0]['id'] == occ['id'] and out['entered'][0]['origin'] == 'scheduled'
    assert any('held for a person' in w for w in out['warnings'])
    assert waiting(books, 'Mallory tools') == []
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM scheduled_holds').fetchone()[0] == 0
    books.run('memorized process', {'as_of': '2026-01-31'}, reason='Clearing reminders')
    tick(books, '2026-02-01')
    assert bill_totals(books) == [999900]


def test_Z9_an_agent_cannot_delete_a_template_with_a_slot_held_for_a_person(books):
    admin = admin_id(books)
    _mallorys_held_bill(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    show = books.run('memorized show', {'memorized': 'Mallory tools'})
    with pytest.raises(BookflowError) as refused:
        run_as(books, 'helperlogin', 'memorized delete', {'memorized': 'Mallory tools', 'expected_version': show['version']},
               on_behalf_of=admin, reason='Agent tidies templates')
    assert refused.value.code == 'E_PERMISSION' and refused.value.details['reason'] == 'held_for_a_person'
    assert [r['template_name'] for r in waiting(books, 'Mallory tools')] == ['Mallory tools']
    books.run('memorized delete', {'memorized': 'Mallory tools', 'expected_version': show['version']},
              reason='The admin deletes it')
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM scheduled_holds').fetchone()[0] == 0


def test_Z12_a_persons_process_preview_and_run_say_which_entries_were_held_and_why(books):
    _mallorys_held_bill(books)
    preview = books.run('memorized process', {'as_of': '2026-01-31'}, reason='look', dry_run=True)
    [row] = [e for e in preview['entered'] if e['template_name'] == 'Mallory tools']
    assert row['waiting_reason'].startswith('memorized by Mallory, scheduled by ')
    assert any('held for a person' in w and 'Mallory tools' in w for w in preview['warnings'])
    done = books.run('memorized process', {'as_of': '2026-01-31'}, reason='enter')
    assert any('held for a person' in w and 'memorized by Mallory' in w for w in done['warnings'])


def test_Z13_slots_never_claimed_while_the_host_was_off_wait_after_an_agents_widening(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    admin = admin_id(books)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    local_agent(books, 'helper', 'helperlogin', admin)
    with sqlite3.connect(root / 'hub.db') as hub:   # an agent holding the admin role, as the reviewer's
        hub.execute("UPDATE memberships SET role = 'admin' WHERE user_id = (SELECT id FROM users WHERE username = 'helper')")
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    as_user(root, 'mallory').run('memorized create', {
        'name': 'Mallory tools', 'command': 'bill post', 'payload': tools('9999.00'),
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'}, company=books.company)
    books.clock.set('2026-04-01')                    # the host stayed off; time passed
    run_as(books, 'helperlogin', 'company update', {'scheduler_catch_up_days': 300}, on_behalf_of=admin, reason='widen')
    books.run('company update', {'scheduler_catch_up_days': 200}, reason='pull back to 200')
    out = tick(books, '2026-04-01')
    assert out['memorized']['held'] == 2, out
    dates_posted = [r['reason'] for r in audit_rows(books, 'bill post')]
    assert not any('2026-01-31' in r or '2026-02-28' in r for r in dates_posted)
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert len(due) == 2 and all('not by its author' in r['waiting_reason'] for r in due)


def test_Z10_an_agents_own_widening_keeps_its_slots_waiting_and_says_why(books):
    admin = admin_id(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    with sqlite3.connect(Path(books.root) / 'hub.db') as hub:
        hub.execute("UPDATE memberships SET role = 'admin' WHERE user_id = (SELECT id FROM users WHERE username = 'helper')")
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    run_as(books, 'helperlogin', 'memorized create', {'name': 'Agent rent', 'command': 'bill post',
           'payload': tools('8.00'), 'frequency': 'monthly', 'start_date': '2026-01-31',
           'mode': 'enter_automatically'}, on_behalf_of=admin, reason='Agent memorizes')
    tick(books, '2026-03-31')
    run_as(books, 'helperlogin', 'company update', {'scheduler_catch_up_days': 300}, on_behalf_of=admin,
           reason='Agent widens catch-up for its own template')
    tick(books, '2026-04-01')
    assert bill_totals(books) == [800]
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert len(due) == 2 and all('an agent widened the stored window' in r['waiting_reason']
                                 and 'not by its author' not in r['waiting_reason'] for r in due)


def test_the_fingerprint_is_documented_as_a_consistency_check():
    from pathlib import Path as P
    text = (P(__file__).resolve().parents[1] / 'src/bookflow/documentation/resources/concepts.md').read_text()
    assert 'not proof that anyone looked at a preview' in text


# ---------------------------------------------------------------- sixth review (Z8r, Z8d, Z9r, Z13b)

def test_Z8r_a_manual_entry_previewed_before_the_tick_entered_the_slot_is_refused(books):
    memorize(books, name='Rent', start_date='2026-01-31')
    body = {'memorized': 'Rent', 'date': '2026-01-31'}
    preview = books.run('memorized enter', body, reason='look', dry_run=True)
    tick(books, '2026-01-31')                        # the scheduler enters that slot meanwhile
    with pytest.raises(BookflowError) as stale:
        books.run('memorized enter', dict(body, expected_facts_fingerprint=preview['facts_fingerprint']), reason='save')
    assert stale.value.code == 'E_PREVIEW_STALE' and 'entered since the preview' in stale.value.message
    assert bill_totals(books) == [180000]


def test_Z8d_an_agent_enters_a_template_with_a_held_slot_on_no_date(books):
    admin = admin_id(books)
    _mallorys_held_bill(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    for day in ('2026-02-01', '2026-01-31'):
        with pytest.raises(BookflowError) as refused:
            run_as(books, 'helperlogin', 'memorized enter', {'memorized': 'Mallory tools', 'date': day},
                   on_behalf_of=admin, reason='Agent enters it another day')
        assert refused.value.code == 'E_PERMISSION' and refused.value.details['reason'] == 'held_for_a_person'
    assert bill_totals(books) == [] and [r['template_name'] for r in waiting(books, 'Mallory tools')] == ['Mallory tools']
    books.run('memorized enter', {'memorized': 'Mallory tools', 'date': '2026-02-01'}, reason='A person may')
    assert bill_totals(books) == [999900]


def test_Z9r_delete_and_re_create_posts_no_history_unattended(books):
    memorize(books, name='Rent', start_date='2026-01-31')
    tick(books, '2026-03-31')
    assert len(bill_totals(books)) == 3
    show = books.run('memorized show', {'memorized': 'Rent'})
    books.run('memorized delete', {'memorized': show['id'], 'expected_version': show['version']}, reason='Remove')
    with pytest.raises(BookflowError) as taken:
        memorize(books, name='Rent', start_date='2026-01-31')
    assert taken.value.code == 'E_NAME_TAKEN' and taken.value.details['field'] == 'name'
    made = memorize(books, name='Rent again', start_date='2026-01-31')
    assert any('3 slots dated today or earlier (from 2026-01-31) will wait for a person' in w for w in made['warnings'])
    out = tick(books, '2026-04-01')
    assert out['memorized']['entered'] == 0 and out['memorized']['held'] == 3, out
    assert len(bill_totals(books)) == 3
    due = [r for r in books.run('reminders', {'as_of': '2026-04-01'})['memorized_due'] if r['template_name'] == 'Rent again']
    assert len(due) == 3 and all(r['waiting_reason'].startswith('due before this schedule was set on 2026-03-31')
                                 for r in due)


def test_Z9r_a_new_automatic_template_with_a_past_start_waits_for_its_past_slots(books):
    books.clock.set('2026-03-15')
    made = memorize(books, name='Late rent', start_date='2026-01-31')
    assert any('2 slots dated today or earlier' in w for w in made['warnings'])
    out = tick(books, '2026-03-31')
    assert out['memorized']['entered'] == 1 and out['memorized']['held'] == 2, out


def test_Z13b_window_changes_count_in_the_order_made_whatever_their_clock_says(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    admin = admin_id(books)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    local_agent(books, 'helper', 'helperlogin', admin)
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute("UPDATE memberships SET role = 'admin' WHERE user_id = (SELECT id FROM users WHERE username = 'helper')")
    as_user(root, 'mallory').run('memorized create', {
        'name': 'Mallory tools', 'command': 'bill post', 'payload': tools('9999.00'),
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'}, company=books.company)
    books.clock.set('2026-04-01')
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    books.clock.set('2026-01-20')                   # a skewed clock writes an earlier `at` on the next change
    run_as(books, 'helperlogin', 'company update', {'scheduler_catch_up_days': 300}, on_behalf_of=admin, reason='widen')
    out = tick(books, '2026-04-01')
    assert out['memorized']['entered'] == 1, out          # only 03-31; an agent's widening reaches no further back
    assert not any('2026-01-31' in r['reason'] or '2026-02-28' in r['reason'] for r in audit_rows(books, 'bill post'))


# ---------------------------------------------------------------- seventh review

def test_7_1_resuming_a_paused_template_leaves_the_paused_months_waiting(books):
    books.clock.set('2026-01-15')
    memorize(books, name='Rent')
    tick(books, '2026-01-31')
    books.clock.set('2026-02-10')
    show = books.run('memorized show', {'memorized': 'Rent'})
    books.run('memorized update', {'memorized': 'Rent', 'expected_version': show['version'], 'status': 'paused'},
              reason='Pause while we renegotiate')
    tick(books, '2026-05-31')
    books.clock.set('2026-06-10')
    show = books.run('memorized show', {'memorized': 'Rent'})
    resumed = books.run('memorized update', {'memorized': 'Rent', 'expected_version': show['version'], 'status': 'active'},
                        reason='Resume')
    assert any('4 slots dated today or earlier (from 2026-02-28)' in w for w in resumed['warnings']), resumed['warnings']
    out = tick(books, '2026-06-10')
    assert out['memorized']['entered'] == 0 and out['memorized']['held'] == 4, out
    assert bill_totals(books) == [180000]
    due = [r for r in books.run('reminders', {'as_of': '2026-06-10'})['memorized_due'] if r['template_name'] == 'Rent']
    assert [r['slot_date'] for r in due] == ['2026-02-28', '2026-03-31', '2026-04-30', '2026-05-31']
    assert all(r['waiting_reason'].startswith('due before this schedule was set on 2026-06-10') for r in due)


def test_7_2_entering_a_date_whose_slot_already_posted_needs_again(books):
    memorize(books, name='Rent')
    tick(books, '2026-01-31')
    for who in ('person', 'agent'):
        body = {'memorized': 'Rent', 'date': '2026-01-31'}
        with pytest.raises(BookflowError) as refused:
            if who == 'person':
                books.run('memorized enter', body, reason='by hand')
            else:
                admin = admin_id(books)
                if not getattr(books, 'agent_made', False):
                    local_agent(books, 'helper', 'helperlogin', admin)
                    books.agent_made = True
                run_as(books, 'helperlogin', 'memorized enter', body, on_behalf_of=admin, reason='agent by hand')
        assert refused.value.code == 'E_VALIDATION' and 'was already entered as' in refused.value.message, who
    assert bill_totals(books) == [180000]
    books.run('memorized enter', {'memorized': 'Rent', 'date': '2026-01-31', 'again': True}, reason='A second, on purpose')
    assert bill_totals(books) == [180000, 180000]


def test_7_3_a_widening_stamped_before_an_earlier_audited_event_counts_from_that_event(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    make_actor(root, 'mallory', company_role=(books.company, 'standard'))
    make_actor(root, 'olga', company_role=(books.company, 'admin'))
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    as_user(root, 'mallory').run('memorized create', {
        'name': 'Mallory tools', 'command': 'bill post', 'payload': tools('9999.00'),
        'frequency': 'monthly', 'start_date': '2026-01-31', 'mode': 'enter_automatically'}, company=books.company)
    books.clock.set('2026-02-15')
    books.run('bill post', dict(rent_payload(), date='2026-02-15'), reason='ordinary activity on 02-15')
    books.clock.set('2026-01-15')                   # a person's widening, stamped by a clock reading 01-15
    as_user(root, 'olga').run('company update', {'scheduler_catch_up_days': 300}, company=books.company)
    books.clock.set('2026-02-20')
    tick(books, '2026-04-01')
    # 01-31 was past the 10 days by 02-15, the latest audited moment before the widening: it waits.
    late = [r for r in audit_rows(books, 'bill post') if 'Mallory tools' in (r['reason'] or '') and '2026-01-31' in r['reason']]
    assert late == []
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert any('2026-01-31' == r['slot_date'] and 'not by its author' in (r['waiting_reason'] or '') for r in due)


def test_7_4_an_agents_widening_never_extends_unattended_reach(books):
    from tests.conftest import as_user, make_actor
    root = Path(books.root)
    admin = admin_id(books)
    local_agent(books, 'helper', 'helperlogin', admin)
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute("UPDATE memberships SET role = 'admin' WHERE user_id = (SELECT id FROM users WHERE username = 'helper')")
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books, name='Rent')                    # the admin's own template
    books.clock.set('2026-04-01')
    run_as(books, 'helperlogin', 'company update', {'scheduler_catch_up_days': 300}, on_behalf_of=admin, reason='widen')
    out = tick(books, '2026-04-01')
    assert out['memorized']['entered'] == 1 and out['memorized']['left_for_a_person'] == 2, out   # 03-31 only
    due = books.run('reminders', {'as_of': '2026-04-01'})['memorized_due']
    assert len(due) == 2 and all('an agent widened the stored window' in r['waiting_reason'] for r in due)


def test_7_5_the_concepts_page_says_todays_slot_waits_unlike_the_anchor():
    from pathlib import Path as P
    text = (P(__file__).resolve().parents[1] / 'src/bookflow/documentation/resources/concepts.md').read_text()
    assert 'The anchor would enter it; Bookflow deliberately does not, for safety' in text
