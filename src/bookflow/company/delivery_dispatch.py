"""The queue: approving, retrying and dispatching messages (R112 increment 2; design/document-delivery.md §2, §4, §7).

**The go.** A ready message leaves only with a go: a person's release (``delivery send``) or approval (``delivery
approve``). An agent's release leaves it waiting. A go is given for the exact frozen content and recipients it saw:
holding, editing or refreshing the message, or an agent's release, ends it. A go counts only while the person who gave
it is active and holds a membership of this company (or its organization) at standard or above that does not deny
``customer``, and, where permissions are activated, while the policy still admits them; an installation administrator
without a membership does not count. At the claim, a go that no longer holds is withdrawn and the message waits for a
new one. (R187 converts "a person" to "an actor holding ``delivery.send``".)

**Dispatch** is three steps, so no database write is open while a transport talks to the outside:

1. ``decide`` reads the queue: leases that ran out; ready messages with a go and a due attempt, in order, each checked
   as it would leave -- the go still holds, it has a To, and its documents drawn now (read as the go's giver on the
   host's writer, else as the actor running the pass) still hash to
   the frozen PDFs -- and given a usable connection under its daily limit.
2. ``claim`` writes that, in one write: ``sending`` with a 10-minute lease and one more attempt (or ``held``, or the go
   withdrawn), each with its event. The claimed messages are read out as ``OutgoingMessage`` with their bytes.
3. The transports are called with no transaction open and the commit barrier open (``send``), each call bounded by
   ``transport.SEND_TIMEOUT``; just before each call the claim is read again and skipped unless it is still ``sending``
   under this attempt with room left on its lease. Then ``record`` writes every outcome in one write.

**Retries.** ``temporary`` comes back after 5 minutes, 30 minutes and 2 hours, then fails: four attempts since the last
``retry``. ``permanent`` fails at once. **An unknown outcome is never retried automatically**: a transport that says
unknown or raises, a crash between claim and record, or a lease that runs out leaves the message ``held``
(``outcome_unknown``) for a person to check their sent mail and choose ``mark-sent`` or ``retry``. A duplicate email
to a customer is worse than a late one. An ``accepted`` that arrives after the lease ran out resolves a held (or
retried, not yet claimed) message to ``sent``, and ``retry`` refuses once an acceptance is on record. Every attempt is
a ``claimed`` event and one outcome event.

**Where it runs.** ``delivery run`` is one pass on demand; it commits its claim and reopens the commit barrier before
calling out. The host's scheduler tick (``Host.schedule_now``) claims on the writer and hands the calls to the host's
own delivery thread, off the writer and off the timer thread, which then submits the record. A company with no
enabled connection and nothing in flight costs the tick one ``EXISTS``; a tick with only tracking to ask writes
nothing until there is an answer.

**Tracking.** A transport that defines ``events`` is asked about recently accepted messages until one bounces;
``delivered`` and ``bounced`` are recorded as events (no state change), and a bounce raises one to-do for the person
who drafted it. A transport's own words are kept redacted and capped (``transport.clean_text``).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa

from bookflow.company import delivery as d
from bookflow.company import delivery_connections as conns
from bookflow.company import delivery_queue_models as q
from bookflow.company import schema as c
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.delivery import transport

CHUNK = 10
LEASE = timedelta(minutes=10)
BACKOFF = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
MAX_ATTEMPTS = len(BACKOFF) + 1
TRACK_DAYS = 14
TRACK_REFS = 50
POLL_INTERVAL = timedelta(minutes=15)
_last_poll: dict[str, datetime] = {}
ACTS = {'claim': 'claimed', 'expire': 'expired', 'hold': 'held', 'withdraw': 'go withdrawn',
        'disconnect': 'connection disabled'}


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


# ---------------------------------------------------------------- the go

@contextmanager
def acting_as(s, user_id):
    """Load ``user_id`` as the session's actor for a block (its authority read afresh), then put the actor back."""
    from bookflow.company.scheduler import _act_as
    saved = (s.actor, list(s.memberships), s.credential, s.directive_code)
    try:
        _act_as(s, user_id)
        yield
    finally:
        s.actor, s.memberships, s.credential, s.directive_code = saved


GO_CAPABILITY = ('customer', 'standard')  # R187: delivery.send


