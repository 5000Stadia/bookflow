"""Actual coordinated source rows, prior to whole-operation registration."""
import copy
import json
import pytest
from pydantic import ValidationError
from bookflow.core.context import Context, Interface
from bookflow.company import deposit_coordinate_persistence as persistence
from bookflow.hub import audit_projection_deposit_coordinate as views
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
