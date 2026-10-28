"""R180 increment 3: a command that opts in plans inside its own write transaction, and its apply
reuses what the plan prepared; a dry run, and every command that does not opt in, plans as before."""
from bookflow.core import registry
from tests.test_deposit_command import COMPANY, books, receipts_in_undeposited_funds  # noqa: F401 - fixture


def test_only_the_reconciliation_and_deposit_writes_opt_in():
    registry.load_all()
    opted = sorted(cmd.name for cmd in registry.all_commands(include_standalone=True) if getattr(cmd, 'plan_in_write', False))
    assert opted == ['bank-feed import', 'deposit post', 'deposit update', 'deposit void', 'journal delete',
                     'reconcile discard', 'reconcile finish', 'reconcile mark', 'reconcile opening start',
                     'reconcile start']
    assert all(registry.get(name).writes == {'company'} for name in opted)


def _watch(monkeypatch):
    """Each reconciliation load and deposit preparation, with whether it ran inside the write."""
    from bookflow.company import reconciliation_loading as loading, deposit_lifecycle as lifecycle
    seen = []
    for module, name in ((loading, 'load'), (lifecycle, 'prepare')):
        original = getattr(module, name)
        def watched(s, *args, _original=original, _name=name, **kwargs):
            seen.append((_name, bool(s.plan_in_write), bool(s.company.write_transaction)))
            return _original(s, *args, **kwargs)
        monkeypatch.setattr(module, name, watched)
    return seen


def test_a_write_plans_inside_its_transaction_and_its_apply_prepares_nothing_again(books, monkeypatch):
    client = books['client']
    receipts_in_undeposited_funds(books)
    page = client.run('deposit sources', dict(date='2026-06-30', limit=200), company=COMPANY)
    sources = [dict(source_type=r['source_type'], source=r['source'], expected_version=r['expected_version'])
               for r in page['items']]
    body = dict(operation_key='inside', document=dict(mode='inline', deposit_to=books['bank'], date='2026-06-04',
                                                      sources=sources))
    seen = _watch(monkeypatch)
    client.run('deposit post', body, company=COMPANY, reason='bank it', dry_run=True)
    assert seen == [('prepare', False, False)], 'a preview plans outside any transaction'
    seen.clear()
    deposit = client.run('deposit post', body, company=COMPANY, reason='bank it')['deposit']
    # Once, in the plan, inside the write; the save does not prepare again (it used to, twice:
    # the guard comparison and the fresh preparation).
    assert seen == [('prepare', True, True)]
    seen.clear()
    void = dict(deposit=deposit['id'], expected_version=deposit['version'], operation_key='inside-void')
    guard = client.run('deposit void', void, company=COMPANY, reason='never banked', dry_run=True)['dependency_guard']
    seen.clear()
    client.run('deposit void', dict(void, dependency_guard=guard), company=COMPANY, reason='never banked')
    assert seen == [('prepare', True, True)]

    # Reconciliation: one load in the plan, inside the write, and the proof after the write.
    seen.clear()
    opening = client.run('reconcile opening start', dict(operation_key='o', account=books['bank'],
        opening_date='2026-01-01', entered_balance='0.00', references=[],
        evidence=dict(format=1, statement_reference=None, entered_text='Opening')), company=COMPANY, reason='open')['draft']
    assert seen == [('load', True, True), ('load', False, True)]
    seen.clear()
    draft = client.run('reconcile start', dict(operation_key='s', account=books['bank'], statement_date='2026-06-30',
        ending_balance='0.00', opening_draft_id=opening['id']), company=COMPANY, reason='statement')['draft']
    assert seen == [('load', True, True), ('load', False, True)]
    seen.clear()
    client.run('reconcile mark', dict(draft=draft['id'], operation_key='m', expected_version=draft['version'],
                                      all=True, all_action='mark'), company=COMPANY, reason='tick', dry_run=True)
    assert seen == [('load', False, False)]
    seen.clear()
    client.run('reconcile mark', dict(draft=draft['id'], operation_key='m', expected_version=draft['version'],
                                      all=True, all_action='mark'), company=COMPANY, reason='tick')
    assert seen == [('load', True, True), ('load', False, True)]


def test_a_command_that_does_not_opt_in_plans_before_its_transaction(books, monkeypatch):
    cmd = registry.get('customer create')
    assert not getattr(cmd, 'plan_in_write', False)
    original, seen = cmd.plan, []
    def plan(inp, ctx, s):
        seen.append((s.plan_in_write, bool(s.company.write_transaction)))
        return original(inp, ctx, s)
    monkeypatch.setattr(cmd, 'plan', plan)
    books['client'].customer.create(name='Outside the write', company=COMPANY)
    assert seen == [(False, False)]
