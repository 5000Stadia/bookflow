"""Draft-owned captured metadata, with the existing custom value-slot owner.

No writer or independent transaction. The enclosing financial validator proves
this plan against its freshly decoded immutable draft, not caller snapshots.
"""
from dataclasses import dataclass
import json
import sqlalchemy as sa
from bookflow.company import custom_fields as cf, journal_custom_fields as owner, schema as c
from bookflow.company import payment_queries as q
from bookflow.company.deposit_draft_validation import require


@dataclass(frozen=True)
class DraftCustomPlan(owner.JournalCustomFieldPlan):
    manifest_hash: str
    captures_json: str


def complete_patch(connection, captures):
    # A saved complete document does not acquire new defaults after capture.
    values={r['id']:None for r in cf._applicable_definitions(connection,'deposit')}
    values.update({key:None if v.canonical_text is None else cf.typed_value_from_canonical(v.kind,v.canonical_text)
                   for key,v in captures.items()})
    return cf.CustomFieldValuePatch(values)


def prepare(s, identity, previous, manifest, manifest_hash, *, creating):
    connection=s.company.conn
    captures=manifest.header.custom_fields
    patch=complete_patch(connection,captures)
    value_plan=cf.plan_owner_value_patch(connection,record_type='deposit',record_id=identity,
        patch=patch,creating=creating,restore_choice_spelling=True)
    slots=owner._slots(connection,identity,record_type='deposit')
    for mutation in value_plan.mutations:slots[mutation.definition_id]=owner._after(mutation)
    snapshot={}
    for key,capture in captures.items():
        if capture.canonical_text is None:continue
        slot=slots[key]
        snapshot[key]=owner.SnapshotField(definition_id=key,value_id=slot['id'],name=capture.name,kind=capture.kind,
            value=cf.typed_value_from_canonical(capture.kind,capture.canonical_text),canonical_text=capture.canonical_text,
            definition_version=capture.definition_version,position=capture.position,
            choice_id=capture.choice_id,choice_label=capture.choice_label).model_dump(mode='json')
    plan=DraftCustomPlan(value_plan,snapshot,q.canonical(previous),q.canonical(patch.root),False,
        manifest_hash,q.canonical({k:v.model_dump(mode='json') for k,v in captures.items()}))
    validate(s,plan,identity,previous,manifest,manifest_hash,snapshot)
    return plan


def _validate(s, plan, identity, previous, manifest, manifest_hash, snapshot):
    """Exact current slot mutation plus independently retained metadata proof."""
    require(type(plan) is DraftCustomPlan,'draft_custom_plan')
    require(plan.manifest_hash==manifest_hash and plan.captures_json==q.canonical(
        {k:v.model_dump(mode='json') for k,v in manifest.header.custom_fields.items()}),'draft_custom_capture')
    require(plan.previous_json==q.canonical(previous) and not plan.refresh,'draft_custom_previous')
    connection=s.company.conn
    actual=connection.execute(sa.select(c.transaction_revisions.c.custom_fields_snapshot).select_from(
        c.transactions.join(c.transaction_revisions,c.transactions.c.current_revision_id==c.transaction_revisions.c.id)
    ).where(c.transactions.c.id==identity)).scalar_one_or_none()
    require((actual is None and not previous and plan.owner_plan.creating) or
        (actual is not None and json.loads(actual)==previous and not plan.owner_plan.creating),'draft_custom_owner')
    slots=owner._slots(connection,identity,record_type='deposit')
    require(set(previous)=={k for k,v in slots.items() if v['active']},'draft_custom_slots')
    for key,raw in previous.items():
        field=owner.SnapshotField.model_validate(raw)
        require(field.definition_id==key and field.value_id==slots[key]['id'] and
            field.canonical_text==slots[key]['canonical_text'],'draft_custom_previous_slot')
    patch=complete_patch(connection,manifest.header.custom_fields)
    require(plan.patch_json==q.canonical(patch.root),'draft_custom_patch')
    definitions=cf._applicable_definitions(connection,'deposit')
    inserted={m.definition_id:m.row_id for m in plan.owner_plan.mutations if m.operation=='insert'}
    require(len(inserted)==sum(m.operation=='insert' for m in plan.owner_plan.mutations),'draft_custom_bijection')
    ids=iter(inserted[d['id']] for d in definitions if d['id'] in inserted)
    expected=cf.plan_owner_value_patch(connection,record_type='deposit',record_id=identity,patch=patch,
        creating=actual is None,id_factory=lambda:next(ids),restore_choice_spelling=True)
    require(plan.owner_plan==expected,'draft_custom_mutations')
    for mutation in expected.mutations:
        if mutation.operation=='insert':
            require(connection.execute(sa.select(c.custom_field_values.c.id).where(c.custom_field_values.c.id==mutation.row_id)).first() is None,'draft_custom_new_slot')
        slots[mutation.definition_id]=owner._after(mutation)
    captures=manifest.header.custom_fields
    populated={k for k,v in captures.items() if v.canonical_text is not None}
    require(set(snapshot)==populated=={k for k,v in slots.items() if v['active']},'draft_custom_complete')
    require(snapshot==plan.snapshot,'draft_custom_snapshot')
    by_id={d['id']:d for d in definitions}
    for key,raw in snapshot.items():
        capture=captures[key];field=owner.SnapshotField.model_validate(raw);slot=slots[key]
        require(set(raw)==set(owner.SnapshotField.model_fields),'draft_custom_shape')
        require(field.definition_id==key and field.value_id==slot['id'] and field.canonical_text==slot['canonical_text'], 'draft_custom_value_owner')
        require(field.kind==by_id[key]['kind']==capture.kind,'draft_custom_current_kind')
        require((field.name,field.definition_version,field.position,field.canonical_text,field.choice_id,field.choice_label)==
            (capture.name,capture.definition_version,capture.position,capture.canonical_text,capture.choice_id,capture.choice_label),'draft_custom_metadata')
        if capture.kind=='choice':
            owner._choice(connection,key,capture.canonical_text,capture.choice_id)


def validate(s, plan, identity, previous, manifest, manifest_hash, snapshot):
    from bookflow.core.errors import BookflowError
    try:
        _validate(s,plan,identity,previous,manifest,manifest_hash,snapshot)
    except (ValueError,TypeError,KeyError,StopIteration,AttributeError):
        raise BookflowError('E_VALIDATION',details={'reason':'draft_custom_facts'}) from None