def go_lost(s, go_by) -> str | None:
    """Why a go no longer counts, or None. Interim rule until R187: an active person with a standing membership of
    this company (or its organization) at standard or above whose denies do not name ``customer``, admitted by the
    activated policy where there is one. An installation administrator counts only through such a membership.
    R187: the go counts while its giver holds ``delivery.send``."""
    from bookflow.hub import schema as h
    from bookflow.hub.access import ROLE_RANK
    user = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == go_by)).mappings().first()
    if user is None or not user['active']:
        return 'the person who gave the go is no longer active'
    if user['kind'] != 'human':
        return "a go is a person's act"  # R187: delivery.send, which a principal may grant an agent
    who = user['display_name'] or user['username']
    capability, role = GO_CAPABILITY
    m, row = h.memberships, s.company_row
    held = False
    for member in s.hub.conn.execute(sa.select(m.c.role, m.c.denies).where(
            m.c.user_id == go_by, m.c.revoked_at.is_(None),
            sa.or_(sa.and_(m.c.scope_type == 'company', m.c.scope_id == row['id']),
                   sa.and_(m.c.scope_type == 'organization', m.c.scope_id == row['organization_id'])))).mappings():
        try:
            denies = set(json.loads(member['denies'] or '[]'))
        except ValueError:
            denies = {capability}  # unreadable denies: assume the worst
        if capability in denies or 'delivery.send' in denies:
            return f"{who} is denied {capability} in this company"
        held = held or ROLE_RANK.get(member['role'], -1) >= ROLE_RANK[role]
    if not held:
        return f"{who} may no longer send this company's email"
    if not _policy_admits(s, go_by, capability, role):
        return f"{who} no longer holds {capability} in this company"
    return None


def _policy_admits(s, user_id, capability, role) -> bool:
    """Where permissions are activated, whether the policy in force admits ``user_id`` for the requirement here
    (grants, denies and suspensions included). Always True in the legacy mode, which the membership rows decide."""
    from bookflow.hub import permission_catalog as pc, permission_policy as pp, permission_runtime as pr
    from bookflow.hub.permission_access import activated
    if not activated(s):
        return True
    s.hub.raw.execute('SAVEPOINT bookflow_go_check')  # one snapshot of the policy for the read
    try:
        comparison = pr._operation_observation(s.hub).snapshot.comparison
        scope = pc.ScopeKey('company', s.company_row['id'])
        if user_id not in comparison.subjects or scope not in comparison.scopes:
            return False
        return pp.execution(comparison, phase='old', actor=user_id, bound_human=None, scope=scope,
                            requirement=pc.Requirement(capability, role)).actor_admitted
    except Exception:  # noqa: BLE001 - a go the policy cannot vouch for does not count
        return False
    finally:
        s.hub.raw.execute('RELEASE SAVEPOINT bookflow_go_check')


def reading_as(s, user_id):
    """Read a document as the person who gave the go where the host allows it (its writer thread); elsewhere (an
    in-process ``delivery run``) as the actor running the pass, who holds the same reads."""
    from contextlib import nullcontext
    from threading import current_thread
    from bookflow.core.identity_admin_binding import SCHEDULER_THREADS
    return acting_as(s, user_id) if current_thread() in SCHEDULER_THREADS else nullcontext()


# ---------------------------------------------------------------- deciding and claiming

@dataclass
class Decision:
    act: str                      # claim | expire | hold | withdraw
    row: dict
    reason: str | None = None
    connection: dict | None = None


@dataclass
class Claim:
    message_id: str
    number: str
    attempt: int
    connection: dict
    attempt_key: str
    outgoing: transport.OutgoingMessage


@dataclass
class Pending:
    """What a claim handed to the senders: the claims, and the tracking questions to ask."""
    claims: list[Claim] = field(default_factory=list)
    polls: list[tuple[dict, list[str]]] = field(default_factory=list)
    rows: list[q.RunRow] = field(default_factory=list)
    connection: str = 'none'


def has_work(conn) -> bool:
    """The idle tick's one statement: an enabled connection, or a message in flight whose lease may run out."""
    return bool(conn.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM delivery_connections WHERE enabled = 1) "
        "OR EXISTS (SELECT 1 FROM delivery_messages WHERE status = 'sending')").scalar())


