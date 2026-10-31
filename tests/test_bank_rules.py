"""R108 increment 4: bank rules and learning (design/bank-feeds.md §5; criterion 6).

Against the demo's Checking and Payment Example Bank. Order is by priority, then a rule for the line's own account,
then specificity (equals before starts_with before contains, then more conditions), and the first rule that fits
wins; a learned rule ranks below a written one; a rule never changes a decided line; a history proposal needs two
earlier answers; two consistent adds learn a suggested rule, a declined key is never suggested again, and reversing
the import that taught a rule retires it unless it was edited. An ignore rule is an automation: it proposes until a
holder of automate.bank-rule makes it live, then ignores at the next import, inside the import's batch.
"""
import pytest

from tests.test_row3_host import hosted  # noqa: F401 - fixture

from bookflow import BookflowError
from tests.test_bank_feeds import csv_file, run, table
from tests.test_bank_feed_reversal import grant


def imp(client, account, rows, **kw):
    return run(client, 'bank-feed import', dict(account=account, content=csv_file(rows), **kw))


def line_of(done, payee):
    return next(v for v in done['lines'] if v['payee'] == payee)


def rule(client, name, conditions, **kw):
    return run(client, 'bank-rule create', dict(name=name, conditions=conditions, **kw))['rule']


def contains(value, field='payee'):
    return [{'field': field, 'op': 'contains', 'value': value}]


def inbox(client, line_id):
    return next(v for v in run(client, 'bank-line query', dict(limit=1000))['lines'] if v['id'] == line_id)


SUPPLY = dict(payee='Central Supply', category='Office Supplies')


