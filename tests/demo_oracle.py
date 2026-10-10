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
from importlib.resources import files
from pathlib import Path
import tomllib

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.storage.engine import open_database

# The day the demo seed is written as of. `demo reset` moves every seed date back by whole months
# to the reset day (R83); reset as of this day, the demo is exactly as written, which is the
# demo every figure below and every date-pinned test was written against.
# A historical source tree (the migration tests seed old files with it, importing these helpers)
# has a seed without a calendar: its demo never moved its dates, so it has no written-as-of day.
DEMO_AS_OF = tomllib.loads(files('bookflow.demo').joinpath('seed.toml').read_text(encoding='utf-8')).get('calendar', {}).get('written_as_of')

# The in-place edits R83 made to seed text that earlier appends had frozen, and nothing else. A
# demo moved into the past accepts an estimate after its expiry, so the acceptance acknowledges
# it; month names that a moved story would contradict became neutral. The exact-prefix witnesses
# compare the old bytes with these edits applied, so any other change to old text still fails.
R83_SEED_EDITS = (
    (b'''reason = "Record the customer's acceptance of alternative B"
input = { "estimate" = "${work_estimate_b.id}", "expected_version" = "${work_estimate_b.version}", "status" = "accepted", "decision_note"''',
     b'''reason = "Record the customer's acceptance of alternative B"
# The customer accepted within the estimate's validity, but the acceptance is entered on the
# reset day, when a demo moved into the past shows the estimate as expired.
input = { "estimate" = "${work_estimate_b.id}", "expected_version" = "${work_estimate_b.version}", "status" = "accepted", "acknowledge_expired" = true, "decision_note"'''),
    (b'"PO-2026-11"', b'"PO-1105"'),
    (b'Year-end count: two valves damaged on site', b'Stock count: two valves damaged on site'),
    (b'# December service-kit restock', b'# Service-kit restock'),
    (b'"December Service Kit"', b'"Service Call Kit"'),
    (b'Service kits for December calls', b'Service kits for service calls'),
    (b'December kits: half kit plus delivery expense', b'Service kits: half kit plus delivery expense'),
    (b'December kits: two bought on the company card', b'Service kits: two bought on the company card'),
    (b'Order ten December service kits; delivery may be partial', b'Order ten service kits; delivery may be partial'),
    (b'"KIT-DEC-4"', b'"KIT-4"'),
)

# The one in-place edit the move-in made (3844af3): the demo's opening journal is no longer a hand-posted
# `journal post` but the one-file move-in (`cutover plan`, `cutover apply`, `cutover tie-out`) that posts the
# same DEMO-OPEN journal. Exact-prefix witnesses apply it to the frozen bytes; every other change still fails.
CUTOVER_SEED_EDITS = (
    ('[[commands]]\ncommand = "journal post"\ncapture = "opening_journal"\ninput = { date = "2026-01-01", number = "DEMO-OPEN", memo = "Opening capital journal", lines = [{ account = "Checking", side = "debit", amount = "5000.00" }, { account = "Opening Balance Equity", side = "credit", amount = "5000.00" }] }\n\n'.encode(),
     '# The company moved in from its old books on the first day of the year: the opening trial balance\n# comes in through the move-in commands, which post it as the DEMO-OPEN journal and tie it out.\n\n[[commands]]\ncommand = "cutover plan"\n\n[commands.input]\nas_of = "2026-01-01"\njournal_number = "DEMO-OPEN"\n\n[[commands.input.files]]\nname = "Opening trial balance.csv"\nkind = "trial_balance"\ncontent = """\n,,"Debit","Credit"\n"1000 · Checking",,"5,000.00",\n"3000 · Opening Balance Equity",,,"5,000.00"\n"TOTAL",,"5,000.00","5,000.00"\n"""\n\n[[commands]]\ncommand = "cutover apply"\nreason = "Opening balances from the old books"\n\n[commands.input]\nas_of = "2026-01-01"\njournal_number = "DEMO-OPEN"\n\n[[commands.input.files]]\nname = "Opening trial balance.csv"\nkind = "trial_balance"\ncontent = """\n,,"Debit","Credit"\n"1000 · Checking",,"5,000.00",\n"3000 · Opening Balance Equity",,,"5,000.00"\n"TOTAL",,"5,000.00","5,000.00"\n"""\n\n[[commands]]\ncommand = "cutover tie-out"\n\n[commands.input]\nas_of = "2026-01-01"\n\n[[commands.input.files]]\nname = "Opening trial balance.csv"\nkind = "trial_balance"\ncontent = """\n,,"Debit","Credit"\n"1000 · Checking",,"5,000.00",\n"3000 · Opening Balance Equity",,,"5,000.00"\n"TOTAL",,"5,000.00","5,000.00"\n"""\n\n'.encode()),
)


