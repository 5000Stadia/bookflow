"""Read immutable bank evidence for one reconciliation period (R108 increment 6)."""
import json

import sqlalchemy as sa

from bookflow.company import schema as c, statement_files as files
from bookflow.core.errors import BookflowError


def require_inbox(s):
    """Only the inbox input asks for bank-feed read authority."""
    from bookflow.hub import access
    access.require_resource(s, 'bank-feed', 'member')


def read(s, snapshot, account_id, closing_date, draft, opening_draft):
    """Return the period's live lines, a stable paging/digest image and their source identities."""
    from bookflow.commands.reconcile_import_cmds import _opening_draft
    from bookflow.company.bank_feeds import frozen_by
    require_inbox(s)
    busy = frozen_by(s.company.conn, account_id)
    if busy:
        raise files.invalid('from_inbox', f"Import {busy['id']} is being reversed ({busy['state']}); "
                            f"finish it with `bank-feed reverse {busy['id']}` before copying its lines.")
    state = next((v for v in snapshot.rows['accounts'] if v['account_id'] == account_id), None)
    if draft is not None and (draft.base_chain_version != (state['version'] if state else 0)
                             or draft.base_head_id != (state['head_certificate_id'] if state else None)):
        raise BookflowError('E_RECONCILIATION_CHAIN_STALE')
    head = draft.base_head_id if draft else (state['head_certificate_id'] if state else None)
    base = draft.base_opening_id if draft else (state['opening_id'] if state else None)
    if head:
        after = snapshot.by('certificates')[head]['statement_date']
    elif base:
        after = snapshot.by('openings')[base]['opening_date']
    else:
        opening_draft = opening_draft or _opening_draft(snapshot, account_id)
        after = opening_draft.header.opening_date
    if closing_date is None or closing_date <= after:
        raise files.invalid('statement_date', f'The closing date must be after {after}, the previous statement or opening date.')
    events = c.bank_line_events
    latest_date = (sa.select(sa.func.json_extract(events.c.details, '$.date_in_file'))
                   .where(events.c.line_id == c.bank_lines.c.id, events.c.kind == 'seen_again')
                   .order_by(events.c.created_at.desc(), events.c.id.desc()).limit(1).scalar_subquery())
    posted_date = sa.func.coalesce(latest_date, c.bank_lines.c.date)
    rows = s.company.conn.execute(sa.select(c.bank_lines, posted_date.label('posted_date')).where(
        c.bank_lines.c.account_id == account_id, c.bank_lines.c.live == 1,
        sa.or_(c.bank_lines.c.state != 'ignored', c.bank_lines.c.ignore_reason.is_(None),
               c.bank_lines.c.ignore_reason != 'duplicate'),
        posted_date > after, posted_date <= closing_date).order_by(
            posted_date, c.bank_lines.c.import_id, c.bank_lines.c.ordinal)).mappings().all()
    # Keep source keys, but use the bank's newest posted date. Only an explicit duplicate
    # decision removes evidence; other ignored and linked lines are rematched against the books.
    lines = tuple(files.Line(line_id=r['line_key'], date=r['posted_date'], amount=r['amount'], payee=r['payee'],
                             memo=r['memo'], number=r['number'], fitid=r['fitid']) for r in rows)
    notes = tuple(f"Inbox line {r['id']} has an undecided possible_duplicate flag (may repeat "
                  f"{r['possible_duplicate_of']}); decide it before finishing the statement."
                  for r in rows if r['state'] == 'waiting' and r['possible_duplicate_of'])
    ids = {r['line_key']: r['id'] for r in rows}
    text = json.dumps(dict(after=after, through=closing_date,
                           lines=[dict(bank_line_id=ids[v.line_id], **v.__dict__) for v in lines]),
                      sort_keys=True, separators=(',', ':'))
    return files.Statement(format='inbox', lines=lines, statement_date=closing_date), text, ids, notes