def test_order_specificity_learned_below_written_and_decided_lines_untouched(client):
    checking = 'Checking'
    broad = rule(client, 'Shell any', contains('SHELL'), account=checking, **SUPPLY)
    narrow = rule(client, 'Shell oil', [{'field': 'payee', 'op': 'starts_with', 'value': 'shell  oil'}],
                  account=checking, payee='Central Supply', category='Vehicle Expense')
    # Every account, two conditions: below a rule for the line's own account at the same priority.
    rule(client, 'Shell everywhere', [*contains('SHELL'), {'field': 'amount', 'op': 'between', 'value': '1.00',
                                                           'value_to': '100.00'}], **SUPPLY)
    done = imp(client, checking, [('2026-10-06', 'SHELL OIL 4471', '-45.67'), ('2026-10-07', 'SHELL CAR WASH', '-9.00'),
                                  ('2026-10-08', 'RIVERSIDE APTS', '300.00')])
    fuel, wash, rent = (line_of(done, p) for p in ('SHELL OIL 4471', 'SHELL CAR WASH', 'RIVERSIDE APTS'))
    # starts_with is more specific than contains; the account's own rule before the every-account one.
    assert fuel['rule_id'] == narrow['id'] and "payee starts with shell  oil" in fuel['rule_reason']
    assert wash['rule_id'] == broad['id'] and rent['rule_id'] is None and done['counts']['proposed'] == 2
    shown = inbox(client, fuel['id'])
    assert [(p['kind'], p['rule_id']) for p in shown['proposals']] == [('rule', narrow['id'])]
    assert shown['rule_id'] == narrow['id']
    # Priority comes first: a contains rule at 50 beats the starts_with at 100, once applied.
    first = rule(client, 'Shell first', contains('shell'), account=checking, priority=50, **SUPPLY)
    plan = run(client, 'bank-rule apply', dict(rule=first['id']), dry_run=True)
    assert {v['line_id'] for v in plan['lines']} == {fuel['id'], wash['id']}
    assert inbox(client, fuel['id'])['rule_id'] == narrow['id']  # a dry run changes nothing
    order = [r['name'] for r in run(client, 'bank-rule query', dict(account=checking))['rules']]
    assert order == ['Shell first', 'Shell oil', 'Shell any', 'Shell everywhere']

    # One click: add with no payee or account takes the proposal; the line keeps its rule.
    added = run(client, 'bank-line add', dict(line=fuel['id']))
    assert added['lines'][0]['state'] == 'added' and added['lines'][0]['rule_id'] == narrow['id']
    assert table(client, 'SELECT account_id FROM posting_lines WHERE transaction_id = ? AND debit_minor_units > 0',
                 added['entries'][0]['entry_id']) == table(client, "SELECT id FROM accounts WHERE name = 'Vehicle "
                                                                   "Expense'")
    # A rule never changes a decided line: the added line keeps its rule; only the waiting one moves.
    applied = run(client, 'bank-rule apply', dict(account=checking))
    assert [v['line_id'] for v in applied['lines']] == [wash['id']]
    assert inbox(client, fuel['id'])['rule_id'] == narrow['id'] and inbox(client, wash['id'])['rule_id'] == first['id']

    # A learned rule ranks below a written one: two consistent adds of COFFEE BEAN learn one, accepted; a written
    # rule for coffee then wins over it on the next line.
    run(client, 'bank-rule deactivate', dict(rule=first['id']))
    beans = imp(client, checking, [('2026-10-10', 'COFFEE BEAN 12', '-4.50'), ('2026-10-11', 'COFFEE BEAN #34', '-5.25')])
    one = run(client, 'bank-line add', dict(line=line_of(beans, 'COFFEE BEAN 12')['id'], **{
        'payee': 'Central Supply', 'account': 'Office Supplies'}))
    assert 'Always do this' not in (one['next'] or '')
    two = run(client, 'bank-line add', dict(line=line_of(beans, 'COFFEE BEAN #34')['id'], payee='Central Supply',
                                            account='Office Supplies'))
    assert 'Always do this for COFFEE BEAN? (2 times so far)' in two['next']
    learned = run(client, 'bank-rule query', dict(status='suggested'))['rules']
    assert [(r['name'], r['source'], r['priority'], r['auto_add'], r['learned_key']) for r in learned] == [
        ('Coffee Bean (learned)', 'learned', 1000, False, 'COFFEE BEAN')]
    run(client, 'bank-rule accept', dict(rule=learned[0]['id']))
    written = rule(client, 'Coffee written', contains('COFFEE'), account=checking, payee='Central Supply',
                   category='Vehicle Expense')
    third = imp(client, checking, [('2026-10-14', 'COFFEE BEAN 77', '-6.10')])
    assert line_of(third, 'COFFEE BEAN 77')['rule_id'] == written['id']
    run(client, 'bank-rule deactivate', dict(rule=written['id']))
    run(client, 'bank-rule apply', dict(account=checking))
    assert inbox(client, line_of(third, 'COFFEE BEAN 77')['id'])['rule_id'] == learned[0]['id']


