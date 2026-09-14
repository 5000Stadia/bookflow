"""Captured edit baselines and purchase-owned register rows, without new posting rules."""
from pathlib import Path

import pytest

from bookflow.adapters.workbench import forms, purchases
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part
from tests.payment_raw_evidence import database


@pytest.mark.parametrize('noun', ['check', 'card-charge'])
def test_saved_purchase_projection_preserves_grids_and_amount_basis(books, noun):
    run = books['run']
    item = _inventory_part(books)
    funding = books['bank'] if noun == 'check' else run('account create',
        dict(name='Purchase card', type='credit_card'))['id']
    cls = run('class create', {'name': 'Purchased parts'})['id']
    raw = dict(account=funding, date='2017-03-03', amount='17.17',
        pay_to=dict(name_type='vendor', name_id=books['vendor']),
        expenses=[dict(account=books['freight'], amount='4.83', memo='Freight')],
        items=[dict(item=item, quantity='0.5', unit_cost='12.34', class_id=cls,
                    customer=books['customer'], billable=True),
               dict(item=item, quantity='0.5', amount='6.17')])
    if noun == 'check':
        raw['number'] = 'PAPER-008'
    posted = run(noun + ' post', raw, reason='Receive paid parts')
    values = purchases.editable_values(posted)
    assert values['amount'] == '17.17'
    assert values['pay_to'] == raw['pay_to']
    assert values['date'] == '2017-03-03'
    assert values['items'][0]['unit_cost'] == '12.34'
    assert 'amount' not in values['items'][0]
    assert values['items'][1]['amount'] == '6.17'
    assert 'unit_cost' not in values['items'][1]
    assert values['items'][0]['class_id'] == cls
    assert values['items'][0]['customer'] == books['customer']
    assert values['items'][0]['billable'] is True
    assert values['items'][1]['class_mode'] == 'none'
    assert [r['line_id'] for r in values['items']] == [r['line_id'] for r in posted['document']['items']]
    if noun == 'check':
        assert values['number'] == 'PAPER-008'
    else:
        assert 'number' not in values
    cmd = registry.get(noun + ' update')
    form = {'f:memo': 'Header only'}
    for grid in ('items', 'expenses'):
        form.update(forms.collection_attempt(cmd.input_model.model_fields[grid].annotation, grid, values[grid]))
    patch, _, _ = forms.translate(cmd, form, values)
    assert 'items' not in patch and 'expenses' not in patch
    assert patch['memo'] == 'Header only'
    run('item update', {'item': item, 'purchase_description': 'New master wording', 'cost': '99.00'})
    selector = 'check' if noun == 'check' else 'card_charge'
    edited = run(noun + ' update', {selector: posted['id'], **patch}, reason='Correct header')
    assert edited['document']['items'] == posted['document']['items']
    old = run(noun + ' show', {selector: posted['id'], 'revision_number': 1})
    assert old['document'] == posted['document']
    rows = run('register query', dict(account=funding, date_from='2017-01-01', date_to='2017-12-31'))['rows']
    assert {r['purchase_noun'] for r in rows if r['transaction_id'] == posted['id']} == {noun}
    # Matching bank/expense legs do not make an unrelated journal a purchase.
    plain = run('register post', dict(account=funding, date='2017-03-03', amount='1.00',
        direction='decrease' if noun == 'check' else 'increase', category=books['freight']), reason='Ordinary register entry')
    rows = run('register query', dict(account=funding, date_from='2017-01-01', date_to='2017-12-31'))['rows']
    assert {r['purchase_noun'] for r in rows if r['transaction_id'] == plain['id']} == {None}


@pytest.mark.parametrize('noun', ['check', 'card-charge'])
def test_item_edit_stale_retry_and_late_failure_are_atomic(books, noun, monkeypatch):
    from bookflow.company import inventory_effects
    run = books['run']
    funding = books['bank'] if noun == 'check' else run('account create',
        dict(name='Atomic card', type='credit_card'))['id']
    posted = run(noun + ' post', dict(account=funding, date='2017-03-03', amount='6.17',
        items=[dict(item=_inventory_part(books), quantity='0.5', unit_cost='12.34')]), reason='Purchase')
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    selector = 'check' if noun == 'check' else 'card_charge'
    row = purchases.editable_values(posted)['items'][0]
    correction = {selector: posted['id'], 'expected_version': posted['version'],
                  'items': [{**row, 'quantity': '1', 'unit_cost': '6.17'}]}
    before = database(path)
    reached = []
    def fail_after_journal(applied, stock, ctx, session, **kwargs):
        reached.append(applied.output.revision.id)
        assert session.company.write_transaction
        assert applied.output.revision.id != posted['revision']['id']
        raise RuntimeError('Injected failure before stock settlement')
    with monkeypatch.context() as scoped:
        scoped.setattr(inventory_effects, 'settle', fail_after_journal)
        with pytest.raises(RuntimeError, match='Injected failure'):
            run(noun + ' update', correction, reason='Correct quantity', idempotency_key='edit-items')
    assert len(reached) == 1
    assert database(path) == before
    corrected = run(noun + ' update', correction, reason='Correct quantity', idempotency_key='edit-items')
    after = database(path)
    assert after != before
    assert corrected['document']['items'][0]['quantity'] == '1'
    with pytest.raises(BookflowError) as failed:
        run(noun + ' update', {selector: posted['id'], 'expected_version': posted['version'],
            'memo': 'Competing saved edit'}, reason='Other editor')
    assert failed.value.code == 'E_VERSION_CONFLICT'
    assert database(path) == after
    replay = run(noun + ' update', correction, reason='Correct quantity', idempotency_key='edit-items')
    assert replay['idempotent_replay'] and replay['revision'] == corrected['revision']
    assert database(path) == after
    void_input = {selector: posted['id'], 'expected_version': corrected['version']}
    voided = run(noun + ' void', void_input, reason='Cancel purchase', idempotency_key='void-items')
    after_void = database(path)
    replay = run(noun + ' void', void_input, reason='Cancel purchase', idempotency_key='void-items')
    assert replay['idempotent_replay'] and replay['status'] == voided['status'] == 'voided'
    assert database(path) == after_void