def decide(s, ctx, now: datetime, *, limit: int = CHUNK, poll: bool = True) -> tuple[list[Decision], list, str]:
    """What a pass would do now: (decisions, tracking polls, connection label). Reads only."""
    t = c.delivery_messages
    now_iso = _iso(now)
    decisions = [Decision('disconnect', cn, 'enabled on another installation or before this file was copied')
                 for cn in conns.foreign(s)]
    decisions += [Decision('expire', dict(r), 'outcome_unknown') for r in s.company.conn.execute(
        sa.select(t).where(t.c.status == 'sending', t.c.lease_until < now_iso).order_by(t.c.lease_until, t.c.id)
    ).mappings()]
    usable = conns.usable(s)
    if not usable:
        return decisions, [], 'none'
    since = _iso(now - timedelta(days=1))
    room = {cn['id']: (None if cn['daily_limit'] is None else
                       max(0, cn['daily_limit'] - conns.attempts_since(s, cn['id'], since))) for cn in usable}
    due = s.company.conn.execute(
        sa.select(t).where(t.c.status == 'ready', t.c.go_by.is_not(None),
                           sa.or_(t.c.next_attempt_at.is_(None), t.c.next_attempt_at <= now_iso))
        .order_by(sa.func.coalesce(t.c.next_attempt_at, t.c.go_at), t.c.id).limit(limit)).mappings().all()
    docs = d._docs(s, [r['id'] for r in due])
    for row in due:
        row = dict(row)
        why = go_lost(s, row['go_by'])
        if why:
            decisions.append(Decision('withdraw', row, why))
            continue
        if not json.loads(row['to_addresses']):
            decisions.append(Decision('hold', row, 'no_recipient'))
            continue
        with reading_as(s, row['go_by']):
            stale = d.content_staleness(s, ctx, docs.get(row['id'], []))
        if stale:
            decisions.append(Decision('hold', row, stale))
            continue
        connection = next((cn for cn in usable if room[cn['id']] is None or room[cn['id']] > 0), None)
        if connection is None:
            break  # every connection reached its daily limit; the rest wait for tomorrow's room
        if room[connection['id']] is not None:
            room[connection['id']] -= 1
        decisions.append(Decision('claim', row, connection=connection))
    polls = _polls(s, usable, now) if poll else []
    return decisions, polls, usable[0]['name']


def _polls(s, usable, now) -> list[tuple[dict, list[str]]]:
    """Accepted messages a transport with ``events`` may still report on: until one bounces (a relay's "delivered"
    is often followed by the far end's bounce)."""
    t, e = c.delivery_messages, c.delivery_events
    out = []
    since = _iso(now - timedelta(days=TRACK_DAYS))
    for cn in usable:
        opener = transport.get(cn['kind'])
        if opener is None:
            continue
        refs = [r[0] for r in s.company.conn.execute(
            sa.select(t.c.provider_ref).where(
                t.c.status == 'sent', t.c.sent_via == 'connection', t.c.connection_id == cn['id'],
                t.c.provider_ref.is_not(None), t.c.sent_at >= since,
                ~sa.exists(sa.select(e.c.id).where(e.c.message_id == t.c.id, e.c.event == 'bounced')))
            .order_by(t.c.sent_at.desc()).limit(TRACK_REFS))]
        if refs:
            out.append((cn, refs))
    return out


def _outgoing(s, row, docs, connection) -> transport.OutgoingMessage:
    settings = json.loads(connection['settings'])
    return transport.OutgoingMessage(
        message_id=row['id'], number=row['number'], rfc_message_id=row['message_id'],
        from_name=row['from_name'] or settings.get('from_name'),
        from_address=row['from_address'] or settings.get('from_address'), reply_to=row['reply_to'],
        to=tuple(json.loads(row['to_addresses'])), cc=tuple(json.loads(row['cc_addresses'])),
        bcc=tuple(json.loads(row['bcc_addresses'])), subject=row['subject'], body=row['body'],
        attachments=tuple(transport.Attachment(doc['file_name'], doc['media_type'], d.file_bytes(s, doc['sha256']),
                                               doc['sha256']) for doc in docs))


def _write(s, ctx, before, after, event, details, at) -> list[Touched] | None:
    t = c.delivery_messages
    done = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        return None  # changed since it was read: left for the next pass
    row = d._event(s, ctx, after['id'], event, after['status'], details, at=at)
    return [Touched(d.RECORD, after['id'], 'update', before['version'], after['version'], after, before, db='company'),
            Touched('delivery_event', row['id'], 'create', None, None, row, db='company')]


def _fresh(s, row) -> dict | None:
    """The row as it is now, if it has not moved since it was decided on."""
    t = c.delivery_messages
    now = s.company.conn.execute(sa.select(t).where(t.c.id == row['id'])).mappings().first()
    return dict(now) if now is not None and now['version'] == row['version'] else None


