"""An independent reading of what a customer refund is about to store.

The writer builds the aggregate; this checks it against the books' own rules without reusing
the writer's arithmetic -- the balance is recomputed from the posting rows themselves, the
accounts are read again from the chart, and what each credit is still worth is read again from
``credits.facts`` rather than taken from the plan. It runs inside the writing transaction, over
a graph the writer rebuilt there, so what it checks is what is about to be stored rather than
what a preview once said. Anything it refuses is ``E_INTERNAL``: a caller cannot cause these.

The two refusals that are not internal are over-refund and a closed period. Two people
refunding the same credit at the same moment each see it available, and the second write has
to lose; that check is made here, in the writer's own transaction, against what storage says at
that instant, and it refuses with ``E_CREDIT_UNAVAILABLE``.

The check this file exists for is the one about legs: a refund has exactly two, a receivable
debit and a funding credit, and **no income leg and no tax leg**. The credit memo already
reversed the sale; a refund that touched income or sales tax again would reverse the same
revenue twice, and that is not something a later reader could ever spot from the totals.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from bookflow.company import accounts, credits, document_effects as effects, schema as c
from bookflow.company.refund_facts import CustomerRefundProfile
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.money import Money

# What a refund may never post to. Income and sales tax were already reversed by the credit
# memo, and an asset or liability that is neither the receivable nor the bank is a different
# document altogether.
FORBIDDEN_TYPES = ('income', 'other_income', 'expense', 'other_expense', 'cost_of_goods_sold')


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid customer refund aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX,
            'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL',
                            message='Invalid customer refund aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    from bookflow.company import refunds

    data = plan.data
    operation = data['operation']
    require(operation in ('post', 'void'), 'wrong operation')
    currency = s.company.conn.execute(c.company_info.select()).mappings().one()['home_currency']
    header, revision, pending = data['header'], data['revision'], data['pending']
    old = data['before']
    require(header['type'] == refunds.DOCUMENT_TYPE, 'wrong document type')
    require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value,
            'header writer attribution')
    require(set(pending) >= {table for table, _, _ in refunds.TABLE_KINDS}, 'incomplete graph')

    indexed = {}
    for name, _, key in refunds.TABLE_KINDS:
        values = pending[name]
        indexed[name] = {row[key]: row for row in values}
        require(len(indexed[name]) == len(values), 'duplicate history identity')
        require(all(row['created_by'] == s.actor.id and row['created_at'] == header['updated_at']
                    and row['created_via'] == header['updated_via'] for row in values),
                'incorrect creation provenance')
        require(all(row['transaction_id'] == header['id'] for row in values), 'cross-document history')

    if old:
        current = refunds.resolve(s, data['input'].refund)
        require(current == old and current['id'] == header['id'] and current['status'] == 'posted',
                'stale or wrong prior refund')
        require(header['version'] == old['version'] + 1, 'wrong refund version')
        require(all(header[key] == old[key] for key in ('created_at', 'created_by', 'created_via')),
                'changed creation provenance')
        require(operation == 'void' and header['status'] == 'voided',
                'a refund is corrected by voiding it')
    else:
        require(operation == 'post' and header['version'] == 1 and header['status'] == 'posted',
                'invalid new refund')
        require(not effects.rows(s, c.transactions, c.transactions.c.id == header['id']),
                'reused document id')
        require(not effects.rows(s, c.transactions, c.transactions.c.type == refunds.DOCUMENT_TYPE,
                                 c.transactions.c.number == header['number']),
                'that refund number is already used')

    _batches(s, data, header, revision, pending, indexed, currency, operation)
    if operation == 'post':
        _profile(s, data, header, revision, pending, currency)
        _consumptions(s, data, header, revision, pending, currency)
    else:
        _releases(s, data, header, revision, pending)


def _batches(s, data, header, revision, pending, indexed, currency, operation):
    batches, legs = pending['posting_batches'], pending['posting_lines']
    sources = pending['posting_line_sources']
    require(len(batches) == 1, 'one accounting effect per write')
    require(all(batch['audit_event_id'] == data['event'] for batch in batches), 'wrong posting event')
    require(batches[0]['kind'] == ('original' if operation == 'post' else 'reversal'), 'wrong batch kind')
    require(batches[0]['effective_date'] == revision['date'], 'an effect is dated away from its revision')
    require(all(leg['batch_id'] in indexed['posting_batches'] for leg in legs), 'unowned posting line')
    require(all(source['posting_line_id'] in indexed['posting_lines'] for source in sources),
            'unowned attribution')
    require(len(legs) == 2, 'a refund is one receivable debit and one funding credit')
    debit = credit = 0
    chart = {row['id']: row for row in effects.rows(s, c.accounts, c.accounts.c.id.in_(
        [leg['account_id'] for leg in legs]))}
    for leg in legs:
        debit += amount(leg['debit_minor_units'])
        credit += amount(leg['credit_minor_units'])
        require(bool(leg['debit_minor_units']) != bool(leg['credit_minor_units']),
                'a posting must have exactly one positive side')
        require(leg['currency'] == currency, 'foreign posting in a domestic refund')
        require(leg['name_type'] == 'customer' and leg['name_id'] == revision['name_id'],
                'a refund posting names the customer it paid')
        account = chart.get(leg['account_id'])
        require(account is not None, 'a refund posts to an account this chart does not have')
        # The whole point of the document, asserted rather than assumed: a refund never
        # touches income or tax, because the credit memo already did.
        require(account['type'] not in FORBIDDEN_TYPES,
                'a refund posts no income, expense or tax leg; the credit memo already reversed the sale')
        attributed = sum(amount(source['amount_minor_units'], positive=True)
                         for source in sources if source['posting_line_id'] == leg['id'])
        require(attributed == leg['debit_minor_units'] + leg['credit_minor_units'],
                'a posting line is not fully attributed to its entered line')
    require(debit == credit == revision['total_minor_units'], 'a refund does not balance at its own total')
    require(sorted(leg['line_no'] for leg in legs) == [1, 2], 'non-contiguous batch lines')


def _profile(s, data, header, revision, pending, currency):
    row = pending['customer_refund_profiles'][0]
    require(len(pending['customer_refund_profiles']) == 1, 'one header per refund')
    require(row['revision_id'] == revision['id'], 'refund header ownership')
    captured = CustomerRefundProfile.model_validate_json(row['profile_snapshot'])
    require(captured.amount_minor_units == amount(row['amount_minor_units'], positive=True)
            == revision['total_minor_units'], 'captured amount differs from the posted total')
    legs = {leg['id']: leg for leg in pending['posting_lines']}
    source = next((value for value in pending['posting_line_sources']
                   if value['id'] == row['ar_posting_source_id']), None)
    require(source is not None, 'the header names an attribution this write does not make')
    leg = legs[source['posting_line_id']]
    receivable = effects.rows(s, c.accounts, c.accounts.c.id == row['ar_account_id'])
    require(len(receivable) == 1 and receivable[0]['type'] == 'accounts_receivable'
            and receivable[0]['active'], 'a refund settles an active receivable account')
    require(leg['account_id'] == row['ar_account_id'] == captured.ar_account.id
            and leg['debit_minor_units'] == revision['total_minor_units']
            and not leg['credit_minor_units'], 'the named attribution is not the receivable debit')
    funding = next(other for other in pending['posting_lines'] if other['id'] != leg['id'])
    bank = effects.rows(s, c.accounts, c.accounts.c.id == row['funding_account_id'])
    require(len(bank) == 1 and bank[0]['type'] == 'bank' and bank[0]['active'],
            'a customer is refunded out of an active bank account')
    require(funding['account_id'] == row['funding_account_id'] == captured.funding_account.id
            and funding['credit_minor_units'] == revision['total_minor_units'],
            'the funding account is not credited for the refunded amount')
    require(accounts.NORMAL_BALANCE[bank[0]['type']] == 'debit',
            'the funding account is not debit-normal, so crediting it is not money leaving')
    require(row['party_id'] == captured.customer.id == revision['name_id'],
            'the captured customer is not the one the document names')


def _consumptions(s, data, header, revision, pending, currency):
    rows = pending['customer_refund_consumptions']
    require(rows, 'a refund pays out named credit, so it consumes some')
    profile = pending['customer_refund_profiles'][0]
    captured = CustomerRefundProfile.model_validate_json(profile['profile_snapshot'])
    wanted = {}
    for row in rows:
        require(row['kind'] == 'consume' and row['reverses_consumption_id'] is None,
                'a new refund releases nothing')
        require(row['revision_id'] == revision['id'] and row['currency'] == currency,
                'a consumption belongs to the revision that made it')
        require(row['effective_date'] == revision['date'],
                'a consumption is dated where the refund is')
        wanted[row['credit_source_key_id']] = wanted.get(row['credit_source_key_id'], 0) + amount(
            row['amount_minor_units'], positive=True)
    require(sum(wanted.values()) == revision['total_minor_units'],
            'the credit consumed differs from the money paid out')
    require({source.credit_source_key_id: source.amount_minor_units for source in captured.sources}
            == wanted, 'the captured sources differ from the consumptions written')
    # Read what each credit is worth again, here, in the writer's transaction: the preview's
    # figure is not evidence that it is still true, and two refunds racing one credit must not
    # both post.
    per_component = {}
    for source in captured.sources:
        facts = credits.facts(s, effects.rows(
            s, c.transactions, c.transactions.c.id == source.credit_memo_id)[0], write=True)
        require(facts['key']['id'] == source.credit_source_key_id, 'a captured source names another credit')
        require(facts['key']['party_id'] == profile['party_id']
                and facts['key']['ar_account_id'] == profile['ar_account_id']
                and facts['key']['currency'] == currency,
                'a refund pays back the customer whose credit it spends, on that receivable account')
        require(facts['header']['status'] == 'posted', 'a voided credit memo is worth nothing')
        require(facts['available'] == source.available_minor_units,
                'the captured available credit is not what the books say')
        if wanted[source.credit_source_key_id] > facts['available']:
            raise BookflowError('E_CREDIT_UNAVAILABLE', details={
                'credit_memo_id': source.credit_memo_id,
                'credit_memo_number': source.credit_memo_number,
                'requested': Money(wanted[source.credit_source_key_id], currency).to_dict(),
                'available': Money(facts['available'], currency).to_dict(),
                'next': 'Refund at most what the credit is still worth.'})
        for component in facts['components']:
            per_component[component['id']] = facts['remaining'][component['id']]
    for row in rows:
        identifier = row['credit_source_component_id']
        require(identifier in per_component, 'a consumption names a component of another credit')
        per_component[identifier] -= row['amount_minor_units']
    require(all(value >= 0 for value in per_component.values()),
            'a consumption exceeds the capacity of the credited line it draws on')


def _releases(s, data, header, revision, pending):
    rows = pending['customer_refund_consumptions']
    live = {row['id']: row for row in credits.active_consumptions(s, refund_id=header['id'])}
    require({row['reverses_consumption_id'] for row in rows} == set(live),
            'voiding a refund releases every consumption it made, and only those')
    provenance = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id', 'kind',
                  'reverses_consumption_id'}
    for row in rows:
        require(row['kind'] == 'release', 'a void writes releases')
        original = live[row['reverses_consumption_id']]
        require({k: v for k, v in row.items() if k not in provenance}
                == {k: v for k, v in original.items() if k not in provenance},
                'a release does not exactly undo its consumption')
