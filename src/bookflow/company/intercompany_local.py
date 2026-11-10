"""Local proposal owner: no peer lookup, cross-company write, or ledger posting."""
import sqlalchemy as sa
from bookflow.company import schema as c, intercompany_models as m
from bookflow.company.accounts import resolve_account
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub import access
from bookflow.hub.users import common

FLAGS = ('linked_entries_enabled', 'templates_enabled', 'bulk_enabled')


def invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def policy(s):
    from bookflow.hub.permission_access import activated, upgrade_required
    if not activated(s):
        raise upgrade_required('intercompany.read', 'member')


def reason(ctx):
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED', message='Give a reason for this change.')


def settings_row(db):
    row = db.conn.execute(sa.select(c.intercompany_settings)).mappings().first()
    return dict(row) if row else None


def settings(db):
    row = settings_row(db)
    return m.SettingsOutput(version=row['version'] if row else 0,
                            **{key: bool(row[key]) if row else False for key in FLAGS})


def require_enabled(db):
    if not settings(db).linked_entries_enabled:
        raise BookflowError('E_FEATURE_DISABLED', details={'feature': 'intercompany',
            'enable_command': 'intercompany-settings update'})


def require_grants(s, requirements):
    from bookflow.hub.capabilities import granted, _company_scopes
    if s.actor.kind != 'human':
        for capability, _ in requirements:
            if not granted(s, s.actor.id, capability, _company_scopes(s)):
                raise BookflowError('E_PERMISSION')


def require_read(s):
    policy(s)
    require_grants(s, (('intercompany.read', 'member'), ('ledger.read', 'member')))
    access.require_resource(s, 'intercompany.read', 'member')
    access.require_resource(s, 'ledger.read', 'member')


def authorize_settings(inp, ctx, s):
    policy(s)
    require_grants(s, (('company', 'admin'),))
    access.require_resource(s, 'company', 'admin')
    if isinstance(inp, m.SettingsUpdate):
        reason(ctx)


def authorize_draft(inp, ctx, s):
    policy(s)
    if isinstance(inp, m.DraftCreate):
        require_enabled(s.company)
        reason(ctx)
        require_grants(s, (('intercompany.post', 'standard'), ('ledger.post', 'standard')))
        access.require_resource(s, 'ledger.post', 'standard')
        if inp.offset_account is not None:
            require_grants(s, (('account', 'member'),))
            access.require_resource(s, 'account', 'member')
    else:
        require_read(s)
        access.require_resource(s, 'ledger.read', 'member')


def version(row, expected):
    current = row['version'] if row else 0
    if current != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={'current_version': current, 'expected_version': expected})


def settings_update(inp, ctx, s):
    authorize_settings(inp, ctx, s)
    old = settings_row(s.company)
    version(old, inp.expected_version)
    if not inp.linked_entries_enabled and (inp.templates_enabled or inp.bulk_enabled):
        raise invalid('linked_entries_enabled', 'templates and bulk require linked entries')
    row = dict(id='settings', **common(s.actor.id, ctx.interface.value, clock.now_iso()),
               **{key: getattr(inp, key) for key in FLAGS})
    if old:
        row.update({key: old[key] for key in ('created_at', 'created_by', 'created_via')})
        row['version'] = old['version'] + 1
    return Plan(m.SettingsOutput(version=row['version'], **{key: row[key] for key in FLAGS}),
                {'table': c.intercompany_settings, 'kind': 'intercompany_settings', 'row': row, 'before': old})


def resolve_draft(db, identifier):
    row = db.conn.execute(sa.select(c.intercompany_drafts).where(c.intercompany_drafts.c.id == identifier)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'intercompany_draft'})
    return dict(row)


def draft_output(row):
    return m.DraftOutput(**row)


def draft_save(inp, ctx, s):
    authorize_draft(inp, ctx, s)
    old = resolve_draft(s.company, inp.draft) if isinstance(inp, m.DraftUpdate) else None
    if old:
        version(old, inp.expected_version)
    home = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()
    if inp.currency is not None and inp.currency != home:
        raise invalid('currency', 'must equal this company\'s home currency')
    account_id = None
    if inp.offset_account is not None:
        account = resolve_account(s.company, inp.offset_account)
        if not account['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'offset_account'})
        if account['currency'] != home:
            raise invalid('offset_account', 'must use home currency')
        if account['system_role'] or account['type'] not in ('bank', 'expense', 'other_expense', 'other_current_asset', 'other_current_liability'):
            raise invalid('offset_account', 'must be an eligible ordinary local offset account')
        account_id = account['id']
    row = dict(id=old['id'] if old else new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()),
               date=inp.date, amount_minor_units=inp.amount_minor_units, currency=home,
               offset_account_id=account_id, memo=inp.memo)
    if old:
        row.update({key: old[key] for key in ('created_at', 'created_by', 'created_via')})
        row['version'] = old['version'] + 1
    return Plan(draft_output(row), {'table': c.intercompany_drafts, 'kind': 'intercompany_draft', 'row': row, 'before': old})


def draft_list(inp, s):
    t = c.intercompany_drafts
    query = sa.select(t).order_by(t.c.id)
    if inp.cursor:
        resolve_draft(s.company, inp.cursor)
        query = query.where(t.c.id > inp.cursor)
    rows = list(s.company.conn.execute(query.limit(inp.limit + 1)).mappings())
    return m.DraftPage(items=[draft_output(dict(row)) for row in rows[:inp.limit]],
                       next_cursor=rows[inp.limit - 1]['id'] if len(rows) > inp.limit else None)


def apply(plan, ctx, s):
    table, row, old, kind = (plan.data[key] for key in ('table', 'row', 'before', 'kind'))
    if old is None:
        s.company.conn.execute(table.insert().values(**row))
    else:
        count = s.company.conn.execute(table.update().where(table.c.id == row['id'], table.c.version == old['version']).values(**row)).rowcount
        if count != 1:
            raise BookflowError('E_VERSION_CONFLICT')
    return Applied(plan.preview, [Touched(kind, row['id'], 'update' if old else 'create',
        old['version'] if old else None, row['version'], row, old, db='company')],
        'Updated local intercompany settings' if kind == 'intercompany_settings' else 'Saved local untargeted draft')