def claim(s, ctx, decisions, now: datetime) -> tuple[list[Claim], list[Touched], list[q.RunRow]]:
    """Write the decisions, inside the caller's open write. Returns the claims to send."""
    at, claims, touched, rows = _iso(now), [], [], []
    for dec in decisions:
        if dec.act == 'disconnect':
            written = _disconnect(s, ctx, dec.row, at)
            if written:
                touched += written
                rows.append(q.RunRow(number=dec.row['name'], act=ACTS['disconnect'], status='disabled',
                                     text=dec.reason))
            continue
        before = _fresh(s, dec.row)
        if before is None:
            continue
        after = d._touch(s, ctx, before, at)
        if dec.act == 'expire':
            if before['status'] != 'sending':
                continue
            after.update(status='held', hold_reason='outcome_unknown', lease_until=None)
            event, details = 'held', {'reason': 'outcome_unknown', 'attempt': before['attempts'],
                                      'why': 'its lease ran out before an answer was recorded'}
            d.drop_go(before, after, details)
        elif dec.act == 'withdraw':
            after.update(go_by=None, go_at=None)
            event, details = 'edited', {'changed': ['go'], 'go_withdrawn': before['go_by'], 'why': dec.reason}
        elif dec.act == 'hold':
            after.update(status='held', hold_reason=dec.reason)
            event, details = 'held', {'reason': dec.reason, 'at': 'claim'}
            d.drop_go(before, after, details)
        else:
            attempt = before['attempts'] + 1
            after.update(status='sending', attempts=attempt, lease_until=_iso(now + LEASE),
                         connection_id=dec.connection['id'], next_attempt_at=None)
            key = f"{before['id']}:{attempt}"
            event, details = 'claimed', {'attempt': attempt, 'attempt_key': key, 'connection': dec.connection['id'],
                                         'connection_name': dec.connection['name'], 'lease_until': after['lease_until']}
        written = _write(s, ctx, before, after, event, details, at)
        if written is None:
            continue
        touched += written
        rows.append(q.RunRow(number=before['number'], act=ACTS[dec.act], status=after['status'],
                             attempt=after['attempts'] if dec.act == 'claim' else None,
                             text=dec.reason if dec.act in ('withdraw', 'hold') else None))
        if dec.act == 'claim':
            docs = d._docs(s, [before['id']]).get(before['id'], [])
            claims.append(Claim(before['id'], before['number'], after['attempts'], dec.connection,
                                details['attempt_key'], _outgoing(s, after, docs, dec.connection)))
    return claims, touched, rows


