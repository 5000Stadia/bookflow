"""Shared POS tap boxes: every edit needs pos.layout; reads need pos.sell."""
from bookflow.company import pos_layout as layout, pos_layout_models as m, pos_layer
from bookflow.core.registry import command, Plan, TransferDescriptor, Touched
from bookflow.commands import attachment_cmds as files
from bookflow.commands.item_picture_cmds import PictureSetInput as ItemPictureSetInput, PictureGetInput
from pydantic import Field, field_validator

_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_FEATURE_DISABLED',
           'E_INACTIVE_REFERENCE', 'E_IO', 'E_VALUE_RANGE', 'E_DB_BUSY']
_OFF = ' Point of sale must be enabled. Every write checks the layout revision from pos-box list.'


def plan_list(inp, ctx, s):
    return Plan(layout.list_layout(s))


def plan_create(inp, ctx, s):
    return layout.plan_create(s, ctx, inp)


def plan_update(inp, ctx, s):
    return layout.plan_update(s, ctx, inp)


def plan_move(inp, ctx, s):
    return layout.plan_move(s, ctx, inp)


def plan_remove(inp, ctx, s):
    return layout.plan_remove(s, ctx, inp)


def plan_view(inp, ctx, s):
    return layout.plan_view(s, ctx, inp)


def plan_seed(inp, ctx, s):
    return layout.plan_seed(s, ctx, inp)


box_list = command('pos-box list', scope='company', capability='pos.sell', required_role='member',
    input_model=m.ListInput, output_model=m.Layout, writes=set(),
    description='Read all POS boxes, the home style and the layout revision. Before the first edit this returns '
        'the category starting layout without writing; its IDs remain stable until it is saved. Needs pos.sell.',
    error_codes=['E_FEATURE_DISABLED'])(plan_list)

_DEFINITIONS = (
    ('create', m.CreateInput, plan_create, 'Add an item box or named group, optionally inside a group. Items may '
     'appear in several groups. Item pictures come from inventory; group pictures name image attachments.'),
    ('update', m.UpdateInput, plan_update, 'Rename or colour a box, or choose or clear a group picture. '
     'An omitted field stays unchanged. This never changes the inventory item.'),
    ('move', m.MoveInput, plan_move, 'Move a box to Home or a group and its zero-based position. Reorders siblings; '
     'refuses moving a group into itself or its descendants.'),
    ('remove', m.TargetInput, plan_remove, 'Remove a box and, for a group, all its descendant boxes. '
     'Never removes an inventory item or its picture.'),
    ('view', m.ViewInput, plan_view, 'Save the default style for a group or Home: block, list, compact or photo. '
     'All registers and devices see the saved style after reloading.'),
    ('seed', m.SeedInput, plan_seed, 'Save the initial layout from item categories or start empty. '
     'Only available before the first edit; an empty saved layout never seeds itself again.'),
)
for _verb, _model, _planner, _description in _DEFINITIONS:
    _cmd = command('pos-box ' + _verb, scope='company', capability='pos.layout', required_role='admin',
        input_model=_model, output_model=m.Layout, writes={'company'}, accepts_idempotency_key=True,
        positional=['pos_box'] if _verb in ('update', 'move', 'remove') else [],
        description=_description + ' Needs pos.layout.' + _OFF, error_codes=_ERRORS)(_planner)
    _cmd.applier(layout.apply)


class PictureSetInput(m.TargetInput):
    original_filename: str = Field(min_length=1, max_length=255, description='Basename of the uploaded image.')
    media_type: str = Field('application/octet-stream', max_length=127,
                           description='The image bytes decide the stored type.')

    @field_validator('original_filename')
    @classmethod
    def filename(cls, value):
        return ItemPictureSetInput.filename(value)


def prepare_set(inp, ctx, s):
    from bookflow.company import item_pictures as pics
    from bookflow.core.transfers import TransferPreparation
    pos_layer.require_enabled(s)
    files._pending(s)
    _info, rows, _before = layout.begin(s, ctx, inp.expected_version)
    layout._group(rows, inp.pos_box)
    store, limit = files._store_limit(s)
    return TransferPreparation(store, min(limit, pics.PICTURE_MAX_BYTES))


