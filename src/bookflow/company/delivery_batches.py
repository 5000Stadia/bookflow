"""Document delivery, increment 3: *Email later*, the customer's method and batches (R112;
design/document-delivery.md §5).

**To be emailed is derived, never narrated.** An invoice, sales receipt or estimate is to be emailed while it is not
voided, carries no live or sent message, and either a person flagged it *Email later* (``delivery_flags.wanted``), or
the company's auto-flag date is set, the customer's effective method (a job inherits its parent's) is ``email`` and the
document is dated on or after that date. A person's explicit ``unflag`` wins over the date. ``queue`` lists them; with
``method mail`` it lists only the customers who prefer mail.

**A batch** drafts many messages in one act. The selection is resolved once when the batch begins: the messages to
draft (``targets``) and who is skipped, with why (no address, prefers mail, not an email customer, opted out, already
in the outbox, already sent, left out). Drafting goes ``CHUNK`` messages per call, each through ``delivery draft``'s own
planner, so a batch never holds the writer for its whole length; the answer names the call that continues it
(``next``). A target that gained a message meanwhile is skipped then, naming it, so a rerun duplicates nothing.

**A batch is cancelled as one unit:** every message it drafted that was never released or exported is cancelled, and
the ones that block (released, exported, sent) are named. Every message names its batch (``batch_id``), so the outbox
filtered to a batch is the anchor's *Send forms*. R186 can carry this onto the shared batch ledger: a delivery batch is
one entry there, its reversal this cancel, and nothing later depends on a delivery batch's drafts.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

import sqlalchemy as sa

from bookflow.company import delivery as d
from bookflow.company import delivery_batch_models as bm
from bookflow.company import schema as c
from bookflow.company.delivery_schema import LIVE
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

RECORD = 'delivery_batch'
FLAG_RECORD = 'delivery_flag'
# Messages drafted per call, set from the measurement (design, As built: increment 3): on 1,000 invoices a chunk of
# 10 held the writer 0.88 s (median; 2.53 s at most) and 200 statements 1.99 s, so a chunk stays near two seconds.
CHUNK = 10
SEQUENCE, PREFIX, FIRST = 'delivery-batch', 'B-', 1001
DOCUMENT_KINDS = ('invoice', 'sales-receipt', 'estimate')
_TXN = {'invoice': 'invoice', 'sales-receipt': 'sales_receipt'}
SKIP_TEXT = {
    'no_address': 'the customer has no email address',
    'prefers_mail': 'the customer prefers mail',
    'not_email': "the customer's delivery method is not email",
    'opted_out': 'marked not to email',
    'in_outbox': 'already in the outbox',
    'sent': 'already sent',
    'excluded': 'left out of this batch',
    'refused': 'could not be drafted',
}


def _invalid(field, problem):
    return d._invalid(field, problem)


# ---------------------------------------------------------------- what the books say

def customers(s) -> dict[str, dict]:
    """Every customer and job with its effective delivery method and customer type (a job inherits its parent's)."""
    t = c.customers
    rows = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(
        t.c.id, t.c.parent_id, t.c.full_name, t.c.active, t.c.preferred_delivery_method,
        t.c.customer_type_id)).mappings()}

    def effective(row, column, default=None):
        seen = 0
        while row is not None and seen < 10:
            if row[column] is not None:
                return row[column]
            row, seen = rows.get(row['parent_id']), seen + 1
        return default

    for row in rows.values():
        row['method'] = effective(row, 'preferred_delivery_method', 'none')
        row['type_id'] = effective(row, 'customer_type_id')
    return rows


def _ancestors(rows, customer_id):
    out, row = [], rows.get(customer_id)
    while row is not None and len(out) < 10:
        out.append(row['id'])
        row = rows.get(row['parent_id'])
    return out


