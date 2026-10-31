"""Overdue notices: the anchor's collection letters, generated on a schedule and queued, never sent (R111).

**The policy** is four company preferences (`company update --overdue-notice-...`): how many days
past due a customer's oldest open invoice must be (null: notices off; the anchor's letters offer 1,
31, 61 or 91), how often notices are generated (monthly or weekly), the least overdue balance worth
a notice, and whether a notice is the customer's statement or a short letter.

**Who gets one.** For each customer or job (merged-away customers read as their survivor), on the
measuring date A: its overdue documents are the open invoices and statement charges at least that
many days past their due date, at their open balance on A. A customer qualifies when those total at
least the minimum and its whole open balance on A is above zero -- a customer whose unapplied
payments already cover what it owes is not dunned, the embarrassment the anchor warns about.

**When.** A notice answers one period of the schedule: the month of A, or the week of A from Monday.
The scheduler (``company/scheduler.py``) runs this at most once a minute, and
``(customer_id, period_key)`` is unique among unreleased notices, so the first run of a period generates that period's
notices and every later run finds them already there -- including a notice a person dismissed,
which stays dismissed until the next period. A reversal releases the original period while
keeping the dismissed notice in history. `notice generate` runs the same thing by hand.

**What is generated** is a row in the ``notice`` queue with the overdue documents captured at A
and a printable PDF (``print_path``): the statement, from the oldest overdue document's date to A,
or the letter. A person (or an agent) sends it however they send mail and marks it sent, or
dismisses it. Nothing here sends anything off the machine: delivery is document delivery's
(R112), which reads this queue and calls ``mark_sent`` when it has delivered one.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

RECORD = 'overdue_notice'


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def _money(units, currency):
    from bookflow.company.ledger_reports import money
    return money(int(units), currency)


def canonical_minimum(text: str, currency: str) -> str:
    """`company update` stores the minimum as canonical decimal text in the home currency."""
    from bookflow.company.sales_models import money
    from bookflow.core.money import Money
    units = money(text, currency, 'overdue_notice_minimum').minor_units
    if units < 0:
        raise _invalid('overdue_notice_minimum', 'must not be negative')
    return Money(units, currency).to_dict()['amount']


def policy(s) -> dict[str, Any]:
    from bookflow.company.sales_models import money
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    currency = info['home_currency']
    minimum = money(info['overdue_notice_minimum'], currency, 'overdue_notice_minimum').minor_units \
        if info['overdue_notice_minimum'] else 0
    return dict(enabled=info['overdue_notice_days'] is not None, days=info['overdue_notice_days'],
                frequency=info['overdue_notice_frequency'], minimum=minimum,
                minimum_set=info['overdue_notice_minimum'] is not None,
                template=info['overdue_notice_template'], currency=currency,
                company_name=info.get('display_name') or info.get('legal_name'))


def policy_out(p):
    from bookflow.company.scheduling_models import NoticePolicyOutput
    return NoticePolicyOutput(enabled=p['enabled'], days_overdue=p['days'], frequency=p['frequency'],
                              minimum=_money(p['minimum'], p['currency']) if p['minimum_set'] else None,
                              template=p['template'])


def period_key(as_of: str, frequency: str) -> str:
    day = date.fromisoformat(as_of)
    if frequency == 'weekly':
        return (day - timedelta(days=day.weekday())).isoformat()
    return day.replace(day=1).isoformat()


def compute(s, as_of: str, p: dict[str, Any]) -> list[dict[str, Any]]:
    """Every customer or job that qualifies for a notice on ``as_of``, in name order."""
    from bookflow.company.aging import days_past_due
    from bookflow.company.finance_charges import _documents
    from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
    per: dict[str, dict[str, Any]] = {}
    for doc in _documents(s, as_of):
        net, party = int(doc['net']), doc['party']
        entry = per.setdefault(party, dict(balance=0, overdue=0, documents=[]))
        entry['balance'] += net
        if net <= 0 or doc['document_type'] not in SETTLEABLE_RECEIVABLE_TYPES:
            continue
        late = days_past_due(as_of, doc['aging_date'])
        if late < max(1, p['days'] or 1):
            continue
        entry['overdue'] += net
        entry['documents'].append(dict(transaction_id=doc['tx'], document_type=doc['document_type'],
                                       number=doc['document_number'], date=doc['document_date'],
                                       due_date=doc['aging_date'], open_balance=net, days_overdue=late))
    wanted = {party: entry for party, entry in per.items()
              if entry['documents'] and entry['balance'] > 0 and entry['overdue'] >= max(1, p['minimum'])}
    if not wanted:
        return []
    names = {row['id']: row for row in s.company.conn.execute(
        sa.select(c.customers.c.id, c.customers.c.full_name, c.customers.c.full_name_key)
        .where(c.customers.c.id.in_(list(wanted)))).mappings()}
    rows = []
    for party, entry in wanted.items():
        if party not in names:
            continue
        documents = sorted(entry['documents'], key=lambda d: (d['due_date'], d['transaction_id']))
        rows.append(dict(customer_id=party, customer_name=names[party]['full_name'], key=names[party]['full_name_key'],
                         overdue=entry['overdue'], balance=entry['balance'], documents=documents,
                         oldest_due_date=documents[0]['due_date'], days_overdue=documents[0]['days_overdue'],
                         statement_from=min(d['date'] for d in documents)))
    rows.sort(key=lambda row: (row['key'], row['customer_id']))
    return rows


def _new_rows(s, ctx, as_of, p):
    """The notices this period still lacks, and how many qualifying customers already have one."""
    key = period_key(as_of, p['frequency'])
    candidates = compute(s, as_of, p)
    have = {row[0] for row in s.company.conn.execute(
        sa.select(c.overdue_notices.c.customer_id).where(c.overdue_notices.c.period_key == key, c.overdue_notices.c.period_released == False))}
    at, via = clock.now_iso(), ctx.interface.value
    rows = []
    for cand in candidates:
        if cand['customer_id'] in have:
            continue
        rows.append(dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=via,
                         updated_at=at, updated_by=s.actor.id, updated_via=via, customer_id=cand['customer_id'],
                         period_key=key, as_of=as_of, template=p['template'], statement_from=cand['statement_from'],
                         overdue_minor_units=cand['overdue'], balance_minor_units=cand['balance'], currency=p['currency'],
                         oldest_due_date=cand['oldest_due_date'], days_overdue=cand['days_overdue'],
                         documents=json.dumps(cand['documents'], sort_keys=True, separators=(',', ':')),
                         status='ready', period_released=False, sent_at=None, sent_by=None, sent_method=None, dismissed_reason=None))
    return key, rows, len(candidates) - len(rows)


def _insert(s, rows):
    s.company.conn.execute(c.overdue_notices.insert(), rows)
    return [Touched(RECORD, row['id'], 'create', None, 1, row, db='company') for row in rows]


def scheduled(s, ctx, as_of) -> dict[str, Any]:
    """The scheduler's run: generate this period's missing notices in one audited transaction."""
    from bookflow.core.audit import write_event_to
    p = policy(s)
    if not p['enabled']:
        return {'enabled': False, 'generated': 0}
    s.company.raw.execute('BEGIN IMMEDIATE')
    try:
        key, rows, already = _new_rows(s, ctx, as_of, p)
        if rows:
            from bookflow.core.dispatch import _upsert_principals
            _upsert_principals(s, ctx)
            touched = _insert(s, rows)
            batch_id = _record_generation(s, ctx, rows, key)
            write_event_to(s.company, ctx, 'notice generate', f'generated {len(rows)} overdue notice(s) for {key}',
                           touched, actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=new_id())
            s.commits.commit(s.company, 'scheduler.write')
        else:
            s.company.raw.rollback()
    except BaseException:
        if s.company.write_transaction:
            s.company.raw.rollback()
        raise
    return {'enabled': True, 'period_key': key, 'generated': len(rows), 'already_for_period': already,
            'batch_id': batch_id if rows else None,
            'next': f'batch reverse {batch_id}' if rows else None}


# ---------------------------------------------------------------- projection

def letter_text(row, customer_name, company_name, documents) -> str:
    """The anchor's friendly collection letter, filled in. A statement notice uses it as its cover."""
    overdue = _money(row['overdue_minor_units'], row['currency']).amount
    listed = '\n'.join(f"  {d['document_type'].replace('_', ' ').capitalize()} {d.get('number') or ''}, "
                       f"due {d['due_date']}: {_money(d['open_balance'], row['currency']).amount}"
                       for d in documents)
    return (f"Dear {customer_name},\n\n"
            f"Our records show {overdue} {row['currency']} on your account is past due, the oldest since "
            f"{row['oldest_due_date']} ({row['days_overdue']} days). The overdue items are:\n\n{listed}\n\n"
            "If you have already sent payment, thank you, and please disregard this notice. Otherwise, "
            "please send payment at your earliest convenience, or contact us if you have any question "
            "about your account.\n\n"
            f"Sincerely,\n{company_name or ''}").rstrip()