def plan_picture_set(inp, ctx, s):
    import sqlalchemy as sa
    from bookflow.company import schema as c, item_pictures as pics
    from bookflow.core.clock import now_iso
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    resource = files._resource(s, prepare_set(inp, ctx, s))
    kind = pics.require_image(resource.head, resource.info.size_bytes, field='picture', limit=pics.PICTURE_MAX_BYTES)
    info, rows, before = layout.begin(s, ctx, inp.expected_version)
    group = layout._group(rows, inp.pos_box)
    found = s.company.conn.execute(sa.select(c.attachments).where(
        c.attachments.c.sha256 == resource.info.sha256)).mappings().first()
    attachment = dict(found) if found else dict(id=new_id(), **common(s.actor.id, ctx.interface.value),
        sha256=resource.info.sha256, size_bytes=resource.info.size_bytes, original_filename=inp.original_filename,
        media_type=kind, uploaded_by=s.actor.id, uploaded_at=now_iso(), collected_at=None)
    previous_attachment = dict(attachment) if found else None
    changed = bool(found and (attachment['collected_at'] is not None or attachment['media_type'] != kind))
    if changed:
        attachment.update(collected_at=None, media_type=kind, version=attachment['version'] + 1,
            updated_at=now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    group['picture_attachment_id'] = attachment['id']
    plan = layout._finish(s, ctx, info, rows, before, extra_pictures={attachment['id']: attachment})
    plan.data.update(attachment=attachment, previous_attachment=previous_attachment, changed_attachment=changed)
    return plan


def apply_picture_set(plan, ctx, s):
    from bookflow.company import schema as c, attachment_store
    attachment_store.publish(s.transfer.store, s.transfer.staged)
    a, old = plan.data['attachment'], plan.data['previous_attachment']
    touched = []
    if old is None:
        s.company.conn.execute(c.attachments.insert().values(**a))
        touched.append(Touched('attachment', a['id'], 'create', None, a['version'], a, db='company'))
    elif plan.data['changed_attachment']:
        s.company.conn.execute(c.attachments.update().where(c.attachments.c.id == a['id']).values(**a))
        touched.append(Touched('attachment', a['id'], 'update', old['version'], a['version'], a, old, db='company'))
    applied = layout.apply(plan, ctx, s)
    applied.touched[:0] = touched
    return applied


picture_set = command('pos-box picture set', scope='company', capability='pos.layout', required_role='admin',
    input_model=PictureSetInput, output_model=m.Layout, writes={'company'}, accepts_idempotency_key=True,
    positional=['pos_box'], transfer=TransferDescriptor('input', prepare_set), error_codes=_ERRORS,
    description='Upload a JPEG, PNG or WebP as a group picture. Item boxes share their inventory item picture. '
                'Needs pos.layout.' + _OFF)(plan_picture_set)
picture_set.applier(apply_picture_set)


def prepare_get(inp, ctx, s):
    import sqlalchemy as sa
    from bookflow.company import schema as c, attachment_store
    from bookflow.core.errors import BookflowError
    from bookflow.core.transfers import TransferPreparation
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)
    files._pending(s)
    shown = s.company.conn.execute(sa.select(c.pos_boxes.c.id).where(
        c.pos_boxes.c.picture_attachment_id == inp.attachment).limit(1)).scalar()
    if not shown:
        raise BookflowError('E_RECORD_NOT_FOUND', 'No POS group shows this picture.',
                            {'record_type': 'attachment', 'record_id': inp.attachment})
    row = files._get(s, 'attachment', inp.attachment)
    files._available(s, row)
    store, limit = files._store_limit(s)
    return TransferPreparation(store, limit, attachment_store.BodyInfo(row['sha256'], row['size_bytes']),
                               {'original_filename': row['original_filename'], 'media_type': row['media_type']})


def plan_picture_get(inp, ctx, s):
    files._resource(s, prepare_get(inp, ctx, s))
    return Plan(files._out(s, files._get(s, 'attachment', inp.attachment), files.AttachmentOut))


picture_get = command('pos-box picture get', scope='company', capability='pos.sell', required_role='member',
    input_model=PictureGetInput, output_model=files.AttachmentOut, positional=['attachment'],
    transfer=TransferDescriptor('output', prepare_get),
    error_codes=['E_RECORD_NOT_FOUND', 'E_FEATURE_DISABLED', 'E_IO', 'E_DB_BUSY', 'E_VALUE_RANGE'],
    description='Download a picture currently shown by a POS group. Needs pos.sell; unrelated attachments '
                'are never available through this command.')(plan_picture_get)
