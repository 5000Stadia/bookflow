"""Authenticated, reconstructable settlement baselines; never authorization tokens."""
import base64
import hmac
import json

import sqlalchemy as sa

from bookflow.company import schema as c, sales, document_effects as effects
from bookflow.company import payment_queries as query
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
from bookflow.company.payment_authority import authorize
from bookflow.company.ledger_reports import _cursor_key
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError

DOMAIN = b'payment-settlement-guard-v1\0'

# Both ends of a settlement edge own a graph: the receipt that supplied the money, and the
# receivable it settled. Which receivables those are is the settlement contract's own answer,
# not a second list kept here.
OWNER_TYPES = ('payment', *SETTLEABLE_RECEIVABLE_TYPES)


def watermark(s):
    return s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.audit_events.c.seq), 0))).scalar_one()


def _rows_at(s, table, cutoff, *conditions):
    return [dict(row) for row in s.company.conn.execute(sa.select(table).join(c.audit_events,
        c.audit_events.c.id == table.c.audit_event_id).where(c.audit_events.c.seq <= cutoff, *conditions)).mappings()]


def _headers_at(s, identifiers, cutoff):
    headers = {}
    identifiers = sorted(identifiers)
    for offset in range(0, len(identifiers), 200):
        chunk = identifiers[offset:offset+200]
        if cutoff is None:
            headers.update({row['id']: dict(row) for row in s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id.in_(chunk))).mappings()})
            continue
        ranked = sa.select(c.audit_entries, sa.func.row_number().over(partition_by=c.audit_entries.c.record_id,
            order_by=(c.audit_events.c.seq.desc(), c.audit_entries.c.id.desc())).label('history_rank')).join(c.audit_events,
            c.audit_events.c.id == c.audit_entries.c.event_id).where(c.audit_entries.c.record_type == 'transaction',
            c.audit_entries.c.record_id.in_(chunk), c.audit_events.c.seq <= cutoff).subquery()
        rows = s.company.conn.execute(sa.select(ranked).where(ranked.c.history_rank <= 2).order_by(ranked.c.record_id, ranked.c.history_rank)).mappings()
        grouped = {}
        for row in rows:
            grouped.setdefault(row['record_id'], []).append(row)
        for identifier, entries in grouped.items():
            if len(entries) > 1 and entries[0]['event_id'] == entries[1]['event_id']:
                continue
            try:
                row = entries[0]
                value = audit.decode_snapshot(row['after'])
                if (isinstance(value, dict) and value.get('id') == identifier and value.get('type') in OWNER_TYPES
                    and type(value.get('version')) is int and value['version'] == row['version_after']
                    and isinstance(value.get('current_revision_id'), str)):
                    headers[identifier] = value
            except Exception:
                continue
    if cutoff is not None and headers:
        revision_owners, document_types = {}, {}
        candidates = list(headers)
        for offset in range(0, len(candidates), 200):
            chunk = candidates[offset:offset+200]
            revisions = [headers[identifier]['current_revision_id'] for identifier in chunk]
            revision_owners.update(dict(s.company.conn.execute(sa.select(c.transaction_revisions.c.id,
                c.transaction_revisions.c.transaction_id).where(c.transaction_revisions.c.id.in_(revisions))).all()))
            document_types.update(dict(s.company.conn.execute(sa.select(c.transactions.c.id, c.transactions.c.type).where(
                c.transactions.c.id.in_(chunk))).all()))
        headers = {identifier: row for identifier, row in headers.items()
                   if revision_owners.get(row['current_revision_id']) == identifier and document_types.get(identifier) == row['type']}
    return headers


def _header_at(s, identifier, cutoff):
    return _headers_at(s, [identifier], cutoff).get(identifier)


