"""Guided opening balances (R107 increment 7, design/bulk-import.md §6): a business with no old books moves in.

An **opening draft** keeps what the owner types toward an opening, so a closed browser loses nothing: the cutover date,
each step's rows (balances they know, open invoices, unpaid bills, stock) as pasted text or an attachment, the totals
they gave to check against, and the stock items a person confirmed may be made. It posts nothing itself:
`opening-draft plan`, `apply` and `tie-out` turn it into the `cutover` engine's input (the `entered_*` file kinds,
read by ``cutover_sources``) and call that engine, which is the only thing that posts an opening. So an agent has one
noun, and every exception, the clearing account and the tie-out are the cutover's own.

- **The totals given** become balance rows on the receivables, payables and inventory accounts; a total left out is
  taken from the rows and labelled `derived` in the plan's checks and the tie-out.
- **All or nothing.** `apply` posts as one batch of the shared ledger (kind `opening`). A command refused part way
  undoes what the opening posted through that batch (`rolled_back`; `failed` when the undo is itself refused, and
  `opening-draft reverse` finishes it once cleared). A run stopped any other way (a lost connection, a killed process)
  stays `applying`, and the same `apply` continues it: every record made before is `already_in`.
- **Reversible as one unit**, for as long as nothing later depends on it: `opening-draft reverse` runs the batch
  ledger's plan (each invoice, bill and journal deleted with its history preserved, each adjustment voided, each list
  record it made renamed and deactivated), refused naming what blocks it (a payment applied to an opening invoice).
- **`accept_differences`** posts an opening whose given totals do not tie to their rows; the difference stays in the
  clearing account. It is the capability `opening.accept-differences`: people hold it at standard, an agent only when
  its principal grants it (design/principal-directives.md §1).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches
from bookflow.company import cutover
from bookflow.company import cutover_sources as src
from bookflow.company import schema as c
from bookflow.company.cutover_models import CutoverApplyInput, CutoverTieOutInput
from bookflow.company.ledger_reports import money
from bookflow.core.ids import new_id

KIND = 'opening'
OWNER = 'cutover.apply'  # the commit owner of a move-in's own writes
ACCEPT_CAPABILITY = 'opening.accept-differences'
STEPS = ('balances', 'open_invoices', 'unpaid_bills', 'stock')
STEP_KINDS = {'balances': 'entered_balances', 'open_invoices': 'entered_open_invoices',
              'unpaid_bills': 'entered_unpaid_bills', 'stock': 'entered_stock'}
STEP_NAMES = {'balances': 'balances', 'open_invoices': 'open invoices', 'unpaid_bills': 'unpaid bills', 'stock': 'stock'}
GIVEN_ROLES = {'receivables': 'accounts_receivable', 'payables': 'accounts_payable', 'stock': 'inventory_asset'}
GIVEN_NAME = 'totals you gave'
WRITABLE = ('draft', 'planned', 'rolled_back')

batches.register(batches.Adapter(KIND, 'record', label='Opening balances'))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


# ------------------------------------------------------------------ reading

def row(s, draft_id: str) -> dict:
    found = s.company.conn.execute(sa.select(c.opening_drafts).where(c.opening_drafts.c.id == draft_id)).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'opening_draft', 'selector': draft_id})
    return dict(found)


def _places(s) -> tuple[str, int]:
    from bookflow.core.money import minor_units_of
    currency = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar()
    return currency, minor_units_of(currency)


def _step_files(draft: dict) -> dict[str, dict]:
    return json.loads(draft['files'] or '{}')


def _given(draft: dict) -> dict[str, str]:
    return json.loads(draft['given'] or '{}')


def _source(s, step: str, entry: dict) -> src.SourceFile:
    if entry.get('attachment'):
        _, text = cutover._attachment_text(s, entry['attachment'])
    else:
        text = entry.get('content') or ''
    return src.SourceFile(STEP_NAMES[step], STEP_KINDS[step], src.digest(text), text, attachment=entry.get('attachment'),
                          date_order=entry.get('date_order'))


def _step_out(s, step: str, entry: dict, places: int) -> dict:
    """A step as given, with what its rows alone show: the guided page's cell errors before any plan."""
    source = _source(s, step, entry)
    read = src.read([source], places) if source.text.strip() else src.Sources()
    return {'step': step, 'kind': STEP_KINDS[step], 'source': 'attachment' if entry.get('attachment') else 'content',
            'attachment': entry.get('attachment'), 'date_order': entry.get('date_order'), 'rows': source.rows,
            'content': None if entry.get('attachment') else entry.get('content'),
            'problems': [{'code': p.code, 'line': p.line, 'problem': p.problem, 'fix': p.fix, 'severity': p.severity,
                          'subject': p.subject} for p in read.problems]}


