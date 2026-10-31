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
    fired, failing = [], []

    def act(s, ctx, row, firing):
        if failing:
            raise ValueError('the fixture is told to fail')
        name = f"{row['customer']} {len(fired) + 1}"
        automation.effect(s, ctx, firing, 'customer create', {'name': name})
        fired.append((name, firing.batch_id))

    # Every rule is due every day, in any state: what is not live is refused by `fire` itself.
    kind = automation.table_kind(
        kind='fixture', capability='automate.invoice', noun='fixture rule', record_type='fixture_rule',
        table=FIXTURE, show_command='customer show', edit_command='customer update', live_command='fixture live',
        what=lambda row: f"adds a customer named {row['customer']}",
        due=lambda s, now: [(dict(r), now.date().isoformat()) for r in s.company.conn.execute(
            sa.select(FIXTURE)).mappings()],
        act=act)
    monkeypatch.setitem(automation.KINDS, 'fixture', kind)
    return dict(kind=kind, fired=fired, failing=failing)


def _batch_count(hosted):
    from bookflow.company import schema as c
    return _on_writer(hosted, _users(hosted)['principal-p'], lambda s: s.company.conn.execute(
        sa.select(sa.func.count()).select_from(c.batches).where(c.batches.c.kind == 'automation')).scalar())


