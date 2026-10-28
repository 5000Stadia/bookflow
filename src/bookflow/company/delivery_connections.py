"""Sending connections (R112 increment 2, co0106; design/document-delivery.md §4, §7).

A connection says where mail goes and under whose keys: a ``kind`` this build registers (none in production, so
``add`` refuses every kind and names the available ones: none), non-secret ``settings`` the kind validates, and a
``secret_ref`` naming the keyring entry or environment variable that holds the secret. The row never holds a secret.

A connection is **added disabled**; ``enable`` binds it to this machine and this company file (``enabled_host``, a
fingerprint of the host name, the data folder and the company id). The dispatcher sends only through a connection
enabled *here*, and ``company restore`` clears every connection, so a copied or restored company full of ready
messages cannot send a second copy of anything until a person enables a connection on that machine.

Every write and ``check`` is a person's act (people-only, admin). R187 converts this to the capability
``delivery.connect``.
"""
from __future__ import annotations

import hashlib
import json
import socket
from datetime import timedelta

import sqlalchemy as sa

from bookflow.company import delivery_queue_models as q
from bookflow.company import schema as c
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.delivery import transport

RECORD = 'delivery_connection'
NONE_NOTE = ('No transport is registered in this build, so no sending connection can be set up and nothing leaves '
             'this machine. Export a message (delivery export) to send it from your own mail program.')


def _invalid(field_name, problem, **extra):
    return BookflowError('E_VALIDATION', message=f'{field_name}: {problem}',
                         details={'fields': [{'field': field_name, 'problem': problem}], **extra})


def binding(s) -> str:
    """This machine, data folder and company file: a connection enabled elsewhere does not send here."""
    raw = '\0'.join((socket.gethostname(), str(s.data_root.resolve()), s.company_row['id']))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def info(row) -> transport.ConnectionInfo:
    return transport.ConnectionInfo(id=row['id'], name=row['name'], kind=row['kind'],
                                    settings=json.loads(row['settings']), secret_ref=row['secret_ref'])


def usable(s) -> list[dict]:
    """Connections the dispatcher may send through now: enabled here, not removed, their kind registered."""
    t = c.delivery_connections
    rows = s.company.conn.execute(sa.select(t).where(t.c.enabled.is_(True), t.c.removed_at.is_(None))
                                  .order_by(t.c.name_key, t.c.id)).mappings().all()
    if not rows:
        return []
    here = binding(s)
    return [dict(r) for r in rows if r['enabled_host'] == here and transport.get(r['kind']) is not None]


def label(s) -> str:
    """The connection a ready message would go through, or ``none``."""
    found = usable(s)
    return found[0]['name'] if found else 'none'


def attempts_since(s, connection_id, since_iso) -> int:
    e = c.delivery_events
    return s.company.conn.execute(sa.select(sa.func.count()).where(
        e.c.event == 'claimed', e.c.at >= since_iso,
        sa.func.json_extract(e.c.details, '$.connection') == connection_id)).scalar() or 0


