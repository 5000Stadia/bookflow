"""Work custom scopes use independent owner slots and ordinary typed validation."""
import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import custom_fields as cf, schema
from bookflow.core.ids import new_id
from tests.test_row5_custom_fields import _definition
from tests.test_row16_schema_foundation import db, old, document


@pytest.mark.parametrize('scope', ['proposal','estimate','work_order'])
def test_work_scope_values_are_typed_and_destination_owned(db, scope):
    definition = _definition(db.conn,name='Work measure',kind='number',scopes=(scope,),required=True,default='2.50')
    source,_ = document(db,scope)
    destination,_ = document(db,scope)
    first=cf.plan_owner_value_patch(db,record_type=scope,record_id=source,patch={},creating=True)
    cf.apply_owner_value_plan(db,first)
    values=cf.read_owner_values(db,record_type=scope,record_id=source)
    assert values[0]['value'] == '2.5'
    copied=cf.plan_owner_value_patch(db,record_type=scope,record_id=destination,
        patch={definition['id']:values[0]['value']},creating=True)
    cf.apply_owner_value_plan(db,copied)
    destination_values=cf.read_owner_values(db,record_type=scope,record_id=destination)
    assert destination_values[0]['id'] != values[0]['id']
    changed=cf.plan_owner_value_patch(db,record_type=scope,record_id=destination,patch={definition['id']:'9'})
    cf.apply_owner_value_plan(db,changed)
    assert cf.read_owner_values(db,record_type=scope,record_id=source) == values
    with pytest.raises(BookflowError) as caught:
        cf.plan_owner_value_patch(db,record_type=scope,record_id=destination,patch={definition['id']:None})
    assert caught.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError):
        cf.plan_owner_value_patch(db,record_type=scope,record_id=destination,patch={definition['id']:True})
    db.conn.execute(schema.custom_field_defs.update().where(schema.custom_field_defs.c.id==definition['id']).values(active=False))
    retained=cf.plan_owner_value_patch(db,record_type=scope,record_id=source,patch={})
    assert not retained.mutations
    with pytest.raises(BookflowError) as caught:
        cf.plan_owner_value_patch(db,record_type=scope,record_id=source,patch={definition['id']:'3'})
    assert caught.value.code == 'E_INACTIVE_REFERENCE'
    assert cf.read_owner_values(db,record_type=scope,record_id=source)[0]['value'] == '2.5'


def test_scope_and_owner_slots_do_not_alias(db):
    definition=_definition(db.conn,name='Shared work label',scopes=('proposal','estimate','work_order'))
    owner=new_id()
    for scope,value in [('proposal','P'),('estimate','E'),('work_order','W')]:
        plan=cf.plan_owner_value_patch(db,record_type=scope,record_id=owner,
            patch={definition['id']:value},creating=True)
        cf.apply_owner_value_plan(db,plan)
    ids=set()
    for scope,value in [('proposal','P'),('estimate','E'),('work_order','W')]:
        row,=cf.read_owner_values(db,record_type=scope,record_id=owner)
        assert row['value'] == value
        ids.add(row['id'])
    assert len(ids)==3
    proposal_only=_definition(db.conn,name='Proposal only',scopes=('proposal',))
    with pytest.raises(BookflowError) as caught:
        cf.plan_owner_value_patch(db,record_type='estimate',record_id=owner,patch={proposal_only['id']:'no'})
    assert caught.value.code == 'E_RECORD_NOT_FOUND'
    assert cf.plan_owner_value_patch(db,record_type='proposal',record_id=new_id(),patch={}).mutations == ()
