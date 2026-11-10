"""R187 increments 3 and 4 (design/principal-directives.md): an agent's go on delivery, confirm-first marks, the
setup step that frames an agent's level, and the closing case an agent under the Bookkeeper preset meets."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_row3_host import hosted  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- confirm-first marks: information, never a prompt

def test_confirm_first_marks_show_in_help_and_the_mcp_list_and_never_on_an_ordinary_post():
    from bookflow.adapters.cli.app import _help_epilog
    from bookflow.adapters.mcp import catalog
    from bookflow.core import confirm_first, registry
    registry.load_all()

    marked = ('invoice void', 'invoice delete', 'batch reverse', 'delivery send')
    def row(name):
        return next(r for r in catalog.list_commands(prefix=name, limit=50)['commands'] if r['name'] == name)
    for name in marked:
        assert registry.get(name).confirm_first, name
        assert row(name).get('confirm_first'), name
        note = catalog.command_help(name)['confirm_first_note']
        assert note.startswith('Confirm first') and 'never asks' in note
        assert _help_epilog(registry.get(name)).startswith('Confirm first')
    assert {confirm_first.category(n) for n in marked} == {'void', 'delete', 'batch reversal', 'send'}
    # An ordinary post carries no mark anywhere.
    assert registry.get('invoice post').confirm_first is False
    assert 'confirm_first' not in row('invoice post')
    assert 'confirm_first_note' not in catalog.command_help('invoice post')
    assert 'Confirm first' not in _help_epilog(registry.get('invoice post'))
    # Every named mark is a real command, so the table cannot drift from the registry.
    names = {c.name for c in registry.all_commands(include_standalone=True)}
    assert set(confirm_first.NAMED) <= names
    # No command asks "are you sure": the phrase appears only where the mark says it never does.
    asking = [p for p in (ROOT / 'src' / 'bookflow').rglob('*') if p.suffix in {'.py', '.html', '.js'}
              and 'are you sure' in p.read_text(encoding='utf-8').lower()]
    assert asking == [ROOT / 'src' / 'bookflow' / 'core' / 'confirm_first.py']


# ---------------------------------------------------------------- the setup step: words, and a list of grants

def test_setup_level_guidance_names_each_preset_automation_reversal_and_the_audit():
    from bookflow.commands.setup_cmds import PRESETS, level_guidance, preset_grants
    assert preset_grants('bookkeeper') == [] and preset_grants('read-only') == []
    assert preset_grants('trusted', ['bank-rule']) == ['delivery.send', 'opening.accept-differences',
                                                       'automate.bank-rule']
    assert preset_grants('bookkeeper', ['bank-rule']) == []
    level = level_guidance(agent='assistant', company='Harbour Mills', preset='bookkeeper', role='standard',
                           grants=[], applied=False, activated=False)
    assert [p['preset'] for p in level['presets']] == ['bookkeeper', 'read-only', 'trusted']
    assert all(p['cannot'] for p in level['presets']) and level['cannot'] == PRESETS['bookkeeper']['cannot']
    assert next(p for p in level['presets'] if p['preset'] == 'trusted')['pick'] == [
        'automate.bank-rule', 'automate.delivery', 'automate.invoice']
    assert len(level['automations']) == 3 and all('unattended' in a and 'establisher' in a
                                                  for a in level['automations'])
    assert 'bookflow batch reverse BATCH' in level['reverse']
    assert all(word in level['audit'] for word in ('actor', 'interface', 'principal', 'reason'))
    assert level['command'].startswith("bookflow membership grant assistant --company 'Harbour Mills' --role standard")


# ---------------------------------------------------------------- an agent's go (increment 3)

@pytest.fixture
def hosted_queue(hosted):  # noqa: F811
    from tests.delivery_fake_transport import FakeBackend, registered
    with registered(FakeBackend()) as backend:
        yield hosted, backend


@pytest.mark.timeout(900)
def test_an_agent_holding_delivery_send_gives_a_go_and_one_without_is_refused_naming_it(hosted_queue):
    from tests.delivery_fake_transport import SETTINGS
    from tests.test_automation import _grant
    from tests.test_delivery_queue import _hosted_mail
    from tests.test_people_only import REASON as PERSON, _agent
    hosted, backend = hosted_queue
    cid, host = hosted.company_id, hosted.handle.host
    books = _hosted_mail(hosted)
    person = {**hosted.bearer, **PERSON}
    agent, as_agent = _agent(hosted)
    hosted.ok('delivery-connection.add', {'name': 'Office', 'kind': 'fake', 'settings': SETTINGS}, company=cid,
              headers=person)
    hosted.ok('delivery-connection.enable', {'connection': 'Office'}, company=cid, headers=person)

    # Without delivery.send: the release waits and says which capability; approving is refused naming it.
    draft = hosted.ok('delivery.draft', {'kind': 'invoice', 'document': 'DOC-100'}, company=cid, headers=as_agent)
    released = hosted.ok('delivery.send', {'messages': [draft['number']]}, company=cid, headers=as_agent)
    assert not released['messages'][0]['go_by']
    assert any('delivery.send' in w and 'waits for a go' in w for w in released['warnings'])
    r = hosted.call('delivery.approve', {'messages': [draft['number']]}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['capability'] == 'delivery.send'
    assert 'delivery.send' in r.json()['message']
    r = hosted.call('delivery.mark-sent', {'message': draft['number']}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['capability'] == 'delivery.mark-sent'
    assert 'delivery.mark-sent' in r.json()['message']
    assert host.schedule_now()[cid]['delivery']['claimed'] == 0

    # Granted delivery.send: its approval is a go, and the tick sends the message.
    _grant(hosted, agent, 'admin', grants=['delivery.send'])
    approved = hosted.ok('delivery.approve', {'messages': [draft['number']]}, company=cid, headers=as_agent)
    assert [m['number'] for m in approved['messages']] == [draft['number']]
    ticked = host.schedule_now()[cid]['delivery']
    assert ticked['sent'] == 1 and backend.deliveries_of(draft['number']) == 1
    # Still no delivery.mark-sent: the send grant does not carry it.
    receipt = hosted.ok('delivery.draft', {'kind': 'sales-receipt', 'document': 'DOC-200'}, company=cid,
                        headers=as_agent)
    r = hosted.call('delivery.mark-sent', {'message': receipt['number']}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['capability'] == 'delivery.mark-sent'

    # Its own release is a go at once; the grant withdrawn before the claim withdraws the go.
    given = hosted.ok('delivery.send', {'messages': [receipt['number']]}, company=cid, headers=as_agent)
    assert given['messages'][0]['go_by'] == agent
    _grant(hosted, agent, 'admin')
    out = books['run']('delivery run', {})
    assert out['go_withdrawn'] == 1 and out['claimed'] == 0 and backend.deliveries_of(receipt['number']) == 0
    events = books['run']('delivery history', {'message': receipt['number']})['events']
    assert events[-1]['details']['go_withdrawn'] == agent and events[-1]['details']['why']


# ---------------------------------------------------------------- the closing case (increment 4)

@pytest.mark.timeout(900)
def test_a_bookkeeper_agent_drafts_an_auto_add_rule_is_told_the_capability_and_after_the_grant_it_acts(hosted):  # noqa: F811
    from tests.test_automation import _grant
    from tests.test_bank_feeds import csv_file
    from tests.test_bank_rules import contains
    from tests.test_people_only import REASON, _agent
    cid = hosted.company_id
    owner = {**hosted.bearer, **REASON}
    agent, as_agent = _agent(hosted, role='standard')   # the Bookkeeper preset: standard, no grants

    def call(name, body, headers=owner, dry_run=False):
        return hosted.ok(name + ('?dry_run=true' if dry_run else ''), body, company=cid, headers=headers)

    # Asked to have Shell fuel added automatically: it drafts the rule and is told which capability making it live needs.
    made = call('bank-rule.create', {'name': 'Shell fuel', 'account': 'Checking', 'direction': 'out',
                                     'conditions': contains('SHELL'), 'payee': 'Central Supply',
                                     'category': 'Vehicle Expense', 'auto_add': True}, as_agent)['rule']
    assert (made['state'], made['auto_add']) == ('draft', True)
    r = hosted.call('bank-rule.update?dry_run=true', {'rule': 'Shell fuel', 'live': True}, company=cid,
                    headers=as_agent)
    body = r.json()
    assert r.status_code == 403 and body['details']['capability'] == 'automate.bank-rule'
    assert body['details']['reason'] == 'capability_not_granted' and 'automate.bank-rule' in body['message']
    assert 'membership grant' in body['details']['next_step']

    # The principal grants it: one check of the dry run's details, then live; the next import adds the line.
    _grant(hosted, agent, 'standard', grants=['automate.bank-rule'])
    check = call('bank-rule.update', {'rule': 'Shell fuel', 'live': True}, as_agent, dry_run=True)['check']
    live = call('bank-rule.update', {'rule': 'Shell fuel', 'live': True, 'confirm': check['fingerprint']},
                as_agent)['rule']
    assert live['state'] == 'live' and live['established_by'] == agent
    done = call('bank-feed.import', {'account': 'Checking', 'content': csv_file([('2026-10-06', 'SHELL 4471',
                                                                                  '-48.20')])})
    [ran] = done['automations']
    assert (ran['outcome'], ran['added'], done['lines'][0]['state']) == ('acted', 1, 'added')


# ---------------------------------------------------------------- the setup step with permissions activated

@pytest.mark.timeout(600)
def test_setup_presets_apply_their_grants_once_permissions_are_activated(tmp_path, monkeypatch):
    """The level step really grants on an activated installation: Trusted holds standard, its two grants and the one
    automate.* picked; Bookkeeper holds standard and nothing granted; Read-only is role readonly. What an agent holds
    by name is read from `membership list` (its grants) and `membership effective` (each capability's grant sources)."""
    import bookflow
    from tests.test_agent_setup import _free_port
    root, project = tmp_path / 'books', tmp_path / 'project'
    project.mkdir()
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))

    def setup(agent, **extra):
        out = client.run('setup', {'company': 'Harbour Mills', 'owner_name': 'Owner', 'serve': False,
                                   'bind': f'127.0.0.1:{_free_port()}', 'agent': agent,
                                   'mcp_config': str(project / f'{agent}.mcp.json'), **extra})
        return out, {s['step']: s for s in out['steps']}

    # The first agent and the company; permissions activated (a new root may already be, else a person activates).
    first, _ = setup('keeper')
    cid = first['company']['company_id']
    state = client.run('permission show', {})
    if state['mode'] != 'policy_v1':
        client.run('permission activate', {'expected_generation': state['generation'],
                                           'expected_catalog_sha256': state['catalog_sha256']}, reason='owner activates')
    assert client.run('permission show', {})['mode'] == 'policy_v1'

    automate = {'automate.bank-rule', 'automate.delivery', 'automate.invoice'}
    named = {'delivery.send', 'opening.accept-differences'} | automate

    def held(agent):
        [row] = [m for m in client.run('membership list', {'user': agent, 'company': cid})['items']
                 if m['scope_type'] == 'company' and m['scope_id'] == cid and m['active']]
        rows = client.run('membership effective', {'company': cid, 'user': agent})['permissions']
        granted = {p['requirement']['capability'] for p in rows if p['grant_sources']}
        admitted = {p['requirement']['capability'] for p in rows if p['admitted']}
        return row['role'], set(row['grants'] or []), granted, admitted

    # Trusted, with one automation picked: the grants are applied, not left as a command to run.
    out, steps = setup('trusty', preset='trusted', automate=['bank-rule'])
    assert steps['level']['status'] == 'created', steps['level']
    assert out['level']['applied'] is True and out['level']['permissions_activated'] is True
    role, grants, granted, admitted = held('trusty')
    want = {'delivery.send', 'opening.accept-differences', 'automate.bank-rule'}
    assert (role, grants) == ('standard', want)
    assert granted & named == want and want <= admitted
    # The two not picked are not granted by name; an agent needs that (a role default is never enough for an agent,
    # hub/capabilities.py), and `membership effective` says so: it does not show them as admitted.
    assert not (automate - {'automate.bank-rule'}) & granted
    assert not (automate - {'automate.bank-rule'}) & admitted

    # Bookkeeper (the second agent, rerun with the preset now that permissions are active): standard, no grants.
    out, steps = setup('keeper', preset='bookkeeper')
    assert steps['level']['status'] == 'exists' and out['level']['applied'] is True
    role, grants, granted, admitted = held('keeper')
    assert (role, grants) == ('standard', set()) and not granted and not (automate | {'delivery.send'}) & admitted

    # Read-only: role readonly, no grants.
    out, steps = setup('reader', preset='read-only')
    assert steps['level']['status'] == 'exists' and out['level']['applied'] is True
    role, grants, granted, admitted = held('reader')
    assert (role, grants) == ('readonly', set()) and not granted and not named & admitted
