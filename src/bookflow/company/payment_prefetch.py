"""One receipt applied to hundreds of invoices, or one invoice settled by hundreds of
receipts, reads their settlement facts in bulk.

Each invoice a receipt settles used to cost a dozen indexed reads (the document, its revision,
profile and active applications, then its lines, settlement keys, posting sources, tax
components and live allocations), so a receipt over 403 invoices spent most of its time asking
403 times. Inside `reading` a preparation that names many invoices fetches each of those
tables once per 400 invoices and answers the per-invoice questions from what it fetched.

The rows are exactly the ones the per-invoice reads return, and a preparation that names fewer
than `MINIMUM` invoices reads them one at a time as before. The scope writes nothing and drops
everything it fetched if the company connection is written to while it is open.
"""
from collections import defaultdict
from contextlib import contextmanager

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.aliases import alias
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES

MINIMUM = 8
CHUNK = 400


@contextmanager
def reading(s):
    if getattr(s, '_invoice_reads', None) is not None:
        yield
        return
    s._invoice_reads = {}
    try:
        yield
    finally:
        s._invoice_reads = None


def _reads(s):
    reads = getattr(s, '_invoice_reads', None)
    if reads is None:
        return None
    changes = s.company.raw.total_changes
    if reads.get('changes') != changes:
        reads.clear()
        reads.update(changes=changes, invoices={}, receipts={}, headers={}, customers={})
    return reads


def _chunks(values):
    values = sorted(values)
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


def _group(conn, query_for, ids, key):
    groups = defaultdict(list)
    for chunk in _chunks(ids):
        for row in conn.execute(query_for(chunk)).mappings():
            groups[row[key]].append(dict(row))
    return groups


def invoices(s, identifiers):
    """Read the settlement facts of these invoices (stable ids) once, if there are enough to matter."""
    reads = _reads(s)
    wanted = {value.upper() for value in identifiers if isinstance(value, str)} - set(reads['invoices'] if reads else ())
    if reads is None or len(wanted) < MINIMUM:
        return
    conn = s.company.conn
    t, r = c.transactions, c.transaction_revisions
    headers = {}
    for chunk in _chunks(wanted):
        for row in conn.execute(sa.select(t).where(t.c.type.in_(SETTLEABLE_RECEIVABLE_TYPES), t.c.id.in_(chunk))).mappings():
            headers[row['id']] = dict(row)
    revision_ids = {row['current_revision_id'] for row in headers.values()}
    revisions = {}
    for chunk in _chunks(revision_ids):
        for row in conn.execute(sa.select(r).where(r.c.id.in_(chunk))).mappings():
            revisions[row['id']] = dict(row)
    complete = {identifier: row for identifier, row in headers.items() if row['current_revision_id'] in revisions}
    ids = list(complete)
    revision_ids = [complete[identifier]['current_revision_id'] for identifier in ids]
    profiles = _group(conn, lambda chunk: sa.select(c.sales_profiles).where(c.sales_profiles.c.revision_id.in_(chunk)),
                      revision_ids, 'revision_id')
    app, inverse = c.applications, alias(c.applications, 'inverse')
    applications = _group(conn, lambda chunk: sa.select(app).where(app.c.kind == 'apply', ~sa.exists(
        sa.select(inverse.c.id).where(inverse.c.reverses_application_id == app.c.id)),
        app.c.paid_transaction_id.in_(chunk)).order_by(app.c.effective_date, app.c.id), ids, 'paid_transaction_id')
    lines, line_profiles = c.document_lines, c.sales_line_profiles
    saved = _group(conn, lambda chunk: sa.select(lines, *(col for col in line_profiles.c if col.name not in lines.c)).join(
        line_profiles, line_profiles.c.document_line_id == lines.c.id).where(lines.c.revision_id.in_(chunk)).order_by(
        lines.c.position), revision_ids, 'revision_id')
    keys = _group(conn, lambda chunk: sa.select(c.settlement_line_keys).where(
        c.settlement_line_keys.c.transaction_id.in_(chunk)), ids, 'transaction_id')
    source, leg = c.posting_line_sources, c.posting_lines
    sources = defaultdict(list)
    for chunk in _chunks(revision_ids):
        for row in conn.execute(sa.select(source, leg.c.account_id, leg.c.debit_minor_units, leg.c.credit_minor_units).join(
                leg, leg.c.id == source.c.posting_line_id).where(source.c.revision_id.in_(chunk),
                source.c.reversed_source_id.is_(None))).mappings():
            sources[(row['transaction_id'], row['revision_id'])].append(dict(row))
    taxes = _group(conn, lambda chunk: sa.select(c.sales_tax_components).where(
        c.sales_tax_components.c.revision_id.in_(chunk)), revision_ids, 'revision_id')
    allocations, reversed_ = c.application_allocations, alias(c.application_allocations, 'inverse')
    live = _group(conn, lambda chunk: sa.select(allocations).where(allocations.c.target_transaction_id.in_(chunk),
        allocations.c.kind == 'allocation', ~sa.exists(sa.select(reversed_.c.id).where(
        reversed_.c.reverses_allocation_id == allocations.c.id))), ids, 'target_transaction_id')
    for identifier in ids:
        header = complete[identifier]
        revision = revisions[header['current_revision_id']]
        reads['invoices'][identifier] = dict(
            header=header, revision=revision, profile=profiles[revision['id']],
            applications=applications[identifier], lines=saved[revision['id']], keys=keys[identifier],
            sources=sources[(identifier, revision['id'])], taxes=taxes[revision['id']], live=live[identifier])


