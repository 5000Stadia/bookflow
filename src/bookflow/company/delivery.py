"""Document delivery, increment 1: drafting messages and the outbox (R112; design/document-delivery.md §2-§4).

**Drafting** reads the document as the drafting principal (so who may not read an invoice may not email it either),
describes it once with ``documents.model.build``, draws the PDF with ``documents.render.drawn`` -- the second half of
``render``, so the attached bytes are exactly what ``render`` gives -- and fills the kind's template from the same
description. The bytes are frozen in ``delivery_files`` with their source, revision, name, size and SHA-256.

**One live message per document.** A second draft of the same document, revision and recipients returns the first
while it is live; a different draft for a document already in the outbox, or one already sent, is refused naming
it, unless ``again`` makes a new message that names the earlier one (``resend_of``).

**Staleness** is derived, never stored as a flag: a message is stale while any source document was voided, its
current revision differs from the one drafted, or -- checked at release, export and ``delivery show`` -- the document
drawn now differs from the frozen PDF (a payment changes the balance an invoice prints without a new revision).
Releasing or exporting a stale message holds it instead (``document_changed`` or ``document_voided``); ``delivery
update --refresh`` freezes the document as it is now into the same message. Statements and notices are dated readings
and are never stale.

**Nothing leaves the machine.** No transport is registered in this build: ``send`` releases a draft to ``ready``,
where it waits. ``export`` gives the owner a ``.eml`` to send from their own mail program, and ``mark-sent`` (a
person's act) records that they did. Every act writes one ``delivery_events`` row in the same transaction.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from types import SimpleNamespace
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email import policy
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any
from urllib.parse import quote

import sqlalchemy as sa

from bookflow.company import delivery_merge as merge
from bookflow.company import delivery_models as m
from bookflow.company import schema as c
from bookflow.company.delivery_schema import FIRST_NUMBER, LIVE, PREFIX, SEQUENCE
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

RECORD = 'delivery_message'
TEMPLATE_RECORD = 'delivery_template'
EMAIL = re.compile(r'^[^@\s<>(),;:"\[\]\\]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
                   r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$')
SOURCE_LABEL = {'invoice': 'Invoice', 'sales-receipt': 'Sales receipt', 'estimate': 'Estimate',
                'statement': 'Statement', 'notice': 'Overdue notice'}
STATUS_LABEL = {'draft': 'Draft', 'sending': 'Sending', 'held': 'Needs attention', 'failed': 'Failed',
                'cancelled': 'Cancelled'}
HOLD_TEXT = {'document_changed': 'the document changed after this message was drafted',
             'document_voided': 'the document was voided after this message was drafted',
             'no_recipient': 'it has no To address', 'template_error': 'its template could not be filled',
             'outcome_unknown': 'an attempt to send it ended without an answer'}
NO_CONNECTION = ('No sending connection is set up, so nothing leaves this machine: the message waits in the '
                 'outbox. Export it (delivery export) to send it from your own mail program, then mark it sent.')
TABS = {'live': LIVE, 'draft': ('draft',), 'ready': ('ready',), 'waiting': ('ready',), 'sending': ('sending',),
        'sent': ('sent',), 'held': ('held',), 'failed': ('failed',), 'cancelled': ('cancelled',),
        'attention': ('held', 'failed'), 'all': None}


def _invalid(field_name, problem, **extra):
    return BookflowError('E_VALIDATION', message=f'{field_name}: {problem}',
                         details={'fields': [{'field': field_name, 'problem': problem}], **extra})


def _conflict(row, *, problem, next_step=None):
    details = {'record_type': RECORD, 'record_id': row['id'], 'number': row['number'], 'status': row['status'],
               'problem': problem}
    if next_step:
        details['next'] = next_step
    return BookflowError('E_WORK_DEPENDENCY', message=f"Message {row['number']} is {row['status']}: {problem}",
                         details=details)


# ---------------------------------------------------------------- reading as the principal

def reader(s, ctx):
    """`read(command, input, company_id)` running a registered read as the acting principal, inside this session,
    leaving the session as it was. Repeated reads within one act are answered once."""
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session, validate_input
    nested = ctx.model_copy(update={'idempotency_key': None, 'directive_id': None})
    seen: dict[str, Any] = {}

    def read(name, raw, company_id=None):
        key = name + json.dumps(raw, sort_keys=True, default=str)
        if key not in seen:
            command = registry.get(name)
            saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run)
            try:
                seen[key] = run_in_session(command, validate_input(command, raw), nested, s)
            finally:
                s.company_touched, s.hub_touched, s.warnings, s.dry_run = saved
        return seen[key]
    return read


@dataclass
class Source:
    kind: str
    id: str
    revision: str | None
    number: str | None
    customer_id: str
    record: dict
    date_from: str | None = None
    date_to: str | None = None
    identity: dict = field(default_factory=dict)

    @property
    def label(self):
        if self.kind == 'statement':
            return f'Statement {self.date_from} to {self.date_to}'
        if self.kind == 'notice':
            return f"Overdue notice {self.record.get('as_of')}"
        return f'{SOURCE_LABEL[self.kind]} {self.number}'


def resolve_source(read, company_id, inp) -> Source:
    kind = inp.kind
    if kind == 'statement':
        missing = [f for f in ('customer', 'date_from', 'date_to') if not getattr(inp, f)]
        if missing:
            raise BookflowError('E_VALIDATION', details={'fields': [
                {'field': f, 'problem': 'a statement is one customer over one period'} for f in missing]})
        if inp.date_from > inp.date_to:
            raise _invalid('date_from', 'is after date_to')
        customer = read('customer show', {'customer': inp.customer}, company_id)
        return Source('statement', customer['id'], None, None, customer['id'], customer, inp.date_from, inp.date_to,
                      {'customer': customer['id'], 'date_from': inp.date_from, 'date_to': inp.date_to})
    if not inp.document:
        raise _invalid('document', f'name the {SOURCE_LABEL[kind].lower()} (number or id)')
    if kind == 'notice':
        notice = read('notice show', {'notice': inp.document}, company_id)
        if notice['status'] != 'ready':
            raise _invalid('document', f"the overdue notice is {notice['status']}; only a notice ready to send can be "
                                       'emailed')
        return Source('notice', notice['id'], None, None, notice['customer_id'], notice,
                      identity={'document': notice['id']})
    noun = {'invoice': ('invoice show', 'invoice'), 'sales-receipt': ('sales-receipt show', 'sales_receipt'),
            'estimate': ('estimate show', 'estimate')}[kind]
    record = read(noun[0], {noun[1]: inp.document}, company_id)
    if record.get('status') == 'voided':
        raise _invalid('document', f"{SOURCE_LABEL[kind].lower()} {record.get('number')} is voided; a voided "
                                   'document is not emailed')
    return Source(kind, record['id'], record['current_revision_id'], record.get('number'), record['customer_id'],
                  record, identity={'document': record['id']})


def current_state(s, kind, source_id) -> tuple[str | None, bool]:
    """(current revision, voided) of a source, read straight from the books; (None, False) for dated readings."""
    if kind in ('invoice', 'sales-receipt'):
        t = c.transactions
        row = s.company.conn.execute(sa.select(t.c.current_revision_id, t.c.status).where(t.c.id == source_id)).first()
    elif kind == 'estimate':
        w = c.work_documents
        row = s.company.conn.execute(sa.select(w.c.current_revision_id, w.c.status).where(w.c.id == source_id)).first()
    else:
        return None, False
    if row is None:
        return None, True
    return row[0], row[1] == 'voided'


def staleness(s, docs) -> str | None:
    """document_voided, document_changed or None for a message's attached documents."""
    reason = None
    for doc in docs:
        if doc['role'] != 'document' or doc['source_kind'] in ('statement', 'notice'):
            continue
        revision, voided = current_state(s, doc['source_kind'], doc['source_id'])
        if voided:
            return 'document_voided'
        if revision != doc['source_revision']:
            reason = 'document_changed'
    return reason


