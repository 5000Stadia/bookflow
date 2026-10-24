"""Accountant review (R121): the anchor's External Accountant, Accountant's Copy and Client Data Review.

Three parts, on one set of tables (``accountant_review_schema``):

* **The external accountant.** A person member of the company designated (``accountant add``, a
  person's act) with the end of their review period. In these books they read everything and write
  only adjusting journal entries, reclassifications, review marks and notes, dated on or before the
  period's end; they may correct or void only the entries they made. ``admit`` refuses every other
  write before it is planned, and ``check_dates`` (called by ``journals.open_dates``, the one place
  a posting date is checked against the closing date) refuses a date after the period.

* **The accountant's copy.** ``export`` writes a verified backup archive of the company (the
  ordinary backup format, so ``company restore`` opens it) whose database carries the copy's terms:
  dividing date, origin company, the audit cursor it was taken at and an HMAC key. These books then
  refuse every posting dated on or before the dividing date (``check_dates``), and list merges and
  account edits (``admit``), as the anchor does, until the change file is imported or a person
  cancels the copy. The restored copy is the accountant's Bookflow: it posts only on or before the
  dividing date and only the accountant's kinds of change, and ``changes`` writes what was done
  there since the cursor into a change file signed with the key. ``read_changes`` refuses a file
  that is not signed by a copy of these books (tampered, another company's, another copy's);
  ``import`` takes back the chosen changes once each, each posted under its own audit event as the
  accountant, naming the copy and the accountant's reason.

* **Review support.** ``checklist`` is the Client Data Review: entries to review, uncategorized
  balances, unreconciled bank and card accounts, open receivables, payables and undeposited funds,
  and the accountant's "reviewed through" mark; ``changes_report`` lists what an accountant changed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from bookflow.company import accountant_review_models as m
from bookflow.company import schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Applied, Plan, Touched

CHANGE_FORMAT = 'bookflow-accountant-changes'
CHANGE_FORMAT_VERSION = 1
CHANGE_FILE_LIMIT = 32 * 1024 * 1024
# What a copy's key reads once it can no longer verify anything: returned, cancelled, or in a backup.
WIPED_KEY = '0' * 64
# What an external accountant may write, in these books or in their copy.
ACCOUNTANT_WRITES = frozenset({
    'journal post', 'journal update', 'journal void', 'accountant reclassify', 'accountant mark-entry',
    'accountant reviewed-through', 'note add', 'note edit'})
# In the copy the accountant is its owner, so the owner's review mark and a backup of their work are theirs too.
COPY_WRITES = ACCOUNTANT_WRITES | {'review mark', 'accountant-copy changes', 'company backup'}
# While a copy is out these books keep their lists whole, as the anchor does: no merges, no account edits.
OWNER_LOCKED = frozenset({
    'account update', 'account deactivate', 'account merge', 'account unmerge', 'customer merge', 'customer unmerge',
    'vendor merge', 'vendor unmerge', 'item merge', 'item unmerge'})
_OWN_ENTRY = frozenset({'journal update', 'journal void'})


def _now():
    from bookflow.core.clock import now_iso
    return now_iso()


def _common(s, ctx):
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    return dict(id=new_id(), **common(s.actor.id, ctx.interface.value))


def _invalid(field, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message, details={'fields': [{'field': field, 'problem': problem}], **details})


def _one(s, sql, **params):
    return s.company.conn.execute(sa.text(sql), params).mappings().first()


def _all(s, sql, **params):
    return [dict(r) for r in s.company.conn.execute(sa.text(sql), params).mappings()]


# ------------------------------------------------------------------ state

def active_copy(s):
    """The copy out (owner side) or this copy's terms (accountant side); None when neither."""
    row = _one(s, "SELECT * FROM accountant_copies WHERE status IN ('out','working')")
    return dict(row) if row else None


def _acting_ids(s):
    """Whose terms bind this write: the actor, and the person an agent acts for."""
    ctx = getattr(s, '_permission_context', None)
    return [x for x in (s.actor.id if s.actor else None, getattr(ctx, 'on_behalf_of', None)) if x]


def designation(s):
    """The active external-accountant designation of the actor or of the person an agent acts for, or None.

    An agent acting for the accountant is held to the accountant's terms, as the accountant is."""
    ids = _acting_ids(s)
    if not ids:
        return None
    row = _one(s, """SELECT * FROM accountant_designations WHERE active=1 AND user_id IN (SELECT value FROM json_each(:ids))
                     ORDER BY through_date LIMIT 1""", ids=json.dumps(ids))
    return dict(row) if row else None


def _refuse(message, reason, command, **details):
    return BookflowError('E_PERMISSION', message=message, details={'reason': reason, 'command': command, **details})


def admit(s, cmd, inp) -> None:
    """Refuse, before planning, a write the accountant's terms do not allow (see module notes)."""
    if cmd.scope != 'company' or not cmd.is_write or s.company is None:
        return
    copy = active_copy(s)
    if copy and copy['side'] == 'accountant':
        if cmd.name not in COPY_WRITES:
            raise _refuse(f"This is an accountant's copy: it takes adjusting entries, reclassifications, review marks and notes "
                          f"dated on or before {copy['dividing_date']}, not `{cmd.name}`.", 'accountant_copy', cmd.name,
                          dividing_date=copy['dividing_date'])
        if cmd.name in _OWN_ENTRY and not _made_in_copy(s, copy, _journal_id(s, inp)):
            raise _refuse("In an accountant's copy only the entries made in the copy can be corrected or voided; "
                          'post an adjusting entry instead.', 'accountant_copy', cmd.name)
        return  # in the copy, the copy's terms are the accountant's terms
    elif copy and copy['side'] == 'owner' and cmd.name in OWNER_LOCKED:
        raise _refuse(f"An accountant's copy is out (dividing date {copy['dividing_date']}): accounts cannot be edited and "
                      'list entries cannot be merged until its changes are imported or the copy is cancelled.',
                      'accountant_copy_out', cmd.name, dividing_date=copy['dividing_date'],
                      next_step='`accountant-copy import` the change file, or `accountant-copy cancel`.')
    mine = designation(s)
    if mine is not None:
        if cmd.name not in ACCOUNTANT_WRITES:
            raise _refuse(f"As this company's external accountant you can post adjusting entries and reclassifications dated "
                          f"on or before {mine['through_date']}, mark review items and add notes; `{cmd.name}` is not one of those.",
                          'external_accountant', cmd.name, through=mine['through_date'])
        if cmd.name == 'note edit':
            note = _one(s, 'SELECT author_id FROM notes WHERE id=:n', n=_note_id(s, inp))
            if note is None or note['author_id'] not in _acting_ids(s):
                raise _refuse('As the external accountant you can correct only the notes you wrote; add a note instead.',
                              'external_accountant', cmd.name)
        if cmd.name in _OWN_ENTRY:
            journal = _journal_id(s, inp)
            first = _one(s, """SELECT r.created_by FROM transaction_revisions r WHERE r.transaction_id=:t
                               ORDER BY r.revision_number LIMIT 1""", t=journal)
            if first is None or first['created_by'] not in _acting_ids(s):
                raise _refuse('As the external accountant you can correct or void only the entries you made; post an adjusting '
                              'entry instead.', 'external_accountant', cmd.name)


def _note_id(s, inp):
    from bookflow.company import records
    return records.resolve(s, 'note', inp.note)


def _journal_id(s, inp):
    from bookflow.company import journals
    return journals.resolve(s, inp.journal)['id']


def _made_in_copy(s, copy, transaction_id):
    row = _one(s, """SELECT e.seq FROM transaction_revisions r JOIN audit_events e ON e.id=r.audit_event_id
                     WHERE r.transaction_id=:t ORDER BY r.revision_number LIMIT 1""", t=transaction_id)
    return row is not None and row['seq'] is not None and row['seq'] > (copy['base_audit_seq'] or 0)


