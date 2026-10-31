"""Bounded host-only bank fetching: snapshot, outside call, checked atomic import.

No provider or keyring is called on the writer. A page and its cursor commit together;
a late result for a disabled, edited, restored or re-authorized connection is discarded.
"""
from __future__ import annotations

import json
from dataclasses import replace

import sqlalchemy as sa

from bookflow.company import bank_sources as sources
from bookflow.company import schema as c

ACCOUNTS_PER_TICK = 4


def _rows(s):
    t, a = c.bank_feed_connections, c.bank_feed_connection_accounts
    return sa.select(t, a.c.account_id, a.c.remote_account, a.c.cursor, a.c.staged_page,
                     a.c.staged_fingerprint, a.c.approved_fingerprint, a.c.backfill_approved).join(a, a.c.connection_id == t.c.id)


def _authorization(s, row):
    from bookflow.company import automation, bank_connections as connections, bank_rules, scheduler
    why = connections.authorize(s, row)
    if why:
        return why
    saved = (s.actor, s.memberships, s.credential, s.directive_code, getattr(s, '_resource_checks', None))
    try:
        s._resource_checks = None
        scheduler._act_as(s, row['established_by'], row['established_for'], row['established_epoch'])
        return None if automation.may(s, 'bank-feed import') else 'bank-feed import authority was withdrawn'
    finally:
        bank_rules._restore(s, saved)


def _authorized(s, row):
    try:
        return _authorization(s, row) is None
    except Exception:
        return False


def claim(s, *, limit=ACCOUNTS_PER_TICK, scan_state=None):
    from bookflow.company import bank_sort, scheduler
    from bookflow.company.delivery_connections import binding
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    from bookflow.core import clock
    t = c.bank_feed_connections
    wanted = sa.or_(t.c.enabled.is_(True), t.c.check_requested.is_(True))
    if not s.company.conn.execute(sa.select(sa.exists().where(wanted))).scalar():
        return []  # One EXISTS; no installation file, keyring or provider access.
    here = binding(s)
    size = min(limit, ACCOUNTS_PER_TICK)
    query = _rows(s).where(wanted, t.c.enabled_host == here, t.c.kind.in_(tuple(sources.SOURCES)))
    after = scan_state.get(s.company_row['id']) if scan_state is not None else None
    rows = list(s.company.conn.execute(query.where(t.c.id > after).order_by(t.c.id).limit(size)
                if after else query.order_by(t.c.id).limit(size)).mappings())
    if after and len(rows) < size:
        rows += list(s.company.conn.execute(query.where(t.c.id <= after).order_by(t.c.id)
                     .limit(size - len(rows))).mappings())
    if rows and scan_state is not None:
        scan_state[s.company_row['id']] = rows[-1]['id']
    claims = []
    for row in rows:
        try:
            why = _authorization(s, dict(row))
        except Exception:
            continue  # Unsettled authority is not a permanent loss.
        if why is None:
            claims.append(dict(row))
            continue
        def disable():
            s.company.conn.execute(t.update().where(t.c.id == row['id']).values(
                enabled=False, enabled_host=None, check_requested=False, version=row['version'] + 1,
                updated_at=clock.now_iso(), updated_by=s.actor.id, updated_via='system'))
            write_event_to(s.company, scheduler.context(), 'bank-feed fetch',
                'Bank feed disabled: its establisher no longer holds required authority',
                [Touched('bank_feed_connection', row['id'], 'update', row['version'], row['version'] + 1,
                         {'enabled': False}, db='company')], actor_id=s.actor.id, actor_kind=s.actor.kind)
        bank_sort._in_tx(s, disable)
    return claims


def current(s, snapshot):
    from bookflow.company.delivery_connections import binding
    row = s.company.conn.execute(_rows(s).where(c.bank_feed_connections.c.id == snapshot['id'])).mappings().first()
    if row is None:
        return False
    keys = ('version', 'enabled', 'enabled_host', 'check_requested', 'cursor', 'staged_fingerprint',
            'approved_fingerprint', 'established_by', 'established_for', 'established_epoch', 'remote_account',
            'account_id', 'kind', 'settings', 'secret_ref', 'backfill_approved')
    return (all(row[key] == snapshot[key] for key in keys)
            and row['enabled_host'] == binding(s) and (row['enabled'] or row['check_requested'])
            and _authorized(s, dict(row)))