def _identity(doc) -> dict:
    if doc['source_kind'] == 'statement':
        return {'customer': doc['source_id'], 'date_from': doc['statement_from'], 'date_to': doc['statement_to']}
    return {'document': doc['source_id']}


def drawn_now(read, company_id, doc):
    """The document's PDF as it would be drawn now, by the same path as drafting (and ``render``)."""
    from bookflow.documents import model
    from bookflow.documents.render import drawn
    return drawn(doc['source_kind'], model.build(read, company_id, doc['source_kind'], _identity(doc)))


def content_staleness(s, ctx, docs, read=None) -> str | None:
    """``staleness``, and then whether a document drawn now differs from its frozen PDF: what the customer would
    receive no longer matches the books even though the revision did not move (a payment, a credit applied)."""
    reason = staleness(s, docs)
    if reason:
        return reason
    read = read or reader(s, ctx)
    for doc in docs:
        if doc['role'] != 'document' or doc['source_kind'] in ('statement', 'notice'):
            continue
        content = drawn_now(read, s.company_row['id'], doc).content
        if hashlib.sha256(content).hexdigest() != doc['sha256']:
            return 'document_changed'
    return None


# A frozen file is kept while a standing message carries it: any message not cancelled, or one that was exported
# (the bytes left the machine in a file). Otherwise it is pruned; history keeps its SHA-256.
_KEPT = ("EXISTS (SELECT 1 FROM delivery_message_documents d JOIN delivery_messages m ON m.id = d.message_id "
         "WHERE d.sha256 = {sha} AND (m.status <> 'cancelled' OR EXISTS (SELECT 1 FROM delivery_events e "
         "WHERE e.message_id = m.id AND e.event = 'exported')))")


def prune_files(s, shas) -> list[str]:
    """Delete the frozen files among ``shas`` that no standing message carries; the ones deleted."""
    gone = []
    for sha in sorted(set(shas)):
        done = s.company.conn.exec_driver_sql(
            'DELETE FROM delivery_files WHERE sha256 = ? AND NOT ' + _KEPT.format(sha='delivery_files.sha256'), (sha,))
        if done.rowcount:
            gone.append(sha)
    return gone


def _exported(s, message_id) -> bool:
    e = c.delivery_events
    return s.company.conn.execute(sa.select(e.c.id).where(e.c.message_id == message_id,
                                                          e.c.event == 'exported').limit(1)).first() is not None


# ---------------------------------------------------------------- addresses and merge values

def addresses(field_name, values) -> list[str]:
    out = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        if len(text) > 254 or not EMAIL.match(text):
            raise _invalid(field_name, f'{text!r} is not an email address')
        if text.casefold() not in {x.casefold() for x in out}:
            out.append(text)
    return out


def _split(text):
    return [part for part in re.split(r'[,;\s]+', text or '') if part]


def default_recipients(read, company_id, customer, info):
    to = _split(customer.get('email'))
    cc = _split(customer.get('cc_email'))
    if not to and customer.get('parent_id'):
        parent = read('customer show', {'customer': customer['parent_id']}, company_id)
        to = _split(parent.get('email'))
        cc = cc or _split(parent.get('cc_email'))
    to = [v for v in to if EMAIL.match(v)]
    cc = [v for v in cc if EMAIL.match(v) and v.casefold() not in {x.casefold() for x in to}]
    bcc = [v for v in _split(info.get('delivery_copy_to')) if EMAIL.match(v)]
    return to, cc, bcc


def _decimal(text):
    try:
        return Decimal(str(text).replace(',', ''))
    except (InvalidOperation, TypeError):
        return None


def merge_values(s, src: Source, document, customer, company) -> dict[str, str | None]:
    facts = dict(document.facts)
    totals = {t.label: t.value for t in document.totals}
    company_name = company.get('display_name') or company.get('legal_name')
    human = s.actor is not None and s.actor.kind == 'human'
    values = {
        'company.name': company_name, 'company.phone': company.get('phone'), 'company.email': company.get('email'),
        'company.website': company.get('website'),
        'sender.name': (s.actor.display_name or s.actor.username) if human else company_name,
        'customer.name': customer.get('full_name') or customer.get('name'),
        'customer.first_name': customer.get('first_name'), 'customer.last_name': customer.get('last_name'),
        'customer.account_number': customer.get('account_number'),
    }
    if src.kind in ('invoice', 'sales-receipt', 'estimate'):
        values.update({
            'document.kind': SOURCE_LABEL[src.kind], 'document.number': document.number,
            'document.date': facts.get('Date'), 'document.due_date': facts.get('Due date'),
            'document.total': totals.get('Total'), 'document.balance_due': totals.get('Balance due'),
            'document.po_number': facts.get('P.O. number'), 'document.terms': facts.get('Terms')})
    elif src.kind == 'statement':
        overdue = None
        if document.grids:
            cells = dict(document.grids[0].cells)
            total, current = _decimal(cells.get('Total')), _decimal(cells.get('Current'))
            if total is not None and current is not None:
                places = len(str(cells['Total']).rpartition('.')[2]) if '.' in str(cells['Total']) else 0
                overdue = f'{total - current:.{places}f}'
        values.update({'statement.date_from': facts.get('Period from'), 'statement.date_to': facts.get('Statement date'),
                       'statement.balance': totals.get('Balance due'), 'statement.overdue': overdue})
    else:
        notice = src.record
        values.update({'notice.overdue': notice['overdue']['amount'], 'notice.oldest_due_date': notice['oldest_due_date'],
                       'notice.days_overdue': str(notice['days_overdue']), 'notice.letter': notice['letter']})
    return {k: (str(v) if v not in (None, '') else None) for k, v in values.items()}


# ---------------------------------------------------------------- rows

def _row(s, selector) -> dict:
    t = c.delivery_messages
    row = s.company.conn.execute(sa.select(t).where(sa.or_(t.c.id == selector, t.c.number == selector,
                                                           t.c.number == PREFIX + selector))).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': selector})
    return dict(row)