def check_dates(s, dates) -> None:
    """Called by ``journals.open_dates`` after the closing date: the copy's and the accountant's date rules."""
    if s.company is None or not dates:
        return
    copy = active_copy(s)
    if copy and copy['side'] == 'owner' and not getattr(s, '_accountant_import', False):
        for date in dates:
            if date <= copy['dividing_date']:
                raise BookflowError('E_PERIOD_CLOSED', message=(
                    f"An accountant's copy is out with dividing date {copy['dividing_date']}: changes dated on or before it "
                    'wait until its changes are imported or a person cancels the copy.'), details={
                    'date': date, 'dividing_date': copy['dividing_date'], 'reason': 'accountant_copy_out',
                    'next_step': '`accountant-copy status`; `accountant-copy import` or `accountant-copy cancel`.'})
    if copy and copy['side'] == 'accountant':
        for date in dates:
            if date > copy['dividing_date']:
                raise BookflowError('E_PERIOD_CLOSED', message=(
                    f"This accountant's copy takes changes dated on or before {copy['dividing_date']}; later dates are the "
                    "owner's."), details={'date': date, 'dividing_date': copy['dividing_date'], 'reason': 'accountant_copy'})
        return
    mine = designation(s)
    if mine is not None:
        for date in dates:
            if date > mine['through_date']:
                raise BookflowError('E_PERIOD_CLOSED', message=(
                    f"Your review period ends {mine['through_date']}; as the external accountant you post on or before it."),
                    details={'date': date, 'through': mine['through_date'], 'reason': 'review_period'})


# ------------------------------------------------------------------ designations

def designated_in_scope(s, user_ids, scope_type, scope_id):
    """Names of the companies in a membership scope where any of these users is a designated external accountant.

    Read from each company's own books, read-only: the hub membership commands ask it, since a designation lives
    in the company and a membership in the hub."""
    from bookflow.hub import schema as h
    ids = [x for x in user_ids if x]
    if not ids or s.hub is None:
        return []
    c_ = h.companies
    where = c_.c.id == scope_id if scope_type == 'company' else c_.c.organization_id == scope_id
    names = []
    for row in s.hub.conn.execute(sa.select(c_.c.display_name, c_.c.path).where(where)).mappings():
        path = s.abs_path(row['path']) / 'company.db'
        if not path.exists():
            continue
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
                hit = db.execute('SELECT 1 FROM accountant_designations WHERE active=1 AND user_id IN (SELECT value FROM json_each(?))',
                                 (json.dumps(ids),)).fetchone()
        except sqlite3.Error:
            continue  # books from before co0088 hold no designation
        if hit:
            names.append(row['display_name'])
    return names


def guard_membership_change(s, command, user, scope, role=None, grants=None):
    """Called by `membership grant` and `membership revoke`.

    A designated external accountant cannot change memberships where they are designated, nor can an agent for
    them; and while someone is designated, their access there can be granted only at standard, with no extra
    grants, so they never come to hold admin acts the designation does not see."""
    acting = [s.actor.id if s.actor else None, getattr(getattr(s, '_permission_context', None), 'on_behalf_of', None)]
    where = designated_in_scope(s, acting, scope.scope_type, scope.scope_id)
    if where:
        raise BookflowError('E_PERMISSION', message=(
            f"As the external accountant of {', '.join(where)} you cannot change who has access there."),
            details={'reason': 'external_accountant', 'command': command, 'companies': where})
    if command != 'membership grant' or (role == 'standard' and not grants):
        return
    where = designated_in_scope(s, [user['id']], scope.scope_type, scope.scope_id)
    if where:
        raise BookflowError('E_VALIDATION', message=(
            f"{user['username']} is the external accountant of {', '.join(where)}, who holds standard access and no more. "
            'End the accountant designation first (`accountant remove`), then change their access.'),
            details={'fields': [{'field': 'role', 'problem': 'the user is a designated external accountant here'}],
                     'reason': 'external_accountant', 'companies': where,
                     'next_step': f"accountant remove {user['username']}"})


def _user(s, selector):
    from bookflow.hub import schema as h
    from bookflow.core.ids import is_ulid, normalize_ulid
    u = h.users
    key = normalize_ulid(selector) if is_ulid(selector) else None
    row = s.hub.conn.execute(sa.select(u.c.id, u.c.kind, u.c.username, u.c.display_name, u.c.active, u.c.hub_admin).where(
        sa.or_(u.c.id == key, u.c.username == selector))).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'user', 'selector': selector})
    return dict(row)


def _member_role(s, user_id):
    from bookflow.hub import schema as h
    from bookflow.hub.access import ROLE_RANK
    ms = h.memberships
    rows = s.hub.conn.execute(sa.select(ms.c.role).where(
        ms.c.user_id == user_id, ms.c.revoked_at.is_(None), sa.or_(
            sa.and_(ms.c.scope_type == 'company', ms.c.scope_id == s.company_row['id']),
            sa.and_(ms.c.scope_type == 'organization', ms.c.scope_id == s.company_row['organization_id'])))).scalars().all()
    return max(rows, key=lambda r: ROLE_RANK.get(r, -1)) if rows else None


def _accountant_out(s, row):
    from bookflow.company.info import principal_names
    names = {}
    try:
        user = _user(s, row['user_id'])
        names = dict(username=user['username'], display_name=user['display_name'])
    except BookflowError:
        names = dict(username=None, display_name=principal_names(s.company, {row['user_id']}).get(row['user_id']))
    return dict(id=row['id'], user_id=row['user_id'], through=row['through_date'], active=bool(row['active']),
                note=row['note'], version=row['version'], **names)


def plan_add(s, ctx, inp) -> Plan:
    user = _user(s, inp.user)
    if user['kind'] != 'human' or not user['active']:
        raise _invalid('user', 'must be an active person', 'An external accountant is a person; an agent cannot be one.')
    if user.get('hub_admin'):
        raise _invalid('user', 'is an installation administrator',
                       'An installation administrator can do anything here, so cannot be held to an external accountant\'s terms. '
                       'Give the accountant their own user without installation administration.', reason='hub_admin')
    role = _member_role(s, user['id'])
    if role != 'standard':
        raise _invalid('user', 'must hold the standard role on this company, no more and no less',
                       'An external accountant holds standard access to this company and no admin, as the anchor\'s External '
                       f"Accountant cannot administer users; this user's role is {role or 'none'}. Give them standard access "
                       '(`membership grant`, or revoke the higher one), then designate them.', role=role)
    old = _one(s, 'SELECT * FROM accountant_designations WHERE user_id=:u', u=user['id'])
    at = _now()
    if old:
        row = dict(old, through_date=inp.through, note=inp.note, active=True, version=old['version'] + 1, updated_at=at,
                   updated_by=s.actor.id, updated_via=ctx.interface.value)
        changed = (old['through_date'], old['note'], bool(old['active'])) != (inp.through, inp.note, True)
    else:
        row = dict(_common(s, ctx), user_id=user['id'], through_date=inp.through, note=inp.note, active=True)
        changed = True
    out = m.AccountantWriteOutput(**dict(_accountant_out(s, row), username=user['username'], display_name=user['display_name']))
    return Plan(out, dict(row=row, old=dict(old) if old else None, changed=changed, user=user))


def apply_add(plan, ctx, s) -> Applied:
    d = plan.data
    if not d['changed']:
        return Applied(plan.preview, [], 'no change')
    t = c.accountant_designations
    from bookflow.company.info import upsert_principal
    upsert_principal(s.company, user_id=d['user']['id'], username=d['user']['username'],
                     display_name=d['user']['display_name'], kind='human')
    if d['old']:
        s.company.conn.execute(t.update().where(t.c.id == d['row']['id']).values(**d['row']))
    else:
        s.company.conn.execute(t.insert().values(**d['row']))
    return Applied(plan.preview, [Touched('accountant_designation', d['row']['id'], 'update' if d['old'] else 'create',
                                          d['old']['version'] if d['old'] else None, d['row']['version'], d['row'], d['old'],
                                          db='company')],
                   f"made {d['user']['display_name']} the external accountant through {d['row']['through_date']}")


def plan_remove(s, ctx, inp) -> Plan:
    user = _user(s, inp.user)
    old = _one(s, 'SELECT * FROM accountant_designations WHERE user_id=:u', u=user['id'])
    if old is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'accountant', 'selector': inp.user})
    row = dict(old, active=False, version=old['version'] + (1 if old['active'] else 0), updated_at=_now(),
               updated_by=s.actor.id, updated_via=ctx.interface.value) if old['active'] else dict(old)
    return Plan(m.AccountantWriteOutput(**_accountant_out(s, row)), dict(row=row, old=dict(old), changed=bool(old['active'])))


