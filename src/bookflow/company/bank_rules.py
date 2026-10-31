"""Bank rules and learning (R108 increment 4, design/bank-feeds.md §5; principal-directives.md §3).

A **rule** fits a waiting line by its account (or every feed account), direction and conditions (all must hold; text
case-folded with spaces collapsed; no regular expressions) and proposes what the line is: **categorise** (a payee,
account, class and memo, which `bank-line add` takes when the caller names none), **transfer** (to an account) or
**ignore** (with a reason). Active rules are tried by priority (lower first), then a rule for the line's own account
before one for every account, then specificity (equals before starts_with before contains, then more conditions),
then oldest first; **the first that fits wins**, and the line records it (``rule_id``) with the reason. A rule never
touches a decided line.

**History proposals.** With no rule, a line's payee key (the payee as Bookflow reads it: letters only, store and
reference numbers, dates and processor words dropped) is looked up among the account's lines already added with one
account: at least two, one answer (payee, account, class) at least two thirds of them, and it is proposed ("from
history: 4 of 5 times").

**Learning.** When a person or agent adds a line and the same key now has at least two lines added the same way in a
row, a rule is learned (``source`` learned, priority 1000, condition payee_key equals the key) as ``suggested`` (the
company preference ``bank_rule_learning`` ask, the default), ``active`` (always) or not at all (never). It records the
import batch of the line that tipped it (``learned_from_batch``): reversing that import retires the rule unless it was
edited. A declined suggestion's key is remembered per account and never suggested again. Learning never asks to
auto-add.

**Auto-add and ignore rules are automations** (principal-directives.md §3): drafted by anyone who may write a rule,
acting on their own only once a holder of ``automate.bank-rule`` makes them live (`bank-rule update --live --confirm`,
one check of the exact details), and then at each import under that holder's authority, checked at every import
(`automation.firing_check`): otherwise paused with one to-do, and their lines wait. Each entry or ignore they make
cites the rule and its establisher (``source_ref`` ``automation:bank-rule:...;bank-line:<line>``) and is recorded in the
import's own batch, so one reversal undoes a misread rule.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import automation, bank_feeds, batches, schema as c, statement_files as files
from bookflow.company import bank_rule_models as m
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched

CAPABILITY = 'automate.bank-rule'
WRITTEN_PRIORITY = 100
LEARNED_PRIORITY = 1000
# Learning threshold (design/bank-feeds.md §12, builder default 4): two consistent answers; a history proposal needs
# at least two earlier answers, one of them at least two thirds.
LEARN_AFTER = 2
HISTORY_AT_LEAST = 2
CHECK_LINES = 20
_OP_RANK = {'equals': 0, 'starts_with': 1, 'contains': 2, 'between': 3}
# What a rule does: a change to any of these is operative (it changes what the rule does or which lines it fits).
OPERATIVE = ('account_id', 'direction', 'conditions', 'action', 'payee_type', 'payee_id', 'category_account_id',
             'class_id', 'memo', 'transfer_account_id', 'ignore_reason', 'auto_add', 'priority', 'status')
_DEFINITION = ('name', *OPERATIVE, 'payee', 'source', 'state', 'established_by', 'established_for', 'learned_key',
               'learned_from_batch', 'edited')


def _refuse(field: str, message: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', message=message, details={
        'field': field, 'fields': [{'field': field, 'problem': message}], **details})


# ------------------------------------------------------------------ reading text

_SPACE = re.compile(r'\s+')
_DATE = re.compile(r'\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?\b')
# Processor and channel words a bank puts before the name ("POS DEBIT", "SQ *"): dropped at the start only.
_LEAD = frozenset('POS DEBIT PURCHASE CHECKCARD DEBITCARD CARD VISA MASTERCARD SQ TST PP RECURRING PENDING POSTED '
                  'PREAUTHORIZED AUTH'.split())
# Reference trailers after the name ("WEB ID: 123", "CONF# 45", "REF 9"): dropped at the end only, once the numbers
# are gone.
_TRAIL = frozenset('WEB ID CONF REF PPD INDN DES TRACE TRN CCD'.split())
# Words that say what kind of movement a line is, a processor, or an own account, but no party: text made only of
# these names nobody (design §5: no history proposal and no learned rule from "CHECK 1234" or "VENMO PAYMENT").
_NO_PARTY = frozenset((
    'CHECK CHK CHEQUE DEPOSIT DEP MOBILE REMOTE ATM WITHDRAWAL WITHDRAW CASH TRANSFER XFER TRANS ONLINE INTERNET '
    'WIRE IN OUT TO FROM PAYMENT PMT PYMT PAY PAYROLL ACH DEBIT CREDIT INTEREST DIVIDEND SERVICE CHARGE FEE FEES '
    'MONTHLY ANNUAL MAINTENANCE OVERDRAFT NSF RETURNED RETURN ITEM ADJUSTMENT REVERSAL REFUND BRANCH TELLER POS '
    'PURCHASE CARD VENMO ZELLE PAYPAL CASHAPP CASH APP SQUARE SQ STRIPE QUICKPAY SAV SAVINGS CHECKING ACCOUNT ACCT '
    'BALANCE BAL REF CONF ID WEB TEL PHONE BANKING BANK & THE OF FOR A AN').split())


def norm(text: str | None) -> str:
    return _SPACE.sub(' ', (text or '').casefold()).strip()


def _number(word: str) -> bool:
    """A store, reference or check number: all digits, or a long run mostly of digits (1A2B3C)."""
    digits = sum(ch.isdigit() for ch in word)
    return digits == len(word) or (len(word) >= 4 and 2 * digits >= len(word))


def payee_key(text: str | None) -> str:
    """The payee as Bookflow reads it: upper case; dates, ``#`` runs, store and reference numbers and punctuation
    removed, keeping letters, ``&`` and a name's own short number (A1, 7 ELEVEN, 76); processor words dropped at the
    start and reference trailers at the end. "SHELL OIL 4471" and "SHELL OIL #12" read alike; "A1 AUTO REPAIR" and
    "B2 AUTO REPAIR" do not."""
    text = re.sub(r'#\S*', ' ', _DATE.sub(' ', (text or '').upper()))
    words = re.findall(r'[A-Z0-9&]+', re.sub(r"(?<=[A-Z])'(?=[A-Z])", '', text))
    kept = []
    for n, word in enumerate(words):
        if word.isdigit():
            if n == 0 and len(word) <= 3 and len(words) > 1:
                kept.append(word)  # a leading brand number: 76, 7 (ELEVEN), 99
            continue
        if _number(word):
            continue
        kept.append(word)
    while kept and kept[0] in _LEAD:
        kept.pop(0)
    while kept and (kept[-1] in _TRAIL or kept[-1] == '&'):
        kept.pop()
    while kept and kept[0] == '&':
        kept.pop(0)
    return ' '.join(kept)[:200]


def party_key(text: str | None) -> str | None:
    """The payee key when the text names a party; None when it is only movement, processor or account words (a check
    known only by its number, "MOBILE DEPOSIT", "VENMO PAYMENT", "ONLINE TRANSFER TO SAV"): such a line gives no
    history proposal and teaches no rule."""
    key = payee_key(text)
    if not key or all(w in _NO_PARTY for w in key.split()):
        return None
    return key


# ------------------------------------------------------------------ rules: reading

def _rows(s, where=()) -> list[dict]:
    return [dict(r) for r in s.company.conn.execute(sa.select(c.bank_rules).where(*where)).mappings()]


def get(s, selector: str) -> dict:
    t = c.bank_rules
    row = s.company.conn.execute(sa.select(t).where(sa.or_(t.c.id == selector, t.c.name_key == norm(selector))))\
        .mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'bank_rule', 'selector': selector})
    return dict(row)


def is_automation(row: dict) -> bool:
    """An auto-add or ignore rule: one that can act on its own (once live)."""
    return bool(row['auto_add']) or row['action'] == 'ignore'


def acting(row: dict) -> bool:
    """Whether this rule acts on its own at an import (its establisher is checked then)."""
    return is_automation(row) and row['status'] == 'active' and row['state'] == 'live'


def order_key(row: dict) -> tuple:
    conds = json.loads(row['conditions'])
    best = min((_OP_RANK[x['op']] for x in conds), default=3)
    return (row['priority'], 0 if row['account_id'] else 1, best, -len(conds), row['created_at'], row['id'])


def active_rules(s, account_id: str) -> list[dict]:
    t = c.bank_rules
    rows = _rows(s, (t.c.status == 'active', sa.or_(t.c.account_id == account_id, t.c.account_id.is_(None))))
    return sorted(rows, key=order_key)


def _amount_units(value: str, places: int, field: str) -> int:
    return abs(files.amount(value, places, field))


def _said(cond: dict) -> str:
    if cond['op'] == 'between':
        return f"amount between {cond['value']} and {cond['value_to']}"
    word = {'payee_key': 'payee reads as'}.get(cond['field'], f"{cond['field']} {cond['op'].replace('_', ' ')}")
    return f"{word} {cond['value']}" if cond['field'] == 'payee_key' else f"{word} {cond['value']}"


def fits(row: dict, line: dict, places: int) -> str | None:
    """Why this rule fits the line (its reason), or None. ``line``: account_id, amount (bank sign), payee, memo,
    number."""
    if row['account_id'] and row['account_id'] != line['account_id']:
        return None
    amount = line['amount']
    if row['direction'] == 'in' and amount <= 0 or row['direction'] == 'out' and amount >= 0:
        return None
    for cond in json.loads(row['conditions']):
        field, op, value = cond['field'], cond['op'], cond['value']
        if field == 'amount':
            try:
                low = _amount_units(value, places, 'value')
                high = _amount_units(cond['value_to'], places, 'value_to') if op == 'between' else low
            except BookflowError:
                return None
            if not low <= abs(amount) <= high:
                return None
            continue
        if field == 'payee_key':
            if payee_key(line['payee']) != payee_key(value):
                return None
            continue
        text, want = norm(line.get(field)), norm(value)
        if op == 'equals' and text != want or op == 'starts_with' and not text.startswith(want) \
                or op == 'contains' and want not in text:
            return None
    return f"rule '{row['name']}': " + ' and '.join(_said(x) for x in json.loads(row['conditions']))


def first_fit(rules: list[dict], line: dict, places: int) -> tuple[dict, str] | None:
    for row in rules:
        reason = fits(row, line, places)
        if reason is not None:
            return row, reason
    return None


# ------------------------------------------------------------------ history

def answers_for(s, account_id: str, key: str | None) -> list[dict]:
    """The account's live lines with this party key that were added with one account, in the order they were added
    (read by the indexed key, so history and learning cost what the payee's own lines cost)."""
    if not key:
        return []
    t = c.bank_lines
    values = [json.loads(a) for a in s.company.conn.execute(sa.select(t.c.answer).where(
        t.c.account_id == account_id, t.c.payee_key == key, t.c.live == 1, t.c.state == 'added',
        t.c.answer.is_not(None))).scalars()]
    return sorted(values, key=lambda v: v.get('at') or '')


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ('payee_type', 'payee_id', 'account_id', 'class_id'))


