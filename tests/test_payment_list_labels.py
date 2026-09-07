"""Captured list facts, bounded disclosure, and complete-profile read failures."""
from copy import deepcopy
import json
import pytest

from bookflow import BookflowError
from bookflow.company import payment_preparation as preparation, payment_authority
from bookflow.core import registry
from tests.test_service_sales_lifecycle import COMPANY
from tests.test_payment_gate_b_oracles import allrows


def run(client, command, **args):
    return client.run(command, args, company=COMPANY)


@pytest.fixture
def labels(client):
    payer = run(client, 'customer create', name='Captured Straße <payer> & 東京 ' + 'long captured payer ' * 6)['id']
    method = run(client, 'payment-method create', name='Captured <cash> & 東京 ' + 'long captured method ' * 6, kind='cash')['id']
    payments = [run(client, 'payment receive', customer=payer, date='2026-06-02', amount='1.00',
                    payment_method=method, number=f'LABEL-{i:03}', operation_key=f'label-{i}') for i in range(26)]
    return payer, method, payments


def pages(client, **args):
    rows = []
    while True:
        page = run(client, 'payment query', **args)
        rows.extend(page['items'])
        if page['next_cursor'] is None:
            assert len(rows) == page['total_count']
            return rows
        args['cursor'] = page['next_cursor']


def assert_shows(client, rows):
    for row in rows:
        shown = run(client, 'payment show', payment=row['id'])
        profile = shown['revision']['profile']
        assert (row['payer_label'], row['method_label']) == (profile['payer']['label'], profile['payment_method']['label'])
        assert row['version'] == shown['version']
        assert row['status'] == shown['status']
        assert row['received_minor_units'] == shown['current']['received_minor_units']
        assert row['applied_minor_units'] == shown['current']['applied_minor_units']
        assert row['unapplied_minor_units'] == shown['current']['available_minor_units']


def test_captured_labels_pages_renames_correction_void_and_all_history_preserved(client, labels):
    payer, method, paid = labels
    before = allrows(client)  # Includes all seeded history, not just these new receipts.
    expected = pages(client, customer=payer, limit=200)
    assert len(expected) == 26
    for limit in (1, 25):
        assert pages(client, customer=payer, limit=limit) == expected
    assert_shows(client, expected)
    assert pages(client, q='no such captured remittance', limit=200) == []
    assert allrows(client) == before
    first = run(client, 'payment query', customer=payer, limit=1)
    run(client, 'customer update', customer=payer, expected_version=1, name='Renamed payer')
    run(client, 'payment-method update', payment_method=method, expected_version=1, name='Renamed method')
    with pytest.raises(BookflowError) as caught:
        run(client, 'payment query', customer=payer, limit=1, cursor=first['next_cursor'])
    assert caught.value.code == 'E_QUERY_STALE'
    assert pages(client, customer=payer, limit=200) == expected
    other = run(client, 'payment-method create', name='Corrected receipt method', kind='check')['id']
    client.run('payment update', dict(payment=paid[0]['id'], expected_version=1,
        payment_method=other, operation_key='label-correction'), company=COMPANY, reason='Correct receipt method')
    client.run('payment void', dict(payment=paid[1]['id'], expected_version=1,
        operation_key='label-void'), company=COMPANY, reason='Cancel receipt')
    before = allrows(client)
    current = pages(client, customer=payer, limit=200)
    assert_shows(client, current)
    assert next(row for row in current if row['id'] == paid[0]['id'])['method_label'] == 'Corrected receipt method'
    assert allrows(client) == before


def test_batch_authority_precedes_only_selected_decodes(client, labels, monkeypatch):
    payer, _, _ = labels
    calls = []
    authorize, decode = payment_authority.authorize, preparation._payment_query_labels
    def authorized(session, ids):
        calls.append(('authorize', tuple(ids)))
        return authorize(session, ids)
    def decoded(snapshot, **ids):
        assert calls and calls[0][0] == 'authorize'
        assert ids['payment_id'] in calls[0][1]
        calls.append(('decode', ids['payment_id']))
        return decode(snapshot, **ids)
    monkeypatch.setattr(payment_authority, 'authorize', authorized)
    monkeypatch.setattr(preparation, '_payment_query_labels', decoded)
    for limit in (1, 25, 200):
        calls.clear()
        page = run(client, 'payment query', customer=payer, limit=limit)
        assert len(calls) == len(page['items']) + 1
        assert set(calls[0][1]) == {row['id'] for row in page['items']}
    calls.clear()
    assert run(client, 'payment query', q='absent-label-receipt')['items'] == []
    assert calls == []
    def denied(*args):
        raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(payment_authority, 'authorize', denied)
    with pytest.raises(BookflowError) as caught:
        run(client, 'payment query', customer=payer, limit=1)
    assert caught.value.code == 'E_PERMISSION' and calls == []