def apply_remove(plan, ctx, s) -> Applied:
    d = plan.data
    if not d['changed']:
        return Applied(plan.preview, [], 'no change')
    t = c.accountant_designations
    s.company.conn.execute(t.update().where(t.c.id == d['row']['id']).values(**d['row']))
    return Applied(plan.preview, [Touched('accountant_designation', d['row']['id'], 'deactivate', d['old']['version'],
                                          d['row']['version'], d['row'], d['old'], db='company')],
                   f"ended {plan.preview.display_name or plan.preview.user_id}'s external accountant access")


def accountant_list(s, inp) -> m.AccountantListOutput:
    rows = _all(s, 'SELECT * FROM accountant_designations' + ('' if inp.include_inactive else ' WHERE active=1')
                + ' ORDER BY through_date DESC, id')
    return m.AccountantListOutput(accountants=[m.AccountantOut(**_accountant_out(s, r)) for r in rows])


def _require_person(s, command):
    """A review mark is the accountant's own judgement: an agent working for them tells them what it found."""
    if s.actor is None or s.actor.kind != 'human':
        raise _refuse('Marking the books reviewed is the accountant\'s own step; an agent cannot mark them.', 'person_only', command,
                      next='Tell the accountant what you found; they mark it.')


def _require_accountant(s, command):
    """Marks and reclassifications are the accountant's: designated here, or working in their copy."""
    copy = active_copy(s)
    if designation(s) is None and not (copy and copy['side'] == 'accountant'):
        raise _refuse(f'`{command}` is the external accountant\'s: designate them with `accountant add` (a person\'s step), '
                      "or work in an accountant's copy. The owner uses `review mark` and `journal post`.",
                      'not_external_accountant', command)
    return copy if copy and copy['side'] == 'accountant' else None


# ------------------------------------------------------------------ reclassify

def _money(minor, currency):
    from bookflow.company.ledger_reports import money
    return money(int(minor), currency)


def _account(s, selector):
    from bookflow.company.accounts import resolve_account
    return resolve_account(s.company, selector, by_number=True)


def plan_reclassify(s, ctx, inp) -> Plan:
    from bookflow.company import journals, records
    from bookflow.company.journal_models import JournalPostInput
    from bookflow.core.money import Money
    _require_accountant(s, 'accountant reclassify')
    source = records.resolve(s, 'transaction', inp.transaction)
    head = _one(s, """SELECT t.id, t.type, t.status, r.number, r.date, r.currency FROM transactions t
                      JOIN transaction_revisions r ON r.id=t.current_revision_id WHERE t.id=:t""", t=source)
    if head['status'] != 'posted':
        raise _invalid('transaction', 'is voided', 'A voided entry has nothing to reclassify.')
    src, dst = _account(s, inp.from_account), _account(s, inp.to_account)
    if src['id'] == dst['id']:
        raise _invalid('to_account', 'must differ from from_account')
    net = _one(s, """SELECT COALESCE(SUM(pl.debit_minor_units - pl.credit_minor_units), 0) AS net FROM posting_lines pl
                     JOIN posting_batches pb ON pb.id=pl.batch_id JOIN transactions t ON t.id=pb.transaction_id
                     WHERE pb.transaction_id=:t AND pb.revision_id=t.current_revision_id
                       AND pb.kind IN ('original','replacement') AND pl.account_id=:a""", t=source, a=src['id'])['net']
    if not net:
        raise _invalid('from_account', 'the entry posts nothing to this account',
                       f"{head['type'].replace('_', ' ')} {head['number']} posts nothing to {src['full_name']}.")
    currency = s.company_info_row['home_currency']
    if inp.amount is None:
        amount = abs(net)
    else:
        try:
            amount = Money.parse(inp.amount, currency).minor_units
        except (BookflowError, ValueError, TypeError):
            raise _invalid('amount', 'must be a positive amount in the home currency')
        if amount <= 0 or amount > abs(net):
            raise _invalid('amount', f'must be more than 0 and at most {Money(abs(net), currency).to_dict()["amount"]}')
    text = Money(amount, currency).to_dict()['amount']
    to_side, from_side = ('debit', 'credit') if net > 0 else ('credit', 'debit')
    memo = inp.memo or (f"Reclassify {head['type'].replace('_', ' ')} {head['number']}: {text} from {src['full_name']} "
                        f"to {dst['full_name']}")
    entry = JournalPostInput(date=inp.date or head['date'], memo=memo, lines=[
        dict(account=dst['id'], side=to_side, amount=text, description=memo[:2000]),
        dict(account=src['id'], side=from_side, amount=text, description=memo[:2000])])
    fresh = journals.prepare(s, ctx, entry, 'post')
    return Plan(fresh.preview, dict(fresh=fresh, source=source, src=src['id'], dst=dst['id']))


def apply_reclassify(plan, ctx, s) -> Applied:
    from bookflow.company import journals
    d = plan.data
    copy = active_copy(s)
    fresh = journals.prepare(s, ctx, d['fresh'].data['input'], 'post')
    row = dict(_common(s, ctx), transaction_id=fresh.data['header']['id'], kind='reclassification',
               source_transaction_id=d['source'], from_account_id=d['src'], to_account_id=d['dst'],
               accountant_user_id=s.actor.id, copy_id=copy['id'] if copy and copy['side'] == 'accountant' else None)
    return journals.persist_prepared(fresh, ctx, s, command_name='accountant reclassify', noun='reclassification',
                                     extra=[('accountant_adjustments', 'accountant_adjustment', 'id', [row])])


# ------------------------------------------------------------------ marks

def plan_mark_entry(s, ctx, inp) -> Plan:
    from bookflow.commands.review_cmds import _current
    from bookflow.company import entry_review as review
    _require_person(s, 'accountant mark-entry')
    _require_accountant(s, 'accountant mark-entry')
    identity, doc, flags, mark = _current(s, inp.transaction)
    if not flags:
        raise _invalid('transaction', 'not on the entries-to-review list', 'This entry is not on the entries-to-review list.')
    changed = not review.covered(mark, doc['revision_id'], flags) or (inp.note or None) != (mark or {}).get('note')
    out = m.MarkEntryOutput(transaction_id=identity, revision_id=doc['revision_id'], flags=flags, note=inp.note, changed=changed)
    return Plan(out, dict(identity=identity, doc=doc, flags=flags, changed=changed))


def apply_mark_entry(plan, ctx, s) -> Applied:
    from bookflow.company import entry_review as review
    d = plan.data
    if not d['changed']:
        return Applied(plan.preview, [], 'no change')
    snapshot = {'transaction_id': d['identity'], 'revision_id': d['doc']['revision_id'], 'flags': d['flags'],
                'note': plan.preview.note, 'by': 'external_accountant'}
    return Applied(plan.preview, [Touched(review.REVIEW_RECORD, d['identity'], 'review', None, None, snapshot, db='company')],
                   f"external accountant marked {d['doc']['type'].replace('_', ' ')} {d['doc']['number']} reviewed")


def _mark_out(s, row):
    from bookflow.company.info import principal_names
    name = principal_names(s.company, {row['accountant_user_id']}).get(row['accountant_user_id'])
    return dict(through=row['through_date'], note=row['note'], accountant_user_id=row['accountant_user_id'],
                accountant_name=name, at=row['created_at'], from_copy=bool(row['copy_id']) and not _is_copy_side(s))


def _is_copy_side(s):
    copy = active_copy(s)
    return bool(copy and copy['side'] == 'accountant')


def plan_reviewed_through(s, ctx, inp) -> Plan:
    _require_person(s, 'accountant reviewed-through')
    copy = _require_accountant(s, 'accountant reviewed-through')
    limit = copy['dividing_date'] if copy else designation(s)['through_date']
    if inp.through > limit:
        raise _invalid('through', f'must be on or before {limit}', f'You review through {limit} at most.')
    row = dict(_common(s, ctx), through_date=inp.through, note=inp.note, accountant_user_id=s.actor.id,
               copy_id=copy['id'] if copy else None)
    out = m.ReviewedThroughWriteOutput(through=inp.through, note=inp.note, accountant_user_id=s.actor.id,
                                       accountant_name=s.actor.display_name, at=row['created_at'], from_copy=False)
    return Plan(out, dict(row=row))


def apply_reviewed_through(plan, ctx, s) -> Applied:
    row = plan.data['row']
    s.company.conn.execute(c.accountant_review_marks.insert().values(**row))
    return Applied(plan.preview, [Touched('accountant_review_mark', row['id'], 'create', None, 1, row, db='company')],
                   f"external accountant reviewed the books through {row['through_date']}")


