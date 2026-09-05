"""Non-posting customer work, immutable scope revisions and durable conversion."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert

from bookflow.company import schema as c, document_effects as effects, sales_calculations as calc
from bookflow.company import journal_custom_fields as custom, custom_fields as cf
from bookflow.company import work_defaults
from bookflow.company.work_facts import WorkFacts, WorkLineFacts
from bookflow.company.work_models import WorkLineInput
from bookflow.company.work_outputs import (
    WorkOutput, WorkWriteOutput, WorkSummaryOutput, WorkRevisionOutput,
    WorkRevisionSummary, WorkLineOutput, WorkLinkOutput, WorkPageOutput, WorkHistoryOutput,
)
from bookflow.company.sales_models import _invalid, money
from bookflow.company.sales import json_text, _changes, _custom_semantic
from bookflow.core import audit, clock, versioning
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id, is_ulid
from bookflow.core.money import Money
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.hub.users import common

KINDS = ('proposal', 'estimate', 'work_order')
QUOTE_STATES = ('draft', 'open', 'accepted', 'declined', 'superseded', 'cancelled')
WORK_STATES = ('draft', 'scheduled', 'in_progress', 'on_hold', 'complete', 'cancelled')
TABLE_KINDS = (('work_revisions', 'work_revision'), ('work_line_identities', 'work_line'),
               ('work_lines', 'work_revision_line'), ('work_links', 'work_link'))
LINE_COLUMNS = ('item_id', 'unit_id', 'quantity_microunits', 'completed_quantity_microunits',
    'unit_factor_nanounits', 'base_quantity_microunits', 'unit_price_minor_units',
    'net_minor_units', 'tax_minor_units', 'gross_minor_units', 'estimated_unit_cost_minor_units',
    'estimated_cost_minor_units', 'pricing_basis', 'markup_percent_millionths', 'billable')
AGREED_FIELDS = ('profile', 'scope', 'inclusions', 'exclusions', 'timing', 'commercial_terms')
OPERATIONAL_FIELDS = ('priority', 'site_address', 'assignees', 'scheduled_start', 'scheduled_end',
                      'actual_start', 'actual_end')


def rows(s, table, *where, order=None):
    return effects.rows(s, table, *where, order=order)


def resolve(s, selector, kind):
    t = c.work_documents
    key = t.c.id == selector.upper() if is_ulid(selector) else t.c.number == selector
    found = rows(s, t, key, t.c.kind == kind)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': kind})
    return found[0]


def revision(s, header, number=None):
    t = c.work_revisions
    found = rows(s, t, t.c.document_id == header['id'],
        t.c.id == header['current_revision_id'] if number is None else t.c.revision_number == number)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'work_revision'})
    return found[0]


def saved_lines(s, rev):
    return rows(s, c.work_lines, c.work_lines.c.revision_id == rev['id'], order=c.work_lines.c.position)


def facts(rev):
    return WorkFacts.model_validate_json(rev['facts_snapshot'])


def line_facts(line):
    return WorkLineFacts.model_validate_json(line['facts_snapshot'])


def _amounts(rev):
    return {key: Money(rev[column], rev['currency']).to_dict() for key, column in
            (('net', 'net_minor_units'), ('tax', 'tax_minor_units'), ('total', 'gross_minor_units'))}


def summary(header, rev):
    f = facts(rev)
    return WorkSummaryOutput(**header, **{k: rev[k] for k in ('date', 'title', 'customer_id',
        'currency', 'net_minor_units', 'tax_minor_units', 'gross_minor_units')},
        customer_name=f.profile.customer.label, **_amounts(rev),
        expired=bool(f.expires_on and f.expires_on < clock.now().date().isoformat()))


def revision_output(s, rev, pending=None, *, summary_only=False):
    pending = pending or {}
    supplied = pending.get('work_lines')
    if supplied is None:
        if summary_only:
            count = s.company.conn.execute(sa.select(sa.func.count()).select_from(c.work_lines)
                .where(c.work_lines.c.revision_id == rev['id'])).scalar_one()
            lines = []
        else:
            lines = saved_lines(s, rev)
            count = len(lines)
    else:
        lines = [row for row in supplied if row['revision_id'] == rev['id']]
        count = len(lines)
    values = {key: value for key, value in rev.items() if key not in ('facts_snapshot', 'custom_fields_snapshot')}
    if summary_only:
        return WorkRevisionSummary(**values, **_amounts(rev), line_count=count)
    identities = {row['id']: row for row in pending.get('work_line_identities', [])}
    missing = {line['line_id'] for line in lines} - identities.keys()
    if missing:
        identities.update({row['id']: row for row in rows(s, c.work_line_identities,
            c.work_line_identities.c.id.in_(missing))})
    output, known, complete = [], 0, True
    for line in lines:
        f, ident = line_facts(line), identities[line['line_id']]
        currency = rev['currency']
        def amount(value):
            return Money(value, currency).to_dict() if value is not None else None
        output.append(WorkLineOutput(**{key: line[key] for key in ('id', 'document_id', 'revision_id',
            'line_id', 'position', 'created_at', 'created_by', 'created_via')},
            **{key: ident[key] for key in ('root_document_id', 'root_line_id', 'source_line_id')},
            facts=f, quantity=format_quantity_micro_units(f.quantity_microunits),
            completed_quantity=format_quantity_micro_units(f.completed_quantity_microunits),
            unit_price=amount(f.unit_price_minor_units), net=amount(f.net_minor_units),
            tax=amount(f.tax_minor_units), total=amount(f.gross_minor_units),
            estimated_unit_cost=amount(f.estimated_unit_cost_minor_units), estimated_cost=amount(f.estimated_cost_minor_units)))
        if f.estimated_cost_minor_units is None:
            complete = False
        else:
            known = calc.total((known, f.estimated_cost_minor_units), 'known_cost_total')
    snapshot = json.loads(rev['custom_fields_snapshot'])
    return WorkRevisionOutput(**values, **_amounts(rev), line_count=count, facts=facts(rev),
        custom_fields_snapshot=snapshot, custom_fields=custom.project(snapshot), lines=output,
        known_cost_total=Money(known, rev['currency']).to_dict(), cost_complete=complete,
        estimated_profit=Money(rev['net_minor_units'] - known, rev['currency']).to_dict() if complete else None)


def links(s, document_id, pending=None, headers=None):
    t = c.work_links
    existing = list(s.company.conn.execute(sa.select(t).where(sa.or_(
        t.c.source_document_id == document_id, t.c.destination_document_id == document_id))
        .order_by(t.c.created_at, t.c.id).limit(201)).mappings())
    all_links = [dict(row) for row in existing] + [row for row in (pending or {}).get('work_links', [])
        if document_id in (row['source_document_id'], row['destination_document_id'])]
    known = {row['id']: row for row in headers or []}
    needed = {row[k] for row in all_links[:200] for k in ('source_document_id', 'destination_document_id')} - known.keys()
    if needed:
        known.update({row['id']: row for row in rows(s, c.work_documents, c.work_documents.c.id.in_(needed))})
    out = []
    for row in all_links[:200]:
        value = {key: item for key, item in row.items() if key not in ('conversion_key_hash', 'request_hash')}
        for side in ('source', 'destination'):
            head = known[row[side + '_document_id']]
            value.update({side + '_' + key: head[key] for key in ('kind', 'number', 'status', 'current_revision_id')})
        out.append(WorkLinkOutput(**value))
    return out, len(all_links) > 200


def output(s, header, rev, pending=None, headers=None):
    linked, more = links(s, header['id'], pending, headers)
    return WorkOutput(**summary(header, rev).model_dump(), revision=revision_output(s, rev, pending),
                      links=linked, links_has_more=more)


def show(s, inp, kind):
    h = resolve(s, getattr(inp, kind), kind)
    return output(s, h, revision(s, h, inp.revision_number))


def page(s, ctx, inp, kind, *, history=False):
    from bookflow.company.query import page_state, continuation
    class Contract:
        cursor = inp.cursor
        query = None
        def model_dump(self, **kw):
            return inp.model_dump(**kw)
    state = page_state(s, kind + (' history' if history else ' query'), Contract(), ctx.on_behalf_of)
    t, r = c.work_documents, c.work_revisions
    if history:
        h = resolve(s, getattr(inp, kind), kind)
        query = sa.select(r).where(r.c.document_id == h['id']).order_by(r.c.revision_number)
    else:
        query = sa.select(t).join(r, r.c.id == t.c.current_revision_id).where(t.c.kind == kind)
        if inp.customer:
            from bookflow.company.parties import resolve_party
            customer = resolve_party(s.company, 'customer', inp.customer)
            query = query.where(r.c.customer_id == customer['id'])
        if inp.date_from:
            query = query.where(r.c.date >= inp.date_from)
        if inp.date_to:
            query = query.where(r.c.date <= inp.date_to)
        if inp.status:
            if inp.status not in (WORK_STATES if kind == 'work_order' else QUOTE_STATES):
                raise _invalid('status', 'select a status applicable to this document kind')
            query = query.where(t.c.status == inp.status)
        if inp.active is not None:
            query = query.where(t.c.active == inp.active)
        for key in ('number', 'title'):
            if getattr(inp, key):
                query = query.where(getattr(r.c, key).contains(getattr(inp, key), autoescape=True))
        currency = s.company_info_row['home_currency']
        low = money(inp.minimum_net, currency, 'minimum_net').minor_units if inp.minimum_net is not None else None
        high = money(inp.maximum_net, currency, 'maximum_net').minor_units if inp.maximum_net is not None else None
        if low is not None and high is not None and low > high:
            raise _invalid('maximum_net', 'must be at least minimum_net')
        if low is not None:
            query = query.where(r.c.net_minor_units >= low)
        if high is not None:
            query = query.where(r.c.net_minor_units <= high)
        query = query.order_by(r.c.date, t.c.id)
    found = [dict(row) for row in s.company.conn.execute(query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    shared = dict(count=len(found), has_more=more, next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence)
    if history:
        return WorkHistoryOutput(**{key: h[key] for key in ('id', 'version', 'number', 'current_revision_id', 'status')},
            items=[revision_output(s, row, summary_only=True) for row in found], **shared)
    return WorkPageOutput(items=[summary(h, revision(s, h)) for h in found], **shared)


def _semantic(rev, lines):
    value = {key: rev[key] for key in ('date', 'number', 'title', 'status', 'active', 'decision_note')}
    value.update(facts=json.loads(rev['facts_snapshot']),
        custom_fields=_custom_semantic(json.loads(rev['custom_fields_snapshot'])),
        lines=[{'line_id': line['line_id'], 'facts': json.loads(line['facts_snapshot'])} for line in lines])
    return value


def _version(s, header, expected):
    writer = versioning.current_writer(s.company, 'work_document', header['id'], header)
    from bookflow.company.info import principal_names
    if writer:
        names = principal_names(s.company, {value for value in (writer.updated_by, writer.on_behalf_of) if value})
        writer.updated_by_name = names.get(writer.updated_by)
        writer.on_behalf_of_name = names.get(writer.on_behalf_of)
    def history(v):
        found = versioning.history_from_entries(s.company, 'work_document', header['id'], v, audit.decode_snapshot)
        for entry in found:
            entry.changed_columns = ['work_document']
        return found
    try:
        return versioning.check_update(current_version=header['version'], current_updated_at=header['updated_at'],
            current_writer=writer, changes={'work_document'}, expected_version=expected, history_since=history,
            actor_id=s.actor.id, window_seconds=s.company_info_row.get('recent_activity_window_seconds', 60))
    except BookflowError as exc:
        if exc.code == 'E_VERSION_CONFLICT':
            older = rows(s, c.work_revisions, c.work_revisions.c.document_id == header['id'],
                c.work_revisions.c.revision_number == expected)
            current = revision(s, header)
            fields = _changes(_semantic(older[0], saved_lines(s, older[0])),
                _semantic(current, saved_lines(s, current))) if older else ['version']
            exc.details['changed_fields'] = fields or ['links']
            from bookflow.core.versioning import _conflict_message
            exc.message = _conflict_message(exc.details, exc.details['changed_fields'])
        raise


def _number(s, kind, explicit, own=None):
    t = c.work_documents
    def occupied(value):
        query = sa.select(t.c.id).where(t.c.kind == kind, t.c.number == value)
        if own:
            query = query.where(t.c.id != own)
        return s.company.conn.execute(query).first() is not None
    if explicit is not None:
        if occupied(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': kind})
        return explicit, None
    found = rows(s, c.sequences, c.sequences.c.name == kind)
    value, prefix = (found[0]['next_number'], found[0]['prefix']) if found else (1, '')
    while occupied(f'{prefix}{value}'):
        value += 1
    if value >= 9223372036854775807:
        raise BookflowError('E_VALUE_RANGE', details={'field': 'next_number'})
    return f'{prefix}{value}', dict(name=kind, next_number=value + 1, prefix=prefix)


def _reason(ctx):
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')


def _agreed(value):
    return {key: value[key] for key in ('date', 'number', 'title', 'lines', 'custom_fields')} | {
        'facts': {key: item for key, item in value['facts'].items() if key not in ('memo', *OPERATIONAL_FIELDS)}}


def _lifecycle(kind, old, value, inp, ctx):
    status = value['status']
    if status not in (WORK_STATES if kind == 'work_order' else QUOTE_STATES):
        raise _invalid('status', 'status does not apply to this document kind')
    if old is None:
        if status != 'draft':
            raise _invalid('status', 'new work documents begin as draft')
        return
    prior = old['status']
    if kind != 'work_order':
        if prior == 'accepted' and _agreed(old) != _agreed(value):
            _reason(ctx)
            value['status'] = status = 'draft'
        allowed = {'draft': {'open', 'accepted', 'declined', 'cancelled'},
            'open': {'draft', 'accepted', 'declined', 'cancelled'},
            'accepted': {'draft', 'superseded', 'cancelled'},
            'declined': {'draft'}, 'superseded': {'draft'}, 'cancelled': {'draft'}}
        if status != prior and status not in allowed[prior]:
            raise _invalid('status', f'cannot change {prior} directly to {status}')
        if status != prior and (status in ('cancelled', 'superseded') or prior in ('accepted', 'declined', 'superseded', 'cancelled')):
            _reason(ctx)
        if status != prior and status in ('accepted', 'declined', 'superseded') and not value['decision_note']:
            raise _invalid('decision_note', 'record the decision when accepting, declining or superseding')
        expiry = value['facts']['expires_on']
        if status == 'accepted' and prior != status and expiry and expiry < clock.now().date().isoformat() and not getattr(inp, 'acknowledge_expired', False):
            raise _invalid('acknowledge_expired', 'explicitly acknowledge that the estimate has expired')
    else:
        ordinary = {'draft', 'scheduled', 'in_progress', 'on_hold'}
        if status != prior:
            allowed = ordinary | {'complete', 'cancelled'} if prior in ordinary else {'in_progress'} if prior == 'complete' else {'draft'}
            if status not in allowed:
                raise _invalid('status', f'cannot change {prior} directly to {status}')
            if prior in ('complete', 'cancelled') or status == 'cancelled':
                _reason(ctx)
            if prior == 'complete':
                value['facts']['actual_end'] = None
        if prior == 'complete' and status == 'complete':
            before_scope = {key: old['facts'][key] for key in ('scope', 'inclusions', 'exclusions', 'timing', 'commercial_terms')}
            after_scope = {key: value['facts'][key] for key in before_scope}
            before_lines = [(line['line_id'], line['facts']['quantity_microunits'], line['facts']['completed_quantity_microunits']) for line in old['lines']]
            after_lines = [(line['line_id'], line['facts']['quantity_microunits'], line['facts']['completed_quantity_microunits']) for line in value['lines']]
            if before_scope != after_scope or before_lines != after_lines:
                raise _invalid('status', 'reopen complete work with a reason before changing scope or quantities')
    _state_invariants(kind, value)


def _state_invariants(kind, value):
    f, status = value['facts'], value['status']
    if f['expires_on'] and f['expires_on'] < value['date']:
        raise _invalid('expires_on', 'must not precede the document date')
    if kind != 'work_order':
        return
    for prefix in ('scheduled', 'actual'):
        start, end = f[prefix + '_start'], f[prefix + '_end']
        if end and (not start or clock.parse_iso(end) < clock.parse_iso(start)):
            raise _invalid(prefix + '_end', 'requires its start and cannot precede it')
    if status == 'scheduled' and not f['scheduled_start']:
        raise _invalid('scheduled_start', 'scheduled work needs a scheduled start')
    if status in ('in_progress', 'complete') and not f['actual_start']:
        raise _invalid('actual_start', 'record when work began')
    if status == 'complete' and (not f['actual_end'] or any(
            line['facts']['completed_quantity_microunits'] != line['facts']['quantity_microunits'] for line in value['lines'])):
        raise _invalid('status', 'complete work needs an actual end and all ordered quantities completed')


def _dependencies(s, header, before, after):
    if header is None:
        return
    t = c.work_links
    if header['kind'] == 'estimate':
        linked = rows(s, t, t.c.source_document_id == header['id'], t.c.relation == 'estimate_work_order')
        if linked and (_agreed(before) != _agreed(after) or after['status'] != 'accepted'):
            raise BookflowError('E_WORK_DEPENDENCY', details={'destination_id': linked[0]['destination_document_id'],
                'problem': 'agreed estimate already has a work order'})
    elif header['kind'] == 'work_order':
        linked = rows(s, t, t.c.destination_document_id == header['id'], t.c.relation == 'estimate_work_order')
        if not linked:
            return
        if before['title'] != after['title'] or any(before['facts'][key] != after['facts'][key] for key in AGREED_FIELDS):
            raise BookflowError('E_WORK_DEPENDENCY', details={'source_id': linked[0]['source_document_id'], 'problem': 'quoted work-order agreement is frozen'})
        identities = rows(s, c.work_line_identities, c.work_line_identities.c.document_id == header['id'])
        quoted = {row['id'] for row in identities if row['root_document_id'] != header['id']}
        old = {row['line_id']: row['facts'] for row in before['lines']}
        new = {row['line_id']: row['facts'] for row in after['lines']}
        def economic(value):
            return {key: item for key, item in value.items() if key not in ('billable', 'completed_quantity_microunits')}
        if any(key not in new or economic(old[key]) != economic(new[key]) for key in quoted if key in old):
            raise BookflowError('E_WORK_DEPENDENCY', details={'source_id': linked[0]['source_document_id'], 'problem': 'quoted lines cannot be removed or repriced'})


def _assignees(s, requested, previous=()):
    from bookflow.company.parties import resolve_party
    from bookflow.company.sales_facts import Reference
    saved = {entry.id: entry for entry in previous}
    result, seen = [], set()
    for selector in requested:
        row = resolve_party(s.company, 'employee', selector)
        if row['id'] in seen:
            raise _invalid('assignees', 'select each employee once')
        seen.add(row['id'])
        if row['id'] in saved:
            result.append(saved[row['id']])
        else:
            if not row['active']:
                raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'employee', 'record_id': row['id']})
            result.append(Reference(id=row['id'], label=row['name'], version=row['version']))
    return result


def _resolve_facts(s, inp, kind, old=None, old_date=None):
    profile, warnings = work_defaults.resolve_header(s, inp, kind, previous=old.profile if old else None, old_date=old_date)
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    issuer = {key: value for key, value in info.items() if key in ('id', 'legal_name', 'display_name', 'home_currency')
              or key.startswith(('address_', 'legal_address_', 'ship_address_'))}
    if old and not inp.refresh_defaults:
        issuer = old.issuer_snapshot
    values = old.model_dump() if old else {}
    values.update(profile=profile, issuer_snapshot=issuer)
    for key in WorkFacts.model_fields:
        if key in ('schema_version', 'profile', 'issuer_snapshot', 'assignees'):
            continue
        if key in inp.model_fields_set:
            values[key] = getattr(inp, key)
    if not old and 'site_address' not in inp.model_fields_set and kind == 'work_order':
        values['site_address'] = profile.shipping_address
    if 'assignees' in inp.model_fields_set:
        values['assignees'] = _assignees(s, inp.assignees, old.assignees if old else ())
    return WorkFacts.model_validate(values), warnings


def _resolve_lines(s, inp, kind, profile, old_rev=None, old_profile=None):
    before = saved_lines(s, old_rev) if old_rev else []
    prior = {row['line_id']: row for row in before}
    entered = inp.lines if inp.lines is not None else [WorkLineInput(item=row['item_id'], line_id=row['line_id']) for row in before]
    if len(entered) > 200 or (kind == 'estimate' and not entered):
        raise _invalid('lines', 'estimates need 1–200 lines; proposals/work orders allow 0–200')
    seen, out, warnings = set(), [], []
    for line in entered:
        key = line.line_id.upper() if line.line_id and is_ulid(line.line_id) else line.line_id
        if key is not None:
            if key not in prior or key in seen:
                raise _invalid('line_id', 'select a unique line identity from the current revision; removed identities cannot return')
            seen.add(key)
        resolved, line_warnings = work_defaults.resolve_line(s, line, profile,
            previous=line_facts(prior[key]) if key else None, previous_header=old_profile,
            refresh=inp.refresh_defaults, kind=kind)
        out.append({'line_id': key, 'facts': resolved.model_dump(mode='json')})
        warnings.extend(line_warnings)
    return out, warnings


def _custom_plan(s, inp, kind, document_id, old_rev=None, *, carry=None):
    patch = inp.custom_fields
    warnings = []
    if carry is not None:
        source = json.loads(carry['custom_fields_snapshot'])
        definitions = {row['id']: row for row in cf._applicable_definitions(s.company.conn, kind)}
        mapping = {}
        for key, value in source.items():
            definition = definitions.get(key)
            problem = None
            if not definition or not definition['active']:
                problem = 'definition is inactive or not enabled for this destination'
            elif definition['kind'] != value['kind']:
                problem = 'definition kind no longer matches'
            elif value['kind'] == 'choice':
                matches = [choice for choice in cf._choice_rows(s.company.conn, key)
                    if choice['active'] and choice['id'] == value['choice_id']]
                if not matches:
                    problem = 'saved choice is no longer eligible'
                else:
                    mapping[key] = matches[0]['value']
            if problem:
                warnings.append(f'custom_fields.{key}: omitted carried value; {problem}')
            elif key not in mapping:
                mapping[key] = value['value']
        mapping.update(patch.root)
        patch = cf.CustomFieldValuePatch.model_validate(mapping)
    custom.validate_kinds(s.company, inp.custom_fields, inp.custom_field_kinds, record_type=kind)
    planned = custom.prepare(s.company, document_id, patch,
        json.loads(old_rev['custom_fields_snapshot']) if old_rev else {}, creating=old_rev is None,
        refresh=getattr(inp, 'refresh_defaults', False), record_type=kind)
    if carry is not None:
        for key in planned.snapshot:
            if key not in json.loads(carry['custom_fields_snapshot']):
                warnings.append(f'custom_fields.{key}: destination value added by explicit input or current creation default')
    return planned, warnings


def _carry_warnings(s, source, lines):
    f = facts(source)
    references = {(c.customers.name, f.profile.customer.id): c.customers}
    for line in lines:
        lf = line_facts(line)
        references[(c.items.name, lf.item_id)] = c.items
        if lf.unit_id:
            references[(c.unit_conversions.name, lf.unit_id)] = c.unit_conversions
        references[(c.accounts.name, lf.profile.income_account.id)] = c.accounts
        for component in lf.taxes:
            rule = component.rule
            references[(c.items.name, rule.id)] = c.items
            references[(c.vendors.name, rule.agency.id)] = c.vendors
            references[(c.accounts.name, rule.liability_account.id)] = c.accounts
    warnings = []
    for (name, key), table in references.items():
        found = rows(s, table, table.c.id == key)
        if not found:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': name, 'record_id': key})
        if not found[0]['active']:
            warnings.append(f'{name}.{key}: preserving inactive historical reference for non-posting work')
    return warnings


def _accepted_group(s, header, status):
    if header['kind'] != 'estimate' or status != 'accepted':
        return
    t = c.work_documents
    found = rows(s, t, t.c.kind == 'estimate', t.c.estimate_group_id == header['estimate_group_id'],
        t.c.status == 'accepted', t.c.id != header['id'])
    if found:
        raise BookflowError('E_WORK_DEPENDENCY', details={'source_id': found[0]['id'],
            'problem': 'explicitly supersede the currently accepted alternative first'})


def _new_revision(s, ctx, header, value, custom_snapshot, old_rev, pending, event, at, *, acceptance=None, source_lines=None, inherit_roots=False):
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    rev_id = new_id()
    accepted = acceptance or dict(accepted_revision_id=None, accepted_at=None, accepted_by=None)
    if value['status'] == 'accepted' and not accepted['accepted_revision_id']:
        accepted = dict(accepted_revision_id=rev_id, accepted_at=at, accepted_by=s.actor.id)
    if value['status'] != 'accepted':
        accepted = dict(accepted_revision_id=None, accepted_at=None, accepted_by=None)
    lf = [WorkLineFacts.model_validate(line['facts']) for line in value['lines']]
    net = calc.total((line.net_minor_units for line in lf), 'net')
    tax = calc.total((line.tax_minor_units for line in lf), 'tax')
    rev = dict(id=rev_id, document_id=header['id'], revision_number=old_rev['revision_number'] + 1 if old_rev else 1,
        supersedes_revision_id=old_rev['id'] if old_rev else None,
        **{key: value[key] for key in ('date', 'number', 'title', 'status', 'active', 'decision_note')},
        customer_id=value['facts']['profile']['customer']['id'], currency=s.company_info_row['home_currency'],
        net_minor_units=net, tax_minor_units=tax, gross_minor_units=calc.total((net, tax)),
        **accepted, facts_snapshot=json_text(value['facts']), custom_fields_snapshot=json_text(custom_snapshot),
        audit_event_id=event, **provenance)
    pending['work_revisions'].append(rev)
    header.update(current_revision_id=rev_id, number=rev['number'], status=rev['status'], active=rev['active'])
    for position, (entry, line) in enumerate(zip(value['lines'], lf), 1):
        identity = entry['line_id']
        if identity is None:
            identity = new_id()
            source = source_lines[position - 1] if source_lines is not None else None
            root_doc, root_line = header['id'], identity
            if source and inherit_roots:
                root = rows(s, c.work_line_identities, c.work_line_identities.c.id == source['line_id'])[0]
                root_doc, root_line = root['root_document_id'], root['root_line_id']
            pending['work_line_identities'].append(dict(id=identity, document_id=header['id'],
                root_document_id=root_doc, root_line_id=root_line, source_line_id=source['id'] if source else None))
        pending['work_lines'].append(dict(id=new_id(), document_id=header['id'], revision_id=rev_id,
            line_id=identity, position=position, **{key: getattr(line, key) for key in LINE_COLUMNS},
            facts_snapshot=line.model_dump_json(), **provenance))
    return rev


def _fingerprint(s, inp, kind, operation, value, source, warnings):
    content = deepcopy(value)
    # Automatic end time is generated at commit, not a master fact to stale a preview.
    if operation == 'complete' and 'actual_end' not in inp.model_fields_set and source and not facts(source).actual_end:
        content['facts']['actual_end'] = '$command_time'
    token = dict(company=s.company_row['id'], kind=kind, operation=operation,
        source_revision=source['id'] if source else None, value=content, warnings=warnings)
    digest = hashlib.sha256(json_text(token).encode()).hexdigest()
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != digest:
        raise BookflowError('E_PREVIEW_STALE', details={'facts_fingerprint': digest})
    return digest


def prepare(s, ctx, inp, kind, operation):
    if operation in ('copy', 'estimate', 'work-order'):
        return _prepare_destination(s, ctx, inp, kind, operation)
    old = resolve(s, getattr(inp, kind), kind) if operation != 'create' else None
    old_rev = revision(s, old) if old else None
    if old:
        _version(s, old, inp.expected_version)
    at, event = clock.now_iso(), new_id()
    header = dict(old) if old else dict(id=new_id(), kind=kind, **common(s.actor.id, ctx.interface.value, at), estimate_group_id=None)
    if not old and kind == 'estimate':
        header['estimate_group_id'] = header['id']
    old_lines = saved_lines(s, old_rev) if old else []
    before = _semantic(old_rev, old_lines) if old else None
    warnings, sequence = [], None
    if operation == 'complete':
        value = deepcopy(before)
        value['status'] = 'complete'
        for key in ('actual_start', 'actual_end'):
            if key in inp.model_fields_set:
                value['facts'][key] = getattr(inp, key)
        if value['facts']['actual_end'] is None:
            value['facts']['actual_end'] = at
        for line in value['lines']:
            line['facts']['completed_quantity_microunits'] = line['facts']['quantity_microunits']
        custom_plan = None
        custom_snapshot = json.loads(old_rev['custom_fields_snapshot'])
    else:
        old_facts = facts(old_rev) if old else None
        resolved, warnings = _resolve_facts(s, inp, kind, old_facts, old_rev['date'] if old else None)
        line_values, line_warnings = _resolve_lines(s, inp, kind, resolved.profile, old_rev, old_facts.profile if old else None)
        warnings += line_warnings
        custom_plan, custom_warnings = _custom_plan(s, inp, kind, header['id'], old_rev)
        warnings += custom_warnings
        number, sequence = _number(s, kind, inp.number if inp.number is not None else old['number'] if old else None, header['id'])
        def picked(key, default=None):
            return getattr(inp, key) if key in inp.model_fields_set else before[key] if before else default
        value = dict(date=picked('date'), number=number, title=picked('title'),
            status=picked('status', 'draft'), active=picked('active', True), decision_note=picked('decision_note'),
            facts=resolved.model_dump(mode='json'), lines=line_values,
            custom_fields=_custom_semantic(custom_plan.snapshot))
        custom_snapshot = custom_plan.snapshot
    _lifecycle(kind, before, value, inp, ctx)
    _state_invariants(kind, value)
    _dependencies(s, old, before, value)
    _accepted_group(s, header, value['status'])
    fingerprint = _fingerprint(s, inp, kind, operation, value, old_rev, warnings)
    changes = _changes(before, value) if before else list(value)
    if old and not changes and (custom_plan is None or not custom_plan.changed):
        return Plan(WorkWriteOutput(**output(s, old, old_rev).model_dump(), changed=False,
            facts_fingerprint=fingerprint, warnings=warnings),
            dict(input=inp, kind=kind, operation=operation, changed=False))
    if old:
        header.update(version=old['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    pending = {table: [] for table, _ in TABLE_KINDS}
    accepted = {key: old_rev[key] for key in ('accepted_revision_id', 'accepted_at', 'accepted_by')} if old_rev else None
    rev = _new_revision(s, ctx, header, value, custom_snapshot, old_rev, pending, event, at, acceptance=accepted)
    view = output(s, header, rev, pending, [header])
    return Plan(WorkWriteOutput(**view.model_dump(), facts_fingerprint=fingerprint,
        changed_fields=changes, warnings=warnings), dict(input=inp, kind=kind, operation=operation,
        changed=True, headers=[header], before={old['id']: old} if old else {}, pending=pending,
        customs=[custom_plan] if custom_plan else [], sequence=sequence, event=event, semantic=value))


def _prepare_destination(s, ctx, inp, kind, operation):
    source = resolve(s, getattr(inp, kind), kind)
    source_rev = revision(s, source)
    destination_kind = kind if operation == 'copy' else 'estimate' if operation == 'estimate' else 'work_order'
    relation = 'copy' if operation == 'copy' else 'proposal_estimate' if kind == 'proposal' else 'estimate_work_order'
    key_hash = request_hash = None
    if relation != 'copy':
        key_hash = hashlib.sha256(inp.conversion_key.encode()).hexdigest()
        intent = inp.model_dump(mode='json', exclude={'expected_facts_fingerprint'})
        request_hash = hashlib.sha256(json_text(dict(kind=kind, operation=operation, input=intent)).encode()).hexdigest()
        matches = rows(s, c.work_links, c.work_links.c.conversion_key_hash == key_hash)
        if matches:
            match = matches[0]
            if match['request_hash'] != request_hash:
                raise BookflowError('E_CONVERSION_KEY_REUSED', details={'destination_id': match['destination_document_id']})
            destination = resolve(s, match['destination_document_id'], destination_kind)
            return Plan(WorkWriteOutput(**output(s, destination, revision(s, destination)).model_dump(),
                changed=False, idempotent_replay=True), dict(input=inp, kind=kind, operation=operation, changed=False))
    _version(s, source, inp.expected_version)
    if relation != 'copy' and not source['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': kind, 'record_id': source['id']})
    if relation == 'proposal_estimate' and source['status'] not in ('draft', 'open', 'accepted'):
        raise _invalid('proposal', 'select an active draft, open or accepted proposal')
    if relation == 'estimate_work_order':
        if source['status'] != 'accepted' or source_rev['accepted_revision_id'] is None:
            raise _invalid('estimate', 'explicitly accept the estimate before creating its work order')
        existing = rows(s, c.work_links, c.work_links.c.source_document_id == source['id'],
            c.work_links.c.relation == 'estimate_work_order')
        if existing:
            raise BookflowError('E_WORK_DEPENDENCY', details={'destination_id': existing[0]['destination_document_id'],
                'problem': 'this estimate already has its work order'})
    source_lines = saved_lines(s, source_rev)
    if destination_kind == 'estimate' and not source_lines:
        raise _invalid('lines', 'an estimate needs at least one priced line in the selected source')
    at, event = clock.now_iso(), new_id()
    header = dict(id=new_id(), kind=destination_kind, **common(s.actor.id, ctx.interface.value, at), estimate_group_id=None)
    if destination_kind == 'estimate':
        header['estimate_group_id'] = source['id'] if relation == 'proposal_estimate' else (
            source['estimate_group_id'] if inp.copy_mode == 'alternative' else header['id'])
    f = facts(source_rev).model_dump(mode='json')
    f.update(expires_on=getattr(inp, 'expires_on', None), assignees=[], scheduled_start=None,
             scheduled_end=None, actual_start=None, actual_end=None)
    if destination_kind == 'work_order':
        f['site_address'] = f['profile']['shipping_address'] if relation == 'estimate_work_order' else f['site_address']
        for field in ('scheduled_start', 'scheduled_end'):
            if field in inp.model_fields_set:
                f[field] = getattr(inp, field)
        if 'assignees' in inp.model_fields_set:
            f['assignees'] = [value.model_dump() for value in _assignees(s, inp.assignees)]
    WorkFacts.model_validate(f)
    warnings = _carry_warnings(s, source_rev, source_lines)
    custom_plan, custom_warnings = _custom_plan(s, inp, destination_kind, header['id'], carry=source_rev)
    warnings += custom_warnings
    number, sequence = _number(s, destination_kind, inp.number, header['id'])
    value = dict(date=inp.date, number=number, title=getattr(inp, 'title', None) or source_rev['title'],
        status='draft', active=True, decision_note=None, facts=f, custom_fields=_custom_semantic(custom_plan.snapshot),
        lines=[dict(line_id=None, facts=dict(json.loads(line['facts_snapshot']), completed_quantity_microunits=0)) for line in source_lines])
    _state_invariants(destination_kind, value)
    fingerprint = _fingerprint(s, inp, kind, operation, value, source_rev, warnings)
    pending = {table: [] for table, _ in TABLE_KINDS}
    rev = _new_revision(s, ctx, header, value, custom_plan.snapshot, None, pending, event, at,
        source_lines=source_lines, inherit_roots=relation == 'estimate_work_order')
    headers, before = [header], {}
    source_current = source
    if relation != 'copy':
        source_current = dict(source, version=source['version'] + 1,
            updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        _new_revision(s, ctx, source_current, _semantic(source_rev, source_lines),
            json.loads(source_rev['custom_fields_snapshot']), source_rev, pending, event, at,
            acceptance={key: source_rev[key] for key in ('accepted_revision_id', 'accepted_at', 'accepted_by')})
        headers.append(source_current)
        before[source['id']] = source
    pending['work_links'].append(dict(id=new_id(), source_document_id=source['id'],
        source_revision_id=source_rev['id'], source_version=source['version'],
        destination_document_id=header['id'], destination_revision_id=rev['id'], relation=relation,
        conversion_key_hash=key_hash, request_hash=request_hash,
        created_at=at, created_by=s.actor.id, created_via=ctx.interface.value))
    view = output(s, header, rev, pending, [header, source_current])
    return Plan(WorkWriteOutput(**view.model_dump(), facts_fingerprint=fingerprint,
        warnings=warnings, changed_fields=['created', 'source_link']),
        dict(input=inp, kind=kind, operation=operation, changed=True, headers=headers, before=before,
            pending=pending, customs=[custom_plan], sequence=sequence, event=event, semantic=value))


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'], plan.data['kind'], plan.data['operation'])
    from bookflow.company.work_validation import validate
    validate(fresh, s, ctx)
    if not fresh.data['changed']:
        return Applied(fresh.preview, [], 'no change')
    data, touched = fresh.data, []
    for header in data['headers']:
        old = data['before'].get(header['id'])
        touched.append(Touched('work_document', header['id'], 'update' if old else 'create',
            old['version'] if old else None, header['version'], header, old, db='company'))
    for table, kind in TABLE_KINDS:
        touched.extend(Touched(kind, row['id'], 'create', None, 1, effects.decoded(row), db='company')
                       for row in data['pending'][table])
    for planned in data['customs']:
        touched.extend(custom.touches(planned))
    command_name = data['kind'].replace('_', '-') + ' ' + data['operation']
    summary_text = f"{data['operation']} {fresh.preview.kind.replace('_', ' ')} {fresh.preview.number}"
    audit.write_event_to(s.company, ctx, command_name, summary_text, touched,
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    for header in data['headers']:
        old = data['before'].get(header['id'])
        if old:
            changed = s.company.conn.execute(c.work_documents.update().where(c.work_documents.c.id == header['id'],
                c.work_documents.c.version == old['version']).values(**header)).rowcount
            if changed != 1:
                raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id']})
        else:
            s.company.conn.execute(c.work_documents.insert().values(**header))
    for table, _ in TABLE_KINDS:
        if data['pending'][table]:
            s.company.conn.execute(getattr(c, table).insert(), data['pending'][table])
    for planned in data['customs']:
        custom.apply(s.company, planned)
    if data['sequence']:
        statement = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(statement.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(fresh.preview, touched, summary_text, audited=True)