def _docs(s, message_ids) -> dict[str, list[dict]]:
    if not message_ids:
        return {}
    d = c.delivery_message_documents
    out: dict[str, list[dict]] = {}
    for row in s.company.conn.execute(sa.select(d).where(d.c.message_id.in_(list(message_ids)))
                                      .order_by(d.c.message_id, d.c.position)).mappings():
        out.setdefault(row['message_id'], []).append(dict(row))
    return out


def _names(s, ids) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    names = {r[0]: r[1] for r in s.company.conn.execute(
        sa.select(c.customers.c.id, c.customers.c.full_name).where(c.customers.c.id.in_(ids)))}
    names.update({r[0]: r[1] for r in s.company.conn.execute(
        sa.select(c.principals.c.user_id, c.principals.c.display_name).where(c.principals.c.user_id.in_(ids)))})
    return names


def _source_numbers(s, docs) -> dict[str, str]:
    numbers: dict[str, str] = {}
    by_kind: dict[str, set] = {}
    for doc in docs:
        by_kind.setdefault(doc['source_kind'], set()).add(doc['source_id'])
    if by_kind.get('invoice') or by_kind.get('sales-receipt'):
        ids = by_kind.get('invoice', set()) | by_kind.get('sales-receipt', set())
        numbers.update({r[0]: r[1] for r in s.company.conn.execute(
            sa.select(c.transactions.c.id, c.transactions.c.number).where(c.transactions.c.id.in_(ids)))})
    if by_kind.get('estimate'):
        numbers.update({r[0]: r[1] for r in s.company.conn.execute(
            sa.select(c.work_documents.c.id, c.work_documents.c.number).where(
                c.work_documents.c.id.in_(by_kind['estimate'])))})
    return numbers


def status_label(row) -> str:
    if row['status'] == 'sent':
        return 'Sent by hand' if row['sent_via'] == 'manual' else 'Sent'
    if row['status'] == 'ready':
        return 'Waiting for your go' if row['go_by'] is None else 'Ready to send'
    return STATUS_LABEL[row['status']]


def _doc_label(doc, numbers):
    if doc['source_kind'] == 'statement':
        return f"Statement {doc['statement_from']} to {doc['statement_to']}"
    if doc['source_kind'] == 'notice':
        return 'Overdue notice'
    return f"{SOURCE_LABEL[doc['source_kind']]} {numbers.get(doc['source_id']) or doc['source_id']}"


def project(s, row, docs=None, *, names=None, numbers=None, stale=None, warnings=(), existing=False,
            next_commands=None) -> m.MessageOutput:
    from bookflow.core.session import localize
    if docs is None:
        docs = _docs(s, [row['id']]).get(row['id'], [])
    if names is None:
        names = _names(s, [row['customer_id'], row['created_by'], row['updated_by']])
    if numbers is None:
        numbers = _source_numbers(s, docs)
    if stale is None:
        stale = staleness(s, docs) if row['status'] in LIVE else None
    company_id = (s.company_row or {}).get('id', '')
    template_name = None
    if row['template_id']:
        template_name = s.company.conn.execute(sa.select(c.delivery_templates.c.name).where(
            c.delivery_templates.c.id == row['template_id'])).scalar()
    resend = None
    if row['resend_of']:
        resend = s.company.conn.execute(sa.select(c.delivery_messages.c.number).where(
            c.delivery_messages.c.id == row['resend_of'])).scalar()
    present = None
    if row['status'] == 'cancelled' and docs:
        present = {r[0] for r in s.company.conn.execute(sa.select(c.delivery_files.c.sha256).where(
            c.delivery_files.c.sha256.in_([d['sha256'] for d in docs])))}
    attached = [m.AttachedOutput(
        position=d['position'], role=d['role'], source_kind=d['source_kind'], source_id=d['source_id'],
        source_number=numbers.get(d['source_id']), source_revision=d['source_revision'],
        statement_from=d['statement_from'], statement_to=d['statement_to'], file_name=d['file_name'],
        media_type=d['media_type'], size_bytes=d['size_bytes'], sha256=d['sha256'],
        pdf_path=(f"/c/{quote(str(company_id), safe='')}/delivery/{quote(row['id'], safe='')}/file/{d['position']}"
                  if row['id'] and (present is None or d['sha256'] in present) else None)) for d in docs]
    if next_commands is None:
        next_commands = _next(row)
    return m.MessageOutput(
        id=row['id'], version=row['version'], number=row['number'], kind=row['kind'], customer_id=row['customer_id'],
        customer_name=names.get(row['customer_id']) or row['customer_id'], status=row['status'],
        status_label=status_label(row), hold_reason=row['hold_reason'], from_name=row['from_name'],
        from_address=row['from_address'], reply_to=row['reply_to'], to=json.loads(row['to_addresses']),
        cc=json.loads(row['cc_addresses']), bcc=json.loads(row['bcc_addresses']), subject=row['subject'],
        body=row['body'], message_id=row['message_id'], template_id=row['template_id'], template_name=template_name,
        resend_of=resend, released_by=row['released_by'], released_at=row['released_at'], go_by=row['go_by'],
        go_at=row['go_at'], waiting_for_go=row['status'] == 'ready' and row['go_by'] is None,
        sent_at=row['sent_at'], sent_via=row['sent_via'], sent_method=row['sent_method'],
        delivery=tracking(s, row), cancelled_reason=row['cancelled_reason'],
        stale=bool(stale), stale_reason=stale, documents=attached,
        created_at=localize(s, row['created_at']), created_by=row['created_by'],
        updated_at=localize(s, row['updated_at']), updated_by=row['updated_by'], existing=existing,
        connection=_connection_name(s, row), attempts=row['attempts'], next_attempt_at=row['next_attempt_at'],
        failure_code=row['failure_code'], failure_text=row['failure_text'], provider_ref=row['provider_ref'],
        warnings=list(warnings), next_commands=next_commands)


def _connection_name(s, row) -> str:
    """The connection a message went through, or the one it would go through now; ``none`` when there is none."""
    from bookflow.company import delivery_connections as conns
    if row['connection_id']:
        name = s.company.conn.execute(sa.select(c.delivery_connections.c.name).where(
            c.delivery_connections.c.id == row['connection_id'])).scalar()
        if name:
            return name
    return conns.label(s) if row['status'] in LIVE else 'none'


def tracking(s, row) -> str | None:
    """A sent message's delivery, derived from its newest tracking event: unknown (hand-sent, or nothing said),
    accepted, delivered or bounced. Never 'opened'."""
    if row['status'] != 'sent':
        return None
    if row['sent_via'] != 'connection':
        return 'unknown'
    e = c.delivery_events
    newest = s.company.conn.execute(sa.select(e.c.event).where(
        e.c.message_id == row['id'], e.c.event.in_(('accepted', 'delivered', 'bounced')))
        .order_by(e.c.ordinal.desc()).limit(1)).scalar()
    return newest or 'unknown'