def reviewed_through(s):
    row = _one(s, 'SELECT * FROM accountant_review_marks ORDER BY created_at DESC, id DESC LIMIT 1')
    return m.ReviewedThroughOut(**_mark_out(s, dict(row))) if row else None


# ------------------------------------------------------------------ the copy: export, status, cancel

def _copy_out(s, row) -> dict:
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {x for x in (row['created_by'], row['closed_by']) if x})
    accountant = row.get('accountant_user_id')
    if accountant:
        names.update(principal_names(s.company, {accountant}))
    return dict(copy_id=row['id'], side=row['side'], status=row['status'], dividing_date=row['dividing_date'],
                accountant_name=names.get(accountant) if accountant else None,
                origin_company_id=row['origin_company_id'], created_at=row['created_at'],
                created_by_name=names.get(row['created_by']), file_name=row['file_name'], archive_sha256=row['archive_sha256'],
                closed_at=row['closed_at'], closed_by_name=names.get(row['closed_by']), close_reason=row['close_reason'])


def plan_export(s, ctx, inp) -> Plan:
    from bookflow.core.ids import new_id
    copy = active_copy(s)
    if copy and copy['side'] == 'accountant':
        raise _refuse("This is an accountant's copy; a copy is made from the owner's books.", 'accountant_copy', 'accountant-copy export')
    if copy:
        raise BookflowError('E_VALIDATION', message=(
            f"An accountant's copy is already out (dividing date {copy['dividing_date']}); import its changes or cancel it first."),
            details={'fields': [{'field': 'through', 'problem': 'a copy is already out'}], 'copy_id': copy['id']})
    closing_date = s.company_info_row.get('closing_date')
    if closing_date and inp.through <= closing_date:
        raise _invalid('through', f'must be after the closing date {closing_date}',
                       f'The books are closed through {closing_date}; the accountant could change nothing on or before it. '
                       'Choose a later dividing date, or move the closing date (a person\'s step).')
    accountant = _copy_accountant(s, inp.accountant)
    row = dict(_common(s, ctx), id=new_id(), side='owner', status='out', dividing_date=inp.through,
               origin_company_id=s.company_row['id'], accountant_user_id=accountant['user_id'],
               mac_key=secrets.token_hex(32), base_audit_seq=None,
               file_name=None, archive_sha256=None, closed_at=None, closed_by=None, close_reason=None)
    preview = m.CopyExportOutput(**_copy_out(s, row))
    return Plan(preview, dict(row=row))


def _copy_accountant(s, selector):
    """The designated external accountant the copy goes to: every change it sends back is theirs."""
    rows = _all(s, 'SELECT user_id FROM accountant_designations WHERE active=1')
    if selector is not None:
        user = _user(s, selector)
        rows = [r for r in rows if r['user_id'] == user['id']]
        if not rows:
            raise _invalid('accountant', 'is not a designated external accountant',
                           f"{user['display_name']} is not this company's external accountant; designate them with `accountant add` "
                           "(a person's step) first.")
    if len(rows) != 1:
        raise _invalid('accountant', 'name the designated external accountant' if rows else 'no external accountant is designated',
                       "Name which external accountant the copy is for (`accountant`)." if rows else
                       "Designate the external accountant the copy is for (`accountant add`, a person's step) first: every "
                       "change the copy sends back is posted as them.")
    return rows[0]