def documents(s, kinds, *, ids=None, date_from=None, date_to=None) -> list[dict]:
    """Invoices, sales receipts and estimates that can be emailed: not voided (an estimate open or accepted)."""
    out = []
    txn = [_TXN[k] for k in kinds if k in _TXN]
    if txn:
        t, r, p = c.transactions, c.transaction_revisions, c.sales_profiles
        q = (sa.select(t.c.id, t.c.type, t.c.number, r.c.date, p.c.customer_id)
             .select_from(t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
             .where(t.c.type.in_(txn), t.c.status == 'posted'))
        if ids is not None:
            q = q.where(t.c.id.in_(list(ids)))
        if date_from:
            q = q.where(r.c.date >= date_from)
        if date_to:
            q = q.where(r.c.date <= date_to)
        for row in s.company.conn.execute(q).mappings():
            out.append(dict(kind='invoice' if row['type'] == 'invoice' else 'sales-receipt', id=row['id'],
                            number=row['number'], date=row['date'], customer_id=row['customer_id']))
    if 'estimate' in kinds:
        w, r = c.work_documents, c.work_revisions
        q = (sa.select(w.c.id, w.c.number, r.c.date, r.c.customer_id)
             .select_from(w.join(r, r.c.id == w.c.current_revision_id))
             .where(w.c.kind == 'estimate', w.c.status.in_(('open', 'accepted'))))
        if ids is not None:
            q = q.where(w.c.id.in_(list(ids)))
        if date_from:
            q = q.where(r.c.date >= date_from)
        if date_to:
            q = q.where(r.c.date <= date_to)
        for row in s.company.conn.execute(q).mappings():
            out.append(dict(kind='estimate', id=row['id'], number=row['number'], date=row['date'],
                            customer_id=row['customer_id']))
    return out


def message_states(s, kinds) -> dict[tuple, tuple[str, str]]:
    """(source kind, source id[, period]) -> ('in_outbox' or 'sent', message number) for every message not cancelled;
    a live message wins over a sent one."""
    t, dd = c.delivery_messages, c.delivery_message_documents
    out: dict[tuple, tuple[str, str]] = {}
    q = (sa.select(dd.c.source_kind, dd.c.source_id, dd.c.statement_from, dd.c.statement_to, t.c.status, t.c.number)
         .select_from(dd.join(t, t.c.id == dd.c.message_id))
         .where(dd.c.role == 'document', dd.c.source_kind.in_(list(kinds)), t.c.status != 'cancelled'))
    for row in s.company.conn.execute(q).mappings():
        key = (row['source_kind'], row['source_id'])
        if row['source_kind'] == 'statement':
            key += (row['statement_from'], row['statement_to'])
        state = 'in_outbox' if row['status'] in LIVE else 'sent'
        if key not in out or (state == 'in_outbox' and out[key][0] == 'sent'):
            out[key] = (state, row['number'])
    return out


def flags(s, ids=None) -> dict[tuple, bool]:
    f = c.delivery_flags
    q = sa.select(f.c.source_kind, f.c.source_id, f.c.wanted)
    if ids is not None:
        q = q.where(f.c.source_id.in_(list(ids)))
    return {(k, i): bool(w) for k, i, w in s.company.conn.execute(q)}


def _auto_from(s):
    return s.company.conn.execute(sa.select(c.company_info.c.delivery_auto_flag_from)).scalar()


def to_be_emailed(doc, people, wanted, auto_from) -> str | None:
    """'flagged', 'auto' or None for one document with no live or sent message."""
    flag = wanted.get((doc['kind'], doc['id']))
    if flag is True:
        return 'flagged'
    if flag is False:
        return None
    if auto_from and doc['date'] >= auto_from and (people.get(doc['customer_id']) or {}).get('method') == 'email':
        return 'auto'
    return None


class Recipients:
    """The To a draft would carry, per customer, read as the principal (``customer show``), once each."""

    def __init__(self, s, ctx):
        from bookflow.company.info import read_info
        self.read = d.reader(s, ctx)
        self.company_id = s.company_row['id']
        self.info = read_info(s.company)
        self.seen: dict[str, list[str]] = {}

    def __call__(self, customer_id) -> list[str]:
        if customer_id not in self.seen:
            customer = self.read('customer show', {'customer': customer_id}, self.company_id)
            self.seen[customer_id] = d.default_recipients(self.read, self.company_id, customer, self.info)[0]
        return self.seen[customer_id]


def _label(kind, number, date_from=None, date_to=None):
    if kind == 'statement':
        return f'Statement {date_from} to {date_to}'
    if kind == 'notice':
        return 'Overdue notice'
    return f'{d.SOURCE_LABEL[kind]} {number}'


# ---------------------------------------------------------------- flags and the queue

def _flag_state(s, ctx, kind, document_id):
    people = customers(s)
    [doc] = documents(s, [kind], ids=[document_id]) or [None]
    wanted = flags(s, [document_id])
    states = message_states(s, [kind])
    message = states.get((kind, document_id))
    flag = wanted.get((kind, document_id))
    name = (people.get(doc['customer_id']) or {}).get('full_name') if doc else None
    if doc is None:
        why, queued = 'it cannot be emailed (voided, or an estimate not open)', False
    elif message:
        queued = False
        why = f"{SKIP_TEXT[message[0]]} as message {message[1]}"
    else:
        reason = to_be_emailed(doc, people, wanted, _auto_from(s))
        queued = reason is not None
        why = {'flagged': 'flagged Email later', 'auto': "dated on or after the auto-flag date and the customer "
               'prefers email'}.get(reason) or ('marked not to email' if flag is False else
                                                 'not flagged, and the auto-flag date does not reach it')
    return doc, name, flag, queued, why, message


def _flag_out(s, ctx, kind, src, flag, version=None, warnings=()):
    doc, name, _, queued, why, message = _flag_state(s, ctx, kind, src.id)
    return bm.FlagOutput(kind=kind, document_id=src.id, number=src.number, customer_id=src.customer_id,
                         customer_name=name or src.customer_id,
                         flag=None if flag is None else ('email-later' if flag else 'not-to-email'),
                         queued=queued, why=why, message=message[1] if message else None, version=version,
                         warnings=list(warnings))


def prepare_flag(s, ctx, inp, wanted: bool) -> Plan:
    read = d.reader(s, ctx)
    src = d.resolve_source(read, s.company_row['id'], SimpleNamespace(kind=inp.kind, document=inp.document,
                                                                      customer=None, date_from=None, date_to=None))
    f = c.delivery_flags
    before = s.company.conn.execute(sa.select(f).where(f.c.source_kind == inp.kind, f.c.source_id == src.id)
                                    ).mappings().first()
    before = dict(before) if before else None
    at = clock.now_iso()
    if before is not None and bool(before['wanted']) == wanted:
        return Plan(_flag_out(s, ctx, inp.kind, src, wanted, before['version']), dict(src=src, unchanged=True))
    if before is None:
        after = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                     updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, source_kind=inp.kind,
                     source_id=src.id, wanted=wanted)
    else:
        after = dict(before, version=before['version'] + 1, updated_at=at, updated_by=s.actor.id,
                     updated_via=ctx.interface.value, wanted=wanted)
    warnings = []
    if wanted and d.messages_for_source(s, inp.kind, src.id):
        live = [r for r in d.messages_for_source(s, inp.kind, src.id) if r['status'] != 'cancelled']
        if live:
            warnings.append(f"{src.label} is already in message {live[0]['number']} ({live[0]['status']}); the "
                            'queue lists it again only once that message is cancelled.')
    out = _flag_out(s, ctx, inp.kind, src, wanted, after['version'], warnings)
    if s.dry_run:
        # The preview says what the flag would make of it.
        out = out.model_copy(update={'flag': 'email-later' if wanted else 'not-to-email'})
    return Plan(out, dict(src=src, before=before, after=after))