def _row(s, selector, *, removed=False) -> dict:
    t = c.delivery_connections
    query = sa.select(t).where(sa.or_(t.c.id == selector, t.c.name_key == selector.strip().casefold()))
    if not removed:
        query = query.where(t.c.removed_at.is_(None))
    row = s.company.conn.execute(query.order_by(t.c.removed_at.is_not(None), t.c.id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': selector})
    return dict(row)


def project(s, row, warnings=()) -> q.ConnectionOutput:
    from bookflow.core.session import localize
    available = transport.get(row['kind']) is not None
    here = bool(row['enabled']) and row['enabled_host'] == binding(s)
    since = (clock.now() - timedelta(days=1)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return q.ConnectionOutput(
        id=row['id'], version=row['version'], name=row['name'], kind=row['kind'], kind_available=available,
        settings=json.loads(row['settings']), secret_ref=row['secret_ref'], enabled=bool(row['enabled']),
        enabled_here=here, usable=here and available and row['removed_at'] is None, daily_limit=row['daily_limit'],
        attempts_last_24h=attempts_since(s, row['id'], since), last_check_at=row['last_check_at'],
        last_check_ok=row['last_check_ok'], last_check_text=row['last_check_text'], removed_at=row['removed_at'],
        updated_at=localize(s, row['updated_at']), updated_by=row['updated_by'], warnings=list(warnings))


def _kind(kind) -> transport.TransportKind:
    entry = transport.get(kind)
    if entry is None:
        available = transport.kinds()
        raise _invalid('kind', f"no transport of kind {kind!r} is available in this build; available: "
                               f"{', '.join(available) if available else 'none'}", available=available)
    return entry


def _settings(entry, settings) -> str:
    try:
        cleaned = entry.validate(dict(settings or {}))
    except (ValueError, TypeError) as error:
        raise _invalid('settings', str(error) or 'not valid for this kind') from None
    text = json.dumps(cleaned, sort_keys=True)
    if len(text) > 20000:
        raise _invalid('settings', 'too large')
    return text


def _secret_ref(value):
    if value is None:
        return None
    value = value.strip()
    if not transport.SECRET_REF.match(value):
        raise _invalid('secret_ref', 'name where the secret is -- env:NAME or keyring:service/name -- never the '
                                     'secret itself')
    return value


def _name_free(s, name, own_id=None):
    t = c.delivery_connections
    other = s.company.conn.execute(sa.select(t.c.id).where(t.c.name_key == name.casefold(), t.c.removed_at.is_(None),
                                                           t.c.id != (own_id or ''))).first()
    if other:
        raise _invalid('name', f'a connection named {name!r} already exists')


def _check_version(row, expected):
    if expected is not None and row['version'] != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': row['id'], 'expected_version': expected,
            'current_version': row['version']})


def _touch(s, ctx, row):
    at = clock.now_iso()
    return dict(row, version=row['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)


def prepare_add(s, ctx, inp) -> Plan:
    entry = _kind(inp.kind)
    name = ' '.join(inp.name.split())
    _name_free(s, name)
    at = clock.now_iso()
    row = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
               updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, name=name,
               name_key=name.casefold(), kind=entry.kind, settings=_settings(entry, inp.settings),
               secret_ref=_secret_ref(inp.secret_ref), enabled=False, enabled_host=None,
               daily_limit=inp.daily_limit, last_check_at=None, last_check_ok=None, last_check_text=None,
               removed_at=None)
    warnings = ['Added disabled: enable it on the machine that is to send (delivery-connection enable).']
    return Plan(project(s, row, warnings), dict(before=None, after=row, summary=f'added sending connection {name}'))


def prepare_update(s, ctx, inp) -> Plan:
    before = _row(s, inp.connection)
    _check_version(before, inp.expected_version)
    after = _touch(s, ctx, before)
    if inp.name is not None:
        name = ' '.join(inp.name.split())
        _name_free(s, name, before['id'])
        after.update(name=name, name_key=name.casefold())
    if inp.settings is not None:
        after['settings'] = _settings(_kind(before['kind']), inp.settings)
    if inp.no_secret:
        after['secret_ref'] = None
    elif inp.secret_ref is not None:
        after['secret_ref'] = _secret_ref(inp.secret_ref)
    if inp.no_daily_limit:
        after['daily_limit'] = None
    elif inp.daily_limit is not None:
        after['daily_limit'] = inp.daily_limit
    keys = ('name', 'settings', 'secret_ref', 'daily_limit')
    if all(after[k] == before[k] for k in keys):
        raise _invalid('connection', 'nothing to change')
    return Plan(project(s, after), dict(before=before, after=after,
                                        summary=f"updated sending connection {after['name']}"))


def prepare_enable(s, ctx, inp) -> Plan:
    before = _row(s, inp.connection)
    _check_version(before, inp.expected_version)
    _kind(before['kind'])
    after = dict(_touch(s, ctx, before), enabled=True, enabled_host=binding(s))
    return Plan(project(s, after), dict(before=before, after=after,
                                        summary=f"enabled sending connection {before['name']} on this machine"))


def prepare_disable(s, ctx, inp) -> Plan:
    before = _row(s, inp.connection)
    _check_version(before, inp.expected_version)
    after = dict(_touch(s, ctx, before), enabled=False, enabled_host=None)
    return Plan(project(s, after), dict(before=before, after=after,
                                        summary=f"disabled sending connection {before['name']}"))


def prepare_remove(s, ctx, inp) -> Plan:
    before = _row(s, inp.connection)
    _check_version(before, inp.expected_version)
    after = dict(_touch(s, ctx, before), enabled=False, enabled_host=None, removed_at=clock.now_iso())
    return Plan(project(s, after), dict(before=before, after=after,
                                        summary=f"removed sending connection {before['name']}"))


def prepare_check(s, ctx, inp) -> Plan:
    """Ask the transport whether it can connect with its secret. The answer is recorded on the connection."""
    before = _row(s, inp.connection)
    entry = _kind(before['kind'])
    try:
        result = entry.open(info(before)).check(info(before))
        ok, text = bool(result.ok), str(result.text or '')
    except Exception as error:  # noqa: BLE001 - a failed check is its answer
        ok, text = False, f'{type(error).__name__}: {error}'
    after = dict(_touch(s, ctx, before), last_check_at=clock.now_iso(), last_check_ok=ok, last_check_text=text[:500])
    return Plan(project(s, after), dict(before=before, after=after,
                                        summary=f"checked sending connection {before['name']}: "
                                                f"{'ok' if ok else 'failed'}"))


def apply(plan, ctx, s) -> Applied:
    before, after = plan.data['before'], plan.data['after']
    t = c.delivery_connections
    if before is None:
        s.company.conn.execute(t.insert().values(**after))
        touched = Touched(RECORD, after['id'], 'create', None, 1, after, db='company')
    else:
        done = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                      .values(**{k: v for k, v in after.items() if k != 'id'}))
        if done.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': before['id']})
        touched = Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                          db='company')
    return Applied(project(s, after, plan.preview.warnings), [touched], plan.data['summary'])


def show(s, inp) -> q.ConnectionOutput:
    return project(s, _row(s, inp.connection, removed=True))


def query(s, inp) -> q.ConnectionPageOutput:
    t = c.delivery_connections
    stmt = sa.select(t).order_by(t.c.name_key, t.c.id)
    if not inp.include_removed:
        stmt = stmt.where(t.c.removed_at.is_(None))
    rows = [project(s, dict(r)) for r in s.company.conn.execute(stmt).mappings()]
    kinds = transport.kinds()
    if any(r.usable for r in rows):
        note = 'Ready messages with a go are sent through the first usable connection, oldest first.'
    elif not kinds:
        note = NONE_NOTE
    else:
        note = 'No usable sending connection: nothing leaves this machine until one is enabled here.'
    return q.ConnectionPageOutput(rows=rows, count=len(rows), kinds_available=kinds, note=note)


def disable_all(raw) -> int:
    """A restored or copied company arrives with every connection disabled (``company restore``, on its raw
    connection inside its write)."""
    if not raw.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'delivery_connections'").fetchone():
        return 0
    return raw.execute('UPDATE delivery_connections SET enabled = 0, enabled_host = NULL '
                       'WHERE enabled = 1 OR enabled_host IS NOT NULL').rowcount