def test_history_learning_decline_and_reversal_retires_only_unedited_learned_rules(client):
    grant(client, 'transaction.check.delete')
    first = imp(client, 'Checking', [('2026-10-06', 'JOES DINER 1234', '-12.00'),
                                     ('2026-10-09', 'JOES DINER #9876 TX', '-14.00')])
    for payee in ('JOES DINER 1234', 'JOES DINER #9876 TX'):
        run(client, 'bank-line add', dict(line=line_of(first, payee)['id'], payee='Central Supply',
                                          account='Office Supplies'))
    diner = run(client, 'bank-rule query', dict(status='suggested'))['rules'][0]
    assert diner['learned_key'] == 'JOES DINER' and diner['learned_from_batch'] == first['batch_id']
    # A suggestion does not propose; history does, from two earlier answers.
    later = imp(client, 'Checking', [('2026-10-15', 'JOES DINER 5555', '-16.00')])
    line = line_of(later, 'JOES DINER 5555')
    assert line['rule_id'] is None and line['rule_reason'] == 'from history: 2 of 2 times'
    shown = inbox(client, line['id'])['proposals']
    assert [p['kind'] for p in shown] == ['history'] and shown[0]['payee'] == 'Central Supply'
    # Declined: never suggested again for that account, however many more agree.
    run(client, 'bank-rule decline', dict(rule=diner['id']))
    taken = run(client, 'bank-line add', dict(line=line['id']))
    assert taken['lines'][0]['state'] == 'added' and 'Always do this' not in (taken['next'] or '')
    assert table(client, "SELECT status FROM bank_rules WHERE learned_key = 'JOES DINER'") == [('retired',)]

    # Payment Example Bank: two adds learn PIZZA PALACE, which is then edited; a later import learns TACO TOWN.
    pizza = imp(client, 'Payment Example Bank', [('2026-10-06', 'PIZZA PALACE 1', '-20.00'),
                                                 ('2026-10-07', 'PIZZA PALACE 2', '-22.00')])
    for payee in ('PIZZA PALACE 1', 'PIZZA PALACE 2'):
        run(client, 'bank-line add', dict(line=line_of(pizza, payee)['id'], payee='Central Supply',
                                          account='Office Supplies'))
    edited = run(client, 'bank-rule update', dict(rule='Pizza Palace (learned)', memo='Team lunch'))['rule']
    assert edited['edited'] and edited['memo'] == 'Team lunch'
    taco = imp(client, 'Payment Example Bank', [('2026-10-20', 'TACO TOWN 1', '-8.00'), ('2026-10-21', 'TACO TOWN 2',
                                                                                          '-9.00')])
    for payee in ('TACO TOWN 1', 'TACO TOWN 2'):
        run(client, 'bank-line add', dict(line=line_of(taco, payee)['id'], payee='Central Supply',
                                          account='Office Supplies'))
    plan = run(client, 'bank-feed reverse', dict(import_id=taco['import_id']), dry_run=True)
    assert 'retire bank_rule (rule_learned)' in str(plan['steps'])
    run(client, 'bank-feed reverse', dict(import_id=taco['import_id'], fingerprint=plan['fingerprint']))
    run(client, 'bank-feed reverse', dict(import_id=pizza['import_id']))
    rules = {r['name']: r for r in run(client, 'bank-rule query', {})['rules']}
    assert rules['Taco Town (learned)']['status'] == 'retired'
    assert rules['Pizza Palace (learned)']['status'] == 'suggested'  # edited since: kept
    # Each reversal undid exactly what its import's lines were added as.
    for batch in (taco['batch_id'], pizza['batch_id']):
        assert table(client, 'SELECT state FROM batches WHERE id = ?', batch) == [('reversed',)]
    with pytest.raises(BookflowError):
        run(client, 'bank-rule update', dict(rule='Taco Town (learned)', memo='x'))
    assert run(client, 'bank-line query', dict(account='Payment Example Bank'))['lines'] == []
    assert len(run(client, 'bank-line query', dict(account='Checking', state='added'))['lines']) == 3