def apply_flag(plan, ctx, s) -> Applied:
    src = plan.data['src']
    if plan.data.get('unchanged'):
        return Applied(plan.preview, [], f'{src.label}: unchanged')
    f, before, after = c.delivery_flags, plan.data['before'], plan.data['after']
    if before is None:
        s.company.conn.execute(f.insert().values(**after))
        touched = Touched(FLAG_RECORD, after['id'], 'create', None, 1, after, db='company')
    else:
        done = s.company.conn.execute(f.update().where(f.c.id == before['id'], f.c.version == before['version'])
                                      .values(**{k: v for k, v in after.items() if k != 'id'}))
        if done.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': FLAG_RECORD, 'record_id': before['id']})
        touched = Touched(FLAG_RECORD, after['id'], 'update', before['version'], after['version'], after, before,
                          db='company')
    out = _flag_out(s, ctx, src.kind, src, after['wanted'], after['version'], plan.preview.warnings)
    what = 'Email later' if after['wanted'] else 'not to email'
    return Applied(out, [touched], f'{src.label} marked {what}')


def queue(s, ctx, inp) -> bm.QueueOutput:
    people = customers(s)
    auto_from = _auto_from(s)
    within = None
    if inp.customer:
        from bookflow.company.parties import resolve_party
        top = resolve_party(s.company, 'customer', inp.customer)['id']
        within = {cid for cid in people if top in _ancestors(people, cid)}
    if inp.method == 'mail':
        rows = sorted((p for p in people.values() if p['method'] == 'mail' and p['active']
                       and (within is None or p['id'] in within)), key=lambda p: p['full_name'].casefold())
        listed = rows[:inp.limit]
        return bm.QueueOutput(customers=[bm.MailCustomer(customer_id=p['id'], customer_name=p['full_name'])
                                         for p in listed], count=len(listed), truncated=len(rows) > inp.limit,
                              auto_flag_from=auto_from)
    kinds = [inp.kind] if inp.kind else list(DOCUMENT_KINDS)
    wanted, states = flags(s), message_states(s, kinds)
    found = []
    for doc in documents(s, kinds, ids=[inp.document] if inp.document else None):
        if within is not None and doc['customer_id'] not in within:
            continue
        if (doc['kind'], doc['id']) in states:
            continue
        why = to_be_emailed(doc, people, wanted, auto_from)
        if why:
            found.append((doc, why))
    found.sort(key=lambda x: ((people.get(x[0]['customer_id']) or {}).get('full_name', '').casefold(),
                              x[0]['date'], x[0]['number'] or ''))
    recipients = Recipients(s, ctx)
    rows = []
    for doc, why in found[:inp.limit]:
        to = recipients(doc['customer_id'])
        person = people.get(doc['customer_id']) or {}
        rows.append(bm.QueueRow(kind=doc['kind'], document_id=doc['id'], number=doc['number'], date=doc['date'],
                                customer_id=doc['customer_id'], customer_name=person.get('full_name') or '',
                                method=person.get('method') or 'none', why=why, to=to,
                                problem=None if to else 'no address'))
    return bm.QueueOutput(rows=rows, count=len(rows), truncated=len(found) > inp.limit, auto_flag_from=auto_from)


