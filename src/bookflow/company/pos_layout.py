"""Ordered POS tap boxes. Reads compute an unsaved seed; only authorized writes persist it.

Every edit compares the layout revision as well as updating affected box revisions. This covers reorder, move,
subtree removal and first-save races without changing the company settings' optimistic version.
"""
from __future__ import annotations

import hashlib
from ulid import ULID
import sqlalchemy as sa

from bookflow.company import schema as c, pos_layer
from bookflow.company import pos_layout_models as m
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

BOX_TYPES = ('service', 'inventory_part', 'non_inventory_part', 'other_charge', 'inventory_assembly', 'group')


def invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def _seed_id(key):
    return str(ULID.from_bytes(hashlib.sha256(('bookflow:pos:seed:' + key).encode()).digest()[:16]))


def seed_rows(s):
    """Same category tree and ordering as increment 5, with stable IDs for the first authorized edit."""
    cats = {r.id: r.full_name for r in s.company.conn.execute(sa.select(c.item_categories.c.id,
                                                                 c.item_categories.c.full_name))}
    items = s.company.conn.execute(sa.select(c.items).where(c.items.c.active.is_(True),
        c.items.c.sales_enabled.is_(True), c.items.c.type.in_(BOX_TYPES))).mappings().all()
    rows, groups = [], {}

    def group(path):
        if path not in groups:
            parent_path = path.rsplit(':', 1)[0] if ':' in path else None
            parent = group(parent_path) if parent_path else None
            key = _seed_id('group:' + path)
            groups[path] = key
            rows.append(dict(id=key, parent_id=parent, position=0, kind='group', item_id=None,
                label=path.split(':')[-1], colour=None, picture_attachment_id=None, view_style='block', version=1))
        return groups[path]

    for item in sorted(items, key=lambda r: r['full_name'].casefold()):
        if item['type'] == 'other_charge' and item['price_minor_units'] is None:
            continue
        category = cats.get(item['category_id'])
        parent = group(category) if category else None
        rows.append(dict(id=_seed_id('item:' + item['id']), parent_id=parent, position=0, kind='item',
            item_id=item['id'], label=None, colour=None, picture_attachment_id=None, view_style='block', version=1))
    # Stable sort retains the full-name order of items, while groups come first alphabetically.
    rows.sort(key=lambda row: (row['kind'] != 'group', (row['label'] or '').casefold()))
    counts = {}
    for row in rows:
        parent = row['parent_id']
        row['position'] = counts.get(parent, 0)
        counts[parent] = row['position'] + 1
    return rows


def state(s):
    info = s.company.conn.execute(sa.select(c.company_info.c.id, c.company_info.c.pos_layout_version,
                                            c.company_info.c.pos_home_view_style)).mappings().one()
    version = info['pos_layout_version']
    rows = ([dict(r) for r in s.company.conn.execute(sa.select(c.pos_boxes).order_by(
        c.pos_boxes.c.parent_id, c.pos_boxes.c.position, c.pos_boxes.c.id)).mappings()] if version else seed_rows(s))
    return dict(info), rows


def output(s, info, rows, extra_pictures=None):
    wanted = {r['picture_attachment_id'] for r in rows if r['picture_attachment_id']}
    pictures = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(c.attachments).where(
        c.attachments.c.id.in_(wanted))).mappings()} if wanted else {}
    pictures.update(extra_pictures or {})
    boxes = []
    for row in rows:
        pic = pictures.get(row['picture_attachment_id'])
        shown = m.Picture(attachment_id=pic['id'], sha256=pic['sha256'], media_type=pic['media_type'],
            size_bytes=pic['size_bytes'], url=f"/c/{s.company_row['id']}/pos/picture/{pic['id']}/{pic['sha256']}") if pic else None
        boxes.append(m.Box(id=row['id'], parent=row['parent_id'], position=row['position'], kind=row['kind'],
            item=row['item_id'], label=row['label'], colour=row['colour'], picture=shown,
            view_style=row['view_style'], version=row['version']))
    return m.Layout(items=boxes, home_view_style=info['pos_home_view_style'], version=info['pos_layout_version'],
                    initialized=bool(info['pos_layout_version']))


def list_layout(s):
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)
    return output(s, *state(s))


def _box(rows, key):
    row = next((r for r in rows if r['id'] == key), None)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_box', 'record_id': key})
    return row


def _group(rows, key):
    if key is None:
        return None
    row = _box(rows, key)
    if row['kind'] != 'group':
        raise invalid('parent', 'must name a group, not an item box')
    return row


def _picture(s, key):
    if key is not None:
        from bookflow.commands import attachment_cmds as files, item_picture_cmds as pics
        files._pending(s)
        picture = files._get(s, 'attachment', key)
        pics._head(s, picture, pics.pics.PICTURE_MAX_BYTES, 'picture_attachment')
        return picture['id']
    return None


