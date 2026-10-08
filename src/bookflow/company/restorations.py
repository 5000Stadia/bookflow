"""Restore a deleted document from its retained history, as a new document linked to it.

Deletion never erases: the deleted document keeps its header, every revision, its lines and
its number, and a balanced cancellation took its accounting out of the books. A restore
does not undo any of that. It reads the revision the deletion captured and posts a *new*
document through the family's own post writer, so every check an ordinary post meets --
the closing date, balance, active accounts, items and names, number uniqueness, stock on
hand, tax -- is met again here, now. The deleted document stays deleted and immutable; the
restored one is an ordinary document of its family.

The link between the two is one audit entry, ``transaction_restoration``, written inside
the restored document's own audit event and keyed by the deleted document's id. Audit
entries are append-only, so the link is as permanent as the deletion receipt, and the
audit trail shows the deletion and the restoration as two separate events. One deleted
document is restored at most once: asking again answers with the first restoration
(``idempotent_replay``) and posts nothing. A restored document deleted in its turn is
restored by restoring *it*.

What no longer fits is refused with a reason, never quietly changed: a date inside a closed
period (give an open ``date``), an account, item or name since made inactive, a tax rate
that would no longer give the same totals. A custom field whose definition has since been
retired is the one thing left out rather than refused, and the output says so in
``left_out``. A deleted document carried no live settlement -- deletion refuses while a
payment or credit still answers it -- so there is no settlement link to restore.

Restoring is a person's act. An agent is refused, whatever it holds. The command requires
the family's explicit Delete grant (whoever may remove the document may bring it back) and
ledger.post at standard, since it posts.
"""
import json

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.restoration_models import RestoreOutput
from bookflow.core import audit, clock
from bookflow.core.deletion_families import TOMBSTONE_TABLE, capability
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.hub import access

RECORD = 'transaction_restoration'
FAMILIES = ('journal_entry', 'invoice')


def refuse(field, problem, **details):
    return BookflowError('E_VALIDATION', message='This deleted document cannot be restored: ' + problem,
                         details={'fields': [{'field': field, 'problem': problem}], **details})


def admit(s, family):
    """People only, then the family's explicit Delete grant; dispatch has checked ledger.post."""
    if getattr(s.actor, 'kind', None) != 'human':
        raise BookflowError('E_PERMISSION', message='Restoring a deleted document is a person\'s act; '
                            'an agent may not restore one.', details={'reason': 'people_only'})
    access.require_explicit_grant(s, capability(family))


def deletion(s, family, identity):
    """The retained deletion receipt of this document, or a refusal saying it is not deleted."""
    table = c.metadata.tables[TOMBSTONE_TABLE[family]]
    row = s.company.conn.execute(sa.select(table).where(table.c.transaction_id == identity)).mappings().first()
    if row is None:
        raise refuse(family, 'it is not deleted; only a deleted document is restored.', transaction_id=identity)
    if row['from_status'] != 'posted':
        raise refuse(family, 'it was already void when it was deleted, so it carried no amounts to restore.',
                     transaction_id=identity)
    return dict(row)


def restoration(s, identity):
    """The first restoration of this deleted document, from its audit entry, or None."""
    e, v = c.audit_entries, c.audit_events
    row = s.company.conn.execute(sa.select(e.c.after).join(v, v.c.id == e.c.event_id).where(
        e.c.record_type == RECORD, e.c.record_id == identity).order_by(v.c.seq).limit(1)).first()
    return audit.decode_snapshot(row[0]) if row is not None else None


def replay(found):
    return RestoreOutput.model_validate({k: found[k] for k in RestoreOutput.model_fields if k in found}).model_copy(
        update={'changed': False, 'idempotent_replay': True})


def revision(s, deleted):
    return dict(s.company.conn.execute(sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.id == deleted['revision_id'])).mappings().one())


def custom_values(s, rev, record_type):
    """The deleted revision's custom values as a post patch; retired definitions left out, named."""
    from bookflow.company.custom_fields import _applicable_definitions
    snapshot = json.loads(rev['custom_fields_snapshot'] or '{}')
    live = {row['id'] for row in _applicable_definitions(s.company.conn, record_type)}
    patch, left_out = {}, []
    for key, field in snapshot.items():
        if key in live:
            patch[key] = field.get('value')
        else:
            left_out.append(f'custom field "{field.get("name", key)}": its definition is no longer in use')
    return patch, left_out


def with_context(error, deleted_number):
    """A post refusal, said about the deleted document being restored."""
    details = dict(error.details or {})
    details.setdefault('restoring', deleted_number)
    if error.code == 'E_PERIOD_CLOSED':
        details.setdefault('next', 'Restore it at an open date with `date`.')
    error.details = details
    return error


