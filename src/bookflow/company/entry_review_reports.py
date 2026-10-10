"""The owner's two review reports: entries to review, and prior balances that moved.

**Entries to review** lists the live entries `entry_review` flags, newest entry first, each with
why it is listed, who posted it, for whom, through what and with what reason, and the
reconciliation it touches. Beside the rows it lists every open statement reconciliation whose
difference is not zero, with who started it and the latest note on it: the sanctioned stop an
agent takes when a statement will not tie.

**Prior balances** is the anchor's Troubleshoot Prior Account Balances, kept without a stored
copy. A balance "as of a date, as it stood when it was reviewed" is the sum of the ledger's
immutable lines dated on or before that date and written at or before the audit event that
reviewed it, so it is derived rather than saved: each finished reconciliation reviews its own
account at its statement date, and the closing date reviews every account at the closing date.
A balance that has since moved is listed with the entries that moved it -- a back-dated entry,
or a void or correction of one already there.
"""
from __future__ import annotations

from typing import Literal

import sqlalchemy as sa
from pydantic import Field, field_validator

from bookflow.company import entry_review as review
from bookflow.company import ledger_reports as ledger
from bookflow.company import schema as c
from bookflow.company.ledger_reports import (
    MoneyOutKind, MoneyOutput, StrictModel, TransactionType, iso_date, money,
)
from bookflow.core.errors import BookflowError

ENTRIES = "entries-to-review"
PRIOR = "prior-balances"
Flag = Literal['reconciled_period', 'closed_period', 'cleared_on_arrival', 'opening_balance_equity',
               'round_unexplained', 'write_off']


class EntriesToReviewInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive date, YYYY-MM-DD; entries dated after it are left out.")
    include_reviewed: bool = Field(default=False, description="Also list entries an owner already marked reviewed.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        return self.as_of


class EntriesToReviewTotals(StrictModel):
    entries: int = Field(ge=0, description="Entries listed, whole report.")
    open_reconciliations: int = Field(ge=0, description="Open statement reconciliations whose difference is not zero.")


class EntryToReviewRow(StrictModel):
    transaction_id: str
    revision_id: str
    transaction_type: TransactionType
    money_out_kind: MoneyOutKind | None
    number: str | None
    date: str
    memo: str | None
    amount: MoneyOutput
    flags: list[Flag]
    why: str = Field(description="Why it is listed, one sentence per flag.")
    account: str | None = Field(description="The account the first flag is about.")
    statement_date: str | None = Field(description="The reconciliation (or closing date) it touches, when a flag names one.")
    posted_by_id: str | None
    posted_by: str | None
    actor_kind: str | None
    on_behalf_of_id: str | None
    on_behalf_of: str | None
    interface: str | None
    reason: str | None
    source_ref: str | None
    entered_at: str | None
    reviewed: bool
    reviewed_by: str | None
    reviewed_at: str | None


class OpenReconciliation(StrictModel):
    draft_id: str
    account_id: str
    display_account_label: str
    statement_date: str | None
    difference: MoneyOutput | None = Field(description="Null when the draft can no longer be read against the ledger.")
    started_by_id: str
    started_by: str | None
    started_by_kind: str | None
    started_at: str
    note: str | None = Field(description="The latest note on the draft: what was checked, as whoever stopped left it.")
    note_by: str | None
    note_at: str | None


class EntriesToReviewOutput(ledger.Page):
    totals: EntriesToReviewTotals
    rows: list[EntryToReviewRow]
    open_reconciliations: list[OpenReconciliation]


def _names(s, ids):
    from bookflow.company.info import principal_names
    return principal_names(s.company, {i for i in ids if i})


def _first(facts):
    for flag in review.FLAGS:
        if flag in facts:
            f = facts[flag]
            return f.get('account'), f.get('statement_date') or f.get('closing_date')
    return None, None


def entry_rows(s, as_of, include_reviewed):
    """Every row of the report, newest entry first; shared by the report and the Overview."""
    found = review.flagged(s)
    docs = review.documents(s, found)
    marks = review.reviews(s, found)
    names = _names(s, {d['actor_id'] for d in docs.values()} | {d['on_behalf_of'] for d in docs.values()}
                   | {m['actor_id'] for m in marks.values()})
    rows = []
    for identity, facts in found.items():
        d = docs.get(identity)
        if d is None or d['date'] > as_of:
            continue
        flags = [f for f in review.FLAGS if f in facts]
        mark = marks.get(identity)
        done = review.covered(mark, d['revision_id'], flags)
        if done and not include_reviewed:
            continue
        account, statement_date = _first(facts)
        rows.append(EntryToReviewRow(
            transaction_id=identity, revision_id=d['revision_id'], transaction_type=d['type'],
            money_out_kind=d['money_out_kind'], number=d['number'], date=d['date'], memo=d['memo'],
            # A write-off receives no cash; the row shows the balance written off.
            amount=money(facts['write_off']['amount'] if 'write_off' in facts and not d['total_minor_units']
                         else d['total_minor_units'], d['currency']), flags=flags,
            why='. '.join(review.WHY[f] for f in flags) + '.', account=account,
            statement_date=statement_date, posted_by_id=d['actor_id'], posted_by=names.get(d['actor_id']),
            actor_kind=d['actor_kind'], on_behalf_of_id=d['on_behalf_of'],
            on_behalf_of=names.get(d['on_behalf_of']), interface=d['interface'], reason=d['reason'],
            source_ref=d['source_ref'], entered_at=d['entered_at'], reviewed=done,
            reviewed_by=names.get(mark['actor_id']) if done else None,
            reviewed_at=mark['at'] if done else None))
    rows.sort(key=lambda r: (r.entered_at or '', r.transaction_id), reverse=True)
    return rows


def open_reconciliations(s):
    """Open statement drafts that do not tie, with who started each and the note left on it."""
    from bookflow.commands import reconcile_cmds
    from bookflow.company import reconciliation_drafts as drafts
    from bookflow.company import reconciliation_loading as loading
    t = c.reconciliation_drafts
    open_rows = s.company.conn.execute(sa.select(t, c.accounts.c.full_name, c.accounts.c.currency)
        .join(c.accounts, c.accounts.c.id == t.c.account_id)
        .where(t.c.state == 'open', t.c.kind.in_(('statement', 'amendment')))
        .order_by(t.c.created_at, t.c.id)).mappings().all()
    out, snapshots = [], {}
    for row in open_rows:
        statement_date, difference = None, None
        try:
            if row['account_id'] not in snapshots:
                snapshots[row['account_id']] = loading.load(s, row['account_id'])
            snapshot = snapshots[row['account_id']]
            draft = drafts.load(snapshot, row['id'], authority_transactions=snapshot.authority_transactions)
            statement_date = draft.header.statement_date
            _, totals = reconcile_cmds._totals(snapshot, draft)
            if totals.difference == 0:
                continue
            difference = money(totals.difference, row['currency'])
        except BookflowError:
            pass
        note = s.company.conn.execute(sa.select(c.notes.c.body, c.notes.c.author_id, c.notes.c.at)
            .where(c.notes.c.record_type == 'reconciliation_draft', c.notes.c.record_id == row['id'])
            .order_by(c.notes.c.at.desc(), c.notes.c.id.desc()).limit(1)).first()
        kind = s.company.conn.execute(sa.select(c.audit_events.c.actor_kind)
            .where(c.audit_events.c.id == row['audit_event_id'])).scalar()
        names = _names(s, {row['created_by'], note[1] if note else None})
        out.append(OpenReconciliation(
            draft_id=row['id'], account_id=row['account_id'], display_account_label=row['full_name'],
            statement_date=statement_date, difference=difference, started_by_id=row['created_by'],
            started_by=names.get(row['created_by']), started_by_kind=kind, started_at=row['created_at'],
            note=note[0] if note else None, note_by=names.get(note[1]) if note else None,
            note_at=note[2] if note else None))
    return out


def entries_to_review(inp: EntriesToReviewInput, s, *, principal_id=None) -> EntriesToReviewOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, ENTRIES, principal_id, None, account_scoped=False)
        rows = entry_rows(s, inp.as_of, inp.include_reviewed)
        drafts_open = open_reconciliations(s)
        page = rows[offset:offset + inp.limit + 1]
        shown = page[:inp.limit]
        return EntriesToReviewOutput(
            metadata=state.metadata,
            totals=EntriesToReviewTotals(entries=len(rows), open_reconciliations=len(drafts_open)),
            rows=shown, count=len(shown), open_reconciliations=drafts_open,
            next_cursor=ledger._continuation(state, offset, len(shown), len(page) > inp.limit, s.company))


# ------------------------------------------------------------------ prior balances

class PriorBalancesInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive date, YYYY-MM-DD; reviews of dates after it are left out.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        return self.as_of


class PriorBalancesTotals(StrictModel):
    reviews: int = Field(ge=0, description="Finished reconciliations and the closing date, each a reviewed balance date.")
    changed_balances: int = Field(ge=0, description="Account balances that moved after they were reviewed.")
    entries: int = Field(ge=0, description="Entries responsible, counted once per balance they moved.")


class PriorBalanceRow(StrictModel):
    kind: Literal["balance", "entry"]
    review: Literal["statement", "closing_date"]
    review_id: str = Field(description="The certificate, or 'closing-date'.")
    review_date: str = Field(description="The statement date or the closing date: the balance is as of this date.")
    reviewed_at: str = Field(description="When it was reviewed: the reconciliation finished, or the closing date set.")
    account_id: str
    display_account_label: str
    reviewed_balance: MoneyOutput | None = Field(description="Balance as of review_date as it stood when reviewed; balance rows only.")
    current_balance: MoneyOutput | None = Field(description="Balance as of review_date now; balance rows only.")
    change: MoneyOutput = Field(description="What moved since the review: on a balance row the total, on an entry row that entry's share.")
    transaction_id: str | None
    transaction_type: TransactionType | None
    money_out_kind: MoneyOutKind | None
    number: str | None
    date: str | None
    entered_at: str | None
    posted_by: str | None


