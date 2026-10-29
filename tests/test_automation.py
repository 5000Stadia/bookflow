"""R187 increment 2: establishing an automation is a capability (design/principal-directives.md §3, §7).

Two witnesses of the same rule. A fake timed automation (a test-only kind, its own table, firing on the host's tick)
drafted by an agent runs nothing; it goes live once an agent its principal granted automate.invoice makes it so, and
its write (a customer) cites the rule and the establisher and lands in a batch one reversal undoes; it pauses with
one to-do when the grant is withdrawn or the establisher is deactivated; an operative edit by a non-holder returns it
to draft and one by a holder keeps it live under the editor. A real delivery schedule shows the same through the
commands: an agent drafts it and cannot make it live, a person holding automate.delivery makes it live through
`delivery-schedule update --mode send --confirm`, its run's audit names the rule, an agent's edit returns it to draft,
`automation pause` stops it with a to-do, and an establisher who loses the role pauses it at the next firing.
"""
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from tests.conftest import hosted_call, make_agent
from tests.test_row3_host import hosted  # noqa: F401 - fixture
from tests.test_people_only import REASON, _agent


def _users(hosted):
    return {u['username']: u['user_id'] for u in hosted.ok('user.list', {})['items']}


def _grant(hosted, user, role, grants=(), denies=()):
    import sqlite3
    with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT version FROM memberships WHERE user_id=? AND scope_type=? AND scope_id=?',
                         (user, 'company', hosted.company_id)).fetchone()
    return hosted.ok('membership.grant', {'user': user, 'company': hosted.company_id, 'role': role,
                                          'grants': list(grants), 'denies': list(denies),
                                          'expected_version': row[0] if row else 0}, headers=REASON)


def _todos(hosted, text):
    items = hosted.ok('todo.list', {'status': 'open'}, company=hosted.company_id, headers=hosted.bearer)['items']
    return [t for t in items if text in t['title']]


# ---------------------------------------------------------------- a fake automation, on the host's tick

_meta = sa.MetaData()


def _fixture_table():
    from bookflow.company import automation
    return sa.Table('automation_fixture_rules', _meta, sa.Column('id', sa.String(26), primary_key=True),
                    sa.Column('version', sa.Integer, nullable=False), sa.Column('name', sa.String(100)),
                    sa.Column('customer', sa.String(100)), sa.Column('updated_at', sa.String(32)),
                    sa.Column('updated_by', sa.String(26)), sa.Column('updated_via', sa.String(16)),
                    *automation.columns())


FIXTURE = _fixture_table()


def _on_writer(hosted, user_id, fn, principal=None):
    """Run ``fn(s)`` on the host's writer as ``user_id`` (an agent with ``principal``), the company open."""
    host = hosted.handle.host

    def job():
        from bookflow.company import scheduler
        from bookflow.core.dispatch import open_company
        from bookflow.hub import schema as h
        from bookflow.hub import credentials
        s = host._writer_session()
        try:
            epoch = credentials._binding(s.hub, user_id, principal)[1] if principal else None
            scheduler._act_as(s, user_id, principal, epoch)
            s.company_row = dict(s.hub.conn.execute(sa.select(h.companies).where(
                h.companies.c.id == hosted.company_id)).mappings().first())
            open_company(s, scheduler.context(), True)
            return fn(s)
        finally:
            s.close_company(release=False)
    return host.submit(job, _maintenance=True)


def _tx(s, fn):
    from bookflow.company import automation
    return automation._in_tx(s, 'automation.pause', lambda: fn(s))


def _row(s, name):
    return dict(s.company.conn.execute(sa.select(FIXTURE).where(FIXTURE.c.name == name)).mappings().first())


def _write(s, before, **values):
    after = dict(before, **values)
    after['version'] = before['version'] + 1
    s.company.conn.execute(FIXTURE.update().where(FIXTURE.c.id == before['id']).values(
        **{k: v for k, v in after.items() if k != 'id'}))
    return after


def _customers(hosted, prefix):
    rows = hosted.ok('customer.list', {'query': prefix}, company=hosted.company_id, headers=hosted.bearer)['items']
    return sorted(r['name'] for r in rows if r['name'].startswith(prefix))


