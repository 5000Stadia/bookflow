"""What a correction of an order-made invoice does to the order (sales-orders design §4, corrections).

Voiding or deleting an invoice made from a sales order or a work order, or updating it so an
order-made line is gone, takes back what it billed. By default (`release_to_order: reopen`) that
quantity is open on the order again, committed and billable; with `release_to_order: cancel` it is
cancelled on the order in the same company transaction. Either way the preview and the result say
which order lines and how much. Other updates (date, terms, memo, independent lines) and credit
memos leave the order alone; a return by credit memo restocks and the line stays invoiced.
"""
from __future__ import annotations

from fractions import Fraction

import sqlalchemy as sa

from bookflow.company import schema as c

ORDER_KINDS = ('sales_order', 'work_order')


def _current_allocations(s, invoice_id):
    t, a = c.transactions, c.work_billing_allocations
    row = s.company.conn.execute(sa.select(t.c.current_revision_id, t.c.status).where(t.c.id == invoice_id)).first()
    if row is None or row.status != 'posted':
        return []
    return [dict(r) for r in s.company.conn.execute(sa.select(a).where(a.c.revision_id == row.current_revision_id)).mappings()]


def _released(s, before, after):
    """Allocations in `before` whose root `after` no longer carries, on sales orders and work orders."""
    kept = {(row['root_document_id'], row['root_line_id']) for row in after}
    gone = [row for row in before if (row['root_document_id'], row['root_line_id']) not in kept]
    if not gone:
        return []
    docs = {row['id']: row for row in s.company.conn.execute(sa.select(c.work_documents).where(
        c.work_documents.c.id.in_({row['source_document_id'] for row in gone}))).mappings()}
    lines = {row['id']: row for row in s.company.conn.execute(sa.select(c.work_lines).where(
        c.work_lines.c.id.in_({row['source_line_id'] for row in gone}))).mappings()}
    out = []
    for row in gone:
        doc = docs.get(row['source_document_id'])
        if doc is None or doc['kind'] not in ORDER_KINDS:
            continue
        line = lines[row['source_line_id']]
        quantity = row['quantity_microunits']
        if quantity is None:   # a quantity that is not whole microunits: read it from the proof
            from bookflow.company import billing_allocations as alloc, billing_math as math
            proof = alloc.read_proof(row)
            quantity = math.exact_microunits(proof.quantity()) if proof else line['quantity_microunits']
        out.append(dict(document=dict(doc), line_id=line['line_id'], item_id=line['item_id'], quantity=quantity))
    return out


def planned(s, invoice_id, after_allocations=()):
    """What a correction about to be written releases: preview time, before anything changes."""
    return _released(s, _current_allocations(s, invoice_id), list(after_allocations))


def notes(s, released, choice):
    from bookflow.core.exact import format_quantity_micro_units
    from bookflow.company.sales_defaults import _row
    out = []
    for row in released:
        doc = row['document']
        item = _row(s.company, 'item', row['item_id'], active=False)['full_name']
        what = (f"cancelled on {doc['kind'].replace('_', ' ')} {doc['number']}" if choice == 'cancel'
                else f"open again on {doc['kind'].replace('_', ' ')} {doc['number']}, committed and billable")
        out.append(f"{format_quantity_micro_units(row['quantity'])} {item} (line {row['line_id']}) is {what}.")
    return out


def cancel(s, ctx, released):
    """`release_to_order: cancel`: cancel each released quantity on its order, in this transaction."""
    from bookflow.company import work, work_models
    by_document = {}
    for row in released:
        by_document.setdefault(row['document']['id'], (row['document'], {}))[1].setdefault(row['line_id'], 0)
        by_document[row['document']['id']][1][row['line_id']] += row['quantity']
    for document_id, (_, added) in by_document.items():
        current = s.company.conn.execute(sa.select(c.work_documents).where(c.work_documents.c.id == document_id)).mappings().one()
        kind = current['kind']
        rev = work.revision(s, dict(current))
        lines = []
        for line in work.saved_lines(s, rev):
            entry = dict(line_id=line['line_id'], item=line['item_id'])
            if line['line_id'] in added:
                cancelled = line['cancelled_quantity_microunits'] + added[line['line_id']]
                from bookflow.core.exact import format_quantity_micro_units
                entry['cancelled_quantity'] = format_quantity_micro_units(cancelled)
            lines.append(entry)
        model = work_models.SalesOrderUpdateInput if kind == 'sales_order' else work_models.WorkOrderUpdateInput
        inp = model.model_validate({kind: document_id, 'expected_version': current['version'], 'lines': lines})
        plan = work.prepare(s, ctx, inp, kind, 'update')
        work.apply(plan, ctx, s)


def wrap(base, *, update=False):
    """An invoice void, delete or update applier that releases to the order as asked, and says so."""
    def apply(plan, ctx, s):
        inp = plan.data.get('input') if isinstance(plan.data, dict) else None
        invoice_id = None
        if inp is not None:
            from bookflow.company import sales
            try:
                invoice_id = sales.resolve(s, inp.invoice, 'invoice')['id']
            except Exception:  # deleted or unknown: the base applier gives the real answer
                invoice_id = None
        before = _current_allocations(s, invoice_id) if invoice_id else []
        applied = base(plan, ctx, s)
        if not before:
            return applied
        released = _released(s, before, _current_allocations(s, invoice_id))
        if not released:
            return applied
        choice = getattr(inp, 'release_to_order', 'reopen')
        if choice == 'cancel':
            cancel(s, ctx, released)
        if applied.output is not None and hasattr(applied.output, 'warnings'):
            applied.output.warnings = list(applied.output.warnings) + notes(s, released, choice)
        return applied
    return apply


def preview_notes(s, plan, inp, *, update=False):
    """Preview: which order lines this correction releases, and to what."""
    try:
        from bookflow.company import sales
        invoice_id = sales.resolve(s, inp.invoice, 'invoice')['id']
    except Exception:
        return
    after = plan.data.get('billing_allocations', []) if (update and isinstance(plan.data, dict)) else []
    released = planned(s, invoice_id, after)
    if released and hasattr(plan.preview, 'warnings'):
        plan.preview.warnings = list(plan.preview.warnings) + notes(s, released, getattr(inp, 'release_to_order', 'reopen'))