def as_edited_by_r83(old, resource):
    """Frozen bytes of a demo resource as they read after R83's deliberate in-place edits.

    Only seed.toml was edited; reference.toml carries parallel text and was not touched.
    """
    if resource != 'seed.toml':
        return old
    for before, after in (*R83_SEED_EDITS, *CUTOVER_SEED_EDITS):
        old = old.replace(before, after)
    return old


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


def posting_documents(client, company):
    """Every posting document as (number, type), keeping multiplicity and kind.

    `document_numbers` returns a set, which answers "was anything unexpected written" and
    cannot answer "is what we expected still here": two invoices collapse to one entry, and a
    journal that became an invoice looks identical. A manifest that claims to cover the company
    needs the count and the kind, not just the names.
    """
    with company_database(client, company) as db:
        return [(number, kind) for number, kind in _rows(db, 'transactions', ('number', 'type'))
                if number]


# ---------------------------------------------------------------- the full-company oracle

# One home for what the whole demo company comes to, so a seed addition updates one place
# instead of every package test that happened to have a database open. These are positions, not
# deltas: a trial-balance total is `sum(max(net_per_account, 0))` and adding one scenario's
# total to another's is unsound -- two scenarios each totalling 150 combine to 50 when an
# account crosses sign between them.
# Balances are trial-balance signed nets, debits positive, so a payable is negative here.
#
# `balances` is every account the trial balance prints, compared as a whole mapping. Three
# accounts used to be named here and the total stood in for everything else, so a seed addition
# could only ever report "the total moved": the service-kit restock below moved four accounts, none
# of which were named, and reading that failure took a full per-document reconciliation where a
# dict diff would have named the account and the amount.
DEMO_POSITION = {
    'balances': {
        'Checking': 653445,                # 657295 - 3850 DEMO-ASK-1
        'Accounts Receivable': 61621,
        'Inventory Asset': 36184,
        'Accounts Payable': -7810,
        'Sales Tax Payable': -7856,         # -7646 - 210 DEMO-STADJ-1
        'Opening Balance Equity': -500000,
        # Early-payment discounts: +500.00 invoiced, 490.00 received plus a 10.00 discount;
        # a 300.00 bill paid with 294.00 plus a 6.00 discount, both through the example bank.
        # DEMO-LINE-KINDS (R147): 456.00 sold less the 15.60 discount = 441.40 income, 36.42
        # tax, 477.82 still owed (it is unpaid, so it is in Accounts Receivable above).
        'Service Income': -309770,         # -215630 - 50000 - 44140
        'Cost of Goods Sold': 523,
        'Professional Fees': 369823,       # 339823 + 30000
        'Business Credit Card': -20000,
        'Discounts Given': 1000,
        'Discounts Taken': -600,
        'Payment Example Bank': -262620,   # -282220 + 49000 - 29400
        'Payment Example Income': -18000,
        # DEMO-ASK-1: 38.50 from checking waits in Ask My Accountant for its account.
        'Uncategorized Expense (Ask My Accountant)': 3850,
        # DEMO-STADJ-1 (R175): a 2.10 penalty added to the State Revenue Office's sales tax due.
        'Other Expense': 210,
    },
    'trial_balance': 1126656,              # 1047664 + 30000 + 1000 + 47782 + 210 (DEMO-STADJ-1)
    'journal_entries': 20,                # + DEMO-ASK-1
    'net_income': -47036,                  # -106716 + 50000 - 1000 - 30000 + 600 + 44140 - 3850 (DEMO-ASK-1) - 210 (DEMO-STADJ-1)
    'total_equity': 452964,                # 393284 + 19600 + 44140 - 3850 (DEMO-ASK-1) - 210 (DEMO-STADJ-1)
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
    'DEMO-KIT-': 'service-kit restock: free sample, and the bill that confirms a cost',
    'REG-': 'register-entry examples: split, payment, card, card payment and deposit',
    'DEMO-1099-': 'a 1099 subcontractor: a bill paid by check and a bill paid on the card',
    'DEMO-DISC-': 'early-payment discounts: a receipt and a bill payment each taking 2%',
    'DEMO-LINE-KINDS': 'a subtotal, a percentage discount and a group item on one invoice',
    'DEMO-ASK-': 'a payment waiting in Uncategorized Expense (Ask My Accountant) for its account',
    'DEMO-STADJ-': 'a sales tax adjustment: a penalty added to what one agency is owed',
    'DEMO-BOUNCE-': 'a returned check: the invoice reopened, the bank fee, and the fee billed to the customer',
    'DEMO-WO-': 'a bad debt written off with a receipt of 0.00 to a Bad Debt expense account',
    # Ten documents take a bare series number rather than a DEMO- prefix, and they are NOT all
    # one series: each document type numbers from 1 independently. `1` is three separate
    # documents -- a deposit, a vendor bill and a journal-family document -- and `2` through `8`
    # are journal-family documents the buying months write without naming. The manifest used to
    # call all of them "the deposit number series" and match them by prefix, which is how `1`
    # also claimed `10` and `123` and how a whole arc could go missing without anything
    # noticing. They are matched exactly now.
    #
    # A document lands here whenever a seed omits `number` *or* names something that is not the
    # ledger document's number: `check post` takes the cheque number, so the service-kit cheque
    # numbered DEMO-KIT-CHECK is journal-family `4`, and an item receipt's own number likewise
    # never reaches the ledger. That is why the service-kit restock shows up mostly as bare
    # numbers, and why only its sales receipt and its bill carry `DEMO-KIT-`.
    '1': 'a deposit, a shipping vendor bill, and the first unnamed journal-family document',
    '2': 'unnamed journal-family document from the buying month',
    '3': 'unnamed journal-family document from the buying month',
    '4': 'service kits paid by cheque: half a kit to stock plus a delivery expense',
    '5': 'service kits bought on the company card',
    '6': 'service kits received against their order, before the vendor bill',
    '7': 'the purchase-price correction the service-kit bill makes to the received cost',
    '8': 'shipping-kit receipt: three units and their allocated shipping',
}