def _next(row) -> list[str]:
    n = row['number']
    if row['status'] == 'held' and row['hold_reason'] == 'outcome_unknown' or row['status'] == 'failed':
        return [f'delivery mark-sent {n}', f'delivery retry {n}', f'delivery cancel {n}']
    if row['status'] in ('draft', 'held'):
        return [f'delivery update {n}', f'delivery send {n}' if row['status'] == 'draft' else
                f'delivery update {n} --refresh', f'delivery cancel {n}']
    if row['status'] == 'ready' and row['go_by'] is None:
        return [f'delivery approve --messages \'["{n}"]\'', f'delivery export {n}', f'delivery cancel {n}']
    if row['status'] == 'ready':
        return [f'delivery export {n}', f'delivery mark-sent {n}', f'delivery cancel {n}']
    return [f'delivery history {n}']


def _event(s, ctx, message_id, event, status, details=None, *, at=None) -> dict:
    e = c.delivery_events
    ordinal = (s.company.conn.execute(sa.select(sa.func.max(e.c.ordinal)).where(e.c.message_id == message_id))
               .scalar() or 0) + 1
    row = dict(id=new_id(), message_id=message_id, ordinal=ordinal, event=event, status=status,
               details=json.dumps(details or {}, sort_keys=True), at=at or clock.now_iso(), actor_id=s.actor.id,
               actor_kind=s.actor.kind, via=ctx.interface.value)
    s.company.conn.execute(e.insert().values(**row))
    return row


def _audit_row(row):
    return {k: v for k, v in row.items() if k != 'content'}


# ---------------------------------------------------------------- templates (used by drafting)

def template_for(s, kind, selector=None) -> dict:
    t = c.delivery_templates
    q = sa.select(t).where(t.c.kind == kind)
    if selector:
        q = q.where(sa.or_(t.c.id == selector, t.c.name_key == selector.strip().casefold()))
    else:
        q = q.where(t.c.is_default.is_(True), t.c.active.is_(True))
    row = s.company.conn.execute(q).mappings().first()
    if row is None:
        if selector:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': TEMPLATE_RECORD, 'selector': selector,
                                                               'kind': kind})
        raise _invalid('template', f'no default template for {kind}; create one with delivery-template create')
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': TEMPLATE_RECORD, 'record_id': row['id']})
    return dict(row)


def compose(s, ctx, read, src: Source, template, *, subject=None, body=None):
    """Describe the document once; fill the template from it; draw the PDF from the same description."""
    from bookflow.company.info import read_info
    from bookflow.documents import model
    from bookflow.documents.render import drawn
    company_id = s.company_row['id']
    document = model.build(read, company_id, src.kind, src.identity)
    rendered = drawn(src.kind, document)
    customer = src.record if src.kind == 'statement' else read('customer show', {'customer': src.customer_id},
                                                                company_id)
    company = read('company show', {}, company_id)
    company = {**company['info'], 'display_name': company['display_name']}
    values = merge_values(s, src, document, customer, company)
    empty: set[str] = set()
    filled_subject = subject if subject is not None else merge.fill(template['subject'], src.kind, values, 'subject',
                                                                    empty)
    filled_body = body if body is not None else merge.fill(template['body'], src.kind, values, 'body', empty)
    filled_subject = ' '.join(filled_subject.split())[:500] or f'{src.label} from {values["company.name"] or ""}'
    warnings = [f'Merge field {{{{{name}}}}} has no value for this {SOURCE_LABEL[src.kind].lower()}; it is left '
                'empty.' for name in sorted(empty)]
    return rendered, customer, read_info(s.company), company, filled_subject, filled_body, warnings