@pytest.fixture
def fixture_kind(monkeypatch):
    from bookflow.company import automation
    fired = []

    def act(s, ctx, row, batch_id):
        name = f"{row['customer']} {len(fired) + 1}"
        automation.effect(s, ctx, batch_id, 'customer create', {'name': name})
        fired.append((name, batch_id))

    kind = automation.table_kind(
        kind='fixture', capability='automate.invoice', noun='fixture rule', record_type='fixture_rule',
        table=FIXTURE, show_command='customer show', edit_command='customer update', live_command='fixture live',
        what=lambda row: f"adds a customer named {row['customer']}",
        due=lambda s, now: [dict(r) for r in s.company.conn.execute(
            sa.select(FIXTURE).where(FIXTURE.c.state == 'live')).mappings()],
        act=act)
    monkeypatch.setitem(automation.KINDS, 'fixture', kind)
    return dict(kind=kind, fired=fired)


@pytest.mark.timeout(900)
def test_a_fake_automation_runs_only_while_live_under_a_holder_and_cites_its_rule(hosted, fixture_kind):  # noqa: F811
    from bookflow import BookflowError
    from bookflow.company import automation
    from bookflow.core.ids import new_id
    cid, host = hosted.company_id, hosted.handle.host
    kind, fired = fixture_kind['kind'], fixture_kind['fired']
    agent, as_agent = _agent(hosted, role='standard')
    p = _users(hosted)['principal-p']
    tick = lambda day: host.schedule_now(now=datetime(2026, 6, day, 12, tzinfo=timezone.utc))[cid]  # noqa: E731

    # The agent drafts the rule (the ordinary right to write it): state draft, and nothing runs.
    def draft(s):
        FIXTURE.create(s.company.conn, checkfirst=True)
        s.company.conn.execute(FIXTURE.insert().values(id=new_id(), version=1, name='Weekly sign-ups',
                                                       customer='Fixture Signup', state='draft'))
    _on_writer(hosted, agent, lambda s: _tx(s, draft), principal=p)
    assert 'automations' not in tick(1) and fired == [] and _customers(hosted, 'Fixture Signup') == []

    # Making it live needs automate.invoice: the agent is refused, naming it and its principal.
    def establish(s):
        automation.require_establish(s, 'automate.invoice', 'fixture live')
        _write(s, _row(s, 'Weekly sign-ups'), **automation.establisher(s))
    with pytest.raises(BookflowError) as refused:
        _on_writer(hosted, agent, lambda s: _tx(s, establish), principal=p)
    assert refused.value.code == 'E_PERMISSION' and refused.value.details['capability'] == 'automate.invoice'
    assert refused.value.details['reason'] == 'capability_not_granted' and 'principal' in refused.value.message

    # Once its principal grants it, the agent makes it live; the next firing writes as the agent, citing the rule.
    _grant(hosted, agent, 'standard', grants=['automate.invoice'])
    _on_writer(hosted, agent, lambda s: _tx(s, establish), principal=p)
    shown = hosted.ok('automation.show', {'kind': 'fixture', 'rule': 'Weekly sign-ups'}, company=cid,
                      headers=hosted.bearer)
    assert shown['state'] == 'live' and shown['established_by'] == agent and shown['established_for'] == p
    assert shown['capability'] == 'automate.invoice' and shown['would_pause'] is None
    done = tick(2)['automations']['fixture:Weekly sign-ups']
    assert _customers(hosted, 'Fixture Signup') == ['Fixture Signup 1'] and done['batch'] == fired[0][1]
    events = hosted.ok('audit.list', {'limit': 20}, company=cid, headers=hosted.bearer)['items']
    made = next(e for e in events if e['command'] == 'customer create' and e['on_rule'])
    assert made['actor_id'] == agent and made['on_behalf_of'] == p and made['reason'] == 'On rule: Weekly sign-ups'
    assert made['on_rule']['name'] == 'Weekly sign-ups' and made['on_rule']['established_by'] == agent
    assert made['on_rule']['text'] == 'On rule: Weekly sign-ups, established by rules-agent'
    # Its effect is in the rule's batch, and one reversal undoes it.
    from bookflow.company import batches

    def reverse(s):
        assert [e['record_type'] for e in batches.effects(s, done['batch'])] == ['customer']
        from bookflow.company import scheduler
        ctx = scheduler.context().model_copy(update={'actor_id': s.actor.id, 'reason': 'Misread rule'})
        return batches.reverse(s, ctx, done['batch'], reason='Misread rule')
    _on_writer(hosted, p, reverse)
    assert _customers(hosted, 'Fixture Signup') == []

    # The grant withdrawn: the next firing pauses it with the reason and one to-do, and writes nothing.
    _grant(hosted, agent, 'standard')
    assert 'automate.invoice' in hosted.ok('automation.show', {'kind': 'fixture', 'rule': 'Weekly sign-ups'},
                                           company=cid, headers=hosted.bearer)['would_pause']
    paused = tick(3)['automations']['fixture:Weekly sign-ups']['paused']
    assert 'no longer granted automate.invoice' in paused and len(fired) == 1
    assert len(_todos(hosted, 'Automation Weekly sign-ups is paused')) == 1
    assert 'automations' not in tick(4) and len(_todos(hosted, 'Automation Weekly sign-ups is paused')) == 1
    listed = hosted.ok('automation.list', {'kind': 'fixture'}, company=cid, headers=hosted.bearer)['rows']
    assert [(r['name'], r['state']) for r in listed] == [('Weekly sign-ups', 'paused')]

    # A person (principal-p, an owner: a holder) makes it live again; an agent's operative edit returns it to draft.
    _on_writer(hosted, p, lambda s: _tx(s, establish))

    def edit(s):
        before = _row(s, 'Weekly sign-ups')
        after, warnings = automation.on_edit(s, 'automate.invoice', before, dict(before, customer='Fixture Other'),
                                             operative=True)
        _write(s, before, **after)
        return warnings
    warned = _on_writer(hosted, agent, lambda s: _tx(s, edit), principal=p)
    shown = hosted.ok('automation.show', {'kind': 'fixture', 'rule': 'Weekly sign-ups'}, company=cid,
                      headers=hosted.bearer)
    assert shown['state'] == 'draft' and shown['established_by'] is None and 'rules-agent' in shown['draft_reason']
    assert 'returns it to draft' in warned[0] and 'automations' not in tick(5)
    # A holder's edit keeps a live rule live, under the editor.
    _on_writer(hosted, p, lambda s: _tx(s, establish))
    _grant(hosted, agent, 'standard', grants=['automate.invoice'])
    _on_writer(hosted, agent, lambda s: _tx(s, edit), principal=p)
    shown = hosted.ok('automation.show', {'kind': 'fixture', 'rule': 'Weekly sign-ups'}, company=cid,
                      headers=hosted.bearer)
    assert shown['state'] == 'live' and shown['established_by'] == agent

    # Pausing is open to any editor of the rule; the establisher's agent is deactivated: paused at the next firing.
    out = hosted.ok('automation.pause', {'kind': 'fixture', 'rule': 'Weekly sign-ups'}, company=cid,
                    headers={**hosted.bearer, **REASON})
    assert out['state'] == 'paused' and out['paused_reason'].startswith('paused by')
    _on_writer(hosted, agent, lambda s: _tx(s, establish), principal=p)
    hosted.ok('agent.deactivate', {'agent': agent}, headers=REASON)
    paused = tick(6)['automations']['fixture:Weekly sign-ups']['paused']
    assert 'deactivated' in paused and len(fired) == 1


