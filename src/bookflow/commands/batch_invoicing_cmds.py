"""Billing groups and batch invoicing, registered for every adapter.

Two nouns. ``billing-group`` keeps the reusable sets of customers a retainer or a membership fee
is addressed to. ``batch-invoice`` writes one ordinary invoice per customer from one request and
records what it did, customer by customer, so a run that half worked can be read back and its
failures retried on their own.
"""

from bookflow.company import batch_invoicing as batch, billing_groups as groups
from bookflow.company.batch_invoicing_models import (
    BatchInvoiceOutput, BatchInvoicePostInput, BatchInvoiceQueryInput, BatchInvoiceRetryInput,
    BatchPageOutput, BatchSelector, BatchShowOutput, BillingGroupCreateInput,
    BillingGroupDeleteInput, BillingGroupListInput, BillingGroupMembersInput,
    BillingGroupPageOutput, BillingGroupRenameInput, BillingGroupShowOutput,
    BillingGroupWriteOutput, GroupSelector,
)
from bookflow.core.registry import Plan, command

_GROUP_WRITE_ERRORS = ['E_RECORD_NOT_FOUND', 'E_NAME_TAKEN', 'E_VERSION_CONFLICT']
# What a batch itself can refuse. The codes an individual invoice raises -- an inactive
# customer, a closed period, a tax code that has gone -- are not in this list on purpose: a
# batch never raises them, it reports them on the customer's own row, which is the whole point
# of the feature. tests/error_matrix.py records them against that row.
_BATCH_WRITE_ERRORS = ['E_RECORD_NOT_FOUND']


# The planners live here rather than being passed straight through from the service, because a
# command's plan function is what names its owner in the permission catalog and what the noun
# index reads to say which module registers which noun.
def plan_group_create(inp, ctx, s) -> Plan:
    return groups.plan_create(inp, ctx, s)


def plan_group_rename(inp, ctx, s) -> Plan:
    return groups.plan_rename(inp, ctx, s)


def plan_group_delete(inp, ctx, s) -> Plan:
    return groups.plan_delete(inp, ctx, s)


def plan_group_add(inp, ctx, s) -> Plan:
    return groups.plan_add(inp, ctx, s)


def plan_group_remove(inp, ctx, s) -> Plan:
    return groups.plan_remove(inp, ctx, s)


def _group_write(name, description, model, planner, applier, *, error_codes, positional, version_source=None):
    cmd = command(
        name, scope='company', description=description, input_model=model,
        output_model=BillingGroupWriteOutput, writes={'company'}, required_role='standard',
        capability='customer', accepts_idempotency_key=True, positional=positional,
        version_source=version_source, error_codes=error_codes)(planner)
    cmd.applier(applier)
    return cmd


billing_group_create = _group_write(
    'billing-group create',
    'Create a named, reusable set of customers or jobs to invoice together. A group carries no '
    'terms, price level, tax code or message: those belong to the customers in it and are '
    'resolved for each of them when an invoice is actually written. Names are unique within the '
    'company, ignoring case. Members may be given here or added later, and the order they are '
    'given is the order a batch invoices them in.',
    BillingGroupCreateInput, plan_group_create, groups.apply_create,
    # No positional: a create with one would be read as a record action by the list page's
    # action strip and the "New billing group" link would never be rendered.
    error_codes=['E_RECORD_NOT_FOUND', 'E_NAME_TAKEN'], positional=[])

billing_group_rename = _group_write(
    'billing-group rename',
    'Rename a billing group. Membership and every batch already recorded against the group are '
    'untouched; only the name a person reads changes.',
    BillingGroupRenameInput, plan_group_rename, groups.apply_rename,
    error_codes=_GROUP_WRITE_ERRORS, positional=['billing_group'],
    version_source=('billing-group show', 'billing_group', 'version'))

billing_group_delete = _group_write(
    'billing-group delete',
    'Delete a billing group and its membership rows. No customer and no invoice is touched, and '
    'batches already recorded against the group keep their own record of what they did.',
    BillingGroupDeleteInput, plan_group_delete, groups.apply_delete,
    error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'], positional=['billing_group'],
    version_source=('billing-group show', 'billing_group', 'version'))

billing_group_add = _group_write(
    'billing-group add',
    'Add customers or jobs to a billing group, after the members already in it. A customer may '
    'belong to several groups. A customer already in this group is left where it is rather than '
    'moved, so adding the same list twice changes nothing.',
    BillingGroupMembersInput, plan_group_add, groups.apply_add,
    error_codes=['E_RECORD_NOT_FOUND'], positional=['billing_group'])