def conflict(s, record_id, expected_version):
    """Explain a stale layout using its own audit revision, including box edits in the same events.

    Layout revision changes are serialization guards, not user-facing fields. A reorder is therefore reported
    as position/parent, and a style change as home_view_style or view_style, with the latest writer's provenance.
    """
    from bookflow.core import audit, versioning
    from bookflow.hub.users import user_names
    current_version = s.company.conn.execute(sa.select(c.company_info.c.pos_layout_version).where(
        c.company_info.c.id == record_id)).scalar_one()
    writer = versioning.current_writer_from_entries(s.company, 'pos_layout', record_id, current_version)
    names = user_names(s, {key for key in (writer.updated_by, writer.on_behalf_of) if key}) if writer else {}
    entries = c.audit_entries
    events = sa.select(entries.c.event_id).where(entries.c.record_type == 'pos_layout',
        entries.c.record_id == record_id, entries.c.version_after > expected_version,
        entries.c.version_after <= current_version)
    changed = set()
    public_fields = {'parent_id': 'parent', 'item_id': 'item', 'picture_attachment_id': 'picture_attachment',
                     'pos_home_view_style': 'home_view_style'}
    for entry in s.company.conn.execute(sa.select(entries).where(entries.c.event_id.in_(events),
            entries.c.record_type.in_(('pos_box', 'pos_layout')))).mappings():
        before, after = audit.decode_snapshot(entry['before']), audit.decode_snapshot(entry['after'])
        if entry['record_type'] == 'pos_box' and (before is None or after is None):
            changed.add('boxes')
            continue
        before, after = before or {}, after or {}
        changed.update(public_fields.get(key, key) for key in set(before) | set(after)
                       if key not in versioning.BOOKKEEPING | {'pos_layout_version'}
                       and before.get(key) != after.get(key))
        if entry['record_type'] == 'pos_layout' and before.get('pos_layout_version') == 0:
            changed.add('initialized')
    fields = sorted(changed)
    details = dict(record_type='pos_layout', record_id=record_id, expected_version=expected_version,
        current_version=current_version, changed_fields=fields, show_command='pos-box list',
        updated_by=writer.updated_by if writer else None,
        updated_by_name=names.get(writer.updated_by) if writer else None,
        updated_on_behalf_of=writer.on_behalf_of if writer else None,
        updated_on_behalf_of_name=names.get(writer.on_behalf_of) if writer else None,
        updated_via=writer.updated_via if writer else None,
        updated_at=writer.at if writer else None,
        seconds_since_update=versioning._seconds_since(writer.at if writer else None))
    return BookflowError('E_VERSION_CONFLICT', message=versioning._conflict_message(details, fields) +
                        ' Reload with pos-box list before editing the layout.', details=details)


def begin(s, ctx, expected_version):
    pos_layer.require_enabled(s)
    info, rows = state(s)
    if expected_version != info['pos_layout_version']:
        raise conflict(s, info['id'], expected_version)
    before = [dict(r) for r in rows] if expected_version else []
    for row in rows:
        if 'created_at' not in row:
            row.update(common(s.actor.id, ctx.interface.value, clock.now_iso()))
    return info, rows, before


