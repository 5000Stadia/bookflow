"""Staged relationship setup. Only the selected company or the hub is ever written."""
from contextlib import contextmanager
from copy import copy
import hashlib
import hmac
import json

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company import intercompany_relationship_models as m
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub import access, schema as h
from bookflow.hub.permission_access import activated
from bookflow.hub.users import common


def invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def require_pair(s, peer_id, manage=False):
    """Require both scopes before resolving a counterpart, under the original credential binding."""
    if not activated(s):
        raise BookflowError('E_PERMISSION', message='Activate current permission rules before using intercompany setup.')
    requirements = (('intercompany.manage', 'admin'), ('account', 'member'), ('company', 'admin')) if manage else (
        ('intercompany.read', 'member'), ('ledger.read', 'member'))
    try:
        for company_id in (s.company_row['id'], peer_id):
            scoped = copy(s)
            organization = s.hub.conn.execute(sa.select(h.companies.c.organization_id).where(h.companies.c.id == company_id)).scalar()
            scoped.company_row = {'id': company_id, 'organization_id': organization}
            scoped.company_id = company_id
            scoped._resource_checks = None
            from bookflow.company.intercompany_local import require_grants
            require_grants(scoped, requirements)
            for capability, threshold in requirements:
                access.require_resource(scoped, capability, threshold)
    except BookflowError as exc:
        if exc.code in ('E_PERMISSION', 'E_COMPANY_NOT_FOUND'):
            raise BookflowError('E_COMPANY_NOT_FOUND') from None
        raise


def peer_row(s, peer_id, manage=False):
    require_pair(s, peer_id, manage)
    row = s.hub.conn.execute(sa.select(h.companies).where(h.companies.c.id == peer_id)).mappings().first()
    if row is None:
        raise BookflowError('E_COMPANY_NOT_FOUND')
    row = dict(row)
    if row['id'] == s.company_row['id']:
        raise invalid('peer', 'requires two distinct companies')
    if row['organization_id'] != s.company_row['organization_id']:
        raise invalid('peer', 'companies must belong to the same organization')
    return row


@contextmanager
def peer_session(s, ctx, row):
    from bookflow.core.dispatch import open_company
    other = copy(s)
    other.company, other.company_info_row, other._co_cm = None, None, None
    other.company_row, other.company_id = row, row['id']
    other._resource_checks = None
    try:
        open_company(other, ctx.model_copy(update={'company_id': row['id']}), False)
        yield other
    finally:
        other.close_company(release=False)


def require_enabled(s):
    from bookflow.company.intercompany_local import settings
    if not settings(s.company).linked_entries_enabled:
        raise BookflowError('E_FEATURE_DISABLED', details={'feature': 'intercompany', 'setting': 'linked_entries_enabled'})


def require_reason(ctx):
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')


def peer_reference(s, peer):
    key = s.hub.conn.execute(sa.select(h.intercompany_identity.c.key).where(h.intercompany_identity.c.id == 1)).scalar_one()
    return hmac.new(bytes.fromhex(key), (s.company_row['id'] + ':' + peer).encode(), hashlib.sha256).hexdigest()


def resolve_peer_reference(s, reference):
    # Private registry-only lookup for projections; no counterpart file is opened.
    for peer in s.hub.conn.execute(sa.select(h.companies.c.id)).scalars():
        if hmac.compare_digest(peer_reference(s, peer), reference):
            return peer
    return None


def latest(s, peer):
    t = c.intercompany_mappings
    row = s.company.conn.execute(sa.select(t).where(t.c.peer_id == peer_reference(s, peer)).order_by(t.c.version.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def check_accounts(s, peer, due_from, due_to, currency):
    for identifier, kind in ((due_from, 'other_current_asset'), (due_to, 'other_current_liability')):
        row = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == identifier)).mappings().first()
        if row is None:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'account'})
        if not row['active'] or row['type'] != kind or row['currency'] != currency or row['system_role']:
            raise invalid('account', 'requires an active dedicated home-currency account of the required type')
        if s.company.conn.execute(sa.select(c.list_merges.c.id).where(c.list_merges.c.list_kind == 'account', c.list_merges.c.undone_at.is_(None), sa.or_(c.list_merges.c.merged_id == identifier, c.list_merges.c.survivor_id == identifier))).first():
            raise invalid('account', 'requires a dedicated account without merged history')
        if s.company.conn.execute(sa.select(c.posting_lines.c.id).where(c.posting_lines.c.account_id == identifier).limit(1)).first():
            raise invalid('account', 'requires zero balance and no previous postings')
        t = c.intercompany_mappings
        if s.company.conn.execute(sa.select(t.c.id).where(t.c.peer_id != peer_reference(s, peer),
                sa.or_(t.c.due_from_id == identifier, t.c.due_to_id == identifier)).limit(1)).first():
            raise invalid('account', 'account is unavailable for this configuration')


def authorize_mapping(inp, ctx, s):
    require_reason(ctx)
    peer_row(s, inp.peer, True)
    require_enabled(s)


def plan_mapping(inp, ctx, s):
    authorize_mapping(inp, ctx, s)
    peer = peer_row(s, inp.peer, True)
    home = s.company_info_row['home_currency']
    with peer_session(s, ctx, peer) as other:
        require_enabled(other)
        if other.company_info_row['home_currency'] != home:
            raise invalid('peer', 'companies must have the same home currency')
    old = latest(s, inp.peer)
    if (old['version'] if old else 0) != inp.expected_version:
        raise BookflowError('E_VERSION_CONFLICT')
    check_accounts(s, inp.peer, inp.due_from, inp.due_to, home)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()),
        peer_id=peer_reference(s, inp.peer), due_from_id=inp.due_from, due_to_id=inp.due_to, currency=home)
    row['version'] = inp.expected_version + 1
    row['digest'] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return Plan(m.Mapping(**{k: row[k] for k in m.Mapping.model_fields}), {'row': row, 'input': inp})