def from_history(answers: list[dict]) -> tuple[dict, str] | None:
    if len(answers) < HISTORY_AT_LEAST:
        return None
    groups: list[list[dict]] = []
    for a in answers:
        for g in groups:
            if _same(g[0], a):
                g.append(a)
                break
        else:
            groups.append([a])
    best = max(groups, key=len)
    if 3 * len(best) < 2 * len(answers):
        return None
    return best[-1], f'from history: {len(best)} of {len(answers)} times'


def automation_on(s) -> bool:
    """The company setting (K2): live auto-add and ignore rules act at imports unless an admin turned it off."""
    return bool(s.company.conn.execute(sa.select(c.company_info.c.bank_rule_automation)).scalar())


# ------------------------------------------------------------------ proposals on lines

def proposal_rule(row: dict, reason: str) -> dict:
    return dict(id=row['id'], name=row['name'], reason=reason[:500], action=row['action'], payee=row['payee'],
                payee_type=row['payee_type'], payee_id=row['payee_id'], account_id=row['category_account_id'],
                class_id=row['class_id'], memo=row['memo'], transfer_account_id=row['transfer_account_id'],
                ignore_reason=row['ignore_reason'], acts=None, held=None)


def _unsure(r: dict) -> str | None:
    """Why a new line is not one a live rule may act on unattended (review H1): it may already be in the books."""
    if r.get('duplicate_of'):
        return f"it may repeat line {r['duplicate_of']} (possible_duplicate)"
    result = r.get('result')
    if r.get('suggest') or (result is not None and result.status == 'suggested' and result.suggestions):
        return 'the matcher suggests an entry already in the books'
    return None


def annotate(s, prepared: dict, *, may_act: bool = True) -> None:
    """For each new waiting line of an import (or its preview): the first rule that fits, else a history proposal.
    Read afresh inside the import's own write, so a rule changed since a preview is read as it is now. A live
    auto-add or ignore rule is marked to act (``acts``) only on a line that cannot already be in the books, while
    the company lets rules act, and not on a move's re-import (``may_act``); otherwise ``held`` says why."""
    new = [r for r in prepared['new'] if r.get('state') == 'waiting']
    for r in prepared['new']:
        r.pop('rule', None)
        r.pop('history', None)
    if new:
        account_id, places = prepared['account_id'], prepared['places']
        rules = active_rules(s, account_id)
        allowed = automation_on(s) if may_act else False
        history: dict[str, list[dict]] = {}
        for r in new:
            line = r['line']
            fit = first_fit(rules, dict(account_id=account_id, amount=line.amount, payee=line.payee, memo=line.memo,
                                        number=line.number), places)
            if fit is not None:
                r['rule'] = proposal_rule(*fit)
                if acting(fit[0]):
                    why = _unsure(r) or (None if allowed else (
                        "a move's re-import only proposes" if not may_act else
                        'the company holds automated bank rules back (company update --bank-rule-automation)'))
                    if why is None:
                        r['rule']['acts'] = 'add' if fit[0]['auto_add'] else 'ignore'
                    else:
                        r['rule']['held'] = why
                continue
            key = party_key(line.payee)
            if key and key not in history:
                history[key] = answers_for(s, account_id, key)
            found = from_history(history.get(key, [])) if key else None
            if found is not None:
                answer, reason = found
                r['history'] = dict(answer, reason=reason)
    prepared['counts']['proposed'] = sum(1 for r in prepared['new'] if r.get('rule') or r.get('history'))


def merged_proposal(base: str | None, r: dict) -> str | None:
    value = json.loads(base) if base else {}
    if r.get('rule'):
        value['rule'] = r['rule']
    if r.get('history'):
        value['history'] = r['history']
    return json.dumps(value, sort_keys=True) if value else None


def proposal_fields(line: dict) -> dict | None:
    """What a waiting line's rule (categorise) or history proposal enters it as, for `bank-line add` with no payee or
    account named: payee, payee_type, account, class, memo and the rule it came from."""
    if not line.get('proposal'):
        return None
    value = json.loads(line['proposal'])
    rule = value.get('rule')
    if rule and rule.get('action') == 'categorise':
        return dict(payee=rule.get('payee_id'), payee_type=rule.get('payee_type'), account=rule.get('account_id'),
                    class_id=rule.get('class_id'), memo=rule.get('memo'), rule_id=rule['id'], source='rule',
                    reason=rule.get('reason'))
    found = value.get('history')
    if found:
        return dict(payee=found.get('payee_id'), payee_type=found.get('payee_type'), account=found.get('account_id'),
                    class_id=found.get('class_id'), memo=None, rule_id=None, source='history',
                    reason=found.get('reason'))
    return None


