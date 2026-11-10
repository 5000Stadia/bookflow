"""Lead conversion through native customer validation, the lead/customer history, and its reversal adapter."""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from bookflow.company import batches, crm, parties, party_merges, schema as c
from bookflow.company import crm_models as m
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.access import require_resource

KIND = 'crm-conversion'
RECORD = 'crm_conversion'


def require(s, capability, threshold):
    """Conversion, history and lead-import rights; every native source keeps its own requirement as well."""
    require_resource(s, capability, threshold)


def allowed(s, capability, threshold):
    try:
        require(s, capability, threshold)
        return True
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        return False


_COPYABLE = tuple(field for field in parties._CONTACT_FIELDS if field != 'role')
_PERSON = ('company_name', 'salutation', 'first_name', 'middle_name', 'last_name', 'job_title')


def digest(inp) -> str:
    return hashlib.sha256(json.dumps(inp.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()


def active_for_lead(s, lead_id):
    t = c.crm_conversions
    row = s.company.conn.execute(sa.select(t).where(t.c.lead_id == lead_id, t.c.state == 'active')).mappings().first()
    return dict(row) if row else None


def _conversion(s, conversion_id):
    t = c.crm_conversions
    row = s.company.conn.execute(sa.select(t).where(t.c.id == conversion_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': conversion_id})
    return dict(row)


def _mappings(s, conversion_id):
    t = c.crm_conversion_contacts
    return [dict(r) for r in s.company.conn.execute(sa.select(t).where(t.c.conversion_id == conversion_id)
                                                     .order_by(t.c.id)).mappings()]


def _customer(s, customer_id):
    return dict(s.company.conn.execute(sa.select(c.customers).where(c.customers.c.id == customer_id)).mappings().one())


def _lead(s, lead_id):
    return dict(s.company.conn.execute(sa.select(c.crm_leads).where(c.crm_leads.c.id == lead_id)).mappings().one())


def conversion_out(s, row) -> m.ConversionOutput:
    customer = _customer(s, row['customer_id'])
    return m.ConversionOutput(id=row['id'], state=row['state'], target=row['target'], customer_id=row['customer_id'],
        customer_name=customer['full_name'], customer_version=customer['version'], batch_id=row['batch_id'],
        created_at=row['created_at'], created_by=row['created_by'],
        contact_map=[m.ContactMapping(lead_contact_id=x['lead_contact_id'], customer_contact_id=x['customer_contact_id'],
                                      action=x['action'], fields=json.loads(x['fields'])) for x in _mappings(s, row['id'])])


def _matches(s, name, lead_contacts):
    """Customers a person should see before converting: same name, or a shared email; never merged."""
    from bookflow.company.list_service import normalize_lookup_key
    t, k = c.customers, c.customer_contacts
    found = {}
    for row in s.company.conn.execute(sa.select(t.c.id, t.c.full_name, t.c.active).where(
            t.c.name_key == normalize_lookup_key(name)).order_by(t.c.id).limit(10)):
        found[row.id] = m.CustomerMatch(id=row.id, name=row.full_name, active=bool(row.active), reason='same name')
    emails = sorted({(x.get('primary_email') or '').strip().casefold() for x in lead_contacts} - {''})
    if emails:
        rows = s.company.conn.execute(sa.select(t.c.id, t.c.full_name, t.c.active).join(k, k.c.customer_id == t.c.id)
            .where(k.c.active.is_(True), sa.func.lower(k.c.primary_email).in_(emails)).order_by(t.c.id).limit(10))
        for row in rows:
            found.setdefault(row.id, m.CustomerMatch(id=row.id, name=row.full_name, active=bool(row.active),
                                                     reason='shared email'))
    return list(found.values())[:10]


def _contact_payload(row, *, role=None):
    data = parties._contact_input_from_row(row)
    data.pop('id')
    for point in data['points']:
        point.pop('id')
    if role:
        data['role'] = role
    return {k: v for k, v in data.items() if v is not None and v != []}


def _active_contacts(rows):
    return [dict(x) for x in rows if x.get('active', True)]


def _already(s, lead, conversion, inp):
    if conversion['request_hash'] != digest(inp):
        customer = _customer(s, conversion['customer_id'])
        raise BookflowError('E_CRM_ALREADY_CONVERTED', message=(
            f"Lead {lead['display_name']} is already converted to customer {customer['full_name']}; "
            'reverse that conversion first to choose another target.'),
            details={'record_type': 'lead', 'record_id': lead['id'], 'conversion': conversion['id'],
                     'customer_id': conversion['customer_id'], 'customer_name': customer['full_name'],
                     'batch_id': conversion['batch_id'],
                     'next': [f"crm timeline --lead {lead['id']}", f"batch show {conversion['batch_id']}"]})


def prepare(s, ctx, inp) -> Plan:
    crm.require_read(s)
    require(s, 'crm.convert', 'standard')
    lead = crm.resolve(s, inp.lead)
    existing_conversion = active_for_lead(s, lead['id'])
    if existing_conversion is not None:
        _already(s, lead, existing_conversion, inp)
        require(s, 'customer', 'member')
        out = m.LeadConvertOutput(lead=m.LeadOutput(**crm.project(s, lead)), conversion=conversion_out(s, existing_conversion),
            replayed=True, dry_run=s.dry_run, next=[f"crm timeline --lead {lead['id']}"])
        return Plan(out, dict(replay=True))
    crm._check_version(s, lead, inp.expected_version)
    at, via = clock.now_iso(), ctx.interface.value
    lead_contacts = _active_contacts(crm.contacts(s, lead['id']))
    copied, mapping_plan, customer_write, before_customer = [], [], None, None
    if inp.target == 'new':
        require(s, 'customer', 'standard')
        if inp.customer or inp.customer_expected_version or inp.contact_transfers:
            raise parties._validation('customer', 'a new-customer conversion takes no existing customer or transfers')
        name = (inp.name or lead['display_name']).strip()
        payload = {'name': name}
        for key in _PERSON:
            if lead.get(key):
                payload[key] = lead[key]
                copied.append(key)
        address = {key: lead['address_' + key] for key in parties.AddressInput.model_fields if lead.get('address_' + key)}
        if address:
            payload['billing_address'] = address
            copied.append('billing_address')
        if lead.get('summary'):
            payload['notes'] = lead['summary']
            copied.append('notes')
        if lead_contacts:
            payload['contacts'] = [_contact_payload(x) for x in lead_contacts]
            copied.append('contacts')
        mutation = parties.plan_party_create(s.company, 'customer', payload, actor_id=s.actor.id, via=via, at=at)
        created = _active_contacts(mutation.children_plan.collections_after.get('contacts', ()) if mutation.children_plan else ())
        for source, made in zip(lead_contacts, created):
            mapping_plan.append(dict(lead_contact_id=source['id'], customer_contact_id=made['id'], action='created',
                                     fields=[f for f in _COPYABLE if source.get(f) is not None], before=None))
        customer_write = ('create', mutation)
        customer_id, customer_name, customer_version = mutation.after['id'], mutation.after['full_name'], 1
    else:
        if inp.name:
            raise parties._validation('name', 'an existing-customer conversion keeps that customer\'s name')
        if not inp.customer or inp.customer_expected_version is None:
            raise parties._validation('customer', 'an existing-customer conversion names the customer and its expected version')
        require(s, 'customer', 'standard' if inp.contact_transfers else 'member')
        before_customer = parties.resolve_party(s.company, 'customer', inp.customer)
        if before_customer['parent_id'] is not None:
            raise parties._validation('customer', 'a lead converts to a top-level customer, not a job')
        if not before_customer['active']:
            raise parties._validation('customer', 'the customer is inactive; make it active first')
        if before_customer['version'] != inp.customer_expected_version:
            raise BookflowError('E_VERSION_CONFLICT', details=parties._version_conflict_detail(
                s.company, 'customer', before_customer, inp.customer_expected_version))
        customer_id, customer_name = before_customer['id'], before_customer['full_name']
        customer_version = before_customer['version']
        if inp.contact_transfers:
            by_id = {x['id']: x for x in lead_contacts}
            current = _active_contacts(parties.read_party_collections(s.company, 'customer', customer_id)['contacts'])
            submission = [parties._contact_input_from_row(x) for x in current]
            positions = {x['id']: i for i, x in enumerate(submission)}
            seen = set()
            for transfer in inp.contact_transfers:
                source = by_id.get(transfer.lead_contact)
                if source is None:
                    raise parties._validation('contact_transfers.lead_contact', 'not an active contact of this lead')
                if transfer.customer_contact is None:
                    submission.append(dict(_contact_payload(source, role='additional')))
                    mapping_plan.append(dict(lead_contact_id=source['id'], customer_contact_id=None, action='created',
                        fields=[f for f in _COPYABLE if source.get(f) is not None], before=None, index=len(submission) - 1))
                    continue
                if transfer.customer_contact not in positions:
                    raise parties._validation('contact_transfers.customer_contact', 'not an active contact of this customer')
                if transfer.customer_contact in seen:
                    raise parties._validation('contact_transfers.customer_contact', 'each customer contact is mapped once')
                seen.add(transfer.customer_contact)
                fields = list(dict.fromkeys(transfer.fields))
                if not fields or any(f not in _COPYABLE for f in fields):
                    raise parties._validation('contact_transfers.fields', 'name the contact fields to copy: ' + ', '.join(_COPYABLE))
                target = submission[positions[transfer.customer_contact]]
                before = {f: target.get(f) for f in fields}
                for f in fields:
                    target[f] = source.get(f)
                mapping_plan.append(dict(lead_contact_id=source['id'], customer_contact_id=transfer.customer_contact,
                                         action='updated', fields=fields, before=before))
            update = parties.plan_party_update(s.company, 'customer', customer_id, {'contacts': submission},
                                               actor_id=s.actor.id, via=via, at=at)
            if update.mutation is not None:
                after_contacts = _active_contacts(update.mutation.children_plan.collections_after['contacts'])
                for item in mapping_plan:
                    if item['customer_contact_id'] is None:
                        item['customer_contact_id'] = after_contacts[item['index']]['id']
                customer_write = ('update', update)
                customer_version = update.mutation.after['version']
                copied.append('contacts')
    row = dict(lead, status='converted', close_reason=None, version=lead['version'] + 1,
               updated_at=at, updated_by=s.actor.id, updated_via=via)
    conversion = dict(id=new_id(), lead_id=lead['id'], customer_id=customer_id, target=inp.target, request_hash=digest(inp),
        lead_version_before=lead['version'], lead_version_after=row['version'], lead_status_before=lead['status'],
        lead_close_reason_before=lead['close_reason'], customer_version_before=before_customer['version'] if before_customer else None,
        customer_version_after=customer_version, lead_label=lead['display_name'][:200], customer_label=customer_name[:400],
        state='active', created_at=at, created_by=s.actor.id, created_via=via)
    preview = m.LeadConvertOutput(lead=m.LeadOutput(**crm.project(s, row)),
        conversion=m.ConversionOutput(id=None if s.dry_run else conversion['id'], state='active', target=inp.target,
            customer_id=None if s.dry_run and inp.target == 'new' else customer_id, customer_name=customer_name,
            customer_version=customer_version, batch_id=None,
            contact_map=[m.ContactMapping(lead_contact_id=x['lead_contact_id'], action=x['action'], fields=x['fields'],
                # A dry run makes no IDs durable: a contact it would create has none yet.
                customer_contact_id=None if s.dry_run and x['action'] == 'created' else x['customer_contact_id'])
                for x in mapping_plan]),
        matching_customers=_matches(s, customer_name if inp.target == 'new' else lead['display_name'], lead_contacts),
        copied_fields=copied, dry_run=s.dry_run)
    return Plan(preview, dict(replay=False, lead=lead, row=row, conversion=conversion, mappings=mapping_plan,
                              customer_write=customer_write, lead_contacts=lead_contacts))


def apply(plan, ctx, s) -> Applied:
    from bookflow.core.audit import write_event_to
    if plan.data['replay']:
        return Applied(plan.preview, [], 'conversion already recorded')
    lead, row, conversion = plan.data['lead'], plan.data['row'], plan.data['conversion']
    touched = []
    write = plan.data['customer_write']
    if write and write[0] == 'create':
        mutation = write[1]
        parties.persist_party_mutation(s.company, mutation)
        touched.append(Touched('customer', mutation.after['id'], 'create', None, 1, dict(mutation.after_snapshot), db='company'))
    elif write:
        update = write[1]
        parties.persist_party_update(s.company, update)
        mutation = update.mutation
        touched.append(Touched('customer', mutation.after['id'], 'update', int(mutation.before['version']),
            int(mutation.after['version']), dict(mutation.after_snapshot), dict(mutation.before_snapshot), db='company'))
    contacts = plan.data['lead_contacts']
    s.company.conn.execute(c.crm_leads.update().where(c.crm_leads.c.id == row['id']).values(**row))
    touched.append(Touched('lead', row['id'], 'update', lead['version'], row['version'], dict(row, contacts=contacts),
                           dict(lead, contacts=contacts), db='company'))
    batch = batches._insert_batch(s, ctx, kind=KIND, label=f"Conversion of lead {lead['display_name']}"[:200],
        scope=f"crm-conversion:{lead['id']}", ordering='record', source_type='lead', source_id=lead['id'])
    conversion = dict(conversion, batch_id=batch)
    s.company.conn.execute(c.crm_conversions.insert().values(**conversion))
    for item in plan.data['mappings']:
        s.company.conn.execute(c.crm_conversion_contacts.insert().values(id=new_id(), conversion_id=conversion['id'],
            lead_contact_id=item['lead_contact_id'], customer_contact_id=item['customer_contact_id'], action=item['action'],
            fields=json.dumps(item['fields']), before=None if item['before'] is None else json.dumps(item['before'], sort_keys=True)))
    snapshot = {k: v for k, v in conversion.items()}
    snapshot['contacts'] = [{k: x[k] for k in ('lead_contact_id', 'customer_contact_id', 'action', 'fields')}
                            for x in plan.data['mappings']]
    touched.append(Touched(RECORD, conversion['id'], 'create', None, 1, snapshot, db='company'))
    summary = f"converted lead {lead['display_name']!r} to customer {conversion['customer_label']!r}"
    event = write_event_to(s.company, ctx, 'lead convert', summary, touched, actor_id=s.actor.id,
                           actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    batches.record_effect(s, batch, record_type=RECORD, record_id=conversion['id'], action='created',
                          version_after=row['version'], event_id=event)
    out = m.LeadConvertOutput(lead=m.LeadOutput(**crm.project(s, row)), conversion=conversion_out(s, conversion),
        matching_customers=plan.preview.matching_customers, copied_fields=plan.preview.copied_fields,
        next=[f"crm timeline --lead {lead['id']}", f'batch show {batch}'])
    return Applied(out, touched, summary, audited=True)


def replay(inp, ctx, s, hit):
    """Keep the committed receipt while rechecking the lead's optional task projection."""
    if hit['state'] != 'done' or not hit['output']:
        return None
    output = json.loads(hit['output'])
    if not crm.may(s, 'note', 'member'):
        output['lead']['next_follow_up'] = None
        output['lead']['open_follow_up_count'] = None
    return output


# ---------------------------------------------------------------- one history across the lead and its customer

def _subjects(s, inp):
    """(lead ids, customer ids, lead row, customer row) the history reads, following active conversions and merges."""
    if (inp.lead is None) == (inp.customer is None):
        raise parties._validation('lead', 'name exactly one of lead or customer')
    t = c.crm_conversions
    if inp.lead is not None:
        lead = crm.resolve(s, inp.lead)
        conversion = active_for_lead(s, lead['id'])
        customer = None
        if conversion is not None:
            customer = _customer(s, party_merges.survivor(s.company, 'customer', conversion['customer_id']))
    else:
        require(s, 'customer', 'member')
        found = parties.resolve_party(s.company, 'customer', inp.customer)
        customer, lead = _customer(s, party_merges.survivor(s.company, 'customer', found['id'])), None
    customers = []
    if customer is not None:
        customers = [r[0] for r in s.company.raw.execute(party_merges.members_sql('customer', ':c'), {'c': customer['id']})]
    leads = {lead['id']} if lead else set()
    if customers:
        leads |= {r[0] for r in s.company.conn.execute(sa.select(t.c.lead_id).where(
            t.c.customer_id.in_(customers), t.c.state == 'active'))}
    return sorted(leads), customers, lead, customer


def timeline(s, ctx, inp) -> m.TimelineOutput:
    from bookflow.company.query import QueryInput, page_state, continuation
    crm.require_read(s)
    leads, customers, lead, customer = _subjects(s, inp)
    read_customer = bool(customers) and allowed(s, 'customer', 'member')
    if not read_customer:
        customers = []
    notes_ok, files_ok = allowed(s, 'note', 'member'), allowed(s, 'attachment', 'member')
    omitted = [name for name, ok in (('notes and to-dos', notes_ok), ('attachments', files_ok)) if not ok]
    if customer is not None and not read_customer:
        omitted.append('customer')
    contact_ids = [r[0] for r in s.company.conn.execute(sa.select(c.crm_lead_contacts.c.id).where(
        c.crm_lead_contacts.c.lead_id.in_(leads)))] if leads else []
    labels = {r.id: r.display_name for r in s.company.conn.execute(sa.select(c.crm_leads.c.id, c.crm_leads.c.display_name)
                                                                  .where(c.crm_leads.c.id.in_(leads)))} if leads else {}
    owner_of = {r.id: r.lead_id for r in s.company.conn.execute(sa.select(c.crm_lead_contacts.c.id, c.crm_lead_contacts.c.lead_id)
                                                               .where(c.crm_lead_contacts.c.id.in_(contact_ids)))} if contact_ids else {}

    def targets(type_col, id_col):
        parts = []
        if leads:
            parts.append(sa.and_(type_col == 'lead', id_col.in_(leads)))
        if contact_ids:
            parts.append(sa.and_(type_col == 'lead_contact', id_col.in_(contact_ids)))
        if customers:
            parts.append(sa.and_(type_col == 'customer', id_col.in_(customers)))
        return sa.or_(*parts) if parts else sa.false()

    n, k, a, f, v = c.notes, c.todos, c.attachment_links, c.attachments, c.crm_conversions
    selects = []
    if notes_ok:
        selects.append(sa.select(sa.literal('note').label('kind'), n.c.id, n.c.at.label('at'), n.c.record_type.label('source_type'),
            n.c.record_id.label('source_id'), sa.func.substr(n.c.body, 1, 200).label('title'), sa.literal(None).label('status'))
            .where(targets(n.c.record_type, n.c.record_id)))
        selects.append(sa.select(sa.literal('todo').label('kind'), k.c.id, k.c.created_at.label('at'), k.c.link_type.label('source_type'),
            k.c.link_id.label('source_id'), k.c.title.label('title'), k.c.status.label('status'))
            .where(targets(k.c.link_type, k.c.link_id)))
    if files_ok:
        selects.append(sa.select(sa.literal('attachment').label('kind'), a.c.id, a.c.linked_at.label('at'),
            a.c.record_type.label('source_type'), a.c.record_id.label('source_id'), f.c.original_filename.label('title'),
            sa.case((a.c.active.is_(True), 'linked'), else_='unlinked').label('status'))
            .select_from(a.join(f, f.c.id == a.c.attachment_id)).where(targets(a.c.record_type, a.c.record_id)))
    scope = v.c.lead_id.in_(leads) if leads else sa.false()
    label = (sa.literal('Converted to customer ') + v.c.customer_label if allowed(s, 'customer', 'member')
             else sa.literal('Converted to a customer'))
    selects.append(sa.select(sa.literal('conversion').label('kind'), v.c.id, v.c.created_at.label('at'),
        sa.literal('lead').label('source_type'), v.c.lead_id.label('source_id'), label.label('title'), v.c.state.label('status'))
        .where(scope))
    selects.append(sa.select(sa.literal('conversion_reversed').label('kind'), v.c.id, v.c.reversed_at.label('at'),
        sa.literal('lead').label('source_type'), v.c.lead_id.label('source_id'), sa.literal('Conversion reversed').label('title'),
        v.c.state.label('status')).where(scope, v.c.reversed_at.is_not(None)))
    union = sa.union_all(*selects).subquery()
    state = page_state(s, 'lead', QueryInput(limit=inp.limit, cursor=inp.cursor, filter=[
        json.dumps(['timeline', inp.lead, inp.customer], sort_keys=True), json.dumps(s.memberships, sort_keys=True, default=str)]),
        ctx.on_behalf_of)
    total = s.company.conn.execute(sa.select(sa.func.count()).select_from(union)).scalar_one()
    rows = s.company.conn.execute(sa.select(union).order_by(union.c.at.desc(), union.c.kind, union.c.id.desc())
                                  .offset(state.offset).limit(inp.limit)).mappings().all()
    more = state.offset + len(rows) < total
    items = []
    for r in rows:
        origin = r['source_id'] if r['source_type'] == 'lead' else owner_of.get(r['source_id'])
        from_lead = labels.get(origin) if origin and (customer is not None) else None
        items.append(m.TimelineEntry(kind=r['kind'], id=r['id'], at=r['at'], source_type=r['source_type'],
            source_id=r['source_id'], from_lead=f'From lead {from_lead}' if from_lead else None, title=r['title'] or '',
            status=r['status']))
    return m.TimelineOutput(lead_id=lead['id'] if lead else None, lead_name=lead['display_name'] if lead else None,
        customer_id=customer['id'] if customer and read_customer else None,
        customer_name=customer['full_name'] if customer and read_customer else None, items=items, count=len(items),
        total_count=total, has_more=more, next_cursor=continuation(state, len(items), more), omitted=omitted)


# ---------------------------------------------------------------- reversal through `batch reverse`

def _read(s, batch):
    crm.require_read(s)
    require(s, 'customer', 'member')


def _authority(s, conversion, mappings):
    crm.require_read(s)
    require(s, 'crm.convert', 'standard')
    require(s, 'customer', 'standard' if conversion['target'] == 'new' or mappings else 'member')


def _first(s, query):
    return s.company.conn.execute(query.limit(1)).first()


def problems(s, effect, conversion):
    """Every dependency on the conversion, read before any inverse writes. Pre-existing lead work never blocks."""
    out = []
    lead, customer = _lead(s, conversion['lead_id']), _customer(s, conversion['customer_id'])
    name, since, cid = customer['full_name'], conversion['created_at'], customer['id']

    def add(code, message, clears=None, **details):
        out.append(dict(seq=effect['seq'], record_type=RECORD, record_id=conversion['id'], code=code,
                        message=message, clears=clears, details=details))

    if conversion['state'] != 'active':
        add('already_reversed', 'This conversion was already reversed.')
        return out
    if lead['version'] != conversion['lead_version_after'] or lead['status'] != 'converted':
        add('edited_again', f"Lead {lead['display_name']} changed after its conversion.", lead=lead['id'])
    merged = s.company.conn.execute(sa.select(c.party_merges.c.id, c.party_merges.c.merged_id, c.party_merges.c.survivor_id)
        .where(c.party_merges.c.party_kind == 'customer', c.party_merges.c.undone_at.is_(None),
               sa.or_(c.party_merges.c.merged_id == cid, c.party_merges.c.survivor_id == cid))).first()
    if merged is not None:
        add('customer_merged', f'Customer {name} is part of a customer merge; undo the merge first.',
            clears=f'party merge undo {merged.id}', merge=merged.id)
    n, k, a = c.notes, c.todos, c.attachment_links
    note = _first(s, sa.select(n.c.id).where(n.c.record_type == 'customer', n.c.record_id == cid, n.c.created_at >= since)
                  .order_by(n.c.created_at))
    if note is not None:
        add('later_note', f'A note added to customer {name} after the conversion depends on it (note {note.id}).',
            note=note.id)
    task = _first(s, sa.select(k.c.id, k.c.title).where(k.c.link_type == 'customer', k.c.link_id == cid,
                                                        k.c.created_at >= since).order_by(k.c.created_at))
    if task is not None:
        add('later_task', f"A to-do for customer {name} made after the conversion depends on it: {task.title} (to-do {task.id}).",
            todo=task.id)
    link = _first(s, sa.select(a.c.id).where(a.c.record_type == 'customer', a.c.record_id == cid, a.c.active.is_(True),
                                             a.c.linked_at >= since).order_by(a.c.linked_at))
    if link is not None:
        add('later_attachment', f'A file attached to customer {name} after the conversion depends on it.',
            attachment_link=link.id)
    # Accounting documents, and the estimates, sales orders and other work documents made for the customer since.
    docs = s.company.raw.execute(f"""SELECT id, type, number FROM (
        SELECT t.id, t.type, t.number, t.created_at FROM transactions t
        WHERE t.id IN ({party_merges._document_ids_sql()}) AND t.created_at >= :since
        UNION ALL SELECT d.id, d.kind, d.number, d.created_at FROM work_documents d
        JOIN work_revisions r ON r.id = d.current_revision_id WHERE r.customer_id = :party AND d.created_at >= :since)
        ORDER BY created_at, id LIMIT 3""", {'kind': 'customer', 'party': cid, 'since': since}).fetchall()
    if docs:
        named = ', '.join(f"{kind.replace('_', ' ')} {number or tid}" for tid, kind, number in docs)
        add('later_document', f'A document for customer {name} made after the conversion depends on it: {named}.',
            documents=[tid for tid, _, _ in docs])
    mappings = _mappings(s, conversion['id'])
    if (conversion['target'] == 'new' or mappings) and customer['version'] != conversion['customer_version_after']:
        add('customer_edited', f'Customer {name} changed after the conversion; reverse or undo that edit first.',
            customer=cid, expected_version=conversion['customer_version_after'], current_version=customer['version'])
    if conversion['target'] == 'new' and not docs:
        uses = batches._in_use(s, 'customer', [cid], set()).get(cid)
        # Notes, to-dos, files and a standing merge are named above; an undone merge is history, and the
        # conversion's own rows are what is being reversed.
        tables = [t for t in (uses or {}).get('tables', []) if not t.startswith('crm_conversion')
                  and t not in ('notes', 'todos', 'attachment_links', 'party_merges')]
        if tables:
            add('in_use', f"Customer {name} is used by {', '.join(uses['documents'] or tables)}.", customer=cid, tables=tables)
    return out


def _plan(s, ctx, batch, effects, done, probe, blockers):
    steps, work = [], []
    for effect in effects:
        if (RECORD, effect['record_id']) in done:
            continue
        conversion = _conversion(s, effect['record_id'])
        mappings = _mappings(s, conversion['id'])
        blockers.extend(problems(s, effect, conversion))
        if probe:
            try:
                _authority(s, conversion, mappings)
            except BookflowError as error:
                blockers.append(dict(seq=effect['seq'], record_type=RECORD, record_id=conversion['id'], code=error.code,
                                     message=error.message, details=error.details))
        lead = _lead(s, conversion['lead_id'])
        operation = ('retire the unused customer it created' if conversion['target'] == 'new'
                     else 'restore copied contact fields' if mappings else 'unlink the existing customer')
        step = dict(seq=effect['seq'], record_type=RECORD, record_id=conversion['id'],
            label=f"Conversion of lead {conversion['lead_label']} to customer {conversion['customer_label']}",
            version=lead['version'], steps=[dict(operation=operation, customer=conversion['customer_id'],
                lead=lead['id'], restores_status=conversion['lead_status_before'])])
        steps.append(step)
        work.append(dict(step, effect=effect))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(work))


def _customer_touched(update):
    mutation = update.mutation if hasattr(update, 'mutation') else update
    return Touched('customer', mutation.after['id'], 'update', int(mutation.before['version']), int(mutation.after['version']),
                   dict(mutation.after_snapshot), dict(mutation.before_snapshot), db='company')


def _run(s, ctx, batch, reversal_id, item):
    from bookflow.core.audit import write_event_to
    effect = item['effect']
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            if (RECORD, effect['record_id']) in batches._done_steps(s, batches._batch(s, batch['id'])):
                s.commits.commit(s.company, batches.OWNER)
                return
            conversion = _conversion(s, effect['record_id'])
            found = problems(s, effect, conversion)
            if found:
                raise BookflowError('E_BATCH_BLOCKED', message=found[0]['message'], details={'blockers': found})
            mappings = _mappings(s, conversion['id'])
            _authority(s, conversion, mappings)
            at, via, actor = clock.now_iso(), ctx.interface.value, s.actor.id
            touched = []
            customer = _customer(s, conversion['customer_id'])
            if conversion['target'] == 'new':
                update = parties.plan_party_update(s.company, 'customer', customer['id'],
                    {'name': batches.released_name(customer['name'], batch['id'])}, actor_id=actor, via=via, at=at)
                parties.persist_party_update(s.company, update)
                touched.append(_customer_touched(update))
                retire = parties.plan_party_active_change(s.company, 'customer', customer['id'], False, actor_id=actor,
                                                          via=via, at=at)
                parties.persist_party_active_change(s.company, retire)
                touched.extend(_customer_touched(x) for x in retire.mutations)
            elif mappings:
                restored = {x['customer_contact_id']: json.loads(x['before']) for x in mappings if x['action'] == 'updated'}
                added = {x['customer_contact_id'] for x in mappings if x['action'] == 'created'}
                current = _active_contacts(parties.read_party_collections(s.company, 'customer', customer['id'])['contacts'])
                submission = []
                for contact in current:
                    if contact['id'] in added:
                        continue
                    data = parties._contact_input_from_row(contact)
                    data.update(restored.get(contact['id'], {}))
                    submission.append(data)
                update = parties.plan_party_update(s.company, 'customer', customer['id'], {'contacts': submission},
                                                   actor_id=actor, via=via, at=at)
                if update.mutation is not None:
                    parties.persist_party_update(s.company, update)
                    touched.append(_customer_touched(update))
            lead = _lead(s, conversion['lead_id'])
            contacts = crm.contacts(s, lead['id'])
            row = dict(lead, status=conversion['lead_status_before'], close_reason=conversion['lead_close_reason_before'],
                       version=lead['version'] + 1, updated_at=at, updated_by=actor, updated_via=via)
            s.company.conn.execute(c.crm_leads.update().where(c.crm_leads.c.id == lead['id']).values(**row))
            touched.append(Touched('lead', lead['id'], 'update', lead['version'], row['version'], dict(row, contacts=contacts),
                                   dict(lead, contacts=contacts), db='company'))
            values = dict(state='reversed', reversed_at=at, reversed_by=actor, reversal_batch_id=reversal_id)
            s.company.conn.execute(c.crm_conversions.update().where(c.crm_conversions.c.id == conversion['id']).values(**values))
            touched.append(Touched(RECORD, conversion['id'], 'update', 1, 2, dict(conversion, **values), conversion, db='company'))
            event = write_event_to(s.company, ctx, 'batch reverse', f"Reversed {item['label']}", touched, actor_id=actor,
                                   actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
            batches.record_effect(s, reversal_id, record_type=RECORD, record_id=conversion['id'], action='reversed',
                version_after=row['version'], before=dict(of_seq=effect['seq'], command='batch reverse'), event_id=event)
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read, release_document_numbers=False,
                                 blocks=True, label='Lead conversions'))
