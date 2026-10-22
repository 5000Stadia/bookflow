"""The Reminders list: everything due across the company, under one heading per kind (R111).

The anchor's Reminders window summarises what needs doing -- memorized transactions due, to-dos,
overdue invoices, bills to pay -- each as a heading with a count and a total, expandable to its
lines. This is that list, plus what the scheduler adds: memorized entries it could not make, and
overdue notices waiting to be sent. It writes nothing: a reminder slot the scheduler has not yet
listed (no host running, say) is still shown, computed from the schedule.

It is also the agent's answer to "what needs doing today?": `reminders` for the company, and
`todo list --mine` for the agent's own work.
"""
from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa

from bookflow.company import schema as c


def _money(units, currency):
    from bookflow.company.ledger_reports import money
    return money(int(units), currency)


def _memorized(s, as_of, limit):
    from bookflow.company import memorized as service
    o, t = c.memorized_occurrences, c.memorized_transactions
    templates = {row['id']: row for row in service._rows(s, t)}
    due, failed = [], []
    rows = s.company.conn.execute(sa.select(o).where(
        sa.or_(o.c.status == 'blocked', sa.and_(o.c.status == 'pending', o.c.due_date <= as_of)))
        .order_by(o.c.slot_date, o.c.id)).mappings().all()
    for row in rows:
        template = templates.get(row['template_id'])
        if template is None:
            continue
        item = dict(template_id=template['id'], template_name=template['name'], occurrence_id=row['id'],
                    slot_date=row['slot_date'], due_date=row['due_date'],
                    state='blocked' if row['status'] == 'blocked' else 'due', mode=template['mode'],
                    error_code=row['error_code'], error_message=row['error_message'])
        (failed if row['status'] == 'blocked' else due).append(item)
    # Slots of remind templates no run has listed yet: the schedule says they are due.
    for template in templates.values():
        if template['status'] != 'active' or template['mode'] != 'remind' or template['frequency'] == 'never':
            continue
        for slot in service._projected_slots(template, as_of, limit):
            due.append(dict(template_id=template['id'], template_name=template['name'], occurrence_id=None,
                            slot_date=slot, due_date=service.schedule.due_date(slot, template['days_in_advance']),
                            state='due', mode=template['mode'], error_code=None, error_message=None))
    due.sort(key=lambda item: (item['slot_date'], item['template_name']))
    return due, failed


def _invoices(s, as_of, currency):
    from bookflow.company.aging import days_past_due
    from bookflow.company.finance_charges import _documents
    from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
    docs = [d for d in _documents(s, as_of) if int(d['net']) > 0 and d['document_type'] in SETTLEABLE_RECEIVABLE_TYPES
            and days_past_due(as_of, d['aging_date']) > 0]
    names = {r[0]: r[1] for r in s.company.conn.execute(sa.select(c.customers.c.id, c.customers.c.full_name).where(
        c.customers.c.id.in_({d['party'] for d in docs})))} if docs else {}
    return [dict(transaction_id=d['tx'], number=d['document_number'], customer_id=d['party'],
                 customer_name=names.get(d['party']), date=d['document_date'], due_date=d['aging_date'],
                 open_balance=int(d['net']), days_overdue=days_past_due(as_of, d['aging_date'])) for d in docs]


def _bills(s, as_of, ahead):
    from bookflow.company import ledger_reports as ledger
    from bookflow.company.payable_reports import _EFFECTS
    ledger.register_ledger_functions(s.company)
    horizon = (date.fromisoformat(as_of) + timedelta(days=ahead)).isoformat()
    cursor = s.company.raw.execute(
        _EFFECTS + "SELECT d.tx, d.party, d.net, d.document_number, d.document_date, d.aging_date, v.name FROM dated d "
                   "LEFT JOIN vendors v ON v.id=d.party WHERE d.document_type='bill' AND d.net!='0' "
                   "AND d.aging_date <= :horizon ORDER BY d.aging_date, d.tx", {'as_of': as_of, 'horizon': horizon})
    return [dict(transaction_id=tx, number=number, vendor_id=party, vendor_name=name, date=on, due_date=due,
                 open_balance=int(net), overdue=due < as_of)
            for tx, party, net, number, on, due, name in cursor.fetchall()]


def reminders(s, inp):
    from bookflow.company import notices, todos
    from bookflow.company.scheduling_models import RemindersOutput, ReminderSection
    as_of = inp.as_of or todos.today(s)
    currency = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()
    limit = inp.limit
    due, failed = _memorized(s, as_of, limit)
    todo_rows, _ = todos.query(s, assignee_id=s.actor.id if inp.mine else None, status='open', due_by=as_of, limit=500)
    labels = todos._labels(s, todo_rows)
    notice_rows, _ = notices.query(s, status='ready', limit=500)
    names = notices._names(s, notice_rows)
    company_name = notices.policy(s)['company_name']
    invoices = _invoices(s, as_of, currency)
    bills = _bills(s, as_of, inp.bills_days_ahead)
    total = lambda rows, key: _money(sum(row[key] for row in rows), currency)  # noqa: E731
    sections = [
        ReminderSection(key='memorized_due', title='Memorized transactions due', count=len(due), total=None),
        ReminderSection(key='memorized_failed', title='Memorized entries that need a person', count=len(failed), total=None),
        ReminderSection(key='todos_due', title='To do notes', count=len(todo_rows), total=None),
        ReminderSection(key='notices_ready', title='Overdue notices to send', count=len(notice_rows),
                        total=total(notice_rows, 'overdue_minor_units')),
        ReminderSection(key='overdue_invoices', title='Overdue invoices', count=len(invoices), total=total(invoices, 'open_balance')),
        ReminderSection(key='bills_to_pay', title='Bills to pay', count=len(bills), total=total(bills, 'open_balance')),
    ]
    with_money = lambda rows: [dict(row, open_balance=_money(row['open_balance'], currency)) for row in rows[:limit]]  # noqa: E731
    return RemindersOutput(
        as_of=as_of, sections=sections, memorized_due=due[:limit], memorized_failed=failed[:limit],
        todos_due=[todos.project(s, row, labels, as_of) for row in todo_rows[:limit]],
        notices_ready=[notices.project(s, row, names, company_name) for row in notice_rows[:limit]],
        overdue_invoices=with_money(invoices), bills_to_pay=with_money(bills),
        total_count=sum(section.count for section in sections))