def graph(s, owner_type, owner_id, *, cutoff=None, write=False):
    if owner_type not in OWNER_TYPES:
        raise BookflowError('E_VALIDATION')
    owner = sales.resolve(s, owner_id, owner_type)
    authorize(s, [owner['id']], write=write)
    limit = watermark(s) if cutoff is None else cutoff
    field = 'paying_transaction_id' if owner_type == 'payment' else 'paid_transaction_id'
    apps = _rows_at(s, c.applications, limit, getattr(c.applications.c, field) == owner['id'])
    inverses = {row['reverses_application_id'] for row in apps if row['kind'] == 'unapply'}
    active = [row for row in apps if row['kind'] == 'apply' and row['id'] not in inverses and row[field] == owner['id']]
    ids = {owner['id']} | {row['paying_transaction_id'] for row in active} | {row['paid_transaction_id'] for row in active}
    authorize(s, ids, write=write)
    allocation_rows = []
    app_ids = sorted(row['id'] for row in active)
    for offset in range(0, len(app_ids), 200):
        allocation_rows.extend(_rows_at(s, c.application_allocations, limit, c.application_allocations.c.application_id.in_(app_ids[offset:offset+200])))
    reversed_ids = {row['reverses_allocation_id'] for row in allocation_rows if row['kind'] == 'reversal'}
    app_ids = {row['id'] for row in active}
    live = [row for row in allocation_rows if row['kind'] == 'allocation' and row['id'] not in reversed_ids and row['application_id'] in app_ids]
    headers = _headers_at(s, ids, limit if cutoff is not None else None)
    unknown = sorted(ids - set(headers))
    payload = dict(owner_type=owner_type, owner_id=owner['id'],
        headers=[[identifier, value['version'], value['current_revision_id']] for identifier, value in sorted(headers.items())],
        # Both source columns are in the payload: a credit settling this invoice has to move
        # the digest, or a preview prepared before it would still look fresh after it.
        applications=[[row[k] for k in ('id', 'paying_transaction_id', 'paid_transaction_id', 'source_component_key_id', 'credit_source_key_id', 'amount_minor_units', 'currency', 'effective_date')]
                      for row in sorted(active, key=lambda row: row['id'])],
        allocations=[[row[k] for k in ('id', 'application_id', 'source_revision_id', 'credit_source_component_id', 'target_revision_id', 'amount_minor_units', 'effective_date')]
                     for row in sorted(live, key=lambda row: row['id'])])
    return dict(payload=payload, digest=query.digest(payload), headers=headers, unknown=unknown, applications=active, allocations=live)


def receivable_owner_type(s, identifier):
    """Which settleable receivable an identifier names, so a guard is issued for what it is."""
    return sales.resolve(s, identifier, SETTLEABLE_RECEIVABLE_TYPES)['type']


def issue(s, owner_type, owner_id):
    current = graph(s, owner_type, owner_id)
    owner_id = current['payload']['owner_id']
    payload = dict(v=1, company_id=s.company_row['id'], owner_type=owner_type, owner_id=owner_id,
        owner_version=current['headers'][owner_id]['version'], baseline_audit_seq=watermark(s),
        issued_at=clock.now_iso(), graph_digest=current['digest'])
    return _sign(s, payload)


def _sign(s, payload):
    raw = query.canonical(payload).encode()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return encode(raw) + '.' + encode(hmac.digest(_cursor_key(s.company), DOMAIN + raw, 'sha256'))


def changes_since_version(s, header, expected):
    """Return only comparisons whose exact owned expected header is proven."""
    if type(expected) is not int or not 0 < expected < header['version']:
        return None
    matches = s.company.conn.execute(sa.select(c.audit_events.c.seq).join(c.audit_entries,
        c.audit_entries.c.event_id == c.audit_events.c.id).where(c.audit_entries.c.record_type == 'transaction',
        c.audit_entries.c.record_id == header['id'], c.audit_entries.c.version_after == expected)).scalars().all()
    if len(matches) != 1:
        return None
    owned = _header_at(s, header['id'], matches[0])
    if owned is None or owned['version'] != expected:
        return None
    baseline = graph(s, header['type'], header['id'], cutoff=matches[0])
    if baseline['unknown']:
        return None
    token = _sign(s, dict(v=1, company_id=s.company_row['id'], owner_type=header['type'], owner_id=header['id'],
        owner_version=expected, baseline_audit_seq=matches[0], issued_at=clock.now_iso(), graph_digest=baseline['digest']))
    result = compare(s, token)
    return None if result['unknown_history'] else result['changes']


