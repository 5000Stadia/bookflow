"""Live feed authorization. Commands queue host work; they never contact a provider."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, bank_connection_models as m, bank_feeds, automation
from bookflow.company.delivery_connections import binding, _invalid, _check_version
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.delivery.transport import SECRET_REF


def provider(kind):
    from bookflow.company.bank_sources import SOURCES
    found = SOURCES.get(kind)
    if found is None:
        raise _invalid('kind', 'no provider of this kind is registered; available: ' + (', '.join(sorted(SOURCES)) or 'none'), available=sorted(SOURCES))
    return found


def row(s, selector):
    t, a = c.bank_feed_connections, c.bank_feed_connection_accounts
    found = s.company.conn.execute(sa.select(t, *[col for col in a.c if col.name != 'connection_id']).join(a, a.c.connection_id == t.c.id).where(sa.or_(t.c.id == selector, t.c.name_key == selector.strip().casefold()))).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'bank_feed_connection', 'selector': selector})
    return dict(found)


def project(s, value):
    from bookflow.hub import access
    scope, role = access.company_role(s, s.company_row['id'], s.company_row['organization_id'])
    admin = access.role_satisfies(role, scope, 'admin', access.legacy_admin(s))
    if admin and s.actor.kind == 'agent':
        from bookflow.hub import schema as h
        from bookflow.hub.capabilities import principal_of
        memberships = h.memberships
        admin = bool(s.hub.conn.execute(sa.select(memberships.c.id).where(
            memberships.c.user_id == principal_of(s), memberships.c.revoked_at.is_(None),
            memberships.c.role.in_(('admin', 'owner')), sa.or_(
                sa.and_(memberships.c.scope_type == 'company', memberships.c.scope_id == s.company_row['id']),
                sa.and_(memberships.c.scope_type == 'organization',
                        memberships.c.scope_id == s.company_row['organization_id'])))).first())
    value = dict(value)
    if not admin:
        value.update(cursor=None, staged_page=None)
    preview = json.loads(value['staged_page']) if value.get('staged_page') else None
    return m.ConnectionOutput(**{k: value[k] for k in ('id', 'version', 'name', 'kind', 'account_id', 'remote_account', 'secret_ref', 'enabled', 'check_requested', 'backfill_approved', 'cursor', 'staged_fingerprint', 'approved_fingerprint', 'last_check_ok', 'last_check_at')},
        enabled_here=bool(value['enabled_host']) and value['enabled_host'] == binding(s), preview=preview,
        next=('Review this page with bank-feed fetch --dry-run, then pass its staged_fingerprint as confirm to bank-feed fetch.' if value.get('staged_fingerprint') and not value.get('approved_fingerprint') else
              'The host fetches bounded pages while this connection is enabled.' if value['enabled'] else
              'Run bank-feed-connection check to request a host check and first-page preview.'))


def _authority(s):
    from bookflow.hub.capabilities import principal_of
    principal = principal_of(s) if s.actor.kind == 'agent' else None
    return dict(established_by=s.actor.id, established_for=principal,
                established_epoch=automation.epoch_of(s, s.actor.id, principal))


def authorize(s, value):
    """None when the recorded actor still holds the capability; otherwise why it must stop."""
    return automation.lost(s, 'bank-feed.connect', value['established_by'], value['established_for'], value['established_epoch'])


def prepare_add(s, ctx, inp):
    entry = provider(inp.kind)
    account = bank_feeds._account(s, inp.account)
    name = ' '.join(inp.name.split())
    if not name:
        raise _invalid('name', 'must not be blank')
    t, a = c.bank_feed_connections, c.bank_feed_connection_accounts
    if s.company.conn.execute(sa.select(t.c.id).where(t.c.name_key == name.casefold())).first():
        raise _invalid('name', 'already exists')
    if s.company.conn.execute(sa.select(a.c.connection_id).where(a.c.account_id == account['id'])).first():
        raise _invalid('account', 'already has a feed connection')
    if inp.secret_ref is not None and (not inp.secret_ref.startswith('keyring:') or not SECRET_REF.fullmatch(inp.secret_ref)):
        raise _invalid('secret_ref', 'use keyring:service/name; never put a secret in this field')
    try:
        cleaned = entry.validate_settings(dict(inp.settings))
        if type(cleaned) is not dict:
            raise ValueError
        settings = json.dumps(cleaned, sort_keys=True, allow_nan=False)
    except Exception:
        raise _invalid('settings', 'not valid non-secret settings for this provider') from None
    if len(settings) > 20000:
        raise _invalid('settings', 'too large')
    at = clock.now_iso()
    value = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
        updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, name=name, name_key=name.casefold(),
        kind=inp.kind, settings=settings, secret_ref=inp.secret_ref, enabled=False, enabled_host=None,
        check_requested=False, last_check_ok=None, last_check_at=None, **_authority(s), account_id=account['id'],
        remote_account=inp.remote_account, backfill_approved=False, cursor=None, staged_page=None, staged_fingerprint=None, approved_fingerprint=None)
    return Plan(project(s, value), {'before': None, 'after': value})


def prepare_change(s, ctx, inp, action):
    before = row(s, inp.connection)
    _check_version(before, inp.expected_version)
    after = dict(before, version=before['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                 updated_via=ctx.interface.value)
    if action == 'disable':
        after.update(enabled=False, enabled_host=None, check_requested=False, approved_fingerprint=None)
    elif action == 'check':
        provider(before['kind'])
        after.update(check_requested=True, enabled_host=binding(s), **_authority(s))
        # A new check replaces the previous preview; it cannot silently approve an older page.
        if not before['backfill_approved']:
            after.update(enabled=False, staged_page=None, staged_fingerprint=None, approved_fingerprint=None)
    else:
        provider(before['kind'])
        if not before['backfill_approved']:
            fingerprint = before['staged_fingerprint']
            if not fingerprint or inp.confirm != fingerprint:
                raise _invalid('confirm', 'review the staged first-page preview with bank-feed fetch --dry-run, then pass its fingerprint')
            after['approved_fingerprint'] = fingerprint
        after.update(enabled=True, enabled_host=binding(s), **_authority(s))
    return Plan(project(s, after), {'before': before, 'after': after})


def prepare_fetch(s, ctx, inp):
    if s.dry_run:
        value = row(s, inp.connection)
        out = project(s, value)
        if value['staged_page']:
            from bookflow.company.bank_sources import page_from_json
            prepared = bank_feeds.prepare_page(s, ctx, value['account_id'], page_from_json(value['staged_page']))
            if prepared is not None:
                out = out.model_copy(update={'import_preview': bank_feeds.output(prepared, dry_run=True).model_dump(mode='json')})
        return Plan(out)
    return prepare_change(s, ctx, inp, 'enable')


def apply(plan, ctx, s):
    before, after = plan.data['before'], plan.data['after']
    t, a = c.bank_feed_connections, c.bank_feed_connection_accounts
    connection = {col.name: after[col.name] for col in t.c}
    mapping = {col.name: after[col.name] for col in a.c if col.name != 'connection_id'}
    mapping['connection_id'] = after['id']
    if before is None:
        s.company.conn.execute(t.insert().values(**connection))
        s.company.conn.execute(a.insert().values(**mapping))
    else:
        updated = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version']).values(**connection))
        if updated.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': 'bank_feed_connection', 'record_id': before['id']})
        s.company.conn.execute(a.update().where(a.c.connection_id == before['id']).values(**mapping))
    touched = Touched('bank_feed_connection', after['id'], 'create' if before is None else 'update',
                      None if before is None else before['version'], after['version'], after, before, db='company')
    return Applied(project(s, after), [touched], f"saved bank feed connection {after['name']}")


def query(s, inp):
    from bookflow.company.bank_sources import SOURCES
    t = c.bank_feed_connections
    ids = s.company.conn.execute(sa.select(t.c.id).order_by(t.c.name_key, t.c.id).limit(inp.limit)).scalars()
    return m.QueryOutput(rows=tuple(project(s, row(s, value)) for value in ids), kinds_available=tuple(sorted(SOURCES)),
                         note='No providers are registered; nothing leaves this machine.' if not SOURCES else 'Only explicitly requested checks and enabled connections contact a provider.')


def disable_all(raw):
    if not raw.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='bank_feed_connections'").fetchone():
        return 0
    count = raw.execute('UPDATE bank_feed_connections SET enabled=0, enabled_host=NULL, check_requested=0 WHERE enabled=1 OR enabled_host IS NOT NULL OR check_requested=1').rowcount
    raw.execute('UPDATE bank_feed_connection_accounts SET backfill_approved=0, approved_fingerprint=NULL, staged_page=NULL, staged_fingerprint=NULL')
    return count