def plan(s, ctx, family, header, inner, deleted, rev, left_out, total, currency):
    written = inner.data['header']
    output = RestoreOutput(family=family, deleted_id=header['id'], deleted_number=header['number'],
        deleted_revision_id=rev['id'], restored_id=written['id'], restored_number=written['number'],
        date=inner.data['pending']['transaction_revisions'][0]['date'], total_minor_units=total,
        currency=currency, left_out=left_out, warnings=list(inner.preview.warnings))
    return Plan(output, dict(family=family, inner=inner, deleted=deleted))


def persist(fresh, ctx, s, persist_inner, command_name):
    """Post the new document through its owner, then link it in the same audit event."""
    if fresh.data.get('replay'):
        return Applied(fresh.preview, [], 'Already restored')
    inner, output = fresh.data['inner'], fresh.preview
    applied = persist_inner(inner, ctx, s, command_name=command_name)
    at = clock.now_iso()
    link = dict(output.model_dump(mode='json', exclude={'dry_run', 'warnings', 'changed', 'idempotent_replay'}),
                restored_at=at, restored_by=s.actor.id, reason=ctx.reason,
                deletion_audit_event_id=fresh.data['deleted']['audit_event_id'])
    marker = Touched(RECORD, output.deleted_id, 'create', None, 1, link, db='company')
    s.company.conn.execute(c.audit_entries.insert().values(id=new_id(), event_id=inner.data['event'],
        record_type=RECORD, record_id=output.deleted_id, action='create', version_before=None,
        version_after=1, after=audit.encode_snapshot(link), before=None))
    result = output.model_copy(update={'restored_at': at, 'restored_by': s.actor.id})
    return Applied(result, [*applied.touched, marker], f'Restored {output.deleted_number} as {output.restored_number}',
                   audited=True)


# ---------------------------------------------------------------- journal entry

def prepare_journal(s, ctx, inp):
    from bookflow.company import journals
    from bookflow.company.journal_models import JournalPostInput
    admit(s, 'journal_entry')
    header = journals.resolve(s, inp.journal)
    deleted = deletion(s, 'journal_entry', header['id'])
    if (found := restoration(s, header['id'])) is not None:
        return Plan(replay(found), dict(replay=True))
    rev = revision(s, deleted)
    lines = journals.rows(s, c.document_lines, c.document_lines.c.revision_id == rev['id'],
                          order=c.document_lines.c.position)
    if any(line['kind'] != 'journal' for line in lines):
        raise refuse('journal', 'it carries lines that are not plain journal lines.')
    patch, left_out = custom_values(s, rev, 'journal_entry')
    entered = [journals.existing_input(line).model_dump(exclude={'line_id'}, exclude_none=True) for line in lines]
    post = JournalPostInput.model_validate(dict(date=inp.date or rev['date'], memo=rev['memo'], lines=entered,
        custom_fields=patch, **({'number': inp.number} if inp.number is not None else {})))
    try:
        inner = journals.prepare(s, ctx, post, 'post')
    except BookflowError as error:
        raise with_context(error, header['number']) from None
    return plan(s, ctx, 'journal_entry', header, inner, deleted, rev, left_out,
                inner.data['pending']['transaction_revisions'][0]['total_minor_units'], rev['currency'])


def apply_journal(plan_, ctx, s):
    from bookflow.company import journals
    fresh = prepare_journal(s, ctx, plan_.data['input'])
    fresh.data['input'] = plan_.data['input']
    return persist(fresh, ctx, s, journals.persist_prepared, 'journal restore')


# ---------------------------------------------------------------- invoice

def _money(minor, currency):
    return dict(minor_units=minor, currency=currency)


def _quantity(micro):
    whole, part = divmod(micro, 1_000_000)
    return str(whole) if not part else f'{whole}.{part:06d}'.rstrip('0')


