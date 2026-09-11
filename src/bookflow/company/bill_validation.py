"""An independent reading of what a bill write is about to store.

The writer builds the aggregate; this checks it against the books' own rules without reusing
the writer's arithmetic -- every total is recomputed from the rows themselves. It runs twice,
once on the preview and once inside the writing transaction, so a graph that only became wrong
between the two is still caught. Anything it refuses is ``E_INTERNAL``: a caller cannot cause
these, and if one fires it is this module's own fault, not the person's.

Both line grids are read the same way. Every ``document_lines`` envelope must be owned by
exactly one profile row in exactly one of the two tables, each family must add up to the total
the header carries for it, and the two totals together must be the revision's. What an item
line adds beyond that is in ``_item``.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from bookflow.company import document_effects as effects, schema as c
from bookflow.company.bill_facts import BillProfile
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid bill aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX,
            'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL', message='Invalid bill aggregate: malformed captured facts') from exc


def _item(line, facts):
    """What an item line stores beyond an expense line, checked against its own captured facts.

    The account is not re-read from the item: what the bill posted is what it captured, and a
    later repointing of the item must not be able to make a stored revision look wrong. What is
    checked is that the columns and the snapshot say the same thing, that the family is one a
    bill may receive, and that a derived amount really is quantity times the unit cost it
    names -- the one arithmetic step this module recomputes rather than trusts.
    """
    from bookflow.company import bills

    require(facts.item.id == line['item_id'], 'the captured item disagrees with the column')
    require(facts.item_type in bills.PURCHASABLE_ITEM_TYPES,
            'an item line names a family a bill cannot receive')
    require(facts.quantity_microunits == line['quantity_microunits'],
            'the captured quantity disagrees with the column')
    require(facts.unit_cost_minor_units == line['unit_cost_minor_units'],
            'the captured unit cost disagrees with the column')
    require(type(line['quantity_microunits']) is int and 0 < line['quantity_microunits'] <= INT64_MAX,
            'an item line has no positive quantity')
    if facts.amount_basis == 'unit_cost':
        require(line['unit_cost_minor_units'] is not None, 'a derived amount names no unit cost')
        amount(line['unit_cost_minor_units'])
        require(bills.extension(line['quantity_microunits'], line['unit_cost_minor_units'])
                == line['amount_minor_units'],
                'a derived amount is not the quantity times the unit cost')
    else:
        require(line['unit_cost_minor_units'] is None,
                'an entered amount carries a unit cost it was not derived from')


def _validate(plan, s, ctx):
    from bookflow.company import bills

    data = plan.data
    if not data['changed']:
        return
    header, pending, old = data['header'], data['pending'], data['before']
    operation = data['operation']
    require(operation in ('post', 'update', 'void'), 'wrong operation')
    require(header['type'] == 'bill', 'wrong document type')
    require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value,
            'header writer attribution')
    require(set(pending) == {table for table, _, _ in bills.TABLE_KINDS}, 'incomplete graph')

    indexed = {}
    for name, _, key in bills.TABLE_KINDS:
        values = pending[name]
        indexed[name] = {row[key]: row for row in values}
        require(len(indexed[name]) == len(values), 'duplicate history identity')
        require(all(row['transaction_id'] == header['id'] for row in values), 'cross-document history')
        require(all(row['created_by'] == s.actor.id and row['created_at'] == header['updated_at']
                    and row['created_via'] == header['updated_via'] for row in values),
                'incorrect creation provenance')

    if old:
        current = bills.resolve(s, data['input'].bill)
        require(current == old and current['id'] == header['id'] and current['status'] == 'posted',
                'stale or wrong prior bill')
        require(header['version'] == old['version'] + 1, 'wrong bill version')
        require(all(header[key] == old[key] for key in ('created_at', 'created_by', 'created_via')),
                'changed creation provenance')
    else:
        require(operation == 'post' and header['version'] == 1, 'invalid new bill')
        require(not effects.rows(s, c.transactions, c.transactions.c.id == header['id']),
                'reused document id')

    batches = pending['posting_batches']
    business = [batch for batch in batches if batch['kind'] != 'reversal']
    inverses = [batch for batch in batches if batch['kind'] == 'reversal']
    require(len(inverses) == (1 if old else 0), 'wrong reversal count')
    require(len(business) == (0 if operation == 'void' else 1), 'wrong business batch count')
    require(all(batch['audit_event_id'] == data['event'] for batch in batches), 'wrong posting event')
    bills.journals.open_dates(s, [batch['effective_date'] for batch in batches])

    legs, sources = pending['posting_lines'], pending['posting_line_sources']
    require(all(leg['batch_id'] in indexed['posting_batches'] for leg in legs), 'unowned posting line')
    require(all(source['posting_line_id'] in indexed['posting_lines'] for source in sources), 'unowned attribution')
    currency = s.company.conn.execute(c.company_info.select()).mappings().one()['home_currency']
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
            require(leg['currency'] == currency, 'foreign posting in a domestic bill')
            require(all(leg[field] is None for field in bills.journals.FACTS),
                    'foreign facts in a domestic bill')
            attributed = sum(amount(source['amount_minor_units'], positive=True)
                             for source in sources if source['posting_line_id'] == leg['id'])
            require(attributed == leg['debit_minor_units'] + leg['credit_minor_units'],
                    'a posting line is not fully attributed to entered lines')
        require(debit == credit and debit > 0, 'a posting batch does not balance')

    if old:
        inverse = inverses[0]
        replaced = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == data['old_revision']['id'],
                                c.posting_batches.c.kind != 'reversal')
        require(len(replaced) == 1 and inverse['reverses_batch_id'] == replaced[0]['id']
                and inverse['effective_date'] == replaced[0]['effective_date'],
                'the reversal does not invert the current business batch at its own date')
        original = effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == replaced[0]['id'],
                                order=c.posting_lines.c.line_no)
        mirrored = sorted((leg for leg in legs if leg['batch_id'] == inverse['id']),
                          key=lambda leg: leg['line_no'])
        require(len(original) == len(mirrored), 'the reversal has a different number of lines')
        for before, after in zip(original, mirrored):
            require(after['reversed_line_id'] == before['id']
                    and after['account_id'] == before['account_id']
                    and after['debit_minor_units'] == before['credit_minor_units']
                    and after['credit_minor_units'] == before['debit_minor_units'],
                    'the reversal is not an exact inverse')
        if business:
            require(business[0]['replaces_batch_id'] == replaced[0]['id']
                    and business[0]['kind'] == 'replacement', 'the replacement does not replace the original')

    if operation == 'void':
        require(header['status'] == 'voided' and header['void_posting_batch_id'] == inverses[0]['id']
                and (header['void_reason'] or '').strip(), 'an incomplete void')
        require(not pending['transaction_revisions'] and not pending['purchase_profiles'],
                'a void writes no new revision')
        return

    require(header['status'] == 'posted', 'a posting write cannot leave the bill voided')
    revisions = pending['transaction_revisions']
    require(len(revisions) == 1, 'wrong revision count')
    revision = revisions[0]
    require(header['current_revision_id'] == revision['id'] and revision['audit_event_id'] == data['event'],
            'the header does not point at the new revision')
    require(revision['name_type'] == 'vendor' and revision['currency'] == currency,
            'a bill revision names its vendor in home currency')

    profiles = pending['purchase_profiles']
    require(len(profiles) == 1 and profiles[0]['revision_id'] == revision['id']
            and profiles[0]['type'] == 'bill', 'wrong purchase profile')
    profile = profiles[0]
    captured = BillProfile.model_validate_json(profile['profile_snapshot'])
    require(captured.vendor.id == profile['vendor_id'] == revision['name_id']
            and captured.ap_account.id == profile['ap_account_id'], 'captured header facts disagree with columns')
    require(captured.ap_account.type == 'accounts_payable', 'a bill is not owed out of a payable account')
    require(profile['due_date'] >= revision['date'], 'a bill is due before it was dated')
    require((profile['supplier_reference'] is None) == (profile['supplier_reference_key'] is None),
            'a supplier reference without its comparison key')

    envelopes = pending['document_lines']
    expenses, items = pending['purchase_expense_lines'], pending['purchase_item_lines']
    require(bool(envelopes) and len(envelopes) == len(expenses) + len(items),
            'every entered line needs exactly one profile')
    require(all(line['revision_id'] == revision['id'] for line in envelopes + expenses + items),
            'a line belongs to another revision')
    require(sorted(line['position'] for line in envelopes) == list(range(1, len(envelopes) + 1)),
            'non-contiguous entered lines')
    require(all(line['kind'] == 'purchase' and line['account_id'] is None and line['side'] is None
                and line['amount_minor_units'] is None and line['account_snapshot'] is None
                for line in envelopes), 'a purchase envelope carries no accounting of its own')
    identities = {line['line_id'] for line in envelopes}
    require(len(identities) == len(envelopes), 'a line identity is used twice')
    known = {row['id'] for row in effects.rows(s, c.document_line_identities,
                                               c.document_line_identities.c.transaction_id == header['id'])}
    known |= {row['id'] for row in pending['document_line_identities']}
    require(identities <= known, 'a line identity this document does not own')

    by_envelope = {line['document_line_id']: ('expense', line) for line in expenses}
    by_envelope.update({line['document_line_id']: ('item', line) for line in items})
    require(len(by_envelope) == len(envelopes), 'one envelope owns two line profiles')
    totals = {'expense': 0, 'item': 0}
    for envelope in envelopes:
        found = by_envelope.get(envelope['id'])
        require(found is not None, 'an entered line without its expense or item profile')
        family, line = found
        facts = bills.LINE_FACTS[family].model_validate_json(line['line_snapshot'])
        require(facts.account.id == line['account_id'], 'captured line facts disagree with columns')
        require(facts.account.type in bills.EXPENSE_ACCOUNTS, 'a bill line names an ineligible account')
        require(bool(line['billable']) is facts.billable, 'billable disagrees with captured facts')
        require(not line['billable'] or line['customer_id'] is not None,
                'a billable cost names no customer or job')
        require(line['customer_id'] == (facts.customer.id if facts.customer else None),
                'the captured job disagrees with the column')
        if family == 'item':
            _item(line, facts)
        totals[family] += amount(line['amount_minor_units'], positive=True)
    require(totals['expense'] == profile['expense_total_minor_units']
            and totals['item'] == profile['item_total_minor_units'],
            'a line family does not add up to the total the header carries for it')
    total = totals['expense'] + totals['item']
    require(total == revision['total_minor_units'] and total > 0,
            'the entered lines do not add up to the bill')

    batch = business[0]
    own = [leg for leg in legs if leg['batch_id'] == batch['id']]
    payable = [leg for leg in own if leg['account_id'] == profile['ap_account_id'] and leg['credit_minor_units']]
    require(len(payable) == 1 and payable[0]['credit_minor_units'] == total,
            'Accounts Payable is not credited exactly once for the whole bill')
    debits = {}
    for leg in own:
        if leg is payable[0]:
            continue
        require(leg['debit_minor_units'] > 0, 'a bill posts nothing but line debits and one payable credit')
        debits[leg['id']] = leg
    require(len(debits) == len(envelopes), 'one debit per entered line')
    attributed = {}
    for source in sources:
        if source['posting_line_id'] == payable[0]['id']:
            attributed[source['document_line_id']] = attributed.get(source['document_line_id'], 0) + source['amount_minor_units']
    require(attributed == {envelope['id']: by_envelope[envelope['id']][1]['amount_minor_units']
                           for envelope in envelopes},
            'the payable credit is not attributed line by line')

    obligations = pending['ap_obligation_keys']
    existing = effects.rows(s, c.ap_obligation_keys, c.ap_obligation_keys.c.transaction_id == header['id'])
    require(len(obligations) + len(existing) == 1, 'a bill owes exactly one payable')
    obligation = (obligations or existing)[0]
    require(obligation['vendor_id'] == profile['vendor_id']
            and obligation['ap_account_id'] == profile['ap_account_id']
            and obligation['currency'] == currency and obligation['ordinal'] == 1,
            'the payable does not match the bill it belongs to')
    if existing:
        require(not obligations, 'a correction cannot mint a second payable')
    components = pending['ap_obligation_components']
    require(len(components) == len(envelopes), 'one obligation component per entered line')
    require(all(component['key_id'] == obligation['id'] and component['currency'] == currency
                and component['revision_id'] == revision['id'] for component in components),
            'an obligation component belongs elsewhere')
    attributions = {source['id']: source for source in sources
                    if source['posting_line_id'] == payable[0]['id']}
    owed = 0
    for component in components:
        source = attributions.get(component['posting_source_id'])
        require(source is not None and source['document_line_id'] == component['document_line_id']
                and source['amount_minor_units'] == component['amount_minor_units'],
                'an obligation component does not name its own payable attribution')
        owed += amount(component['amount_minor_units'], positive=True)
    require(owed == total, 'the payable components do not add up to what the bill owes')

    plan_custom = data.get('custom_plan')
    require(plan_custom is not None, 'a revision without a custom-field plan')
    from bookflow.company import journal_custom_fields as custom
    custom.validate(s.company, plan_custom, header['id'], json.loads(revision['custom_fields_snapshot']),
                    record_type='bill')