@pytest.mark.timeout(900)
def test_a_fake_automation_runs_only_while_live_under_a_holder_and_cites_its_rule(hosted, fixture_kind):  # noqa: F811
    from bookflow import BookflowError
    from bookflow.company import automation
    from bookflow.core.ids import new_id
    cid, host = hosted.company_id, hosted.handle.host
    fired, failing = fixture_kind['fired'], fixture_kind['failing']
    agent, as_agent = _agent(hosted, role='standard')
    p = _users(hosted)['principal-p']
    rule = {'kind': 'fixture', 'rule': 'Weekly sign-ups'}
    tick = lambda day: host.schedule_now(now=datetime(2026, 6, day, 12, tzinfo=timezone.utc))[cid]  # noqa: E731
    outcome = lambda day: tick(day)['automations']['fixture:Weekly sign-ups']  # noqa: E731
    paused_todos = lambda: _todos(hosted, 'Automation Weekly sign-ups is paused')  # noqa: E731

    # The agent drafts the rule (the ordinary right to write it): due, but `fire` refuses a draft and nothing is
    # written, no batch opened.
    def draft(s):
        FIXTURE.create(s.company.conn, checkfirst=True)
        s.company.conn.execute(FIXTURE.insert().values(id=new_id(), version=1, name='Weekly sign-ups',
                                                       customer='Fixture Signup', state='draft'))
    _on_writer(hosted, agent, lambda s: _tx(s, draft), principal=p)
    assert outcome(1) == {'skipped': 'it is draft'} and fired == [] and _customers(hosted, 'Fixture Signup') == []
    assert _batch_count(hosted) == 0

    # Making it live needs automate.invoice: the agent is refused, naming it and its principal.
    def establish(s):
        automation.require_establish(s, 'automate.invoice', 'fixture live')
        _write(s, _row(s, 'Weekly sign-ups'), **automation.establisher(s))
    with pytest.raises(BookflowError) as refused:
        _on_writer(hosted, agent, lambda s: _tx(s, establish), principal=p)
    assert refused.value.code == 'E_PERMISSION' and refused.value.details['capability'] == 'automate.invoice'
    assert refused.value.details['reason'] == 'capability_not_granted' and 'principal' in refused.value.message

    # Once its principal grants it, the agent makes it live (its authority epoch recorded); the next firing writes as
    # the agent, citing the rule, and the same slot never fires twice.
    _grant(hosted, agent, 'standard', grants=['automate.invoice'])
    _on_writer(hosted, agent, lambda s: _tx(s, establish), principal=p)
    assert _on_writer(hosted, p, lambda s: _row(s, 'Weekly sign-ups')['established_epoch']) is not None
    shown = hosted.ok('automation.show', rule, company=cid, headers=hosted.bearer)
    assert shown['state'] == 'live' and shown['established_by'] == agent and shown['established_for'] == p
    assert shown['capability'] == 'automate.invoice' and shown['would_pause'] is None
    done = outcome(2)
    assert _customers(hosted, 'Fixture Signup') == ['Fixture Signup 1'] and done['batch'] == fired[0][1]
    assert outcome(2) == {'skipped': 'slot 2026-06-02 already fired'} and len(fired) == 1
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
    # The citation as an ordinary write's source is refused; one written anyway renders only for the establisher's
    # own system write.
    forged = automation.source_ref('fixture', 'x', agent, 'Weekly sign-ups')
    r = hosted.call('customer.create', {'name': 'Forged Co'}, company=cid,
                    headers={**hosted.bearer, **REASON, 'X-Bookflow-Source-Ref': forged})
    assert r.status_code == 422 and r.json()['details']['fields'][0]['field'] == 'source_ref', r.text
    assert _on_writer(hosted, p, lambda s: automation.render(s, forged, interface='http', actor_id=agent)) is None
    assert _on_writer(hosted, p, lambda s: automation.render(s, forged, interface='system', actor_id=p)) is None

    # The grant withdrawn: the next firing pauses it with the reason and one to-do, and writes nothing.
    _grant(hosted, agent, 'standard')
    assert 'automate.invoice' in hosted.ok('automation.show', rule, company=cid, headers=hosted.bearer)['would_pause']
    paused = outcome(3)['paused']
    assert 'no longer granted automate.invoice' in paused and len(fired) == 1 and len(paused_todos()) == 1
    assert outcome(4) == {'skipped': 'it is paused'} and len(paused_todos()) == 1
    listed = hosted.ok('automation.list', {'kind': 'fixture'}, company=cid, headers=hosted.bearer)['rows']
    assert [(r['name'], r['state']) for r in listed] == [('Weekly sign-ups', 'paused')]

    # A person (principal-p, an owner: a holder) makes it live again; an agent's operative edit returns it to draft.
    _on_writer(hosted, p, lambda s: _tx(s, establish))

    def edit(s, confirmed=True):
        before = _row(s, 'Weekly sign-ups')
        after, warnings = automation.on_edit(s, 'automate.invoice', before, dict(before, customer='Fixture Other'),
                                             operative=True, confirmed=confirmed)
        _write(s, before, **after)
        return warnings
    warned = _on_writer(hosted, agent, lambda s: _tx(s, edit), principal=p)
    shown = hosted.ok('automation.show', rule, company=cid, headers=hosted.bearer)
    assert shown['state'] == 'draft' and shown['established_by'] is None and 'rules-agent' in shown['draft_reason']
    assert 'returns it to draft' in warned[0] and outcome(5) == {'skipped': 'it is draft'}
    # A holder's edit keeps a live rule live, under the editor; where the kind needs its check confirmed, a holder's
    # unconfirmed edit returns it to draft too.
    _on_writer(hosted, p, lambda s: _tx(s, establish))
    _on_writer(hosted, p, lambda s: _tx(s, lambda s2: edit(s2, confirmed=False)))
    assert hosted.ok('automation.show', rule, company=cid, headers=hosted.bearer)['state'] == 'draft'
    _on_writer(hosted, p, lambda s: _tx(s, establish))
    _grant(hosted, agent, 'standard', grants=['automate.invoice'])
    _on_writer(hosted, agent, lambda s: _tx(s, edit), principal=p)
    shown = hosted.ok('automation.show', rule, company=cid, headers=hosted.bearer)
    assert shown['state'] == 'live' and shown['established_by'] == agent

    # Pausing is open to any editor of the rule; the establisher's agent is deactivated: paused at the next firing,
    # with a to-do.
    out = hosted.ok('automation.pause', rule, company=cid, headers={**hosted.bearer, **REASON})
    assert out['state'] == 'paused' and out['paused_reason'].startswith('paused by')
    assert len(paused_todos()) == 2
    _on_writer(hosted, agent, lambda s: _tx(s, establish), principal=p)
    hosted.ok('agent.deactivate', {'agent': agent}, headers=REASON)
    paused = outcome(6)['paused']
    assert 'deactivated' in paused and len(fired) == 1 and len(paused_todos()) == 3

    # An effect that keeps failing: counted, and paused with a to-do on the third failure in a row; no empty batch.
    _on_writer(hosted, p, lambda s: _tx(s, establish))
    failing.append(True)
    batches_before = _batch_count(hosted)
    assert outcome(7)['failures'] == 1 and outcome(8)['failures'] == 2
    assert 'its last 3 firings failed' in outcome(9)['paused'] and len(paused_todos()) == 4
    assert _batch_count(hosted) == batches_before and len(fired) == 1


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
    agent, as_agent = _agent(hosted, role='standard')
    as_clerk = _clerk(hosted, 'clerk')
    as_temp = _clerk(hosted, 'temp')
    as_viewer = _clerk(hosted, 'viewer', role='readonly')
    users = _users(hosted)
    tick = lambda day: host.schedule_now(now=datetime(2026, 6, day, 12, tzinfo=timezone.utc))[cid]  # noqa: E731
    name = {'schedule': 'Daily invoices'}
    rule = {'kind': 'delivery', 'rule': 'Daily invoices'}
    paused_todos = lambda: _todos(hosted, 'Email schedule Daily invoices is paused')  # noqa: E731

    def make_live(headers, command='update'):
        body = {**name, 'mode': 'send'} if command == 'update' else name
        check = hosted.ok(f'delivery-schedule.{command}?dry_run=true', body, company=cid, headers=headers)['check']
        return hosted.ok(f'delivery-schedule.{command}', {**body, 'confirm': check['fingerprint']}, company=cid,
                         headers=headers)

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
    # A draft's run leaves every message waiting for a go: it sends nothing.
    ran = tick(1)['delivery_schedules']['Daily invoices']['ran']
    assert ran[-1]['state'] == 'done' and not hosted.ok('delivery-schedule.show', name, company=cid,
                                                        headers=hosted.bearer)['runs'][0]['go']

    # A person denied automate.delivery is refused, naming it (and the grant said the deny binds them too).
    denied = _grant(hosted, users['temp'], 'standard', denies=['automate.delivery'])
    assert 'binds temp too' in denied['message'] and 'binds only' not in denied['message']
    r = hosted.call('delivery-schedule.update?dry_run=true', {**name, 'mode': 'send'}, company=cid, headers=as_temp)
    assert r.status_code == 200 and r.json()['check'] is None
    r = hosted.call('delivery-schedule.update', {**name, 'mode': 'send', 'confirm': 'x'}, company=cid,
                    headers=as_temp)
    body = r.json()
    assert r.status_code == 403 and body['details']['capability'] == 'automate.delivery', r.text
    assert body['details']['reason'] == 'capability_not_held' and 'denied' in body['message']
    # An agent granted automate.delivery is refused by the go check: a go is still a person's (increment 3).
    _grant(hosted, agent, 'standard', grants=['automate.delivery'])
    check = hosted.ok('delivery-schedule.update?dry_run=true', {**name, 'mode': 'send'}, company=cid,
                      headers=as_agent)['check']
    r = hosted.call('delivery-schedule.update', {**name, 'mode': 'send', 'confirm': check['fingerprint']},
                    company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['reason'] == 'no_send_authority', r.text
    # The clerk (standard: a holder) makes it live by update.
    live = make_live(as_clerk)
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
    make_live(as_clerk, 'live')

    # Anyone who may edit it pauses it (the temp, who may not make it live); the clerk gets one to-do.
    paused = hosted.ok('automation.pause', rule, company=cid, headers=as_temp)
    assert paused['state'] == 'paused' and paused['paused_reason'].startswith('paused by temp')
    assert len(paused_todos()) == 1
    again = hosted.ok('automation.pause', rule, company=cid, headers=as_temp)
    assert again['warnings'] == ['It is already paused.']
    # Paused to live again through update, by a holder.
    assert make_live(as_clerk)['state'] == 'live'

    # The clerk loses the capability: a reader sees only that it would pause, an administrator why; the next firing
    # pauses it with the reason and a to-do.
    _grant(hosted, users['clerk'], 'standard', denies=['automate.delivery'])
    seen = hosted.ok('automation.show', rule, company=cid, headers={'Authorization': as_viewer['Authorization']})
    assert seen['would_pause'] == 'its establisher no longer holds the authority'
    shown = hosted.ok('automation.show', rule, company=cid, headers=hosted.bearer)
    assert 'denied automate.delivery' in shown['would_pause']
    tick(3)
    shown = hosted.ok('delivery-schedule.show', name, company=cid, headers=hosted.bearer)
    assert shown['state'] == 'paused' and 'denied automate.delivery' in shown['paused_reason']
    assert not shown['runs'][0]['go'] and len(paused_todos()) == 2

    # Resuming a stopped live schedule is making it live: a non-holder's resume brings it back as a draft.
    week = {'schedule': 'Weekly invoices'}
    hosted.ok('delivery-schedule.create', {'name': 'Weekly invoices', 'kind': 'invoice', 'unsent': True,
                                           'frequency': 'weekly', 'start_date': '2099-01-05', 'mode': 'send'},
              company=cid, headers=person)
    check = hosted.ok('delivery-schedule.live?dry_run=true', week, company=cid, headers=person)['check']
    hosted.ok('delivery-schedule.live', {**week, 'confirm': check['fingerprint']}, company=cid, headers=person)
    assert hosted.ok('delivery-schedule.pause', week, company=cid, headers=as_temp)['active'] is False
    resumed = hosted.ok('delivery-schedule.resume', week, company=cid, headers=as_temp)
    assert resumed['state'] == 'draft' and resumed['active'] and 'making it live' in resumed['warnings'][0]


class _Minutes:
    def __init__(self, monkeypatch, at):
        from bookflow.core import clock as bf_clock
        self.at = at
        monkeypatch.setattr(bf_clock, 'now', lambda: self.at)


@pytest.mark.timeout(900)
def test_a_deny_or_a_pause_mid_run_stops_the_go_of_the_next_chunk(hosted, monkeypatch):  # noqa: F811
    from bookflow.company import delivery_batches
    from tests.delivery_fake_transport import SETTINGS, FakeBackend, registered
    from tests.test_delivery_schedules import _host_books
    cid, host = hosted.company_id, hosted.handle.host
    _host_books(hosted)
    person = {**hosted.bearer, **REASON}
    as_clerk = _clerk(hosted, 'clerk')
    users = _users(hosted)
    name = {'schedule': 'Daily invoices'}
    paused_todos = lambda: _todos(hosted, 'Email schedule Daily invoices is paused')  # noqa: E731
    monkeypatch.setattr(delivery_batches, 'CHUNK', 1)  # a run spans ticks, one message a chunk

    def invoices(day):
        for n in range(3):
            hosted.ok('invoice.post', {'number': f'M-{day}-{n}', 'date': f'2026-06-0{day}', 'customer':
                                       'Harbour Mills', 'lines': [{'item': 'Site labour', 'quantity': '1'}]},
                      company=cid, headers=person)

    def make_live():
        check = hosted.ok('delivery-schedule.live?dry_run=true', name, company=cid, headers=as_clerk)['check']
        hosted.ok('delivery-schedule.live', {**name, 'confirm': check['fingerprint']}, company=cid, headers=as_clerk)

    def at(day, minute):
        clock.at = datetime(2026, 6, day, 12, minute, tzinfo=timezone.utc)
        host.schedule_now(now=clock.at)
        return hosted.ok('delivery-schedule.show', name, company=cid, headers=hosted.bearer)

    hosted.ok('delivery-schedule.create', {'name': 'Daily invoices', 'kind': 'invoice', 'unsent': True,
                                           'frequency': 'daily', 'start_date': '2026-06-02', 'mode': 'send'},
              company=cid, headers=as_clerk)
    make_live()
    clock = _Minutes(monkeypatch, datetime(2026, 6, 2, 11, tzinfo=timezone.utc))
    with registered(FakeBackend()) as backend:
        hosted.ok('delivery-connection.add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS}, company=cid,
                  headers=person)
        hosted.ok('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=person)
        # `automation pause` mid-run: the open run's go is dropped, so its next chunk waits for a person's.
        invoices(2)
        shown = at(2, 0)
        assert shown['runs'][0]['go'] and len(backend.delivered) == 1
        hosted.ok('automation.pause', {'kind': 'delivery', 'rule': 'Daily invoices'}, company=cid, headers=person)
        shown = at(2, 10)
        assert shown['state'] == 'paused' and not shown['runs'][0]['go'] and len(backend.delivered) == 1
        assert len(paused_todos()) == 1
        for minute in (20, 30, 40):  # five unsent invoices (the books' two and these three): one chunk a tick, no go, until the run is done; until then
            # the open run is continued before a new slot begins
            shown = at(2, minute)
        assert shown['runs'][0]['state'] == 'done' and len(backend.delivered) == 1
        # A deny mid-run: the next chunk asks again, pauses the rule with one to-do, and nothing more leaves.
        make_live()
        invoices(3)
        shown = at(3, 0)
        assert shown['state'] == 'live' and shown['runs'][0]['go'] and len(backend.delivered) == 2
        _grant(hosted, users['clerk'], 'standard', denies=['automate.delivery'])
        shown = at(3, 10)
        assert shown['state'] == 'paused' and 'denied automate.delivery' in shown['paused_reason']
        assert not shown['runs'][0]['go'] and len(backend.delivered) == 2 and len(paused_todos()) == 2
        at(3, 20)
        assert len(backend.delivered) == 2 and len(paused_todos()) == 2


@pytest.mark.timeout(900)
def test_a_root_activated_before_the_capability_keeps_its_people_holding_it(root, monkeypatch):
    """A root whose stored catalog predates capabilities-v1: a person's membership decides, as their role did, so an
    owner still makes a schedule live and the tick does not pause it."""
    import bookflow
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.config import os_login
    from bookflow.core.context import client_version
    from bookflow.hub import permission_catalog as c, permission_runtime as runtime
    from tests.conftest import make_legacy
    from tests.test_people_only import _bearer, _free_port
    from tests.test_row3_host import PASSWORD, Hosted
    make_legacy(root)
    lib = bookflow.connect(data_root=str(root))
    with monkeypatch.context() as previous_build:
        previous_build.setattr(runtime, 'current_catalog',
                               lambda: runtime.known_catalog(c.BANK_FEED_REVERSE_POLICY_VERSION))
        state = lib.permission.show()
        lib.permission.activate(expected_generation=state['generation'],
                                expected_catalog_sha256=state['catalog_sha256'])
    company = lib.company.list()['items'][0]['company_id']
    lib.run('user set-password', {'username': os_login(), 'password': PASSWORD})
    issued = lib.token.issue(label='installer')
    handle = start_serving(root, client_version(), bind=f'127.0.0.1:{_free_port()}', secure_cookies=False)
    try:
        hosted = Hosted(handle, root, os_login(), company, issued, '', {})
        hosted.ok('user.add', {'username': 'principal-p', 'company': company, 'role': 'owner',
                               'password': 'pw-principal-p-12345'}, headers=REASON)
        as_person = {**_bearer(hosted, 'principal-p'), **REASON}
        name = {'schedule': 'Daily invoices'}
        hosted.ok('delivery-schedule.create', {'name': 'Daily invoices', 'kind': 'invoice', 'unsent': True,
                                               'frequency': 'daily', 'start_date': '2026-06-01', 'mode': 'send'},
                  company=company, headers=as_person)
        check = hosted.ok('delivery-schedule.live?dry_run=true', name, company=company, headers=as_person)['check']
        made = hosted.ok('delivery-schedule.live', {**name, 'confirm': check['fingerprint']}, company=company,
                         headers=as_person)
        assert made['state'] == 'live'
        handle.host.schedule_now(now=datetime(2026, 6, 2, 12, tzinfo=timezone.utc))
        shown = hosted.ok('delivery-schedule.show', name, company=company,
                          headers={'Authorization': as_person['Authorization']})
        assert shown['state'] == 'live' and shown['paused_reason'] is None
    finally:
        handle.stop()