def out(s, draft: dict, *, next_: str | None = None) -> dict:
    currency, places = _places(s)
    files = _step_files(draft)
    given = _given(draft)
    shown = {}
    for name in GIVEN_ROLES:
        value = given.get(name)
        shown[name] = money(src.parse_amount(value, places), currency) if value not in (None, '') else None
    nexts = {'draft': f"opening-draft plan {draft['id']}", 'planned': f"opening-draft apply {draft['id']}",
             'applying': f"opening-draft apply {draft['id']}", 'applied': f"opening-draft tie-out {draft['id']}",
             'rolled_back': f"opening-draft plan {draft['id']}", 'failed': f"opening-draft reverse {draft['id']}"}
    return {'id': draft['id'], 'label': draft['label'], 'as_of': draft['as_of'], 'state': draft['state'],
            'version': draft['version'],
            'steps': [_step_out(s, step, files[step], places) for step in STEPS if step in files],
            'given': shown, 'make_items': json.loads(draft['make_items'] or '[]'),
            'clearing_account': draft['clearing_account'], 'batch': draft['batch_id'],
            'summary': json.loads(draft['summary']) if draft['summary'] else None,
            'created_at': draft['created_at'], 'updated_at': draft['updated_at'], 'applied_at': draft['applied_at'],
            'next': next_ if next_ is not None else nexts.get(draft['state'])}


def query(s, inp) -> list[dict]:
    q = sa.select(c.opening_drafts).order_by(c.opening_drafts.c.created_at.desc(), c.opening_drafts.c.id.desc())
    if inp.state:
        q = q.where(c.opening_drafts.c.state == inp.state)
    return [out(s, dict(r)) for r in s.company.conn.execute(q.limit(inp.limit)).mappings()]


# ------------------------------------------------------------------ writing the draft (in the command's transaction)

def _grid(value) -> dict | None:
    """A step's stored form; None removes the step (an empty paste clears it)."""
    if value is None:
        return None
    if value.attachment:
        return {'attachment': value.attachment, 'date_order': value.date_order}
    if not (value.content or '').strip():
        return None
    return {'content': value.content, 'date_order': value.date_order}


def _check_given(s, given) -> dict[str, str]:
    _, places = _places(s)
    kept = {}
    for name in GIVEN_ROLES:
        value = getattr(given, name)
        if value in (None, ''):
            continue
        try:
            minor = src.parse_amount(value, places)
        except ValueError as error:
            raise BookflowError('E_VALIDATION', message=f'given.{name}: {error}',
                                details={'fields': [{'field': f'given.{name}', 'problem': str(error)}]}) from None
        if minor is None:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': f'given.{name}', 'problem': 'not an amount'}]})
        kept[name] = src._show(minor, places).replace(',', '')
    return kept


def _check_attachments(s, files: dict) -> None:
    for entry in files.values():
        if entry.get('attachment'):
            cutover._attachment_text(s, entry['attachment'])  # refused here, not at plan, when it is not readable


def prepare_create(s, ctx, inp) -> dict:
    files = {step: grid for step in STEPS if (grid := _grid(getattr(inp, step))) is not None}
    _check_attachments(s, files)
    at = now()
    return {'id': new_id(), 'label': (inp.label or f'Opening balances as of {inp.as_of}')[:200], 'as_of': inp.as_of,
            'state': 'draft', 'files': json.dumps(files, sort_keys=True), 'given': json.dumps(_check_given(s, inp.given)),
            'make_items': json.dumps(sorted(set(inp.make_items))), 'clearing_account': inp.clearing_account,
            'batch_id': None, 'summary': None, 'version': 1, 'created_at': at, 'created_by': s.actor.id,
            'updated_at': at, 'applied_at': None}


def insert(s, values: dict) -> None:
    s.company.conn.execute(sa.insert(c.opening_drafts).values(**values))