def _disconnect(s, ctx, row, at) -> list[Touched] | None:
    """Disable a connection enabled somewhere other than here (a copy, an attached file, another installation)."""
    t = c.delivery_connections
    after = dict(row, enabled=False, enabled_host=None, version=row['version'] + 1, updated_at=at,
                 updated_by=s.actor.id, updated_via=ctx.interface.value)
    done = s.company.conn.execute(t.update().where(t.c.id == row['id'], t.c.version == row['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        return None
    return [Touched(conns.RECORD, row['id'], 'update', row['version'], after['version'], after, row, db='company')]


# ---------------------------------------------------------------- sending (no transaction open)

def still_claimed(s, cl: 'Claim', now: datetime) -> bool:
    """Just before a send: the row is still ``sending`` under this attempt and connection, with room left on its lease
    for a whole bounded call. A held, cancelled or expiring claim is skipped (nothing leaves)."""
    t = c.delivery_messages
    row = s.company.conn.execute(sa.select(t.c.status, t.c.attempts, t.c.connection_id, t.c.lease_until)
                                 .where(t.c.id == cl.message_id)).mappings().first()
    return bool(row is not None and row['status'] == 'sending' and row['attempts'] == cl.attempt
                and row['connection_id'] == cl.connection['id'] and row['lease_until']
                and row['lease_until'] > _iso(now + timedelta(seconds=transport.SEND_TIMEOUT)))


def send(claims: list[Claim], polls, *, check=None, answered=None) -> tuple[list[transport.Outcome],
                                                                     list[tuple[dict, list[transport.TransportEvent]]]]:
    """Call each claim's transport, then ask the tracking questions. Never touches the database itself: ``check(claim)``
    (the caller's read of ``still_claimed``) runs before each send, and a claim it refuses is skipped; ``answered``
    hears each outcome as it comes. Every transport call is bounded."""
    outcomes, opened = [], {}
    for cl in claims:
        if check is not None and not check(cl):
            outcome = transport.Outcome(cl.message_id, cl.attempt, cl.connection['id'], transport.TransportResult.temporary(
                'not_sent', 'its claim lapsed before it was handed to the transport; nothing left'), skipped=True)
            outcomes.append(outcome)
            if answered is not None:
                answered(outcome)
            continue
        entry = transport.get(cl.connection['kind'])
        result = None
        if cl.connection['id'] not in opened:
            try:
                opened[cl.connection['id']] = (transport.bounded(entry.open, transport.CALL_TIMEOUT,
                                                                 conns.info(cl.connection)) if entry else None)
            except Exception as error:  # noqa: BLE001 - nothing left: the transport could not be opened
                opened[cl.connection['id']] = None
                result = transport.TransportResult.temporary(
                    'open_failed', transport.clean_text(f'{type(error).__name__}: {error}'))
        sender = opened[cl.connection['id']]
        if result is None:
            result = (transport.call_send(sender, cl.outgoing, cl.attempt_key) if sender is not None else
                      transport.TransportResult.temporary('unavailable', 'the transport could not be opened'))
        outcome = transport.Outcome(cl.message_id, cl.attempt, cl.connection['id'], result)
        outcomes.append(outcome)
        if answered is not None:
            answered(outcome)
    reports = []
    for cn, refs in polls:
        entry = transport.get(cn['kind'])
        try:
            sender = opened.get(cn['id']) or transport.bounded(entry.open, transport.CALL_TIMEOUT, conns.info(cn))
            events = getattr(sender, 'events', None)
            if events is not None:
                found = transport.bounded(events, transport.CALL_TIMEOUT, conns.info(cn), refs)
                reports.append((cn, [e for e in found if e.provider_ref in refs]))
        except Exception:  # noqa: BLE001 - tracking is best effort (a hang included); the next pass asks again
            continue
    return outcomes, reports


# ---------------------------------------------------------------- recording

def _since_retry(s, row) -> int:
    e = c.delivery_events
    last = s.company.conn.execute(sa.select(e.c.details).where(e.c.message_id == row['id'], e.c.event == 'retried')
                                  .order_by(e.c.ordinal.desc()).limit(1)).scalar()
    base = json.loads(last).get('attempts_before', 0) if last else 0
    return row['attempts'] - base


def record(s, ctx, outcomes, reports, now: datetime) -> tuple[list[Touched], list[q.RunRow], int]:
    """Write every outcome (and tracking report), inside the caller's open write."""
    t = c.delivery_messages
    at, touched, rows, tracked = _iso(now), [], [], 0
    for o in outcomes:
        before = s.company.conn.execute(sa.select(t).where(t.c.id == o.message_id)).mappings().first()
        if before is None:
            continue
        before, r = dict(before), o.result
        info = {'attempt': o.attempt, 'connection': o.connection_id}
        live = (before['status'] == 'sending' and before['attempts'] == o.attempt
                and before['connection_id'] == o.connection_id)
        text = transport.clean_text(r.text)
        if o.skipped:
            if not live:
                rows.append(q.RunRow(number=before['number'], act='skipped', status=before['status'], attempt=o.attempt,
                                     text='its claim lapsed before it was sent; nothing left'))
                continue
            # Nothing left: back to ready with its go, due now; a skipped claim is not a failed attempt.
            after = dict(d._touch(s, ctx, before, at), lease_until=None, status='ready', next_attempt_at=None)
            written = _write(s, ctx, before, after, 'attempt_failed', {
                **info, 'outcome': 'not_sent', 'code': 'not_sent', 'text': text, 'next_attempt_at': None,
                'final': False}, at)
            if written:
                touched += written
                rows.append(q.RunRow(number=before['number'], act='skipped', status='ready', attempt=o.attempt,
                                     text=text))
            continue
        if not live and r.outcome == 'accepted' and before['status'] in ('held', 'ready'):
            # Its lease ran out (held outcome_unknown, or already retried and waiting): the far side took it, so it is
            # sent, and nothing sends it again.
            after = dict(d._touch(s, ctx, before, at), status='sent', hold_reason=None, lease_until=None,
                         next_attempt_at=None, sent_at=at, sent_via='connection', connection_id=o.connection_id,
                         provider_ref=(r.provider_ref or '')[:200], from_address=r.actual_from or before['from_address'])
            written = _write(s, ctx, before, after, 'accepted', {
                **info, 'late': True, 'provider_ref': r.provider_ref, 'actual_from': r.actual_from, 'sent_at': at,
                'resolved_from': before['status']}, at)
            if written:
                touched += written
                rows.append(q.RunRow(number=before['number'], act='recorded', status='sent', attempt=o.attempt,
                                     outcome=r.outcome, text='accepted after its lease ran out'))
                continue
        if not live:
            # Its lease ran out and a person may already have acted: the answer is kept in the history, nothing moves.
            if r.outcome != 'unknown':
                event = 'accepted' if r.outcome == 'accepted' else 'attempt_failed'
                row = d._event(s, ctx, before['id'], event, before['status'], {
                    **info, 'late': True, 'outcome': r.outcome, 'code': r.code, 'text': text,
                    'provider_ref': r.provider_ref}, at=at)
                touched.append(Touched('delivery_event', row['id'], 'create', None, None, row, db='company'))
            rows.append(q.RunRow(number=before['number'], act='late answer', status=before['status'],
                                 attempt=o.attempt, outcome=r.outcome, code=r.code, text=text or None))
            continue
        after = dict(d._touch(s, ctx, before, at), lease_until=None)
        next_at = None
        if r.outcome == 'accepted':
            after.update(status='sent', sent_at=at, sent_via='connection', provider_ref=(r.provider_ref or '')[:200],
                         from_address=r.actual_from or before['from_address'])
            event, details = 'accepted', {**info, 'provider_ref': r.provider_ref, 'actual_from': r.actual_from,
                                          'sent_at': at}
        elif r.outcome == 'unknown':
            after.update(status='held', hold_reason='outcome_unknown')
            event, details = 'held', {**info, 'reason': 'outcome_unknown', 'text': text}
            d.drop_go(before, after, details)
        else:
            used = _since_retry(s, before)
            if r.outcome == 'temporary' and used < MAX_ATTEMPTS:
                next_at = _iso(now + BACKOFF[used - 1])
                after.update(status='ready', next_attempt_at=next_at, failure_code=(r.code or 'temporary')[:64],
                             failure_text=text or None)
            else:
                after.update(status='failed', failure_code=(r.code or r.outcome)[:64], failure_text=text or None)
            event, details = 'attempt_failed', {**info, 'outcome': r.outcome, 'code': r.code, 'text': text,
                                                'next_attempt_at': next_at, 'final': after['status'] == 'failed'}
        written = _write(s, ctx, before, after, event, details, at)
        if written:
            touched += written
            rows.append(q.RunRow(number=before['number'], act='recorded', status=after['status'], attempt=o.attempt,
                                 outcome=r.outcome, code=r.code, text=text or None, next_attempt_at=next_at))
    for cn, events in reports:
        for ev in events:
            row = s.company.conn.execute(sa.select(t).where(t.c.connection_id == cn['id'],
                                                            t.c.provider_ref == ev.provider_ref)).mappings().first()
            if row is None or row['status'] != 'sent':
                continue
            e = c.delivery_events
            if s.company.conn.execute(sa.select(e.c.id).where(e.c.message_id == row['id'],
                                                              e.c.event == ev.event)).first():
                continue
            event = d._event(s, ctx, row['id'], ev.event, 'sent', {'provider_ref': ev.provider_ref,
                             'reported_at': ev.at, 'text': transport.clean_text(ev.text), 'connection': cn['id']}, at=at)
            touched.append(Touched('delivery_event', event['id'], 'create', None, None, event, db='company'))
            tracked += 1
            if ev.event == 'bounced':
                touched += _bounce_todo(s, ctx, dict(row), ev)
    return touched, rows, tracked


def _bounce_todo(s, ctx, row, ev) -> list[Touched]:
    from bookflow.company import todos
    from bookflow.hub import schema as h
    kind = s.hub.conn.execute(sa.select(h.users.c.kind).where(h.users.c.id == row['created_by'])).scalar()
    assignee = row['created_by'] if kind == 'human' else row['go_by'] or row['created_by']
    todo = todos.scheduler_todo(
        s, ctx, source_ref=f"delivery-bounce:{row['id']}", title=f"Email {row['number']} bounced",
        notes=f"The mail server could not deliver {row['number']} ({row['subject']}): "
              f"{transport.clean_text(ev.text) or 'no reason given'}. "
              'Check the address and draft it again.', assignee_id=assignee, due_date=clock.today_iso())
    if todo is None:
        return []
    s.company.conn.execute(c.todos.insert().values(**todo))
    return [Touched('todo', todo['id'], 'create', None, 1, todo, db='company')]


def tally(out: q.RunOutput, rows: list[q.RunRow]) -> q.RunOutput:
    for row in rows:
        if row.act == 'claimed':
            out.claimed += 1
        elif row.act == 'expired':
            out.expired += 1
        elif row.act == 'held':
            out.held += 1
        elif row.act == 'go withdrawn':
            out.go_withdrawn += 1
        elif row.act == 'recorded':
            if row.status == 'sent':
                out.sent += 1
            elif row.status == 'ready':
                out.retrying += 1
            elif row.status == 'failed':
                out.failed += 1
            elif row.status == 'held':
                out.held += 1
    out.messages = rows
    return out


# ---------------------------------------------------------------- `delivery run`

def prepare_run(s, ctx, inp) -> Plan:
    now = clock.now()
    decisions, polls, label = decide(s, ctx, now, limit=inp.limit)
    rows = [q.RunRow(number=dec.row.get('number') or dec.row['name'], act=ACTS[dec.act],
                     status=dec.row.get('status') or 'enabled', text=dec.reason) for dec in decisions]
    preview = q.RunOutput(connection=label, claimed=sum(1 for dec in decisions if dec.act == 'claim'), messages=rows)
    if label == 'none':
        preview.warnings.append(f'{preview.claimed} claimed, no sending connection. ' + d.NO_CONNECTION)
    return Plan(preview, dict(decisions=decisions, polls=polls, label=label, now=now, input_hash=_input_hash(s, inp)))


def _input_hash(s, inp):
    from bookflow.core import idempotency
    return idempotency.input_hash(inp.model_dump(mode='json'), s.company_id)


def _audit(s, ctx, summary, touched):
    from bookflow.core.audit import write_event_to
    if touched:
        write_event_to(s.company, ctx, 'delivery run', summary, touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                       directive_code=s.directive_code)


def apply_run(plan, ctx, s) -> Applied:
    """Claim and commit, then reopen the commit barrier; call the transports with nothing open (each claim read again
    just before its send); record and commit. Owns its transactions."""
    from bookflow.core import idempotency
    from bookflow.core.dispatch import _upsert_principals
    data = plan.data
    with s.commits.operation('delivery.run', s.hub, s.company):
        try:
            _upsert_principals(s, ctx)
            claims, touched, rows = claim(s, ctx, data['decisions'], data['now'])
            _audit(s, ctx, f'claimed {len(claims)} message(s) to send', touched)
            s.commits.commit(s.company, 'delivery.run')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    # The claim is committed: readers may go on while the transports talk to the outside.
    s.commits.reopen()
    outcomes, reports = send(claims, data['polls'], check=lambda cl: still_claimed(s, cl, clock.now()))
    now = clock.now()
    with s.commits.operation('delivery.run', s.hub, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            recorded, more, tracked = record(s, ctx, outcomes, reports, now)
            out = tally(q.RunOutput(connection=data['label'], claimed=0, tracking_events=tracked), rows + more)
            if data['label'] == 'none':
                out.warnings.append(f'{out.claimed} claimed, no sending connection. ' + d.NO_CONNECTION)
            _audit(s, ctx, f'recorded {len(outcomes)} send attempt(s)', recorded)
            if ctx.idempotency_key:
                idempotency.store(s.company, s.actor.id, ctx.idempotency_key, 'delivery run', data['input_hash'],
                                  ctx.request_id, out.model_dump(mode='json'))
            s.commits.commit(s.company, 'delivery.run')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return Applied(out, [], f'dispatched: {out.claimed} claimed, {out.sent} sent', finalized=True, audited=True)


# ---------------------------------------------------------------- the host's tick

def tick_claim(s, system_id, now: datetime) -> Pending | None:
    """The scheduler's claim for one company, on the writer. None, after one EXISTS, when there is nothing to do."""
    if not has_work(s.company.conn):
        return None
    from bookflow.company.scheduler import _act_as, context
    _act_as(s, system_id)
    ctx = context().model_copy(update={'actor_id': system_id, 'reason': 'On schedule: sending the outbox'})
    last = _last_poll.get(s.company_row['id'])
    poll = last is None or not timedelta(0) <= now - last < POLL_INTERVAL
    decisions, polls, label = decide(s, ctx, now, poll=poll)
    if poll and polls:
        _last_poll[s.company_row['id']] = now
    if not decisions:  # nothing to write; tracking alone takes no write and no commit barrier
        return Pending(polls=polls, connection=label)
    with s.commits.operation('delivery.tick', s.hub, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            claims, touched, rows = claim(s, ctx, decisions, now)
            _audit(s, ctx, f'claimed {len(claims)} message(s) to send', touched)
            s.commits.commit(s.company, 'delivery.tick')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return Pending(claims=claims, polls=polls, rows=rows, connection=label)


def tick_record(s, system_id, outcomes, reports, now: datetime) -> q.RunOutput:
    from bookflow.company.scheduler import _act_as, context
    _act_as(s, system_id)
    ctx = context().model_copy(update={'actor_id': system_id, 'reason': 'On schedule: sending the outbox'})
    with s.commits.operation('delivery.tick', s.hub, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            touched, rows, tracked = record(s, ctx, outcomes, reports, now)
            _audit(s, ctx, f'recorded {len(outcomes)} send attempt(s)', touched)
            s.commits.commit(s.company, 'delivery.tick')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return tally(q.RunOutput(connection='', claimed=0, tracking_events=tracked), rows)


# ---------------------------------------------------------------- approve and retry

def prepare_approve(s, ctx, inp) -> Plan:
    """A person's go for ready messages waiting for one. People-only; R187: ``delivery.send``."""
    t = c.delivery_messages
    if inp.all_waiting or getattr(inp, 'batch', None):
        q = sa.select(t).where(t.c.status == 'ready', t.c.go_by.is_(None))
        if getattr(inp, 'batch', None):
            from bookflow.company import delivery_batches as batches
            q = q.where(t.c.batch_id == batches.row(s, inp.batch)['id'])
        rows = [dict(r) for r in s.company.conn.execute(q.order_by(t.c.released_at, t.c.id).limit(100)).mappings()]
    elif inp.messages:
        rows, seen = [], set()
        for selector in inp.messages:
            row = d._row(s, selector)
            if row['id'] not in seen:
                seen.add(row['id'])
                rows.append(row)
    else:
        raise d._invalid('messages', 'name the messages to approve, or give all_waiting')
    at, plans, warnings = clock.now_iso(), [], []
    for before in rows:
        if before['status'] != 'ready':
            raise d._conflict(before, problem='only a ready message waits for a go', next_step=(
                f"delivery send {before['number']}" if before['status'] == 'draft' else None))
        if before['go_by'] is not None:
            warnings.append(f"Message {before['number']} already has a go.")
            continue
        after = dict(d._touch(s, ctx, before, at), go_by=s.actor.id, go_at=at)
        plans.append((before, after, 'approved', {'go_by': s.actor.id, 'go_at': at}))
    if plans and conns.label(s) == 'none':
        warnings.append(d.NO_CONNECTION)
    outs = [d.project(s, after) for _, after, _, _ in plans]
    return Plan(q_messages(outs, warnings), dict(plans=plans, summary='approved message(s) ' + ', '.join(
        a['number'] for _, a, _, _ in plans) if plans else 'approved nothing'))


def q_messages(outs, warnings):
    from bookflow.company import delivery_models as m
    return m.MessagesOutput(messages=outs, count=len(outs), warnings=warnings)


def apply_many(plan, ctx, s) -> Applied:
    touched, outs = [], []
    for before, after, event_name, details in plan.data['plans']:
        written = _write(s, ctx, before, after, event_name, details, after['updated_at'])
        if written is None:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': d.RECORD, 'record_id': before['id']})
        touched += written
        outs.append(d.project(s, after))
    return Applied(q_messages(outs, plan.preview.warnings), touched, plan.data['summary'])


RETRYABLE_HOLDS = ('outcome_unknown', 'no_recipient', 'template_error')


def prepare_retry(s, ctx, inp) -> Plan:
    """A held or failed message back to ready. A person's retry is also their go; an agent's waits for one
    (R187: ``delivery.send``). A message held because its document changed is refreshed instead."""
    before = d._row(s, inp.message)
    d._check_version(before, inp.expected_version)
    if before['status'] not in ('held', 'failed'):
        raise d._conflict(before, problem='only a held or failed message can be retried')
    if before['status'] == 'held' and before['hold_reason'] not in RETRYABLE_HOLDS:
        raise d._conflict(before, problem=d.HOLD_TEXT[before['hold_reason']],
                          next_step=d._refresh_hint(before['number'], before['hold_reason']))
    e = c.delivery_events
    if s.company.conn.execute(sa.select(e.c.id).where(e.c.message_id == before['id'], e.c.event == 'accepted')
                              .limit(1)).first():
        raise d._conflict(before, problem='its transport accepted an attempt already; sending it again would be a '
                                          'second copy', next_step=f"delivery mark-sent {before['number']}")
    if not json.loads(before['to_addresses']):
        raise d._invalid('to', f"message {before['number']} has no To address; add one with delivery update",
                         number=before['number'])
    at = clock.now_iso()
    person = s.actor.kind == 'human'
    after = dict(d._touch(s, ctx, before, at), status='ready', hold_reason=None, next_attempt_at=None,
                 lease_until=None, failure_code=None, failure_text=None,
                 go_by=s.actor.id if person else None, go_at=at if person else None)
    details = {'from': before['status'], 'hold_reason': before['hold_reason'], 'attempts_before': before['attempts'],
               'go_by': after['go_by']}
    warnings = []
    if before['hold_reason'] == 'outcome_unknown':
        warnings.append('An earlier attempt may have reached the customer. Retry only after checking the sent mail '
                        'of the connection; if it went, mark it sent instead.')
    if not person:
        warnings.append("An agent's retry waits for a person's go (delivery approve).")
    return d._plan_single(s, ctx, before, after, 'retried', details, f"retried message {before['number']}",
                          warnings=warnings)
