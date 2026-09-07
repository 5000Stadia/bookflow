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


def test_original_combined_page_fields_and_canonical_cursors(client, labels, monkeypatch, tmp_path):
    import hashlib
    import inspect
    import math
    import sqlite3
    import subprocess
    import types
    from pathlib import Path
    from tests.test_row8_journal import database_path

    reference = 'c8e0f8ba34962eb0b8fc7bd26a22a71cede3b083'
    checkout = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(['git', 'show', reference + ':src/bookflow/company/payment_preparation.py'], cwd=checkout)
    old = types.ModuleType('combined_payment_preparation')
    exec(compile(source, reference + '/payment_preparation.py', 'exec'), old.__dict__)
    command = registry.get('payment query')
    registered = command.plan
    # The registry closes over the selected function at registration time.
    # Observe that actual execution seam; patching a module attribute is inert.
    assert inspect.getclosurevars(registered).nonlocals['planner'] is preparation.payment_page
    receipts = []
    calls = {'old': 0, 'new': 0}
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

    def compared(inp, ctx, session):
        expected = old.payment_page(session, inp)
        calls['old'] += 1
        plan = registered(inp, ctx, session)
        calls['new'] += 1
        actual = plan.preview.model_dump(mode='json')
        stripped = {**actual, 'items': [{k: v for k, v in row.items() if k not in ('payer_label', 'method_label')} for row in actual['items']]}
        assert stripped == expected
        assert canonical(stripped) == canonical(expected)
        receipts.append({'input': inp.model_dump(mode='json'), 'old': expected, 'new': actual,
                         'old_canonical': canonical(expected).decode(),
                         'new_without_labels_canonical': canonical(stripped).decode()})
        return plan

    monkeypatch.setattr(command, 'plan', compared)
    before = allrows(client)  # Every old seeded history row plus the new receipts.
    with sqlite3.connect(database_path(client)) as db:
        total = db.execute("SELECT count(*) FROM transactions WHERE type='payment'").fetchone()[0]
    assert total > 25, 'Fixture must exercise continuation at both limits 1 and 25'
    request_count = 0
    coverage = {}
    complete_rows = []
    for limit in (1, 25, 200):
        cursor = None
        rows = []
        start = len(receipts)
        while True:
            request = dict(limit=limit, **({'cursor': cursor} if cursor else {}))
            result = run(client, 'payment query', **request)
            request_count += 1
            # This assertion fails immediately if dispatch bypasses the comparator.
            assert len(receipts) == calls['old'] == calls['new'] == request_count
            assert result == receipts[-1]['new']
            assert result['total_count'] == total
            rows.extend(result['items'])
            cursor = result['next_cursor']
            if cursor is None:
                break
        chain = receipts[start:]
        assert len(chain) == math.ceil(total / limit)
        assert len(rows) == len({row['id'] for row in rows}) == total
        assert chain[0]['input']['cursor'] is None
        for previous, following in zip(chain, chain[1:]):
            assert previous['old']['next_cursor'] == previous['new']['next_cursor'] == following['input']['cursor']
        if limit < total:
            assert len(chain) > 1 and chain[0]['new']['next_cursor']
        complete_rows.append(rows)
        coverage[str(limit)] = {'pages': len(chain), 'rows': len(rows), 'continuations': len(chain)-1}
        empty = run(client, 'payment query', q='absent-captured-payment', limit=limit)
        request_count += 1
        assert len(receipts) == calls['old'] == calls['new'] == request_count
        assert empty == receipts[-1]['new']
        assert empty['items'] == [] and empty['total_count'] == 0 and empty['next_cursor'] is None
    assert complete_rows[0] == complete_rows[1] == complete_rows[2]
    assert len(receipts) == sum(math.ceil(total / limit) + 1 for limit in (1, 25, 200)) > 3
    assert allrows(client) == before
    (tmp_path/'combined-before-after-pages.json').write_text(json.dumps(receipts, indent=2))
    (tmp_path/'combined-before-after-coverage.json').write_text(json.dumps({
        'reference': reference, 'reference_source_sha256': hashlib.sha256(source).hexdigest(),
        'candidate_source_sha256': hashlib.sha256(Path(preparation.__file__).read_bytes()).hexdigest(),
        'calls': calls, 'requests': request_count, 'coverage': coverage,
        'empty_pages': sum(row['new']['total_count'] == 0 for row in receipts),
        'old_seeded_and_new_rows_preserved': True}, indent=2))