def proposals_of(s, line: dict) -> list:
    """The rule and history proposals a waiting line shows (design §5): with why, and the command that accepts it."""
    from bookflow.company import bank_feed_models as bm
    if not line.get('proposal'):
        return []
    value = json.loads(line['proposal'])
    out = []
    rule = value.get('rule')
    if rule:
        if rule['action'] == 'transfer':
            nxt = f"bank-line transfer --line {line['id']} --to {rule['transfer_account_id']}"
        elif rule['action'] == 'ignore':
            nxt = f"bank-line ignore --line {line['id']} --ignore-reason {rule.get('ignore_reason') or 'other'}"
        else:
            missing = [f'--{f} <which>' for f, k in (('payee', 'payee_id'), ('account', 'account_id')) if not rule.get(k)]
            nxt = ' '.join([f"bank-line add --line {line['id']}", *missing])
        out.append(bm.Proposal(kind='rule', reason=rule['reason'], unique=True, next=nxt, rule_id=rule['id'],
                               payee=rule.get('payee'), account_id=rule.get('account_id')
                               or rule.get('transfer_account_id')))
    found = value.get('history')
    if found and not rule:
        out.append(bm.Proposal(kind='history', reason=f"{found['reason']}: {found.get('payee')}", unique=True,
                               next=f"bank-line add --line {line['id']}", payee=found.get('payee'),
                               account_id=found.get('account_id')))
    return out


# ------------------------------------------------------------------ the automation kind

def _what(row: dict) -> str:
    if row['action'] == 'ignore':
        return f"ignores matching bank lines ({row['ignore_reason'] or 'other'}) at each import"
    return f"adds matching bank lines as entries to {row['payee'] or 'its payee'} at each import"


def _make_kind() -> automation.Kind:
    base = automation.table_kind(
        kind='bank-rule', capability=CAPABILITY, noun='bank rule', record_type='bank_rule', table=c.bank_rules,
        show_command='bank-rule show', edit_command='bank-rule update', live_command='bank-rule update --live',
        what=_what, edit_needs_confirm=True, due=lambda s, now: _due(s, now),
        act=lambda s, ctx, row, firing: _tick_act(s, ctx, row, firing))

    def rows(s, state=None):
        return [r for r in base.rows(s, state) if is_automation(r)]

    def pause(s, ctx, row, why):
        after, touched = base.pause(s, ctx, row, why)
        if after is not row:
            _version(s, ctx, after, 'paused', why)
        return after, touched
    return automation.register(replace(base, rows=rows, pause=pause))


KIND = _make_kind()


# ------------------------------------------------------------------ writing rules

def _via(ctx) -> str:
    return str(getattr(ctx.interface, 'value', ctx.interface))[:16]


def _version(s, ctx, row: dict, change: str, reason: str | None = None) -> None:
    s.company.conn.execute(c.bank_rule_versions.insert().values(
        id=new_id(), rule_id=row['id'], version=row['version'],
        definition=json.dumps({k: row.get(k) for k in _DEFINITION}, sort_keys=True, default=str), change=change,
        changed_by=s.actor.id, changed_via=_via(ctx), changed_at=batches.now(),
        reason=(reason or getattr(ctx, 'reason', None) or None) and (reason or ctx.reason)[:500]))


def _insert(s, ctx, row: dict, change: str) -> Touched:
    s.company.conn.execute(c.bank_rules.insert().values(**row))
    _version(s, ctx, row, change)
    return Touched('bank_rule', row['id'], 'create', None, 1, _public(row), db='company')


