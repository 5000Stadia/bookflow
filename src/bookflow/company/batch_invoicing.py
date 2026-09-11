"""One batch of invoices: the same lines, one ordinary invoice per customer.

**Each invoice is an ordinary invoice, resolved for its own customer.** This module builds one
``InvoicePostInput`` per customer and hands it to ``sales.prepare``, which is the same function
``invoice post`` uses, so terms, tax code, tax item, sales rep, price level, class, billing
address and customer message are resolved from that customer by ``sales_defaults`` exactly as
they would be if a person had typed the invoice by hand. Nothing is resolved once and reused: a
price level in particular means the same line is a different amount on each invoice, and the
only way that comes out right is by resolving per customer, which is what happens here.
``batch_invoicing_models`` keeps a batch request from even having a place to put a shared term
or price level, so there is nothing to stamp.

**Partial failure is the normal case, not an exception.** A customer that is inactive, or whose
tax code has gone, or whose invoice would land in a closed period, must not take the rest of the
run down with it. Each invoice is written inside its own ``SAVEPOINT``: a refusal rolls that one
customer's rows back and the run continues, so what succeeded stays written. The outcome of
every customer -- the invoice it created, or the error code and message that refused it -- is
stored in ``invoice_batch_results`` next to its ``invoice_batches`` row, because a list of who
was missed has to outlive the response the user might have closed. ``batch-invoice retry`` reads
that list and runs the same request again for the failures alone.

**Numbers come from the allocator, and only on success.** ``sales.commercial`` calls
``document_effects.allocate``, and the sequence advances when the invoice is persisted, so a
refused customer consumes no number and the created invoices carry an unbroken run. A preview
writes nothing, so a preview has no numbers to show and shows none rather than showing one
number twenty times.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from bookflow.company import document_effects as effects, sales, schema as c
from bookflow.company.billing_groups import members as group_members, resolve_customer, resolve_group
from bookflow.company.sales_models import InvoicePostInput
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

SNAPSHOT_VERSION = 1
# One savepoint name, taken and released around every customer in turn. It is released or
# rolled back before the next customer takes it, so one name is enough and nesting never grows.
SAVEPOINT = 'bookflow_batch_customer'


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def _money(minor_units: int, currency: str) -> dict[str, Any]:
    return Money(minor_units, currency).to_dict()


def _home_currency(s) -> str:
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _targets(s, *, billing_group, customers, field: str):
    """The customers this run invoices, in order, with the group they came from."""
    if billing_group is not None:
        group = resolve_group(s, billing_group)
        rows = group_members(s, group['id'])
        if not rows:
            raise _invalid('billing_group', 'this billing group has no members; add customers before invoicing it')
        return group, [dict(id=row['customer_id'], full_name=row['full_name']) for row in rows]
    resolved, seen = [], set()
    for selector in customers:
        customer = resolve_customer(s, selector, field=field)
        if customer['id'] in seen:
            raise _invalid(field, 'name a customer once; one batch writes one invoice per customer')
        seen.add(customer['id'])
        resolved.append(dict(id=customer['id'], full_name=customer['full_name']))
    return None, resolved


def _request_snapshot(inp, *, date: str, customer_ids: list[str], group_id: str | None) -> dict[str, Any]:
    """The exact question this run was asked, so a retry can ask it again unchanged."""
    from bookflow.company.list_service import assert_no_floats
    snapshot = dict(v=SNAPSHOT_VERSION, date=date, billing_group_id=group_id,
                    customers=list(customer_ids), memo=inp.memo,
                    customer_message=inp.customer_message,
                    lines=[line.model_dump(mode='json', exclude_unset=True) for line in inp.lines])
    assert_no_floats(snapshot, path='request')
    return snapshot


def _invoice_input(snapshot: dict[str, Any], customer_id: str) -> InvoicePostInput:
    """One ordinary invoice request. Nothing commercial is carried in from the batch."""
    payload: dict[str, Any] = dict(date=snapshot['date'], customer=customer_id, lines=snapshot['lines'])
    if snapshot.get('memo') is not None:
        payload['memo'] = snapshot['memo']
    if snapshot.get('customer_message') is not None:
        payload['customer_message'] = snapshot['customer_message']
    return InvoicePostInput.model_validate(payload)


def _resolved_labels(preview) -> dict[str, Any]:
    """Terms and price level as this customer's own invoice resolved them.

    Read off the invoice's own captured profile rather than off the batch request, which has
    nowhere to carry either: these two columns are how a person sees, on the preview, that the
    customers were resolved separately.
    """
    profile = getattr(preview.revision, 'profile', None)

    def label(field):
        value = profile.get(field) if isinstance(profile, dict) else getattr(profile, field, None)
        if value is None:
            return None
        return value.get('label') if isinstance(value, dict) else getattr(value, 'label', None)

    return dict(terms=label('terms'), price_level=label('price_level'))


def _failure_row(position: int, customer: dict[str, Any], exc: BookflowError) -> dict[str, Any]:
    return dict(position=position, customer_id=customer['id'], customer_label=customer['full_name'],
                status='failed', error_code=exc.code, error_message=exc.message)


def preview_rows(s, ctx, snapshot: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve every customer's own invoice without writing anything.

    A refusal is a row, not an exception: the point of a preview is to show a person which
    customers would be missed before twenty invoices are written, not to stop at the first one.
    """
    rows = []
    for position, customer in enumerate(targets, 1):
        try:
            plan = sales.prepare(s, ctx, _invoice_input(snapshot, customer['id']), 'invoice', 'post')
        except BookflowError as exc:
            rows.append(_failure_row(position, customer, exc))
            continue
        preview = plan.preview
        rows.append(dict(position=position, customer_id=customer['id'], customer_label=preview.customer_name,
                         status='will_create', total=preview.total.model_dump(),
                         subtotal=preview.subtotal.model_dump(), tax=preview.tax.model_dump(),
                         due_date=preview.due_date, **_resolved_labels(preview)))
    return rows


