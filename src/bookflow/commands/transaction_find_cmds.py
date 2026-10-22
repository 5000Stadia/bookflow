"""`transaction find`: search every kind of document at once, the anchor's Edit > Find."""
from bookflow.core.registry import Plan, command
from bookflow.company.transaction_find import TransactionFindInput, TransactionFindOutput


@command('transaction find', scope='company',
         description=('Find any transaction: search invoices, sales receipts, credit memos, statement charges, customer '
                      'payments and refunds, estimates, proposals, work orders, purchase orders, item receipts, bills, '
                      'bill payments, vendor credits, checks, card charges and credits, deposits, transfers, journal '
                      'entries, sales tax payments and adjustments and inventory adjustments in one read, by any mix of '
                      'type (types), name (a customer, job, vendor, employee or other name; part of a name finds every '
                      'match), number (document or check number), amount (exact, or amount_from/amount_to on the '
                      'total), date_from/date_to, memo text and account. Each row gives date, type, number, name, '
                      'account, memo, amount and status (open, paid, posted, closed or void) with the id and the '
                      '`show_command` that opens it, sorted by date then number. Voided documents are included unless '
                      'include_voided is false; deleted ones are not. Use this first when asked to find, look up or '
                      'locate a document, e.g. "the $412.50 check to Acme": types ["check"], name "Acme", amount "412.50".'),
         input_model=TransactionFindInput, output_model=TransactionFindOutput,
         required_role='member', capability='ledger.read',
         error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_QUERY_STALE', 'E_VALUE_RANGE'])
def transaction_find(inp: TransactionFindInput, ctx, s) -> Plan:
    from bookflow.company.transaction_find import find
    return Plan(find(inp, s, principal_id=ctx.on_behalf_of))