def _update(s, ctx, before: dict, after: dict, change: str, reason: str | None = None) -> Touched:
    at = batches.now()
    after = dict(after, version=before['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=_via(ctx))
    done = s.company.conn.execute(c.bank_rules.update().where(
        c.bank_rules.c.id == before['id'], c.bank_rules.c.version == before['version'])
        .values(**{k: v for k, v in after.items() if k != 'id'}))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': 'bank_rule', 'record_id': before['id']})
    _version(s, ctx, after, change, reason)
    return Touched('bank_rule', after['id'], 'update', after['version'] - 1, after['version'], _public(after),
                   _public(before), db='company')


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in ('name', 'status', 'state', 'action', 'auto_add', 'priority', 'established_by')}


def _new_row(s, ctx, **values) -> dict:
    at = batches.now()
    row = dict(id=new_id(), version=1, created_at=at, created_by=s.actor.id, created_via=_via(ctx), updated_at=at,
               updated_by=s.actor.id, updated_via=_via(ctx), state='draft', established_by=None, established_for=None,
               established_epoch=None, established_at=None, paused_reason=None, paused_at=None, draft_reason=None,
               fired_slot=None, failed_firings=0, learned_from_batch=None, learned_key=None, edited=0,
               payee=None, payee_type=None, payee_id=None, category_account_id=None, class_id=None, memo=None,
               transfer_account_id=None, ignore_reason=None, auto_add=0)
    row.update(values)
    row['name_key'] = norm(row['name'])
    return row


def _unique_name(s, name: str, besides: str | None = None) -> None:
    t = c.bank_rules
    q = sa.select(t.c.id).where(t.c.name_key == norm(name))
    if besides:
        q = q.where(t.c.id != besides)
    if s.company.conn.execute(q).first() is not None:
        raise BookflowError('E_NAME_TAKEN', message=f'A bank rule named {name!r} already exists.',
                            details={'field': 'name', 'fields': [{'field': 'name', 'problem': 'already used'}]})


def _free_name(s, base: str) -> str:
    name, n = base[:190], 1
    while s.company.conn.execute(sa.select(c.bank_rules.c.id).where(c.bank_rules.c.name_key == norm(name))).first():
        n += 1
        name = f'{base[:185]} ({n})'
    return name


def _feed_account(s, selector: str) -> dict:
    return bank_feeds._account(s, selector)


def _account_id(s, selector: str, field: str) -> str:
    from bookflow.company.accounts import resolve_account
    try:
        return resolve_account(s.company, selector)['id']
    except BookflowError as e:
        if e.code == 'E_RECORD_NOT_FOUND':
            e.details.setdefault('field', field)
        raise


def _places(s, account_id: str | None) -> int:
    if account_id:
        return bank_feeds._currency_places(s, account_id)
    return bank_feeds._places(s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar())


def _resolve(s, inp, row: dict, given: set[str]) -> dict:
    """The input's rule fields onto ``row``, each resolved and checked; returns the row."""
    from bookflow.company import bank_sort
    if 'account' in given:
        row['account_id'] = _feed_account(s, inp.account)['id'] if inp.account else None
    for f in ('direction', 'action', 'memo', 'ignore_reason', 'priority'):
        if f in given and getattr(inp, f) is not None or f in ('memo', 'ignore_reason') and f in given:
            row[f] = getattr(inp, f)
    if 'auto_add' in given and inp.auto_add is not None:
        row['auto_add'] = int(inp.auto_add)
    if 'conditions' in given and inp.conditions is not None:
        row['conditions'] = json.dumps([x.model_dump() for x in inp.conditions], sort_keys=True)
    if 'payee' in given or 'payee_type' in given:
        if inp.payee is None and 'payee' in given:
            row.update(payee=None, payee_id=None, payee_type=None)
        elif inp.payee is not None or row['payee_id']:
            kind = inp.payee_type or row['payee_type'] or 'vendor'
            found = bank_sort._payee(s, inp.payee if inp.payee is not None else row['payee_id'], kind)
            row.update(payee=found['name'][:1000], payee_id=found['id'], payee_type=kind)
    if 'category' in given:
        row['category_account_id'] = _account_id(s, inp.category, 'category') if inp.category else None
    if 'class_id' in given:
        from bookflow.company.budgets import resolve_class
        row['class_id'] = resolve_class(s, inp.class_id) if inp.class_id else None
    if 'transfer_to' in given:
        row['transfer_account_id'] = _account_id(s, inp.transfer_to, 'transfer_to') if inp.transfer_to else None
    # The action decides which fields it takes.
    if row['action'] == 'categorise':
        if row['transfer_account_id'] or row['ignore_reason']:
            raise _refuse('action', 'A categorise rule takes a payee, category, class and memo, not transfer_to or '
                                    'ignore_reason.')
    elif row['action'] == 'transfer':
        if not row['transfer_account_id']:
            raise _refuse('transfer_to', 'A transfer rule names the other account (transfer_to).')
        if row['transfer_account_id'] == row['account_id']:
            raise _refuse('transfer_to', 'A transfer goes to another account than the rule\'s own.')
        if row['payee_id'] or row['category_account_id'] or row['ignore_reason'] or row['auto_add']:
            raise _refuse('action', 'A transfer rule takes only transfer_to.')
    else:
        row['ignore_reason'] = row['ignore_reason'] or 'other'
        if row['payee_id'] or row['category_account_id'] or row['transfer_account_id'] or row['auto_add']:
            raise _refuse('action', 'An ignore rule takes only ignore_reason.')
    if row['auto_add'] and not (row['payee_id'] and row['category_account_id']):
        raise _refuse('auto_add', 'An auto-add rule names the payee and category it adds each line with: Bookflow '
                                  'never guesses who a line was for or what it was for.')
    places = _places(s, row['account_id'])
    for n, cond in enumerate(json.loads(row['conditions'])):
        if cond['field'] == 'amount':
            low = _amount_units(cond['value'], places, f'conditions[{n}].value')
            if cond['op'] == 'between' and _amount_units(cond['value_to'], places, f'conditions[{n}].value_to') < low:
                raise _refuse(f'conditions[{n}].value_to', 'value_to is at least value.')
        elif cond['field'] == 'payee_key' and not payee_key(cond['value']):
            raise _refuse(f'conditions[{n}].value', 'That payee reads as nothing once numbers and processor words '
                                                    'are dropped; test payee contains instead.')
    return row


def fingerprint(row: dict) -> str:
    """What the rule will do, exactly: its operative fields (and identity), hashed. The one check of its details."""
    body = {k: row.get(k) for k in ('id', *OPERATIVE)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def does(s, row: dict) -> str:
    where = bank_feeds._full_name(s, row['account_id']) if row['account_id'] else 'every feed account'
    money = {'in': 'money in', 'out': 'money out', 'any': 'money in or out'}[row['direction']]
    when = ' and '.join(_said(x) for x in json.loads(row['conditions']))
    lines = f"{where}'s lines ({money}) where {when}"
    if row['action'] == 'transfer':
        what = f"proposes a transfer to {bank_feeds._full_name(s, row['transfer_account_id'])}"
    elif row['action'] == 'ignore':
        what = (f"ignores the line ({row['ignore_reason']})" if acting(row)
                else f"proposes ignoring the line ({row['ignore_reason']})")
        if row['state'] != 'live':
            what += '; asks to ignore them unattended once live'
    else:
        parts = [row['payee'] or 'no payee',
                 bank_feeds._full_name(s, row['category_account_id']) if row['category_account_id'] else 'no account']
        if row['class_id']:
            name = s.company.conn.execute(sa.select(c.classes.c.name).where(c.classes.c.id == row['class_id'])).scalar()
            parts.append(f'class {name}')
        if row['memo']:
            parts.append(f"memo {row['memo']!r}")
        what = 'proposes ' + ', '.join(parts)
        if row['auto_add']:
            what += ('; adds the line in the books unattended at each import' if acting(row)
                     else '; asks to add the lines unattended once live')
    return f"For {lines}: {what}."


def _hits(s, rule_ids: list[str]) -> dict[str, int]:
    out = {}
    for chunk in bank_feeds._chunks(rule_ids):
        out.update(s.company.conn.execute(sa.select(c.bank_lines.c.rule_id, sa.func.count()).where(
            c.bank_lines.c.rule_id.in_(chunk), c.bank_lines.c.live == 1).group_by(c.bank_lines.c.rule_id)).all())
    return out


def out(s, row: dict, hits: int | None = None) -> m.RuleOut:
    def name(account_id):
        return bank_feeds._full_name(s, account_id) if account_id else None
    if hits is None:
        hits = _hits(s, [row['id']]).get(row['id'], 0)
    return m.RuleOut(
        id=row['id'], name=row['name'], account_id=row['account_id'], account=name(row['account_id']),
        direction=row['direction'], conditions=tuple(m.ConditionOut(**x) for x in json.loads(row['conditions'])),
        action=row['action'], payee=row['payee'], payee_type=row['payee_type'], payee_id=row['payee_id'],
        category_account_id=row['category_account_id'], category=name(row['category_account_id']),
        class_id=row['class_id'], memo=row['memo'], transfer_account_id=row['transfer_account_id'],
        ignore_reason=row['ignore_reason'], auto_add=bool(row['auto_add']), priority=row['priority'],
        source=row['source'], status=row['status'], state=row['state'], automation=is_automation(row),
        established_by=row['established_by'], established_at=row['established_at'],
        paused_reason=row['paused_reason'], draft_reason=row['draft_reason'],
        learned_from_batch=row['learned_from_batch'], learned_key=row['learned_key'], edited=bool(row['edited']),
        hits=hits, does=does(s, row), version=row['version'], created_at=row['created_at'],
        created_by=row['created_by'], updated_at=row['updated_at'])


def _waiting(s, account_id: str | None, limit: int) -> list[dict]:
    t = c.bank_lines
    q = sa.select(t).where(t.c.live == 1, t.c.state == 'waiting')
    if account_id:
        q = q.where(t.c.account_id == account_id)
    return [dict(r) for r in s.company.conn.execute(q.order_by(t.c.date, t.c.id).limit(limit)).mappings()]


def check(s, row: dict) -> m.Check:
    """The one check of a rule's exact details before it acts on its own: what it does, and the waiting lines it fits
    now (it acts only on lines of later imports)."""
    fitting = []
    places: dict[str, int] = {}
    for line in _waiting(s, row['account_id'], 20_000):
        p = places.setdefault(line['account_id'], bank_feeds._currency_places(s, line['account_id']))
        if fits(row, line, p) is not None:
            fitting.append((line, p))
    return m.Check(fingerprint=fingerprint(row), does=does(s, row), waiting=len(fitting), lines=tuple(
        m.CheckLine(line_id=v['id'], date=v['date'], amount=v['amount'], amount_decimal=bank_feeds.decimal(
            v['amount'], p), payee=v['payee']) for v, p in fitting[:CHECK_LINES]))


# ------------------------------------------------------------------ commands

def create(s, ctx, inp, *, dry_run: bool) -> tuple[dict, list[Touched]]:
    _unique_name(s, inp.name)
    row = _new_row(s, ctx, name=inp.name.strip(), account_id=None, direction=inp.direction or 'any',
                   conditions='[]', action=inp.action or 'categorise', priority=inp.priority or WRITTEN_PRIORITY,
                   source='written', status='active')
    row = _resolve(s, inp, row, set(inp.model_fields_set) | {'conditions'})
    notes = []
    if is_automation(row):
        notes.append(f'It is a draft: it proposes for lines but acts for nobody until a holder of {CAPABILITY} makes '
                     f"it live (`bank-rule update --rule {row['id']} --live`, with the check's fingerprint).")
    touched = [] if dry_run else [_insert(s, ctx, row, 'created')]
    return dict(row=row, notes=notes, summary=f"bank rule {row['name']} {'would be ' if dry_run else ''}created",
                next=f"`bank-rule apply --rule {row['id']} --dry-run` lists the waiting lines it would propose for"), \
        touched


def update(s, ctx, inp, *, dry_run: bool) -> tuple[dict, list[Touched]]:
    before = get(s, inp.rule)
    if inp.expected_version is not None and inp.expected_version != before['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': 'bank_rule', 'record_id': before['id'],
                                                           'expected_version': inp.expected_version,
                                                           'version': before['version']})
    if before['status'] in ('retired', 'suggested') and set(inp.model_fields_set) - {'rule', 'expected_version'}:
        if before['status'] == 'retired':
            raise _refuse('rule', f"Bank rule {before['name']} is retired; write a new one.")
    given = set(inp.model_fields_set) - {'rule', 'name', 'status', 'live', 'confirm', 'expected_version'}
    after = _resolve(s, inp, dict(before), given)
    if inp.name is not None and inp.name.strip() != before['name']:
        _unique_name(s, inp.name, before['id'])
        after.update(name=inp.name.strip(), name_key=norm(inp.name))
    if inp.status is not None:
        if before['status'] == 'suggested':
            raise _refuse('status', f"Bank rule {before['name']} is a suggestion: `bank-rule accept` or `bank-rule "
                                    'decline` it.')
        after['status'] = inp.status
    operative = any(before.get(k) != after.get(k) for k in OPERATIVE)
    if any(before.get(k) != after.get(k) for k in OPERATIVE if k != 'status'):
        after['edited'] = 1  # what it does changed (a status change alone is not an edit, here or in deactivate)
    notes: list[str] = []
    holder_unconfirmed = False
    if inp.live is True:
        if not is_automation(after):
            raise _refuse('live', f"Bank rule {after['name']} only proposes: only an auto-add or ignore rule acts on "
                                  'its own.')
        if after['status'] != 'active':
            raise _refuse('live', f"Bank rule {after['name']} is {after['status']}: "
                                  + ('accept it first.' if after['status'] == 'suggested' else 'make it active first.'))
        automation.require_establish(s, CAPABILITY, 'bank-rule update')
        after.update(automation.establisher(s))
        change = 'live'
    elif inp.live is False:
        if operative and is_automation(before):
            # A non-holder's change goes to draft as any edit does; pausing is open to every editor (L4).
            # A holder pausing needs no check of what it would do: it acts for nobody until made live again.
            after, notes = automation.on_edit(s, CAPABILITY, before, after, operative=True, confirmed=True)
        if after['state'] == 'live':
            at = clock.now_iso()
            after.update(state='paused', paused_reason=f'paused by {automation.name_of(s, s.actor.id)}'[:500],
                         paused_at=at)
        change = 'paused'
    else:
        if is_automation(before) or is_automation(after):
            confirmed = inp.confirm == fingerprint(after)
            holder_unconfirmed = (operative and before['state'] == 'live' and not confirmed
                                  and automation.holds_now(s, CAPABILITY))
            after, notes = automation.on_edit(s, CAPABILITY, before, after, operative=operative,
                                              confirmed=confirmed)
            if not is_automation(after) and after['state'] != 'draft':
                after.update(state='draft', draft_reason='no longer an auto-add or ignore rule')
        change = 'deactivated' if inp.status == 'disabled' else 'updated'
    the_check = check(s, after) if is_automation(after) else None
    if inp.live is True and not dry_run and inp.confirm != the_check.fingerprint:
        raise BookflowError('E_VALIDATION', message=(
            f"Making bank rule {after['name']} live is a lasting change: run the same call with --dry-run, check what "
            "it will do, then pass the check's fingerprint as confirm."),
            details={'field': 'confirm', 'fields': [{'field': 'confirm', 'problem': 'the dry run\'s check fingerprint'}],
                     'reason': 'confirm_required'})
    if holder_unconfirmed and the_check is not None:
        notes = [f"Passing the check's fingerprint as confirm ({the_check.fingerprint}) keeps it live under you; "
                 'without it this change returns it to draft.']
    touched = [] if dry_run or after == before else [_update(s, ctx, before, after, change)]
    word = {'live': 'made live', 'paused': 'paused', 'deactivated': 'deactivated'}.get(change, 'updated')
    return dict(row=after if dry_run else get(s, before['id']), check=the_check, notes=notes,
                summary=f"bank rule {after['name']} {'would be ' if dry_run else ''}{word}",
                next=(f"the same call with confirm {the_check.fingerprint}"
                      if dry_run and the_check and (inp.live or holder_unconfirmed) else None)), touched


def set_status(s, ctx, inp, to: str, *, dry_run: bool) -> tuple[dict, list[Touched]]:
    """accept (suggested -> active), decline (suggested -> retired, its key remembered) or deactivate."""
    before = get(s, inp.rule)
    after = dict(before)
    if to == 'accept':
        if before['status'] != 'suggested':
            raise _refuse('rule', f"Bank rule {before['name']} is {before['status']}, not a suggestion.")
        after['status'] = 'active'
    elif to == 'decline':
        if before['status'] != 'suggested':
            raise _refuse('rule', f"Bank rule {before['name']} is {before['status']}, not a suggestion: "
                                  '`bank-rule deactivate` stops it.')
        after['status'] = 'retired'
    else:
        if before['status'] in ('disabled', 'retired'):
            raise _refuse('rule', f"Bank rule {before['name']} is already {before['status']}.")
        after['status'] = 'disabled'
    touched = []
    if not dry_run:
        touched.append(_update(s, ctx, before, after, {'accept': 'accepted', 'decline': 'declined'}.get(
            to, 'deactivated')))
        if to == 'decline' and before['learned_key'] and before['account_id']:
            exists = s.company.conn.execute(sa.select(c.bank_rule_declines.c.id).where(
                c.bank_rule_declines.c.account_id == before['account_id'],
                c.bank_rule_declines.c.payee_key == before['learned_key'])).first()
            if exists is None:
                decline_id = new_id()
                s.company.conn.execute(c.bank_rule_declines.insert().values(
                    id=decline_id, account_id=before['account_id'], payee_key=before['learned_key'],
                    rule_id=before['id'], declined_by=s.actor.id, declined_at=batches.now()))
                touched.append(Touched('bank_rule_decline', decline_id, 'create', None, 1,
                                       {'payee_key': before['learned_key']}, db='company'))
    word = {'accept': 'accepted', 'decline': 'declined (never suggested again)'}.get(to, 'deactivated')
    return dict(row=after if dry_run else get(s, before['id']), notes=[],
                summary=f"bank rule {before['name']} {'would be ' if dry_run else ''}{word}",
                next=(f"`bank-rule apply --rule {before['id']} --dry-run` lists the waiting lines it fits"
                      if to == 'accept' else None)), touched


def query(s, inp) -> dict:
    t = c.bank_rules
    where = []
    if inp.account is not None:
        account_id = _feed_account(s, inp.account)['id']
        where.append(sa.or_(t.c.account_id == account_id, t.c.account_id.is_(None)))
    if inp.status:
        where.append(t.c.status == inp.status)
    if inp.source:
        where.append(t.c.source == inp.source)
    rows = sorted(_rows(s, where), key=order_key)
    start = bank_feeds._offset(inp.cursor)
    page = rows[start:start + inp.limit]
    hits = _hits(s, [r['id'] for r in page])
    return dict(rules=tuple(out(s, r, hits.get(r['id'], 0)) for r in page),
                next_cursor=str(start + inp.limit) if len(rows) > start + inp.limit else None)


def apply(s, ctx, inp, *, dry_run: bool) -> tuple[dict, list[Touched]]:
    """Read the rules over waiting lines again: each line's rule proposal set to the first rule that fits now (a rule
    never touches a decided line). With ``rule``, only the lines whose proposal it gains or loses."""
    only = get(s, inp.rule) if inp.rule else None
    account_id = _feed_account(s, inp.account)['id'] if inp.account else None
    lines = _waiting(s, account_id, inp.limit)
    by_account: dict[str, list[dict]] = {}
    for line in lines:
        by_account.setdefault(line['account_id'], []).append(line)
    changes = []
    for acct, rows in by_account.items():
        rules, places = active_rules(s, acct), bank_feeds._currency_places(s, acct)
        if only is not None and only['status'] == 'active' and only['id'] not in {r['id'] for r in rules}:
            rules = []
        history: dict[str, list[dict]] = {}
        for line in rows:
            fit = first_fit(rules, line, places)
            value = json.loads(line['proposal']) if line['proposal'] else {}
            value.pop('rule', None)
            value.pop('history', None)
            if fit is not None:
                value['rule'] = proposal_rule(*fit)
            else:
                key = line['payee_key']
                if key and key not in history:
                    history[key] = answers_for(s, acct, key)
                found = from_history(history.get(key, [])) if key else None
                if found is not None:
                    value['history'] = dict(found[0], reason=found[1])
            rule_id = fit[0]['id'] if fit else None
            if rule_id == line['rule_id']:
                continue
            if only is not None and only['id'] not in (rule_id, line['rule_id']):
                continue
            changes.append((line, rule_id, fit, json.dumps(value, sort_keys=True) if value else None, places))
    touched = []
    if not dry_run:
        for line, rule_id, _, proposal, _ in changes:
            done = s.company.conn.execute(c.bank_lines.update().where(
                c.bank_lines.c.id == line['id'], c.bank_lines.c.version == line['version'],
                c.bank_lines.c.state == 'waiting', c.bank_lines.c.live == 1).values(
                rule_id=rule_id, proposal=proposal, version=line['version'] + 1))
            if done.rowcount != 1:
                raise BookflowError('E_VERSION_CONFLICT', message=f"Line {line['id']} changed meanwhile; apply again.",
                                    details={'line': line['id']})
            touched.append(Touched('bank_line', line['id'], 'update', line['version'], line['version'] + 1,
                                   {'rule_id': rule_id}, {'rule_id': line['rule_id']}, db='company'))
    shown = tuple(m.Applied(line_id=line['id'], date=line['date'], amount_decimal=bank_feeds.decimal(line['amount'], p),
                            payee=line['payee'], rule_id=rule_id, rule=fit[0]['name'] if fit else None,
                            reason=fit[1] if fit else None, was_rule_id=line['rule_id'])
                  for line, rule_id, fit, _, p in changes[:1000])
    n = len(changes)
    return dict(lines=shown, read=len(lines), summary=(
        f"{n} waiting line(s) {'would ' if dry_run else ''}change their rule proposal"
        + ('. Nothing was changed.' if dry_run else '.')),
        next=('the same call without --dry-run' if dry_run and n else
              ('`bank-line add --lines ...` enters lines with their proposals' if n else None))), touched


# ------------------------------------------------------------------ learning (inside bank-line add's own write)

def learning(s) -> str:
    return s.company.conn.execute(sa.select(c.company_info.c.bank_rule_learning)).scalar() or 'ask'


def answer_of(plan_input: dict, payee_id: str, payee_type: str, payee: str, account: str | None,
              class_id: str | None) -> dict | None:
    if not account:
        return None
    return dict(payee_id=payee_id, payee_type=payee_type, payee=payee, account_id=account, class_id=class_id,
                at=batches.now())


def learn(s, ctx, line: dict, answer: dict | None, import_batch: str, *, always: bool = False,
          sort_batch: str | None = None) -> str | None:
    """After a person or agent adds ``line`` with ``answer``: a rule learned when its party key now has LEARN_AFTER
    adds in a row answered the same (or at once with ``always``), recorded as a ``rule_learned`` effect of the line's
    import batch (and of its --lines batch), so reversing either takes it back unless it was edited since. Returns
    what to tell the caller; ``always`` that cannot apply says why."""
    from bookflow.core.audit import write_event_to
    if answer is None:
        return 'Always do this did not apply: a rule is learned from one account, not splits.' if always else None
    key = line.get('payee_key') or party_key(line['payee'])
    if not key:
        return (f"Always do this did not apply: {line['payee']!r} names no party (a check number, a deposit, a "
                'transfer or a processor alone), so no rule is learned for it.') if always else None
    pref = learning(s)
    if pref == 'never':
        return ('Always do this did not apply: this company learns no bank rules (bank_rule_learning never).'
                if always else None)
    account_id = line['account_id']
    t = c.bank_rules
    same = _rows(s, (t.c.account_id == account_id, t.c.learned_key == key, t.c.status != 'retired'))
    if same:
        return (f"Always do this did not apply: bank rule {same[0]['name']} already exists for {key} "
                f"({same[0]['status']}); " + (f"`bank-rule accept {same[0]['id']}` makes it a rule."
                                             if same[0]['status'] == 'suggested' else
                                             f"`bank-rule update {same[0]['id']}` changes it.")) if always else None
    places = bank_feeds._currency_places(s, account_id)
    fit = first_fit(active_rules(s, account_id), line, places)
    if fit is not None:
        return (f"Always do this did not apply: bank rule {fit[0]['name']} already fits this payee's lines "
                f"(`bank-rule update {fit[0]['id']}` changes it).") if always else None
    if not always:
        if s.company.conn.execute(sa.select(c.bank_rule_declines.c.id).where(
                c.bank_rule_declines.c.account_id == account_id, c.bank_rule_declines.c.payee_key == key)).first():
            return None
        run = 0
        for a in reversed(answers_for(s, account_id, key)):
            if not _same(a, answer):
                break
            run += 1
        if run < LEARN_AFTER:
            return None
    status = 'active' if always or pref == 'always' else 'suggested'
    row = _new_row(s, ctx, name=_free_name(s, f'{key.title()} (learned)'), account_id=account_id,
                   direction='in' if line['amount'] > 0 else 'out',
                   conditions=json.dumps([{'field': 'payee_key', 'op': 'equals', 'value': key, 'value_to': None}]),
                   action='categorise', priority=LEARNED_PRIORITY, source='learned', status=status,
                   payee=answer['payee'], payee_type=answer['payee_type'], payee_id=answer['payee_id'],
                   category_account_id=answer['account_id'], class_id=answer['class_id'], learned_key=key,
                   learned_from_batch=import_batch)
    touched = _insert(s, ctx, row, 'created')
    write_event_to(s.company, ctx, 'bank-line add', f"learned bank rule {row['name']} ({status})", [touched],
                   actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    event = batches.last_event_id(s)
    for batch_id in (import_batch, sort_batch):
        if batch_id:
            batches.record_effect(s, batch_id, record_type='bank_rule', record_id=row['id'], action='rule_learned',
                                  version_after=1, event_id=event)
    if status == 'suggested':
        return (f"Always do this for {key}? ({LEARN_AFTER} times so far) `bank-rule accept {row['id']}` makes it a "
                f"rule; `bank-rule decline {row['id']}` stops the suggestion.")
    return f"Bank rule {row['name']} now proposes this for {key} (`bank-rule show {row['id']}`)."


def retire(s, effect: dict, batch: dict, reversal_id: str) -> str:
    """Reversing the batch that taught a rule (its import, a move, or the undo of its --lines batch) retires it,
    unless it was edited since (or written by hand)."""
    row = s.company.conn.execute(sa.select(c.bank_rules).where(c.bank_rules.c.id == effect['record_id'])) \
        .mappings().first()
    if row is None or row['source'] != 'learned' or row['edited'] or row['status'] == 'retired':
        return 'kept'
    row = dict(row)
    after = dict(row, status='retired', state='draft' if row['state'] != 'draft' else row['state'])
    from bookflow.company.scheduler import context
    ctx = context().model_copy(update={'reason': f'the batch that taught it was reversed ({reversal_id})'})
    _update(s, ctx, row, after, 'retired', f"batch {batch['id']} that taught it was reversed ({reversal_id})")
    return 'retired'


# ------------------------------------------------------------------ live rules at an import
#
# Who an act runs as (review M2, the confused deputy). A live rule's lines are entered under its establisher's
# authority, but on a file import the file is the importer's: a clerk without authority to post could otherwise
# import a crafted statement and have the owner's rule post whatever it fits. So on a file import a rule acts only
# when the importer also holds every authority the act needs (bank-line add or ignore, and the posting command);
# otherwise its lines wait with their proposals and the import says which rules were held back and why. The
# establisher's authority alone suffices only for lines Bookflow fetched itself (``fetched``: the live feed of
# increment 8), where nobody chose the lines.

SLICE = 25  # acts per writer job, as the scheduler's ENTRIES_PER_TICK; the rest continue on the host's next tick


def _on_writer() -> bool:
    from threading import current_thread
    from bookflow.core.identity_admin_binding import SCHEDULER_THREADS
    return current_thread() in SCHEDULER_THREADS


def _as(s, user_id, principal):
    from bookflow.company import scheduler
    saved = (s.actor, s.memberships, s.credential, s.directive_code, getattr(s, '_resource_checks', None))
    s._resource_checks = None  # nothing the previous actor was found to hold carries over
    scheduler._act_as(s, user_id, principal, automation.epoch_of(s, user_id, principal))
    return saved


def _restore(s, saved) -> None:
    s.actor, s.memberships, s.credential, s.directive_code, s._resource_checks = saved


def _system_id(s) -> str | None:
    from bookflow.hub import schema as h
    return s.hub.conn.execute(sa.select(h.users.c.id).where(h.users.c.kind == 'system', h.users.c.active.is_(True))
                              .order_by(h.users.c.created_at)).scalars().first()


def pause_as_system(s, row: dict, why: str) -> None:
    """Pause a live rule with its one to-do, written as the system on the rule's behalf (review L5), never as the act
    of whoever happened to import."""
    from bookflow.company import scheduler
    from bookflow.core.dispatch import _load_actor_by_id
    system = _system_id(s)
    saved = (s.actor, s.memberships, s.credential, s.directive_code, getattr(s, '_resource_checks', None))
    try:
        if system is not None:
            _load_actor_by_id(s, system)
        ctx = scheduler.context().model_copy(update={'actor_id': s.actor.id, 'reason': f"On rule: {row['name']}"[:140]})

        def write():
            _, touched = KIND.pause(s, ctx, get(s, row['id']), why)
            automation._audit(s, ctx, 'automation pause', f"automation {row['name']} paused: {why}"[:512], touched)
        automation._in_tx(s, 'automation.pause', write)
    finally:
        _restore(s, saved)


def _needs(row: dict, plans: dict[str, dict]) -> list[str]:
    return ['bank-line ignore'] if row['action'] == 'ignore' else \
        ['bank-line add', *sorted({p['command'] for p in plans.values()})]


def _missing(s, needs: list[str]) -> list[str]:
    """The commands of an act the session's actor may not run (each command's own capability and resources)."""
    return [name for name in needs if not automation.may(s, name)]


def _mark(s, line_ids, pending: dict | None) -> None:
    """Note on each waiting line whether its live rule is still to act on it (``pending``: why, and the importer whose
    authority it needs), or not any longer. Bookkeeping on the proposal: the line's version is untouched."""
    if line_ids:
        _txn(s, lambda: _marked(s, line_ids, pending))


def _marked(s, line_ids, pending) -> None:
    for line_id in line_ids:
        current = s.company.conn.execute(sa.select(c.bank_lines.c.proposal).where(
            c.bank_lines.c.id == line_id, c.bank_lines.c.state == 'waiting', c.bank_lines.c.live == 1)).scalar()
        if not current:
            continue
        value = json.loads(current)
        if not value.get('rule'):
            continue
        if pending is None:
            value['rule'].pop('pending', None)
        else:
            value['rule']['pending'] = pending
        s.company.conn.execute(c.bank_lines.update().where(c.bank_lines.c.id == line_id).values(
            proposal=json.dumps(value, sort_keys=True)))


def _pending_lines(s, account_id: str | None = None, rule_id: str | None = None) -> list[dict]:
    t = c.bank_lines
    q = sa.select(t).where(t.c.live == 1, t.c.state == 'waiting',
                           sa.func.json_extract(t.c.proposal, '$.rule.pending').is_not(None))
    if account_id:
        q = q.where(t.c.account_id == account_id)
    if rule_id:
        q = q.where(sa.func.json_extract(t.c.proposal, '$.rule.id') == rule_id)
    return [dict(r) for r in s.company.conn.execute(q.order_by(t.c.date, t.c.id)).mappings()]


def _result(row: dict) -> dict:
    return dict(rule_id=row['id'], name=row['name'], outcome='skipped', added=0, ignored=0, refused=[], held=[],
                later=0, reason=None)


def _plans(s, row: dict, lines: list[dict]) -> dict[str, dict]:
    from bookflow.company import bank_sort
    from bookflow.company import bank_feed_models as bm
    if row['action'] == 'ignore':
        return {}
    out = {}
    for line in lines:
        item = bm.AddItem(line=line['id'], payee=row['payee_id'], payee_type=row['payee_type'],
                          account=row['category_account_id'], class_id=row['class_id'], memo=row['memo'])
        try:
            out[line['id']] = dict(bank_sort._add_plan(s, line, item), item=item)
        except BookflowError as error:
            out[line['id']] = dict(error=error)
    return out


def _txn(s, fn):
    from bookflow.company import bank_sort
    bank_sort._in_tx(s, fn)


def _act(s, row: dict, lines: list[dict], *, importer: tuple[str, str | None] | None, fetched: bool, budget: int,
         result: dict) -> int:
    """One live rule over some of its waiting lines, as far as ``budget`` acts: every check first, then each line,
    re-checked per act. Returns the budget left."""
    from bookflow.company import bank_sort, scheduler
    from bookflow.core.context import ActorKind
    ids = [v['id'] for v in lines]
    if not acting(row):
        result['reason'] = f"it is {row['status']}, {row['state']}"
        _mark(s, ids, None)
        return budget
    if not automation_on(s):
        result.update(outcome='held', reason='the company holds automated bank rules back '
                                             '(company update --bank-rule-automation)')
        _mark(s, ids, None)
        return budget
    try:
        why = automation.firing_check(s, KIND, row)
    except automation.Unsettled as e:
        result['reason'] = e.reason
        return budget
    if why is not None:
        pause_as_system(s, row, why)
        _mark(s, ids, None)
        result.update(outcome='paused', reason=why)
        return budget
    v = KIND.view(s, row)
    # H1: never unattended on a line that may already be in the books (a transfer's other end, among them).
    todo = []
    for line in lines:
        fits_, _ = bank_sort._pairing(s, line)
        if line['possible_duplicate_of']:
            result['held'].append(dict(line_id=line['id'], reason=f"it may repeat line {line['possible_duplicate_of']}"))
        elif fits_:
            result['held'].append(dict(line_id=line['id'], reason='it may be one end of a transfer'))
        else:
            todo.append(line)
    _mark(s, [h['line_id'] for h in result['held']], None)
    if not todo:
        result['outcome'] = 'held'
        return budget
    on_writer = _on_writer()
    me = s.actor.id
    if not on_writer and v['established_by'] != me:
        # The establisher's authority is lent only on the host's writer (as R111 and R112 lend it).
        result.update(outcome='skipped', reason=(
            f"{automation.name_of(s, v['established_by'])}'s authority is lent only through the host (bookflow "
            'serve), or when they import: the lines wait, marked for the host to act on'))
        _mark(s, [x['id'] for x in todo], dict(why='local', importer=importer[0] if importer else me,
                                               importer_for=importer[1] if importer else None))
        return budget
    plans = _plans(s, row, todo)
    needs = _needs(row, {k: p for k, p in plans.items() if 'command' in p})
    if not fetched and importer is not None:
        if importer[0] == me:
            missing = _missing(s, needs)
        elif on_writer:
            saved = _as(s, importer[0], importer[1])
            try:
                missing = _missing(s, needs)
            finally:
                _restore(s, saved)
        else:
            # Another person's file, read off the host: their authority cannot be checked here; it waits, marked.
            result.update(outcome='skipped', reason=(
                f"the lines {automation.name_of(s, importer[0])} imported wait for the host (bookflow serve), where "
                'their authority can be checked'))
            return budget
        if missing:
            result.update(outcome='held', reason=(
                f"{automation.name_of(s, importer[0])} imported the file and may not run {', '.join(missing)}: on a "
                "file import a live rule acts only with the importer's authority too; the lines wait"))
            _mark(s, ids, None)
            return budget
    saved = _as(s, v['established_by'], v['established_for']) if on_writer else None
    pause_why = None
    try:
        base = scheduler.context().model_copy(update={
            'actor_id': v['established_by'], 'actor_kind': ActorKind(s.actor.kind),
            'on_behalf_of': v['established_for'], 'reason': f"On rule: {row['name']}"[:140]})
        for n, line in enumerate(todo):
            if budget <= 0:
                rest = [x['id'] for x in todo[n:]]
                result['later'] = len(rest)
                _mark(s, rest, dict(why='later', importer=importer[0] if importer else None,
                                    importer_for=importer[1] if importer else None))
                break
            # L6: the rule and its establisher are read again before every act.
            now = get(s, row['id'])
            if now['version'] != row['version'] or not acting(now):
                result['reason'] = 'the rule changed meanwhile; its other lines wait'
                _mark(s, [x['id'] for x in todo[n:]], None)
                break
            try:
                why = automation.firing_check(s, KIND, now)
            except automation.Unsettled as e:
                result['reason'] = e.reason
                break
            if why is not None:
                pause_why = why
                break
            plan = plans.get(line['id'], {})
            try:
                if 'error' in plan:
                    raise plan['error']
                # M1: every requirement of the act, as the establisher: bank-line add or ignore, and the posting
                # command's own capability.
                for name in (['bank-line ignore'] if row['action'] == 'ignore' else ['bank-line add', plan['command']]):
                    automation.require_command(s, name)
                current = bank_sort.lines(s, [line['id']])[0]
                ref = automation.source_ref(KIND.kind, row['id'], v['established_by'], row['name'],
                                            inner=f"bank-line:{line['id']}")
                rctx = base.model_copy(update={'source_ref': ref})
                if row['action'] == 'ignore':
                    _ignore(s, rctx, current, row)
                    result['ignored'] += 1
                else:
                    bank_sort.add_one(s, rctx, current, plan['item'], how='rule', rule_id=row['id'], source_ref=ref)
                    result['added'] += 1
                budget -= 1
            except BookflowError as error:
                result['refused'].append(dict(line_id=line['id'], code=error.code, message=error.message))
                _mark(s, [line['id']], None)
                if error.code in ('E_PERMISSION', 'E_UNAUTHENTICATED'):
                    pause_why = (f"{automation.name_of(s, v['established_by'])} was refused ({error.code}): "
                                 f"{error.message}")
                    break
    finally:
        if saved is not None:
            _restore(s, saved)
    result['outcome'] = 'acted' if result['added'] or result['ignored'] else result['outcome']
    if pause_why:
        pause_as_system(s, row, pause_why)
        _mark(s, [x['id'] for x in todo], None)
        result.update(outcome='paused', reason=pause_why)
    return budget


def run_live(s, ctx, prepared: dict, *, fetched: bool = False) -> list[dict]:
    """After an import committed: each live rule that fit its new lines acts on them (and on lines of the account an
    earlier import left for it), checked as `_act` says, at most SLICE acts in this job; the rest are marked and
    continue on the host's next tick. ``fetched``: lines Bookflow fetched itself (increment 8's live feed), where the
    establisher's authority alone suffices."""
    from bookflow.company import bank_sort
    from bookflow.hub.capabilities import principal_of
    importer = None if fetched else (s.actor.id, principal_of(s) if s.actor.kind == 'agent' else None)
    # Older lines an earlier import left for a rule go first, each with the importer whose file brought it in.
    groups: dict[tuple, list[str]] = {}
    for line in _pending_lines(s, prepared['account_id']):
        rule = json.loads(line['proposal'])['rule']
        pending = rule['pending']
        who = (pending['importer'], pending.get('importer_for')) if pending.get('importer') else None
        groups.setdefault((rule['id'], who), []).append(line['id'])
    taken = {i for ids in groups.values() for i in ids}
    for r in prepared['new']:
        rule = r.get('rule')
        if rule and rule.get('acts') and r.get('line_id') and r['line_id'] not in taken:
            groups.setdefault((rule['id'], importer), []).append(r['line_id'])
    results: dict[str, dict] = {}
    budget = SLICE
    for (rule_id, who), ids in groups.items():
        row = get(s, rule_id)
        result = results.setdefault(rule_id, _result(row))
        lines = [v for v in bank_sort.lines(s, ids) if v['live'] and v['state'] == 'waiting']
        budget = _act(s, row, lines, importer=who, fetched=who is None, budget=budget, result=result)
    return list(results.values())


def _due(s, now) -> list:
    """The host's tick: each live rule with lines an import left for it (local, or past an import's slice)."""
    t = c.bank_rules
    live = _rows(s, (t.c.state == 'live', t.c.status == 'active', sa.or_(t.c.auto_add == 1, t.c.action == 'ignore')))
    if not live:
        return []
    slot = now.strftime('%Y-%m-%dT%H:%M:%S')
    return [(row, slot) for row in live if _pending_lines(s, rule_id=row['id'])]


def _tick_act(s, ctx, row: dict, firing) -> None:
    """Up to SLICE marked lines of this rule, each with the authority of the importer who brought it in (checked
    again now, M2) and its establisher (fire has already loaded them)."""
    budget = SLICE
    lines = _pending_lines(s, rule_id=row['id'])
    groups: dict[tuple, list[dict]] = {}
    for line in lines:
        pending = json.loads(line['proposal'])['rule']['pending']
        groups.setdefault((pending.get('importer'), pending.get('importer_for')), []).append(line)
    saved = (s.actor, s.memberships, s.credential, s.directive_code, getattr(s, '_resource_checks', None))
    try:
        for (importer, importer_for), group in groups.items():
            result = _result(row)
            budget = _act(s, get(s, row['id']), group, importer=(importer, importer_for) if importer else None,
                          fetched=importer is None, budget=budget, result=result)
            if budget <= 0 or result['outcome'] == 'paused':
                break
    finally:
        _restore(s, saved)


def _ignore(s, ctx, line: dict, row: dict) -> None:
    from bookflow.company import bank_sort

    def work():
        current = bank_sort.lines(s, [line['id']])[0]
        batch_id = bank_sort._sortable(s, current)
        after = dict(bank_sort._CLEAR, state='ignored', ignore_reason=row['ignore_reason'] or 'other', link_how='rule')
        event = bank_sort._audit(s, ctx, 'bank-line ignore', f"line ignored on rule {row['name']}", [(current, after)])
        bank_sort._write_line(s, ctx, current, after, kind='ignored', details={
            'how': 'rule', 'rule': row['id'], 'established_by': row['established_by'],
            'ignore_reason': after['ignore_reason']}, event_id=event, batch_ids=[batch_id])
    bank_sort._in_tx(s, work)


def _register_retire():
    from bookflow.company import bank_reversal
    bank_reversal.RETIRE[('bank_rule', 'rule_learned')] = retire


_register_retire()