class PriorBalancesOutput(ledger.Page):
    totals: PriorBalancesTotals
    rows: list[PriorBalanceRow]


def _checkpoints(s, as_of):
    found = [dict(r) for r in s.company.conn.execute(sa.text("""
        SELECT cert.id, ac.account_id, ac.statement_date AS day, ce.seq, ce.at
        FROM reconciliation_active_certificates ac
        JOIN reconciliation_certificates cert ON cert.id=ac.certificate_id
        JOIN audit_events ce ON ce.id=cert.audit_event_id
        WHERE ac.statement_date<=:as_of ORDER BY ac.statement_date, cert.id"""), dict(as_of=as_of)).mappings()]
    out = [dict(r, review='statement') for r in found]
    closed = review.closing(s)
    if closed is not None and closed[1] is not None and closed[0] <= as_of:
        at = s.company.conn.execute(sa.select(c.audit_events.c.at).where(c.audit_events.c.seq == closed[1])).scalar()
        out.append(dict(id='closing-date', account_id=None, day=closed[0], seq=closed[1], at=at, review='closing_date'))
    return out


def prior_rows(s, as_of):
    from bookflow.company.accounts import NORMAL_BALANCE
    currency = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar()
    rows, changed, entries = [], 0, 0
    checkpoints = _checkpoints(s, as_of)
    for point in checkpoints:
        moved = [dict(r) for r in s.company.conn.execute(sa.text("""
            SELECT l.account_id, a.full_name, a.type, b.transaction_id,
                   sum(l.debit_minor_units-l.credit_minor_units) AS delta, max(be.at) AS entered_at,
                   max(be.seq) AS seq
            FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
            JOIN audit_events be ON be.id=b.audit_event_id JOIN accounts a ON a.id=l.account_id
            WHERE b.effective_date<=:day AND be.seq>:seq AND (:account IS NULL OR l.account_id=:account)
            GROUP BY l.account_id, b.transaction_id HAVING delta<>0
            ORDER BY a.full_name, l.account_id, seq"""),
            dict(day=point['day'], seq=point['seq'], account=point['account_id'])).mappings()]
        by_account = {}
        for row in moved:
            by_account.setdefault(row['account_id'], []).append(row)
        docs = review.documents(s, {r['transaction_id'] for r in moved})
        names = _names(s, {d['actor_id'] for d in docs.values()})
        for account_id, items in by_account.items():
            sign = -1 if NORMAL_BALANCE.get(items[0]['type']) == 'credit' else 1
            delta = sum(r['delta'] for r in items)
            if delta == 0:
                continue
            now = s.company.conn.execute(sa.text("""
                SELECT coalesce(sum(l.debit_minor_units-l.credit_minor_units),0) FROM posting_lines l
                JOIN posting_batches b ON b.id=l.batch_id WHERE l.account_id=:a AND b.effective_date<=:day"""),
                dict(a=account_id, day=point['day'])).scalar()
            changed += 1
            common = dict(review=point['review'], review_id=point['id'], review_date=point['day'],
                          reviewed_at=point['at'], account_id=account_id,
                          display_account_label=items[0]['full_name'])
            rows.append(PriorBalanceRow(
                kind='balance', reviewed_balance=money(sign * (now - delta), currency),
                current_balance=money(sign * now, currency), change=money(sign * delta, currency),
                transaction_id=None, transaction_type=None, money_out_kind=None, number=None, date=None,
                entered_at=None, posted_by=None, **common))
            for item in items:
                entries += 1
                d = docs.get(item['transaction_id'], {})
                rows.append(PriorBalanceRow(
                    kind='entry', reviewed_balance=None, current_balance=None,
                    change=money(sign * item['delta'], currency), transaction_id=item['transaction_id'],
                    transaction_type=d.get('type'), money_out_kind=d.get('money_out_kind'),
                    number=d.get('number'), date=d.get('date'), entered_at=item['entered_at'],
                    posted_by=names.get(d.get('actor_id')), **common))
    return rows, PriorBalancesTotals(reviews=len(checkpoints), changed_balances=changed, entries=entries)


def prior_balances(inp: PriorBalancesInput, s, *, principal_id=None) -> PriorBalancesOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, PRIOR, principal_id, None, account_scoped=False)
        rows, totals = prior_rows(s, inp.as_of)
        page = rows[offset:offset + inp.limit + 1]
        shown = page[:inp.limit]
        return PriorBalancesOutput(
            metadata=state.metadata, totals=totals, rows=shown, count=len(shown),
            next_cursor=ledger._continuation(state, offset, len(shown), len(page) > inp.limit, s.company))
