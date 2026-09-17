"""Read-only cash recognition adjustments, signed debit minus credit.

The caller owns one database snapshot. Add these rows to accrual effects at a
cutoff; subtract two cutoff projections for a period. Matching is current, money
and commercial dates are historical. No reporting row is a posting instruction.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date

from bookflow.core.errors import BookflowError

PL_TYPES = frozenset({'income', 'other_income', 'expense', 'other_expense', 'cost_of_goods_sold'})


@dataclass(frozen=True)
class Adjustment:
    """One synthetic leg. Identity fields always identify retained source facts.

    amount_minor_units is debit minus credit. control_offset distinguishes the
    balancing control leg from its original P&L component. effective_date is the
    original component date, NOT a synthetic cash posting date. This is a cutoff
    balance adjustment, not a stream to filter by date after construction.
    """
    transaction_id: str
    revision_id: str
    posting_line_id: str
    batch_id: str
    posting_source_id: str
    document_line_id: str
    line_id: str
    account_id: str
    amount_minor_units: int
    currency: str
    effective_date: str
    name_type: str | None
    name_id: str | None
    class_id: str | None
    item_id: str | None
    control_offset: bool = False


def _invalid(message):
    raise BookflowError('E_CASH_BASIS_EVIDENCE', message)


def _rows(db, sql):
    cursor = db.execute(sql)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _signed_shares(amounts, paid, total):
    """Endpoint allocation over signed components, never absolute weights.

    100 expense + 100 asset - 20 discount, 90 paid => 50 + 50 - 10.
    Stable source order places indivisible cents. Full settlement exhausts each
    component, including negative components, exactly.
    """
    if total <= 0 or not 0 <= paid <= total or sum(amounts) != total:
        _invalid('Settlement components do not reconcile to their captured payable.')
    cumulative = previous = 0
    result = []
    for amount in amounts:
        cumulative += amount
        endpoint = cumulative * paid // total
        result.append(endpoint - previous)
        previous = endpoint
    return result


def _recognized(value, paid, total):
    """Truncate magnitude, then restore sign: returns cannot earn a penny."""
    return (abs(value) * paid // total) * (1 if value >= 0 else -1)


def cutoff_adjustments(db, as_of: str) -> tuple[Adjustment, ...]:
    """Balanced cash-basis deferrals at ISO date ``as_of``.

    Accepts a company Database or its DB-API connection. Read inside the caller's
    transaction; do not reuse the result across authority/data snapshots. All
    current allocation inverses are consulted, including those after the cutoff.
    """
    date.fromisoformat(as_of)
    raw = getattr(db, 'raw', db)
    headers = {r['id']: r for r in _rows(raw, 'SELECT * FROM transactions')}
    revisions = {r['id']: r for r in _rows(raw, 'SELECT * FROM transaction_revisions')}
    documents = {r['id']: r for r in _rows(raw, 'SELECT * FROM document_lines')}
    profiles = {}
    for table, family, control in (
        ('sales_profiles', None, 'control_account_id'),
        ('credit_profiles', 'credit_memo', 'ar_account_id'),
        ('purchase_profiles', 'bill', 'ap_account_id'),
        ('vendor_credit_profiles', 'vendor_credit', 'ap_account_id'),
    ):
        for row in _rows(raw, f'SELECT * FROM {table}'):
            kind = family or row['type']
            if kind in {'invoice', 'statement_charge', 'credit_memo', 'bill', 'vendor_credit'}:
                profiles[row['revision_id']] = (kind, row[control])
    line_profiles = {}
    for table in ('sales_line_profiles', 'credit_line_profiles', 'purchase_item_lines'):
        for row in _rows(raw, f'SELECT * FROM {table}'):
            line_profiles[row['document_line_id']] = row

    # Monetary applications use the original payment revision date; noncash
    # applications require both commercial components and the application date.
    applications = {r['id']: r for r in _rows(raw, """
        SELECT a.* FROM applications a WHERE a.kind='apply'
        AND NOT EXISTS(SELECT 1 FROM applications x WHERE x.reverses_application_id=a.id)
    """)}
    allocations = _rows(raw, """
        SELECT a.* FROM application_allocations a WHERE a.kind='allocation'
        AND NOT EXISTS(SELECT 1 FROM application_allocations x WHERE x.reverses_allocation_id=a.id)
    """)
    allocated = defaultdict(int)
    for row in allocations:
        allocated[row['application_id']] += row['amount_minor_units']
    if any(allocated[key] != row['amount_minor_units'] for key, row in applications.items()):
        _invalid('A live application lacks its complete recognition allocations.')
    net_paid = defaultdict(int)
    credit_paid = defaultdict(int)
    credit_components = {r['id']: r for r in _rows(raw, 'SELECT * FROM credit_components')}
    for allocation in allocations:
        application = applications.get(allocation['application_id'])
        if application is None:
            _invalid('A live recognition allocation has no live application.')
        target_date = revisions[headers[allocation['target_transaction_id']]['current_revision_id']]['date']
        source_date = revisions[allocation['source_revision_id']]['date']
        when = max(target_date, source_date)
        credit = allocation['credit_source_component_id']
        if credit:
            when = max(when, application['effective_date'])
        if when > as_of:
            continue
        if allocation['logical_kind'] == 'net':
            net_paid[(allocation['target_transaction_id'], allocation['target_line_id'])] += allocation['amount_minor_units']
        if credit:
            component = credit_components[credit]
            line = documents[component['document_line_id']]
            credit_paid[(component['transaction_id'], line['line_id'])] += allocation['amount_minor_units']
    for consumption in _rows(raw, """
        SELECT c.* FROM customer_refund_consumptions c
        WHERE c.credit_source_component_id IS NOT NULL
        AND c.reverses_consumption_id IS NULL
        AND NOT EXISTS(SELECT 1 FROM customer_refund_consumptions x WHERE x.reverses_consumption_id=c.id)
    """):
        component = credit_components[consumption['credit_source_component_id']]
        if max(consumption['effective_date'], revisions[component['revision_id']]['date']) <= as_of:
            line = documents[component['document_line_id']]
            credit_paid[(component['transaction_id'], line['line_id'])] += consumption['amount_minor_units']

    ap_paid = defaultdict(int)
    ap_credit_paid = defaultdict(int)
    ap_components = {r['id']: r for r in _rows(raw, 'SELECT * FROM ap_source_components')}
    ap_keys = {r['id']: r for r in _rows(raw, 'SELECT * FROM ap_source_keys')}
    for application in _rows(raw, """
        SELECT a.* FROM ap_applications a WHERE a.kind='apply'
        AND NOT EXISTS(SELECT 1 FROM ap_applications x WHERE x.reverses_application_id=a.id)
    """):
        component = ap_components[application['source_component_id']]
        source_date = revisions[component['revision_id']]['date']
        key = ap_keys[application['source_key_id']]
        when = source_date
        if key['source_type'] == 'vendor_credit':
            when = max(when, application['effective_date'],
                revisions[headers[application['obligation_transaction_id']]['current_revision_id']]['date'])
        if when <= as_of:
            ap_paid[application['obligation_transaction_id']] += application['amount_minor_units']
            if key['source_type'] == 'vendor_credit':
                line = documents[component['document_line_id']]
                ap_credit_paid[(application['source_transaction_id'], line['line_id'])] += application['amount_minor_units']

    sources = _rows(raw, """
        SELECT s.id source_id,s.reversed_source_id,s.document_line_id,s.revision_id,
          s.amount_minor_units source_amount,l.id posting_line_id,l.transaction_id,
          l.account_id,l.debit_minor_units,l.credit_minor_units,l.currency,
          l.name_type,l.name_id,l.class_id,a.type account_type,b.effective_date,b.id batch_id
        FROM posting_line_sources s JOIN posting_lines l ON l.id=s.posting_line_id
        JOIN posting_batches b ON b.id=l.batch_id JOIN accounts a ON a.id=l.account_id
        ORDER BY b.effective_date,b.id,l.line_no,s.id
    """)
    posting_lines = {r['id']: r for r in _rows(raw, '''
        SELECT l.*,b.effective_date,a.type account_type FROM posting_lines l
        JOIN posting_batches b ON b.id=l.batch_id JOIN accounts a ON a.id=l.account_id
    ''')}
    attributed = defaultdict(int)
    for row in sources:
        attributed[row['posting_line_id']] += row['source_amount']
    for row in posting_lines.values():
        if (row['effective_date'] <= as_of and row['account_type'] in PL_TYPES
                and headers[row['transaction_id']]['type'] in
                {'invoice', 'statement_charge', 'credit_memo', 'bill', 'vendor_credit'}
                and attributed[row['id']] != row['debit_minor_units'] + row['credit_minor_units']):
            _invalid('A commercial P&L posting lacks its complete source attribution.')
    for row in sources:
        if (row['effective_date'] <= as_of and headers[row['transaction_id']]['type'] in
                {'invoice', 'statement_charge', 'credit_memo', 'bill', 'vendor_credit'}
                and row['revision_id'] not in profiles):
            _invalid('A commercial revision lacks its required recognition profile.')
    by_source = {r['source_id']: r for r in sources}
    # Collapse exact source inverses at the cutoff before rounding recognition.
    # Old revisions are not dropped merely because a document was corrected.
    amounts = defaultdict(int)
    for row in sources:
        if row['effective_date'] > as_of:
            continue
        origin = row
        while origin['reversed_source_id']:
            origin = by_source[origin['reversed_source_id']]
        amounts[origin['source_id']] += row['source_amount'] * (1 if row['debit_minor_units'] else -1)

    result = []

    def emit(row, amount, control, commercial=None):
        if not amount:
            return
        line = documents[commercial or row['document_line_id']]
        profile = line_profiles.get(line['id'], {})
        attribution = row
        leg = Adjustment(row['transaction_id'], row['revision_id'], row['posting_line_id'], row['batch_id'],
            row['source_id'], line['id'], line['line_id'], row['account_id'], amount,
            row['currency'], row['effective_date'], attribution['name_type'],
            attribution['name_id'], attribution['class_id'], profile.get('item_id'))
        result.extend((leg, replace(leg, account_id=control, amount_minor_units=-amount, control_offset=True)))

    def ar_fraction(line, family):
        profile = line_profiles.get(line['id'])
        if profile is None:
            _invalid('A receivable recognition component lacks its captured commercial line.')
        key = (line['transaction_id'], line['line_id'])
        denominator = profile['gross_minor_units'] if family == 'credit_memo' else profile['net_minor_units']
        paid = credit_paid[key] if family == 'credit_memo' else net_paid[key]
        if denominator == 0 or revisions[line['revision_id']]['total_minor_units'] == 0:
            return 1, 1
        if not 0 <= paid <= denominator:
            _invalid('Recognized receivable capacity exceeds its captured commercial component.')
        return paid, denominator

    capacity_sources = {r['posting_source_id'] for r in _rows(raw, 'SELECT posting_source_id FROM ap_obligation_components')}
    capacity_sources.update(r['posting_source_id'] for r in ap_components.values())
    ap_rows = defaultdict(list)
    movement_sources = set()
    recost_documents = {r['transaction_id'] for r in _rows(raw, "SELECT transaction_id FROM inventory_documents WHERE kind='recost'")}
    movements = _rows(raw, 'SELECT * FROM inventory_movements ORDER BY sequence')
    movement_by_id = {r['id']: r for r in movements}
    accumulated_cost = defaultdict(int)
    cost_groups = defaultdict(list)
    source_by_batch_account = defaultdict(list)
    for row in sources:
        source_by_batch_account[(row['batch_id'], row['account_id'])].append(row)
    for movement in movements:
        if movement['effective_date'] > as_of:
            continue
        owner = movement
        while owner['reverses_movement_id'] or owner['corrects_movement_id']:
            owner = movement_by_id[owner['reverses_movement_id'] or owner['corrects_movement_id']]
        profile = profiles.get(owner['revision_id'])
        if profile is None and headers[owner['transaction_id']]['type'] in {'invoice', 'statement_charge', 'credit_memo'}:
            _invalid('An inventory sale or return lacks its required commercial profile.')
        if not profile or profile[0] not in {'invoice', 'statement_charge', 'credit_memo'}:
            continue
        candidates = source_by_batch_account[(movement['posting_batch_id'], movement['offset_account_id'])]
        matches = [r for r in candidates if r['document_line_id'] == movement['document_line_id']]
        # inventory_effects binds each retained journal asset at line_index
        # 2*n+1, with its exact offset at 2*n+2. Several pairs share a batch.
        # Do not infer this counterpart from a shared account or equal amount.
        if not matches and movement['posting_line_id'] and movement['transaction_id'] in recost_documents:
            asset_leg = posting_lines[movement['posting_line_id']]
            matches = [r for r in candidates
                       if posting_lines[r['posting_line_id']]['line_no'] == asset_leg['line_no'] + 1
                       and documents[r['document_line_id']]['position'] ==
                           documents[movement['document_line_id']]['position'] + 1
                       and r['source_amount'] == abs(movement['value_minor_units'])
                       and r['debit_minor_units'] - r['credit_minor_units'] == -movement['value_minor_units']]
        if len(matches) != 1 and movement['value_minor_units']:
            _invalid('An inventory cost movement lacks exact offset attribution.')
        for row in matches:
            movement_sources.add(row['source_id'])
        if not movement['value_minor_units']:
            continue
        cost_groups[owner['id']].append((movement, matches[0], owner, profile))

    for group in cost_groups.values():
        if sum(m['value_minor_units'] for m, _, _, _ in group) == 0:
            continue  # Exact cancellation before considering current matched capacity.
        for movement, row, owner, profile in group:
            paid, total = ar_fraction(documents[owner['document_line_id']], profile[0])
            # Exact signed cost, including effective-date recost and reversal.
            cost = -movement['value_minor_units']
            previous = accumulated_cost[owner['id']]
            accumulated_cost[owner['id']] += cost
            recognized = _recognized(accumulated_cost[owner['id']], paid, total) - _recognized(previous, paid, total)
            emit(row, recognized - cost, profile[1], owner['document_line_id'])

    for source_id, amount in amounts.items():
        if not amount or source_id in movement_sources:
            continue
        row = by_source[source_id]
        profile = profiles.get(row['revision_id'])
        if profile is None:
            continue
        family, control = profile
        if family in {'bill', 'vendor_credit'}:
            if source_id not in capacity_sources:
                ap_rows[row['revision_id']].append((row, amount))
        elif row['account_type'] in PL_TYPES:
            paid, total = ar_fraction(documents[row['document_line_id']], family)
            recognized = _recognized(amount, paid, total)
            emit(row, recognized - amount, control)

    for revision_id, rows in ap_rows.items():
        family, control = profiles[revision_id]
        revision = revisions[revision_id]
        sign = 1 if family == 'bill' else -1
        total = sum(sign * amount for _, amount in rows)
        # Linked inventory bills may contain only control transfers, and require
        # no P&L deferral. Their physical receipt/correction is left untouched.
        if not any(row['account_type'] in PL_TYPES for row, _ in rows):
            continue
        paid = ap_paid[revision['transaction_id']] if family == 'bill' else sum(
            amount for (transaction_id, _), amount in ap_credit_paid.items() if transaction_id == revision['transaction_id'])
        if total != revision['total_minor_units']:
            _invalid('Purchase recognition components do not exhaust the captured total.')
        if total == 0 and paid == 0:
            continue  # No collectible amount: retain the physical commercial effects.
        if family == 'vendor_credit':
            # Source capacity already names the exact credited commercial line.
            # Do not redistribute that consumption to unrelated expense accounts.
            line_rows = defaultdict(list)
            for row, amount in rows:
                line_rows[row['document_line_id']].append((row, amount))
            for document_line_id, components in line_rows.items():
                line = documents[document_line_id]
                used = ap_credit_paid[(revision['transaction_id'], line['line_id'])]
                values = [-amount for _, amount in components]
                for (row, amount), share in zip(components, _signed_shares(values, used, sum(values))):
                    if row['account_type'] in PL_TYPES:
                        emit(row, -share - amount, control)
            continue
        shares = _signed_shares([sign * amount for _, amount in rows], paid, total)
        for (row, amount), share in zip(rows, shares):
            if row['account_type'] in PL_TYPES:
                emit(row, sign * share - amount, control)
    return tuple(result)
