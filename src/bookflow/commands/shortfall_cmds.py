"""Shortfall coverage: `sales-order purchase-order` and `work-order purchase-order` (sales-orders design §5)."""
from typing import Literal

from pydantic import Field, model_validator

from bookflow.core.models import WriteOutput
from bookflow.core.registry import command, Applied, Plan, Touched
from bookflow.company.journal_models import _Date
from bookflow.company.sales_models import StrictModel, Selector, Text
from bookflow.company.purchase_order_models import PurchaseOrderWriteOutput
from bookflow.company.work_outputs import CoverageOutput


class CoverageLine(StrictModel):
    line_id: str | None = Field(default=None, max_length=26, description='The order or work-order line to cover.')
    item: Selector | None = Field(default=None, description='Or the item one stocked line of the document carries.')
    quantity: str = Field(min_length=1, max_length=40, description='Base quantity to order: any positive amount (spares included).')

    @model_validator(mode='after')
    def one_line(self):
        if (self.line_id is None) == (self.item is None):
            raise ValueError('name exactly one of line_id or item')
        return self


class _CoverageInput(StrictModel):
    date: _Date = Field(description='Date of the purchase order.')
    vendor: Selector | None = Field(default=None, description=(
        'The vendor to order from. Omit it when every short line\'s preferred vendor is the same one; with '
        'several, the refusal lists each vendor with its lines.'))
    lines: list[CoverageLine] | None = Field(default=None, min_length=1, max_length=200, description=(
        'Explicit lines and quantities (needs vendor). Omit for the default: each line\'s shortfall, the open '
        'quantity not on hand, not already on order for it and not needed first by other open documents.'))
    expected_date: _Date | None = Field(default=None, description='When the vendor is expected to deliver.')
    memo: Text | None = None


class SalesOrderPurchaseOrderInput(_CoverageInput):
    sales_order: Selector


class WorkOrderPurchaseOrderInput(_CoverageInput):
    work_order: Selector


class ShortLine(StrictModel):
    line_id: str
    item: str
    short: str


class CoveragePurchaseOutput(WriteOutput):
    purchase_order: PurchaseOrderWriteOutput
    links: list[CoverageOutput]
    left_for_other_vendors: list[ShortLine] = Field(default_factory=list, description=(
        'Short lines whose preferred vendor is another one: run the command again naming that vendor.'))


DESCRIPTION = ('Order what this {noun} is short from a vendor: one ordinary purchase order (as purchase-order '
               'post writes) and a coverage link from each of its lines to the {noun} line it covers, in one step. '
               'With no lines the quantities are each stocked line\'s shortfall: open quantity less what links '
               'already cover, after on-hand stock and unlinked incoming purchase orders serve every other open '
               'document first, so no incoming unit is counted twice and running it again before anything changes '
               'orders nothing (it refuses: nothing is short). Lines default to those whose preferred vendor is the '
               'vendor; the result lists short lines left for other vendors. The rate is the vendor\'s purchase cost '
               'for the item, else the item\'s cost. Explicit lines may order any positive quantity (spares). What a '
               'link covers falls as the purchase order is amended down, received, closed or voided, so the shortfall '
               'comes back by exactly that.')


def _register(kind, model):
    noun = kind.replace('_', '-')

    def build(s, ctx, inp):
        from bookflow.company import purchase_orders, shortfall
        header, vendor, lines, left = shortfall.plan(s, inp, kind)
        po_input = shortfall.purchase_input(s, inp, header, vendor, lines)
        return header, lines, left, po_input, purchase_orders.prepare(s, ctx, po_input, 'post')

    def left_rows(left):
        return [dict(line_id=row.line_id, item=row.item_name, short=row.short and __import__(
            'bookflow.company.shortfall', fromlist=['_plain'])._plain(row.short)) for row in left]

    def preview_links(header, lines, po_preview):
        from bookflow.company.shortfall import _plain
        return [CoverageOutput(work_document_id=header['id'], work_kind=header['kind'], work_number=header['number'],
                               work_line_id=line_id, purchase_order_id=po_preview.id,
                               purchase_order_line_id=po_line.line_id, purchase_order_number=po_preview.revision.number,
                               linked=_plain(quantity), to_arrive=_plain(quantity),
                               expected_date=po_preview.revision.expected_date)
                for (line_id, _, quantity), po_line in zip(lines, po_preview.revision.lines, strict=True)]

    def planner(inp, ctx, s):
        header, lines, left, po_input, po_plan = build(s, ctx, inp)
        out = CoveragePurchaseOutput(purchase_order=po_plan.preview, links=preview_links(header, lines, po_plan.preview),
                                     left_for_other_vendors=left_rows(left), warnings=list(po_plan.preview.warnings))
        return Plan(out, dict(input=inp))

    def apply(plan, ctx, s):
        from bookflow.company import purchase_orders, schema as c
        from bookflow.core import audit, clock
        from bookflow.core.ids import new_id
        header, lines, left, po_input, po_plan = build(s, ctx, plan.data['input'])
        applied = purchase_orders.apply(po_plan, ctx, s)
        po = purchase_orders.resolve(s, applied.output.id)
        po_lines = purchase_orders.saved_lines(s, purchase_orders.revision(s, po))
        at, event = clock.now_iso(), new_id()
        rows = [dict(id=new_id(), work_document_id=header['id'], work_line_id=line_id, purchase_order_id=po['id'],
                     purchase_order_line_id=po_line['line_id'], quantity_microunits=quantity, audit_event_id=event,
                     created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
                for (line_id, _, quantity), po_line in zip(lines, po_lines, strict=True)]
        touched = [Touched('purchase_coverage_link', row['id'], 'create', None, 1, row, db='company') for row in rows]
        summary = f'{noun} purchase-order: purchase order {po["number"]} covers {header["number"]}'
        audit.write_event_to(s.company, ctx, f'{noun} purchase-order', summary, touched, actor_id=s.actor.id,
                             actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
        s.company.conn.execute(c.purchase_coverage_links.insert(), rows)
        shown = purchase_orders.show(s, type('Show', (), dict(purchase_order=po['id'], revision_number=None))())
        from bookflow.company.shortfall import document_coverage
        coverage = [entry for entries in document_coverage(s, header['id']).values() for entry in entries
                    if entry.purchase_order_id == po['id']]
        out = CoveragePurchaseOutput(purchase_order=PurchaseOrderWriteOutput(**shown.model_dump(), warnings=applied.output.warnings),
                                     links=coverage, left_for_other_vendors=left_rows(left),
                                     warnings=list(applied.output.warnings))
        return Applied(out, list(applied.touched) + touched, summary, audited=True)

    cmd = command(f'{noun} purchase-order', scope='company', description=DESCRIPTION.format(noun=noun.replace('-', ' ')),
                  input_model=model, output_model=CoveragePurchaseOutput, writes={'company'}, required_role='standard',
                  capability='ledger.post', accepts_idempotency_key=True, positional=[kind],
                  error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_WORK_DEPENDENCY', 'E_INACTIVE_REFERENCE',
                               'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_DUPLICATE_NUMBER'])(planner)
    cmd.resource_requirements = (('customer-work', 'standard'),)
    cmd.authorization = 'ledger.post (what purchase-order post requires) and customer-work standard role'
    cmd.applier(apply)
    return cmd


SHORTFALL_COMMANDS = [_register('sales_order', SalesOrderPurchaseOrderInput),
                      _register('work_order', WorkOrderPurchaseOrderInput)]