def project(s, row, names=None, company_name=None) -> dict[str, Any]:
    from urllib.parse import quote

    from bookflow.commands.common import common_out
    if names is None:
        names = {row['customer_id']: s.company.conn.execute(sa.select(c.customers.c.full_name).where(
            c.customers.c.id == row['customer_id'])).scalar_one()}
    if company_name is None:
        company_name = policy(s)['company_name']
    documents = json.loads(row['documents'])
    name = names.get(row['customer_id']) or row['customer_id']
    company_id = (s.company_row or {}).get('id', '')
    return dict(common_out(s, row), customer_id=row['customer_id'], customer_name=name,
                period_key=row['period_key'], period_released=row.get('period_released', False), as_of=row['as_of'], template=row['template'],
                statement_from=row['statement_from'], overdue=_money(row['overdue_minor_units'], row['currency']),
                balance=_money(row['balance_minor_units'], row['currency']), oldest_due_date=row['oldest_due_date'],
                days_overdue=row['days_overdue'],
                documents=[dict(d, open_balance=_money(d['open_balance'], row['currency'])) for d in documents],
                status=row['status'], sent_at=row['sent_at'], sent_by=row['sent_by'], sent_method=row['sent_method'],
                dismissed_reason=row['dismissed_reason'],
                letter=letter_text(row, name, company_name, documents),
                print_path=f"/c/{quote(str(company_id), safe='')}/notice/{quote(row['id'], safe='')}/print")