def _dedupe(kind, customer_id, src: Source, to, cc, bcc, again_of=None) -> str:
    raw = json.dumps([kind, customer_id, [src.kind, src.id, src.revision, src.date_from, src.date_to],
                      sorted(x.casefold() for x in to), sorted(x.casefold() for x in cc),
                      sorted(x.casefold() for x in bcc), again_of], separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def messages_for_source(s, kind, source_id, date_from=None, date_to=None) -> list[dict]:
    d, t = c.delivery_message_documents, c.delivery_messages
    q = (sa.select(t).join(d, d.c.message_id == t.c.id)
         .where(d.c.role == 'document', d.c.source_kind == kind, d.c.source_id == source_id))
    if kind == 'statement':
        q = q.where(d.c.statement_from == date_from, d.c.statement_to == date_to)
    return [dict(r) for r in s.company.conn.execute(q.order_by(t.c.created_at.desc(), t.c.id.desc())).mappings()]


def _number(s):
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (FIRST_NUMBER, PREFIX)
    while s.company.conn.execute(sa.select(c.delivery_messages.c.id).where(
            c.delivery_messages.c.number == f'{prefix}{number}')).first():
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


# ---------------------------------------------------------------- draft

def prepare_draft(s, ctx, inp) -> Plan:
    read = reader(s, ctx)
    company_id = s.company_row['id']
    src = resolve_source(read, company_id, inp)
    template = template_for(s, src.kind, inp.template)
    customer = src.record if src.kind == 'statement' else read('customer show', {'customer': src.customer_id},
                                                                company_id)
    from bookflow.company.info import read_info
    info = read_info(s.company)
    to, cc, bcc = default_recipients(read, company_id, customer, info)
    if inp.to is not None:
        to = addresses('to', inp.to)
    if inp.cc is not None:
        cc = addresses('cc', inp.cc)
    if inp.bcc is not None:
        bcc = addresses('bcc', inp.bcc)
    earlier = [r for r in messages_for_source(s, src.kind, src.id, src.date_from, src.date_to)
               if r['status'] != 'cancelled']
    again_of = None
    if inp.again and earlier:
        # Again names the newest sent message, or else the original in the outbox -- never an earlier "again",
        # so asking twice returns the same new message rather than a chain of them.
        named = next((r for r in earlier if r['status'] == 'sent'), None) or next(
            (r for r in earlier if r['resend_of'] is None), earlier[-1])
        again_of = named['id']
    key = _dedupe(src.kind, src.customer_id, src, to, cc, bcc, again_of)
    same = next((r for r in earlier if r['dedupe_key'] == key and r['status'] in LIVE), None)
    if same is not None:
        return Plan(project(s, same, existing=True, warnings=[
            f"Message {same['number']} is already in the outbox for {src.label}; it is returned unchanged."]),
            dict(existing=True))
    if not inp.again:
        live = next((r for r in earlier if r['status'] in LIVE), None)
        if live is not None:
            n = live['number']
            if staleness(s, _docs(s, [live['id']]).get(live['id'], [])):
                raise _conflict(live, problem=f'{src.label} changed after this message was drafted', next_step=(
                    f'Attach it as it is now with `delivery update {n} --refresh` (the message keeps its number '
                    'and history), or cancel it.'))
            raise _conflict(live, problem=f'{src.label} is already in the outbox in this message', next_step=(
                f'Change it with `delivery update {n}` (`--refresh` attaches the document as it is now), cancel '
                'it, or draft with --again for a second message.'))
        sent = next((r for r in earlier if r['status'] == 'sent'), None)
        if sent is not None:
            raise _conflict(sent, problem=f'{src.label} was already sent in this message', next_step=(
                'Draft with --again to send it again; the new message names this one.'))
    rendered, customer, info, company, subject, body, warnings = compose(
        s, ctx, read, src, template, subject=inp.subject, body=inp.body)
    if not to:
        warnings.append('No To address: the customer has no email. Add one with `delivery update --to` before '
                        'releasing it.')
    at = clock.now_iso()
    number, sequence = _number(s)
    message_id = new_id()
    content = rendered.content
    sha = hashlib.sha256(content).hexdigest()
    row = dict(
        id=message_id, version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
        updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, number=number, kind=src.kind,
        customer_id=src.customer_id, status='draft', hold_reason=None,
        from_name=company.get('display_name') or company.get('legal_name'), from_address=company.get('email') or None,
        reply_to=info.get('delivery_reply_to') or None, to_addresses=json.dumps(to), cc_addresses=json.dumps(cc),
        bcc_addresses=json.dumps(bcc), subject=subject, body=body,
        message_id=f'<{message_id.lower()}@bookflow.invalid>', template_id=template['id'], dedupe_key=key,
        resend_of=again_of, released_by=None, released_at=None, go_by=None, go_at=None, attempts=0,
        next_attempt_at=None, lease_until=None, sent_at=None, sent_via=None, sent_method=None, connection_id=None,
        provider_ref=None, failure_code=None, failure_text=None, cancelled_reason=None)
    if from_check := row['from_address']:
        if not EMAIL.match(from_check):
            row['from_address'] = None
    doc = dict(id=new_id(), message_id=message_id, position=1, role='document', source_kind=src.kind,
               source_id=src.id, source_revision=src.revision, statement_from=src.date_from,
               statement_to=src.date_to, file_name=rendered.filename, media_type=rendered.media_type,
               size_bytes=len(content), sha256=sha, attachment_id=None, created_at=at, created_by=s.actor.id,
               created_via=ctx.interface.value)
    file_row = dict(sha256=sha, size_bytes=len(content), media_type=rendered.media_type, content=content,
                    created_at=at)
    numbers = {src.id: src.number} if src.number else {}
    preview = project(s, dict(row, id=None if s.dry_run else row['id']), [doc],
                      names={src.customer_id: customer.get('full_name') or customer.get('name')}, numbers=numbers,
                      stale=None, warnings=warnings,
                      next_commands=[f'delivery update {number}', f'delivery send {number}',
                                     f'delivery export {number}'])
    preview = preview.model_copy(update={'documents': [a.model_copy(update={'pdf_path': None}) if s.dry_run else a
                                                       for a in preview.documents]})
    return Plan(preview, dict(row=row, doc=doc, file=file_row, sequence=sequence,
                              details=dict(template=template['id'], template_name=template['name'],
                                           sources=[dict(kind=src.kind, id=src.id, revision=src.revision,
                                                         date_from=src.date_from, date_to=src.date_to)],
                                           warnings=warnings, again_of=again_of,
                                           custom=[k for k in ('subject', 'body') if getattr(inp, k) is not None])))


def apply_draft(plan, ctx, s) -> Applied:
    if plan.data.get('existing'):
        return Applied(plan.preview, [], f'returned message {plan.preview.number}, already in the outbox')
    from sqlalchemy.dialects.sqlite import insert
    conn = s.company.conn
    row, doc, file_row, sequence = plan.data['row'], plan.data['doc'], plan.data['file'], plan.data['sequence']
    conn.execute(insert(c.delivery_files).values(**file_row).on_conflict_do_nothing(index_elements=['sha256']))
    stmt = insert(c.sequences).values(**sequence)
    conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=sequence))
    conn.execute(c.delivery_messages.insert().values(**row))
    conn.execute(c.delivery_message_documents.insert().values(**doc))
    event = _event(s, ctx, row['id'], 'drafted', 'draft', plan.data['details'], at=row['created_at'])
    out = plan.preview.model_copy(update={'id': row['id']})
    return Applied(out, [Touched(RECORD, row['id'], 'create', None, 1, row, db='company'),
                         Touched('delivery_message_document', doc['id'], 'create', None, None, doc, db='company'),
                         Touched('delivery_event', event['id'], 'create', None, None, event, db='company')],
                   f"drafted message {row['number']} to {plan.preview.customer_name}: {row['subject']}")


# ---------------------------------------------------------------- single-message writes

def _check_version(row, expected):
    if expected is not None and row['version'] != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': row['id'], 'expected_version': expected,
            'current_version': row['version'], 'updated_by': row['updated_by'], 'updated_via': row['updated_via']})