# ---------------------------------------------------------------- a real delivery schedule

def _clerk(hosted, name, role='standard'):
    hosted.ok('user.add', {'username': name, 'company': hosted.company_id, 'role': role,
                           'password': f'pw-{name}-12345'}, headers=REASON)
    secret = hosted.ok('token.issue', {'user': name, 'label': name}, headers=REASON)['secret']
    return {'Authorization': 'Bearer ' + secret, **REASON}


@pytest.mark.timeout(900)
def test_a_delivery_schedule_is_an_automation_end_to_end(hosted):  # noqa: F811
    from tests.test_delivery_schedules import _host_books
    cid, host = hosted.company_id, hosted.handle.host
    _host_books(hosted)
    person = {**hosted.bearer, **REASON}
    _, as_agent = _agent(hosted, role='standard')
    as_clerk = _clerk(hosted, 'clerk')
    as_temp = _clerk(hosted, 'temp')
    users = _users(hosted)
    tick = lambda day: host.schedule_now(now=datetime(2026, 6, day, 12, tzinfo=timezone.utc))[cid]  # noqa: E731
    name = {'schedule': 'Daily invoices'}

    # The agent drafts a send schedule: a draft, and it cannot make it live through update either.
    made = hosted.ok('delivery-schedule.create', {'name': 'Daily invoices', 'kind': 'invoice', 'unsent': True,
                                                  'frequency': 'daily', 'start_date': '2026-06-01', 'mode': 'send'},
                     company=cid, headers=as_agent)
    assert made['state'] == 'draft'
    r = hosted.call('delivery-schedule.update', {**name, 'mode': 'send', 'confirm': 'x'}, company=cid,
                    headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['capability'] == 'automate.delivery'
    listed = hosted.ok('automation.list', {}, company=cid, headers=hosted.bearer)['rows']
    assert [(x['kind'], x['name'], x['state']) for x in listed] == [('delivery', 'Daily invoices', 'draft')]
    # A draft's run leaves every message waiting for a go: nothing goes out on its say.
    ran = tick(1)['delivery_schedules']['Daily invoices']['ran']
    assert ran[-1]['state'] == 'done' and not hosted.ok('delivery-schedule.show', name, company=cid,
                                                        headers=hosted.bearer)['runs'][0]['go']

    # A person denied automate.delivery is refused, naming it; the clerk (standard: a holder) makes it live by update.
    denied = _grant(hosted, users['temp'], 'standard', denies=['automate.delivery'])
    assert 'binds temp too' in denied['message'] and 'binds only' not in denied['message']
    r = hosted.call('delivery-schedule.update?dry_run=true', {**name, 'mode': 'send'}, company=cid, headers=as_temp)
    assert r.status_code == 200 and r.json()['check'] is None
    r = hosted.call('delivery-schedule.update', {**name, 'mode': 'send', 'confirm': 'x'}, company=cid,
                    headers=as_temp)
    body = r.json()
    assert r.status_code == 403 and body['details']['capability'] == 'automate.delivery', r.text
    assert body['details']['reason'] == 'capability_not_held' and 'denied' in body['message']
    check = hosted.ok('delivery-schedule.update?dry_run=true', {**name, 'mode': 'send'}, company=cid,
                      headers=as_clerk)['check']
    live = hosted.ok('delivery-schedule.update', {**name, 'mode': 'send', 'confirm': check['fingerprint']},
                     company=cid, headers=as_clerk)
    assert live['state'] == 'live' and live['established_by'] == users['clerk']
    # Its next run carries the clerk's go and its audit cites the rule and the establisher.
    hosted.ok('invoice.post', {'number': 'A-1', 'date': '2026-06-02', 'customer': 'Harbour Mills',
                               'lines': [{'item': 'Site labour', 'quantity': '1'}]}, company=cid, headers=person)
    tick(2)
    assert hosted.ok('delivery-schedule.show', name, company=cid, headers=hosted.bearer)['runs'][0]['go']
    events = hosted.ok('audit.list', {'limit': 30}, company=cid, headers=hosted.bearer)['items']
    cited = [e for e in events if e['command'] == 'delivery-schedule run' and e['on_rule']]
    assert cited and cited[0]['on_rule']['text'] == 'On rule: Daily invoices, established by clerk'
    assert cited[0]['on_rule']['kind'] == 'delivery' and cited[0]['actor_id'] == users['clerk']

    # The agent's edit to what it sends returns it to draft; the clerk makes it live again.
    edited = hosted.ok('delivery-schedule.update', {**name, 'combine': True}, company=cid, headers=as_agent)
    assert edited['state'] == 'draft' and edited['established_by'] is None
    check = hosted.ok('delivery-schedule.live?dry_run=true', name, company=cid, headers=as_clerk)['check']
    hosted.ok('delivery-schedule.live', {**name, 'confirm': check['fingerprint']}, company=cid, headers=as_clerk)

    # Anyone who may edit it pauses it (the temp, who may not make it live); the clerk gets one to-do.
    paused = hosted.ok('automation.pause', {'kind': 'delivery', 'rule': 'Daily invoices'}, company=cid,
                       headers=as_temp)
    assert paused['state'] == 'paused' and paused['paused_reason'].startswith('paused by temp')
    assert len(_todos(hosted, 'Email schedule Daily invoices is paused')) == 1
    again = hosted.ok('automation.pause', {'kind': 'delivery', 'rule': 'Daily invoices'}, company=cid,
                      headers=as_temp)
    assert again['warnings'] == ['It is already paused.']

    # Live again under the clerk; the clerk loses the role: the next firing pauses it with the reason and a to-do.
    check = hosted.ok('delivery-schedule.live?dry_run=true', name, company=cid, headers=as_clerk)['check']
    hosted.ok('delivery-schedule.live', {**name, 'confirm': check['fingerprint']}, company=cid, headers=as_clerk)
    _grant(hosted, users['clerk'], 'standard', denies=['automate.delivery'])
    shown = hosted.ok('automation.show', {'kind': 'delivery', 'rule': 'Daily invoices'}, company=cid,
                      headers=hosted.bearer)
    assert 'denied automate.delivery' in shown['would_pause']
    tick(3)
    shown = hosted.ok('delivery-schedule.show', name, company=cid, headers=hosted.bearer)
    assert shown['state'] == 'paused' and 'denied automate.delivery' in shown['paused_reason']
    assert not shown['runs'][0]['go'] and len(_todos(hosted, 'Email schedule Daily invoices is paused')) == 2