def prepare_update(s, ctx, inp) -> tuple[dict, dict]:
    draft = row(s, inp.draft)
    if draft['version'] != inp.expected_version:
        raise BookflowError('E_VERSION_CONFLICT', message=(
            f"Opening draft {draft['id']} is at version {draft['version']}, not {inp.expected_version}; read it again."),
            details={'record_type': 'opening_draft', 'record_id': draft['id'], 'current_version': draft['version']})
    if draft['state'] not in WRITABLE:
        raise BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} is {draft['state']}; only a draft not yet applied can change"
            + (" (reverse the opening first to start again)." if draft['state'] == 'applied' else ".")),
            details={'field': 'draft', 'state': draft['state']})
    files = _step_files(draft)
    for step in STEPS:
        if step not in inp.model_fields_set or getattr(inp, step) is None:
            continue
        grid = _grid(getattr(inp, step))
        if grid is None:
            files.pop(step, None)
        else:
            files[step] = grid
    _check_attachments(s, files)
    changes: dict[str, Any] = {'files': json.dumps(files, sort_keys=True)}
    if inp.as_of is not None:
        changes['as_of'] = inp.as_of
    if inp.label is not None:
        changes['label'] = inp.label[:200]
    if inp.given is not None:
        changes['given'] = json.dumps(_check_given(s, inp.given))
    if inp.make_items is not None:
        changes['make_items'] = json.dumps(sorted(set(inp.make_items)))
    if 'clearing_account' in inp.model_fields_set:
        changes['clearing_account'] = inp.clearing_account
    changes.update(state='draft', summary=None, version=draft['version'] + 1, updated_at=now())
    return draft, changes


def write(s, draft_id: str, changes: dict) -> None:
    s.company.conn.execute(sa.update(c.opening_drafts).where(c.opening_drafts.c.id == draft_id).values(**changes))


def prepare_discard(s, inp) -> tuple[dict, dict]:
    draft = row(s, inp.draft)
    nothing_stands = _batch_state(s, draft['batch_id']) in (None, 'reversed') or not s.company.conn.execute(
        sa.select(sa.func.count()).select_from(c.batch_effects).where(c.batch_effects.c.batch_id == draft['batch_id'])
    ).scalar()
    if draft['state'] == 'applied' or (draft['state'] in ('applying', 'failed') and not nothing_stands):
        raise BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} is {draft['state']}: what it posted stays until `opening-draft reverse "
            f"{draft['id']}` undoes it as one unit; discard it after that."), details={'field': 'draft', 'state': draft['state']})
    if draft['state'] == 'discarded':
        raise BookflowError('E_VALIDATION', message=f"Opening draft {draft['id']} is discarded already.",
                            details={'field': 'draft', 'state': 'discarded'})
    return draft, {'state': 'discarded', 'version': draft['version'] + 1, 'updated_at': now()}


# ------------------------------------------------------------------ the cutover engine's input

def cutover_input(s, draft: dict, model=CutoverApplyInput, **extra):
    """The draft as `cutover`'s input: each step one entered file, and the totals given as a balances file on the
    receivables, payables and inventory accounts (by ID, so the company's own names never matter)."""
    files = []
    for step, entry in sorted(_step_files(draft).items(), key=lambda item: STEPS.index(item[0])):
        given = {'kind': STEP_KINDS[step], 'name': STEP_NAMES[step], 'date_order': entry.get('date_order')}
        if entry.get('attachment'):
            given['attachment'] = entry['attachment']
        else:
            given['content'] = entry['content']
        files.append(given)
    given = _given(draft)
    if given:
        roles = {r['system_role']: r['id'] for r in s.company.conn.execute(
            sa.select(c.accounts.c.id, c.accounts.c.system_role).where(c.accounts.c.system_role.isnot(None))).mappings()}
        lines = ['Account,Balance'] + [f"{roles.get(GIVEN_ROLES[name], GIVEN_ROLES[name])},{given[name]}"
                                       for name in GIVEN_ROLES if name in given]
        files.append({'kind': 'entered_balances', 'name': GIVEN_NAME, 'content': '\n'.join(lines) + '\n'})
    if not files:
        raise BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} has nothing in it yet: give the balances you know, open invoices, unpaid bills "
            "or stock first (`opening-draft update`)."), details={'field': 'draft', 'problem': 'empty'})
    data = {'as_of': draft['as_of'], 'files': files, **extra}
    if draft['clearing_account']:
        data['clearing_account'] = draft['clearing_account']
    return model.model_validate(data)


def make_items(draft: dict) -> list[str]:
    return json.loads(draft['make_items'] or '[]')