# ---------------------------------------------------------------- the selection

def _decimal(text, field):
    try:
        value = Decimal(str(text).replace(',', ''))
    except (InvalidOperation, TypeError):
        raise _invalid(field, f'{text!r} is not an amount') from None
    if not value.is_finite():
        raise _invalid(field, f'{text!r} is not an amount')
    return value


def _period(inp):
    if inp.period:
        import calendar
        year, month = (int(x) for x in inp.period.split('-'))
        if not 1 <= month <= 12:
            raise _invalid('period', 'give a month as YYYY-MM')
        return f'{inp.period}-01', f'{inp.period}-{calendar.monthrange(year, month)[1]:02d}'
    return inp.date_from, inp.date_to


def check_selection(inp):
    """Missing specifics are asked for, never guessed: each refusal names what to give."""
    chosen = [name for name in ('queued', 'unsent', 'ready') if getattr(inp, name)]
    if len(chosen) > 1:
        raise _invalid('selection', 'choose one of queued, unsent or ready')
    if inp.kind == 'statement':
        if chosen:
            raise _invalid('selection', 'statements take period (or date_from and date_to), not ' + chosen[0])
        if not inp.period and not (inp.date_from and inp.date_to):
            raise _invalid('period', 'say which period the statements cover: period 2026-09, or date_from and '
                                     'date_to')
        if inp.period and (inp.date_from or inp.date_to):
            raise _invalid('period', 'give period or date_from and date_to, not both')
        if inp.combine:
            raise _invalid('combine', 'a statement is already one message per customer')
    elif inp.kind == 'notice':
        if not inp.ready:
            raise _invalid('ready', 'overdue notices are batched with ready: true (every ready notice)')
    elif inp.unsent:
        if not inp.kind:
            raise _invalid('kind', 'say which documents: invoice, sales-receipt or estimate')
    elif not inp.queued:
        raise _invalid('selection', 'choose what to draft: queued (the to-be-emailed documents), unsent with a '
                                    'kind, kind statement with a period, or kind notice with ready')
    if inp.kind != 'statement' and (inp.period or inp.balance_over is not None or inp.method):
        raise _invalid('period' if inp.period else 'balance_over' if inp.balance_over is not None else 'method',
                       'is for statements only')
    if inp.date_from and inp.date_to and inp.date_from > inp.date_to:
        raise _invalid('date_from', 'is after date_to')
    if inp.combine and inp.kind == 'notice':
        raise _invalid('combine', 'each overdue notice is its own message')
    if inp.balance_over is not None:
        _decimal(inp.balance_over, 'balance_over')


def _type_ids(s, selector) -> set[str]:
    t = c.customer_types
    rows = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(t.c.id, t.c.parent_id, t.c.full_name_key,
                                                                       t.c.name_key)).mappings()}
    key = selector.strip().casefold()
    top = next((r['id'] for r in rows.values() if selector in (r['id'],) or key in (r['full_name_key'],
                                                                                    r['name_key'])), None)
    if top is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'customer_type', 'selector': selector})
    out = {top}
    changed = True
    while changed:
        changed = False
        for r in rows.values():
            if r['parent_id'] in out and r['id'] not in out:
                out.add(r['id'])
                changed = True
    return out