def invoice_input(s, header, rev, inp):
    """The post input that re-enters the deleted invoice exactly, every default made explicit."""
    from bookflow.company import sales, sales_adjustments
    from bookflow.company.sales_facts import SalesProfile, SalesLineProfile
    from bookflow.company.sales_models import InvoicePostInput
    if s.company.conn.execute(sa.select(c.work_billing_allocations.c.id).where(
            c.work_billing_allocations.c.transaction_id == header['id']).limit(1)).first():
        raise refuse('invoice', 'it was billed from an estimate or work order; bill that work again instead.')
    profile = SalesProfile.model_validate_json(sales.profile_row(s, rev)['profile_snapshot'])
    currency = rev['currency']
    lines = []
    for line in sales.saved_lines(s, rev):
        facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
        if facts.group is not None:
            raise refuse('lines', f'line {line["position"]} came from group item "{facts.group.item.label}"; '
                         'restoring group lines is not supported yet.')
        if sales_adjustments.kind(facts) != 'item' or facts.pricing_basis == 'allocated':
            raise refuse('lines', f'line {line["position"]} is a subtotal, discount or charge line; '
                         'restoring those is not supported yet.')
        if line['unit_price_minor_units'] is not None and line['unit_price_minor_units'] < 0:
            raise refuse('lines', f'line {line["position"]} has a negative price.')
        value = dict(item=facts.item.id, quantity=_quantity(line['quantity_microunits']),
                     description=line['description'])
        if facts.unit is not None:
            value['unit'] = facts.unit.id
        if facts.pricing_basis == 'amount':
            value['net_amount'] = _money(facts.net_amount_minor_units, currency)
        elif line['unit_price_minor_units'] is not None:
            value['unit_price'] = _money(line['unit_price_minor_units'], currency)
        if facts.class_id is not None:
            value['class_id'] = facts.class_id.id
        if facts.tax_code is not None:
            value['tax_code'] = facts.tax_code.id
        lines.append(value)
    ref = lambda name: (getattr(profile, name).id if getattr(profile, name) is not None else None)
    address = lambda value: value.model_dump() if value is not None else None
    fields = dict(customer=profile.customer.id, date=inp.date or rev['date'], memo=rev['memo'], lines=lines,
        ar_account=profile.control_account.id, terms=ref('terms'), due_date=profile.due_date,
        customer_message=profile.customer_message, customer_message_item=ref('customer_message_item'),
        customer_purchase_order=profile.customer_purchase_order,
        billing_address=address(profile.billing_address), shipping_address=address(profile.shipping_address),
        shipping_address_id=profile.shipping_address_id, ship_date=profile.ship_date,
        ship_method=ref('ship_method'), sales_rep=ref('sales_rep'), class_id=ref('class_id'),
        customer_tax_code=ref('customer_tax_code'), sales_tax_item=ref('sales_tax_item'),
        price_level=ref('price_level'), sales_tax_calculation=profile.sales_tax_calculation,
        number=inp.number)
    if inp.date and inp.date != rev['date'] and profile.terms is not None:
        # A new date with terms recomputes the due date from those terms, as a new invoice would.
        fields['due_date'] = None
    patch, left_out = custom_values(s, rev, 'invoice')
    fields = {k: v for k, v in fields.items() if v is not None}
    return InvoicePostInput.model_validate(dict(fields, custom_fields=patch)), left_out


def prepare_invoice(s, ctx, inp):
    from bookflow.company import sales
    admit(s, 'invoice')
    header = sales.resolve(s, inp.invoice, 'invoice')
    deleted = deletion(s, 'invoice', header['id'])
    if (found := restoration(s, header['id'])) is not None:
        return Plan(replay(found), dict(replay=True))
    rev = revision(s, deleted)
    post, left_out = invoice_input(s, header, rev, inp)
    try:
        inner = sales.prepare(s, ctx, post, 'invoice', 'post')
    except BookflowError as error:
        raise with_context(error, header['number']) from None
    before = sales.profile_row(s, rev)
    after = inner.data['pending']['sales_profiles'][0]
    total = inner.data['pending']['transaction_revisions'][0]['total_minor_units']
    if (after['subtotal_minor_units'], after['tax_minor_units'], total) != (
            before['subtotal_minor_units'], before['tax_minor_units'], rev['total_minor_units']):
        raise refuse('invoice', 'posted today it would not come to the same amounts (a price, tax code or '
                     'tax rate it used has changed since), so it would not be the invoice that was deleted.',
                     deleted={'subtotal_minor_units': before['subtotal_minor_units'],
                              'tax_minor_units': before['tax_minor_units'], 'total_minor_units': rev['total_minor_units']},
                     restored={'subtotal_minor_units': after['subtotal_minor_units'],
                               'tax_minor_units': after['tax_minor_units'], 'total_minor_units': total})
    return plan(s, ctx, 'invoice', header, inner, deleted, rev, left_out, total, rev['currency'])


def apply_invoice(plan_, ctx, s):
    from bookflow.company import sales
    fresh = prepare_invoice(s, ctx, plan_.data['input'])
    fresh.data['input'] = plan_.data['input']
    return persist(fresh, ctx, s, sales.persist_prepared, 'invoice restore')