def summary(result) -> str:
    """What the page and `show` keep of the last plan or apply."""
    data = result.model_dump(mode='json')
    return json.dumps({k: data.get(k) for k in ('ready', 'summary', 'blocking', 'counts', 'checks', 'clearing',
                                                'created', 'already_in')}, sort_keys=True)


def plan(s, ctx, draft: dict):
    inp = cutover_input(s, draft)
    built = cutover.build(s, inp, make_items=make_items(draft))
    return cutover.plan_output(built, inp)


# ------------------------------------------------------------------ accept_differences: a capability

def require_accept(s) -> None:
    """`accept_differences` is the capability opening.accept-differences: a person holds it at standard, as before;
    an agent only by its principal's explicit grant, and only while the principal holds it too (R187)."""
    from bookflow.hub import access
    from bookflow.hub import capabilities
    person = capabilities._is_person(s)

    def refused(why: str) -> BookflowError:
        holds = {'capability_not_granted': f'your principal has not granted you {ACCEPT_CAPABILITY}',
                 'principal_lacks_capability': f'your principal does not hold {ACCEPT_CAPABILITY} themselves'}[why]
        return BookflowError('E_PERMISSION', message=(
            f'Posting an opening that does not tie (accept_differences) needs the capability {ACCEPT_CAPABILITY}, and '
            f'{holds}. Ask your principal to grant it if this is yours to do.'),
            details={'capability': ACCEPT_CAPABILITY, 'reason': why, 'field': 'accept_differences',
                     'command': 'opening-draft apply', 'required_role': 'standard',
                     'next_step': (f'Ask your principal (the person you act for) to grant {ACCEPT_CAPABILITY} on your '
                                   f'membership (membership grant <you> --grants {ACCEPT_CAPABILITY}) if this is yours to do.')})
    if not person and (s.actor is None or not capabilities._activated(s) or not capabilities.granted(
            s, s.actor.id, ACCEPT_CAPABILITY, capabilities._company_scopes(s))):
        raise refused('capability_not_granted')
    try:
        access.require_resource(s, 'opening.accept-differences', 'standard')
    except BookflowError as error:
        if person or error.code != 'E_PERMISSION':
            raise
        raise refused('principal_lacks_capability') from None


# ------------------------------------------------------------------ apply: one batch, all or nothing

STANDING = ('applying', 'applied', 'failed')  # an opening whose postings are in the books


def _batch_state(s, batch_id: str | None) -> str | None:
    if not batch_id:
        return None
    return s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == batch_id)).scalar()


def prepare_apply(s, ctx, inp):
    draft = row(s, inp.draft)
    if draft['state'] in ('applied', 'reversed', 'discarded', 'failed'):
        hint = {'applied': f"it is applied; `opening-draft tie-out {draft['id']}` proves it",
                'failed': f"undo what it posted first (`opening-draft reverse {draft['id']}`)",
                'reversed': 'it was reversed; make a new draft to start again',
                'discarded': 'it was discarded'}[draft['state']]
        raise BookflowError('E_VALIDATION', message=f"Opening draft {draft['id']} cannot be applied: {hint}.",
                            details={'field': 'draft', 'state': draft['state']})
    if _batch_state(s, draft['batch_id']) in ('reversing', 'blocked'):
        raise BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} cannot be applied while the undo of what it posted is part way: finish it with "
            f"`opening-draft reverse {draft['id']}` first."), details={'field': 'draft', 'batch_state': 'reversing'})
    other = s.company.conn.execute(sa.select(c.opening_drafts.c.id, c.opening_drafts.c.label, c.opening_drafts.c.state)
                                   .where(c.opening_drafts.c.state.in_(STANDING), c.opening_drafts.c.id != draft['id'])
                                   ).mappings().first()
    if other is not None:
        raise BookflowError('E_VALIDATION', message=(
            f"Another opening stands: {other['label']} ({other['id']}, {other['state']}). A company has one opening; "
            f"reverse that one first (`opening-draft reverse {other['id']}`) or correct the books after it."),
            details={'field': 'draft', 'standing': other['id'], 'standing_state': other['state']})
    accept = cutover.ACCEPTABLE if inp.accept_differences else ()
    if inp.accept_differences:
        require_accept(s)
    cinp = cutover_input(s, draft)
    planned = cutover.prepare_apply(s, ctx, cinp, make_items=make_items(draft), accept=accept)
    planned.data['draft'] = draft
    return planned