def _write_one(s, ctx, snapshot, customer, position, *, command_name):
    """Write one customer's invoice inside its own savepoint; a refusal rolls back only it."""
    s.company.raw.execute(f'SAVEPOINT {SAVEPOINT}')
    try:
        plan = sales.prepare(s, ctx, _invoice_input(snapshot, customer['id']), 'invoice', 'post')
        applied = effects.persist(plan, ctx, s, command_name=command_name, table_kinds=sales.TABLE_KINDS)
    except BookflowError as exc:
        s.company.raw.execute(f'ROLLBACK TO {SAVEPOINT}')
        s.company.raw.execute(f'RELEASE {SAVEPOINT}')
        return _failure_row(position, customer, exc), []
    s.company.raw.execute(f'RELEASE {SAVEPOINT}')
    preview = applied.output
    row = dict(position=position, customer_id=customer['id'], customer_label=preview.customer_name,
               status='created', transaction_id=preview.id, number=preview.number,
               total=preview.total.model_dump(), subtotal=preview.subtotal.model_dump(),
               tax=preview.tax.model_dump(), due_date=preview.due_date, **_resolved_labels(preview))
    return row, applied.touched


def _batch_output(*, batch_id, snapshot, group, rows, currency, retry_of=None, created=None):
    """The output a real run returns. A preview is the same document, counted the same way.

    A previewed row is ``will_create`` rather than ``created`` because no invoice exists yet and
    no number has been allocated, but it counts toward the same totals: the whole point of the
    preview is to show the person what the run will do, in the shape the run will report it.
    """
    from bookflow.company.batch_invoicing_models import BatchInvoiceOutput, BatchRowOutput
    made = [row for row in rows if row['status'] in ('created', 'will_create')]
    total = sum(row['total']['minor_units'] for row in made)
    return BatchInvoiceOutput(
        batch_id=batch_id, date=snapshot['date'],
        billing_group_id=group['id'] if group else None,
        billing_group_name=group['name'] if group else None,
        retry_of_batch_id=retry_of,
        requested_count=len(rows), created_count=len(made),
        failed_count=len(rows) - len(made),
        created_total=_money(total, currency), currency=currency,
        batch_rows=[BatchRowOutput(**row) for row in rows],
        **(created or {}))