def _touch(s, ctx, row, at=None):
    at = at or clock.now_iso()
    return dict(row, version=row['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)


def _write_row(s, before, after):
    t = c.delivery_messages
    done = s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': before['id']})


def _plan_single(s, ctx, before, after, event, details, summary, *, warnings=(), extra=None) -> Plan:
    out = project(s, after, warnings=warnings)
    return Plan(out, dict(before=before, after=after, event=event, details=details, summary=summary,
                          extra=extra or {}))


def apply_single(plan, ctx, s) -> Applied:
    before, after = plan.data['before'], plan.data['after']
    _write_row(s, before, after)
    event = _event(s, ctx, after['id'], plan.data['event'], after['status'], plan.data['details'],
                   at=after['updated_at'])
    touched = [Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                       db='company'),
               Touched('delivery_event', event['id'], 'create', None, None, event, db='company')]
    extra = plan.data['extra']
    if extra.get('refresh'):
        touched += _apply_refresh(s, after, extra['refresh'])
    if after['status'] == 'cancelled' and not _exported(s, after['id']):
        prune_files(s, [d['sha256'] for d in _docs(s, [after['id']]).get(after['id'], [])])
    if extra.get('notice_id'):
        from bookflow.company import notices
        touched += notices.mark_sent(s, ctx, extra['notice_id'], 'email').touched
    return Applied(project(s, after, warnings=plan.preview.warnings), touched, plan.data['summary'])


def prepare_update(s, ctx, inp) -> Plan:
    before = _row(s, inp.message)
    _check_version(before, inp.expected_version)
    if before['status'] not in ('draft', 'held'):
        raise _conflict(before, problem='only a draft or a held message can be edited', next_step=(
            f"Cancel it and draft again with --again." if before['status'] == 'sent' else None))
    after = _touch(s, ctx, before)
    changed = {}
    for name, column in (('to', 'to_addresses'), ('cc', 'cc_addresses'), ('bcc', 'bcc_addresses')):
        value = getattr(inp, name)
        if value is not None:
            new = json.dumps(addresses(name, value))
            if new != before[column]:
                after[column], changed[name] = new, json.loads(new)
    for name in ('subject', 'body'):
        value = getattr(inp, name)
        if value is not None:
            value = ' '.join(value.split()) if name == 'subject' else value
            if value != before[name]:
                after[name], changed[name] = value, True
    details, extra = {}, {}
    if inp.refresh:
        extra['refresh'], refreshed, refilled, kept = _plan_refresh(s, ctx, before, after, changed)
        if refreshed:
            changed['attachment'] = True
            details['refreshed'] = refreshed
        for name in refilled:
            changed[name] = True
        details.update(refilled=refilled, kept=kept)
        if not changed:
            raise _invalid('refresh', 'the document is drawn the same as when this message was drafted; there is '
                                      'nothing to refresh')
        if before['status'] == 'held' and before['hold_reason'] in ('document_changed', 'document_voided'):
            after.update(status='draft', hold_reason=None)
    if not changed:
        raise _invalid('message', 'nothing to change: give to, cc, bcc, subject or body, or refresh')
    if before['status'] == 'held' and before['hold_reason'] == 'no_recipient' and json.loads(after['to_addresses']):
        after.update(status='draft', hold_reason=None)
    drop_go(before, after, details)  # an edit or a refresh changes what a go was given for
    return _plan_single(s, ctx, before, after, 'edited', {'changed': sorted(changed), **details,
                        **{k: v for k, v in changed.items() if k in ('to', 'cc', 'bcc')}},
                        f"edited message {before['number']} ({', '.join(sorted(changed))})", extra=extra)


def drop_go(before, after, details=None) -> None:
    """A go is given for the exact frozen content and recipients it saw: it ends when the message is held, refreshed
    or edited, or released by an actor without the send authority. The event says whose go ended."""
    after.update(go_by=None, go_at=None)
    if before.get('go_by') and details is not None:
        details['go_withdrawn'] = before['go_by']


def _edited_fields(s, message_id) -> set[str]:
    """Subject and body the person wrote: given at draft, or changed by an edit (not by a refresh)."""
    e = c.delivery_events
    out: set[str] = set()
    for event, details in s.company.conn.execute(sa.select(e.c.event, e.c.details).where(
            e.c.message_id == message_id, e.c.event.in_(('drafted', 'edited')))):
        details = json.loads(details)
        if event == 'drafted':
            out.update(details.get('custom') or ())
        else:
            out.update(set(details.get('changed') or ()) - set(details.get('refilled') or ()))
    return out & {'subject', 'body'}


def _plan_refresh(s, ctx, before, after, changed):
    """Draw each document as it is now and refill the subject and body the person did not write. The message keeps
    its number, recipients and history; the frozen rows are rewritten while it is a draft or held, and the event
    keeps the old revision and SHA-256."""
    read = reader(s, ctx)
    company_id = s.company_row['id']
    docs = _docs(s, [before['id']]).get(before['id'], [])
    template = s.company.conn.execute(sa.select(c.delivery_templates).where(
        c.delivery_templates.c.id == before['template_id'])).mappings().first()
    updates, files, refreshed, src = [], [], [], None
    filled = {}
    for doc in docs:
        if doc['role'] != 'document':
            continue
        identity = _identity(doc)
        src = resolve_source(read, company_id, SimpleNamespace(
            kind=doc['source_kind'], document=identity.get('document'), customer=identity.get('customer'),
            date_from=identity.get('date_from'), date_to=identity.get('date_to')))
        if template is not None and not filled:
            rendered, *_, subject, body, _warn = compose(s, ctx, read, src, dict(template))
            filled = {'subject': subject, 'body': body}
        else:
            rendered = drawn_now(read, company_id, doc)
        sha = hashlib.sha256(rendered.content).hexdigest()
        if sha == doc['sha256'] and src.revision == doc['source_revision']:
            continue
        new = dict(doc, source_revision=src.revision, file_name=rendered.filename, media_type=rendered.media_type,
                   size_bytes=len(rendered.content), sha256=sha)
        updates.append((doc, new))
        files.append(dict(sha256=sha, size_bytes=len(rendered.content), media_type=rendered.media_type,
                          content=rendered.content, created_at=after['updated_at']))
        refreshed.append({'position': doc['position'], 'source_revision': [doc['source_revision'], src.revision],
                          'sha256': [doc['sha256'], sha]})
    kept = sorted(_edited_fields(s, before['id']) | {k for k in ('subject', 'body') if k in changed})
    refilled = []
    for name in ('subject', 'body'):
        if name in filled and name not in kept and filled[name] != after[name]:
            after[name] = filled[name]
            refilled.append(name)
    if src is not None and updates:
        after['dedupe_key'] = _dedupe(before['kind'], before['customer_id'], src, json.loads(after['to_addresses']),
                                      json.loads(after['cc_addresses']), json.loads(after['bcc_addresses']),
                                      before['resend_of'])
    plan = dict(updates=updates, files=files, prune=[] if _exported(s, before['id']) else
                [old['sha256'] for old, _ in updates])
    return plan, refreshed, refilled, kept


def _apply_refresh(s, after, plan) -> list:
    from sqlalchemy.dialects.sqlite import insert
    conn, d = s.company.conn, c.delivery_message_documents
    for file_row in plan['files']:
        conn.execute(insert(c.delivery_files).values(**file_row).on_conflict_do_nothing(index_elements=['sha256']))
    touched = []
    for old, new in plan['updates']:
        conn.execute(d.update().where(d.c.id == old['id']).values(
            **{k: new[k] for k in ('source_revision', 'file_name', 'media_type', 'size_bytes', 'sha256')}))
        touched.append(Touched('delivery_message_document', old['id'], 'update', None, None, new, old, db='company'))
    prune_files(s, plan['prune'])
    return touched


def prepare_cancel(s, ctx, inp) -> Plan:
    before = _row(s, inp.message)
    _check_version(before, inp.expected_version)
    if before['status'] in ('sent', 'cancelled'):
        raise _conflict(before, problem='a sent or cancelled message cannot be cancelled')
    reason = (ctx.reason or '').strip()
    if not reason:
        raise BookflowError('E_REASON_REQUIRED', message='Say why the message is cancelled (--reason).')
    after = dict(_touch(s, ctx, before), status='cancelled', hold_reason=None, cancelled_reason=reason[:500])
    return _plan_single(s, ctx, before, after, 'cancelled', {'reason': reason[:500], 'from': before['status']},
                        f"cancelled message {before['number']}")


def _sent_at(s, text):
    now = clock.now()
    if text is None:
        return clock.now_iso()
    try:
        if len(text) == 10:
            from zoneinfo import ZoneInfo
            day = datetime.fromisoformat(text)
            zone = ZoneInfo(s.company_tz) if s.company_tz else timezone.utc
            moment = day.replace(tzinfo=zone)
            if moment.date() == now.astimezone(zone).date():
                moment = now
        else:
            moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    except ValueError:
        raise _invalid('sent_at', 'give a date (2026-10-27) or an ISO date and time') from None
    if moment > now:
        raise _invalid('sent_at', 'is in the future')
    return moment.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def prepare_mark_sent(s, ctx, inp) -> Plan:
    before = _row(s, inp.message)
    _check_version(before, inp.expected_version)
    if before['status'] not in ('draft', 'ready', 'held', 'failed'):
        raise _conflict(before, problem='only an unsent message can be marked sent')
    sent_at = _sent_at(s, inp.sent_at)
    after = dict(_touch(s, ctx, before), status='sent', hold_reason=None, sent_at=sent_at, sent_via='manual',
                 sent_method=inp.method)
    extra = {}
    if before['kind'] == 'notice':
        doc = _docs(s, [before['id']]).get(before['id'], [])
        notice = next((d['source_id'] for d in doc if d['source_kind'] == 'notice'), None)
        status = s.company.conn.execute(sa.select(c.overdue_notices.c.status).where(
            c.overdue_notices.c.id == notice)).scalar() if notice else None
        if status == 'ready':
            extra['notice_id'] = notice
    return _plan_single(s, ctx, before, after, 'marked_sent', {'method': inp.method, 'sent_at': sent_at,
                        'from': before['status']},
                        f"marked message {before['number']} sent by hand ({inp.method})", extra=extra)


# ---------------------------------------------------------------- release

def prepare_send(s, ctx, inp) -> Plan:
    person = s.actor.kind == 'human'
    seen, plans, warnings = set(), [], []
    read = reader(s, ctx)
    at = clock.now_iso()
    for selector in inp.messages:
        before = _row(s, selector)
        if before['id'] in seen:
            continue
        seen.add(before['id'])
        if before['status'] != 'draft':
            raise _conflict(before, problem='only a draft can be released to the outbox')
        if not json.loads(before['to_addresses']):
            raise _invalid('to', f"message {before['number']} has no To address; add one with delivery update",
                           number=before['number'])
        stale = content_staleness(s, ctx, _docs(s, [before['id']]).get(before['id'], []), read)
        after = _touch(s, ctx, before, at)
        if stale:
            after.update(status='held', hold_reason=stale)
            held = {'reason': stale, 'at': 'release'}
            drop_go(before, after, held)
            plans.append((before, after, 'held', held))
            warnings.append(f"Message {before['number']} is held: {HOLD_TEXT[stale]}. "
                            + _refresh_hint(before['number'], stale))
            continue
        after.update(status='ready', released_by=s.actor.id, released_at=at)
        details = {'released_by': s.actor.id}
        if person:
            after.update(go_by=s.actor.id, go_at=at)
            details.update(go_by=s.actor.id, go_at=at)
        else:  # R187: an actor without delivery.send; whatever go the message carried does not survive its release
            drop_go(before, after, details)
        plans.append((before, after, 'released', details))
    if any(a['status'] == 'ready' for _, a, _, _ in plans):
        from bookflow.company import delivery_connections as conns
        connected = conns.label(s) != 'none'
        if not person:  # R187: an actor holding delivery.send releases with a go
            warnings.append(("" if connected else NO_CONNECTION + ' ') + "An agent's release waits for a person's go "
                            '(delivery approve) before anything could leave.')
        elif not connected:
            warnings.append(NO_CONNECTION)
    outs = [project(s, after) for _, after, _, _ in plans]
    return Plan(m.MessagesOutput(messages=outs, count=len(outs), warnings=warnings), dict(plans=plans))


def apply_send(plan, ctx, s) -> Applied:
    touched, outs = [], []
    for before, after, event_name, details in plan.data['plans']:
        _write_row(s, before, after)
        event = _event(s, ctx, after['id'], event_name, after['status'], details, at=after['updated_at'])
        touched += [Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                            db='company'),
                    Touched('delivery_event', event['id'], 'create', None, None, event, db='company')]
        outs.append(project(s, after))
    numbers = ', '.join(a['number'] for _, a, _, _ in plan.data['plans'])
    return Applied(m.MessagesOutput(messages=outs, count=len(outs), warnings=plan.preview.warnings), touched,
                   f'released message(s) {numbers}')