def payment_version(s, header, expected):
    from bookflow.company import journals
    try:
        return journals.version_meta(s, header, expected, history_decoder=sales._history_snapshot)
    except BookflowError as exc:
        if exc.code != 'E_VERSION_CONFLICT':
            raise
        changes = changes_since_version(s, header, expected) if 'unknown_versions' not in exc.details else None
        if changes is None:
            exc.details.setdefault('unknown_versions', [expected])
            exc.details['changed_fields'] = []
        else:
            own = [row for row in changes if row['record_id'] == header['id']]
            fields = sorted({field for row in own for field in (row['fields'] or []) + row['settlement_fields']})
            exc.details.update(changed_fields=fields, settlement_changes=[row for row in changes if row['settlement_fields']][:50])
        raise BookflowError(exc.code, message=sales._sale_conflict_message(exc.details, exc.details['changed_fields']), details=exc.details) from None


def decode(s, token):
    try:
        if len(token) > 2048:
            raise ValueError()
        body, signature = token.split('.')
        raw = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
        mac = base64.b64decode(signature + '=' * (-len(signature) % 4), altchars=b'-_', validate=True)
        if not hmac.compare_digest(mac, hmac.digest(_cursor_key(s.company), DOMAIN + raw, 'sha256')):
            raise ValueError()
        value = json.loads(raw)
        required = {'v', 'company_id', 'owner_type', 'owner_id', 'owner_version', 'baseline_audit_seq', 'issued_at', 'graph_digest'}
        if (not isinstance(value, dict) or set(value) != required or type(value['v']) is not int or value['v'] != 1 or
            value['company_id'] != s.company_row['id'] or value['owner_type'] not in OWNER_TYPES or
            type(value['baseline_audit_seq']) is not int or not 0 <= value['baseline_audit_seq'] <= watermark(s) or
            type(value['owner_version']) is not int or value['owner_version'] < 1 or
            not all(isinstance(value[k], str) for k in ('owner_id', 'issued_at', 'graph_digest'))):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'invalid_guard'}) from None


def _commercial_snapshot(s, header, event_seq, cache):
    """Only immutable revisions proven to belong to this event's owner qualify."""
    key = (header['id'], header['type'], header['current_revision_id'])
    if key not in cache:
        revision = s.company.conn.execute(sa.select(c.transaction_revisions, c.audit_events.c.seq.label('recorded_seq'))
            .join(c.audit_events, c.audit_events.c.id == c.transaction_revisions.c.audit_event_id).where(
                c.transaction_revisions.c.id == key[2], c.transaction_revisions.c.transaction_id == key[0])).mappings().one_or_none()
        if revision is None:
            raise ValueError('unowned commercial revision')
        if header['type'] in SETTLEABLE_RECEIVABLE_TYPES:
            semantic = sales._saved_semantic(s, revision)
        elif header['type'] == 'payment':
            profile = s.company.conn.execute(sa.select(c.payment_profiles).where(
                c.payment_profiles.c.revision_id == key[2])).mappings().one_or_none()
            if profile is None:
                raise ValueError('missing payment profile')
            from bookflow.company.payment_outputs import PaymentProfileOutput
            semantic = dict(date=revision['date'], number=revision['number'], memo=revision['memo'],
                amount=revision['total_minor_units'], reference=profile['reference'],
                profile=PaymentProfileOutput.model_validate_json(profile['profile_snapshot']).model_dump(mode='json'),
                issuer=json.loads(revision['issuer_snapshot']),
                custom_fields=sales._custom_semantic(json.loads(revision['custom_fields_snapshot'])))
        else:
            raise ValueError('unsupported commercial owner')
        cache[key] = revision['recorded_seq'], semantic
    recorded_seq, semantic = cache[key]
    if recorded_seq > event_seq:
        raise ValueError('commercial revision recorded after header event')
    return semantic


def _event_fields(s, entry, cache):
    before, after = audit.decode_snapshot(entry['before']), audit.decode_snapshot(entry['after'])
    if not isinstance(after, dict) or after.get('id') != entry['record_id'] or after.get('version') != entry['version_after']:
        raise ValueError('unproven after header')
    if before is None and entry['version_before'] is None:
        _commercial_snapshot(s, after, entry['seq'], cache)
        return ['created']
    if (not isinstance(before, dict) or before.get('id') != entry['record_id'] or
        before.get('type') != after.get('type') or before.get('version') != entry['version_before']):
        raise ValueError('unproven before header')
    fields = sales._changes(_commercial_snapshot(s, before, entry['seq'], cache),
                            _commercial_snapshot(s, after, entry['seq'], cache))
    if before['status'] != after['status']:
        fields.append('status')
    return sorted(set(fields))


