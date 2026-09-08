"""Actual coordinated source rows, prior to whole-operation registration."""
import copy
import json
import pytest
from pydantic import ValidationError
from bookflow.core.context import Context, Interface
from bookflow.company import deposit_coordinate_persistence as persistence
from bookflow.hub import audit_projection_deposit_coordinate as views
from bookflow.hub import audit_projection_legacy as legacy
from tests.test_deposit_coordinate_persistence import n2, driver, sale, prepare


def test_actual_coordinate_rows_preserve_before_inserted_current(client, sale, driver, n2):
    inp, post, document, payment, receipt, invoice = n2
    ctx = Context.new(Interface.python, 'Coordinate audit witness', reason='Correct deposited payment')
    with driver.session() as session:
        result = persistence.execute(session, ctx, prepare(session, ctx, inp))
        raw = json.loads(session.company.raw.execute(
            'SELECT effect_snapshot FROM deposit_operations WHERE id=?',
            (result.operation_id,),
        ).fetchone()[0])
        before = tuple(session.company.raw.iterdump())
        cursor = session.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?',(result.operation_id,))
        operation_raw = dict(zip((column[0] for column in cursor.description),cursor.fetchone(),strict=True))
        operation = legacy.decode_company_snapshot(producer='deposit coordinate',record_type='deposit_operation',action='create',snapshot=operation_raw)
        assert isinstance(operation, views.CoordinateOperation)
        assert operation.id == result.operation_id
        if operation.effect_snapshot.effect.source.action.kind == 'payment_update':
            for fault in ('key', 'event', 'manifest', 'request_presence'):
                damaged = copy.deepcopy(operation_raw)
                request = json.loads(damaged['request_snapshot'])
                if fault == 'key': damaged['operation_key'] = 'different-coordinate-key'
                elif fault == 'event': damaged['audit_event_id'] = post.current.id
                elif fault == 'manifest': request['item_manifests']['request_sources']['count'] += 1
                else: request['provided_fields'] = []
                damaged['request_snapshot'] = json.dumps(request)
                with pytest.raises(ValidationError): views.CoordinateOperation.model_validate(damaged)
        operation.model_dump(mode='json',warnings='error')
        complete = views.CoordinateOutput.model_validate(raw)
        assert complete.operation_id == result.operation_id
        assert complete.effect.source.action.kind == 'payment_update'
        assert complete.effect.source.payment_effect.current.received_minor_units == 12000
        assert complete.current.revision_bank_total == 19200
        complete.model_dump(mode='json', warnings='error')
        assert_all_captured_fields(complete, raw)
        for fault in RECEIPT_GUARDS:
            assert_independent_receipt_guard(raw, fault)
        for fault in ('current', 'headers', 'source', 'rows', 'operation', 'input'):
            broken = copy.deepcopy(raw)
            if fault == 'current': broken['current']['version'] += 1
            elif fault == 'headers': broken['current_headers'].pop()
            elif fault == 'source': broken['effect']['source']['after_header']['id'] = post.current.id
            elif fault == 'rows': broken['current_source_rows']['posting_lines'].pop()
            elif fault == 'operation': broken['operation_id'] = post.current.id
            else: broken['effect']['source']['action']['input']['payment'] = post.current.id
            with pytest.raises(ValidationError): views.CoordinateOutput.model_validate(broken)
        decoded = []
        for captured in (raw['effect']['source']['before'], raw['effect']['source']['inserted'], raw['current_source_rows']):
            rows = views.CoordinateSourceRows.model_validate(captured)
            output = rows.model_dump(mode='json', by_alias=True)
            assert set(output) == set(captured)
            for table, originals in captured.items():
                assert len(output[table]) == len(originals)
                if originals and 'id' in originals[0]:
                    assert [row['id'] for row in output[table]] == [row['id'] for row in originals]
                # Reuse the established row decoder exactly, without its event tag.
                model = type(getattr(rows, table)[0]).__bases__[0] if originals else None
                for original, projected in zip(originals, output[table], strict=True):
                    expected = model.model_validate(original).model_dump(mode='json', by_alias=True)
                    expected.pop('tag')
                    assert projected == expected
            decoded.append(output)
        assert decoded[0]['applications']
        assert decoded[0]['application_allocations']
        assert decoded[1]['applications'] == []
        assert [(row['kind'], row['amount_minor_units']) for row in decoded[1]['application_allocations']] == [('reversal', 10000), ('allocation', 10000)]
        assert decoded[1]['posting_lines']
        assert sum(row['amount_minor_units'] for row in decoded[0]['applications']) == 10000
        assert sum(row['debit_minor_units'] - row['credit_minor_units'] for row in decoded[1]['posting_lines']) == 0
        assert {h['after']['id']:(h['before']['version'],h['after']['version']) for h in raw['effect']['headers']} == {
            payment['id']:(2,3), receipt['id']:(2,3), invoice['id']:(2,3), post.current.id:(1,2),
        }
        for header in raw['effect']['headers']:
            assert views.CoordinateHeader.model_validate(header).model_dump(mode='json') == header
        for defect in ('unknown_table', 'unknown_row_field', 'injected_tag', 'null_stamp', 'wrong_integer'):
            bad = copy.deepcopy(raw['effect']['source']['before'])
            row = bad['applications'][0]
            if defect == 'unknown_table': bad['future_table'] = []
            elif defect == 'unknown_row_field': row['future_field'] = 'secret'
            elif defect == 'injected_tag': row['tag'] = 'application'
            elif defect == 'null_stamp': row['created_at'] = None
            else: row['amount_minor_units'] = '100'
            with pytest.raises(ValidationError):
                views.CoordinateSourceRows.model_validate(bad)
        assert tuple(session.company.raw.iterdump()) == before