def resolve(s, ctx, inp) -> tuple[str, list[dict], list[dict]]:
    """(kind, targets, skipped) for a selection: every message to draft, and who is left out with why."""
    check_selection(inp)
    people = customers(s)
    within = types = None
    if inp.customer:
        from bookflow.company.parties import resolve_party
        top = resolve_party(s.company, 'customer', inp.customer)['id']
        within = {cid for cid in people if top in _ancestors(people, cid)}
    if inp.customer_type:
        types = _type_ids(s, inp.customer_type)
    excluded = set(inp.exclude or ())
    recipients = Recipients(s, ctx)
    targets, skipped = [], []

    def name(cid):
        return (people.get(cid) or {}).get('full_name') or cid

    def admit(cid):
        return (within is None or cid in within) and (types is None or (people.get(cid) or {}).get('type_id') in types)

    def item(kind, cid, docs, *, key, date_from=None, date_to=None, reason=None, message=None):
        labels = [_label(kind, x.get('number'), date_from, date_to) for x in docs] or [
            _label(kind, None, date_from, date_to)]
        out = dict(key=key, kind=kind, customer_id=cid, customer_name=name(cid), documents=labels,
                   ids=[x['id'] for x in docs], date_from=date_from, date_to=date_to, to=[])
        if reason:
            out.update(reason=reason, message=message)
        return out

    if inp.kind == 'statement':
        date_from, date_to = _period(inp)
        floor = _decimal(inp.balance_over if inp.balance_over is not None else '0', 'balance_over')
        states = message_states(s, ['statement'])
        read, cursor = recipients.read, None
        balances = []
        while True:
            raw = {'as_of': date_to, 'limit': 200, **({'cursor': cursor} if cursor else {})}
            page = read('report customer-balance-summary', raw, recipients.company_id)
            balances += page['rows']
            cursor = page.get('next_cursor')
            if not cursor:
                break
        for row in sorted(balances, key=lambda r: (r.get('current_customer_label') or '').casefold()):
            cid = row['customer_id']
            amount = row['balance']['amount'] if isinstance(row['balance'], dict) else row['balance']
            if not admit(cid) or _decimal(amount, 'balance') <= floor:
                continue
            reason = message = None
            method = (people.get(cid) or {}).get('method')
            state = states.get(('statement', cid, date_from, date_to))
            if cid in excluded:
                reason = 'excluded'
            elif state:
                reason, message = state
            elif method == 'mail':
                reason = 'prefers_mail'
            elif inp.method == 'email' and method != 'email':
                reason = 'not_email'
            entry = item('statement', cid, [], key=cid, date_from=date_from, date_to=date_to, reason=reason,
                         message=message)
            if not reason:
                entry['to'] = recipients(cid)
                if not entry['to']:
                    entry.update(reason='no_address')
            (skipped if entry.get('reason') else targets).append(entry)
        return 'statement', targets, skipped

    if inp.kind == 'notice':
        n = c.overdue_notices
        states = message_states(s, ['notice'])
        rows = [dict(r) for r in s.company.conn.execute(sa.select(n.c.id, n.c.customer_id, n.c.as_of).where(
            n.c.status == 'ready')).mappings()]
        for row in sorted(rows, key=lambda r: (name(r['customer_id']).casefold(), r['as_of'])):
            cid = row['customer_id']
            if not admit(cid):
                continue
            state = states.get(('notice', row['id']))
            reason, message = (state if state else (None, None))
            if row['id'] in excluded:
                reason = 'excluded'
            elif not reason and (people.get(cid) or {}).get('method') == 'mail':
                reason = 'prefers_mail'
            entry = item('notice', cid, [{'id': row['id'], 'number': None}], key=row['id'], reason=reason,
                         message=message)
            if not reason:
                entry['to'] = recipients(cid)
                if not entry['to']:
                    entry.update(reason='no_address')
            (skipped if entry.get('reason') else targets).append(entry)
        return 'notice', targets, skipped

    kinds = [inp.kind] if inp.kind else list(DOCUMENT_KINDS)
    wanted, states, auto_from = flags(s), message_states(s, kinds), _auto_from(s)
    docs = [x for x in documents(s, kinds, date_from=inp.date_from, date_to=inp.date_to) if admit(x['customer_id'])]
    docs.sort(key=lambda x: (name(x['customer_id']).casefold(), x['kind'], x['date'], x['number'] or ''))
    groups: dict[tuple, list[dict]] = {}
    for doc in docs:
        state = states.get((doc['kind'], doc['id']))
        reason = message = None
        if inp.queued:
            if state or not to_be_emailed(doc, people, wanted, auto_from):
                continue  # not to be emailed: the queue does not hold it
        elif state:
            reason, message = state
        elif wanted.get((doc['kind'], doc['id'])) is False:
            reason = 'opted_out'
        elif (people.get(doc['customer_id']) or {}).get('method') == 'mail' and not wanted.get(
                (doc['kind'], doc['id'])):
            reason = 'prefers_mail'
        if not reason and doc['id'] in excluded:
            reason = 'excluded'
        if not reason and not recipients(doc['customer_id']):
            reason = 'no_address'
        if reason:
            skipped.append(item(doc['kind'], doc['customer_id'], [doc], key=doc['id'], reason=reason,
                                message=message))
            continue
        group = (doc['customer_id'], doc['kind']) if inp.combine else (doc['id'],)
        groups.setdefault(group, []).append(doc)
    for group in groups.values():
        first = group[0]
        entry = item(first['kind'], first['customer_id'], group, key=first['id'])
        entry['to'] = recipients(first['customer_id'])
        targets.append(entry)
    return inp.kind or 'mixed', targets, skipped


def _item(entry, numbers=None) -> bm.BatchItem:
    reason = entry.get('reason')
    message = entry.get('message') or (numbers or {}).get(entry['key'])
    text = None
    if reason:
        text = SKIP_TEXT.get(reason, reason) + (f' as message {message}' if reason in ('in_outbox', 'sent')
                                                and message else '')
        if entry.get('text'):
            text += f": {entry['text']}"
    return bm.BatchItem(key=entry['key'], kind=entry['kind'], customer_id=entry['customer_id'],
                        customer_name=entry['customer_name'], documents=entry['documents'], to=entry.get('to') or [],
                        reason=reason, reason_text=text, message=message)


# ---------------------------------------------------------------- batch rows