# Arcs that deliberately post nothing. They are declared because they exist and are seeded, and
# exempted from `unseen_arcs` because absence from the posting ledger is precisely their claim --
# `test_work_seeds_post_nothing_and_previews_change_nothing` is what proves it, and demanding a
# posting document here would contradict that test rather than reinforce it.
NONPOSTING_ARCS = frozenset({'DEMO-WORK-'})


def _claims(number, prefix):
    """Whether `prefix` claims `number`, exactly for a bare series number.

    A prefix ending in `-` is a family and matches by prefix. A bare number like `1` is one
    document in the deposit series, and matching it by prefix would also claim `10` and `123` --
    quietly widening the manifest to cover documents nobody declared.
    """
    return number.startswith(prefix) if prefix.endswith('-') else number == prefix


def undeclared_documents(client, company, arcs):
    """Posting documents whose number no arc claims. Empty is the only passing answer."""
    return sorted({number for number, _kind in posting_documents(client, company)
                   if not any(_claims(number, prefix) for prefix in arcs)})


def unseen_arcs(client, company, arcs):
    """Declared arcs that no document matches. Empty is the only passing answer.

    The complement of `undeclared_documents`, and the half that was missing: rejecting unknown
    documents cannot notice a seeded arc that stopped being seeded. An arc that silently stops
    posting takes its money out of the company and leaves every remaining assertion consistent.
    """
    documents = posting_documents(client, company)
    return sorted(prefix for prefix in arcs
                  if prefix not in NONPOSTING_ARCS
                  and not any(_claims(number, prefix) for number, _kind in documents))