def _names(s, rows):
    ids = {row['customer_id'] for row in rows}
    if not ids:
        return {}
    return {r[0]: r[1] for r in s.company.conn.execute(
        sa.select(c.customers.c.id, c.customers.c.full_name).where(c.customers.c.id.in_(ids)))}


def _row(s, notice_id):
    row = s.company.conn.execute(sa.select(c.overdue_notices).where(c.overdue_notices.c.id == notice_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': notice_id})
    return dict(row)


# ---------------------------------------------------------------- commands

def prepare_generate(s, ctx, inp) -> Plan:
    from bookflow.company.scheduling_models import NoticeGenerateOutput
    from bookflow.company.todos import today
    p = policy(s)
    as_of = inp.as_of or today(s)
    if not p['enabled']:
        raise BookflowError('E_VALIDATION', message='Overdue notices are off: set the overdue notice preferences first.', details={
            'fields': [{'field': 'overdue_notice_days', 'problem': 'overdue notices are off until the days overdue is set'}],
            'next': 'bookflow company update --overdue-notice-days 30 --overdue-notice-template letter'})
    key, rows, already = _new_rows(s, ctx, as_of, p)
    names, company_name = _names(s, rows), p['company_name']
    out = NoticeGenerateOutput(as_of=as_of, period_key=key, policy=policy_out(p),
                               generated=[project(s, row, names, company_name) for row in rows],
                               generated_count=len(rows), already_for_period=already, dry_run=s.dry_run)
    return Plan(out, dict(rows=rows, key=key))


def apply_generate(plan, ctx, s) -> Applied:
    rows = plan.data['rows']
    touched = _insert(s, rows) if rows else []
    batch_id = _record_generation(s, ctx, rows, plan.data['key']) if rows else None
    return Applied(plan.preview.model_copy(update={'dry_run': False, 'batch_id': batch_id,
                   'next': f'batch reverse {batch_id}' if batch_id else None}), touched,
                   f"generated {len(rows)} overdue notice(s) for {plan.data['key']}")


def prepare_write(s, ctx, inp, verb) -> Plan:
    from bookflow.company.scheduling_models import NoticeWriteOutput
    before = _row(s, inp.notice)
    if inp.expected_version is not None and before['version'] != inp.expected_version:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': RECORD, 'record_id': before['id'], 'expected_version': inp.expected_version,
            'current_version': before['version'], 'updated_by': before['updated_by'], 'updated_via': before['updated_via']})
    if verb == 'reverse' and before['status'] == 'sent':
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f"Notice {before['id']} to {_names(s, [before])[before['customer_id']]} was sent; sent mail cannot be recalled."),
            details={'record_type': RECORD, 'record_id': before['id'], 'status': 'sent'})
    if before['status'] != 'ready':
        raise _invalid('notice', f"is {before['status']}; only a notice ready to send can be marked sent or dismissed")
    from bookflow.company import delivery
    live = delivery.live_for_notice(s, before['id'])
    if live is not None:
        # Document delivery (R112) holds the notice while an email carrying it is in the outbox: marking the
        # notice sent there is what records it, so the two never disagree.
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f"Overdue notice is in email message {live['number']} ({live['status']}); mark that message sent or "
            'cancel it first.'), details={'record_type': 'delivery_message', 'record_id': live['id'],
                                          'number': live['number'], 'status': live['status'],
                                          'next': f"delivery mark-sent {live['number']}"})
    at = clock.now_iso()
    row = dict(before, version=before['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    if verb == 'mark-sent':
        row.update(status='sent', sent_at=at, sent_by=s.actor.id, sent_method=inp.method)
    else:
        row.update(status='dismissed', dismissed_reason=(inp.note or ctx.reason or '').strip()[:280] or None)
        if verb == 'reverse':
            row['period_released'] = True
    return Plan(NoticeWriteOutput(**project(s, row), dry_run=s.dry_run), dict(row=row, before=before, verb=verb))


def apply_write(plan, ctx, s) -> Applied:
    from bookflow.company.scheduling_models import NoticeWriteOutput
    row, before = plan.data['row'], plan.data['before']
    done = s.company.conn.execute(c.overdue_notices.update().where(
        c.overdue_notices.c.id == row['id'], c.overdue_notices.c.version == before['version'])
        .values(**{k: v for k, v in row.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': RECORD, 'record_id': row['id']})
    what = 'marked sent' if plan.data['verb'] == 'mark-sent' else 'dismissed'
    return Applied(NoticeWriteOutput(**project(s, row)),
                   [Touched(RECORD, row['id'], 'update', before['version'], row['version'], row, before, db='company')],
                   f"{what} the overdue notice to {plan.preview.customer_name} for {row['period_key']}")


def mark_sent(s, ctx, notice_id: str, method: str):
    """The seam for document delivery (R112): record that a notice went out. Not a command."""
    from types import SimpleNamespace
    plan = prepare_write(s, ctx, SimpleNamespace(notice=notice_id, method=method, expected_version=None), 'mark-sent')
    return apply_write(plan, ctx, s)


def show(s, inp):
    from bookflow.company.scheduling_models import NoticeOutput
    return NoticeOutput(**project(s, _row(s, inp.notice)))


def query(s, *, status='ready', customer_id=None, limit=100):
    n = c.overdue_notices
    q = sa.select(n)
    if status != 'all':
        q = q.where(n.c.status == status)
    if customer_id is not None:
        q = q.where(n.c.customer_id == customer_id)
    rows = [dict(r) for r in s.company.conn.execute(
        q.order_by(n.c.as_of.desc(), n.c.overdue_minor_units.desc(), n.c.id).limit(limit + 1)).mappings()]
    return rows[:limit], len(rows) > limit


def page(s, inp):
    from bookflow.company.scheduling_models import NoticeListOutput
    customer_id = None
    if inp.customer is not None:
        from bookflow.company.parties import resolve_party
        customer_id = resolve_party(s.company, 'customer', inp.customer)['id']
    rows, truncated = query(s, status=inp.status, customer_id=customer_id, limit=inp.limit)
    names, company_name = _names(s, rows), policy(s)['company_name']
    total = s.company.conn.execute(sa.select(sa.func.sum(c.overdue_notices.c.overdue_minor_units), sa.func.max(c.overdue_notices.c.currency))
                                   .where(c.overdue_notices.c.status == 'ready')).one()
    return NoticeListOutput(items=[project(s, row, names, company_name) for row in rows], count=len(rows),
                            truncated=truncated, ready_total=_money(total[0], total[1]) if total[1] else None)


# ---------------------------------------------------------------- reversible generation

def _record_generation(s, ctx, rows, key):
    from bookflow.company import batches
    batch_id = batches._insert_batch(s, ctx, kind='notice-generate', label=f'Overdue notices for {key}',
                                     scope=f'notice:{key}', ordering='strict', source_type='notice_generate')
    for row in rows:
        batches.record_effect(s, batch_id, record_type=RECORD, record_id=row['id'], action='created',
                              version_after=row['version'])
    return batch_id


def _reversal_plan(s, ctx, batch, effects, done, probe, blockers):
    from bookflow.company import batches, delivery
    steps, work = [], []
    pending = [effect for effect in reversed(effects) if not done.get((effect['record_type'], effect['record_id']))]
    for effect in pending:
        row = _row(s, effect['record_id'])
        name = f"Notice {row['id']} to {_names(s, [row])[row['customer_id']]} for {row['period_key']}"
        payload = {'notice': row['id'], 'expected_version': row['version'], 'note': f"Reversed batch {batch['id']}"}
        step = dict(seq=effect['seq'], record_type=RECORD, record_id=row['id'], name=name,
                    action='created', version=row['version'], steps=['notice reverse'])
        steps.append(step)
        work.append(dict(step, payload=payload, effect=effect))
        live = delivery.live_for_notice(s, row['id'])
        if row['status'] == 'sent':
            blockers.append(dict(step, code='sent', message=f'{name} was sent; sent mail cannot be recalled.'))
        elif row['version'] != effect['version_after']:
            blockers.append(dict(step, code='edited_again', message=f'{name} was changed since the batch.'))
        elif live is not None:
            blockers.append(dict(step, code='E_WORK_DEPENDENCY', message=(
                f"{name} is held by email message {live['number']} ({live['status']}); cancel that message first.")))
        elif probe:
            try:
                batches.run_nested(s, ctx, 'notice reverse', payload, source_ref=f"reverse:{batch['id']}",
                                   dry_run=True, recovery=False)
            except BookflowError as error:
                blockers.append(dict(step, code=error.code, message=f'{name}: {error.message}', details=error.details))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(pending))


def _reverse_one(s, ctx, batch, reversal_id, item):
    from bookflow.company import batches
    def record(session, cmd, applied, output):
        batches.record_effect(session, reversal_id, record_type=RECORD, record_id=item['record_id'],
                              action='reversed', version_after=output['version'],
                              before={'of_seq': item['seq'], 'step': 0, 'command': cmd.name},
                              event_id=batches.last_event_id(session))
    batches.run_nested(s, ctx, 'notice reverse', item['payload'], source_ref=f"reverse:{batch['id']}",
                       recorder=record, recovery=False)


from bookflow.company import batches as _batches
_batches.register(_batches.Adapter('notice-generate', ordering='strict', planner=_reversal_plan, runner=_reverse_one,
                                  reader=lambda s, batch: _batches.require_read(s, 'notice show')))