def test_an_ignore_rule_proposes_until_live_then_ignores_inside_the_import_batch(client):
    fee = rule(client, 'Service fee', contains('SERVICE FEE'), account='Checking', action='ignore',
               ignore_reason='already_entered')
    assert fee['automation'] and fee['state'] == 'draft'
    first = imp(client, 'Checking', [('2026-10-06', 'MONTHLY SERVICE FEE', '-12.00')])
    line = line_of(first, 'MONTHLY SERVICE FEE')
    assert line['state'] == 'waiting' and line['rule_id'] == fee['id'] and line['rule_acts'] is None
    assert inbox(client, line['id'])['proposals'][0]['next'].startswith('bank-line ignore')
    # Going live is one check of the exact details: the dry run's fingerprint, or refused.
    check = run(client, 'bank-rule update', dict(rule=fee['id'], live=True), dry_run=True)['check']
    assert check['waiting'] == 1 and check['does'].endswith('ignores the line (already_entered).')
    with pytest.raises(BookflowError) as raised:
        run(client, 'bank-rule update', dict(rule=fee['id'], live=True))
    assert raised.value.details['reason'] == 'confirm_required'
    live = run(client, 'bank-rule update', dict(rule=fee['id'], live=True, confirm=check['fingerprint']))['rule']
    assert live['state'] == 'live' and live['established_by']
    assert inbox(client, line['id'])['state'] == 'waiting'  # it acts on later imports only
    second = imp(client, 'Checking', [('2026-11-06', 'MONTHLY SERVICE FEE', '-12.50')])
    [ran] = second['automations']
    assert (ran['outcome'], ran['ignored']) == ('acted', 1)
    now = inbox(client, line_of(second, 'MONTHLY SERVICE FEE')['id'])
    assert (now['state'], now['ignore_reason'], now['link_how']) == ('ignored', 'already_entered', 'rule')
    effects = table(client, "SELECT record_type, action FROM batch_effects WHERE batch_id = ? AND record_type = "
                            "'bank_line' ORDER BY seq", second['batch_id'])
    assert effects == [('bank_line', 'created'), ('bank_line', 'updated')]
    events = table(client, "SELECT source_ref, interface FROM audit_events WHERE command = 'bank-line ignore'")
    assert events[-1][0].startswith(f"automation:bank-rule:{fee['id']}:{live['established_by']}:Service fee;"
                                    'bank-line:') and events[-1][1] == 'system'
    run(client, 'bank-feed reverse', dict(import_id=second['import_id']))
    assert table(client, 'SELECT live FROM bank_lines WHERE id = ?', now['id']) == [(0,)]


# ---------------------------------------------------------------- auto-add, through the host (R187's seam)