def test_coordinate_source_owner_inventory_is_closed():
    from bookflow.company import deposit_coordinate_models as owners
    from pydantic import BaseModel
    original = {name:model for name,model in vars(owners).items()
                if name.startswith('Coordinate') and name.endswith('Row')
                and isinstance(model, type) and issubclass(model, BaseModel)}
    actual = {name:model for name,model in vars(views).items()
              if name.startswith('Coordinate') and name.endswith('Row')
              and isinstance(model, type) and issubclass(model, BaseModel)}
    assert actual.keys() == original.keys()
    for name, owner in original.items():
        projected = actual[name]
        assert set(projected.model_fields) - {'tag', 'projection_partial'} == set(owner.model_fields)
        assert projected.model_fields['tag'].exclude is True
    assert set(views.CoordinateSourceRows.model_fields) - {'projection_partial'} == set(owners.SourceRows.model_fields)
    for name, field in owners.SourceRows.model_fields.items():
        expected_row = field.annotation.__args__[0].__name__
        assert views.CoordinateSourceRows.model_fields[name].annotation.__args__[0] is actual[expected_row]


def assert_all_captured_fields(value, raw):
    from pydantic import BaseModel
    from bookflow.hub.audit_projection_legacy import Origins, CustomCaptures
    if isinstance(value, BaseModel):
        if type(raw) is str:
            raw = json.loads(raw)
        if isinstance(value, Origins):
            assert value.model_dump(mode='json') == {'values': [dict(field=k, origin=v) for k,v in sorted(raw.items())]}
            return
        if isinstance(value, CustomCaptures):
            assert {row.definition_id for row in value.values} == set(raw)
            for row in value.values: assert_all_captured_fields(row,raw[row.definition_id])
            return
        for key, original in raw.items():
            names = [name for name,field in type(value).model_fields.items() if (field.alias or name) == key]
            assert len(names) == 1, key
            assert_all_captured_fields(getattr(value,names[0]),original)
    elif isinstance(value, (tuple,list)):
        assert len(value) == len(raw)
        for projected, original in zip(value,raw,strict=True):
            assert_all_captured_fields(projected,original)
    elif isinstance(value,dict):
        assert value.keys() == raw.keys()
        for key in raw: assert_all_captured_fields(value[key],raw[key])
    else:
        assert value == raw