def _seed_copy(row):
    """Turn the snapshot into the accountant's copy: its own terms, the audit cursor, no other copy's key."""
    def inspect(snapshot: Path):
        with closing(sqlite3.connect(str(snapshot))) as db:
            base = db.execute('SELECT COALESCE(MAX(seq), 0) FROM audit_events').fetchone()[0]
            db.execute('UPDATE accountant_copies SET mac_key=?', (WIPED_KEY,))
            values = dict(row, side='accountant', status='working', base_audit_seq=base)
            db.execute(f"INSERT INTO accountant_copies ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                       tuple(values.values()))
            db.commit()
        return base
    return inspect


def apply_export(plan, ctx, s) -> Applied:
    from bookflow.company import backup_archive as archive
    from bookflow.core.context import client_version
    from bookflow.core.durability import sync_directory
    from bookflow.storage.paths import derive_folder_name
    row = dict(plan.data['row'])
    company = s.company_row
    folder = s.company.path.parent
    exports = folder / 'exports'
    result = archive.create(folder, company_id=company['id'], display_name=company['display_name'], backups_dir=exports,
                            created_by={'user_id': s.actor.id, 'username': s.actor.username, 'display_name': s.actor.display_name},
                            bookflow_version=client_version(), backup_id=row['id'], inspect=_seed_copy(row), read_only=True)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')
    target = exports / f"{derive_folder_name(company['display_name'])} accountant copy through {row['dividing_date']} {stamp}{archive.SUFFIX}"
    os.replace(result['path'], target)
    manifest_name = target.name[:-len(archive.SUFFIX)] + '.accountant.json'
    manifest = {'format': 'bookflow-accountant-copy', 'format_version': 1, 'copy_id': row['id'], 'company_id': company['id'],
                'display_name': company['display_name'], 'dividing_date': row['dividing_date'], 'archive': target.name,
                'archive_sha256': result['sha256'], 'created_at': result['manifest']['created_at'],
                'created_by': s.actor.display_name,
                'open_with': 'bookflow company restore <archive> --as-copy (on the accountant\'s machine, as a separate company)'}
    (exports / manifest_name).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
    sync_directory(exports)
    row.update(file_name=target.name, archive_sha256=result['sha256'])
    s.company.conn.execute(c.accountant_copies.insert().values(**row))
    out = m.CopyExportOutput(**_copy_out(s, row), path=str(target), manifest_file=manifest_name, size_bytes=result['size_bytes'],
                             warnings=[f"Until its changes come back, these books refuse changes dated on or before {row['dividing_date']}."])
    snapshot = {k: v for k, v in row.items() if k != 'mac_key'}
    return Applied(out, [Touched('accountant_copy', row['id'], 'create', None, 1, snapshot, db='company')],
                   f"made an accountant's copy through {row['dividing_date']}: {target.name}")


def status(s) -> m.CopyStatusOutput:
    copy = active_copy(s)
    history = _all(s, "SELECT * FROM accountant_copies WHERE status NOT IN ('out','working') ORDER BY created_at DESC, id DESC LIMIT 20")
    return m.CopyStatusOutput(current=m.CopyOut(**_copy_out(s, copy)) if copy else None,
                              history=[m.CopyOut(**_copy_out(s, r)) for r in history],
                              locked_through=copy['dividing_date'] if copy and copy['side'] == 'owner' else None,
                              restricted_to=copy['dividing_date'] if copy and copy['side'] == 'accountant' else None)


def plan_cancel(s, ctx) -> Plan:
    copy = active_copy(s)
    if copy is None or copy['side'] != 'owner':
        raise BookflowError('E_RECORD_NOT_FOUND', message="No accountant's copy is out.",
                            details={'record_type': 'accountant_copy', 'selector': 'out'})
    if not (ctx.reason or '').strip():
        raise BookflowError('E_REASON_REQUIRED', message="Say why the accountant's copy is cancelled; its changes can no longer be imported.")
    row = dict(copy, status='cancelled', mac_key=WIPED_KEY, version=copy['version'] + 1, closed_at=_now(), closed_by=s.actor.id,
               close_reason=ctx.reason.strip()[:140], updated_at=_now(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    out = m.CopyCancelOutput(**_copy_out(s, row), warnings=["The accountant's change file for this copy can no longer be imported."])
    return Plan(out, dict(row=row, old=copy))


def apply_cancel(plan, ctx, s) -> Applied:
    row, old = plan.data['row'], plan.data['old']
    t = c.accountant_copies
    s.company.conn.execute(t.update().where(t.c.id == row['id']).values(**row))
    strip = lambda r: {k: v for k, v in r.items() if k != 'mac_key'}  # noqa: E731
    return Applied(plan.preview, [Touched('accountant_copy', row['id'], 'update', old['version'], row['version'], strip(row),
                                          strip(old), db='company')],
                   f"cancelled the accountant's copy through {row['dividing_date']}; its restrictions are removed")


# ------------------------------------------------------------------ the change file

def _canonical(body) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _sign(key_hex, body) -> str:
    return hmac.new(bytes.fromhex(key_hex), _canonical(body), hashlib.sha256).hexdigest()


def _decode(blob):
    from bookflow.core.audit import decode_snapshot
    return decode_snapshot(blob) or {}


def _account_names(s, ids):
    if not ids:
        return {}
    return {r['id']: r['full_name'] for r in _all(s, 'SELECT id, full_name FROM accounts WHERE id IN (SELECT value FROM json_each(:ids))',
                                                   ids=json.dumps(sorted(ids)))}


def _collect(s, copy):
    """What the accountant did in this copy since it was taken: (changes, skipped)."""
    from bookflow.core.money import Money
    base = copy['base_audit_seq'] or 0
    currency = s.company_info_row['home_currency']
    changes, skipped = [], []
    reclass = {r['transaction_id']: r for r in _all(s, 'SELECT * FROM accountant_adjustments')}
    journals = _all(s, """
        SELECT t.id, t.status, r.id AS revision_id, r.date, r.number, r.memo, first.audit_event_id AS first_event
        FROM transactions t JOIN transaction_revisions r ON r.id=t.current_revision_id
        JOIN transaction_revisions first ON first.transaction_id=t.id AND first.revision_number=1
        JOIN audit_events e ON e.id=first.audit_event_id
        WHERE t.type='journal_entry' AND e.seq > :base ORDER BY e.seq""", base=base)
    events = {e['id']: e for e in _all(s, 'SELECT id, seq, actor_id, reason, at FROM audit_events WHERE seq > :base', base=base)}
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {e['actor_id'] for e in events.values() if e['actor_id']})
    for j in journals:
        if j['status'] != 'posted':
            continue
        lines = _all(s, """SELECT account_id, side, amount_minor_units, name_type, name_id, class_id, description, original_currency
                           FROM document_lines WHERE revision_id=:r AND kind='journal' ORDER BY position""", r=j['revision_id'])
        if any(x['original_currency'] for x in lines):
            skipped.append(f"journal {j['number']}: foreign-currency lines are not carried; enter it in the owner's books")
            continue
        event = events.get(j['first_event'], {})
        adj = reclass.get(j['id'])
        changes.append(dict(
            key=j['id'], kind='reclassification' if adj and adj['kind'] == 'reclassification' else 'journal', date=j['date'],
            number=j['number'], memo=j['memo'], reason=event.get('reason'), at=event.get('at'),
            by={'user_id': event.get('actor_id'), 'name': names.get(event.get('actor_id'))},
            source_transaction_id=adj['source_transaction_id'] if adj else None,
            from_account_id=adj['from_account_id'] if adj else None, to_account_id=adj['to_account_id'] if adj else None,
            lines=[dict(account_id=x['account_id'], side=x['side'], amount=Money(x['amount_minor_units'], currency).to_dict()['amount'],
                        name_type=x['name_type'], name_id=x['name_id'], class_id=x['class_id'], description=x['description'])
                   for x in lines]))
    entries = _all(s, """SELECT ae.record_type, ae.record_id, ae.action, ae.after, e.id AS event_id, e.seq FROM audit_entries ae
                         JOIN audit_events e ON e.id=ae.event_id
                         WHERE e.seq > :base AND ae.record_type IN ('note', 'transaction_review') ORDER BY e.seq""", base=base)
    notes, marks = {}, {}
    for x in entries:
        if x['record_type'] == 'note':
            notes.setdefault(x['record_id'], x)
        else:
            marks[x['record_id']] = x
    for note_id, x in notes.items():
        note = _one(s, 'SELECT * FROM notes WHERE id=:n', n=note_id)
        if note is None or x['action'] != 'create':
            continue
        event = events.get(x['event_id'], {})
        changes.append(dict(key=note_id, kind='note', record_type=note['record_type'], record_id=note['record_id'], body=note['body'],
                            reason=event.get('reason'), at=note['at'],
                            by={'user_id': note['author_id'], 'name': names.get(note['author_id'])}))
    for transaction_id, x in marks.items():
        after, event = _decode(x['after']), events.get(x['event_id'], {})
        changes.append(dict(key=f"mark-{transaction_id}-{x['seq']}", kind='review_mark', transaction_id=transaction_id,
                            revision_id=after.get('revision_id'), flags=after.get('flags') or [], note=after.get('note'),
                            reason=event.get('reason'), at=event.get('at'),
                            by={'user_id': event.get('actor_id'), 'name': names.get(event.get('actor_id'))}))
    for mark in _all(s, 'SELECT * FROM accountant_review_marks WHERE copy_id=:c ORDER BY created_at, id', c=copy['id']):
        changes.append(dict(key=mark['id'], kind='reviewed_through', date=mark['through_date'], note=mark['note'], reason=None,
                            at=mark['created_at'], by={'user_id': mark['accountant_user_id'],
                                                       'name': names.get(mark['accountant_user_id'])}))
    return changes, skipped


def plan_changes(s, ctx) -> Plan:
    copy = active_copy(s)
    if copy is None or copy['side'] != 'accountant':
        raise _refuse("`accountant-copy changes` runs in an accountant's copy: open the copy the owner sent with `company restore`.",
                      'not_accountant_copy', 'accountant-copy changes')
    changes, skipped = _collect(s, copy)
    body = dict(format=CHANGE_FORMAT, format_version=CHANGE_FORMAT_VERSION, copy_id=copy['id'],
                company_id=copy['origin_company_id'], dividing_date=copy['dividing_date'], created_at=_now(),
                accountant={'user_id': s.actor.id, 'username': s.actor.username, 'name': s.actor.display_name},
                changes=changes)
    signed = dict(body, mac=_sign(copy['mac_key'], body))
    view = _view(s, signed, {}, s.actor.display_name)
    from bookflow.storage.paths import derive_folder_name
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')
    name = f"{derive_folder_name(s.company_row['display_name'])} accountant changes {stamp}.json"
    out = m.CopyChangesOutput(**view, file_name=name, path=str(s.company.path.parent / 'exports' / name), skipped=skipped)
    return Plan(out, dict(signed=signed, name=name, copy=copy))


def apply_changes(plan, ctx, s) -> Applied:
    from bookflow.core.durability import sync_directory, sync_file
    from bookflow.core.ids import new_id
    exports = s.company.path.parent / 'exports'
    exports.mkdir(mode=0o700, exist_ok=True)
    target = exports / plan.data['name']
    partial = exports / f".{plan.data['name']}.partial"
    partial.write_bytes(json.dumps(plan.data['signed'], indent=1, sort_keys=True, ensure_ascii=False).encode('utf-8'))
    sync_file(partial)
    os.replace(partial, target)
    sync_directory(exports)
    snapshot = dict(file_name=target.name, copy_id=plan.data['copy']['id'], changes=len(plan.data['signed']['changes']),
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    return Applied(plan.preview, [Touched('accountant_change_file', new_id(), 'create', None, 1, snapshot, db='company')],
                   f"wrote the accountant's change file {target.name} with {snapshot['changes']} changes")


def read_changes(s, path_text):
    """The verified change file and the owner's copy it belongs to, or a refusal saying what is wrong."""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise _invalid('file', 'must be an absolute path on the machine running Bookflow', "Give the change file's full path.")
    try:
        if path.stat().st_size > CHANGE_FILE_LIMIT:
            raise _invalid('file', 'is too large to be a change file')
        body = json.loads(path.read_bytes())
    except OSError:
        raise _invalid('file', 'no readable file at that path', 'No change file at that path.')
    except ValueError:
        raise _invalid('file', 'is not a change file', "The file is not an accountant's change file.", reason='not_a_change_file')
    if not isinstance(body, dict) or body.get('format') != CHANGE_FORMAT:
        raise _invalid('file', 'is not a change file', "The file is not an accountant's change file.", reason='not_a_change_file')
    if body.get('format_version') != CHANGE_FORMAT_VERSION:
        raise _invalid('file', 'was written by a newer Bookflow', 'The change file was written in a format this Bookflow does not read.',
                       reason='format_version')
    if body.get('company_id') != s.company_row['id']:
        raise _invalid('file', 'belongs to another company', "This change file comes from a copy of another company's books.",
                       reason='wrong_company')
    copy = _one(s, "SELECT * FROM accountant_copies WHERE id=:i AND side='owner'", i=str(body.get('copy_id')))
    if copy is None:
        raise _invalid('file', 'is not from a copy these books made', "This change file is not from an accountant's copy of these books.",
                       reason='unknown_copy')
    if copy['status'] == 'cancelled':
        raise _invalid('file', 'its copy was cancelled', f"The accountant's copy through {copy['dividing_date']} was cancelled; "
                       'its changes can no longer be imported.', reason='cancelled')
    if copy['status'] == 'returned':
        raise _invalid('file', 'its copy has already come back', f"The accountant's copy through {copy['dividing_date']} was "
                       'imported and is closed; send the accountant a new copy for further changes.', reason='returned')
    if not copy['mac_key'] or set(copy['mac_key']) == {'0'}:
        raise _invalid('file', "these books do not hold the copy's key", "These books do not hold this copy's key (they were "
                       'restored from a backup, which never carries it), so no change file can be verified. Cancel the copy and '
                       'make a new one.', reason='key_unavailable')
    mac = body.pop('mac', None)
    if not isinstance(mac, str) or not hmac.compare_digest(mac, _sign(copy['mac_key'], body)):
        raise _invalid('file', 'has been changed since the accountant\'s copy wrote it',
                       "The change file does not verify: it was changed after the accountant's Bookflow wrote it, or was not "
                       'written by that copy. Ask the accountant for a fresh one.', reason='tampered')
    if body.get('dividing_date') != copy['dividing_date'] or not isinstance(body.get('changes'), list):
        raise _invalid('file', 'does not match its copy', reason='tampered')
    return body, dict(copy)


def _imported(s, copy_id):
    return {r['change_key']: r for r in _all(s, 'SELECT * FROM accountant_imports WHERE copy_id=:c', c=copy_id)}


def _journal_input(change, number_free):
    from bookflow.company.journal_models import JournalPostInput
    lines = []
    for x in change['lines']:
        line = dict(account=x['account_id'], side=x['side'], amount=x['amount'])
        for key in ('name_type', 'name_id', 'class_id', 'description'):
            if x.get(key) is not None:
                line[key] = x[key]
        lines.append(line)
    raw = dict(date=change['date'], memo=change.get('memo'), lines=lines)
    if change.get('number') and number_free:
        raw['number'] = change['number']
    return JournalPostInput(**raw)


def _number_free(s, number):
    return number and _one(s, "SELECT 1 AS x FROM transactions WHERE type='journal_entry' AND number=:n", n=number) is None


def _check(s, ctx, change, copy, mapped, pending=frozenset()):
    """Why this change cannot be taken, or None. Runs the same validation the import will."""
    from pydantic import ValidationError
    from bookflow.company import journals, records
    kind = change.get('kind')
    dividing = copy['dividing_date']
    try:
        if kind in ('journal', 'reclassification'):
            if not isinstance(change.get('date'), str) or change['date'] > dividing:
                return f'dated after the dividing date {dividing}'
            journals.prepare(s, ctx, _journal_input(change, _number_free(s, change.get('number'))), 'post')
        elif kind == 'note':
            target = mapped.get(change.get('record_id'), change.get('record_id'))
            if target not in mapped.values() and target not in pending:
                records.resolve(s, change['record_type'], target)
            if not isinstance(change.get('body'), str) or not change['body'].strip():
                return 'the note is empty'
        elif kind == 'review_mark':
            from bookflow.commands.review_cmds import _current
            if change.get('transaction_id') in pending or change.get('transaction_id') in mapped:
                return None  # a mark on an entry made in the copy comes in with that entry
            _, doc, flags, _ = _current(s, change['transaction_id'])
            if doc['revision_id'] != change.get('revision_id'):
                return 'the entry has changed since the accountant reviewed it'
            if not flags:
                return 'the entry is no longer on the entries-to-review list'
        elif kind == 'reviewed_through':
            if not isinstance(change.get('date'), str) or change['date'] > dividing:
                return f'dated after the dividing date {dividing}'
        else:
            return 'not a kind of change an accountant can send back'
    except BookflowError as e:
        return f"{e.code}: {e.message}"
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        return f'malformed change ({type(e).__name__})'
    return None


def _summary(change, names):
    kind = change.get('kind')
    if kind in ('journal', 'reclassification'):
        total = sum(1 for x in change.get('lines') or [])
        label = 'Reclassification' if kind == 'reclassification' else 'Adjusting entry'
        return f"{label} {change.get('number') or ''} dated {change.get('date')}: {change.get('memo') or ''} ({total} lines)".replace('  ', ' ')
    if kind == 'note':
        return f"Note on {change.get('record_type')} {change.get('record_id')}: {(change.get('body') or '')[:120]}"
    if kind == 'review_mark':
        return f"Marked entry {change.get('transaction_id')} reviewed" + (f": {change['note']}" if change.get('note') else '')
    if kind == 'reviewed_through':
        return f"Reviewed the books through {change.get('date')}" + (f": {change['note']}" if change.get('note') else '')
    return 'unknown change'


def _view(s, body, states, accountant_name):
    """What a change file holds, for a person to read. The accountant is named from these books' own record of the
    copy; names the file gives are the copy holder's to write, so they appear only as labelled annotations."""
    ids = {x.get('account_id') for ch in body['changes'] for x in (ch.get('lines') or []) if isinstance(x, dict)}
    names = _account_names(s, ids)
    out = []
    for ch in body['changes']:
        state, problem, result = states.get(ch.get('key'), ('new', None, None))
        out.append(m.ChangeOut(
            key=str(ch.get('key')), kind=ch.get('kind') if ch.get('kind') in m.ChangeOut.model_fields['kind'].annotation.__args__ else 'note',
            date=ch.get('date'), summary=_summary(ch, names), reason=ch.get('reason'), by_name=accountant_name,
            named_in_file_as=_label((ch.get('by') or {}).get('name'), accountant_name),
            lines=[m.ChangeLine(account_id=x['account_id'], account_name=names.get(x['account_id']), side=x['side'],
                                amount=x['amount'], description=x.get('description')) for x in ch.get('lines') or []],
            state=state, problem=problem, result_id=result))
    counts = {k: sum(1 for x in out if x.state == k) for k in ('new', 'imported', 'refused')}
    return dict(copy_id=body['copy_id'], company_id=body['company_id'], dividing_date=body['dividing_date'],
                accountant_name=accountant_name,
                named_in_file_as=_label((body.get('accountant') or {}).get('name'), accountant_name),
                created_at=str(body.get('created_at') or ''),
                changes=out, counts=counts)


def _label(name, accountant_name):
    name = str(name)[:120] if name else None
    return name if name and name != accountant_name else None


def _states(s, ctx, body, copy):
    done = _imported(s, copy['id'])
    mapped = {k: r['result_id'] for k, r in done.items() if r['result_type'] == 'transaction'}
    states = {}
    # A note may hang off an entry made in the copy: it comes in with that entry.
    pending = {ch.get('key') for ch in body['changes'] if ch.get('kind') in ('journal', 'reclassification')}
    s._accountant_import = True
    try:
        for ch in body['changes']:
            key = ch.get('key')
            if key in done:
                states[key] = ('imported', None, done[key]['result_id'])
                continue
            problem = _check(s, ctx, ch, copy, mapped, pending)
            states[key] = ('refused', problem, None) if problem else ('new', None, None)
    finally:
        s._accountant_import = False
    return states


def preview(s, ctx, inp) -> m.ChangesFileOutput:
    body, copy = read_changes(s, inp.file)
    return m.ChangesFileOutput(**_view(s, body, _states(s, ctx, body, copy), _copy_accountant_identity(s, copy)[1]))


def plan_import(s, ctx, inp) -> Plan:
    body, copy = read_changes(s, inp.file)
    states = _states(s, ctx, body, copy)
    keys = [ch.get('key') for ch in body['changes']]
    if inp.changes is not None:
        unknown = sorted(set(inp.changes) - set(keys))
        if unknown:
            raise _invalid('changes', f"not in the change file: {', '.join(unknown[:5])}")
        refused = [k for k in inp.changes if states[k][0] == 'refused']
        if refused:
            raise _invalid('changes', f"cannot be taken: {', '.join(refused[:5])}", 'Some chosen changes cannot be taken; '
                           '`accountant-copy preview` says why.', refused={k: states[k][1] for k in refused})
    chosen = [k for k in (inp.changes if inp.changes is not None else keys) if states.get(k, ('',))[0] == 'new']
    view = _view(s, body, states, _copy_accountant_identity(s, copy)[1])
    declined = sum(1 for ch in body['changes'] if states.get(ch.get('key'), ('',))[0] != 'imported') - len(chosen)
    if not chosen and not inp.close_copy:
        raise _invalid('changes', 'takes none of the changes',
                       f"This import takes none of the file's changes, and importing closes the copy: all {declined} would be "
                       'declined for good. Choose changes to take, or say close_copy true to close the copy taking nothing '
                       '(or cancel it).', reason='nothing_taken', declined=declined)
    # Importing brings the copy back, closes it and lifts its restrictions, as the anchor's import does; changes
    # left out are declined, and the copy's key is wiped so no file for it verifies again.
    new_status = 'returned' if copy['status'] == 'out' else copy['status']
    out = m.CopyImportOutput(**view, imported=chosen, status=new_status, declined=declined)
    return Plan(out, dict(body=body, copy=copy, chosen=chosen, status=new_status))


def apply_import(plan, ctx, s) -> Applied:
    from bookflow.company import journals
    from bookflow.core.audit import write_event_to
    from bookflow.core.session import Actor
    d = plan.data
    body, copy = d['body'], d['copy']
    # Rebuild under the write lock: another import may have taken some of these meanwhile.
    states = _states(s, ctx, body, copy)
    by_key = {ch['key']: ch for ch in body['changes']}
    # Every change is the accountant's the copy was made for, as these books recorded them at export. The file's
    # own names are the copy holder's to write, so they are only a label in the reason, never an identity.
    who_id, who_name, who_username = _copy_accountant_identity(s, copy)
    acct_name = who_name
    as_accountant = Actor(id=who_id, kind='human', username=who_username, display_name=who_name, hub_admin=False)
    person, touched, imported = s.actor, [], []
    mapped = {k: r['result_id'] for k, r in _imported(s, copy['id']).items() if r['result_type'] == 'transaction'}
    source_ref = f"accountant-copy:{copy['id']}"
    s._accountant_import = True
    try:
        for key in d['chosen']:
            if states.get(key, ('',))[0] != 'new':
                continue
            ch = by_key[key]
            label = str((ch.get('by') or {}).get('name') or '')[:40]
            reason = (str(ch.get('reason') or f"From {acct_name}'s accountant's copy")
                      + (f' (entered in the copy as {label})' if label and label != who_name else ''))[:140]
            change_ctx = ctx.model_copy(update={'reason': reason, 'source_ref': f'{source_ref}:{key}'[:512], 'idempotency_key': None,
                                                'directive_id': None})
            result_type = result_id = None
            if ch['kind'] in ('journal', 'reclassification'):
                fresh = journals.prepare(s, change_ctx, _journal_input(ch, _number_free(s, ch.get('number'))), 'post')
                adj = dict(_common(s, ctx), transaction_id=fresh.data['header']['id'], kind='reclassification'
                           if ch['kind'] == 'reclassification' else 'adjusting',
                           source_transaction_id=ch.get('source_transaction_id'), from_account_id=ch.get('from_account_id'),
                           to_account_id=ch.get('to_account_id'), accountant_user_id=who_id, copy_id=copy['id'])
                adj.update(created_by=who_id, updated_by=who_id)
                s.actor = as_accountant
                try:
                    journals.persist_prepared(fresh, change_ctx, s, command_name='accountant-copy import',
                                              noun="entry from the accountant's copy",
                                              extra=[('accountant_adjustments', 'accountant_adjustment', 'id', [adj])])
                finally:
                    s.actor = person
                result_type, result_id = 'transaction', fresh.data['header']['id']
                mapped[key] = result_id
            else:
                # Reads run as the person; the record and its audit event name the accountant.
                result_type, result_id, record = _apply_other(s, change_ctx, ch, copy, mapped, who_id)
                write_event_to(s.company, change_ctx, 'accountant-copy import',
                               f"{_summary(ch, {})} (from {acct_name}'s accountant's copy)"[:512], [record],
                               actor_id=who_id, actor_kind='human')
            row = dict(_common(s, ctx), copy_id=copy['id'], change_key=str(key)[:64], kind=ch['kind'], result_type=result_type,
                       result_id=result_id, accountant_user_id=who_id, accountant_name=acct_name)
            s.company.conn.execute(c.accountant_imports.insert().values(**row))
            touched.append(Touched('accountant_import', row['id'], 'create', None, 1, row, db='company'))
            imported.append(key)
    finally:
        s.actor = person
        s._accountant_import = False
    if d['status'] != copy['status']:
        t = c.accountant_copies
        new = dict(copy, status=d['status'], mac_key=WIPED_KEY, version=copy['version'] + 1, closed_at=_now(), closed_by=person.id,
                   updated_at=_now(), updated_by=person.id, updated_via=ctx.interface.value)
        s.company.conn.execute(t.update().where(t.c.id == copy['id']).values(**new))
        strip = lambda r: {k: v for k, v in r.items() if k != 'mac_key'}  # noqa: E731
        touched.append(Touched('accountant_copy', copy['id'], 'update', copy['version'], new['version'], strip(new), strip(copy),
                               db='company'))
    out = plan.preview.model_copy(update={'imported': imported})
    if not touched:
        return Applied(out, [], 'no change')
    return Applied(out, touched, f"imported {len(imported)} changes from {acct_name}'s accountant's copy through {copy['dividing_date']}")


def _copy_accountant_identity(s, copy):
    """(user id, display name, username) of the accountant the copy was made for."""
    from bookflow.company.info import principal_names
    user_id = copy.get('accountant_user_id')
    if not user_id:
        raise _invalid('file', 'its copy names no accountant', 'This copy was made without an external accountant; '
                       'cancel it and make a new one for the designated accountant.', reason='no_accountant')
    try:
        user = _user(s, user_id)
        return user_id, user['display_name'], user['username']
    except BookflowError:
        name = principal_names(s.company, {user_id}).get(user_id) or 'the external accountant'
        return user_id, name, name[:64]


def _apply_other(s, ctx, ch, copy, mapped, who_id):
    from bookflow.company import entry_review as review, records
    if ch['kind'] == 'note':
        from bookflow.core.ids import new_id
        target = mapped.get(ch['record_id'], ch['record_id'])
        if target not in mapped.values():
            target = records.resolve(s, ch['record_type'], target)
        row = dict(id=new_id(), **_common_as(who_id, ctx), record_type=ch['record_type'], record_id=target, body=ch['body'],
                   author_id=who_id, interface=ctx.interface.value, at=ch.get('at') or _now(), edited_at=None, kind='comment')
        s.company.conn.execute(c.notes.insert().values(**row))
        return 'note', row['id'], Touched('note', row['id'], 'create', None, 1, row, db='company')
    if ch['kind'] == 'review_mark':
        from bookflow.commands.review_cmds import _current
        identity, doc, flags, _ = _current(s, mapped.get(ch['transaction_id'], ch['transaction_id']))
        snapshot = {'transaction_id': identity, 'revision_id': doc['revision_id'], 'flags': flags, 'note': ch.get('note'),
                    'by': 'external_accountant'}
        return 'transaction', identity, Touched(review.REVIEW_RECORD, identity, 'review', None, None, snapshot, db='company')
    row = dict(_common(s, ctx), through_date=ch['date'], note=ch.get('note'), accountant_user_id=who_id, copy_id=copy['id'])
    row.update(created_by=who_id, updated_by=who_id)
    s.company.conn.execute(c.accountant_review_marks.insert().values(**row))
    return 'accountant_review_mark', row['id'], Touched('accountant_review_mark', row['id'], 'create', None, 1, row, db='company')


def _common_as(user_id, ctx):
    from bookflow.hub.users import common
    return common(user_id, ctx.interface.value)


# ------------------------------------------------------------------ review support

def _accountant_ids(s):
    ids = {r['user_id'] for r in _all(s, 'SELECT user_id FROM accountant_designations')}
    ids |= {r['accountant_user_id'] for r in _all(s, 'SELECT DISTINCT accountant_user_id FROM accountant_imports')}
    ids |= {r['accountant_user_id'] for r in _all(s, 'SELECT DISTINCT accountant_user_id FROM accountant_review_marks')}
    return ids


_CHANGE_COMMANDS = ('journal post', 'journal update', 'journal void', 'accountant reclassify', 'accountant mark-entry',
                    'accountant reviewed-through', 'note add', 'note edit', 'review mark', 'accountant-copy import')


def changes_report(s, inp) -> m.AccountantChangesOutput:
    from bookflow.company.info import principal_names
    ids = _accountant_ids(s)
    if not ids:
        return m.AccountantChangesOutput(count=0, rows=[], journal_entries=0, reclassifications=0, notes=0, review_marks=0)
    params = dict(ids=json.dumps(sorted(ids)), cmds=json.dumps(list(_CHANGE_COMMANDS)))
    where = """FROM audit_events e WHERE (e.actor_id IN (SELECT value FROM json_each(:ids))
               OR e.on_behalf_of IN (SELECT value FROM json_each(:ids))) AND e.command IN (SELECT value FROM json_each(:cmds))"""
    count = _one(s, f'SELECT COUNT(*) AS n {where}', **params)['n']
    events = _all(s, f'SELECT e.id, e.at, e.command, e.summary, e.actor_id, e.actor_kind, e.on_behalf_of, e.reason, e.source_ref {where} ORDER BY e.seq DESC LIMIT :lim',
                  lim=inp.limit, **params)
    entries = {}
    for x in _all(s, """SELECT event_id, record_type, record_id FROM audit_entries WHERE event_id IN (SELECT value FROM json_each(:ev))
                        AND record_type IN ('transaction', 'note', 'transaction_review', 'accountant_review_mark', 'accountant_adjustment')""",
                  ev=json.dumps([e['id'] for e in events])):
        entries.setdefault(x['event_id'], []).append(f"{x['record_type']}:{x['record_id']}")
    names = principal_names(s.company, ids | {e['actor_id'] for e in events if e['actor_id']})

    def who(e):
        """(accountant, agent): an agent's change is the accountant's it acted for, naming the agent too."""
        if e['actor_kind'] != 'human' and e['on_behalf_of'] in ids:
            return e['on_behalf_of'], e['actor_id']
        return e['actor_id'], None

    rows = [m.AccountantChangeRow(at=e['at'], command=e['command'], summary=e['summary'], accountant_user_id=who(e)[0],
                                  accountant_name=names.get(who(e)[0]), agent_id=who(e)[1], agent_name=names.get(who(e)[1]),
                                  reason=e['reason'], source_ref=e['source_ref'],
                                  from_copy=(e['source_ref'] or '').startswith('accountant-copy:'),
                                  records=entries.get(e['id'], [])) for e in events]
    kinds = _one(s, f"""SELECT
        (SELECT COUNT(*) FROM (SELECT transaction_id FROM accountant_adjustments UNION
          SELECT t.id FROM transactions t JOIN transaction_revisions r ON r.transaction_id=t.id AND r.revision_number=1
          JOIN audit_events e ON e.id=r.audit_event_id WHERE t.type='journal_entry'
          AND (e.actor_id IN (SELECT value FROM json_each(:ids)) OR e.on_behalf_of IN (SELECT value FROM json_each(:ids))))) AS journals,
        (SELECT COUNT(*) FROM accountant_adjustments WHERE kind='reclassification') AS reclass,
        (SELECT COUNT(DISTINCT ae.record_id) FROM audit_entries ae JOIN audit_events e ON e.id=ae.event_id
          WHERE ae.record_type='note' AND ae.action='create' AND (e.actor_id IN (SELECT value FROM json_each(:ids))
          OR e.on_behalf_of IN (SELECT value FROM json_each(:ids)))) AS notes,
        (SELECT COUNT(*) FROM audit_entries ae JOIN audit_events e ON e.id=ae.event_id WHERE ae.record_type='transaction_review'
          AND (e.actor_id IN (SELECT value FROM json_each(:ids)) OR e.on_behalf_of IN (SELECT value FROM json_each(:ids)))) AS marks""", ids=params['ids'])
    return m.AccountantChangesOutput(count=count, rows=rows, journal_entries=kinds['journals'], reclassifications=kinds['reclass'],
                                     notes=kinds['notes'], review_marks=kinds['marks'])


def _balances(s, ids, as_of):
    if not ids:
        return {}
    return {r['account_id']: int(r['net']) for r in _all(s, """
        SELECT pl.account_id, SUM(pl.debit_minor_units - pl.credit_minor_units) AS net FROM posting_lines pl
        JOIN posting_batches pb ON pb.id=pl.batch_id WHERE pb.effective_date <= :d
          AND pl.account_id IN (SELECT value FROM json_each(:ids)) GROUP BY pl.account_id""", d=as_of, ids=json.dumps(sorted(ids)))}


def checklist(s, inp) -> m.ReviewChecklistOutput:
    from bookflow.company.accounts import NORMAL_BALANCE
    from bookflow.company.entry_review_reports import EntriesToReviewInput, entries_to_review
    from bookflow.company import uncategorized as unc
    currency = s.company_info_row['home_currency']
    signed = lambda net, kind: net if NORMAL_BALANCE.get(kind, 'debit') == 'debit' else -net  # noqa: E731
    review = entries_to_review(EntriesToReviewInput(as_of=inp.as_of, limit=50), s, principal_id=None)
    entries = [m.ChecklistEntry(transaction_id=r.transaction_id, type=r.transaction_type, number=r.number, date=r.date,
                                flags=list(r.flags)) for r in review.rows]
    waiting = unc.uncategorized(unc.UncategorizedInput(limit=1), s)
    uncategorized = [m.ChecklistBalance(account_id=a.account_id, name=a.name, count=a.count, balance=a.amount)
                     for a in waiting.accounts if a.count]
    cash = _all(s, "SELECT id, full_name, type FROM accounts WHERE type IN ('bank','credit_card') AND active=1 ORDER BY full_name")
    last = {r['account_id']: r['d'] for r in _all(s, 'SELECT account_id, MAX(statement_date) AS d FROM reconciliation_active_certificates GROUP BY account_id')}
    active_ids = {r['account_id'] for r in _all(s, """SELECT DISTINCT pl.account_id FROM posting_lines pl JOIN posting_batches pb
        ON pb.id=pl.batch_id WHERE pb.effective_date <= :d""", d=inp.as_of)}
    bal = _balances(s, {a['id'] for a in cash}, inp.as_of)
    unreconciled = [m.ChecklistAccount(account_id=a['id'], name=a['full_name'], type=a['type'], last_reconciled=last.get(a['id']),
                                       balance=_money(signed(bal.get(a['id'], 0), a['type']), currency))
                    for a in cash if a['id'] in active_ids and (last.get(a['id']) or '') < inp.as_of]
    opens = _all(s, """SELECT id, full_name, type FROM accounts WHERE type IN ('accounts_receivable','accounts_payable')
                       OR system_role='undeposited_funds' ORDER BY type, full_name""")
    obal = _balances(s, {a['id'] for a in opens}, inp.as_of)
    open_items = [m.ChecklistBalance(account_id=a['id'], name=a['full_name'], balance=_money(signed(obal.get(a['id'], 0), a['type']), currency))
                  for a in opens if obal.get(a['id'])]
    mark = reviewed_through(s)
    changes = changes_report(s, m.AccountantChangesInput(limit=1)).count
    n = review.totals.entries
    items = [
        m.ChecklistItem(key='entries_to_review', title='Entries to review', done=n == 0,
                        detail=f'{n} entries on the entries-to-review list through {inp.as_of}.' if n else 'Nothing flagged for review.'),
        m.ChecklistItem(key='uncategorized', title='Uncategorized balances (Ask My Accountant)', done=not uncategorized,
                        detail='; '.join(f'{u.name}: {u.count} entries, {u.balance.amount}' for u in uncategorized) or 'Nothing waiting.'),
        m.ChecklistItem(key='unreconciled', title='Bank and card reconciliations', done=not unreconciled,
                        detail='; '.join(f"{a.name}: last reconciled {a.last_reconciled or 'never'}" for a in unreconciled)
                        or f'Every active bank and card account is reconciled through {inp.as_of}.'),
        m.ChecklistItem(key='open_items', title='Open receivables, payables and undeposited funds', done=not open_items,
                        detail='; '.join(f'{o.name}: {o.balance.amount}' for o in open_items) or 'Nothing open.'),
        m.ChecklistItem(key='reviewed_through', title='Reviewed through', done=bool(mark and mark.through >= inp.as_of),
                        detail=f'The accountant reviewed through {mark.through}.' if mark else 'The accountant has not marked a review yet.'),
    ]
    return m.ReviewChecklistOutput(as_of=inp.as_of, items=items, entries_to_review=entries, entries_to_review_count=n,
                                   uncategorized=uncategorized, unreconciled=unreconciled, open_items=open_items,
                                   reviewed_through=mark, accountant_changes=changes)
