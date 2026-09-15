"""An independent reading of what a vendor-credit write is about to store.

The writer builds the aggregate; this checks it against the books' own rules without reusing
the writer's arithmetic -- every total is recomputed from the rows themselves, and what the
credit still has free is read again from storage rather than taken from the plan. It runs on
the preview and again inside the writing transaction, so a graph that only became wrong between
the two is still caught. Anything it refuses is ``E_INTERNAL``: a caller cannot cause these.

The one refusal that is not internal is over-settlement, and it is not this module's own: the
two concurrency fences live in ``bill_payment_validation`` and are called from here, because a
bill settled by a credit and a bill settled by a check are the same payable being settled past
the same gross. Two rules that agree are two rules that can stop agreeing.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from bookflow.company import bill_payment_validation as settlement
from bookflow.company import document_effects as effects, schema as c
from bookflow.company.vendor_credit_facts import BillExpenseProfile, VendorCreditProfile
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid vendor credit aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX,
            'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL',
                            message='Invalid vendor credit aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    from bookflow.company import vendor_credits

    data = plan.data
    if not data.get('changed', True):
        return
    operation = data['operation']
    require(operation in ('post', 'update', 'void', 'apply', 'unapply'), 'wrong operation')
    header, pending, old = data['header'], data['pending'], data['before']
    require(header['type'] == 'vendor_credit', 'wrong document type')
    require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value,
            'header writer attribution')
    currency = s.company.conn.execute(c.company_info.select()).mappings().one()['home_currency']

    tables = vendor_credits.table_kinds_for(operation)
    require(set(pending) == {table for table, _, _ in tables}, 'incomplete graph')
    indexed = {}
    for name, _, key in tables:
        values = pending[name]
        indexed[name] = {row[key]: row for row in values}
        require(len(indexed[name]) == len(values), 'duplicate history identity')
        require(all(row['created_by'] == s.actor.id and row['created_at'] == header['updated_at']
                    and row['created_via'] == header['updated_via'] for row in values),
                'incorrect creation provenance')

    if old:
        current = vendor_credits.resolve(s, data['input'].credit)
        require(current == old and current['id'] == header['id'], 'stale or wrong prior credit')
        require(header['version'] == old['version'] + 1, 'wrong credit version')
        require(all(header[key] == old[key] for key in ('created_at', 'created_by', 'created_via')),
                'changed creation provenance')
    else:
        require(operation == 'post' and header['version'] == 1, 'invalid new credit')
        require(not effects.rows(s, c.transactions, c.transactions.c.id == header['id']),
                'reused document id')

    if operation in ('apply', 'unapply'):
        _settling(s, data, header, pending, currency, operation)
        return

    # A settlement edge names the document on the paying side rather than owning a
    # `transaction_id` of its own; every other row this write mints belongs to the credit.
    require(all(row['transaction_id'] == header['id']
                for name, _, _ in tables if name != 'ap_applications' for row in pending[name]),
            'cross-document history')
    require(all(row['source_transaction_id'] == header['id']
                for row in pending.get('ap_applications', [])), 'cross-document settlement')
    _accounting(s, data, header, pending, indexed, currency, operation)


def _batches(pending, indexed, currency, noun):
    """Every posting batch this write mints, balanced and fully attributed to entered lines."""
    legs, sources = pending['posting_lines'], pending['posting_line_sources']
    require(all(leg['batch_id'] in indexed['posting_batches'] for leg in legs), 'unowned posting line')
    require(all(source['posting_line_id'] in indexed['posting_lines'] for source in sources),
            'unowned attribution')
    for batch in pending['posting_batches']:
        own = [leg for leg in legs if leg['batch_id'] == batch['id']]
        require(bool(own), 'empty batch')
        require(sorted(leg['line_no'] for leg in own) == list(range(1, len(own) + 1)),
                'non-contiguous batch lines')
        debit = credit = 0
        for leg in own:
            debit += amount(leg['debit_minor_units'])
            credit += amount(leg['credit_minor_units'])
            require(bool(leg['debit_minor_units']) != bool(leg['credit_minor_units']),
                    'a posting must have exactly one positive side')
            require(leg['currency'] == currency, f'foreign posting in a domestic {noun}')
            attributed = sum(amount(source['amount_minor_units'], positive=True)
                             for source in sources if source['posting_line_id'] == leg['id'])
            require(attributed == leg['debit_minor_units'] + leg['credit_minor_units'],
                    'a posting line is not fully attributed to entered lines')
        require(debit == credit and debit > 0, 'a posting batch does not balance')


def _exact_inverse(s, replaced, mirrored):
    """The legs offered back against the batch they undo, cell for cell.

    A void and a correction both invert the current business batch; reading it once is what
    stops the two from disagreeing about what an exact inverse is.
    """
    original = effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == replaced['id'],
                            order=c.posting_lines.c.line_no)
    mirrored = sorted(mirrored, key=lambda leg: leg['line_no'])
    require(len(original) == len(mirrored), 'the reversal has a different number of lines')
    for before, after in zip(original, mirrored):
        require(after['reversed_line_id'] == before['id']
                and after['account_id'] == before['account_id']
                and after['debit_minor_units'] == before['credit_minor_units']
                and after['credit_minor_units'] == before['debit_minor_units'],
                'the reversal is not an exact inverse')


def _corrected_settlements(s, header, pending, revision, key, currency):
    """A correction leaves every bill owing exactly what it owed, out of the new capacity.

    The superseded revision's components are retired with it, so every standing edge is
    released and the same bill settled again on the same date out of the corrected revision.
    Read here from storage rather than from the writer's arithmetic: what is released has to
    be what is actually standing, and what is retaken has to add up, per bill and per date, to
    exactly what it replaced.
    """
    from bookflow.company import ap_settlement, bill_payment_validation as settlement

    edges = pending['ap_applications']
    standing = {row['id']: row for row in ap_settlement.active_applications(s, header['id'])}
    releases = [row for row in edges if row['kind'] == 'unapply']
    retakes = [row for row in edges if row['kind'] == 'apply']
    require(len(releases) == len(standing) == len({row['reverses_application_id'] for row in releases}),
            'a correction does not release every settlement standing on the credit')
    mirrored = ('source_key_id', 'source_component_id', 'obligation_transaction_id',
                'obligation_key_id', 'amount_minor_units', 'currency', 'effective_date')
    for row in releases:
        original = standing.get(row['reverses_application_id'])
        require(original is not None and all(row[name] == original[name] for name in mirrored),
                'a release does not exactly reverse the settlement it takes back')
    components = {row['id']: row for row in pending['ap_source_components']}
    drawn = {}
    for row in retakes:
        component = components.get(row['source_component_id'])
        require(component is not None and row['source_key_id'] == key['id']
                and row['currency'] == component['currency'] == currency
                and row['reverses_application_id'] is None
                and amount(row['amount_minor_units'], positive=True) <= component['amount_minor_units'],
                'a retaken settlement does not come out of the corrected credit')
        require(row['effective_date'] >= revision['date'],
                'a credit cannot settle a bill before the credit existed')
        drawn[component['id']] = drawn.get(component['id'], 0) + row['amount_minor_units']
        settlement._settled_bill(s, row, key, currency)
    for identifier, units in drawn.items():
        require(units <= components[identifier]['amount_minor_units'],
                'a corrected component is settled past its own capacity')

    def owed(rows):
        totals = {}
        for row in rows:
            name = (row['obligation_transaction_id'], row['obligation_key_id'], row['effective_date'])
            totals[name] = totals.get(name, 0) + row['amount_minor_units']
        return totals

    require(owed(retakes) == owed(standing.values()),
            'a correction changes what a bill owes')
    # The same two concurrency fences every other settlement write runs, because a bill
    # settled past its gross and a credit spent past its capacity are one rule each, read
    # fresh against storage, and not a second pair that happens to agree.
    settlement.settlement_fence(s, edges, source_transaction_id=header['id'])
    settlement.source_capacity_fence(s, edges, pending['ap_source_components'], noun='credit')


def _accounting(s, data, header, pending, indexed, currency, operation):
    from bookflow.company import ap_settlement, journals

    batches = pending['posting_batches']
    require(all(batch['audit_event_id'] == data['event'] for batch in batches), 'wrong posting event')
    journals.open_dates(s, [batch['effective_date'] for batch in batches])
    _batches(pending, indexed, currency, 'vendor credit')

    if operation == 'void':
        revision = data['old_revision']
        require(header['status'] == 'voided' and (header['void_reason'] or '').strip(), 'an incomplete void')
        require(len(batches) == 1 and batches[0]['kind'] == 'reversal'
                and header['void_posting_batch_id'] == batches[0]['id'], 'wrong void batch')
        require(not pending['transaction_revisions'] and not pending['vendor_credit_profiles']
                and not pending['ap_source_keys'] and not pending['ap_source_components'],
                'a void writes no new revision and mints no new capacity')
        replaced = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                c.posting_batches.c.kind != 'reversal')
        require(len(replaced) == 1 and batches[0]['reverses_batch_id'] == replaced[0]['id']
                and batches[0]['effective_date'] == replaced[0]['effective_date'],
                'the reversal does not invert the current business batch at its own date')
        _exact_inverse(s, replaced[0], pending['posting_lines'])
        require(not ap_settlement.active_applications(s, header['id']),
                'a voided credit still settles a bill')
        return

    from bookflow.company import bills

    require(header['status'] == 'posted', 'a credit that is written is posted')
    revisions = pending['transaction_revisions']
    require(len(revisions) == 1, 'wrong revision count')
    revision = revisions[0]
    require(header['current_revision_id'] == revision['id'] and revision['audit_event_id'] == data['event'],
            'the header does not point at the new revision')
    require(revision['name_type'] == 'vendor' and revision['currency'] == currency,
            'a vendor credit names its vendor in home currency')
    if operation == 'update':
        old_revision = data['old_revision']
        require(revision['revision_number'] == old_revision['revision_number'] + 1
                and revision['supersedes_revision_id'] == old_revision['id'],
                'a correction does not succeed the revision it replaces')
        replaced = effects.rows(s, c.posting_batches,
                                c.posting_batches.c.revision_id == old_revision['id'],
                                c.posting_batches.c.kind != 'reversal')
        reversal = [row for row in batches if row['kind'] == 'reversal']
        replacement = [row for row in batches if row['kind'] == 'replacement']
        require(len(replaced) == 1 and len(batches) == 2 and len(reversal) == 1 and len(replacement) == 1,
                'a correction reverses exactly one batch and posts exactly one replacement')
        require(reversal[0]['revision_id'] == old_revision['id']
                and reversal[0]['reverses_batch_id'] == replaced[0]['id']
                and reversal[0]['effective_date'] == replaced[0]['effective_date'],
                'the reversal does not invert the superseded batch at its own date')
        require(replacement[0]['revision_id'] == revision['id']
                and replacement[0]['replaces_batch_id'] == replaced[0]['id']
                and replacement[0]['reverses_batch_id'] is None
                and replacement[0]['effective_date'] == revision['date'],
                'the replacement does not stand in for the batch it replaces')
        _exact_inverse(s, replaced[0], [leg for leg in pending['posting_lines']
                                        if leg['batch_id'] == reversal[0]['id']])
        batches = replacement
    else:
        require(revision['revision_number'] == 1 and revision['supersedes_revision_id'] is None,
                'a new credit starts at its first revision')
        require(len(batches) == 1 and batches[0]['kind'] == 'original'
                and batches[0]['effective_date'] == revision['date'], 'wrong business batch')

    profiles = pending['vendor_credit_profiles']
    require(len(profiles) == 1 and profiles[0]['revision_id'] == revision['id']
            and profiles[0]['type'] == 'vendor_credit', 'wrong vendor credit profile')
    profile = profiles[0]
    captured = VendorCreditProfile.model_validate_json(profile['profile_snapshot'])
    require(captured.vendor.id == profile['vendor_id'] == revision['name_id']
            and captured.ap_account.id == profile['ap_account_id'],
            'captured header facts disagree with columns')
    require(captured.ap_account.type == 'accounts_payable',
            'a vendor credit is not credited against a payable account')
    require((profile['supplier_reference'] is None) == (profile['supplier_reference_key'] is None),
            'a supplier reference without its comparison key')

    envelopes = pending['document_lines']
    expenses = pending['vendor_credit_expense_lines']
    require(bool(envelopes) and len(envelopes) == len(expenses),
            'every credited line needs exactly one profile')
    require(all(line['revision_id'] == revision['id'] for line in envelopes + expenses),
            'a line belongs to another revision')
    require(sorted(line['position'] for line in envelopes) == list(range(1, len(envelopes) + 1)),
            'non-contiguous entered lines')
    require(all(line['kind'] == 'purchase' and line['account_id'] is None and line['side'] is None
                and line['amount_minor_units'] is None and line['account_snapshot'] is None
                for line in envelopes), 'a purchase envelope carries no accounting of its own')
    identities = {line['line_id'] for line in envelopes}
    require(len(identities) == len(envelopes), 'a line identity is used twice')
    minted = {row['id'] for row in pending['document_line_identities']}
    require(minted <= identities, 'a minted line identity no row on this revision names')
    kept = identities - minted
    if operation == 'update':
        # A row carried across a correction keeps the identity the superseded revision gave
        # it, and only that one: an identity retired by an earlier correction cannot return,
        # because the row a reader follows through the history would then be two rows.
        prior = {line['line_id'] for line in effects.rows(
            s, c.document_lines, c.document_lines.c.revision_id == data['old_revision']['id'])}
        require(kept <= prior, 'a retired line identity cannot return')
    else:
        require(not kept, 'a new credit mints every line identity it uses')

    by_envelope = {line['document_line_id']: line for line in expenses}
    total = 0
    for envelope in envelopes:
        line = by_envelope.get(envelope['id'])
        require(line is not None, 'a credited line without its expense profile')
        facts = BillExpenseProfile.model_validate_json(line['line_snapshot'])
        require(facts.account.id == line['account_id'], 'captured line facts disagree with columns')
        require(facts.account.type in bills.EXPENSE_ACCOUNTS,
                'a credited line names an ineligible account')
        require(facts.billable is False, 'a vendor credit line is never billable')
        require(line['customer_id'] == (facts.customer.id if facts.customer else None),
                'the captured job disagrees with the column')
        total += amount(line['amount_minor_units'], positive=True)
    require(total == profile['expense_total_minor_units'] == revision['total_minor_units'],
            'the credited lines do not add up to the credit')

    batch = batches[0]
    legs, sources = pending['posting_lines'], pending['posting_line_sources']
    own = [leg for leg in legs if leg['batch_id'] == batch['id']]
    payable = [leg for leg in own if leg['account_id'] == profile['ap_account_id'] and leg['debit_minor_units']]
    require(len(payable) == 1 and payable[0]['debit_minor_units'] == total,
            'Accounts Payable is not debited exactly once for the whole credit')
    credits = {}
    for leg in own:
        if leg is payable[0]:
            continue
        require(leg['credit_minor_units'] > 0,
                'a vendor credit posts nothing but expense credits and one payable debit')
        credits[leg['id']] = leg
    require(len(credits) == len(envelopes), 'one credited account per entered line')

    attributed = {}
    for source in sources:
        if source['posting_line_id'] == payable[0]['id']:
            attributed[source['document_line_id']] = attributed.get(source['document_line_id'], 0) + source['amount_minor_units']
    require(attributed == {envelope['id']: by_envelope[envelope['id']]['amount_minor_units']
                           for envelope in envelopes},
            'the payable debit is not attributed line by line')

    keys = pending['ap_source_keys']
    if operation == 'update':
        # The source is minted once: a correction hangs new capacity off the one the document
        # has always carried, so the vendor, the payable and the currency an application is
        # checked against cannot move underneath the bills already settled.
        require(not keys, 'a correction mints a second settlement source')
        key = ap_settlement.source_key_row(s, header['id'])
        require(key is not None and key['source_type'] == 'vendor_credit',
                'a correction of a credit that carries no settlement source')
    else:
        require(len(keys) == 1 and keys[0]['ordinal'] == 1 and keys[0]['source_type'] == 'vendor_credit',
                'a vendor credit carries exactly one settlement source')
        key = keys[0]
        require(key['audit_event_id'] == data['event'], 'the source was minted by another event')
    require(key['vendor_id'] == profile['vendor_id'] and key['ap_account_id'] == profile['ap_account_id']
            and key['currency'] == currency,
            'the source does not match the credit it belongs to')
    # A credit is a source and never a payable: an obligation key here would put it on a report
    # called unpaid bills, at a negative balance, as a document nobody owes.
    require(not effects.rows(s, c.ap_obligation_keys,
                             c.ap_obligation_keys.c.transaction_id == header['id']),
            'a vendor credit owes no payable of its own')

    attributions = {source['id']: source for source in sources
                    if source['posting_line_id'] == payable[0]['id']}
    components = pending['ap_source_components']
    require(len(components) == len(envelopes), 'one source component per credited line')
    carried = 0
    seen = set()
    for component in components:
        attribution = attributions.get(component['posting_source_id'])
        require(attribution is not None
                and attribution['document_line_id'] == component['document_line_id']
                and attribution['amount_minor_units'] == component['amount_minor_units'],
                'a source component does not name its own payable attribution')
        require(component['key_id'] == key['id'] and component['currency'] == currency
                and component['revision_id'] == revision['id'],
                'a source component belongs elsewhere')
        seen.add(component['document_line_id'])
        carried += amount(component['amount_minor_units'], positive=True)
    require(carried == total, 'the source components do not add up to what the credit is worth')
    require(seen == {line['id'] for line in envelopes}, 'a credited line without its source component')

    if operation == 'update':
        _corrected_settlements(s, header, pending, revision, key, currency)
    else:
        require(not pending.get('ap_applications'), 'a new credit settles nothing')


def _settling(s, data, header, pending, currency, operation):
    """Edges hung on capacity that already exists, read back from storage, not from the plan."""
    from bookflow.company import ap_settlement

    revision = data['old_revision']
    require(header['status'] == 'posted', 'settling existing capacity cannot void the credit')
    applications = pending['ap_applications']
    require(bool(applications), 'a settlement write that settles nothing')
    require(all(row['source_transaction_id'] == header['id'] for row in applications),
            'cross-document settlement')
    key = ap_settlement.source_key_row(s, header['id'])
    require(key is not None and key['currency'] == currency
            and key['source_type'] == 'vendor_credit',
            'a settlement against a credit that carries no source')
    components = {row['id']: row for row in effects.rows(
        s, c.ap_source_components, c.ap_source_components.c.transaction_id == header['id'])}
    for application in applications:
        component = components.get(application['source_component_id'])
        require(component is not None and application['source_key_id'] == key['id']
                and application['currency'] == component['currency'] == currency
                and amount(application['amount_minor_units'], positive=True) <= component['amount_minor_units'],
                'an application does not match the capacity it consumes')
        if operation == 'apply':
            require(application['kind'] == 'apply' and application['reverses_application_id'] is None,
                    'an apply that is not an apply')
            require(application['effective_date'] >= revision['date'],
                    'a credit cannot settle a bill before the credit existed')
        else:
            require(application['kind'] == 'unapply' and application['reverses_application_id'],
                    'an unapply that takes nothing back')
        settlement._settled_bill(s, application, key, currency)
    settlement.settlement_fence(s, applications, operation=operation,
                               source_transaction_id=header['id'])
    settlement.source_capacity_fence(s, applications, noun='credit')