def _stored_rows(batch_id: str, rows: list[dict[str, Any]], *, at: str, actor: str, via: str):
    return [dict(id=new_id(), batch_id=batch_id, position=row['position'],
                 customer_id=row['customer_id'], customer_label=row['customer_label'],
                 status=row['status'],
                 transaction_id=row.get('transaction_id'), number=row.get('number'),
                 total_minor_units=row['total']['minor_units'] if row.get('total') else None,
                 currency=row['total']['currency'] if row.get('total') else None,
                 error_code=row.get('error_code'), error_message=row.get('error_message'),
                 created_at=at, created_by=actor, created_via=via)
            for row in rows]


def _persist(s, ctx, *, snapshot, group, rows, currency, retry_of, command_name) -> Applied:
    """Record the run itself, after its invoices are already written."""
    from bookflow.core import audit
    at, event = clock.now_iso(), new_id()
    made = [row for row in rows if row['status'] == 'created']
    batch = dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                 audit_event_id=event, date=snapshot['date'],
                 billing_group_id=group['id'] if group else None,
                 billing_group_name=group['name'] if group else None, retry_of_batch_id=retry_of,
                 requested_count=len(rows), created_count=len(made), failed_count=len(rows) - len(made),
                 created_total_minor_units=sum(row['total']['minor_units'] for row in made),
                 currency=currency, request_snapshot=json.dumps(snapshot, sort_keys=True, separators=(',', ':')))
    stored = _stored_rows(batch['id'], rows, at=at, actor=s.actor.id, via=ctx.interface.value)
    touched = [Touched('invoice_batch', batch['id'], 'create', None, 1, effects.decoded(batch), db='company')]
    touched += [Touched('invoice_batch_result', row['id'], 'create', None, 1, row, db='company')
                for row in stored]
    summary = (f"{command_name}: {batch['created_count']} invoiced, {batch['failed_count']} failed")
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None),
                         event_id=event)
    s.company.conn.execute(c.invoice_batches.insert().values(**batch))
    if stored:
        s.company.conn.execute(c.invoice_batch_results.insert(), stored)
    output = _batch_output(batch_id=batch['id'], snapshot=snapshot, group=group, rows=rows,
                           currency=currency, retry_of=retry_of,
                           created=dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value))
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------------------- post

def prepare_post(s, ctx, inp) -> Plan:
    group, targets = _targets(s, billing_group=inp.billing_group, customers=inp.customers, field='customers')
    snapshot = _request_snapshot(inp, date=inp.date, customer_ids=[row['id'] for row in targets],
                                 group_id=group['id'] if group else None)
    currency = _home_currency(s)
    rows = preview_rows(s, ctx, snapshot, targets)
    return Plan(_batch_output(batch_id=None, snapshot=snapshot, group=group, rows=rows, currency=currency),
                dict(snapshot=snapshot, group=group, targets=targets, currency=currency, retry_of=None))


def apply_post(plan: Plan, ctx, s) -> Applied:
    return _run(plan, ctx, s, command_name='batch-invoice post')


def _run(plan: Plan, ctx, s, *, command_name) -> Applied:
    """Re-resolve and write, one customer at a time, each in its own savepoint."""
    data = plan.data
    snapshot, group = data['snapshot'], data['group']
    # Re-read the customers from the recorded request rather than trusting the plan's own list,
    # so what is written is what was asked for and not what a stale preview happened to hold.
    targets = [dict(id=identifier, full_name=resolve_customer(s, identifier)['full_name'])
               for identifier in snapshot['customers']]
    currency = _home_currency(s)
    rows = []
    for position, customer in enumerate(targets, 1):
        row, _touched = _write_one(s, ctx, snapshot, customer, position, command_name=command_name)
        rows.append(row)
    return _persist(s, ctx, snapshot=snapshot, group=group, rows=rows, currency=currency,
                    retry_of=data['retry_of'], command_name=command_name)


# ---------------------------------------------------------------------------- retry

def resolve_batch(s, selector: Any) -> dict[str, Any]:
    trimmed = selector.strip() if isinstance(selector, str) else selector
    row = None
    if isinstance(trimmed, str) and is_ulid(trimmed):
        row = s.company.conn.execute(sa.select(c.invoice_batches).where(
            c.invoice_batches.c.id == normalize_ulid(trimmed))).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'invoice_batch', 'selector': selector})
    return dict(row)