def row(s, selector) -> dict:
    t = c.delivery_batches
    found = s.company.conn.execute(sa.select(t).where(sa.or_(t.c.id == selector, t.c.number == selector,
                                                             t.c.number == PREFIX + selector))).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': selector})
    return dict(found)


def _number(s):
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (FIRST, PREFIX)
    while s.company.conn.execute(sa.select(c.delivery_batches.c.id).where(
            c.delivery_batches.c.number == f'{prefix}{number}')).first():
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def _messages(s, batch_id) -> list[dict]:
    t = c.delivery_messages
    return [dict(r) for r in s.company.conn.execute(sa.select(t).where(t.c.batch_id == batch_id)
                                                    .order_by(t.c.created_at, t.c.id)).mappings()]


def drafts_to_release(s, selector) -> tuple[list[str], int]:
    """A batch's drafts for ``delivery send --batch``: one chunk, and how many more wait."""
    t = c.delivery_messages
    ids = [r[0] for r in s.company.conn.execute(sa.select(t.c.id).where(
        t.c.batch_id == row(s, selector)['id'], t.c.status == 'draft').order_by(t.c.created_at, t.c.id))]
    return ids[:CHUNK], max(0, len(ids) - CHUNK)


def _output(s, batch, *, messages=(), cancelled=(), kept=(), warnings=(), with_targets=False) -> bm.BatchOutput:
    targets = json.loads(batch['targets'])
    skipped = json.loads(batch['skipped'])
    rows = _messages(s, batch['id']) if batch.get('id') else []
    drafted = len(rows)
    released = sum(1 for r in rows if r['released_at'])
    numbers, statuses = {}, {}
    for r in rows:
        source = s.company.conn.execute(sa.select(c.delivery_message_documents.c.source_id).where(
            c.delivery_message_documents.c.message_id == r['id']).order_by(
            c.delivery_message_documents.c.position).limit(1)).scalar() if with_targets else None
        if source:
            numbers[source] = r['number']
            statuses[r['number']] = r['status']
    would = []
    if with_targets:
        for entry in targets:
            ref = entry['ids'][0] if entry['ids'] else entry['key']
            if entry['kind'] == 'statement':
                ref = entry['customer_id']
            out = _item(entry)
            number = numbers.get(ref)
            would.append(out.model_copy(update={'message': number, 'status': statuses.get(number)}))
    remaining = batch['planned'] - batch['position'] if batch['state'] == 'drafting' else 0
    state = batch['state']
    nxt = {'batch': batch['number']} if state == 'drafting' and batch.get('id') else None
    commands = []
    if nxt:
        commands.append(f"delivery batch --batch {batch['number']}")
    if batch.get('id') and state != 'cancelled':
        commands += [f"delivery query --batch {batch['number']} --status all",
                     f"delivery batch-cancel {batch['number']} --reason ..."]
    return bm.BatchOutput(
        batch_id=batch.get('id'), number=batch.get('number'), state=state, kind=batch['kind'],
        selection=json.loads(batch['selection']), planned=batch['planned'], drafted=drafted, released=released,
        remaining=remaining, would_draft=would, skipped=[_item(x) for x in skipped], messages=list(messages),
        cancelled=list(cancelled), kept=list(kept), next=nxt, version=batch.get('version'),
        warnings=list(warnings), next_commands=commands)


# ---------------------------------------------------------------- batch

def prepare_batch(s, ctx, inp) -> Plan:
    if inp.batch:
        given = [k for k in inp.model_fields_set if k not in ('batch',)]
        if given:
            raise _invalid(given[0], 'continue a batch with batch alone; its selection is fixed')
        batch = row(s, inp.batch)
        if batch['state'] != 'drafting':
            return Plan(_output(s, batch, warnings=[f"Batch {batch['number']} is {batch['state']}; nothing to "
                                                    'draft.']), dict(batch=batch, idle=True))
        if s.dry_run:
            targets = json.loads(batch['targets'])[batch['position']:batch['position'] + CHUNK]
            out = _output(s, batch).model_copy(update={'would_draft': [_item(x) for x in targets]})
            return Plan(out, dict(batch=batch, idle=True))
        return Plan(_output(s, batch), dict(batch=batch))
    kind, targets, skipped = resolve(s, ctx, inp)
    selection = inp.model_dump(exclude_none=True, exclude_defaults=True, exclude={'batch'})
    preview = dict(id=None, number=None, kind=kind, selection=json.dumps(selection, sort_keys=True),
                   targets=json.dumps(targets), skipped=json.dumps(skipped), planned=len(targets), position=0,
                   send=inp.send, state='preview' if s.dry_run else ('drafting' if targets else 'nothing'),
                   version=None)
    warnings = []
    if not targets:
        warnings.append('Nothing to draft: ' + ('every match is skipped, each with why.' if skipped else
                                                'nothing matches this selection.'))
    if s.dry_run:
        out = _output(s, preview, warnings=warnings).model_copy(update={
            'would_draft': [_item(x) for x in targets], 'state': 'preview', 'remaining': len(targets)})
        if targets:
            out.next_commands.insert(0, 'the same delivery batch without dry_run drafts them, '
                                        f'{CHUNK} per call')
        return Plan(out, dict(idle=True))
    if not targets:
        return Plan(_output(s, preview, warnings=warnings), dict(idle=True))
    at = clock.now_iso()
    number, sequence = _number(s)
    batch = dict(preview, id=new_id(), version=1, created_at=at, created_by=s.actor.id,
                 created_via=ctx.interface.value, updated_at=at, updated_by=s.actor.id,
                 updated_via=ctx.interface.value, number=number, state='drafting', schedule_run_id=None,
                 cancelled_reason=None)
    return Plan(_output(s, dict(batch, id=None)), dict(batch=batch, new=True, sequence=sequence))