@pytest.mark.parametrize('action,bank_total', [('payment_void',7200), ('sales_receipt_void',11200), ('sales_receipt_update',17200), ('direct_bank',7200), ('noop',17200)])
def test_actual_coordinate_other_source_actions(client, sale, driver, n2, action, bank_total):
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    inp, post, document, payment, receipt, invoice = n2
    wire = inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    if action in ('direct_bank','noop'):
        wire['source_action']['input']['amount'] = '100' if action == 'noop' else '120'
        if action == 'direct_bank':
            wire['source_action']['input']['deposit_to'] = client.account.create(name='Coordinate audit direct bank',type='bank',company='Demo Plumbing Co')['id']
            wire['replacement']['document']['sources'] = [dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    elif action == 'payment_void':
        wire['source_action'] = dict(kind=action,payment=payment['id'],expected_version=2,unapply='all_active')
        wire['replacement']['document']['sources'] = [dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    else:
        wire['source_action'] = dict(kind=action,sales_receipt=receipt['id'],expected_version=2) if action.endswith('_void') else dict(kind=action,input=dict(sales_receipt=receipt['id'],expected_version=2,memo='Correct captured receipt memo'))
        wire['replacement']['document']['sources'] = [dict(source_type='payment',source=payment['id'],expected_version=2)]
        if action.endswith('_update'):
            wire['replacement']['document']['sources'].append(dict(source_result=True,source=receipt['id']))
    inp = CoordinateInput.model_validate(wire)
    ctx = Context.new(Interface.python,'Coordinate action audit',reason='Correct deposited source')
    with driver.session() as session:
        result = persistence.execute(session,ctx,prepare(session,ctx,inp))
        raw = json.loads(session.company.raw.execute('SELECT effect_snapshot FROM deposit_operations WHERE id=?',(result.operation_id,)).fetchone()[0])
        before = tuple(session.company.raw.iterdump())
        cursor = session.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?',(result.operation_id,))
        operation_raw = dict(zip((column[0] for column in cursor.description),cursor.fetchone(),strict=True))
        operation = views.CoordinateOperation.model_validate(operation_raw)
        assert operation.id == result.operation_id
        if operation.effect_snapshot.effect.source.action.kind == 'payment_update':
            for fault in ('key', 'event', 'manifest', 'request_presence'):
                damaged = copy.deepcopy(operation_raw)
                request = json.loads(damaged['request_snapshot'])
                if fault == 'key': damaged['operation_key'] = 'different-coordinate-key'
                elif fault == 'event': damaged['audit_event_id'] = post.current.id
                elif fault == 'manifest': request['item_manifests']['request_sources']['count'] += 1
                else: request['provided_fields'] = []
                damaged['request_snapshot'] = json.dumps(request)
                with pytest.raises(ValidationError): views.CoordinateOperation.model_validate(damaged)
        operation.model_dump(mode='json',warnings='error')
        value = views.CoordinateOutput.model_validate(raw)
        assert_all_captured_fields(value,raw)
        assert value.current.revision_bank_total == bank_total
        assert value.effect.source.action.kind == wire['source_action']['kind']
        if action.startswith('sales_receipt'):
            assert value.effect.source.payment_effect is None
        elif action == 'payment_void':
            assert value.effect.source.payment_effect.current.status == 'voided'
            assert value.effect.source.payment_effect.current.effective_received_minor_units == 0
        if action == 'noop':
            assert value.changed is False and value.new_effect is False
        if action == 'direct_bank':
            assert value.effect.source.bank_changes.after
            assert value.effect.source.bank_changes.after[0].signed_debit == 12000
        output = value.model_dump(mode='json',warnings='error')
        assert 'facts_fingerprint' not in output and 'dependency_guard' not in output
        assert 'current_draft' not in output
        assert tuple(session.company.raw.iterdump()) == before


def test_coordinate_receipt_fields_and_disclosure_descriptors():
    from pydantic import TypeAdapter
    from bookflow.company import deposit_coordinate_models as owner
    from bookflow.company import reconciliation_models as bank
    from bookflow.company import payment_outputs, sales_models
    pairs = (
        (views.CoordinateReceiptUpdateInput,sales_models.SalesReceiptUpdateInput),
        (views.CoordinatePaymentUpdate,owner.PaymentUpdateAction),
        (views.CoordinateReceiptUpdate,owner.SalesReceiptUpdateAction),
        (views.CoordinatePaymentVoid,owner.PaymentVoidAction),
        (views.CoordinateReceiptVoid,owner.SalesReceiptVoidAction),
        (views.CoordinatePaymentOutput,payment_outputs.PaymentSourceOutput),
        (views.CoordinateStatementRef,bank.StatementEffectRef),
        (views.CoordinateMovement,bank.MovementKey),
        (views.CoordinateStatementVersion,bank.StatementEffectVersion),
        (views.CoordinateChangedEffects,bank.ChangedEffects),
        (views.CoordinateUnsupportedPopulation,bank.UnsupportedPopulation),
        (views.CoordinateCustomValue,owner.CoordinateCustomValue),
        (views.CoordinateCustomChange,owner.CoordinateCustomChange),
        (views.CoordinateSourceEvidence,owner.SourceEvidence),
        (views.CoordinateIdentity,owner.CoordinateIdentity),
        (views.CoordinateEffect,owner.CoordinateEffect),
        (views.CoordinateOutput,owner.CoordinateOutput),
        (views.CoordinateSourceResult,owner.SourceResult),
        (views.CoordinateReplacementDocument,owner.CoordinateDocument),
        (views.CoordinateDocumentReplacement,owner.DocumentReplacement),
        (views.CoordinateVoidReplacement,owner.VoidReplacement),
    )
    for projected, original in pairs:
        assert set(projected.model_fields)-{'projection_partial'} == set(original.model_fields)
    assert set(views.CoordinateInput.model_fields)-{'projection_partial'} == set(owner.CoordinateInput.model_fields)-{'operation_key'}
    for model, groups in views.REFERENCE_GROUPS.items():
        for fields, _ in groups:
            for field in fields:
                assert field in model.model_fields
                assert TypeAdapter(model.model_fields[field].annotation).validate_python(None) is None
    for (model, field), _ in views.FIELD_REQUIREMENTS.items():
        assert field in model.model_fields
        assert TypeAdapter(model.model_fields[field].annotation).validate_python(None) is None


RECEIPT_GUARDS = ('payment_family', 'operation_presence', 'event_presence',
                  'deposit_target', 'header_contiguous')


def assert_independent_receipt_guard(raw, fault):
    """Detached real receipt faults; exact errors and mutants isolate each clause."""
    views.CoordinateOutput.model_validate(raw)  # unchanged positive control
    broken = copy.deepcopy(raw)
    effect = broken['effect']
    if fault == 'payment_family':
        assert effect['source']['action']['kind'] == 'payment_update'
        assert effect['source']['payment_effect'] is not None
        effect['source']['payment_effect'] = None
        message = 'coordinate source receipt kind differs'
    elif fault in ('operation_presence', 'event_presence'):
        key = 'operation_id' if fault == 'operation_presence' else 'event'
        expected = broken['operation_id'] if key == 'operation_id' else effect['deposit']['audit_event_id']
        assert any(row['physical_id'] == expected for row in effect['identities'])
        effect['identities'] = [row for row in effect['identities'] if row['physical_id'] != expected]
        # No mismatched keyed identity remains to trip the earlier guard.
        assert not [row for row in effect['identities'] if row['owner_kind'] == 'aggregate' and row['logical_key'] == key]
        assert not any(row['physical_id'] == expected for row in effect['identities'])
        message = 'coordinate aggregate identity absent'
    elif fault == 'deposit_target':
        deposit = broken['current']['id']
        assert deposit in effect['target_ids']
        effect['target_ids'].remove(deposit)
        broken['current_headers'] = [row for row in broken['current_headers'] if row['id'] != deposit]
        # Keep the remaining changed/current comparisons valid as well.
        effect['headers'] = [row for row in effect['headers'] if row['after']['id'] != deposit]
        assert {row['id'] for row in broken['current_headers']} == set(effect['target_ids'])
        assert len(broken['current_headers']) == len(effect['target_ids'])
        assert broken['current'] == effect['deposit']['after']
        message = 'coordinate deposit target absent'
    elif fault == 'header_contiguous':
        source = effect['source']['after_header']['id']
        header = next(row for row in effect['headers'] if row['after']['id'] not in
                      (source, broken['current']['id']) and row['before']['version'] > 1)
        assert header['after']['version'] == header['before']['version'] + 1
        header['before']['version'] -= 1
        assert header['after']['version'] == header['before']['version'] + 2
        assert header['after'] == next(row for row in broken['current_headers'] if row['id'] == header['after']['id'])
        assert broken['current'] == effect['deposit']['after']
        message = 'coordinate changed header differs'
    else:
        raise AssertionError(fault)
    with pytest.raises(ValidationError) as caught:
        views.CoordinateOutput.model_validate(broken)
    errors = caught.value.errors()
    assert len(errors) == 1 and errors[0]['loc'] == ()
    assert errors[0]['msg'] == 'Value error, ' + message
    # The helper never mutates the producer's original receipt.
    assert_all_captured_fields(views.CoordinateOutput.model_validate(raw), raw)


def test_inherited_internal_fields_remain_internal_across_view_mro():
    from bookflow.hub import audit_projection_legacy as legacy
    models = {value for module in (legacy, views) for value in vars(module).values()
              if isinstance(value, type) and issubclass(value, legacy.View)}
    assert views.CoordinateRequest in models
    assert legacy.DepositAuditRequest._internal <= views.CoordinateRequest._internal
    for model in models:
        for base in model.__mro__[1:]:
            inherited = base.__dict__.get('_internal', frozenset())
            assert inherited & model.model_fields.keys() <= model._internal, (model.__name__, base.__name__)