@pytest.mark.timeout(900)
def test_an_auto_add_rule_acts_only_while_live_under_a_holder_and_cites_its_rule(hosted):  # noqa: F811
    from tests.test_automation import _clerk, _grant, _todos, _users
    from tests.test_people_only import REASON, _agent
    cid = hosted.company_id
    owner = {**hosted.bearer, **REASON}
    agent, as_agent = _agent(hosted, role='standard')
    as_clerk = _clerk(hosted, 'clerk')
    users = _users(hosted)
    _grant(hosted, users[hosted.login], 'owner', grants=['transaction.check.delete'])

    def call(name, body, headers=owner, dry_run=False):
        return hosted.ok(name + ('?dry_run=true' if dry_run else ''), body, company=cid, headers=headers)

    def imported(rows):
        return call('bank-feed.import', {'account': 'Checking', 'content': csv_file(rows)})

    def entries():
        return len(call('bank-line.query', {'account': 'Checking', 'state': 'added'}, hosted.bearer)['lines'])

    # An agent drafts the auto-add rule; it cannot make it live (refused naming the capability).
    made = call('bank-rule.create', {'name': 'Fuel stop', 'account': 'Checking', 'direction': 'out',
                                     'conditions': contains('FUEL STOP'), 'payee': 'Central Supply',
                                     'category': 'Vehicle Expense', 'auto_add': True}, as_agent)['rule']
    assert (made['state'], made['automation'], made['auto_add']) == ('draft', True, True)
    r = hosted.call('bank-rule.update?dry_run=true', {'rule': 'Fuel stop', 'live': True}, company=cid,
                    headers=as_agent)
    assert r.status_code == 403 and r.json()['details']['capability'] == 'automate.bank-rule'
    # Drafted, it adds nothing: the line waits with the rule's proposal.
    first = imported([('2026-10-06', 'FUEL STOP 001', '-30.00')])
    line = first['lines'][0]
    assert (line['state'], line['rule_id'], line['rule_acts'], first['automations']) == (
        'waiting', made['id'], None, [])
    assert entries() == 0

    # The clerk holds automate.bank-rule: one check of the exact details, then live under the clerk.
    check = call('bank-rule.update', {'rule': 'Fuel stop', 'live': True}, as_clerk, dry_run=True)['check']
    assert check['waiting'] == 1 and 'adds the line in the books unattended' in check['does']
    live = call('bank-rule.update', {'rule': 'Fuel stop', 'live': True, 'confirm': check['fingerprint']},
                as_clerk)['rule']
    assert live['state'] == 'live' and live['established_by'] == users['clerk']
    listed = hosted.ok('automation.list', {'kind': 'bank-rule'}, company=cid, headers=hosted.bearer)['rows']
    assert [(x['name'], x['state']) for x in listed] == [('Fuel stop', 'live')]

    # The next import adds its line inside the import's batch, as the clerk, citing the rule; the waiting one stays.
    second = imported([('2026-10-09', 'FUEL STOP 002', '-31.00')])
    [ran] = second['automations']
    assert (ran['outcome'], ran['added'], second['lines'][0]['state']) == ('acted', 1, 'added')
    assert entries() == 1 and call('bank-line.query', {'account': 'Checking', 'state': 'waiting'}, hosted.bearer)['lines'][0][
        'id'] == line['id']
    events = hosted.ok('audit.list', {'limit': 30}, company=cid, headers=hosted.bearer)['items']
    cited = [e for e in events if e['command'] == 'check post' and e.get('on_rule')]
    assert cited and cited[0]['on_rule']['text'] == 'On rule: Fuel stop, established by clerk'
    assert cited[0]['actor_id'] == users['clerk']
    # One reversal undoes it.
    plan = call('bank-feed.reverse', {'import_id': second['import_id']}, dry_run=True)
    assert plan['entries'] == 1
    call('bank-feed.reverse', {'import_id': second['import_id'], 'fingerprint': plan['fingerprint']})
    assert entries() == 0

    # An edit by the agent (no capability) returns it to draft: the next import adds nothing.
    edited = call('bank-rule.update', {'rule': 'Fuel stop', 'memo': 'Diesel'}, as_agent)['rule']
    assert edited['state'] == 'draft' and 'rules-agent' in edited['draft_reason']
    third = imported([('2026-10-12', 'FUEL STOP 003', '-32.00')])
    assert third['automations'] == [] and third['lines'][0]['state'] == 'waiting' and entries() == 0
    # Live again under the clerk; a holder's change confirmed keeps it live.
    check = call('bank-rule.update', {'rule': 'Fuel stop', 'live': True}, as_clerk, dry_run=True)['check']
    call('bank-rule.update', {'rule': 'Fuel stop', 'live': True, 'confirm': check['fingerprint']}, as_clerk)
    check = call('bank-rule.update', {'rule': 'Fuel stop', 'memo': 'Fuel'}, as_clerk, dry_run=True)['check']
    kept = call('bank-rule.update', {'rule': 'Fuel stop', 'memo': 'Fuel', 'confirm': check['fingerprint']},
                as_clerk)['rule']
    assert kept['state'] == 'live' and kept['memo'] == 'Fuel'

    # The establisher loses the capability: the next import pauses the rule with one to-do; its line waits.
    _grant(hosted, users['clerk'], 'standard', denies=['automate.bank-rule'])
    fourth = imported([('2026-10-15', 'FUEL STOP 004', '-33.00')])
    [ran] = fourth['automations']
    assert ran['outcome'] == 'paused' and 'automate.bank-rule' in ran['reason']
    assert fourth['lines'][0]['state'] == 'waiting' and entries() == 0
    shown = call('bank-rule.show', {'rule': 'Fuel stop'}, hosted.bearer)['rule']
    assert shown['state'] == 'paused' and len(_todos(hosted, 'Automation Fuel stop is paused')) == 1
    fifth = imported([('2026-10-18', 'FUEL STOP 005', '-34.00')])
    assert fifth['automations'] == [] and entries() == 0  # paused: it only proposes
    assert len(_todos(hosted, 'Automation Fuel stop is paused')) == 1