def apply_batch(plan, ctx, s) -> Applied:
    if plan.data.get('idle'):
        return Applied(plan.preview, [], 'drafted nothing')
    from sqlalchemy.dialects.sqlite import insert
    conn, t = s.company.conn, c.delivery_batches
    batch, touched = dict(plan.data['batch']), []
    if plan.data.get('new'):
        sequence = plan.data['sequence']
        conn.execute(insert(c.sequences).values(**sequence).on_conflict_do_update(index_elements=['name'],
                                                                                   set_=sequence))
        conn.execute(t.insert().values(**{k: v for k, v in batch.items() if k in t.c}))
        touched.append(Touched(RECORD, batch['id'], 'create', None, 1, batch, db='company'))
    drafted, warnings, chunk_touched, batch = draft_chunk(s, ctx, batch)
    touched += chunk_touched
    out = _output(s, batch, messages=drafted, warnings=warnings)
    left = f", {out.remaining} to go" if out.remaining else ''
    return Applied(out, touched, f"batch {batch['number']}: drafted {len(drafted)}{left}")


def draft_chunk(s, ctx, batch):
    """Draft the next ``CHUNK`` targets through ``delivery draft``'s planner; a target that gained a message or can
    no longer be drafted is skipped, naming why. With ``send``, each draft is released too."""
    from bookflow.company import delivery_models as m  # noqa: F401
    conn, t = s.company.conn, c.delivery_batches
    targets, skipped = json.loads(batch['targets']), json.loads(batch['skipped'])
    selection = json.loads(batch['selection'])
    start, end = batch['position'], min(batch['position'] + CHUNK, batch['planned'])
    read = d.reader(s, ctx)
    drafted, warnings, touched = [], [], []
    person = s.actor.kind == 'human'
    for entry in targets[start:end]:
        ids = entry['ids']
        inp = SimpleNamespace(kind=entry['kind'], document=ids[0] if ids else None, documents=ids[1:] or None,
                              combine=len(ids) > 1, customer=entry['customer_id'] if entry['kind'] == 'statement'
                              else None, date_from=entry.get('date_from'), date_to=entry.get('date_to'),
                              template=selection.get('template'), to=None, cc=None, bcc=None, subject=None,
                              body=None, again=False)
        try:
            plan = d.prepare_draft(s, ctx, inp, batch_id=batch['id'], read=read)
        except BookflowError as e:
            number = (e.details or {}).get('number')
            status = (e.details or {}).get('status')
            reason = ('sent' if status == 'sent' else 'in_outbox') if number else 'refused'
            skipped.append(dict(entry, reason=reason, message=number, text=None if number else e.message))
            continue
        if plan.data.get('existing'):
            skipped.append(dict(entry, reason='in_outbox', message=plan.data['row']['number']))
            continue
        if plan.data['row']['to_addresses'] == '[]':
            skipped.append(dict(entry, reason='no_address'))
            continue
        applied = d.apply_draft(plan, ctx, s)
        touched += applied.touched
        message = plan.data['row']
        drafted.append(message['number'])
        if batch['send']:
            # Freshly drafted in this write, so not stale. R187: an actor holding delivery.send releases with a go;
            # until then a person's release is their go and an agent's waits for one.
            at = message['updated_at']
            after = dict(d._touch(s, ctx, message, at), status='ready', released_by=s.actor.id, released_at=at)
            details = {'released_by': s.actor.id, 'batch': batch['id']}
            if person:
                after.update(go_by=s.actor.id, go_at=at)
                details.update(go_by=s.actor.id, go_at=at)
            d._write_row(s, message, after)
            event = d._event(s, ctx, after['id'], 'released', 'ready', details, at=at)
            touched += [Touched(d.RECORD, after['id'], 'update', message['version'], after['version'], after,
                                message, db='company'),
                        Touched('delivery_event', event['id'], 'create', None, None, event, db='company')]
    if batch['send'] and drafted and not person:
        warnings.append("An agent's release waits for a person's go (delivery approve --batch).")
    before = dict(batch)
    at = clock.now_iso()
    batch = dict(batch, position=end, skipped=json.dumps(skipped), state='done' if end >= batch['planned'] else
                 'drafting', version=batch['version'] + 1, updated_at=at, updated_by=s.actor.id,
                 updated_via=ctx.interface.value)
    done = conn.execute(t.update().where(t.c.id == batch['id'], t.c.version == before['version']).values(
        **{k: batch[k] for k in ('position', 'skipped', 'state', 'version', 'updated_at', 'updated_by',
                                 'updated_via')}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': batch['id']})
    touched.append(Touched(RECORD, batch['id'], 'update', before['version'], batch['version'], batch, before,
                           db='company'))
    return drafted, warnings, touched, batch


def show(s, inp) -> bm.BatchOutput:
    return _output(s, row(s, inp.batch), with_targets=True)


# ---------------------------------------------------------------- cancel as one unit

def _blocker(s, message) -> str | None:
    if message['status'] == 'cancelled':
        return None
    if message['status'] == 'sent':
        return 'sent'
    if message['released_at']:
        return 'released'
    if d._exported(s, message['id']):
        return 'exported'
    return None


_BLOCK_TEXT = {'sent': 'already sent', 'released': 'released to the outbox; cancel it on its own if it should not go',
               'exported': 'exported as a .eml file, which may have been sent by hand'}


def prepare_cancel(s, ctx, inp) -> Plan:
    batch = row(s, inp.batch)
    if inp.expected_version is not None and batch['version'] != inp.expected_version:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': batch['id'], 'expected_version': inp.expected_version,
            'current_version': batch['version']})
    reason = (ctx.reason or '').strip()
    if not reason:
        raise BookflowError('E_REASON_REQUIRED', message='Say why the batch is cancelled (--reason).')
    rows = _messages(s, batch['id'])
    cancel, kept = [], []
    for message in rows:
        if message['status'] == 'cancelled':
            continue
        block = _blocker(s, message)
        if block:
            kept.append(bm.BatchItem(key=message['id'], kind=message['kind'], customer_id=message['customer_id'],
                                     customer_name=d._names(s, [message['customer_id']]).get(message['customer_id'])
                                     or message['customer_id'], reason=block, reason_text=_BLOCK_TEXT[block],
                                     message=message['number'], status=message['status']))
        else:
            cancel.append(message)
    at = clock.now_iso()
    plans = []
    for before in cancel:
        after = dict(d._touch(s, ctx, before, at), status='cancelled', hold_reason=None, cancelled_reason=reason[:500])
        d.drop_go(before, after)
        plans.append((before, after))
    after_batch = None
    if batch['state'] != 'cancelled':
        after_batch = dict(batch, state='cancelled', cancelled_reason=reason[:500], version=batch['version'] + 1,
                           updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    warnings = []
    if kept:
        warnings.append(f"{len(kept)} message(s) of batch {batch['number']} block and stay as they are: "
                        + '; '.join(f'{k.message} ({k.reason_text})' for k in kept) + '.')
    out = _output(s, after_batch or batch, cancelled=[b['number'] for b, _ in plans], kept=kept, warnings=warnings)
    return Plan(out, dict(batch=batch, after=after_batch, plans=plans, reason=reason[:500]))


def apply_cancel(plan, ctx, s) -> Applied:
    batch, after, reason = plan.data['batch'], plan.data['after'], plan.data['reason']
    touched, shas = [], []
    for before, message in plan.data['plans']:
        d._write_row(s, before, message)
        event = d._event(s, ctx, message['id'], 'cancelled', 'cancelled', {
            'reason': reason, 'from': before['status'], 'batch': batch['id']}, at=message['updated_at'])
        touched += [Touched(d.RECORD, message['id'], 'update', before['version'], message['version'], message, before,
                            db='company'),
                    Touched('delivery_event', event['id'], 'create', None, None, event, db='company')]
        shas += [x['sha256'] for x in d._docs(s, [message['id']]).get(message['id'], [])]
    d.prune_files(s, shas)
    if after is not None:
        t = c.delivery_batches
        done = s.company.conn.execute(t.update().where(t.c.id == batch['id'], t.c.version == batch['version']).values(
            **{k: after[k] for k in ('state', 'cancelled_reason', 'version', 'updated_at', 'updated_by',
                                     'updated_via')}))
        if done.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': batch['id']})
        touched.append(Touched(RECORD, batch['id'], 'update', batch['version'], after['version'], after, batch,
                               db='company'))
    out = _output(s, after or batch, cancelled=plan.preview.cancelled, kept=plan.preview.kept,
                  warnings=plan.preview.warnings)
    return Applied(out, touched, f"cancelled batch {batch['number']}: {len(plan.data['plans'])} message(s)"
                   + (f", {len(plan.preview.kept)} kept" if plan.preview.kept else ''))
