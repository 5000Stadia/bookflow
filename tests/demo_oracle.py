"""Evidence at the scope of the claim, for tests that run against a seeded demo company.

Three kinds of claim get made about the demo seeds, and they were all being checked the same
way -- with whole-company totals written as literals. That is why every legitimate seed addition
broke unrelated package tests, and why the repairs kept holding only until the next one.

* "these commands post nothing" is a claim about *those commands*. `written_by` answers it from
  the audit trail: every financial row names the audit event that wrote it, and every audit
  event names its command. A posting made under a different document number, in another
  namespace, or reversed a moment later, is still owned by the event that made it.
* "this preview changes nothing" is a claim about *the rows a preview promised not to touch*.
  `financial_snapshot` takes identities and amounts, not balances, because an erroneous posting
  and its reversal net to zero and must still fail.
* "the company's books are these" is a claim about *the whole company*, and belongs in one
  full-company oracle per company rather than in every package test that happens to have a
  database open.

`position` and `trial_total` exist because a trial-balance total is not additive across
scenarios. It is `sum(max(net_per_account, 0))`, so two scenarios whose per-scenario totals are
150 each can combine to 50 when an account crosses sign between them. Combine by account first,
then derive the total from the combined position; never add scenario totals together.
"""
from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.storage.engine import open_database

# The tables that say money moved. `transactions` is here for its identity and status; the
# amounts live below it. A claim about "posting nothing" has to look at all five, because a
# document with no legs still occupies a number and a balance-only check would miss it.
FINANCIAL_TABLES = ('transactions', 'transaction_revisions', 'posting_batches', 'posting_lines',
                    'posting_line_sources')


def company_database(client, company):
    return open_database(Path(client.run('company show', {}, company=company)['path']) / 'company.db',
                         writable=False)


def _rows(db, table, columns=None):
    selected = c.metadata.tables[table]
    query = sa.select(*[selected.c[name] for name in columns]) if columns else sa.select(selected)
    return [tuple(row) for row in db.conn.execute(query)]


def financial_snapshot(client, company, tables=FINANCIAL_TABLES):
    """Every financial row, by identity and amount, in a form two snapshots can be compared in.

    Deliberately not balances. An erroneous posting followed by its reversal nets to zero on
    every account, and this is what still sees it.
    """
    with company_database(client, company) as db:
        return {table: sorted(_rows(db, table)) for table in tables}


def written_by(client, company, commands):
    """The financial rows whose audit events name one of these commands.

    This is the whole of "posts nothing": not a balance, not a total, not a namespace filter --
    the rows those commands are recorded as having written. A work command that posted under a
    sales document's number is caught here and nowhere else.
    """
    with company_database(client, company) as db:
        events = {identity for identity, command in _rows(db, 'audit_events', ('id', 'command'))
                  if command in commands}
        if not events:
            return dict(events=events, revisions=[], batches=[], lines=[])
        revisions = [row for row in _rows(db, 'transaction_revisions', ('id', 'transaction_id', 'audit_event_id'))
                     if row[2] in events]
        batches = [row for row in _rows(db, 'posting_batches', ('id', 'transaction_id', 'audit_event_id'))
                   if row[2] in events]
        owned = {row[0] for row in batches}
        lines = [row for row in _rows(db, 'posting_lines', ('id', 'batch_id', 'account_id',
                                                            'debit_minor_units', 'credit_minor_units'))
                 if row[1] in owned]
        return dict(events=events, revisions=revisions, batches=batches, lines=lines)


def position(effects, date_to=None, account=None):
    """Signed net per account over declared postings: the combined position, nothing derived.

    `effects` are `(account, date, number, kind, debit, credit)` rows -- the shape a seed arc
    declares its own contribution in.
    """
    net = defaultdict(int)
    for name, day, _number, _kind, debit, credit in effects:
        if date_to is not None and day > date_to:
            continue
        if account is not None and name != account:
            continue
        net[name] += debit - credit
    return dict(net)


def trial_total(balances):
    """What a trial balance prints, from a combined position and never from other totals."""
    return sum(value for value in balances.values() if value > 0)


def gross(effects, date_to=None):
    """Debits and credits per account without netting, for turnover and history claims."""
    totals = defaultdict(lambda: [0, 0])
    for name, day, _number, _kind, debit, credit in effects:
        if date_to is not None and day > date_to:
            continue
        totals[name][0] += debit
        totals[name][1] += credit
    return {name: list(value) for name, value in totals.items()}


def document_numbers(client, company):
    """Every posting document's number, for a manifest to prove it covers the company."""
    with company_database(client, company) as db:
        return {number for number, in _rows(db, 'transactions', ('number',)) if number}


# ---------------------------------------------------------------- the full-company oracle

# One home for what the whole demo company comes to, so a seed addition updates one place
# instead of every package test that happened to have a database open. These are positions, not
# deltas: a trial-balance total is `sum(max(net_per_account, 0))` and adding one scenario's
# total to another's is unsound -- two scenarios each totalling 150 combine to 50 when an
# account crosses sign between them.
# Balances are trial-balance signed nets, debits positive, so a payable is negative here.
DEMO_POSITION = {
    'Checking': 657295,
    'Accounts Receivable': 13839,
    'Sales Tax Payable': -4004,
    'trial_balance': 781754,
    'journal_entries': 14,
    'net_income': 149290,
    'total_equity': 649290,
}

# Every namespace of posting documents the demo seeds, and the arc that owns it. A document
# whose number belongs to no arc fails `test_every_demo_posting_document_belongs_to_a_declared_arc`,
# which is what makes an omitted or unexpected scenario loud rather than silently averaged into
# a total. Add the prefix here in the same change that adds the seed rows.
DEMO_ARCS = {
    'DEMO-SALE-': 'service sales: invoices and receipts, active and voided',
    'DEMO-WORK-': 'nonposting customer work; posts nothing, which is its own test',
    'DEMO-BILL-': 'whole-line work billing',
    'DEMO-PROG-': 'progress billing installments, each voided on its own date',
    'DEMO-PREF-': 'work preference examples',
    'DEMO-TAX-': 'captured sales-tax policy examples',
    'DEMO-PAY-': 'customer payments and settlement',
    'DEMO-BUY-': 'purchasing: order, bill, payment, card, return and count',
    'DEMO-BANK-': "banking the day's takings",
    'DEMO-CREDIT-': 'credit memos and their application',
    'DEMO-OPEN': 'the opening balance the company starts from',
    'DEMO-SERVICE': 'the plain service journals the walkthrough opens with',
    'DEMO-VOID': 'a journal entered and voided, so history shows both',
    'DEMO-FEE': 'a bank fee',
    'DEMO-JPY': 'a foreign-tagged entry posting in home currency',
    'DEMO-COUNT': 'the inventory count adjustment',
    'REG-': 'register-entry examples: split, payment, card, card payment and deposit',
    # The deposit takes the next number in the deposit series rather than a DEMO- prefix, which
    # is the series behaving as designed; naming the bare numbers is how this manifest stays
    # honest about what it has seen rather than quietly widening to match.
    '1': 'deposit number series',
    '2': 'deposit number series',
    '3': 'deposit number series',
}


def undeclared_documents(client, company, arcs):
    """Posting documents whose number no arc claims. Empty is the only passing answer."""
    return sorted(number for number in document_numbers(client, company)
                  if not any(number.startswith(prefix) for prefix in arcs))
