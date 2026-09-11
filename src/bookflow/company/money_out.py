"""Finding, reading and correcting the three documents that post through the register.

A check, a credit card charge and a transfer are journal entries: the same batch, the same
lines, the same money that ``register post`` writes. What this module adds is the ability to
address one *as itself* -- to list checks without listing every journal entry, to read one
back with the figures its own footer showed, and to correct or void one in the document's
words rather than in the register's.

**How a document is found.** ``money_out_documents`` carries the one fact the posting cannot:
which of the three a person entered. Everything here starts from that marker, so a hand-typed
journal entry that happens to credit a bank account is never listed as a check.

**How a document is read.** The footer is derived from the stored revision, never from a
second copy of the amount: the funding line is line one, the expense lines are the rest, and
the totals are their sums. A revision that no longer has that shape -- because someone opened
it in the journal editor and rearranged it -- is refused by name rather than described wrongly,
the same way ``register update`` refuses an entry it cannot edit losslessly.

**How a document is changed.** Correction and void are ``journals.prepare`` under the
document's own vocabulary, so an update appends an exact reversal of the old accounting at its
old date plus a full replacement at the new one, and a void reverses exactly at the document's
own date. Neither erases anything, and ``history`` walks the revisions both leave behind.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import journals, schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid

# The noun a person types, and the kind stored against the transaction it posts. The stored
# spellings are exactly ``money_out_schema.KINDS``, which is what the table's own CHECK
# constraint allows; ``tests/test_money_out_lifecycle.py`` holds the two together.
KIND = {'check': 'check', 'card-charge': 'card_charge', 'transfer': 'transfer'}

# What each document is called in a sentence a person reads.
LABEL = {'check': 'check', 'card-charge': 'credit card charge', 'transfer': 'transfer'}


def resolve(s, selector, noun):
    """The document this selector names, by stable id or by the number a person reads off it.

    Only a transaction carrying this noun's marker answers. A journal entry, a check when the
    caller asked for a transfer, or a document that was never entered as one of the three is
    not found here -- it is not this noun's record, and reporting it would be worse than
    saying so.

    **A check is named by the number on its face**, which belongs to one bank account's
    chequebook, so two accounts can both have written a cheque ``1001``. This never picks one
    of them: it names both and refuses. That is the whole point of the ambiguity -- returning
    whichever row came back first would have shown, corrected or voided the wrong cheque
    without saying a word. A cheque written from Pay Bills shares that chequebook but is not
    a check, so it is filtered out of the candidates before they are counted rather than
    making a number ambiguous that names one check. The document reference is still accepted
    for a cheque whose number finds nothing, which is what makes a journal-series reference
    from the audit page work.

    A card charge and a transfer have no cheque number, so the document reference is all they
    are ever addressed by; that number is unique across journal entries, but more than one
    row is refused here as well rather than silently resolved.
    """
    t, m = c.transactions, c.money_out_documents
    key = selector.upper() if is_ulid(selector) else selector
    base = sa.select(t).join(m, m.c.transaction_id == t.c.id).where(m.c.kind == KIND[noun])
    found = [dict(r) for r in s.company.conn.execute(base.where(t.c.id == key)).mappings()]
    if not found and noun == 'check':
        from bookflow.company import check_numbers
        candidates = check_numbers.by_number(s, selector)
        if candidates:
            # Narrowed to the cheques that are checks before ambiguity is decided. A bill
            # payment carries a cheque number too, and `check show` cannot open one, so
            # letting it make 1001 ambiguous would refuse a number that names exactly one
            # check. Two checks on two chequebooks still name two, and that is the case the
            # guard below is for.
            owned = {row['id'] for row in s.company.conn.execute(base.where(
                t.c.id.in_([row['transaction_id'] for row in candidates]))).mappings()}
            candidates = [row for row in candidates if row['transaction_id'] in owned]
        if len(candidates) > 1:
            raise check_numbers.ambiguous(noun, selector, candidates)
        if candidates:
            found = [dict(r) for r in s.company.conn.execute(
                base.where(t.c.id == candidates[0]['transaction_id'])).mappings()]
    if not found:
        found = [dict(r) for r in s.company.conn.execute(base.where(t.c.number == selector)).mappings()]
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND',
                            details={'record_type': KIND[noun], 'selector': selector})
    if len(found) > 1:
        raise BookflowError('E_VALIDATION', message=(
            f'"{selector}" names {len(found)} {LABEL[noun]}s. Open the one you mean by its id.'),
            details={'fields': [{'field': noun.replace('-', '_'),
                                 'problem': 'names more than one document'}],
                     'selector': selector,
                     'candidates': [{'transaction_id': row['id'], 'number': row['number']}
                                    for row in found]})
    return found[0]


def check_numbers_of(s, noun, revision_ids):
    """The number each of these revisions of a cheque was written with, keyed by revision.

    Read from the immutable per-revision identity rather than from the cheque's current one,
    so reading revision one back shows the number revision one had. A card charge and a
    transfer have no such number and answer with nothing at all.
    """
    if noun != 'check' or not revision_ids:
        return {}
    t = c.check_instrument_revisions
    return {row['revision_id']: row['check_number'] for row in s.company.conn.execute(
        sa.select(t.c.revision_id, t.c.check_number).where(
            t.c.revision_id.in_(list(revision_ids)))).mappings()}


def marker(noun, transaction_id, *, at, actor_id, interface, event):
    """The row that says a person entered this transaction as this document."""
    return dict(transaction_id=transaction_id, type='journal_entry', kind=KIND[noun],
                created_at=at, created_by=actor_id, created_via=interface, audit_event_id=event)


def lines(s, revision):
    return journals.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                         order=c.document_lines.c.position)


def lines_by_revision(s, revision_ids):
    """Every entered line of the named revisions, in one read, grouped and ordered."""
    if not revision_ids:
        return {}
    grouped = {identifier: [] for identifier in revision_ids}
    for row in journals.rows(s, c.document_lines,
                             c.document_lines.c.revision_id.in_(list(revision_ids)),
                             order=c.document_lines.c.position):
        grouped.setdefault(row['revision_id'], []).append(row)
    return grouped


def snapshot(line):
    return json.loads(line['account_snapshot']) if line['account_snapshot'] else {}


def unreadable(noun, header, problem):
    """Refuse to describe an entry that is no longer the document it was entered as.

    A check whose lines were rearranged in the journal editor is still perfectly good
    accounting; what it is not is a check any more. Saying so and pointing at the editor that
    can open it is the only honest answer -- inventing a funding line or a payee out of
    whatever now sits at position one would report figures nobody entered.
    """
    return BookflowError('E_VALIDATION', message=(
        f'This entry was written as a {LABEL[noun]} but no longer has that shape: {problem}. '
        f'Open it with `journal show` and correct it there.'), details={
        'fields': [{'field': noun.replace('-', '_'), 'problem': problem}],
        'open_journal': {'journal': header['id'], 'command': 'journal show'}})


# ---------------------------------------------------------------- paging


class _Contract:
    """The page contract `query.page_state` fingerprints: every filter, limit and direction."""

    def __init__(self, inp):
        self._inp = inp
        self.cursor = inp.cursor
        self.query = getattr(inp, 'query', None)

    def model_dump(self, **kwargs):
        return self._inp.model_dump(**kwargs)


def state_for(s, ctx, inp, noun, verb):
    from bookflow.company.query import page_state
    return page_state(s, f'{noun} {verb}', _Contract(inp), ctx.on_behalf_of)


def take(s, query, state, limit):
    from bookflow.company.query import continuation
    found = [dict(row) for row in s.company.conn.execute(
        query.offset(state.offset).limit(limit + 1)).mappings()]
    more, found = len(found) > limit, found[:limit]
    return found, dict(count=len(found), has_more=more,
                       next_cursor=continuation(state, len(found), more),
                       audit_watermark=state.sequence)


def ordered(query, direction):
    """Accounting date then stable id; descending is the exact reverse of the same order."""
    t, r = c.transactions, c.transaction_revisions
    order = (r.c.date, t.c.id)
    return query.order_by(*([column.desc() for column in order] if direction == 'desc' else order))


def headers(s, identifiers):
    """The full header and current revision of each listed document, in the listed order."""
    if not identifiers:
        return []
    rows = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.transactions).where(c.transactions.c.id.in_(identifiers))).mappings()}
    ordered_headers = [rows[identifier] for identifier in identifiers]
    revision_ids = [header['current_revision_id'] for header in ordered_headers]
    revisions = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.transaction_revisions).where(
            c.transaction_revisions.c.id.in_(revision_ids))).mappings()}
    pairs = []
    for header in ordered_headers:
        revision = revisions.get(header['current_revision_id'])
        if revision is None or revision['transaction_id'] != header['id']:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'transaction_revision'})
        pairs.append((header, revision))
    return pairs


def history_page(s, ctx, inp, noun, header):
    """The document's immutable revisions, oldest first, with each one's own lines."""
    state = state_for(s, ctx, inp, noun, 'history')
    t = c.transaction_revisions
    query = sa.select(t).where(t.c.transaction_id == header['id']).order_by(t.c.revision_number)
    found, shared = take(s, query, state, inp.limit)
    return found, shared, lines_by_revision(s, [revision['id'] for revision in found])
