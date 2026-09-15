"""Time worked on a job, recorded as the one-line customer-work document it already is.

A stretch of recorded time is the same shape as a quoted line of work: a person, a job, a
service item, a quantity and a rate, billable or not. So it is stored as a customer-work
document of kind ``time_activity`` carrying exactly one line, rather than as a second kind of
record with a second way of being billed. What that buys, and why it is not a coincidence:

* the duration is the line's ``quantity_microunits`` -- hours in millionths, the quantity
  convention already in the table -- and the charge is that quantity extended by a rate per
  hour, through the same exact arithmetic a quoted line uses;
* billing runs through ``work_billing_allocations``, the interval ledger that already records
  which part of a line has been invoiced. That ledger, not a flag on the time entry, is what
  makes a stretch of time billable exactly once: an hour already carried onto an invoice is an
  occupied span, and the second attempt finds no free span to take;
* ``report unbilled-costs`` lists it because it lists every billable work line that is not
  finished, and billed labour reaches ``report profit-and-loss-by-job`` because the invoice it
  becomes carries the job on its revenue lines like any other sale.

Time posts nothing on its own. Recording it moves no money; billing it does, through the
invoice path that already owns the accounting.

What a caller types is deliberately much smaller than a work order's input -- who, for whom,
when, how long, as what, and whether it can be billed. This module is the translation between
that and the work document it is, so the small surface is the only thing the command contract
has to keep small, and every rule about versions, revisions, reasons and billing dependencies
stays in ``work`` where the other kinds already obey it.
"""
from __future__ import annotations

from bookflow.company import work
from bookflow.company.parties import resolve_party
from bookflow.company.sales_models import _invalid
from bookflow.core.errors import BookflowError
from bookflow.company.work_models import (
    WorkLineInput, _TimeActivityWorkCreateInput, _TimeActivityWorkUpdateInput,
)

KIND = 'time_activity'

# What the caller's small input calls a thing, and what the work line already calls it. Written
# once so the translation and anything reading it back cannot disagree about a name. `rate` is
# absent because it is not a plain rename: see `_line`.
LINE_FIELDS = (('duration', 'quantity'), ('item', 'item'), ('note', 'description'),
               ('billable', 'billable'))


def _title(s, employee_selector):
    """A recorded entry's headline: whose time it was.

    Derived rather than typed, because a time entry has no name of its own -- the two things
    that identify one in a list are the person and the date, and the date is already a column.
    """
    row = resolve_party(s.company, 'employee', employee_selector)
    return ('Time — ' + str(row['name']))[:200]


def _line(inp, previous=None):
    """The single work line this time entry is, from whichever fields the caller supplied.

    A correction names only what moves; everything it does not name is read back off the
    saved line, which is also what keeps this a revision of the same recorded time rather
    than a second entry standing next to it.
    """
    supplied = {}
    for name, target in LINE_FIELDS:
        if name in inp.model_fields_set:
            supplied[target] = getattr(inp, name)
    if 'rate' in inp.model_fields_set:
        # An explicit rate prices the hour; an explicitly cleared one is not an absent price,
        # it is the instruction to charge the service item's own rate again.
        if inp.rate is None:
            supplied['use_defaults'] = ['unit_price']
        else:
            supplied['unit_price'] = inp.rate
    if previous is not None:
        supplied['line_id'] = previous['line_id']
        supplied.setdefault('item', previous['item_id'])
    return WorkLineInput(**supplied)


def _translated(s, inp, operation):
    """The customer-work input this time entry is."""
    if operation == 'void':
        return inp
    fields = {}
    for name in ('date', 'customer', 'number', 'class_id', 'custom_fields', 'custom_field_kinds',
                 'expected_facts_fingerprint'):
        if name in inp.model_fields_set:
            fields[name] = getattr(inp, name)
    if operation == 'create':
        return _TimeActivityWorkCreateInput(status='recorded', title=_title(s, inp.employee),
            assignees=[inp.employee], lines=[_line(inp)], **fields)
    header = work.resolve(s, inp.time_activity, KIND)
    saved = work.saved_lines(s, work.revision(s, header))
    if len(saved) != 1:
        raise _invalid('time_activity', 'this recorded time does not carry exactly one line')
    values = dict(time_activity=inp.time_activity, expected_version=inp.expected_version,
                  status='recorded', lines=[_line(inp, saved[0])], **fields)
    if 'employee' in inp.model_fields_set:
        values.update(assignees=[inp.employee], title=_title(s, inp.employee))
    return _TimeActivityWorkUpdateInput(**values)


def prepare(s, ctx, inp, operation):
    """Plan a recorded-time write as the customer-work write it is."""
    plan = work.prepare(s, ctx, _translated(s, inp, operation), KIND, operation)
    if operation == 'update' and plan.data['changed']:
        # A correction to recorded time always says why, the way `vendor-credit update` and
        # `customer-refund update` do: somebody's hours are being restated after the fact, and
        # what was already billed from them was billed on the old number. Asked only once the
        # plan says something actually moves, so a patch that changes nothing is still a no-op
        # rather than a demand for a reason to do nothing.
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        if len(ctx.reason.strip()) > 140:
            raise _invalid('reason', 'must be at most 140 characters')
    return plan


def show(s, inp, ctx=None):
    return work.show(s, inp, KIND, ctx)


def page(s, ctx, inp, *, history=False):
    return work.page(s, ctx, inp, KIND, history=history)