def fetch(snapshot):
    """Run only outside the host writer. Return safe values, never exception/provider text."""
    try:
        if snapshot['enabled'] and snapshot['staged_page']:
            page = sources.page_from_json(snapshot['staged_page'])
            if sources.fingerprint(page) != snapshot['approved_fingerprint']:
                raise sources.FeedFailure(permanent=True)
            return {'page': page}
        source = sources.SOURCES[snapshot['kind']]
        from bookflow.delivery.transport import resolve_secret
        conn = sources.FeedConnection(json.loads(snapshot['settings']), resolve_secret(snapshot['secret_ref']))
        if snapshot['check_requested'] and not source.check(conn, timeout=sources.FETCH_TIMEOUT).ok:
            raise sources.FeedFailure(permanent=True)
        if not snapshot['enabled'] and snapshot['backfill_approved']:
            return {'checked': True}
        accounts = source.accounts(conn, limit=1000, timeout=sources.FETCH_TIMEOUT)
        if not isinstance(accounts, (list, tuple)) or len(accounts) > 1000:
            raise sources.FeedFailure(permanent=True)
        matching = [a for a in accounts if isinstance(a, sources.RemoteAccount)
                    and a.id == snapshot['remote_account']]
        if len(matching) != 1:
            raise sources.FeedFailure(permanent=True)
        remote = matching[0]
        if (type(remote.last4) is not str or len(remote.last4) != 4
                or remote.kind not in ('bank', 'card')
                or type(remote.currency) is not str or len(remote.currency) != 3):
            raise sources.FeedFailure(permanent=True)
        page = source.fetch(conn, snapshot['remote_account'], snapshot['cursor'],
                            limit=sources.MAX_LINES, timeout=sources.FETCH_TIMEOUT)
        sources.validate_page(page)
        if (page.currency != remote.currency or page.account_kind != remote.kind
                or page.account_last4 is not None and page.account_last4 != remote.last4):
            raise sources.FeedFailure(permanent=True)
        return {'page': replace(page, account_last4=remote.last4)}
    except sources.FeedFailure as error:
        return {'failed': True, 'permanent': error.permanent}
    except Exception:
        return {'failed': True, 'permanent': False}


def record(s, snapshot, result):
    from bookflow.company import bank_feeds, bank_rules, bank_sort, scheduler
    from bookflow.core import clock
    from bookflow.core.dispatch import _upsert_principals
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    if not current(s, snapshot):
        return {'state': 'stale'}
    t, a = c.bank_feed_connections, c.bank_feed_connection_accounts
    saved = (s.actor, s.memberships, s.credential, s.directive_code, getattr(s, '_resource_checks', None))
    try:
        s._resource_checks = None
        scheduler._act_as(s, snapshot['established_by'], snapshot['established_for'], snapshot['established_epoch'])
        ctx = scheduler.context().model_copy(update={
            'actor_id': s.actor.id, 'actor_kind': s.actor.kind,
            'on_behalf_of': snapshot['established_for'], 'company_id': s.company_row['id'], 'reason': 'On bank feed: ' + snapshot['name'],
            'source_ref': f"bank-feed-connection:{snapshot['id']}:{snapshot['established_by']}"})
        prepared = None
        def write():
            nonlocal prepared
            _upsert_principals(s, ctx)
            values = dict(last_check_at=clock.now_iso(), last_check_ok=not result.get('failed'), check_requested=False,
                          version=snapshot['version'] + 1, updated_at=clock.now_iso(),
                          updated_by=s.actor.id, updated_via='system')
            if result.get('failed'):
                if result['permanent']:
                    values.update(enabled=False, enabled_host=None)
                state = 'unavailable'
            elif result.get('checked'):
                state = 'checked'
            elif not snapshot['enabled']:
                page = result['page']
                # Validate against the selected account before presenting its actual backfill.
                bank_feeds.prepare_page(s, ctx, snapshot['account_id'], page)
                s.company.conn.execute(a.update().where(a.c.connection_id == snapshot['id']).values(
                    staged_page=sources.serialize_page(page), staged_fingerprint=sources.fingerprint(page),
                    approved_fingerprint=None))
                state = 'preview'
            else:
                page = result['page']
                prepared = bank_feeds.prepare_page(s, ctx, snapshot['account_id'], page)
                out = None
                if prepared is not None:
                    bank_rules.annotate(s, prepared)
                    out, _ = bank_feeds.write(s, ctx, prepared)
                    # Commit fetched provenance with the lines. A crash before run_live must still resume
                    # as fetched work, while older user-imported lines retain their own importer.
                    ids = [r['line_id'] for r in prepared['new'] if r.get('line_id') and r.get('rule', {}).get('acts')]
                    bank_rules._marked(s, ids, {'reason': 'fetched bank page waits for its live rule'})
                s.company.conn.execute(a.update().where(a.c.connection_id == snapshot['id']).values(
                    cursor=page.next_cursor, staged_page=None, staged_fingerprint=None, approved_fingerprint=None,
                    backfill_approved=True))
                state = 'imported'
            s.company.conn.execute(t.update().where(t.c.id == snapshot['id']).values(**values))
            write_event_to(s.company, ctx, 'bank-feed fetch', 'Bank feed ' + state,
                           [Touched('bank_feed_connection', snapshot['id'], 'update', snapshot['version'],
                                    snapshot['version'] + 1, {'state': state}, db='company')],
                           actor_id=s.actor.id, actor_kind=s.actor.kind)
            return {'state': state, **({'import_id': out.import_id} if state == 'imported' and out else {})}
        try:
            outcome = bank_sort._in_tx(s, write)
        except Exception as error:
            from bookflow.core.errors import BookflowError
            if not result.get('failed') and (isinstance(error, sources.FeedFailure) or
                    isinstance(error, BookflowError) and error.code in ('E_VALIDATION', 'E_ACCOUNT_MISMATCH')):
                return record(s, snapshot, {'failed': True, 'permanent': True})
            raise
        if prepared is not None:
            outcome['rules'] = bank_rules.run_live(s, ctx, prepared, fetched=True)
        return outcome
    finally:
        bank_rules._restore(s, saved)