billing_group_remove = _group_write(
    'billing-group remove',
    'Take customers or jobs out of a billing group. The customers themselves are untouched, and '
    'a customer that is not in the group is ignored rather than refused. This is also how a '
    'customer is released before it can be removed from the company for good.',
    BillingGroupMembersInput, plan_group_remove, groups.apply_remove,
    error_codes=['E_RECORD_NOT_FOUND'], positional=['billing_group'])


def plan_group_show(inp, ctx, s) -> Plan:
    return Plan(groups.show(s, inp))


def plan_group_list(inp, ctx, s) -> Plan:
    return Plan(groups.page(s, inp))


def plan_batch_post(inp, ctx, s) -> Plan:
    return batch.prepare_post(s, ctx, inp)


def plan_batch_retry(inp, ctx, s) -> Plan:
    return batch.prepare_retry(s, ctx, inp)


def plan_batch_show(inp, ctx, s) -> Plan:
    return Plan(batch.show(s, inp))


def plan_batch_query(inp, ctx, s) -> Plan:
    return Plan(batch.page(s, inp))


billing_group_show = command(
    'billing-group show', scope='company',
    description='Show one billing group and its members in invoicing order, each with the '
                'customer name it currently carries and whether that customer is still active. '
                'An inactive member stays in the group and is reported as a failed row by a '
                'batch rather than being silently dropped from it.',
    input_model=GroupSelector, output_model=BillingGroupShowOutput, required_role='member',
    capability='customer', positional=['billing_group'],
    error_codes=['E_RECORD_NOT_FOUND'])(plan_group_show)

billing_group_list = command(
    'billing-group list', scope='company',
    description='Page billing groups in name order with the number of customers in each. '
                'Continue from the id of the last group on the previous page.',
    input_model=BillingGroupListInput, output_model=BillingGroupPageOutput, required_role='member',
    capability='customer',
    error_codes=['E_LIST_FILTER'])(plan_group_list)


batch_invoice_post = command(
    'batch-invoice post', scope='company',
    description='Invoice a billing group, or an explicit list of customers, with the same lines: '
                'one ordinary invoice per customer. Every invoice resolves its own customer\'s '
                'terms, tax code, tax item, sales rep, price level, class, billing address and '
                'message exactly as a hand-entered invoice would, so the same line can be a '
                'different amount on each invoice; a batch has no place to put a shared term or '
                'price level and never stamps one. Dry-run previews one row per customer with '
                'its resolved amount and writes nothing, which is also where a customer that '
                'would be refused is visible before anything is posted. A customer that is '
                'refused does not roll back the invoices that succeeded: every outcome is '
                'recorded against the batch, and batch-invoice retry runs the failures again. '
                'Document numbers come from the existing allocator and are allocated only on '
                'successful creation, so a preview shows none.',
    input_model=BatchInvoicePostInput, output_model=BatchInvoiceOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    error_codes=_BATCH_WRITE_ERRORS)(plan_batch_post)
batch_invoice_post.ledger = True
batch_invoice_post.applier(batch.apply_post)

batch_invoice_retry = command(
    'batch-invoice retry', scope='company',
    description='Invoice again, from a recorded batch, only the customers whose invoice was '
                'refused. The request is the one that batch was given, so the lines and words are '
                'unchanged; supply a date to move the retry off the original batch date. This '
                'records its own batch naming the one it retried, and refuses when the named '
                'batch has no failed customers left.',
    input_model=BatchInvoiceRetryInput, output_model=BatchInvoiceOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    positional=['batch'],
    error_codes=_BATCH_WRITE_ERRORS)(plan_batch_retry)
batch_invoice_retry.ledger = True
batch_invoice_retry.applier(batch.apply_retry)

batch_invoice_show = command(
    'batch-invoice show', scope='company',
    description='Show one recorded batch and what it did for each customer in turn: the invoice '
                'it created with its allocated number and resolved total, or the error code and '
                'message that refused it. This is the durable record, so it still reads back '
                'after the response that reported it has been closed.',
    input_model=BatchSelector, output_model=BatchShowOutput, required_role='member',
    capability='ledger.read', positional=['batch'],
    error_codes=['E_RECORD_NOT_FOUND'])(plan_batch_show)

batch_invoice_query = command(
    'batch-invoice query', scope='company',
    description='Page recorded batches, newest first, with their counts and created totals. '
                'Filter by billing group or by invoice date, and continue from the id of the '
                'last batch on the previous page.',
    input_model=BatchInvoiceQueryInput, output_model=BatchPageOutput, required_role='member',
    capability='ledger.read',
    error_codes=['E_RECORD_NOT_FOUND'])(plan_batch_query)


BILLING_GROUP_COMMANDS = [billing_group_create, billing_group_rename, billing_group_delete,
                          billing_group_add, billing_group_remove, billing_group_show,
                          billing_group_list]
BATCH_INVOICE_COMMANDS = [batch_invoice_post, batch_invoice_retry, batch_invoice_show,
                          batch_invoice_query]