# ---------------------------------------------------------------- export

def _refresh_hint(number, reason):
    if reason == 'document_voided':
        return 'Cancel it: a voided document is not emailed.'
    return f'Attach the document as it is now with `delivery update {number} --refresh`, or cancel it.'


def eml_bytes(s, row, docs) -> bytes:
    msg = EmailMessage(policy=policy.SMTP)
    if row['from_address']:
        msg['From'] = formataddr((row['from_name'] or '', row['from_address']))
    to, cc, bcc = (json.loads(row[k]) for k in ('to_addresses', 'cc_addresses', 'bcc_addresses'))
    if to:
        msg['To'] = ', '.join(to)
    if cc:
        msg['Cc'] = ', '.join(cc)
    if bcc:
        msg['Bcc'] = ', '.join(bcc)
    if row['reply_to']:
        msg['Reply-To'] = row['reply_to']
    msg['Subject'] = row['subject']
    msg['Message-ID'] = row['message_id']
    msg['X-Unsent'] = '1'
    msg.set_content(row['body'])
    for doc in docs:
        content = file_bytes(s, doc['sha256'])
        maintype, _, subtype = doc['media_type'].partition('/')
        msg.add_attachment(content, maintype=maintype, subtype=subtype or 'octet-stream', filename=doc['file_name'])
    return msg.as_bytes()


def file_bytes(s, sha) -> bytes:
    content = s.company.conn.execute(sa.select(c.delivery_files.c.content).where(
        c.delivery_files.c.sha256 == sha)).scalar()
    if content is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'delivery_file', 'selector': sha})
    return bytes(content)


def prepare_export(s, ctx, inp) -> Plan:
    at = clock.now_iso()
    rows, seen = [], set()
    for selector in inp.messages:
        row = _row(s, selector)
        if row['id'] not in seen:
            seen.add(row['id'])
            rows.append(row)
    docs = _docs(s, [r['id'] for r in rows])
    files, holds, held, exported, warnings = [], [], [], [], []
    read = reader(s, ctx)
    for row in rows:
        if row['status'] == 'cancelled':
            raise _conflict(row, problem='a cancelled message is not exported')
        stale = content_staleness(s, ctx, docs.get(row['id'], []), read) if row['status'] in LIVE else None
        if stale:
            held.append(row['number'])
            # Asking again for a message already held for this reason holds nothing more.
            if not (row['status'] == 'held' and row['hold_reason'] == stale):
                holds.append((row, dict(_touch(s, ctx, row, at), status='held', hold_reason=stale, go_by=None,
                                        go_at=None)))
            warnings.append(f"Message {row['number']} is held, not exported: {HOLD_TEXT[stale]}. "
                            + _refresh_hint(row['number'], stale))
            continue
        files.append((f"message-{row['number']}.eml", eml_bytes(s, row, docs.get(row['id'], []))))
        exported.append(row)
    if len(files) == 1:
        filename, content, media = files[0][0], files[0][1], 'message/rfc822'
    elif files:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                archive.writestr(name, data)
        filename, content, media = 'messages.zip', buffer.getvalue(), 'application/zip'
    else:
        filename, content, media = 'messages.eml', b'', 'message/rfc822'
    if exported:
        warnings.append('Exporting sends nothing. Send the file from your mail program, then record it with '
                        '`delivery mark-sent`.')
    out = m.ExportOutput(filename=filename, media_type=media, size_bytes=len(content),
                         content_base64=base64.b64encode(content).decode('ascii') if files else None,
                         exported=[r['number'] for r in exported], held=held,
                         warnings=warnings)
    return Plan(out, dict(exported=exported, holds=holds, at=at, filename=filename,
                          sha=hashlib.sha256(content).hexdigest()))