def test_complete_profile_failure_boundary_and_valid_empty(client):
    payment = run(client, 'payment query', limit=1)['items'][0]
    shown = run(client, 'payment show', payment=payment['id'])
    profile = shown['revision']['profile']
    ids = dict(payment_id=payment['id'], revision_id=shown['revision']['id'])
    decode = preparation._payment_query_labels
    assert decode(json.dumps(profile), **ids) == (profile['payer']['label'], profile['payment_method']['label'])
    empty = deepcopy(profile)
    empty['payer']['label'] = empty['payment_method']['label'] = ''
    assert decode(json.dumps(empty), **ids) == ('', '')
    invalid = ['{secret', 'null', '[]']
    for name in ('payer', 'payment_method'):
        for value in (None, 3, {}, []):
            bad = deepcopy(profile); bad[name]['label'] = value
            invalid.append(json.dumps(bad))
        bad = deepcopy(profile); del bad[name]['label']; invalid.append(json.dumps(bad))
    for change in (lambda p: p.update(secret='never disclose'),
                   lambda p: p.update(preferences={}),
                   lambda p: p['ar_account'].update(version='1'),
                   lambda p: p.update(lineage=None)):
        bad = deepcopy(profile); change(bad); invalid.append(json.dumps(bad))
    for snapshot in invalid:
        with pytest.raises(BookflowError) as caught:
            decode(snapshot, **ids)
        assert caught.value.to_dict() == {'code': 'E_PAYMENT_PROFILE_INVALID',
            'message': 'Stored payment profile is invalid.', 'details': dict(ids, field='profile_snapshot')}
    with pytest.raises(TypeError):
        decode(object(), **ids)


def test_query_only_error_and_required_label_contract():
    registry.load_all()
    query = registry.get('payment query')
    assert 'E_PAYMENT_PROFILE_INVALID' in query.error_codes
    for name in ('payment invoices', 'payment suggest', 'payment calculate', 'payment show'):
        assert 'E_PAYMENT_PROFILE_INVALID' not in registry.get(name).error_codes
    from bookflow.company.payment_outputs import PaymentSummaryOutput
    from bookflow.adapters.http.app import STATUS
    for name in ('payer_label', 'method_label'):
        assert PaymentSummaryOutput.model_fields[name].annotation is str
        assert PaymentSummaryOutput.model_fields[name].is_required()
    assert STATUS['E_PAYMENT_PROFILE_INVALID'] == 500
    from bookflow.adapters.mcp.catalog import command_help
    discovery = command_help('payment query', view='output_schema')
    schema = discovery['output_schema']['$defs']['PaymentSummaryOutput']
    assert {'payer_label', 'method_label'} <= set(schema['required'])
    assert 'E_PAYMENT_PROFILE_INVALID' in discovery['error_codes']


def test_original_combined_page_fields_and_canonical_cursors(client, monkeypatch, tmp_path):
    import subprocess
    import types
    from pathlib import Path
    source = subprocess.check_output(['git', 'show', 'c8e0f8ba34962eb0b8fc7bd26a22a71cede3b083:src/bookflow/company/payment_preparation.py'], cwd=Path(__file__).resolve().parents[1], text=True)
    old = types.ModuleType('combined_payment_preparation')
    exec(compile(source, 'c8e0f8b/payment_preparation.py', 'exec'), old.__dict__)
    current = preparation.payment_page
    receipts = []
    def compared(session, inp):
        expected = old.payment_page(session, inp)
        actual = current(session, inp)
        stripped = {**actual, 'items': [{k:v for k,v in row.items() if k not in ('payer_label','method_label')} for row in actual['items']]}
        assert stripped == expected
        assert json.dumps(stripped, sort_keys=True, separators=(',',':')) == json.dumps(expected, sort_keys=True, separators=(',',':'))
        receipts.append({'old':expected, 'new':actual})
        return actual
    monkeypatch.setattr(preparation, 'payment_page', compared)
    before = allrows(client)
    for limit in (1,25,200):
        pages(client, limit=limit)
    pages(client, q='absent-captured-payment', limit=1)
    assert allrows(client) == before
    (tmp_path/'combined-before-after-pages.json').write_text(json.dumps(receipts, indent=2))