def batch_results(s, batch_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in s.company.conn.execute(
        sa.select(c.invoice_batch_results).where(c.invoice_batch_results.c.batch_id == batch_id)
        .order_by(c.invoice_batch_results.c.position, c.invoice_batch_results.c.id)).mappings()]


def prepare_retry(s, ctx, inp) -> Plan:
    batch = resolve_batch(s, inp.batch)
    failed = [row for row in batch_results(s, batch['id']) if row['status'] == 'failed']
    if not failed:
        raise _invalid('batch', 'every customer in this batch was invoiced; there is nothing to retry')
    original = json.loads(batch['request_snapshot'])
    snapshot = dict(original, date=inp.date or batch['date'],
                    customers=[row['customer_id'] for row in failed])
    targets = [dict(id=row['customer_id'],
                    full_name=resolve_customer(s, row['customer_id'])['full_name']) for row in failed]
    # The group comes off the recorded batch, not out of the list: a retry has to work after
    # the group it was addressed to has been renamed or deleted.
    group = None
    if batch['billing_group_id'] is not None:
        group = dict(id=batch['billing_group_id'], name=batch['billing_group_name'])
    currency = _home_currency(s)
    rows = preview_rows(s, ctx, snapshot, targets)
    return Plan(_batch_output(batch_id=None, snapshot=snapshot, group=group, rows=rows,
                              currency=currency, retry_of=batch['id']),
                dict(snapshot=snapshot, group=group, targets=targets, currency=currency,
                     retry_of=batch['id']))


def apply_retry(plan: Plan, ctx, s) -> Applied:
    return _run(plan, ctx, s, command_name='batch-invoice retry')


# ---------------------------------------------------------------------------- reads

def _row_output(row: dict[str, Any]):
    from bookflow.company.batch_invoicing_models import BatchRowOutput
    total = (_money(row['total_minor_units'], row['currency'])
             if row['total_minor_units'] is not None else None)
    return BatchRowOutput(position=row['position'], customer_id=row['customer_id'],
                          customer_label=row['customer_label'], status=row['status'],
                          transaction_id=row['transaction_id'], number=row['number'], total=total,
                          error_code=row['error_code'], error_message=row['error_message'])


def _summary_fields(s, batch: dict[str, Any]) -> dict[str, Any]:
    # The group's name is read off the batch, not off the group: the batch is history and has to
    # keep reading back after the group it was addressed to has been deleted or renamed.
    return dict(batch_id=batch['id'], date=batch['date'], billing_group_id=batch['billing_group_id'],
                billing_group_name=batch['billing_group_name'],
                retry_of_batch_id=batch['retry_of_batch_id'],
                requested_count=batch['requested_count'], created_count=batch['created_count'],
                failed_count=batch['failed_count'],
                created_total=_money(batch['created_total_minor_units'], batch['currency']),
                currency=batch['currency'], created_at=batch['created_at'],
                created_by=batch['created_by'], created_via=batch['created_via'])


def show(s, inp):
    from bookflow.company.batch_invoicing_models import BatchShowOutput
    batch = resolve_batch(s, inp.batch)
    return BatchShowOutput(**_summary_fields(s, batch),
                           batch_rows=[_row_output(row) for row in batch_results(s, batch['id'])])


def page(s, inp):
    from bookflow.company.batch_invoicing_models import BatchPageOutput, BatchSummaryOutput
    table = c.invoice_batches
    query = sa.select(table)
    if inp.billing_group is not None:
        query = query.where(table.c.billing_group_id == resolve_group(s, inp.billing_group)['id'])
    if inp.date_from is not None:
        query = query.where(table.c.date >= inp.date_from)
    if inp.date_to is not None:
        query = query.where(table.c.date <= inp.date_to)
    if inp.cursor:
        query = query.where(table.c.id < inp.cursor)
    rows = [dict(row) for row in s.company.conn.execute(
        query.order_by(table.c.id.desc()).limit(inp.limit + 1)).mappings()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    return BatchPageOutput(items=[BatchSummaryOutput(**_summary_fields(s, row)) for row in rows],
                           count=len(rows), next_cursor=rows[-1]['id'] if more and rows else None)
