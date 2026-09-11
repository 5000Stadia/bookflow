"""An independent reading of what a bill-payment write is about to store.

The writer builds the aggregate; this checks it against the books' own rules without reusing
the writer's arithmetic -- every total is recomputed from the rows themselves, and the capacity
each bill has left is read again from storage rather than taken from the plan. It runs on the
preview and again inside the writing transaction, so a graph that only became wrong between
the two is still caught. Anything it refuses is ``E_INTERNAL``: a caller cannot cause these.

The one refusal that is not internal is over-settlement, and it has two directions. Two people
paying the same bill at the same moment each see it open, and two people re-pointing the same
freed payment each see it free; in both races the second write has to lose. So the checks that
a payable is never settled past its gross and that a payment's capacity never answers two bills
at once are made here, in the writer's own transaction, against what storage says at that
instant, and both refuse with ``E_APPLICATION_CAPACITY``.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from bookflow.company import ap_settlement, document_effects as effects, schema as c
from bookflow.company.bill_payment_facts import BillPaymentProfile
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid bill payment aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX,
            'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL',
                            message='Invalid bill payment aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    from bookflow.company import bill_payments as payments

    data = plan.data
    if not data.get('changed', True):
        return
    operation = data['operation']
    require(operation in ('pay', 'apply', 'unapply', 'void'), 'wrong operation')
    currency = s.company.conn.execute(c.company_info.select()).mappings().one()['home_currency']
    numbers = set()
    for document in data['documents']:
        _document(s, ctx, payments, data, document, currency, operation)
        numbers.add(document['header']['number'])
    require(len(numbers) == len(data['documents']), 'two payments with the same number')
    _settlement(s, data, operation)
    _source_capacity(s, data)


def _document(s, ctx, payments, data, document, currency, operation):
    header, pending, old = document['header'], document['pending'], document.get('before')
    require(header['type'] == 'bill_payment', 'wrong document type')
    require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value,
            'header writer attribution')
    require(set(pending) >= {table for table, _, _ in payments.TABLE_KINDS}, 'incomplete graph')

    indexed = {}
    for name, _, key in payments.TABLE_KINDS:
        values = pending[name]
        indexed[name] = {row[key]: row for row in values}
        require(len(indexed[name]) == len(values), 'duplicate history identity')
        require(all(row['created_by'] == s.actor.id and row['created_at'] == header['updated_at']
                    and row['created_via'] == header['updated_via'] for row in values),
                'incorrect creation provenance')
    for name in ('transaction_revisions', 'document_line_identities', 'document_lines',
                 'ap_payment_profiles', 'posting_batches', 'posting_lines', 'posting_line_sources',
                 'ap_source_keys', 'ap_source_components'):
        require(all(row['transaction_id'] == header['id'] for row in pending[name]),
                'cross-document history')
    require(all(row['source_transaction_id'] == header['id'] for row in pending['ap_applications']),
            'cross-document settlement')

    if old:
        current = payments.resolve(s, data['input'].payment)
        require(current == old and current['id'] == header['id'] and current['status'] == 'posted',
                'stale or wrong prior payment')
        require(header['version'] == old['version'] + 1, 'wrong payment version')
        require(all(header[key] == old[key] for key in ('created_at', 'created_by', 'created_via')),
                'changed creation provenance')
    else:
        require(operation == 'pay' and header['version'] == 1, 'invalid new payment')
        require(not effects.rows(s, c.transactions, c.transactions.c.id == header['id']),
                'reused document id')

    batches = pending['posting_batches']
    legs, sources = pending['posting_lines'], pending['posting_line_sources']
    require(all(batch['audit_event_id'] == data['event'] for batch in batches), 'wrong posting event')
    require(all(leg['batch_id'] in indexed['posting_batches'] for leg in legs), 'unowned posting line')
    require(all(source['posting_line_id'] in indexed['posting_lines'] for source in sources),
            'unowned attribution')
    for batch in batches:
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
            require(leg['currency'] == currency, 'foreign posting in a domestic payment')
            attributed = sum(amount(source['amount_minor_units'], positive=True)
                             for source in sources if source['posting_line_id'] == leg['id'])
            require(attributed == leg['debit_minor_units'] + leg['credit_minor_units'],
                    'a posting line is not fully attributed to entered lines')
        require(debit == credit and debit > 0, 'a posting batch does not balance')

    if operation == 'void':
        _void(s, data, document, header, pending, batches, legs)
        return
    if operation in ('apply', 'unapply'):
        require(not any(pending[name] for name, _, _ in payments.TABLE_KINDS
                        if name != 'ap_applications'),
                'settling existing capacity writes nothing but edges')
        require(header['status'] == 'posted', 'settling existing capacity cannot void the payment')
        if operation == 'apply':
            _apply(s, document, header, pending, currency)
        return

    require(header['status'] == 'posted', 'a new payment is posted')
    revisions = pending['transaction_revisions']
    require(len(revisions) == 1, 'wrong revision count')
    revision = revisions[0]
    require(header['current_revision_id'] == revision['id'] and revision['audit_event_id'] == data['event'],
            'the header does not point at the new revision')
    require(revision['name_type'] == 'vendor' and revision['currency'] == currency,
            'a payment names its vendor in home currency')
    require(len(batches) == 1 and batches[0]['kind'] == 'original'
            and batches[0]['effective_date'] == revision['date'], 'wrong business batch')

    profiles = pending['ap_payment_profiles']
    require(len(profiles) == 1 and profiles[0]['revision_id'] == revision['id']
            and profiles[0]['type'] == 'bill_payment', 'wrong payment profile')
    profile = profiles[0]
    captured = BillPaymentProfile.model_validate_json(profile['profile_snapshot'])
    require(captured.vendor.id == profile['vendor_id'] == revision['name_id']
            and captured.ap_account.id == profile['ap_account_id']
            and captured.funding_account.id == profile['funding_account_id']
            and captured.payment_method.id == profile['payment_method_id']
            and captured.check_number == profile['check_number'],
            'captured header facts disagree with columns')
    require(captured.ap_account.type == 'accounts_payable', 'a payment does not settle a payable account')
    require(captured.funding_account.type in payments.FUNDING_KIND
            and payments.FUNDING_KIND[captured.funding_account.type] == profile['funding_kind'],
            'the funding account does not match what the payment says funded it')
    require(profile['check_number'] is None or (captured.payment_method.kind == 'check'
                                                and profile['funding_kind'] == 'bank_cash'),
            'a check number on something that is not a check')

    envelopes = pending['document_lines']
    components = pending['ap_source_components']
    applications = pending['ap_applications']
    require(bool(envelopes) and len(envelopes) == len(components) == len(applications),
            'every selected bill needs one envelope, one component and one application')
    require(all(line['revision_id'] == revision['id'] for line in envelopes + components),
            'a line belongs to another revision')
    require(sorted(line['position'] for line in envelopes) == list(range(1, len(envelopes) + 1)),
            'non-contiguous entered lines')
    require(all(line['kind'] == 'bill_payment' and line['account_id'] is None and line['side'] is None
                and line['amount_minor_units'] is None and line['account_snapshot'] is None
                for line in envelopes), 'a payment envelope carries no accounting of its own')
    identities = {line['line_id'] for line in envelopes}
    require(len(identities) == len(envelopes), 'a line identity is used twice')
    require(identities == {row['id'] for row in pending['document_line_identities']},
            'a line identity this document does not own')

    keys = pending['ap_source_keys']
    require(len(keys) == 1 and keys[0]['ordinal'] == 1 and keys[0]['source_type'] == 'bill_payment',
            'a payment carries exactly one source')
    key = keys[0]
    require(key['vendor_id'] == profile['vendor_id'] and key['ap_account_id'] == profile['ap_account_id']
            and key['currency'] == currency, 'the source does not match the payment it belongs to')

    batch = batches[0]
    own = [leg for leg in legs if leg['batch_id'] == batch['id']]
    total = revision['total_minor_units']
    payable = [leg for leg in own if leg['account_id'] == profile['ap_account_id'] and leg['debit_minor_units']]
    funding = [leg for leg in own if leg['account_id'] == profile['funding_account_id'] and leg['credit_minor_units']]
    require(len(own) == 2 and len(payable) == 1 and len(funding) == 1,
            'a bill payment posts one payable debit and one funding credit and nothing else')
    require(payable[0]['debit_minor_units'] == funding[0]['credit_minor_units'] == total == profile['amount_minor_units'],
            'the two legs disagree with what the payment is written for')

    attributions = {source['id']: source for source in sources
                    if source['posting_line_id'] == payable[0]['id']}
    by_envelope = {}
    settled = 0
    for component in components:
        source = attributions.get(component['posting_source_id'])
        require(source is not None and source['document_line_id'] == component['document_line_id']
                and source['amount_minor_units'] == component['amount_minor_units'],
                'a source component does not name its own payable attribution')
        require(component['key_id'] == key['id'] and component['currency'] == currency,
                'a source component belongs elsewhere')
        by_envelope[component['document_line_id']] = component
        settled += amount(component['amount_minor_units'], positive=True)
    require(settled == total, 'the source components do not add up to what the payment is written for')
    require(set(by_envelope) == {line['id'] for line in envelopes},
            'an entered line without its source component')

    for application in applications:
        component = indexed['ap_source_components'].get(application['source_component_id'])
        require(component is not None and application['source_key_id'] == key['id']
                and application['amount_minor_units'] == component['amount_minor_units']
                and application['currency'] == currency
                and application['effective_date'] == revision['date']
                and application['kind'] == 'apply' and application['reverses_application_id'] is None,
                'an application does not match the capacity it consumes')
        _settled_bill(s, application, key, currency)


def _settled_bill(s, application, key, currency):
    """What an application names on the payable side, whichever write made it."""
    bill = effects.rows(s, c.transactions, c.transactions.c.id == application['obligation_transaction_id'])
    require(len(bill) == 1 and bill[0]['type'] == 'bill' and bill[0]['status'] == 'posted',
            'an application names something that is not an open bill')
    obligation = effects.rows(s, c.ap_obligation_keys,
                              c.ap_obligation_keys.c.id == application['obligation_key_id'])
    require(len(obligation) == 1 and obligation[0]['transaction_id'] == bill[0]['id']
            and obligation[0]['vendor_id'] == key['vendor_id']
            and obligation[0]['ap_account_id'] == key['ap_account_id']
            and obligation[0]['currency'] == currency,
            'an application crosses a vendor, a payable account or a currency')


def _apply(s, document, header, pending, currency):
    """Edges hung on capacity that already exists, read back from storage rather than the plan."""
    revision = document['revision']
    key = ap_settlement.source_key_row(s, header['id'])
    require(key is not None and key['currency'] == currency,
            'an apply against a payment that carries no settlement source')
    components = {row['id']: row for row in effects.rows(
        s, c.ap_source_components, c.ap_source_components.c.transaction_id == header['id'])}
    applications = pending['ap_applications']
    require(bool(applications), 'an apply that settles nothing')
    for application in applications:
        component = components.get(application['source_component_id'])
        require(component is not None and application['source_key_id'] == key['id']
                and application['currency'] == component['currency'] == currency
                and application['kind'] == 'apply' and application['reverses_application_id'] is None
                and amount(application['amount_minor_units'], positive=True) <= component['amount_minor_units'],
                'an application does not match the capacity it consumes')
        require(application['effective_date'] >= revision['date'],
                'an application cannot settle before the money left')
        _settled_bill(s, application, key, currency)


def _void(s, data, document, header, pending, batches, legs):
    revision = document['revision']
    require(header['status'] == 'voided' and (header['void_reason'] or '').strip(), 'an incomplete void')
    require(len(batches) == 1 and batches[0]['kind'] == 'reversal'
            and header['void_posting_batch_id'] == batches[0]['id'], 'wrong void batch')
    require(not pending['transaction_revisions'] and not pending['ap_payment_profiles']
            and not pending['ap_applications'] and not pending['ap_source_components'],
            'a void writes no new revision and settles nothing')
    replaced = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                            c.posting_batches.c.kind != 'reversal')
    require(len(replaced) == 1 and batches[0]['reverses_batch_id'] == replaced[0]['id']
            and batches[0]['effective_date'] == replaced[0]['effective_date'],
            'the reversal does not invert the current business batch at its own date')
    original = effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == replaced[0]['id'],
                            order=c.posting_lines.c.line_no)
    mirrored = sorted(legs, key=lambda leg: leg['line_no'])
    require(len(original) == len(mirrored), 'the reversal has a different number of lines')
    for before, after in zip(original, mirrored):
        require(after['reversed_line_id'] == before['id']
                and after['account_id'] == before['account_id']
                and after['debit_minor_units'] == before['credit_minor_units']
                and after['credit_minor_units'] == before['debit_minor_units'],
                'the reversal is not an exact inverse')
    require(not ap_settlement.active_applications(s, header['id']),
            'a voided payment still settles a bill')


def _settlement(s, data, operation):
    pending = [row for document in data['documents'] for row in document['pending']['ap_applications']]
    settlement_fence(s, pending, operation=operation,
                     source_transaction_id=data['documents'][0]['header']['id'])


def settlement_fence(s, pending, *, operation=None, source_transaction_id=None):
    """No payable ends up settled past its gross, and no inverse takes back what is not there.

    Read fresh, against storage, plus what this write adds: the arithmetic that decides whether
    a concurrent settlement already took the money. Both kinds of paying source come through
    here -- a bill payment and a vendor credit answer the same payable, so the rule that a bill
    is never settled past its gross has to be one rule reading one edge, not two that agree.
    """
    if not pending:
        return
    obligations = sorted({row['obligation_key_id'] for row in pending})
    settled = ap_settlement.applied_totals(s, obligations)
    for row in pending:
        sign = 1 if row['kind'] == 'apply' else -1
        settled[row['obligation_key_id']] = settled[row['obligation_key_id']] + sign * row['amount_minor_units']
    for identifier in obligations:
        key = effects.rows(s, c.ap_obligation_keys, c.ap_obligation_keys.c.id == identifier)[0]
        header = effects.rows(s, c.transactions, c.transactions.c.id == key['transaction_id'])[0]
        revision = effects.rows(s, c.transaction_revisions,
                                c.transaction_revisions.c.id == header['current_revision_id'])[0]
        gross = revision['total_minor_units']
        total = settled[identifier]
        require(total >= 0, 'a bill is settled by less than nothing')
        if total > gross:
            raise BookflowError('E_APPLICATION_CAPACITY', details={
                'bill_id': header['id'], 'bill_number': header['number'],
                'requested_minor_units': total, 'available_minor_units': gross,
                'next': 'Someone else settled this bill first; re-read what is open and pay that.'})
    if operation == 'unapply':
        originals = {row['reverses_application_id'] for row in pending}
        require(len(originals) == len(pending), 'two inverses of one application')
        active = {row['id'] for row in ap_settlement.active_applications(s, source_transaction_id)}
        require(originals <= active, 'an inverse of something already unapplied')


def _source_capacity(s, data):
    rows = [row for document in data['documents'] for row in document['pending']['ap_applications']]
    source_capacity_fence(s, rows, [row for document in data['documents']
                                    for row in document['pending']['ap_source_components']])


def source_capacity_fence(s, rows, minted=(), *, noun='payment'):
    """No part of a source's capacity answers two bills at once, read fresh against storage.

    The mirror of ``settlement_fence``: that one refuses settling a payable past its gross,
    this one refuses spending a source past what it carries. Both have to read storage inside
    the writer's transaction, because the concurrent write that took the money is only visible
    there -- two people re-pointing the same freed check at different bills is the same race as
    two people paying the same bill, and a vendor credit races exactly the same way.
    """
    if not rows:
        return
    for identifier in sorted({row['source_transaction_id'] for row in rows}):
        components = {row['id']: row for row in effects.rows(
            s, c.ap_source_components, c.ap_source_components.c.transaction_id == identifier)}
        components.update({row['id']: row for row in minted
                           if row['transaction_id'] == identifier})
        held = dict.fromkeys(components, 0)
        for edge in ap_settlement.applications(s, source_transaction_id=identifier):
            if edge['active']:
                held[edge['source_component_id']] = held.get(edge['source_component_id'], 0) + edge['amount_minor_units']
        for row in rows:
            if row['source_transaction_id'] == identifier:
                sign = 1 if row['kind'] == 'apply' else -1
                held[row['source_component_id']] = held.get(row['source_component_id'], 0) + sign * row['amount_minor_units']
        for component_id, units in held.items():
            component = components.get(component_id)
            require(component is not None, f'an edge against capacity this {noun} does not own')
            require(units >= 0, f'a {noun} has been given back more than it carried')
            if units > component['amount_minor_units']:
                header = effects.rows(s, c.transactions, c.transactions.c.id == identifier)[0]
                raise BookflowError('E_APPLICATION_CAPACITY', details={
                    f'{noun}_id': identifier, f'{noun}_number': header['number'],
                    'requested_minor_units': units,
                    'available_minor_units': component['amount_minor_units'],
                    'next': f'Someone else attached this {noun} first; re-read what it has free '
                            'and apply that.'})