def apply_mapping(plan, ctx, s):
    # Planning occurs inside the single local transaction. Recheck the peer before publication.
    require_pair(s, plan.data['input'].peer, True)
    row = plan.data['row']
    s.company.conn.execute(c.intercompany_mappings.insert().values(**row))
    local = {k: v for k, v in row.items() if k != 'peer_id'}
    return Applied(plan.preview, [Touched('intercompany_mapping', row['id'], 'create', None, row['version'], local, db='company')],
        'Saved local account configuration')


def authorize_relationship(inp, ctx, s):
    require_reason(ctx)
    peer_row(s, inp.peer, True)
    require_enabled(s)


def authorize_read(inp, ctx, s):
    peer_row(s, inp.peer)


def relationship_row(s, identifier, peer):
    a, b = sorted((s.company_row['id'], peer))
    t = h.intercompany_relationships
    row = s.hub.conn.execute(sa.select(t).where(t.c.id == identifier, t.c.company_a == a, t.c.company_b == b)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND')
    return dict(row)


def project(s, row, peer):
    local_a = row['company_a'] == s.company_row['id']
    return m.Relationship(id=row['id'], version=row['version'], peer_id=peer['id'], peer_name=peer['display_name'],
        enabled=bool(row['enabled']), local_mapping_digest=row['mapping_a_digest' if local_a else 'mapping_b_digest'],
        peer_mapping_digest=row['mapping_b_digest' if local_a else 'mapping_a_digest'])


def plan_relationship(inp, ctx, s):
    authorize_relationship(inp, ctx, s)
    peer = peer_row(s, inp.peer, True)
    a, b = sorted((s.company_row['id'], peer['id']))
    old = relationship_row(s, inp.relationship, inp.peer) if isinstance(inp, m.RelationshipUpdate) else None
    if old is not None and old['version'] != inp.expected_version:
        raise BookflowError('E_VERSION_CONFLICT')
    t = h.intercompany_relationships
    if old is None and s.hub.conn.execute(sa.select(t.c.id).where(t.c.company_a == a, t.c.company_b == b)).first():
        raise invalid('peer', 'relationship already exists')
    local = latest(s, peer['id'])
    home = s.company_info_row['home_currency']
    with peer_session(s, ctx, peer) as other:
        require_enabled(other)
        if other.company_info_row['home_currency'] != home:
            raise invalid('peer', 'companies must have the same home currency')
        remote = latest(other, s.company_row['id'])
        if local is None or remote is None:
            raise invalid('mapping', 'save both local account configurations first')
        check_accounts(s, peer['id'], local['due_from_id'], local['due_to_id'], home)
        check_accounts(other, s.company_row['id'], remote['due_from_id'], remote['due_to_id'], home)
    digests = {s.company_row['id']: local['digest'], peer['id']: remote['digest']}
    row = dict(old) if old else dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()),
        organization_id=peer['organization_id'], company_a=a, company_b=b)
    row.update(enabled=inp.enabled if old else True, mapping_a_digest=digests[a], mapping_b_digest=digests[b])
    if old:
        row.update(version=old['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(project(s, row, peer), {'row': row, 'old': old, 'input': inp})


def apply_relationship(plan, ctx, s):
    # Hub writes plan before BEGIN; validate again inside the hub transaction.
    fresh = plan_relationship(plan.data['input'], ctx, s)
    row, old = fresh.data['row'], fresh.data['old']
    if old is None:
        row['id'] = plan.data['row']['id']
        s.hub.conn.execute(h.intercompany_relationships.insert().values(**row))
    else:
        done = s.hub.conn.execute(h.intercompany_relationships.update().where(
            h.intercompany_relationships.c.id == row['id'], h.intercompany_relationships.c.version == old['version']).values(**row)).rowcount
        if done != 1:
            raise BookflowError('E_VERSION_CONFLICT')
    s.hub.conn.execute(h.intercompany_relationship_revisions.insert().values(relationship_id=row['id'],
        **{k: row[k] for k in ('version', 'enabled', 'mapping_a_digest', 'mapping_b_digest')}))
    require_pair(s, plan.data['input'].peer, True)
    output = fresh.preview.model_copy(update={'id': row['id']})
    return Applied(output, [Touched('intercompany_relationship', row['id'], 'update' if old else 'create',
        old['version'] if old else None, row['version'], row, old, db='hub')], 'Saved relationship registry metadata')


def plan_show(inp, ctx, s):
    peer = peer_row(s, inp.peer)
    return Plan(project(s, relationship_row(s, inp.relationship, inp.peer), peer))


def plan_list(inp, ctx, s):
    from bookflow.company.intercompany_local import policy
    policy(s)
    t = h.intercompany_relationships
    query = sa.select(t).where(sa.or_(t.c.company_a == s.company_row['id'], t.c.company_b == s.company_row['id'])).order_by(t.c.id)
    if inp.cursor:
        query = query.where(t.c.id > inp.cursor)
    items = []
    for row in s.hub.conn.execute(query).mappings():
        peer_id = row['company_b'] if row['company_a'] == s.company_row['id'] else row['company_a']
        try:
            peer = peer_row(s, peer_id)
        except BookflowError as exc:
            if exc.code in ('E_COMPANY_NOT_FOUND', 'E_PERMISSION'):
                continue
            raise
        items.append(project(s, row, peer))
        if len(items) > inp.limit:
            break
    return Plan(m.RelationshipPage(items=items[:inp.limit], next_cursor=items[inp.limit-1].id if len(items)>inp.limit else None))
