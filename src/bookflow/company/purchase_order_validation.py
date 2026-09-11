"""An independent reading of what a purchase order write is about to store.

The writer builds the aggregate; this checks it against the document's own rules without
reusing the writer's arithmetic -- the total is recomputed from the line rows themselves. It
runs twice, once on the preview and once inside the writing transaction, so a graph that only
became wrong between the two is still caught. Anything it refuses is ``E_INTERNAL``: a caller
cannot cause these, and if one fires it is this module's own fault, not the person's.

The first rule is the one the whole document rests on: **a purchase order write produces no
posting row of any kind.** That is checked here rather than assumed, because "it posts nothing"
is the claim a reader most needs to be able to trust.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from bookflow.company.purchase_order_facts import PurchaseOrderLineProfile, PurchaseOrderProfile
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX

POSTING_TABLES = ('posting_batches', 'posting_lines', 'posting_line_sources',
                  'ap_obligation_keys', 'ap_obligation_components', 'transaction_revisions')


def require(condition, problem):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid purchase order aggregate: ' + problem)


def amount(value, *, positive=False):
    require(type(value) is int and (0 < value if positive else 0 <= value) and value <= INT64_MAX,
            'invalid exact amount')
    return value


def validate(plan, s, ctx):
    try:
        return _validate(plan, s, ctx)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BookflowError('E_INTERNAL',
                            message='Invalid purchase order aggregate: malformed captured facts') from exc


def _validate(plan, s, ctx):
    from bookflow.company import purchase_orders as orders

    data = plan.data
    if not data['changed']:
        return
    header, pending, old = data['header'], data['pending'], data['before']
    operation = data['operation']
    require(operation in ('post', 'update', 'void'), 'wrong operation')
    require(set(pending) == {table for table, _, _ in orders.TABLE_KINDS}, 'incomplete graph')
    require(not any(table in pending for table in POSTING_TABLES), 'a purchase order posts nothing')
    require(header['updated_by'] == s.actor.id and header['updated_via'] == ctx.interface.value,
            'header writer attribution')
    require(header['status'] in ('open', 'partly_received', 'closed', 'voided'), 'unknown state')
    require((old is None) == (operation == 'post'), 'wrong predecessor for this operation')
    if old is not None:
        require(header['id'] == old['id'] and header['version'] == old['version'] + 1,
                'version does not follow its predecessor')

    for name, _, key in orders.TABLE_KINDS:
        values = pending[name]
        identities = {row[key] for row in values}
        require(len(identities) == len(values), 'duplicate history identity')
        require(all(row['document_id'] == header['id'] for row in values), 'cross-document history')
        require(all(row['created_by'] == s.actor.id and row['created_via'] == header['updated_via']
                    for row in values), 'history writer attribution')

    revisions = pending['purchase_order_revisions']
    if operation == 'void':
        require(not revisions and not pending['purchase_order_lines'], 'a void writes no new revision')
        require(header['status'] == 'voided' and header['void_reason'], 'a void records its reason')
        return
    require(len(revisions) == 1, 'exactly one new revision')
    revision = revisions[0]
    require(revision['id'] == header['current_revision_id'], 'the header points at the new revision')
    require(revision['status'] == header['status'], 'the revision captures the current state')
    require(revision['number'] == header['number'], 'the revision captures the current number')
    require(header['status'] != 'voided', 'an ordinary write never voids')

    profile = PurchaseOrderProfile.model_validate_json(revision['profile_snapshot'])
    require(profile.vendor.id == revision['vendor_id'], 'captured vendor matches the stored column')
    require(profile.currency == revision['currency'], 'captured currency matches the stored column')

    lines = pending['purchase_order_lines']
    require(lines, 'a purchase order has at least one ordered line')
    require(all(row['revision_id'] == revision['id'] for row in lines), 'lines belong to this revision')
    require([row['position'] for row in lines] == list(range(1, len(lines) + 1)),
            'ordered line positions are one-based and contiguous')
    identities = {row['id'] for row in pending['purchase_order_line_identities']}
    known = identities | {row['line_id'] for row in lines if row['line_id'] not in identities}
    require(len({row['line_id'] for row in lines}) == len(lines), 'a line identity appears once')
    require(all(row['line_id'] in known for row in lines), 'every line names an identity')

    total = 0
    for row in lines:
        amount(row['amount_minor_units'], positive=True)
        require((row['item_id'] is None) != (row['account_id'] is None),
                'an ordered line is an item or an account, never both and never neither')
        require((row['quantity_microunits'] is None) == (row['rate_minor_units'] is None),
                'quantity and rate are stored together or not at all')
        if row['quantity_microunits'] is not None:
            from bookflow.company import sales_calculations as calc
            require(calc.extension(row['quantity_microunits'], row['rate_minor_units'])
                    == row['amount_minor_units'], 'the line amount is its quantity times its rate')
        facts = PurchaseOrderLineProfile.model_validate_json(row['line_snapshot'])
        captured = facts.item.account.id if facts.item else facts.account.id
        require(bool(captured), 'a line captures where its cost is destined')
        require((facts.item.id if facts.item else None) == row['item_id'], 'captured item matches the column')
        require((facts.account.id if facts.account else None) == row['account_id'],
                'captured account matches the column')
        require(bool(row['billable']) == facts.billable, 'captured billable matches the column')
        require(not row['billable'] or row['customer_id'] is not None,
                'a billable cost names the job it will be passed on to')
        total += row['amount_minor_units']
    require(total == revision['total_minor_units'] == profile.total_minor_units,
            'the stored total is the sum of the ordered lines')
    amount(total, positive=True)