def _transaction(s, work) -> Any:
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            result = work()
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return result


def _mark(s, ctx, draft_id: str, changes: dict, command: str, what: str) -> None:
    """A state change of the draft made inside apply or reverse, with its own audit event (review L4), inside the
    caller's open transaction."""
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    current = row(s, draft_id)
    values = {**changes, 'version': current['version'] + 1, 'updated_at': now()}
    write(s, draft_id, values)
    write_event_to(s.company, ctx, command, f"{what} {current['label']}",
                   [Touched('opening_draft', draft_id, 'update', current['version'], values['version'],
                            {'state': values.get('state', current['state'])}, db='company')],
                   actor_id=s.actor.id, actor_kind=s.actor.kind)


def outside_batch(result) -> list[str]:
    """What an applied opening changed that its batch does not undo (review L4): said in the result and kept in the
    draft's summary, so `reverse` can say what stays."""
    out = []
    if result.clearing_account_id:
        out.append('the clearing account the opening balanced against (accounts keep their history; it stands at 0.00 '
                   'once the opening is undone, and the next opening uses it again)')
    if result.default_sales_tax_item:
        out.append(f'the company default sales tax item, set to {result.default_sales_tax_item}')
    if 'the next invoice number is' in (result.summary or ''):
        out.append('the next invoice number, carried on after the opening\'s invoices')
    kept = [f.attachment for f in result.files if f.attachment]
    if kept:
        out.append(f'the {len(kept)} step(s) kept as company attachments')
    return out


def apply(planned, ctx, s) -> tuple[dict, Any]:
    """Post the draft through the cutover engine as one batch; see the module's notes for a refusal or a stop."""
    from bookflow.core.dispatch import _upsert_principals
    draft = planned.data['draft']

    def begin():
        current = row(s, draft['id'])
        if current['version'] != draft['version']:
            raise BookflowError('E_VERSION_CONFLICT', message=f"Opening draft {draft['id']} changed meanwhile; apply again.",
                                details={'record_type': 'opening_draft', 'record_id': draft['id']})
        batch_id, opened = current['batch_id'], False
        if _batch_state(s, batch_id) != 'live':
            batch_id, opened = batches._insert_batch(s, ctx, kind=KIND, label=current['label'],
                                                     scope=f'opening:{current["id"]}', ordering='record',
                                                     source_type='opening_draft', source_id=current['id'],
                                                     reason=ctx.reason), True
        _mark(s, ctx, current['id'], {'state': 'applying', 'batch_id': batch_id}, 'opening-draft apply', 'applying')
        return batch_id, opened
    # The batch and the draft's `applying` commit in the command's own transaction, before the first posting; the
    # cutover engine then opens and commits one transaction per posting.
    with s.commits.operation(OWNER, s.hub, s.company):
        try:
            _upsert_principals(s, ctx)
            batch_id, opened = begin()
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    s.company.raw.execute('BEGIN IMMEDIATE')  # cutover.apply commits the session's open transaction first
    planned.data['batch_id'] = batch_id
    try:
        applied = cutover.apply(planned, ctx, s)
    except BookflowError as error:
        if error.code != 'E_CUTOVER_INCOMPLETE':
            raise
        _roll_back(s, ctx, draft['id'], batch_id, error, own=opened)
        raise
    result = applied.output
    stays = outside_batch(result)
    kept = json.loads(summary(result))
    kept['outside_batch'] = stays
    _transaction(s, lambda: _mark(s, ctx, draft['id'], {'state': 'applied', 'applied_at': now(),
                                                        'summary': json.dumps(kept, sort_keys=True)},
                                  'opening-draft apply', 'applied'))
    return row(s, draft['id']), result


