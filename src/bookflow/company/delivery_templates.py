"""Delivery templates (R112; design/document-delivery.md §3): a subject and plain-text body per document kind.

Exactly one active default per kind (a partial unique index). Making a template the default retires the old default
in the same write, before the new one is marked, so the index never sees two. A default cannot be deactivated: make
another the default first. Every save checks the text against the closed list of merge fields for its kind.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import delivery_merge as merge
from bookflow.company import delivery_models as m
from bookflow.company import schema as c
from bookflow.company.delivery_schema import KINDS
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

RECORD = 'delivery_template'


def _invalid(field_name, problem):
    return BookflowError('E_VALIDATION', message=f'{field_name}: {problem}',
                         details={'fields': [{'field': field_name, 'problem': problem}]})


def output(s, row, warnings=(), schedules=()) -> m.TemplateOutput:
    from bookflow.core.session import localize
    warnings = list(warnings)
    if schedules:
        warnings.append('Live email schedule(s) ' + ', '.join(schedules) + ' return to draft: a person confirmed the '
                        'old text; their messages wait for a go until a person makes each live again.')
    return m.TemplateOutput(id=row['id'], version=row['version'], kind=row['kind'], name=row['name'],
                            subject=row['subject'], body=row['body'], is_default=bool(row['is_default']),
                            active=bool(row['active']), updated_at=localize(s, row['updated_at']),
                            updated_by=row['updated_by'], schedules_to_draft=list(schedules), warnings=warnings)


def _schedules(s, ctx, kind, name, touched) -> list[str]:
    """Live schedules whose confirmed rule covered this kind's template text or default: back to draft, with why and
    one to-do each (design/document-delivery.md §6)."""
    from bookflow.company import delivery_schedules
    names, written = delivery_schedules.recheck_live(s, ctx, kind, f'its email template {name} changed')
    touched += written
    return names


def resolve(s, selector, kind=None) -> dict:
    t = c.delivery_templates
    q = sa.select(t).where(sa.or_(t.c.id == selector, t.c.name_key == selector.strip().casefold()))
    if kind:
        q = q.where(t.c.kind == kind)
    rows = [dict(r) for r in s.company.conn.execute(q).mappings()]
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': selector})
    if len(rows) > 1:
        raise _invalid('kind', f'{selector!r} names a template of several kinds; give kind')
    return rows[0]


def _name_taken(s, kind, name, own_id=None):
    t = c.delivery_templates
    q = sa.select(t.c.id).where(t.c.kind == kind, t.c.name_key == name.strip().casefold())
    if own_id:
        q = q.where(t.c.id != own_id)
    if s.company.conn.execute(q).first():
        raise _invalid('name', f'a {kind} template is already called {name.strip()!r}')


def _current_default(s, kind):
    t = c.delivery_templates
    row = s.company.conn.execute(sa.select(t).where(t.c.kind == kind, t.c.is_default.is_(True),
                                                    t.c.active.is_(True))).mappings().first()
    return dict(row) if row else None


def _stamp(s, ctx, row, at):
    return dict(row, version=row['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)


def _name(name):
    if not name.strip():
        raise _invalid('name', 'is blank')


def prepare_create(s, ctx, inp) -> Plan:
    _name(inp.name)
    merge.check(inp.kind, inp.subject, inp.body)
    _name_taken(s, inp.kind, inp.name)
    at = clock.now_iso()
    row = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
               updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, kind=inp.kind,
               name=inp.name.strip(), name_key=inp.name.strip().casefold(), subject=inp.subject, body=inp.body,
               is_default=inp.is_default, active=True)
    retire = None
    if inp.is_default:
        old = _current_default(s, inp.kind)
        retire = (old, dict(_stamp(s, ctx, old, at), is_default=False)) if old else None
    from bookflow.company import delivery_schedules
    would = delivery_schedules.affected_by_template(s, inp.kind, row['id'], content=False,
                                                    default=bool(inp.is_default))
    return Plan(output(s, row, schedules=would), dict(row=row, retire=retire))


def apply_create(plan, ctx, s) -> Applied:
    touched = _retire(s, plan.data['retire'])
    row = plan.data['row']
    s.company.conn.execute(c.delivery_templates.insert().values(**row))
    touched.append(Touched(RECORD, row['id'], 'create', None, 1, row, db='company'))
    names = _schedules(s, ctx, row['kind'], row['name'], touched) if row['is_default'] else []
    return Applied(output(s, row, schedules=names), touched, f"created {row['kind']} email template {row['name']}")


def _retire(s, retire) -> list:
    """Clear the old default before the new one is marked: the one-default index counts only standing rows."""
    if not retire:
        return []
    before, after = retire
    _write(s, before, after)
    return [Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before, db='company')]


def _write(s, before, after):
    t = c.delivery_templates
    done = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': before['id']})


def _check_version(row, expected):
    if expected is not None and row['version'] != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': row['id'], 'expected_version': expected,
            'current_version': row['version']})


def prepare_update(s, ctx, inp) -> Plan:
    before = resolve(s, inp.template, inp.kind)
    _check_version(before, inp.expected_version)
    at = clock.now_iso()
    after = _stamp(s, ctx, before, at)
    if inp.name is not None:
        _name(inp.name)
        _name_taken(s, before['kind'], inp.name, before['id'])
        after.update(name=inp.name.strip(), name_key=inp.name.strip().casefold())
    for name in ('subject', 'body'):
        if getattr(inp, name) is not None:
            after[name] = getattr(inp, name)
    merge.check(before['kind'], after['subject'], after['body'])
    retire = None
    if inp.is_default is False and before['is_default']:
        raise _invalid('is_default', 'make another template the default instead')
    if inp.is_default and not before['is_default']:
        if not before['active']:
            raise _invalid('is_default', 'an inactive template cannot be the default')
        old = _current_default(s, before['kind'])
        retire = (old, dict(_stamp(s, ctx, old, at), is_default=False)) if old else None
        after['is_default'] = True
    if all(after[k] == before[k] for k in ('name', 'subject', 'body', 'is_default')):
        raise _invalid('template', 'nothing to change: give name, subject, body or is_default')
    from bookflow.company import delivery_schedules
    would = delivery_schedules.affected_by_template(
        s, before['kind'], before['id'], content=any(after[k] != before[k] for k in ('subject', 'body')),
        default=after['is_default'] and not before['is_default'])
    return Plan(output(s, after, schedules=would), dict(before=before, after=after, retire=retire))


def apply_update(plan, ctx, s) -> Applied:
    touched = _retire(s, plan.data['retire'])
    before, after = plan.data['before'], plan.data['after']
    _write(s, before, after)
    touched.append(Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                           db='company'))
    names = _schedules(s, ctx, after['kind'], after['name'], touched)
    return Applied(output(s, after, schedules=names), touched,
                   f"updated {after['kind']} email template {after['name']}")


def prepare_deactivate(s, ctx, inp) -> Plan:
    before = resolve(s, inp.template, inp.kind)
    _check_version(before, inp.expected_version)
    if not before['active']:
        raise _invalid('template', 'is already inactive')
    if before['is_default']:
        raise _invalid('template', f"is the {before['kind']} default; make another template the default first")
    after = dict(_stamp(s, ctx, before, clock.now_iso()), active=False)
    return Plan(output(s, after), dict(before=before, after=after, retire=None))


def apply_deactivate(plan, ctx, s) -> Applied:
    before, after = plan.data['before'], plan.data['after']
    _write(s, before, after)
    return Applied(output(s, after), [Touched(RECORD, after['id'], 'deactivate', before['version'],
                                              after['version'], after, before, db='company')],
                   f"deactivated {after['kind']} email template {after['name']}")


def show(s, inp) -> m.TemplateOutput:
    return output(s, resolve(s, inp.template, inp.kind))


def query(s, inp) -> m.TemplatePageOutput:
    t = c.delivery_templates
    q = sa.select(t)
    if inp.kind:
        q = q.where(t.c.kind == inp.kind)
    if not inp.include_inactive:
        q = q.where(t.c.active.is_(True))
    order = sa.case(*[(t.c.kind == k, i) for i, k in enumerate(KINDS)])
    rows = [output(s, dict(r)) for r in s.company.conn.execute(
        q.order_by(order, t.c.is_default.desc(), t.c.name_key)).mappings()]
    return m.TemplatePageOutput(rows=rows, count=len(rows))


def fields(inp) -> m.FieldsOutput:
    names = []
    for kind in ([inp.kind] if inp.kind else KINDS):
        for name in merge.FIELDS[kind]:
            if name not in names:
                names.append(name)
    return m.FieldsOutput(fields=[m.FieldOutput(field=n, description=merge.DESCRIPTIONS[n],
                                                kinds=[k for k in KINDS if n in merge.FIELDS[k]]) for n in names])


def preview(s, ctx, inp) -> m.PreviewOutput:
    from bookflow.company import delivery
    read = delivery.reader(s, ctx)
    src = delivery.resolve_source(read, s.company_row['id'], inp)
    template = delivery.template_for(s, src.kind, inp.template)
    _, customer, info, _, subject, body, warnings = delivery.compose(s, ctx, read, src, template)
    empty = sorted(w.split('{{')[1].split('}}')[0] for w in warnings)
    to, cc, bcc = delivery.default_recipients(read, s.company_row['id'], customer, info)
    return m.PreviewOutput(kind=src.kind, source_id=src.id, source_label=src.label, customer_id=src.customer_id,
                           customer_name=customer.get('full_name') or customer.get('name'), to=to, cc=cc, bcc=bcc,
                           template_id=template['id'], template_name=template['name'], subject=subject, body=body,
                           empty_fields=empty, warnings=warnings)
