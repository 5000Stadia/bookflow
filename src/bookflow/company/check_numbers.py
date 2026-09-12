"""The number written on a cheque: where it comes from, what it collides with, what it leaves.

A check number is not a document number. ``transactions.number`` says which document this is
inside Bookflow and comes from the shared journal series; a check number says which piece of
paper came out of one bank account's chequebook. Until this module existed a check had only
the first of those, which is why ``report missing-checks`` called every number the journal
series handed a journal entry, a transfer or a card charge a missing cheque. Card charges and
transfers still keep their document reference and still consume no cheque number.

**Everything that prints a cheque comes here, and there is nowhere else to go.** A cheque
written from Pay Bills is the same piece of paper as one written from Write Checks, so
``bill pay`` allocates through this module exactly as ``check post`` does whenever the money
leaves a bank account and the method is a cheque -- it does not have an allocator of its own.
``ap_payment_profiles.check_number`` used to hold a number a person typed and nothing could
place, which was the last thing making ``report missing-checks`` name holes that were not
holes; it now records what this module handed out. The documents allowed to carry a number
are ``money_out_schema.CHEQUE_DOCUMENT_TYPES``, and a caller writing several cheques in one
command passes the keys it has already claimed as ``taken``, because the database it reads
cannot yet see them.

**Where a number comes from.** ``accounts.next_check_number`` is the pointer a person sets on
the bank account, and it is what an unnumbered check draws from. Allocation happens at
successful posting inside the writer's own transaction -- never at preview, where it would be
a promise nothing keeps -- skips every number that account has already issued, and advances
the pointer past what it handed out. An idempotent retry never reaches allocation at all: the
first attempt stored its output and dispatch replays it.

**What the pointer is and is not.** It is a hint about where to start looking, not the record
of what was issued; the audited record of that is one immutable
``check_instrument_revisions`` row per revision. So the pointer moves with a plain write and
does not bump the account's version -- the same treatment ``sequences.next_number`` already
gets -- and if something writes a stale pointer back, the next allocation simply walks past
the numbers that are occupied and arrives at the same place.

**An explicit number below the pointer is valid.** It is a cheque out of an older book, or
one written by hand before the rest; accepting it is right and moving the pointer backwards
would hand the next few numbers out twice. Below the pointer is not the same thing as
duplicate. An explicit number at or above the pointer moves the pointer to one past it. A
number carried forward by a correction that did not name one moves no pointer at all, because
nobody issued or typed it on that call.

**Duplicates are refused, not warned about (parity difference).** This module refuses a
same-account collision by name -- ``E_DUPLICATE_NUMBER``, saying which cheque already holds
the number -- and ``uq_check_instrument_number``, a partial unique index over
``(account_id, check_number_key)`` where ``origin = 'issued'``, is the structural backstop
behind that refusal. The anchor product warns and then lets a duplicate through; Bookflow does
not, because a hard constraint and a warning-and-accept policy cannot both be true, and
because every finding ``report missing-checks`` makes is ambiguous the moment one number can
mean two cheques. A person who really did write two cheques with one number corrects one of
them. The index is partial because a company file co0040 upgraded can already hold ``1001``
and ``01001`` on one account: that file has to open so the report can say so, and the
refusal above covers both origins anyway, since occupancy is checked before the write.

**A voided check keeps its number.** Voiding writes no new revision, so the instrument is
untouched: the number stays occupied, is not a gap, and is not handed out again.

**A correction keeps the number it replaces out of the allocator.** Changing a cheque's
account or number writes a new revision row and replaces the projection; the previous account
and number stay on the superseded revision for ever, so print and history still read what was
issued. That number is then *retired*: nothing holds it, automatic allocation still refuses to
hand it out, and ``report missing-checks`` names it as retired rather than as a hole in the
chequebook. Typing it again explicitly is allowed, because that is a deliberate act by a
person looking at the paper.

**Leading zeros and non-numeric numbers, decided rather than coerced.** Nothing here goes
through a float. ``check_number`` is stored exactly as typed. ``check_number_key`` is what
uniqueness compares: a run of one to eighteen ASCII digits with its leading zeros removed --
so ``01001`` and ``1001`` are one cheque and not two -- and anything else case-folded and
compared whole. ``check_sequence`` is the integer value when there is one, and null otherwise:
``EFT`` and ``1001-A`` are real cheque numbers that sit between no two others, so they occupy
no place in the run and take no part in the arithmetic. A pointer written with leading zeros
keeps its width, so an account whose next number is ``000105`` issues ``000105`` and then
``000106``.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.money_out_schema import CHEQUE_DOCUMENT_TYPES
from bookflow.core.errors import BookflowError

# How wide a run of digits still reads back from SQLite's CAST as the integer it spells. A
# longer number is a real cheque number with no place in a sequence. Shared with
# ``company/check_reports.py`` so the two cannot disagree about what counts as a position.
SEQUENCE_DIGITS = 18
SEQUENCE_LIMIT = 10 ** SEQUENCE_DIGITS

# The document whose numbers these are. Read from the money-out marker map rather than
# retyped, so a new money-out document cannot silently start consuming cheque numbers.
KIND = 'check'

ORIGINS = ('issued', 'migrated')


def is_sequence(number: str) -> bool:
    """Whether this number takes a place in a run of numbers rather than merely naming one."""
    return (number.isascii() and number.isdigit()
            and 1 <= len(number) <= SEQUENCE_DIGITS)


def canonical(number: str) -> tuple[str, str, int | None]:
    """The literal as typed, the key uniqueness compares, and the place it takes in a run."""
    literal = number.strip()
    if not literal:
        raise _invalid('number', 'a check number cannot be blank')
    if len(literal) > 64:
        raise _invalid('number', 'a check number is at most 64 characters')
    if is_sequence(literal):
        value = int(literal)
        if value <= 0:
            raise _invalid('number', 'a numbered cheque starts at 1')
        return literal, str(value), value
    return literal, literal.casefold(), None


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


# ---------------------------------------------------------------- occupancy


def _held(s, account_id, key, *, excluding=None):
    """The check currently carrying this number on this account, if any."""
    t = c.check_instruments
    query = sa.select(t).where(t.c.account_id == account_id, t.c.check_number_key == key)
    if excluding is not None:
        query = query.where(t.c.transaction_id != excluding)
    found = s.company.conn.execute(query).mappings().first()
    return dict(found) if found else None


def _ever_issued(s, account_id, keys, *, excluding=None):
    """Which of these numbers this account has ever issued, held now or retired by a correction.

    Retired numbers are in here deliberately: a number that was once on a cheque is not free
    for the allocator to hand out again, even though nothing holds it any more.
    """
    if not keys:
        return set()
    revisions, current = c.check_instrument_revisions, c.check_instruments
    found = set()
    for table in (revisions, current):
        query = sa.select(table.c.check_number_key).where(
            table.c.account_id == account_id, table.c.check_number_key.in_(sorted(keys)))
        if excluding is not None:
            query = query.where(table.c.transaction_id != excluding)
        found.update(row[0] for row in s.company.conn.execute(query))
    return found


def _highest_issued(s, account_id):
    """The largest sequence number this account has ever put on a cheque, or zero."""
    revisions, current = c.check_instrument_revisions, c.check_instruments
    highest = 0
    for table in (revisions, current):
        value = s.company.conn.execute(sa.select(sa.func.max(table.c.check_sequence)).where(
            table.c.account_id == account_id)).scalar()
        highest = max(highest, int(value or 0))
    return highest


def duplicate(account, literal, holder):
    """Refuse a same-account collision by name, and say which cheque already has the number."""
    return BookflowError('E_DUPLICATE_NUMBER', message=(
        f'Check number {literal} is already on a cheque drawn on "{account["name"]}". '
        f'Two cheques cannot carry one number on one bank account; correct whichever of the '
        f'two is wrong, or write this one with another number.'), details={
        'fields': [{'field': 'number', 'problem': f'is already used on "{account["name"]}"'}],
        'number': literal, 'account_id': account['id'], 'held_by': holder['transaction_id'],
        'held_number': holder['check_number']})


# ---------------------------------------------------------------- the pointer


def pointer(account):
    """Where this account's chequebook says to start, and how wide its numbers are written.

    Three answers, each meaning something different: a number to start at, ``None`` because
    nobody has set one, and ``None`` because what is set names no place in a sequence -- a
    non-numeric string like ``EFT``, or ``0``, which no cheque carries. The last is a
    deliberate refusal rather than a coercion: guessing a next number there would be
    inventing a cheque.
    """
    raw = account.get('next_check_number')
    if raw is None:
        return None, None, 'unset'
    text = str(raw).strip()
    if is_sequence(text) and int(text) > 0:
        return int(text), (len(text) if text.startswith('0') else None), 'numeric'
    return None, None, 'opaque'


def _formatted(value, width):
    return f'{value:0{width}d}' if width else str(value)


def _allocate(s, account, taken=()):
    """The next free number on this account's chequebook, and where the pointer moves to.

    ``taken`` is every key this same command has already handed out and not yet written.
    One ``bill pay`` can write a cheque to each of several payees out of one chequebook, and
    the database it reads cannot see the numbers the call before it in that loop just took.
    """
    start, width, state = pointer(account)
    if state == 'opaque':
        raise _invalid('number', (
            f'"{account["name"]}" has a next check number of "{account["next_check_number"]}", '
            f'which names no place in a sequence, so there is no next number to take from it; '
            f'type the number written on the cheque, or set a next check number that counts'))
    if start is None:
        start = _highest_issued(s, account['id']) + 1
    value = start
    while True:
        if value >= SEQUENCE_LIMIT:
            raise BookflowError('E_VALUE_RANGE', details={'field': 'next_check_number',
                                                          'account_id': account['id']})
        literal = _formatted(value, width)
        _, key, sequence = canonical(literal)
        if key not in taken and not _ever_issued(s, account['id'], {key}):
            return literal, key, sequence, _formatted(value + 1, width)
        value += 1


def _advanced(account, sequence):
    """Where an explicitly typed number leaves the pointer. Never backwards."""
    start, width, state = pointer(account)
    if sequence is None or state == 'opaque':
        return None
    if start is not None and sequence < start:
        # Below the pointer is not the same thing as a duplicate: an older cheque is being
        # entered, and moving the pointer back would hand its successors out twice.
        return None
    return _formatted(sequence + 1, width)


# ---------------------------------------------------------------- planning a write


def current(s, transaction_id):
    """The cheque identity this check carries now, or None when it is not a check."""
    t = c.check_instruments
    found = s.company.conn.execute(sa.select(t).where(
        t.c.transaction_id == transaction_id)).mappings().first()
    return dict(found) if found else None


def request(account_id, number):
    """What ``check post`` and ``check update`` ask this module for."""
    return {'account_id': account_id, 'number': number}


IDENTITY = ('account_id', 'check_number', 'check_number_key', 'check_sequence', 'origin')


def identity(s, *, requested, existing, taken=()):
    """Which chequebook and which number this write leaves the cheque carrying.

    ``requested`` is the account and number the check commands supply; ``None`` means a
    writer that is not the check form -- the journal editor or the register -- is rewriting
    the entry, and the cheque identity is carried forward untouched rather than being
    re-derived from lines that writer was free to rearrange.

    ``taken`` is the set of canonical keys a caller writing several cheques in one command
    has already claimed; see ``_allocate``.

    Allocation, the refusal of a same-account collision and the pointer move all happen
    here, which is inside the writer's own transaction; nothing about a cheque number is
    settled at preview time.
    """
    if requested is None:
        if existing is None:
            return None
        return {key: existing[key] for key in IDENTITY} | {'pointer': None}
    account_id = requested['account_id']
    literal, key, sequence, origin, moves_to = _requested(s, requested, existing, account_id, taken)
    return dict(account_id=account_id, check_number=literal, check_number_key=key,
                check_sequence=sequence, origin=origin, pointer=moves_to)


def changed(settled, existing):
    """Whether this write leaves the cheque carrying something other than what it carried.

    The journal writer asks, because a correction that only renumbers a cheque changes no
    accounting at all: without this the entry would read as unchanged and the new number
    would be quietly dropped.
    """
    if settled is None:
        return False
    if existing is None:
        return True
    return any(settled[key] != existing[key] for key in IDENTITY)


def rows(s, ctx, header, revision, settled, existing, *, at, event):
    """The immutable revision row, the replaced projection, and the pointer move."""
    if settled is None:
        return None
    if header['type'] not in CHEQUE_DOCUMENT_TYPES:
        # The projection's own CHECK says the same thing; saying it here names the caller
        # rather than leaving a new document family to discover it as an integrity error.
        raise BookflowError('E_INTERNAL', message=(
            f'A {header["type"]} carries no cheque number.'))
    shared = {key: settled[key] for key in IDENTITY}
    written = dict(revision_id=revision['id'], transaction_id=header['id'], **shared,
                   created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                   audit_event_id=event)
    projected = dict(transaction_id=header['id'], type=header['type'], revision_id=revision['id'],
                     **shared, updated_at=at, updated_by=s.actor.id,
                     updated_via=ctx.interface.value, audit_event_id=event)
    return dict(revision=written, instrument=projected, before=existing,
                pointer=(settled['account_id'], settled['pointer']) if settled['pointer'] else None)


def _requested(s, requested, existing, account_id, taken=()):
    from bookflow.company import accounts as account_service
    account = account_service.resolve_account(s.company, account_id)
    supplied = requested['number']
    if supplied is None and existing is not None:
        # A correction that does not mention the number keeps it, even when it moves the
        # cheque to another bank account: the paper still says what it says. No pointer moves
        # for a number nobody issued or typed on this call -- correcting the memo on cheque
        # 5000 must not jump a chequebook that is only at 3000, and moving that cheque to
        # another account must not jump that account's book either. The allocator will walk
        # past the number anyway, because occupancy decides and the pointer only suggests.
        literal, key, sequence = existing['check_number'], existing['check_number_key'], existing['check_sequence']
        holder = _held(s, account_id, key, excluding=existing['transaction_id'])
        if holder is not None:
            raise duplicate(account, literal, holder)
        return literal, key, sequence, existing['origin'], None
    if supplied is None:
        literal, key, sequence, moves_to = _allocate(s, account, taken)
        return literal, key, sequence, 'issued', moves_to
    literal, key, sequence = canonical(supplied)
    holder = _held(s, account_id, key, excluding=existing['transaction_id'] if existing else None)
    if holder is not None:
        raise duplicate(account, literal, holder)
    return literal, key, sequence, 'issued', _advanced(account, sequence)


def write(s, planned):
    """Insert the revision row, replace the projection, and move the pointer."""
    from sqlalchemy.dialects.sqlite import insert
    s.company.conn.execute(c.check_instrument_revisions.insert().values(**planned['revision']))
    statement = insert(c.check_instruments).values(**planned['instrument'])
    s.company.conn.execute(statement.on_conflict_do_update(
        index_elements=['transaction_id'], set_=planned['instrument']))
    if planned['pointer']:
        account_id, value = planned['pointer']
        # A plain write: the pointer is a hint about where to look next, and the audited
        # record of what was issued is the instrument revision written just above.
        s.company.conn.execute(c.accounts.update().where(c.accounts.c.id == account_id)
                               .values(next_check_number=value))


def touches(planned):
    from bookflow.core.registry import Touched
    before = planned['before']
    return [Touched('check_instrument_revision', planned['revision']['revision_id'], 'create',
                    None, 1, planned['revision'], db='company'),
            Touched('check_instrument', planned['instrument']['transaction_id'],
                    'update' if before else 'create', None, 1, planned['instrument'],
                    before, db='company')]


# ---------------------------------------------------------------- reading one back


def ambiguous(noun, selector, candidates):
    """Two bank accounts issued this number; name both rather than picking one.

    ``1001`` on its own does not identify a cheque once each bank account runs its own
    sequence, and returning whichever row the database happened to hand back first would
    open, correct or void the wrong document without saying so.
    """
    return BookflowError('E_VALIDATION', message=(
        f'Check number {selector} names {len(candidates)} cheques, one on each of '
        + ', '.join(f'"{row["account_name"]}"' for row in candidates)
        + '. Open the one you mean by its id, or filter `check query` by account.'), details={
        'fields': [{'field': noun, 'problem': 'names more than one cheque'}],
        'selector': selector,
        'candidates': [{'transaction_id': row['transaction_id'], 'account_id': row['account_id'],
                        'account_name': row['account_name'], 'check_number': row['check_number']}
                       for row in candidates]})


def by_number(s, selector):
    """Every check carrying this number, on any bank account, with the account named.

    Matched on the canonical key as well as the literal, so ``1001`` finds the cheque written
    ``01001`` -- they are one number in one chequebook and the report treats them as one.
    """
    literal = selector.strip()
    if not literal or len(literal) > 64:
        return []
    keys = {literal, literal.casefold()}
    if is_sequence(literal):
        keys.add(str(int(literal)))
    t, a = c.check_instruments, c.accounts
    rows = s.company.conn.execute(
        sa.select(t.c.transaction_id, t.c.account_id, t.c.check_number, a.c.name.label('account_name'))
        .join(a, a.c.id == t.c.account_id)
        .where(sa.or_(t.c.check_number == literal, t.c.check_number_key.in_(sorted(keys))))
        .order_by(a.c.name, t.c.transaction_id)).mappings()
    return [dict(row) for row in rows]