def _roll_back(s, ctx, draft_id: str, batch_id: str, error: BookflowError, *, own: bool) -> None:
    """All or nothing: a refusal part way undoes what the opening posted, through its batch. When this call opened the
    batch, every record in it is this call's own writes, and the undo is the command's own promise: its deletes need
    no delete grant of the caller's (review M1). When the call resumed a batch an earlier call began (perhaps someone
    else's), the undo needs the caller's own grants, like any reversal, or the draft ends `failed` naming them."""
    undone = s.company.conn.execute(sa.select(sa.func.count()).select_from(c.batch_effects).where(
        c.batch_effects.c.batch_id == batch_id)).scalar()
    try:
        while True:
            done = batches.reverse(s, ctx, batch_id, reason='Undo an opening that stopped part way', dry_run=False,
                                   limit=batches.DEFAULT_SLICE, own_writes=own)
            if done['state'] != 'reversing':
                break
        state, undo = 'rolled_back', None
    except BookflowError as refused:
        state, undo = 'failed', {'code': refused.code, 'message': refused.message,
                                 'blockers': (refused.details or {}).get('blockers')}
    _transaction(s, lambda: _mark(s, ctx, draft_id, {'state': state}, 'opening-draft apply',
                                  'rolled back' if state == 'rolled_back' else 'failed to roll back'))
    created = undone
    if state == 'rolled_back':
        error.message = (error.message.split(' The ')[0] + f" All or nothing: the {created} record(s) it had posted were "
                         "undone as one unit, so nothing of this opening stays in the books. Fix the cause and apply again.")
    else:
        error.message = (error.message.split(' The ')[0] + f" Undoing the {created} record(s) it had posted was refused "
                         f"({undo['message']}); clear that and run `opening-draft reverse {draft_id}`.")
    error.args = (f'{error.code}: {error.message}',)
    error.details = {**(error.details or {}), 'rolled_back': state == 'rolled_back', 'draft_state': state,
                     **({'undo_refused': undo} if undo else {})}


# ------------------------------------------------------------------ tie-out and reverse

def tie_out(s, ctx, draft: dict, detail: str):
    if draft['state'] not in ('applied', 'applying', 'failed'):
        raise BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} is {draft['state']}: tie-out compares the books after `opening-draft apply`; "
            f"before that, `opening-draft plan {draft['id']}` shows the checks."), details={'field': 'draft', 'state': draft['state']})
    return cutover.tie_out(s, ctx, cutover_input(s, draft, CutoverTieOutInput, detail=detail))


def reverse(s, ctx, inp, *, dry_run: bool) -> tuple[dict, dict]:
    """Undo an opening as one unit. A reversal stopped part way leaves the draft `failed`, so its next step is this
    command again (review H2). One that finishes on a draft that never finished applying (stopped, or its own undo
    refused) returns it to `rolled_back`, editable and applied again without pasting anything anew (review L5); an
    applied opening undone ends `reversed`."""
    draft = row(s, inp.draft)
    if not draft['batch_id'] or draft['state'] not in ('applied', 'failed', 'applying'):
        raise BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} is {draft['state']}; only an applied opening is reversed."),
            details={'field': 'draft', 'state': draft['state']})
    # An applied opening undone ends `reversed`, however many runs the undo took; one that never finished applying
    # returns to `rolled_back` (review L5).
    ended = 'reversed' if draft['applied_at'] else 'rolled_back'
    if _batch_state(s, draft['batch_id']) == 'reversed':  # undone already (a mark that never landed): no dead end
        if not dry_run:
            _transaction(s, lambda: _mark(s, ctx, draft['id'], {'state': ended}, 'opening-draft reverse', 'reversed'))
        return row(s, draft['id']), {'batch': {'id': draft['batch_id']}, 'state': 'reversed', 'reversed_now': 0,
                                     'remaining': 0, 'steps': [], 'blockers': [], 'fingerprint': None,
                                     'reversal_batch': None, 'event_id': None, 'stays': []}
    fingerprint = inp.fingerprint
    try:
        while True:
            done = batches.reverse(s, ctx, draft['batch_id'], reason=ctx.reason, dry_run=dry_run,
                                   limit=batches.DEFAULT_SLICE, fingerprint=fingerprint)
            fingerprint = None
            if dry_run or done['state'] != 'reversing':
                break
    except BookflowError:
        if not dry_run and _batch_state(s, draft['batch_id']) in ('reversing', 'blocked') and draft['state'] != 'failed':
            _transaction(s, lambda: _mark(s, ctx, draft['id'], {'state': 'failed'}, 'opening-draft reverse',
                                          'undo stopped part way for'))
        raise
    if not dry_run and done['state'] == 'reversed':
        _transaction(s, lambda: _mark(s, ctx, draft['id'], {'state': ended}, 'opening-draft reverse', 'reversed'))
    done = dict(done)
    done['stays'] = (json.loads(draft['summary']) if draft['summary'] else {}).get('outside_batch') or [
        'the clearing account, at 0.00 (accounts keep their history)']
    return row(s, draft['id']), done
