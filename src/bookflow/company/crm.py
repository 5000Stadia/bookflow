"""Lead identity and contact aggregates; notes and work remain in their native owners."""
from __future__ import annotations
import json
import sqlalchemy as sa
from bookflow.company import schema as c, parties, todos
from bookflow.company import crm_models as m
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id, is_ulid, normalize_ulid
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.access import require_resource


def require_read(s):
    require_resource(s, 'crm.read', 'member')


def may(s, capability, threshold):
    try:
        require_resource(s, capability, threshold)
        return True
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        return False


def resolve(s, selector):
    require_read(s)
    from bookflow.company.list_service import normalize_lookup_key
    t = c.crm_leads
    condition = t.c.id == normalize_ulid(selector) if is_ulid(selector) else t.c.name_key == normalize_lookup_key(selector)
    rows = [dict(r) for r in s.company.conn.execute(sa.select(t).where(condition).order_by(t.c.id).limit(21)).mappings()]
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'lead', 'selector': selector, 'suggestions': []})
    if len(rows) > 1:
        raise BookflowError('E_AMBIGUOUS_SELECTOR', details={'record_type': 'lead', 'selector': selector,
            'candidates': [{'id': r['id'], 'display_name': r['display_name'], 'version': r['version']} for r in rows[:20]],
            'has_more': len(rows) > 20})
    return rows[0]


def contacts(s, lead_id):
    return parties._read_contacts(s.company, owner_id=lead_id, owner_column='lead_id',
        contact_table=c.crm_lead_contacts, point_table=c.crm_lead_contact_points)


def project(s, row, contact_rows=None):
    from bookflow.commands.common import common_out
    from bookflow.company.info import principal_names
    data = {key: row.get(key) for key in m.LeadOutput.model_fields if key in row}
    data.update(common_out(s, row))
    data['address'] = {key: row.get('address_' + key) for key in parties.AddressInput.model_fields}
    data['contacts'] = parties._public_contacts(contacts(s, row['id']) if contact_rows is None else contact_rows)
    data['owner_name'] = principal_names(s.company, {row['owner_id']}).get(row['owner_id']) if row['owner_id'] else None
    if row['owner_id'] == s.actor.id:
        data['owner_name'] = s.actor.display_name
    data['permitted_actions'] = ['lead show', 'lead list']
    if may(s, 'crm.write', 'standard'):
        data['permitted_actions'] += ['lead update', 'lead archive', 'lead close' if row['status'] == 'open' else 'lead reopen']
    if may(s, 'note', 'member'):
        q = sa.select(c.todos).where(c.todos.c.link_type == 'lead', c.todos.c.link_id == row['id'], c.todos.c.status == 'open')
        data['open_follow_up_count'] = s.company.conn.execute(sa.select(sa.func.count()).select_from(q.subquery())).scalar_one()
        priority = sa.case((c.todos.c.priority == 'high', 0), (c.todos.c.priority == 'medium', 1), else_=2)
        task = s.company.conn.execute(q.order_by(c.todos.c.due_date, priority, c.todos.c.created_at, c.todos.c.id).limit(1)).mappings().first()
        data['next_follow_up'] = todos.project(s, dict(task)) if task else None
    return data


def _check_version(s, row, expected):
    from bookflow.core.audit import decode_snapshot
    from bookflow.core.versioning import current_writer, history_from_entries, fold_field, _conflict_message, _seconds_since
    if expected == row['version']:
        return
    writer = current_writer(s.company, 'lead', row['id'], row)
    history = history_from_entries(s.company, 'lead', row['id'], expected, decode_snapshot)
    changed = sorted({fold_field(key) for entry in history for key in (entry.changed_columns or [])})
    details = {
        'record_type': 'lead', 'record_id': row['id'], 'expected_version': expected,
        'current_version': row['version'], 'changed_fields': changed,
        'updated_by': writer.updated_by if writer else None,
        'updated_by_name': writer.updated_by_name if writer else None,
        'updated_on_behalf_of': writer.on_behalf_of if writer else None,
        'updated_on_behalf_of_name': writer.on_behalf_of_name if writer else None,
        'updated_via': writer.updated_via if writer else None,
        'seconds_since_update': _seconds_since(row['updated_at'])}
    raise BookflowError('E_VERSION_CONFLICT', message=_conflict_message(details, changed), details=details)


