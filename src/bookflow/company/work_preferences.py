"""Current company work policy and authorized per-setting provenance."""
from datetime import datetime, timezone
import sqlalchemy as sa
from pydantic import StrictBool
from bookflow.company import schema as c
from bookflow.company.sales_models import StrictModel
from bookflow.core.audit import decode_snapshot
from bookflow.core.errors import BookflowError

FIELDS = ('estimates_enabled', 'progress_billing_enabled', 'close_estimates_after_billing')


class WorkBillingPreferences(StrictModel):
    estimates_enabled: StrictBool
    progress_billing_enabled: StrictBool
    close_estimates_after_billing: StrictBool
    auto_close_effective: StrictBool


def preferences(s):
    row = s.company.conn.execute(sa.select(c.company_info)).mappings().one()
    return WorkBillingPreferences(**{f: row[f] for f in FIELDS},
        auto_close_effective=not row['progress_billing_enabled'] and row['close_estimates_after_billing'])


def financial_projection(s, kind):
    policy = preferences(s)
    return dict(progress_billing_enabled=policy.progress_billing_enabled,
                auto_close_effective=policy.auto_close_effective and kind == 'estimate')


def financial_fields(s, kind):
    return ['progress_billing_enabled'] + (['close_estimates_after_billing']
        if kind == 'estimate' and not preferences(s).progress_billing_enabled else [])


def changes(s, fields):
    """Latest actual edits, not a diff from an opaque preview token."""
    info = s.company.conn.execute(sa.select(c.company_info)).mappings().one()
    e, a = c.audit_events, c.audit_entries
    rows = s.company.conn.execute(sa.select(e, a.c.after, a.c.action).join(a, a.c.event_id == e.c.id)
        .where(a.c.record_type == 'company_info', a.c.record_id == info['id'])
        .order_by(e.c.seq)).mappings()
    previous, latest = dict(zip(FIELDS, (True, True, False))), {}
    for row in rows:
        snapshot = decode_snapshot(row['after']) or {}
        for field in fields:
            if field in snapshot and (row['action'] == 'create' or snapshot[field] != previous[field]):
                latest[field] = row
                previous[field] = snapshot[field]
    now = datetime.now(timezone.utc)
    result = []
    for field in fields:
        row = latest.get(field)
        at = row['at'] if row else info['created_at']
        result.append(dict(field=field, company_id=info['id'],
            audit_event_id=row['id'] if row else None,
            updated_by=row['actor_id'] if row else info['created_by'],
            updated_via=row['interface'] if row else info['created_via'],
            on_behalf_of=row['on_behalf_of'] if row else None,
            seconds_since_update=max(0, int((now-datetime.fromisoformat(at)).total_seconds()))))
    return result


def disabled(s, feature, fields, **details):
    return disabled_error(feature, changes(s, fields), **details)


def disabled_error(feature, preference_changes, **details):
    """Shared error projection for an authorized command or blocked entry page."""
    return BookflowError('E_FEATURE_DISABLED', details=dict(feature=feature,
        setting=feature + '_enabled', enable_command='company update',
        preference_changes=preference_changes, **details))


def require_estimates(s):
    if not preferences(s).estimates_enabled:
        raise disabled(s, 'estimates', ['estimates_enabled'])


def recovery(s, root, facts, currency):
    from bookflow.company import billing_allocations as alloc, billing_math as math
    from bookflow.core.money import Money
    needed = facts.billable and sum(1 for _ in alloc.free_spans(s, root, facts)) > 200
    net = math.recommended_net(alloc.free_spans(s, root, facts), denominator=math.denominator(
        facts.quantity_microunits, facts.net_minor_units), source_net=facts.net_minor_units) if needed else 0
    return bool(needed), Money(net, currency).to_dict() if net > 0 else None


def check_selection(s, inp, source, rev, lines, identities):
    if preferences(s).progress_billing_enabled:
        return
    fields = financial_fields(s, source['kind'])
    if inp.percent is not None or any(v.model_fields_set != {'line_id', 'net_amount'} for v in inp.selections or []):
        raise disabled(s, 'progress_billing', fields)
    if not inp.selections:
        return
    from bookflow.company import work, billing_queries as query
    from bookflow.company.sales_models import money
    by_id = {line['line_id']: line for line in lines}
    roots = [(r['root_document_id'], r['root_line_id']) for r in identities.values()]
    for entry in inp.selections:
        line = by_id.get(entry.line_id)
        needed, recommendation = False, None
        if line:
            identity = identities[entry.line_id]
            needed, recommendation = recovery(s, (identity['root_document_id'], identity['root_line_id']),
                                               work.line_facts(line), rev['currency'])
        if not (needed and recommendation and money(entry.net_amount, rev['currency']).minor_units == recommendation['minor_units']):
            details = dict(line_id=entry.line_id, recommended_net_amount=recommendation,
                problem='Disabled progress billing permits only net-only recovery of the exact current recommendation on a root with more than200 free spans.')
            if inp.expected_facts_fingerprint:
                raise BookflowError('E_PREVIEW_STALE', details=dict(**details,
                    consumption_changes=query.latest_consumption_changes(s, roots),
                    preference_changes=changes(s, fields)))
            raise disabled(s, 'progress_billing', fields, **details)


def closes(s, source, rev, selected_net):
    from bookflow.company import work, billing_queries as query, billing_allocations as alloc
    if source['kind'] != 'estimate' or not source['active'] or source['status'] != 'accepted' or not preferences(s).auto_close_effective:
        return False
    identities = query.root_identities(s, source)
    remaining = 0
    for line in work.saved_lines(s, rev):
        facts = work.line_facts(line)
        if facts.billable:
            identity = identities[line['line_id']]
            remaining += alloc.remaining(s, (identity['root_document_id'], identity['root_line_id']), facts)[1]
    return remaining > 0 and selected_net == remaining