def _finish(s, ctx, info, rows, before, *, extra_pictures=None):
    old = {r['id']: r for r in before}
    for row in rows:
        prev = old.get(row['id'])
        if prev and row != prev:
            row.update(version=prev['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                       updated_via=ctx.interface.value)
    after_info = dict(info, pos_layout_version=info['pos_layout_version'] + 1)
    original_info = dict(s.company.conn.execute(sa.select(c.company_info.c.id, c.company_info.c.pos_layout_version,
        c.company_info.c.pos_home_view_style)).mappings().one())
    return Plan(output(s, after_info, rows, extra_pictures), dict(info=original_info, after_info=after_info,
                                                               rows=rows, before=before))


def _place(rows, row, parent, position):
    previous_parent = row['parent_id']
    siblings = sorted((r for r in rows if r['parent_id'] == parent and r['id'] != row['id']),
                      key=lambda r: r['position'])
    at = len(siblings) if position is None else position
    if at > len(siblings):
        raise invalid('position', f'must be between 0 and {len(siblings)}')
    row['parent_id'] = parent
    siblings.insert(at, row)
    for n, sibling in enumerate(siblings):
        sibling['position'] = n
    if previous_parent != parent:
        for n, sibling in enumerate(sorted((r for r in rows if r['parent_id'] == previous_parent),
                                          key=lambda r: r['position'])):
            sibling['position'] = n


def plan_create(s, ctx, inp):
    info, rows, before = begin(s, ctx, inp.expected_version)
    _group(rows, inp.parent)
    label = (inp.label or '').strip() or None
    if inp.kind == 'group':
        if not label:
            raise invalid('label', 'a group needs a name')
        if inp.item is not None:
            raise invalid('item', 'a group cannot name an item')
        item_id = None
    else:
        if inp.item is None:
            raise invalid('item', 'an item box needs a sales item')
        from bookflow.company.items import resolve_item
        item = resolve_item(s.company, inp.item)
        if not item['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'item', 'record_id': item['id']})
        if not item['sales_enabled'] or item['type'] not in BOX_TYPES or (
                item['type'] == 'other_charge' and item['price_minor_units'] is None):
            raise invalid('item', 'must be a sales item with a fixed price; discounts and subtotals use lookup')
        item_id = item['id']
        if inp.picture_attachment is not None:
            raise invalid('picture_attachment', 'item boxes share the inventory item picture')
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), parent_id=inp.parent,
        position=0, kind=inp.kind, item_id=item_id, label=label, colour=inp.colour,
        picture_attachment_id=_picture(s, inp.picture_attachment), view_style='block')
    rows.append(row)
    _place(rows, row, inp.parent, inp.position)
    return _finish(s, ctx, info, rows, before)


def plan_update(s, ctx, inp):
    info, rows, before = begin(s, ctx, inp.expected_version)
    row = _box(rows, inp.pos_box)
    if 'label' in inp.model_fields_set:
        row['label'] = (inp.label or '').strip() or None
        if row['kind'] == 'group' and not row['label']:
            raise invalid('label', 'a group needs a name')
    if 'colour' in inp.model_fields_set:
        row['colour'] = inp.colour
    if 'picture_attachment' in inp.model_fields_set:
        if row['kind'] != 'group':
            raise invalid('picture_attachment', 'item boxes share the inventory item picture')
        row['picture_attachment_id'] = _picture(s, inp.picture_attachment)
    return _finish(s, ctx, info, rows, before)


def plan_move(s, ctx, inp):
    info, rows, before = begin(s, ctx, inp.expected_version)
    row = _box(rows, inp.pos_box)
    parent = _group(rows, inp.parent)
    while parent:
        if parent['id'] == row['id']:
            raise invalid('parent', 'a group cannot be moved into itself or a descendant')
        parent = _group(rows, parent['parent_id'])
    _place(rows, row, inp.parent, inp.position)
    return _finish(s, ctx, info, rows, before)


def plan_remove(s, ctx, inp):
    info, rows, before = begin(s, ctx, inp.expected_version)
    row = _box(rows, inp.pos_box)
    gone = {row['id']}
    while True:
        expanded = gone | {r['id'] for r in rows if r['parent_id'] in gone}
        if expanded == gone:
            break
        gone = expanded
    rows = [r for r in rows if r['id'] not in gone]
    for n, sibling in enumerate(sorted((r for r in rows if r['parent_id'] == row['parent_id']),
                                      key=lambda r: r['position'])):
        sibling['position'] = n
    return _finish(s, ctx, info, rows, before)


def plan_view(s, ctx, inp):
    info, rows, before = begin(s, ctx, inp.expected_version)
    if inp.pos_box is None:
        info['pos_home_view_style'] = inp.style
    else:
        _group(rows, inp.pos_box)['view_style'] = inp.style
    return _finish(s, ctx, info, rows, before)


def plan_seed(s, ctx, inp):
    info, rows, before = begin(s, ctx, inp.expected_version)
    if info['pos_layout_version']:
        raise invalid('mode', 'the layout is already saved; edit its boxes instead')
    return _finish(s, ctx, info, [] if inp.mode == 'empty' else rows, before)


def apply(plan, ctx, s):
    data = plan.data
    info, after = data['info'], data['after_info']
    if s.company.conn.execute(c.company_info.update().where(c.company_info.c.id == info['id'],
            c.company_info.c.pos_layout_version == info['pos_layout_version']).values(
                pos_layout_version=after['pos_layout_version'], pos_home_view_style=after['pos_home_view_style'])).rowcount != 1:
        raise conflict(s, info['id'], info['pos_layout_version'])
    old = {r['id']: r for r in data['before']}
    new = {r['id']: r for r in data['rows']}
    touched = []
    # Remove children before parents so foreign keys remain true even inside the transaction.
    removed = set(old) - set(new)
    while removed:
        leaves = {key for key in removed if not any(old[k]['parent_id'] == key for k in removed)}
        for key in sorted(leaves):
            row = old[key]
            s.company.conn.execute(c.pos_boxes.delete().where(c.pos_boxes.c.id == key))
            touched.append(Touched('pos_box', key, 'delete', row['version'], None, None, row, db='company'))
        removed -= leaves
    pending = [r for r in data['rows'] if r['id'] not in old]
    inserted = set(old) & set(new)
    while pending:
        ready = [r for r in pending if r['parent_id'] is None or r['parent_id'] in inserted]
        for row in ready:
            s.company.conn.execute(c.pos_boxes.insert().values(**row))
            touched.append(Touched('pos_box', row['id'], 'create', None, row['version'], row, db='company'))
            inserted.add(row['id'])
            pending.remove(row)
    for key in old.keys() & new.keys():
        if old[key] != new[key]:
            s.company.conn.execute(c.pos_boxes.update().where(c.pos_boxes.c.id == key).values(**new[key]))
            touched.append(Touched('pos_box', key, 'update', old[key]['version'], new[key]['version'], new[key], old[key],
                                   db='company'))
    touched.append(Touched('pos_layout', info['id'], 'update', info['pos_layout_version'], after['pos_layout_version'],
                           after, info, db='company'))
    return Applied(plan.preview, touched, 'updated POS layout')
