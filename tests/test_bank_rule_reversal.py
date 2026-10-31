"""R186: bank rule runs restore exactly the proposals and rule ids they replaced."""
import pytest

from bookflow import BookflowError
from bookflow.company import bank_rule_reversal, batches
from tests.test_bank_feeds import run, table
from tests.test_bank_rules import imp, rule, contains, SUPPLY


def proposals(client):
    return table(client, 'SELECT id, proposal, rule_id, state, live FROM bank_lines ORDER BY id')


def snapshot(client):
    return (table(client, 'SELECT * FROM bank_lines ORDER BY id'),
            table(client, 'SELECT * FROM batches ORDER BY id'),
            table(client, 'SELECT * FROM batch_effects ORDER BY batch_id, seq'),
            table(client, 'SELECT count(*) FROM audit_events'))


def setup(client):
    rule(client, 'First proposal', contains('REVERSIBLE'), **SUPPLY)
    imported = imp(client, 'Checking', [('2026-10-06', 'REVERSIBLE ONE', '-45.67'),
                                       ('2026-10-07', 'REVERSIBLE TWO', '-9.00')])
    before = proposals(client)
    changed = rule(client, 'Corrected proposal', contains('REVERSIBLE'), priority=50,
                   payee='Central Supply', category='Vehicle Expense')
    applied = run(client, 'bank-rule apply', {'rule': changed['id']})
    return before, applied, imported


def test_exact_plan_and_restore_prior_proposals(client):
    before, applied, _ = setup(client)
    batch = applied['batch_id']
    assert batch and batch in applied['summary'] and applied['next'] == f'batch reverse {batch} --dry-run'
    current = snapshot(client)
    plan = run(client, 'batch reverse', {'batch': batch}, dry_run=True)
    assert snapshot(client) == current
    assert len(plan['steps']) == len(before) == 2
    assert {step['record_id']: step['fields'] for step in plan['steps']} == {
        row[0]: {'proposal': row[1], 'rule_id': row[2]} for row in before}
    assert {s['operation'] for s in plan['steps']} == {'restore bank-line proposal'}
    assert {s['authority'] for s in plan['steps']} == {'bank-line undo'}
    done = run(client, 'batch reverse', {'batch': batch})
    assert done['state'] == 'reversed'
    assert proposals(client) == before


@pytest.mark.parametrize('decision', ['add', 'match', 'ignore'])
def test_decided_line_blocks_whole_batch_and_names_line(client, decision):
    _, applied, _ = setup(client)
    line = applied['lines'][0]['line_id']
    if decision == 'add':
        run(client, 'bank-line add', {'line': line})
    elif decision == 'ignore':
        run(client, 'bank-line ignore', {'line': line, 'ignore_reason': 'other'})
    else:
        from tests.test_bank_feed_reversal import journal
        entry = journal(client, '2026-10-06', '45.67', debit='Office Supplies', credit='Checking')
        run(client, 'bank-line match', {'line': line, 'entry': entry['id']})
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        run(client, 'batch reverse', {'batch': applied['batch_id']})
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert line in caught.value.message
    assert snapshot(client) == before


def test_later_proposals_unwind_newest_first(client):
    before, first, _ = setup(client)
    intermediate = proposals(client)
    next_rule = rule(client, 'Third proposal', contains('REVERSIBLE'), priority=10, **SUPPLY)
    later = run(client, 'bank-rule apply', {'rule': next_rule['id']})
    saved = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        run(client, 'batch reverse', {'batch': first['batch_id']})
    assert caught.value.code == 'E_BATCH_BLOCKED' and later['batch_id'] in caught.value.message
    assert snapshot(client) == saved
    run(client, 'batch reverse', {'batch': later['batch_id']})
    assert proposals(client) == intermediate
    run(client, 'batch reverse', {'batch': first['batch_id']})
    assert proposals(client) == before


def test_interrupted_reversal_resumes_without_repeating_committed_line(client, monkeypatch):
    before, applied, _ = setup(client)
    real = bank_rule_reversal._run
    def interrupt(*args):
        real(*args)
        raise KeyboardInterrupt('stopped after committed inverse')
    import dataclasses
    adapter = batches.ADAPTERS[bank_rule_reversal.KIND]
    monkeypatch.setitem(batches.ADAPTERS, bank_rule_reversal.KIND, dataclasses.replace(adapter, runner=interrupt))
    with pytest.raises(KeyboardInterrupt):
        run(client, 'batch reverse', {'batch': applied['batch_id']})
    monkeypatch.setitem(batches.ADAPTERS, bank_rule_reversal.KIND, adapter)
    done = run(client, 'batch reverse', {'batch': applied['batch_id']})
    assert done['state'] == 'reversed' and done['reversed_now'] == 1
    assert proposals(client) == before
    assert table(client, 'SELECT count(*) FROM batch_effects WHERE batch_id = ?', done['reversal_batch']) == [(2,)]


def test_member_without_inverse_write_grant_cannot_reverse(client, root):
    from tests.conftest import as_user, make_actor
    _, applied, _ = setup(client)
    make_actor(root, 'proposal-reader')
    client.run('membership grant', {'user': 'proposal-reader', 'company': 'Demo Plumbing Co',
                                    'role': 'readonly', 'expected_version': 0})
    reader = as_user(root, 'proposal-reader')
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        run(reader, 'batch reverse', {'batch': applied['batch_id']})
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert all(b['code'] == 'E_PERMISSION' for b in caught.value.details['blockers'])
    assert snapshot(client) == before


def test_effect_failure_rolls_back_proposal_and_batch(client, monkeypatch):
    rule(client, 'First proposal', contains('REVERSIBLE'), **SUPPLY)
    imp(client, 'Checking', [('2026-10-06', 'REVERSIBLE ONE', '-45.67')])
    second = rule(client, 'Second proposal', contains('REVERSIBLE'), priority=10, **SUPPLY)
    before = snapshot(client)
    def fail(*args, **kwargs):
        raise RuntimeError('effect write interrupted')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect write interrupted'):
        run(client, 'bank-rule apply', {'rule': second['id']})
    assert snapshot(client) == before


def test_same_rule_changed_fields_refresh_and_reverse(client):
    first = rule(client, 'Same rule', contains('REVERSIBLE'), **SUPPLY)
    imp(client, 'Checking', [('2026-10-06', 'REVERSIBLE ONE', '-45.67')])
    before = proposals(client)
    run(client, 'bank-rule update', {'rule': first['id'], 'memo': 'Corrected memo'})
    applied = run(client, 'bank-rule apply', {'rule': first['id']})
    assert len(applied['lines']) == 1 and proposals(client) != before
    run(client, 'batch reverse', {'batch': applied['batch_id']})
    assert proposals(client) == before


@pytest.mark.parametrize('batch_decision', [True, False])
def test_undo_decision_then_reverse_proposals(client, batch_decision):
    before, applied, _ = setup(client)
    line = applied['lines'][0]['line_id']
    selector = {'lines': [line]} if batch_decision else {'line': line}
    decision = run(client, 'bank-line ignore', {**selector, 'ignore_reason': 'other'})
    if batch_decision:
        run(client, 'bank-line undo', {'batch': decision['batch_id']})
    else:
        run(client, 'bank-line unmatch', {'line': line})
    run(client, 'batch reverse', {'batch': applied['batch_id']})
    assert proposals(client) == before
