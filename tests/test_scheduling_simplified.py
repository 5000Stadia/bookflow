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
    assert all('catch-up widened to 300 days by Olga' in r['waiting_reason'] for r in due) and len(due) == 2


def test_X5_a_widening_by_the_author_lets_them_in_and_names_it_whatever_its_size(books):
    books.run('company update', {'scheduler_catch_up_days': 10}, reason='ten days')
    memorize(books)
    tick(books, '2026-03-31')
    books.run('company update', {'scheduler_catch_up_days': 300}, reason='Admin widens catch-up')
    assert tick(books, '2026-04-01')['memorized']['entered'] == 2
    late = [r for r in audit_rows(books, 'bill post') if '2026-01-31' in r['reason'] or '2026-02-28' in r['reason']]
    assert len(late) == 2 and all('catch-up widened to 300 days by' in r['reason'] for r in late)


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