def compare(s, token, *, owner_type=None, owner_id=None, write=False):
    saved = decode(s, token)
    if ((owner_type is not None and saved['owner_type'] != owner_type) or
        (owner_id is not None and saved['owner_id'] != owner_id)):
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'invalid_guard'})
    current = graph(s, saved['owner_type'], saved['owner_id'], write=write)
    baseline = graph(s, saved['owner_type'], saved['owner_id'], cutoff=saved['baseline_audit_seq'], write=write)
    unknown = list(baseline['unknown'])
    if baseline['digest'] != saved['graph_digest']:
        unknown = sorted(set(unknown) | set(baseline['headers']) | {saved['owner_id']})
    ids = set(current['headers']) | set(baseline['headers']) | set(unknown)
    authorize(s, ids, write=write)
    observed_headers = _headers_at(s, ids, None)
    entries = s.company.conn.execute(sa.select(c.audit_entries, c.audit_events.c.seq,
        c.audit_events.c.at, c.audit_events.c.actor_id, c.audit_events.c.on_behalf_of, c.audit_events.c.interface
    ).join(c.audit_events, c.audit_events.c.id == c.audit_entries.c.event_id).where(
        c.audit_events.c.seq > saved['baseline_audit_seq'], c.audit_entries.c.record_type == 'transaction',
        c.audit_entries.c.record_id.in_(ids)).order_by(c.audit_events.c.seq, c.audit_entries.c.id)).mappings()
    changes, commercial_cache = [], {}
    for row in entries:
        fields = None
        try:
            fields = _event_fields(s, row, commercial_cache)
        except Exception:
            pass
        changes.append(dict(record_id=row['record_id'], event_id=row['event_id'], at=row['at'], actor_id=row['actor_id'],
            on_behalf_of=row['on_behalf_of'], interface=row['interface'], version_before=row['version_before'],
            version_after=row['version_after'], baseline_version=baseline['headers'].get(row['record_id'], {}).get('version'),
            current_version=observed_headers.get(row['record_id'], {}).get('version'), fields=fields, unknown_fields=fields is None))
    # Settlement fields derive from the same actual event's immutable owned
    # evidence, not from a header version bump or the latest writer's identity.
    for change in changes:
        app_rows = effects.rows(s, c.applications, c.applications.c.audit_event_id == change['event_id'])
        allocation_rows = effects.rows(s, c.application_allocations, c.application_allocations.c.audit_event_id == change['event_id'])
        derived = []
        if any(change['record_id'] in (row['paying_transaction_id'], row['paid_transaction_id']) for row in app_rows):
            derived.append('settlement.applied')
        if any(change['record_id'] in (row['source_transaction_id'], row['target_transaction_id']) for row in allocation_rows):
            derived.append('settlement.allocations')
        change['settlement_fields'] = derived
        current_header = observed_headers.get(change['record_id'])
        change['latest_writer_id'] = current_header['updated_by'] if current_header else None
        change['age_seconds'] = max(0, int((clock.now() - clock.parse_iso(change['at'])).total_seconds()))
    unknown = sorted(set(unknown) | {row['record_id'] for row in changes if row['unknown_fields']})
    for identifier, old in baseline['headers'].items():
        latest = observed_headers.get(identifier)
        represented = {row['version_after'] for row in changes if row['record_id'] == identifier}
        expected_count = latest['version'] - old['version'] if latest is not None else -1
        if (expected_count < 0 or len(represented) != expected_count or
            expected_count > 0 and (min(represented) != old['version'] + 1 or max(represented) != latest['version'])):
            unknown.append(identifier)
    unknown = sorted(set(unknown))
    return dict(matches=not unknown and current['digest'] == saved['graph_digest'], unknown_history=bool(unknown),
        unknown_record_ids=unknown, changes=changes, current=current, baseline=baseline, saved=saved)


def validate(s, token, owner_type, owner_id):
    result = compare(s, token, owner_type=owner_type, owner_id=owner_id, write=True)
    if not result['matches']:
        raise BookflowError('E_PREVIEW_STALE', details=dict(reason='settlement_dependencies',
            unknown_history=result['unknown_history'], unknown_record_ids=result['unknown_record_ids'],
            changes=result['changes'][:50], total_count=len(result['changes']),
            settlement_guard=issue(s, owner_type, owner_id),
            review={'command': 'payment settlement changes', 'input': {'guard': token}}))
    return result['current']
