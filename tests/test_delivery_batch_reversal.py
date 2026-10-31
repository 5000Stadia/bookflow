"""Shared delivery reversal is atomic and refuses anything beyond unchanged drafts."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bookflow import BookflowError
from bookflow.company import batches
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_delivery_batch import shop  # noqa: F401
from tests.test_scheduling import member


def draft(shop):
    return shop['run']('delivery batch', {'kind': 'invoice', 'unsent': True}, reason='Draft invoices')


def messages(shop, out):
    return shop['run']('delivery query', {'batch': out['number'], 'status': 'all'})['rows']


def reverse(shop, out, **context):
    return shop['run']('batch reverse', {'batch': out['reversal_batch_id']}, reason='Wrong selection', **context)


def test_delivery_plan_names_exact_messages_and_cancels_whole_run(shop):
    out = draft(shop)
    assert out['reversal_batch_id'] and any(out['reversal_batch_id'] in n for n in out['next_commands'])
    before = messages(shop, out)
    preview = reverse(shop, out, dry_run=True)
    assert len(preview['steps']) == 1
    step = preview['steps'][0]
    assert step['steps'] == [{'operation': 'delivery batch-cancel', 'batch': out['batch_id'],
                              'expected_version': out['version']}]
    assert {m['number'] for m in step['messages']} == set(out['messages'])
    assert all(m['action'] == 'cancel' for m in step['messages'])
    assert step['undrafted'] == 0
    assert messages(shop, out) == before
    assert reverse(shop, out)['state'] == 'reversed'
    assert {m['status'] for m in messages(shop, out)} == {'cancelled'}
    assert shop['run']('delivery batch-show', {'batch': out['batch_id']})['state'] == 'cancelled'


@pytest.mark.parametrize('change, phrase', [('sent', 'sent mail cannot be recalled'),
                                           ('exported', 'exported files cannot be recalled'),
                                           ('released', 'only unreleased drafts'),
                                           ('edited', 'changed since')])
def test_delivery_refusal_names_message_and_changes_nothing(shop, change, phrase):
    out = draft(shop)
    message = out['messages'][0]
    if change == 'sent':
        shop['run']('delivery mark-sent', {'message': message}, reason='Sent by hand')
    elif change == 'exported':
        shop['run']('delivery export', {'messages': [message]}, reason='Exported')
    elif change == 'released':
        shop['run']('delivery send', {'messages': [message]}, reason='Send')
    else:
        shop['run']('delivery update', {'message': message, 'subject': 'Edited'}, reason='Edit')
    before = messages(shop, out)
    for preview in (True, False):
        with pytest.raises(BookflowError) as error:
            reverse(shop, out, dry_run=preview)
        assert error.value.code == 'E_BATCH_BLOCKED'
        assert message in str(error.value.details) and phrase in str(error.value.details)
        assert messages(shop, out) == before
    assert any(m['status'] == 'draft' for m in before)


def test_delivery_reversal_requires_native_cancel_grant(shop):
    out = draft(shop)
    person = SimpleNamespace(root=shop['client'].data_root, client=shop['client'], company=shop['company'])
    _, call = member(person, 'delivery_reader', role='readonly')
    before = messages(shop, out)
    with pytest.raises(BookflowError) as error:
        call('batch reverse', {'batch': out['reversal_batch_id']}, reason='Wrong selection')
    assert error.value.code == 'E_BATCH_BLOCKED'
    assert {b['code'] for b in error.value.details['blockers']} == {'E_PERMISSION'}
    assert messages(shop, out) == before


def test_delivery_effect_failure_rolls_back_batch_and_messages(shop, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('effect failed')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(RuntimeError, match='effect failed'):
        draft(shop)
    assert shop['run']('delivery query', {'status': 'all'})['rows'] == []
    assert shop['run']('delivery batch-query', {'state': 'all'})['rows'] == []
    assert shop['run']('batch query', {'kind': 'delivery-batch'})['items'] == []


def test_delivery_chunked_run_records_all_drafts_and_stops_unfinished_run(shop, monkeypatch):
    from bookflow.company import delivery_batches
    monkeypatch.setattr(delivery_batches, 'CHUNK', 1)
    out = draft(shop)
    assert out['remaining'] > 0
    second = shop['run']('delivery batch', out['next'], reason='Continue')
    assert second['reversal_batch_id'] == out['reversal_batch_id']
    plan = reverse(shop, second, dry_run=True)
    assert {m['number'] for m in plan['steps'][0]['messages']} == set(out['messages'] + second['messages'])
    assert plan['steps'][0]['undrafted'] == second['remaining']
    assert reverse(shop, second)['state'] == 'reversed'
    resumed = shop['run']('delivery batch', {'batch': out['batch_id']}, reason='Continue')
    assert resumed['state'] == 'cancelled' and resumed['messages'] == []


def test_delivery_interrupted_after_inverse_resumes_without_repeating(shop, monkeypatch):
    out = draft(shop)
    adapter = batches.ADAPTERS['delivery-batch']
    def interrupt(*args):
        adapter.runner(*args)
        raise RuntimeError('interrupted after inverse')
    monkeypatch.setitem(batches.ADAPTERS, 'delivery-batch', replace(adapter, runner=interrupt))
    with pytest.raises(RuntimeError, match='interrupted after inverse'):
        reverse(shop, out)
    before = messages(shop, out)
    monkeypatch.setitem(batches.ADAPTERS, 'delivery-batch', adapter)
    done = reverse(shop, out)
    assert done['state'] == 'reversed' and done['reversed_now'] == 0
    assert messages(shop, out) == before