def invoice(s, selector):
    """The prefetched facts of the invoice a stable id names, else None."""
    reads = _reads(s)
    if reads is None or not isinstance(selector, str):
        return None
    return reads['invoices'].get(selector.upper())


def receipts(s, identifiers):
    """Read the settlement facts of these paying documents (stable ids) once, if there are enough to matter.

    Every kind of paying document gets its header here; the rest is read for receipts only, the
    facts `payment_queries.payment_facts` and `payments.discount_rows` ask of one receipt."""
    reads = _reads(s)
    wanted = {value.upper() for value in identifiers if isinstance(value, str)} - set(reads['headers'] if reads else ())
    if reads is None or len(wanted) < MINIMUM:
        return
    conn = s.company.conn
    t, r = c.transactions, c.transaction_revisions
    found = {}
    for chunk in _chunks(wanted):
        for row in conn.execute(sa.select(t).where(t.c.id.in_(chunk))).mappings():
            found[row['id']] = dict(row)
    pays = {identifier: row for identifier, row in found.items() if row['type'] == 'payment'}
    ids = list(pays)
    revisions = {}
    for chunk in _chunks({row['current_revision_id'] for row in pays.values()}):
        for row in conn.execute(sa.select(r).where(r.c.id.in_(chunk))).mappings():
            revisions[row['id']] = dict(row)
    revision_ids = [row['current_revision_id'] for row in pays.values() if row['current_revision_id'] in revisions]
    profiles = _group(conn, lambda chunk: sa.select(c.payment_profiles).where(c.payment_profiles.c.revision_id.in_(chunk)),
                      revision_ids, 'revision_id')
    components = _group(conn, lambda chunk: sa.select(c.payment_components).where(c.payment_components.c.revision_id.in_(chunk)),
                        revision_ids, 'revision_id')
    keys = _group(conn, lambda chunk: sa.select(c.payment_component_keys).where(
        c.payment_component_keys.c.transaction_id.in_(chunk)), ids, 'transaction_id')
    app, inverse = c.applications, alias(c.applications, 'inverse')
    applications = _group(conn, lambda chunk: sa.select(app).where(app.c.kind == 'apply', ~sa.exists(
        sa.select(inverse.c.id).where(inverse.c.reverses_application_id == app.c.id)),
        app.c.paying_transaction_id.in_(chunk)).order_by(app.c.effective_date, app.c.id), ids, 'paying_transaction_id')
    use, release = c.customer_refund_consumptions, alias(c.customer_refund_consumptions, 'release')
    key_ids = [row['id'] for rows in keys.values() for row in rows]
    consumed = _group(conn, lambda chunk: sa.select(use).where(use.c.kind == 'consume', ~sa.exists(
        sa.select(release.c.id).where(release.c.reverses_consumption_id == use.c.id)),
        use.c.payment_source_key_id.in_(chunk)), key_ids, 'payment_source_key_id')
    discounts = _group(conn, lambda chunk: sa.select(c.payment_discounts).where(
        c.payment_discounts.c.transaction_id.in_(chunk)), ids, 'transaction_id')
    reads['headers'].update(found)
    for identifier, header in pays.items():
        revision = revisions.get(header['current_revision_id'])
        if revision is None:
            continue
        own_keys = {row['id']: row for row in keys[identifier]}
        reads['receipts'][identifier] = dict(
            header=header, revision=revision, profile=profiles[revision['id']], components=components[revision['id']],
            keys=own_keys, applications=applications[identifier],
            consumptions=sorted((row for key in own_keys for row in consumed[key]), key=lambda row: row['id']) if own_keys else [],
            discounts={row['application_id']: row for row in discounts[identifier]})


def header(s, identifier):
    """A prefetched paying document's header (any kind), else None."""
    reads = _reads(s)
    if reads is None or not isinstance(identifier, str):
        return None
    return reads['headers'].get(identifier.upper())


def receipt(s, selector):
    """The prefetched facts of the receipt a stable id names, else None."""
    reads = _reads(s)
    if reads is None or not isinstance(selector, str):
        return None
    return reads['receipts'].get(selector.upper())


def customer(s, party_id, active, compute):
    """`compute()` once per (customer, active) inside the scope; a failure is never kept."""
    reads = _reads(s)
    if reads is None:
        return compute()
    key = (party_id, active)
    if key not in reads['customers']:
        reads['customers'][key] = compute()
    return reads['customers'][key]
