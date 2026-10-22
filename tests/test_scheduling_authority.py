"""R111 review findings, each as the behaviour that now holds: whose authority a scheduled entry
carries and how it is recorded (agents, legacy roots), blocked slots, the Reminders list's edges,
the writer's fair share, and the hardening of what may be replayed.

Agents here act offline through a mapped OS login bound to the person they act for, exactly as
their direct posts do; the scheduler runs through `Host.schedule_now` under an injected clock.
"""
import logging
import sqlite3
import threading
from pathlib import Path

import pytest

from bookflow.core.errors import BookflowError
from tests.test_memorized_transactions import dates, memorize, rent_payload
from tests.test_scheduling import (  # noqa: F401 - fixture
    admin_id, at, books, company_db, host, occurrences, tick, todo_for)


def audit_rows(books, command):
    with company_db(books) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            'SELECT actor_id, actor_kind, on_behalf_of, client_name, interface, reason FROM audit_events '
            'WHERE command = ? ORDER BY seq', (command,))]


def run_as(books, login, name, body, *, on_behalf_of=None, reason=None):
    from bookflow.core import dispatch, registry
    from bookflow.core.context import Context, Interface
    cmd = registry.get(name)
    ctx = Context.new(Interface.python, 'direct', reason=reason, on_behalf_of=on_behalf_of)
    return dispatch.run(cmd, body, ctx, data_root=str(books.root),
                        company_selector=books.company if cmd.scope == 'company' else None, _login=login)


def map_login(books, login, user_id):
    from bookflow.core.config import Config
    cfg = Config.load(Path(books.root) / 'config.toml')
    cfg.set_user(login, user_id)
    cfg.save()


def local_agent(books, username, login, principal):
    c = books.client
    agent = c.run('agent create', {'username': username})['agent']['agent_id']
    c.run('agent assign', {'agent': agent, 'principal': principal, 'confirm_permitted_use': True})
    c.run('membership grant', {'user': agent, 'company': books.company, 'role': 'standard'})
    c.run('agent authorize', {'agent': agent, 'confirm_permitted_use': True})
    map_login(books, login, agent)
    return agent


def agent_rent(books, login, principal, name='Agent rent'):
    run_as(books, login, 'memorized create',
           {'name': name, 'command': 'bill post', 'payload': rent_payload(), 'frequency': 'monthly',
            'start_date': '2026-01-31', 'mode': 'enter_automatically'},
           on_behalf_of=principal, reason='Agent memorizes rent')


def test_E_offline_an_agents_entry_is_its_own_write_as_its_direct_post_is(books):
    admin = admin_id(books)
    agent = local_agent(books, 'helper', 'helperlogin', admin)
    run_as(books, 'helperlogin', 'bill post', dict(rent_payload(), date='2026-01-15'), on_behalf_of=admin,
           reason='Agent posts directly')
    agent_rent(books, 'helperlogin', admin)
    assert tick(books, '2026-01-31')['memorized']['entered'] == 1
    direct, scheduled = audit_rows(books, 'bill post')
    assert (scheduled['actor_id'], scheduled['actor_kind'], scheduled['on_behalf_of']) == \
        (direct['actor_id'], direct['actor_kind'], direct['on_behalf_of']) == (agent, 'agent', admin)
    assert (scheduled['interface'], scheduled['client_name']) == ('system', 'scheduler')
    listed = books.run('audit list', {'actor': 'helper', 'command': 'bill post', 'limit': 10})['items']
    assert len(listed) == 2


def test_E2_an_agent_acts_for_its_assigned_person_not_its_record_owner(books):
    from tests.conftest import make_actor
    paula = make_actor(Path(books.root), 'paula', company_role=(books.company, 'owner'))
    agent = local_agent(books, 'helper', 'helperlogin', 'paula')   # its record owner is the admin who made it
    agent_rent(books, 'helperlogin', paula)
    out = tick(books, '2026-01-31')
    assert out['memorized']['entered'] == 1, out
    [row] = audit_rows(books, 'bill post')
    assert (row['actor_id'], row['on_behalf_of']) == (agent, paula)


