"""Entries to review: what an owner should look at after anyone -- an agent above all -- posts.

An agent asked to make a statement tie will, sooner or later, post an entry that makes it tie
(the R164 study: Penrose's AccountingBench, where the best models invented or pulled in
transactions to pass a reconciliation check despite being told not to). Nothing here refuses or
changes a posting. It lists, keyed on what an entry *does* rather than on which command wrote
it, the entries an owner would want to see, says why each is listed and who posted it for whom,
and lets the owner mark one reviewed. The same flags reach an agent at write time as warnings.

**The flags** (ISA 240 A43's "unusual entries", narrowed to what this ledger can see):

- ``reconciled_period``: a bank or card movement dated on or before the statement date of a
  reconciliation already finished for that account, entered after it was finished, that no
  reconciliation cleared. The statement was certified without it, so either the statement or
  the entry is wrong. ``closed_period``: any entry dated on or before the closing date, entered
  after the closing date was set (the anchor's Closing Date Exception report).
- ``cleared_on_arrival``: a movement an agent entered after a reconciliation was started and
  that the same reconciliation then cleared. This is what an invented plug looks like.
- ``opening_balance_equity``: an entry touching Opening Balance Equity (or the move-in's
  ``Cutover Clearing`` account) outside the move-in. A move-in entry carries a source reference
  starting ``cutover:``; one a person posted that way is setup and is not listed.
- ``round_unexplained``: an agent's journal entry with no memo putting a round amount (a whole
  multiple of 100) into a bank or card account in the last three days of a month.

Only live effects count: a voided or corrected document is judged by what it posts now. The
order of events is the audit sequence, never a clock, so two writes in one millisecond still
come out in the order they happened.

**Reviewed.** Marking an entry reviewed writes an audit event and nothing else: the entry is not
edited, and the mark names the revision and the flags it covered. A correction (a new revision)
or a new flag puts the entry back on the list.
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date

import sqlalchemy as sa

from bookflow.company import schema as c

FLAGS = ('reconciled_period', 'closed_period', 'cleared_on_arrival', 'opening_balance_equity',
         'round_unexplained')
# What the owner reads beside each flag.
WHY = {
    'reconciled_period': 'Dated inside a reconciled statement period but entered after the statement was finished, and not cleared on it',
    'closed_period': 'Dated on or before the closing date but entered after the books were closed',
    'cleared_on_arrival': 'Entered by an agent after the reconciliation was started, then cleared on that same reconciliation',
    'opening_balance_equity': 'Touches Opening Balance Equity outside the move-in',
    'round_unexplained': 'A round amount into a bank or card account at month end, by an agent, with no memo',
}
# The move-in marker R166's cutover sets on every document it writes (as the write's
# source reference), and the clearing account its balancing lines post to.
CUTOVER_PREFIX = 'cutover:'
CUTOVER_CLEARING = 'Cutover Clearing'
REVIEW_RECORD = 'transaction_review'
ROUND = 10000          # minor units: a whole multiple of 100.00
MONTH_END_DAYS = 3

LIVE = ("b.kind IN ('original','replacement') AND NOT EXISTS "
        "(SELECT 1 FROM posting_batches rv WHERE rv.reverses_batch_id=b.id)")
SCOPE = "(:tx IS NULL OR b.transaction_id IN (SELECT value FROM json_each(:tx)))"


def _rows(s, sql, **params):
    return [dict(r) for r in s.company.conn.execute(sa.text(sql), params).mappings()]


def _tx(transactions):
    return None if transactions is None else json.dumps(sorted(set(transactions)))


def closing(s):
    """(closing date, audit seq at which it was set to that date), or None when the books are open.

    The seq is the first event of the latest unbroken run in which company_info's audited
    snapshot carried today's closing date. A closing date with no audited setting is reported
    with seq None, and nothing is judged against it.
    """
    current = s.company.conn.execute(sa.select(c.company_info.c.closing_date)).scalar()
    if not current:
        return None
    from bookflow.core.audit import decode_snapshot
    seq, previous = None, None
    for row in s.company.conn.execute(sa.text(
            "SELECT e.seq, ae.after FROM audit_entries ae JOIN audit_events e ON e.id=ae.event_id "
            "WHERE ae.record_type='company_info' AND e.seq IS NOT NULL ORDER BY e.seq")):
        snapshot = decode_snapshot(row[1])
        if not isinstance(snapshot, dict) or 'closing_date' not in snapshot:
            continue
        value = snapshot['closing_date']
        if value == current and previous != current:
            seq = row[0]
        previous = value
    return current, seq


def _reconciled_period(s, tx, since_cert=None):
    found = _rows(s, f"""
        SELECT b.transaction_id, b.id AS batch_id, l.account_id, a.full_name AS account,
               b.effective_date, cert.id AS certificate_id, cert.statement_date
        FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
        JOIN accounts a ON a.id=l.account_id
        JOIN reconciliation_active_certificates ac ON ac.account_id=l.account_id
             AND ac.statement_date>=b.effective_date
        JOIN reconciliation_certificates cert ON cert.id=ac.certificate_id
        JOIN audit_events be ON be.id=b.audit_event_id
        JOIN audit_events ce ON ce.id=cert.audit_event_id
        WHERE {LIVE} AND {SCOPE} AND a.type IN ('bank','credit_card') AND be.seq>ce.seq
          AND NOT EXISTS (SELECT 1 FROM reconciliation_keys k
                          JOIN reconciliation_current_members m ON m.key_id=k.id
                          JOIN reconciliation_claims cl ON cl.id=m.claim_id
                          WHERE k.transaction_id=b.transaction_id AND cl.account_id=l.account_id)
        ORDER BY ac.statement_date""", tx=tx)
    out = {}
    for row in found:
        out.setdefault(row['transaction_id'], row)
    return out


def _closed_period(s, tx):
    found = closing(s)
    if found is None or found[1] is None:
        return {}
    closed, seq = found
    out = {}
    for row in _rows(s, f"""
            SELECT b.transaction_id, b.id AS batch_id, b.effective_date
            FROM posting_batches b JOIN audit_events be ON be.id=b.audit_event_id
            WHERE {LIVE} AND {SCOPE} AND b.effective_date<=:closed AND be.seq>:seq
            ORDER BY be.seq""", tx=tx, closed=closed, seq=seq):
        out.setdefault(row['transaction_id'], dict(row, closing_date=closed))
    return out


def _cleared_on_arrival(s, tx, certificates=None):
    found = _rows(s, f"""
        SELECT v.transaction_id, v.business_batch_id AS batch_id, cert.id AS certificate_id,
               cert.statement_date, a.full_name AS account
        FROM reconciliation_certificate_members cm
        JOIN reconciliation_certificates cert ON cert.id=cm.certificate_id
        JOIN reconciliation_draft_revisions dr ON dr.id=cert.origin_draft_revision_id
        JOIN reconciliation_drafts d ON d.id=dr.draft_id
        JOIN audit_events de ON de.id=d.audit_event_id
        JOIN reconciliation_effect_versions v ON v.id=cm.version_id
        JOIN posting_batches b ON b.id=v.business_batch_id
        JOIN audit_events be ON be.id=b.audit_event_id
        JOIN accounts a ON a.id=cert.account_id
        WHERE cm.classification='selected' AND be.actor_kind='agent' AND be.seq>de.seq
          AND {LIVE} AND {SCOPE}
          AND (:certs IS NULL OR cert.id IN (SELECT value FROM json_each(:certs)))
        ORDER BY cert.statement_date""", tx=tx,
        certs=None if certificates is None else json.dumps(sorted(certificates)))
    out = {}
    for row in found:
        out.setdefault(row['transaction_id'], row)
    return out


def _opening_balance_equity(s, tx):
    out = {}
    for row in _rows(s, f"""
            SELECT b.transaction_id, b.id AS batch_id, a.full_name AS account
            FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
            JOIN accounts a ON a.id=l.account_id
            LEFT JOIN audit_events be ON be.id=b.audit_event_id
            WHERE {LIVE} AND {SCOPE}
              AND (a.system_role='opening_balance_equity' OR a.full_name=:clearing)
              AND NOT (coalesce(be.source_ref,'') LIKE :cutover
                       AND coalesce(be.actor_kind,'')<>'agent')""",
            tx=tx, clearing=CUTOVER_CLEARING, cutover=CUTOVER_PREFIX + '%'):
        out.setdefault(row['transaction_id'], row)
    return out


def _month_end(day):
    value = date.fromisoformat(day)
    return monthrange(value.year, value.month)[1] - value.day < MONTH_END_DAYS


def _round_unexplained(s, tx):
    out = {}
    for row in _rows(s, f"""
            SELECT b.transaction_id, b.id AS batch_id, b.effective_date, a.full_name AS account,
                   l.debit_minor_units+l.credit_minor_units AS amount
            FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
            JOIN accounts a ON a.id=l.account_id
            JOIN transactions t ON t.id=b.transaction_id
            JOIN transaction_revisions r ON r.id=t.current_revision_id
            JOIN audit_events be ON be.id=b.audit_event_id
            WHERE {LIVE} AND {SCOPE} AND t.type='journal_entry' AND be.actor_kind='agent'
              AND a.type IN ('bank','credit_card') AND trim(coalesce(r.memo,''))=''
              AND (l.debit_minor_units+l.credit_minor_units) % :round = 0
              AND NOT EXISTS (SELECT 1 FROM money_out_documents mo WHERE mo.transaction_id=t.id)""",
            tx=tx, round=ROUND):
        if _month_end(row['effective_date']):
            out.setdefault(row['transaction_id'], row)
    return out


def flagged(s, *, transactions=None, certificates=None, only=FLAGS):
    """{transaction id: {flag: facts}} for the live entries that match each flag."""
    tx = _tx(transactions)
    finders = dict(
        reconciled_period=lambda: _reconciled_period(s, tx),
        closed_period=lambda: _closed_period(s, tx),
        cleared_on_arrival=lambda: _cleared_on_arrival(s, tx, certificates),
        opening_balance_equity=lambda: _opening_balance_equity(s, tx),
        round_unexplained=lambda: _round_unexplained(s, tx))
    out = {}
    for flag in FLAGS:
        if flag not in only:
            continue
        for identity, facts in finders[flag]().items():
            out.setdefault(identity, {})[flag] = facts
    return out


def reviews(s, identities):
    """{transaction id: the latest review mark} for these transactions."""
    if not identities:
        return {}
    from bookflow.core.audit import decode_snapshot
    found = {}
    for row in _rows(s, """
            SELECT ae.record_id, ae.after, e.actor_id, e.at, e.seq FROM audit_entries ae
            JOIN audit_events e ON e.id=ae.event_id
            WHERE ae.record_type=:kind AND ae.record_id IN (SELECT value FROM json_each(:ids))
            ORDER BY e.seq""", kind=REVIEW_RECORD, ids=json.dumps(sorted(identities))):
        found[row['record_id']] = dict(decode_snapshot(row['after']) or {}, actor_id=row['actor_id'],
                                       at=row['at'])
    return found


def covered(review, revision_id, flags):
    """Whether a review mark still covers an entry: same revision, no flag it did not see."""
    return (review is not None and review.get('revision_id') == revision_id
            and set(flags) <= set(review.get('flags') or ()))


def documents(s, identities):
    """Current facts of these transactions, and who wrote the effect they post now."""
    if not identities:
        return {}
    rows = _rows(s, f"""
        SELECT t.id, t.type, t.version, t.current_revision_id AS revision_id, r.number, r.date,
               r.memo, r.total_minor_units, r.currency, mo.kind AS money_out_kind,
               e.actor_id, e.actor_kind, e.on_behalf_of, e.interface, e.reason, e.source_ref,
               e.at AS entered_at, e.command
        FROM transactions t JOIN transaction_revisions r ON r.id=t.current_revision_id
        LEFT JOIN money_out_documents mo ON mo.transaction_id=t.id AND mo.type=t.type
        LEFT JOIN posting_batches b ON b.transaction_id=t.id AND {LIVE}
        LEFT JOIN audit_events e ON e.id=coalesce(b.audit_event_id, r.audit_event_id)
        WHERE t.id IN (SELECT value FROM json_each(:ids))
        ORDER BY e.seq""", ids=json.dumps(sorted(identities)))
    out = {}
    for row in rows:
        out[row['id']] = row        # the latest live effect's writer wins
    return out


def sentence(flag, facts):
    """The warning an agent reads when its own write matches a flag."""
    if flag == 'reconciled_period':
        return (f"This posts to {facts['account']} on {facts['effective_date']}, inside the statement "
                f"dated {facts['statement_date']} that is already reconciled, which did not include it. "
                f"It is on the owner's entries-to-review list.")
    if flag == 'closed_period':
        return (f"This is dated {facts['effective_date']}, on or before the closing date "
                f"{facts['closing_date']}. It is on the owner's entries-to-review list.")
    if flag == 'cleared_on_arrival':
        return (f"This reconciliation of {facts['account']} to {facts['statement_date']} cleared an "
                f"entry an agent posted after the reconciliation was started. A statement that ties "
                f"only because of such an entry is what the owner reviews; it is on the owner's "
                f"entries-to-review list. If a statement will not tie, leave the draft open with a "
                f"note instead.")
    if flag == 'opening_balance_equity':
        return (f"This touches {facts['account']}, which holds opening balances from the move-in. "
                f"An entry there afterwards is on the owner's entries-to-review list.")
    return (f"A round amount into {facts['account']} at month end with no memo is on the owner's "
            f"entries-to-review list; say what it is in the memo.")


# ------------------------------------------------------------------ write time

def watermark(s):
    """Where the ledger and the certificates stand before a write, to find what it added."""
    raw = s.company.raw
    return (raw.execute("SELECT coalesce(max(rowid),0) FROM posting_batches").fetchone()[0],
            raw.execute("SELECT coalesce(max(rowid),0) FROM reconciliation_certificates").fetchone()[0])


def write_warnings(s, before):
    """Warnings for what one write just added, in the words `sentence` gives each flag."""
    raw = s.company.raw
    batches, certs = before
    added = [r[0] for r in raw.execute(
        "SELECT DISTINCT transaction_id FROM posting_batches WHERE rowid>? AND kind<>'reversal'",
        (batches,))]
    new_certs = [r[0] for r in raw.execute(
        "SELECT id FROM reconciliation_certificates WHERE rowid>?", (certs,))]
    lines = []
    if added:
        found = flagged(s, transactions=added, only=('reconciled_period', 'closed_period',
                                                     'opening_balance_equity'))
        for identity in added:
            for flag, facts in found.get(identity, {}).items():
                lines.append(sentence(flag, facts))
    if new_certs:
        found = flagged(s, certificates=new_certs, only=('cleared_on_arrival',))
        for facts in found.values():
            lines.append(sentence('cleared_on_arrival', facts['cleared_on_arrival']))
    return list(dict.fromkeys(lines))
