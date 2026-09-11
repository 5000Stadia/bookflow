"""An independent reading of what a sales tax remittance is about to store.

The writer builds the aggregate; this checks it against the books' own rules without reusing
the writer's arithmetic -- the balance is recomputed from the posting rows themselves, the
liability account is read again from the chart, and what the agency is owed is read again from
the liability derivation rather than taken from the plan. It runs inside the writing transaction,
over a graph the writer rebuilt there, so what it checks is what is about to be stored rather
than what a preview once said. Anything it refuses is ``E_INTERNAL``: a caller cannot cause
these.

The one refusal that is not internal is over-remittance. Two people remitting the same balance
at the same moment each see it owed, and the second write has to lose; that check is made here,
in the writer's own transaction, against what storage says at that instant, and it refuses with
``E_APPLICATION_CAPACITY``.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from bookflow.company import document_effects as effects, sales_tax_reports, schema as c
from bookflow.company.sales_tax_payment_facts import SalesTaxPaymentProfile
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.money import Money


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid sales tax payment aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX,
            'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL',
                            message='Invalid sales tax payment aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    from bookflow.company import sales_tax_payments as payments

    data = plan.data
    operation = data['operation']
    require(operation in ('pay', 'void'), 'wrong operation')
    currency = s.company.conn.execute(c.company_info.select()).mappings().one()['home_currency']
    header, revision, pending = data['header'], data['revision'], data['pending']
    old = data['before']
    require(header['type'] == payments.DOCUMENT_TYPE, 'wrong document type')
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
        require(all(row['transaction_id'] == header['id'] for row in values), 'cross-document history')

    if old:
        current = payments.resolve(s, data['input'].payment)
        require(current == old and current['id'] == header['id'] and current['status'] == 'posted',
                'stale or wrong prior remittance')
        require(header['version'] == old['version'] + 1, 'wrong remittance version')
        require(all(header[key] == old[key] for key in ('created_at', 'created_by', 'created_via')),
                'changed creation provenance')
        require(operation == 'void' and header['status'] == 'voided', 'a remittance is corrected by voiding it')
    else:
        require(operation == 'pay' and header['version'] == 1 and header['status'] == 'posted',
                'invalid new remittance')
        require(not effects.rows(s, c.transactions, c.transactions.c.id == header['id']),
                'reused document id')
        require(not effects.rows(s, c.transactions, c.transactions.c.type == payments.DOCUMENT_TYPE,
                                 c.transactions.c.number == header['number']),
                'that remittance number is already used')

    _batches(s, data, header, revision, pending, indexed, currency, operation)
    if operation == 'pay':
        _profile(s, data, header, revision, pending, currency)


def _batches(s, data, header, revision, pending, indexed, currency, operation):
    batches, legs = pending['posting_batches'], pending['posting_lines']
    sources = pending['posting_line_sources']
    require(len(batches) == 1, 'one accounting effect per write')
    require(all(batch['audit_event_id'] == data['event'] for batch in batches), 'wrong posting event')
    require(batches[0]['kind'] == ('original' if operation == 'pay' else 'reversal'), 'wrong batch kind')
    require(batches[0]['effective_date'] == revision['date'], 'an effect is dated away from its revision')
    require(all(leg['batch_id'] in indexed['posting_batches'] for leg in legs), 'unowned posting line')
    require(all(source['posting_line_id'] in indexed['posting_lines'] for source in sources),
            'unowned attribution')
    require(len(legs) == 2, 'a remittance is one debit and one credit')
    debit = credit = 0
    for leg in legs:
        debit += amount(leg['debit_minor_units'])
        credit += amount(leg['credit_minor_units'])
        require(bool(leg['debit_minor_units']) != bool(leg['credit_minor_units']),
                'a posting must have exactly one positive side')
        require(leg['currency'] == currency, 'foreign posting in a domestic remittance')
        require(leg['name_type'] == 'vendor' and leg['name_id'] == revision['name_id'],
                'a remittance posting names the agency it paid')
        attributed = sum(amount(source['amount_minor_units'], positive=True)
                         for source in sources if source['posting_line_id'] == leg['id'])
        require(attributed == leg['debit_minor_units'] + leg['credit_minor_units'],
                'a posting line is not fully attributed to its entered line')
    require(debit == credit == revision['total_minor_units'], 'a remittance does not balance at its own total')
    require(sorted(leg['line_no'] for leg in legs) == [1, 2], 'non-contiguous batch lines')


def _profile(s, data, header, revision, pending, currency):
    from bookflow.company import sales_tax_payments as payments

    row = pending['sales_tax_payment_profiles'][0]
    require(len(pending['sales_tax_payment_profiles']) == 1, 'one header per remittance')
    require(row['revision_id'] == revision['id'], 'remittance header ownership')
    captured = SalesTaxPaymentProfile.model_validate_json(row['profile_snapshot'])
    require(captured.amount_minor_units == amount(row['amount_minor_units'], positive=True)
            == revision['total_minor_units'], 'captured amount differs from the posted total')
    require(row['through_date'] == captured.through_date <= revision['date'],
            'a remittance period ends after the money left')
    live = payments._liability_account(s)
    require(row['liability_account_id'] == live['id'] == captured.liability_account.id,
            'a remittance debits the chart\'s own sales tax payable account')
    # The liability leg is the debit to that account, and the profile names its attribution.
    legs = {leg['id']: leg for leg in pending['posting_lines']}
    source = next((row_ for row_ in pending['posting_line_sources']
                   if row_['id'] == row['liability_posting_source_id']), None)
    require(source is not None, 'the header names an attribution this write does not make')
    leg = legs[source['posting_line_id']]
    require(leg['account_id'] == live['id'] and leg['debit_minor_units'] == revision['total_minor_units']
            and not leg['credit_minor_units'], 'the named attribution is not the liability debit')
    funding = next(other for other in pending['posting_lines'] if other['id'] != leg['id'])
    require(funding['account_id'] == row['funding_account_id']
            and funding['credit_minor_units'] == revision['total_minor_units'],
            'the funding account is not credited for the remitted amount')
    # Read the balance again, here, in the writer's transaction: the preview's figure is not
    # evidence that it is still true, and two remittances racing for one balance must not both
    # post.
    sales_tax_reports.require_accrual_basis(s.company)
    owed = sales_tax_reports.agency_balances(
        s.company, row['through_date'], agency_id=row['agency_id']).get(row['agency_id'], 0)
    require(owed == captured.liability_minor_units, 'the captured liability is not what the books owe')
    if revision['total_minor_units'] > owed:
        raise BookflowError('E_APPLICATION_CAPACITY', details={
            'field': 'amount', 'agency_id': row['agency_id'],
            'requested_minor_units': revision['total_minor_units'], 'available_minor_units': owed,
            'requested': Money(revision['total_minor_units'], currency).to_dict(),
            'available': Money(owed, currency).to_dict(),
            'next': 'Remit at most what this agency is owed through that date.'})