def _suspended(books, legacy):
    from tests.conftest import make_legacy
    admin = admin_id(books)
    agent = local_agent(books, 'helper', 'helperlogin', admin)
    agent_rent(books, 'helperlogin', admin)
    if legacy:
        make_legacy(Path(books.root))
    with sqlite3.connect(Path(books.root) / 'hub.db') as hub:
        hub.execute("UPDATE agent_authority SET suspended_at = '2026-01-01T00:00:00.000Z' WHERE agent_user_id = ?", (agent,))
    out = tick(books, '2026-01-31')
    assert out['memorized']['entered'] == 0 and out['memorized']['blocked'] == 1, out
    assert dates(books) == []
    [occurrence] = occurrences(books, 'Agent rent')
    assert occurrence['error_code'] == 'E_PERMISSION' and 'suspended' in occurrence['error_message']
    assert todo_for(books, occurrence['id'])['assignee_id'] == admin != agent


def test_P_a_suspended_agents_template_is_blocked_on_a_legacy_root(books):
    _suspended(books, legacy=True)


def test_P_a_suspended_agents_template_is_blocked_under_the_current_policy(books):
    _suspended(books, legacy=False)


def test_P_an_agent_whose_authority_changed_since_it_saved_is_blocked(books):
    admin = admin_id(books)
    agent = local_agent(books, 'helper', 'helperlogin', admin)
    agent_rent(books, 'helperlogin', admin)
    with sqlite3.connect(Path(books.root) / 'hub.db') as hub:
        hub.execute('UPDATE agent_authority SET epoch = epoch + 1 WHERE agent_user_id = ?', (agent,))
    out = tick(books, '2026-01-31')
    assert out['memorized']['blocked'] == 1 and dates(books) == [], out
    assert 'different authority' in occurrences(books, 'Agent rent')[0]['error_message']


def test_Q_a_slot_blocked_in_a_persons_group_run_is_not_retried_by_the_scheduler(books):
    books.run('memorized-group create', {'name': 'Month end', 'frequency': 'monthly', 'start_date': '2026-01-31',
                                         'mode': 'enter_automatically'}, reason='month end')
    books.run('memorized create', {'name': 'Tools', 'command': 'bill post',
                                   'payload': rent_payload(account='Tool Hire', vendor='Tool Depot', amount='4.00'),
                                   'group': 'Month end', 'group_ordinal': 1}, reason='member')
    books.run('company update', {'closing_date': '2026-01-31'}, reason='Close January')
    manual = books.run('memorized-group enter', {'memorized_group': 'Month end', 'date': '2026-01-31'}, reason='By hand')
    assert manual['blocked_count'] == 1
    books.run('company update', {'closing_date': None}, reason='Reopen January')
    out = tick(books, '2026-02-01')
    assert out['memorized']['entered'] == 0, out
    [occurrence] = occurrences(books, 'Tools')
    assert (occurrence['status'], occurrence['attempt_count']) == ('blocked', 1)
    assert books.run('memorized retry', {'occurrence': occurrence['id'], 'expected_version': occurrence['version']},
                     reason='A person retries')['occurrence']['status'] == 'entered'


def test_O_reminders_show_remind_groups_drop_deleted_slots_and_list_automatic_ones_no_host_entered(books):
    books.run('memorized-group create', {'name': 'Remind trio', 'frequency': 'monthly', 'start_date': '2026-01-31',
                                         'mode': 'remind'}, reason='remind group')
    books.run('memorized create', {'name': 'Grouped', 'command': 'bill post', 'payload': rent_payload(amount='5.00'),
                                   'group': 'Remind trio', 'group_ordinal': 1}, reason='member')
    before = books.run('reminders', {'as_of': '2026-02-01'})['memorized_due']
    assert [(r['template_name'], r['slot_date']) for r in before] == [('Grouped', '2026-01-31')]
    memorize(books, name='Old remind', mode='remind')
    tick(books, '2026-02-01')
    show = books.run('memorized show', {'memorized': 'Old remind'})
    books.run('memorized delete', {'memorized': show['id'], 'expected_version': show['version']}, reason='gone')
    gone = books.run('reminders', {'as_of': '2026-02-01'})['memorized_due']
    assert 'Old remind' not in [r['template_name'] for r in gone]
    memorize(books, name='Auto rent')               # automatic, due 2026-01-31, and no host has run since
    auto = books.run('reminders', {'as_of': '2026-02-05'})['memorized_due']
    assert ('Auto rent', '2026-01-31', None) in [(r['template_name'], r['slot_date'], r['occurrence_id']) for r in auto]