def apply_export(plan, ctx, s) -> Applied:
    touched = []
    for before, after in plan.data['holds']:
        _write_row(s, before, after)
        event = _event(s, ctx, after['id'], 'held', 'held', {'reason': after['hold_reason'], 'at': 'export',
                                                             **({'go_withdrawn': before['go_by']} if before['go_by']
                                                                else {})}, at=after['updated_at'])
        touched += [Touched(RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                            db='company'),
                    Touched('delivery_event', event['id'], 'create', None, None, event, db='company')]
    for row in plan.data['exported']:
        event = _event(s, ctx, row['id'], 'exported', row['status'], {'file': plan.data['filename'],
                       'sha256': plan.data['sha']}, at=plan.data['at'])
        touched.append(Touched('delivery_event', event['id'], 'create', None, None, event, db='company'))
    numbers = ', '.join(plan.preview.exported) or 'nothing'
    return Applied(plan.preview, touched, f'exported {numbers}')


# ---------------------------------------------------------------- reads

def show(s, inp, ctx=None) -> m.MessageOutput:
    row = _row(s, inp.message)
    include = getattr(inp, 'include_files', False)
    stale = None
    if ctx is not None and not include and row['status'] in LIVE:
        stale = content_staleness(s, ctx, _docs(s, [row['id']]).get(row['id'], []))
    out = project(s, row, stale=stale)
    if include:
        out = out.model_copy(update={'documents': [
            doc.model_copy(update={'content_base64': base64.b64encode(file_bytes(s, doc.sha256)).decode('ascii')
                                   if doc.pdf_path else None})
            for doc in out.documents]})
    return out


def history(s, inp) -> m.HistoryOutput:
    from bookflow.core.session import localize
    row = _row(s, inp.message)
    e = c.delivery_events
    events = [dict(r) for r in s.company.conn.execute(sa.select(e).where(e.c.message_id == row['id'])
                                                       .order_by(e.c.ordinal)).mappings()]
    names = _names(s, [ev['actor_id'] for ev in events])
    return m.HistoryOutput(message_id=row['id'], number=row['number'], status=row['status'], events=[
        m.EventOutput(ordinal=ev['ordinal'], event=ev['event'], status=ev['status'], at=localize(s, ev['at']),
                      actor_id=ev['actor_id'], actor_name=names.get(ev['actor_id']), actor_kind=ev['actor_kind'],
                      via=ev['via'], details=json.loads(ev['details'])) for ev in events])


def counts(s) -> dict[str, int]:
    t = c.delivery_messages
    raw = {status: n for status, n in s.company.conn.execute(
        sa.select(t.c.status, sa.func.count()).group_by(t.c.status))}
    waiting = s.company.conn.execute(sa.select(sa.func.count()).where(
        t.c.status == 'ready', t.c.go_by.is_(None))).scalar()
    return {'waiting': waiting, 'ready': raw.get('ready', 0) - waiting, 'sent': raw.get('sent', 0),
            'attention': raw.get('held', 0) + raw.get('failed', 0), 'draft': raw.get('draft', 0),
            'cancelled': raw.get('cancelled', 0)}


def query(s, inp) -> m.MessagePageOutput:
    from bookflow.core.session import localize
    t, d = c.delivery_messages, c.delivery_message_documents
    q = sa.select(t)
    statuses = TABS[inp.status]
    if statuses is not None:
        q = q.where(t.c.status.in_(statuses))
    if inp.status == 'waiting':
        q = q.where(t.c.go_by.is_(None))
    if inp.status == 'ready':
        q = q.where(t.c.go_by.is_not(None))
    if inp.kind:
        q = q.where(t.c.kind == inp.kind)
    if inp.customer:
        from bookflow.company.parties import resolve_party
        q = q.where(t.c.customer_id == resolve_party(s.company, 'customer', inp.customer)['id'])
    if inp.source:
        q = q.where(sa.exists(sa.select(d.c.id).where(d.c.message_id == t.c.id, d.c.source_id == inp.source)))
    if inp.date_from:
        q = q.where(t.c.created_at >= inp.date_from)
    if inp.date_to:
        q = q.where(t.c.created_at < inp.date_to + 'T99')
    rows = [dict(r) for r in s.company.conn.execute(
        q.order_by(t.c.updated_at.desc(), t.c.id.desc()).limit(inp.limit + 1)).mappings()]
    truncated = len(rows) > inp.limit
    rows = rows[:inp.limit]
    docs = _docs(s, [r['id'] for r in rows])
    every = [doc for group in docs.values() for doc in group]
    numbers = _source_numbers(s, every)
    names = _names(s, [r['customer_id'] for r in rows] + [r['updated_by'] for r in rows])
    out = []
    for row in rows:
        mine = docs.get(row['id'], [])
        stale = staleness(s, mine) if row['status'] in LIVE else None
        out.append(m.MessageRow(
            id=row['id'], number=row['number'], kind=row['kind'], customer_id=row['customer_id'],
            customer_name=names.get(row['customer_id']) or row['customer_id'], status=row['status'],
            status_label=status_label(row), hold_reason=row['hold_reason'], to=json.loads(row['to_addresses']),
            subject=row['subject'], documents=[_doc_label(doc, numbers) for doc in mine],
            source_ids=[doc['source_id'] for doc in mine], stale=bool(stale),
            waiting_for_go=row['status'] == 'ready' and row['go_by'] is None,
            updated_at=localize(s, row['updated_at']), updated_by=names.get(row['updated_by']) or row['updated_by'],
            sent_at=localize(s, row['sent_at']) if row['sent_at'] else None))
    from bookflow.company import delivery_connections as conns
    return m.MessagePageOutput(rows=out, count=len(out), truncated=truncated, counts=counts(s),
                               connection=conns.label(s))


def live_for_notice(s, notice_id) -> dict | None:
    """The live message carrying an overdue notice, if any: `notice mark-sent` and `dismiss` wait for it."""
    return next((r for r in messages_for_source(s, 'notice', notice_id) if r['status'] in LIVE), None)
