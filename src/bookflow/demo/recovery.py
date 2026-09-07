"""Resolve seed-only complete recovery hashes from actual captured shared revisions."""
from bookflow.company.payment_queries import digest


def resolve_intent(entry, raw_input, captures, resolve_references):
    descriptor=entry.get('recovery_intent')
    if descriptor is None:
        return raw_input
    if entry['command']!='payment recovery begin' or set(descriptor)!={'anchor_revision','entries'} or 'intent_hash' in raw_input:
        raise ValueError('recovery_intent is only a complete begin hash declaration')
    values=resolve_references(descriptor,captures)
    manifest=dict(domain='bookflow.payment.recovery.intent',format=1,selection=raw_input['selection'],
        local_baseline_revision=raw_input['local_baseline_revision'],anchor_revision=values['anchor_revision'],
        attempt_generation=raw_input['attempt_generation'],header_intent=raw_input['header_intent'],entries=values['entries'])
    if len(values['entries'])!=raw_input['declared_entry_count']:
        raise ValueError('seed recovery declaration must include the entire attempt')
    return dict(raw_input,intent_hash=digest(manifest))