def test_N_a_catch_up_tick_takes_a_small_batch_and_lets_others_write(books):
    import time
    books.run('memorized create', {'name': 'Daily sales', 'command': 'bill post',
                                   'payload': rent_payload(account='Tool Hire', vendor='Tool Depot', amount='3.00'),
                                   'frequency': 'daily', 'start_date': '2026-01-01', 'mode': 'enter_automatically'},
              reason='daily')
    with host(books) as running:
        started = time.monotonic()
        out = running.schedule_now(now=at('2026-08-01'))[books.company]
        held = time.monotonic() - started
        again = running.schedule_now(now=at('2026-08-01').replace(minute=1))[books.company]
    assert out['memorized']['entered'] == 25 and again['memorized']['entered'] == 25, (out, again)
    assert again['notices'] == {'checked': 'within the hour'}
    assert held < 6, held


def test_a_group_members_reason_uses_the_groups_frequency(books):
    books.run('memorized-group create', {'name': 'Weekly run', 'frequency': 'weekly', 'start_date': '2026-01-05',
                                         'mode': 'enter_automatically'}, reason='group')
    books.run('memorized create', {'name': 'Cleaner', 'command': 'bill post', 'payload': rent_payload(amount='9.00'),
                                   'group': 'Weekly run', 'group_ordinal': 1}, reason='member')
    tick(books, '2026-01-05')
    [row] = audit_rows(books, 'bill post')
    assert row['reason'] == 'On schedule: Cleaner (weekly) for 2026-01-05'


def test_a_company_behind_head_is_logged_not_silently_skipped(books, caplog):
    folder = Path(books.run('company show', {})['path'])
    with sqlite3.connect(Path(books.root) / 'hub.db') as hub:
        hub.execute("UPDATE companies SET schema_revision = 'co0062' WHERE id = ?", (books.company,))
    with caplog.at_level(logging.WARNING, logger='bookflow.host'):
        with host(books) as running:
            out = running.schedule_now(now=at('2026-01-31'))
    assert out[books.company]['skipped'] == 'schema_behind'
    assert any(books.company in record.getMessage() and 'upgrade' in record.getMessage() for record in caplog.records)
    assert folder.exists()


def test_with_no_catch_up_set_a_start_more_than_a_year_back_waits_for_a_person(books):
    memorize(books, start_date='2024-01-31')
    out = tick(books, '2026-01-31')
    assert dates(books)[0] >= '2025-01-31' and '2024-01-31' not in dates(books), out
    assert out['memorized']['left_for_a_person'] > 0


def test_a_scheduled_binding_is_refused_off_the_hosts_writer_thread():
    from bookflow.core.identity_admin_binding import ScheduledBinding
    seen = []
    impostor = threading.Thread(target=lambda: seen.append(_try(ScheduledBinding)), name='bookflow-writer')
    impostor.start()
    impostor.join()
    assert seen == ['refused']


def _try(binding):
    try:
        binding('01ZZZZZZZZZZZZZZZZZZZZZZZZ')
    except Exception:  # noqa: BLE001 - the refusal is the point
        return 'refused'
    return 'built'


def test_a_people_only_command_cannot_be_memorized_or_entered(books, monkeypatch):
    from bookflow.company import memorized_references as edges
    from bookflow.core import registry
    from bookflow.hub import people_only
    registry.load_all()
    assert edges.enterable(registry.get('bill post'))
    monkeypatch.setattr(people_only, 'people_only_commands', lambda: frozenset({'bill post'}))
    assert not edges.enterable(registry.get('bill post'))
    with pytest.raises(BookflowError):
        memorize(books)
    monkeypatch.undo()
    memorize(books)
    monkeypatch.setattr(people_only, 'people_only_commands', lambda: frozenset({'bill post'}))
    out = books.run('memorized process', {'as_of': '2026-01-31'}, reason='Run')
    assert out['blocked_count'] == 1 and out['blocked'][0]['error_code'] == 'E_VALIDATION'