def prepare(s, ctx, inp, verb):
    from bookflow.company.list_service import normalize_lookup_key
    at, via = clock.now_iso(), ctx.interface.value
    before = None if verb == 'create' else resolve(s, inp.lead)
    existing = () if before is None else contacts(s, before['id'])
    writes, desired, owner = (), existing, None
    if before:
        _check_version(s, before, inp.expected_version)
        if before['status'] == 'converted':
            raise parties._validation('lead', 'converted lead identity is locked until conversion is reversed')
        row = dict(before)
    else:
        row = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=via,
            updated_at=at, updated_by=s.actor.id, updated_via=via, status='open', archived=False,
            close_reason=None, duplicate_of=None, owner_id=None)
        for key in ('company_name', 'salutation', 'first_name', 'middle_name', 'last_name', 'job_title', 'source', 'summary'):
            row[key] = None
        row.update({f'address_{key}': None for key in parties.AddressInput.model_fields})
    if verb in ('create', 'update'):
        fields = set(m.LeadFields.model_fields) if verb == 'create' else inp.model_fields_set
        for key in fields - {'owner', 'address', 'contacts', 'lead', 'expected_version'}:
            value = getattr(inp, key)
            if key == 'display_name' and (value is None or not value.strip()):
                raise parties._validation(key, 'must not be blank')
            row[key] = value
        row['name_key'] = normalize_lookup_key(row['display_name'])
        if 'owner' in fields:
            if inp.owner is None and 'owner' in inp.model_fields_set:
                row['owner_id'] = None
            else:
                owner = todos.resolve_assignee(s, inp.owner)
                row['owner_id'] = owner['id']
        if 'address' in fields:
            row.update({f'address_{key}': getattr(inp.address, key) if inp.address else None for key in parties.AddressInput.model_fields})
        if 'contacts' in fields:
            submitted = [item.model_dump(exclude_unset=True) for item in inp.contacts]
            for item in submitted:
                if not item.get('id') and 'display_name' not in item:
                    item['display_name'] = row['display_name']
            writes, desired = parties._plan_contacts(s.company, owner_id=row['id'], owner_column='lead_id',
                contact_table=c.crm_lead_contacts, point_table=c.crm_lead_contact_points, existing=existing, submitted=submitted)
    elif verb == 'close':
        if not ctx.reason or not ctx.reason.strip():
            raise parties._validation('reason', 'closing a lead requires a reason')
        if len(ctx.reason) > 1000:
            raise parties._validation('reason', 'at most 1000 characters')
        duplicate = resolve(s, inp.duplicate_of)['id'] if inp.duplicate_of else None
        seen, target = {row['id']}, duplicate
        while target:
            if target in seen:
                raise parties._validation('duplicate_of', 'duplicate links must not form a cycle')
            seen.add(target)
            target = resolve(s, target)['duplicate_of']
        row.update(status='closed', close_reason=ctx.reason, duplicate_of=duplicate)
    elif verb == 'reopen':
        row.update(status='open', close_reason=None, duplicate_of=None)
    elif verb == 'archive':
        row['archived'] = inp.archived
    if before:
        row.update(version=before['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=via)
    return Plan(m.LeadWriteOutput(**project(s, row, desired), dry_run=s.dry_run),
        dict(row=row, before=before, writes=writes, desired=desired, existing=existing, owner=owner))


def apply(plan, ctx, s):
    row, before = plan.data['row'], plan.data['before']
    if plan.data['owner']:
        todos._mirror(s, plan.data['owner'])
    if before is None:
        s.company.conn.execute(c.crm_leads.insert().values(**row))
    else:
        s.company.conn.execute(c.crm_leads.update().where(c.crm_leads.c.id == row['id']).values(**row))
    for write in plan.data['writes']:
        table = c.metadata.tables[write.table_name]
        owner_key = 'lead_id' if table.name == 'crm_lead_contacts' else 'contact_id'
        owners = {r[owner_key] for r in write.rows}
        s.company.conn.execute(table.update().where(table.c[owner_key].in_(owners)).values(active=False))
        for child in write.rows:
            exists = s.company.conn.execute(sa.select(table.c.id).where(table.c.id == child['id'])).first()
            s.company.conn.execute(table.update().where(table.c.id == child['id']).values(**child) if exists else table.insert().values(**child))
    after = dict(row, contacts=plan.data['desired'])
    old = dict(before, contacts=plan.data['existing']) if before else None
    return Applied(m.LeadWriteOutput(**project(s, row)),
        [Touched('lead', row['id'], 'update' if before else 'create', before['version'] if before else None,
                 row['version'], after, old, db='company')], f"saved lead {row['display_name']!r}")


def page(s, ctx, inp):
    from bookflow.company.query import QueryInput, page_state, continuation
    from bookflow.company.list_service import normalize_lookup_key
    require_read(s)
    state = page_state(s, 'lead', QueryInput(query=inp.search, limit=inp.limit, cursor=inp.cursor,
        filter=[json.dumps(inp.model_dump(exclude={'cursor'}), sort_keys=True), json.dumps(s.memberships, sort_keys=True, default=str)]), ctx.on_behalf_of)
    t, q = c.crm_leads, sa.select(c.crm_leads)
    if inp.status != 'all':
        q = q.where(t.c.status == inp.status)
    if not inp.include_archived:
        q = q.where(t.c.archived.is_(False))
    if inp.search:
        q = q.where(t.c.name_key.contains(normalize_lookup_key(inp.search), autoescape=True))
    if inp.owner:
        q = q.where(t.c.owner_id == todos.resolve_assignee(s, inp.owner)['id'])
    total = s.company.conn.execute(sa.select(sa.func.count()).select_from(q.subquery())).scalar_one()
    rows = s.company.conn.execute(q.order_by(t.c.name_key, t.c.id).offset(state.offset).limit(inp.limit)).mappings().all()
    more = state.offset + len(rows) < total
    return m.LeadPage(items=[m.LeadOutput(**project(s, dict(r))) for r in rows], count=len(rows),
        total_count=total, has_more=more, next_cursor=continuation(state, len(rows), more))


def replay(inp, ctx, s, hit):
    """Keep the committed receipt while rechecking optional source projections."""
    if hit['state'] != 'done' or not hit['output']:
        return None
    output = json.loads(hit['output'])
    if not may(s, 'note', 'member'):
        output['next_follow_up'] = None
        output['open_follow_up_count'] = None
    return output
