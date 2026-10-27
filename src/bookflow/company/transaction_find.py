"""Find any transaction: one bounded search across every document family the books keep.

The anchor's Edit > Find, simple and advanced in one read. A caller narrows by any mix of
document type, name, number, amount (exact or a range, on the document's total), date range,
memo text and account, and gets back one row per document -- its date, type, number, name,
account, memo, amount and state -- with the id and the read command that opens it.

What is searched, measured from the storage rather than listed by hand:

* every posted business document in ``transactions`` (each ``TRANSACTION_TYPES`` entry),
  with the three markers that say what a journal entry was *entered as*: a check, card
  charge, card credit or transfer (``money_out_documents``), an inventory adjustment
  (``inventory_documents``; a recalculation's recost stays a journal entry) and an item
  receipt (``item_receipts``);
* the non-posting documents: purchase orders, and the customer-work estimates, proposals and
  work orders. A time activity is a timesheet line, not a transaction, as in the anchor.

Voided documents are found and marked void, as the anchor's Find lists them; ``include_voided``
false leaves them out. Deleted documents are not found: the anchor's Find does not show them,
and their retained history opens from the audit trail and the reports that name them.

A name is resolved the way every other read resolves one: by id or by full name, among
customers, vendors, employees and other names. A name that is no party's exact name finds
every party whose name contains it. A customer brings its jobs; a customer or vendor brings
every name merged into it, so a merged-away name's documents are found under its survivor,
and the row names the survivor.

The read is one SQL statement over the union of the families (indexed lookups for the name
and account filters), paged and sorted by date, then number. The account, name and state of
the rows on the page are read for those rows alone. A family the caller may not read is left
out and named in ``withheld_types``, never dropped silently.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company.party_merges import survivor_sql
from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.company.ledger_schema import TRANSACTION_TYPES
from bookflow.core.deletion_families import tombstone_tables
from bookflow.core.errors import BookflowError

# Every family, in the order a person reads the list: customers, vendors, banking, the rest.
# Each is (label, the noun whose `show` opens it).
FAMILIES: dict[str, tuple[str, str]] = {
    'invoice': ('Invoice', 'invoice'),
    'sales_receipt': ('Sales receipt', 'sales-receipt'),
    'credit_memo': ('Credit memo', 'credit-memo'),
    'statement_charge': ('Statement charge', 'statement-charge'),
    'payment': ('Customer payment', 'payment'),
    'customer_refund': ('Customer refund', 'customer-refund'),
    'estimate': ('Estimate', 'estimate'),
    'proposal': ('Proposal', 'proposal'),
    'work_order': ('Work order', 'work-order'),
    'purchase_order': ('Purchase order', 'purchase-order'),
    'item_receipt': ('Item receipt', 'item-receipt'),
    'bill': ('Bill', 'bill'),
    'bill_payment': ('Bill payment', 'bill payment'),
    'vendor_credit': ('Vendor credit', 'vendor-credit'),
    'check': ('Check', 'check'),
    'card_charge': ('Credit card charge', 'card-charge'),
    'card_credit': ('Credit card credit', 'card-credit'),
    'deposit': ('Deposit', 'deposit'),
    'transfer': ('Transfer', 'transfer'),
    'journal_entry': ('Journal entry', 'journal'),
    'sales_tax_payment': ('Sales tax payment', 'sales-tax payment'),
    'sales_tax_adjustment': ('Sales tax adjustment', 'sales-tax adjustment'),
    'inventory_adjustment': ('Inventory adjustment', 'inventory'),
}
TransactionFamily = Literal[tuple(FAMILIES)]
NON_POSTING = ('purchase_order', 'estimate', 'proposal', 'work_order')
PARTY_KINDS = ('customer', 'vendor', 'employee', 'other_name')
# The register accounts, in the order a reader looks for the one a document is "in".
_RANK = {'bank': 0, 'credit_card': 1, 'accounts_receivable': 2, 'accounts_payable': 3}
# A document that something else settles, with the control account it is owed on.
_SETTLED = {'invoice': 'accounts_receivable', 'statement_charge': 'accounts_receivable', 'bill': 'accounts_payable'}
# What a stored state reads as. A posted document that nothing settles is simply posted.
_OPEN_STATES = {'draft', 'open', 'partly_received', 'scheduled', 'in_progress', 'on_hold'}


class TransactionFindInput(StrictModel):
    types: list[TransactionFamily] | None = Field(default=None, max_length=len(FAMILIES),
        description='Document types to search; omit for every type. ' + ', '.join(FAMILIES) + '.')
    name: str | None = Field(default=None, min_length=1, max_length=200,
        description='Customer, job, vendor, employee or other name, by id or full name; a name that is no '
                    "party's exact name finds every party whose name contains it. A customer includes its jobs, "
                    'and a merged-away name is found under the name it was merged into.')
    name_type: Literal['customer', 'vendor', 'employee', 'other_name'] | None = Field(default=None,
        description='Which list `name` is on, when the same name is on two.')
    number: str | None = Field(default=None, min_length=1, max_length=64,
        description='Document or check number, exact (letter case ignored).')
    amount: str | None = Field(default=None, max_length=40,
        description='Exact document total, a decimal string such as "412.50".')
    amount_from: str | None = Field(default=None, max_length=40, description='Smallest document total, inclusive.')
    amount_to: str | None = Field(default=None, max_length=40, description='Largest document total, inclusive.')
    date_from: str | None = Field(default=None, min_length=10, max_length=10,
        description='Inclusive first document date, YYYY-MM-DD.')
    date_to: str | None = Field(default=None, min_length=10, max_length=10,
        description='Inclusive last document date, YYYY-MM-DD.')
    memo: str | None = Field(default=None, min_length=1, max_length=200,
        description="Text the memo contains (or a line's description, or an estimate's title); letter case ignored.")
    account: str | None = Field(default=None, min_length=1, max_length=1000,
        description='Account ID or full name the document posts to; purchase orders, estimates, proposals and '
                    'work orders post nothing and are left out when this is given.')
    include_voided: bool = Field(default=True, description='false leaves voided documents out.')
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)

    _dates = field_validator('date_from', 'date_to')(lambda value: value if value is None else iso_date(value))

    @model_validator(mode='after')
    def ordered(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from must be on or before date_to')
        if self.amount is not None and (self.amount_from is not None or self.amount_to is not None):
            raise ValueError('give amount, or amount_from and amount_to, not both')
        return self


class FoundName(StrictModel):
    name_type: Literal['customer', 'vendor', 'employee', 'other_name']
    id: str
    name: str


class FoundTransaction(StrictModel):
    id: str = Field(description='The document id its show command opens.')
    transaction_id: str | None = Field(description='The ledger document id; null for a document that posts nothing.')
    type: TransactionFamily
    type_label: str
    show_command: str = Field(description='The read that opens this document, given `id`.')
    date: str
    number: str | None = Field(description='The document or check number; null for a bank payment, a check with no '
                               'cheque behind it, and for a check still waiting to be printed.')
    name: str | None
    name_type: Literal['customer', 'vendor', 'employee', 'other_name'] | None
    name_id: str | None
    account_id: str | None
    account: str | None
    memo: str | None
    amount: MoneyOutput
    status: Literal['open', 'paid', 'posted', 'closed', 'void'] = Field(
        description='open or paid for an invoice, statement charge or bill by what is still owed on it; open or '
                    'closed for a purchase order, estimate, proposal or work order; void when voided; posted otherwise.')
    state: str = Field(description='The stored state the status is read from.')


class TransactionFindOutput(StrictModel):
    count: int = Field(ge=0, le=200)
    matching_total: int = Field(ge=0)
    next_cursor: str | None
    items: list[FoundTransaction]
    names: list[FoundName] | None = Field(description='The names `name` matched, when given.')
    withheld_types: list[TransactionFamily] = Field(
        description='Types left out because this caller may not read them; empty when nothing was.')
    note: str | None


def _party_sql(type_col: str, id_col: str) -> str:
    return '(' + ' OR '.join(f"({type_col}='{kind}' AND {id_col} IN (SELECT value FROM json_each(:p_{kind})))"
                             for kind in PARTY_KINDS) + ')'


_TOMBSTONES = ' '.join(f'AND t.id NOT IN (SELECT transaction_id FROM {table})' for table in tombstone_tables())
_TYPES = '(:types IS NULL OR family IN (SELECT value FROM json_each(:types)))'
_COMMON = ("(:date_from IS NULL OR date>=:date_from) AND (:date_to IS NULL OR date<=:date_to) "
           "AND (:amount_from IS NULL OR amount>=:amount_from) AND (:amount_to IS NULL OR amount<=:amount_to) "
           "AND (:include_voided OR state<>'voided')")

_POSTED = f"""
SELECT * FROM (
 SELECT CASE WHEN m.kind IS NOT NULL THEN m.kind WHEN i.kind='adjustment' THEN 'inventory_adjustment'
             WHEN ir.id IS NOT NULL THEN 'item_receipt' ELSE t.type END AS family,
        coalesce(ir.id, t.id) AS id, t.id AS tx, r.id AS rev, r.date AS date,
        CASE WHEN ci.check_number IS NOT NULL THEN ci.check_number WHEN m.kind='check' THEN NULL
             ELSE r.number END AS number, r.number AS stored_number, ci.check_number_key AS check_key,
        r.memo AS memo, r.total_minor_units AS amount, t.status AS state, r.name_type AS name_type, r.name_id AS name_id
 FROM transactions t JOIN transaction_revisions r ON r.id=t.current_revision_id
 LEFT JOIN money_out_documents m ON m.transaction_id=t.id
 LEFT JOIN inventory_documents i ON i.transaction_id=t.id
 LEFT JOIN item_receipts ir ON ir.transaction_id=t.id
 LEFT JOIN check_instruments ci ON ci.transaction_id=t.id
 WHERE 1=1 {_TOMBSTONES}
   AND (:account IS NULL OR t.id IN (SELECT transaction_id FROM posting_lines WHERE account_id IN (
        SELECT :account UNION ALL SELECT merged_id FROM list_merges
        WHERE list_kind='account' AND survivor_id=:account AND undone_at IS NULL)))
   AND (:named=0 OR {_party_sql('r.name_type', 'r.name_id')}
        OR t.id IN (SELECT transaction_id FROM posting_lines WHERE {_party_sql('name_type', 'name_id')}))
   AND (:number IS NULL OR (lower(r.number)=:number AND coalesce(m.kind,'')<>'check') OR lower(ci.check_number)=:number)
   AND (:memo IS NULL OR instr(lower(coalesce(r.memo,'')), :memo)>0
        OR EXISTS (SELECT 1 FROM document_lines dl WHERE dl.revision_id=r.id AND instr(lower(dl.description), :memo)>0))
) WHERE {_TYPES} AND {_COMMON}
"""

_ORDERS = f"""
SELECT * FROM (
 SELECT 'purchase_order' AS family, po.id AS id, NULL AS tx, NULL AS rev, pr.date AS date, pr.number AS number,
        pr.memo AS memo, pr.total_minor_units AS amount, po.status AS state, 'vendor' AS name_type, pr.vendor_id AS name_id
 FROM purchase_orders po JOIN purchase_order_revisions pr ON pr.id=po.current_revision_id
 WHERE :account IS NULL AND (:named=0 OR {_party_sql("'vendor'", 'pr.vendor_id')})
   AND (:number IS NULL OR lower(pr.number)=:number)
   AND (:memo IS NULL OR instr(lower(coalesce(pr.memo,'')), :memo)>0)
 UNION ALL
 SELECT wd.kind AS family, wd.id AS id, NULL AS tx, NULL AS rev, wr.date AS date, wr.number AS number,
        wr.title AS memo, wr.gross_minor_units AS amount, wd.status AS state, 'customer' AS name_type, wr.customer_id AS name_id
 FROM work_documents wd JOIN work_revisions wr ON wr.id=wd.current_revision_id
 WHERE wd.kind IN ('estimate','proposal','work_order') AND :account IS NULL
   AND (:named=0 OR {_party_sql("'customer'", 'wr.customer_id')})
   AND (:number IS NULL OR lower(wr.number)=:number)
   AND (:memo IS NULL OR instr(lower(wr.title), :memo)>0)
) WHERE {_TYPES} AND {_COMMON}
"""

_FOUND = f"""
SELECT family, id, tx, rev, date, number, memo, amount, state, name_type, name_id, count(*) OVER () AS total
FROM (SELECT family, id, tx, rev, date, number, memo, amount, state, name_type, name_id FROM ({_POSTED})
      UNION ALL SELECT family, id, tx, rev, date, number, memo, amount, state, name_type, name_id FROM ({_ORDERS}))
ORDER BY date, number, id LIMIT :limit OFFSET :offset
"""


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _minor(value: str | None, field: str, currency: str) -> int | None:
    if value is None:
        return None
    from bookflow.core.money import Money
    text = value.strip().replace('$', '').replace(',', '')
    try:
        parsed = Money.parse(text, currency)
    except BookflowError:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': field, 'problem': 'expected a decimal amount such as "412.50"'}]}) from None
    if parsed.currency != currency:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': field, 'problem': f'amounts are in the home currency, {currency}'}]})
    return abs(parsed.minor_units)


_PARTY_TABLES = {'customer': ('customers', 'full_name'), 'vendor': ('vendors', 'name'),
                 'employee': ('employees', 'name'), 'other_name': ('other_names', 'name')}
_PARTY_NOUNS = {'customer': 'customer', 'vendor': 'vendor', 'employee': 'employee', 'other_name': 'other-name'}


def _names(s, selector: str, kind: str | None) -> list[dict]:
    """The parties `name` names: an exact one, else every one whose name contains it."""
    from bookflow.company.parties import resolve_party
    kinds = (kind,) if kind else PARTY_KINDS
    exact, refusals = [], []
    for each in kinds:
        try:
            row = resolve_party(s.company, _PARTY_NOUNS[each], selector)
        except BookflowError as error:
            if error.code != 'E_RECORD_NOT_FOUND':
                raise
            refusals.append(error)
            continue
        exact.append(dict(name_type=each, id=row['id']))
    if len(exact) > 1:
        raise BookflowError('E_VALIDATION', message=f'{selector!r} is on more than one list ('
                            + ', '.join(found['name_type'] for found in exact) + '); pass name_type to say which.',
                            details={'fields': [{'field': 'name_type', 'problem': 'the name is on more than one list'}]})
    if exact:
        return exact
    needle = selector.strip().lower()
    found = []
    for each in kinds:
        table, column = _PARTY_TABLES[each]
        found += [dict(name_type=each, id=row[0]) for row in s.company.raw.execute(
            f'SELECT id FROM {table} WHERE instr(lower({column}), ?)>0 ORDER BY {column}, id', (needle,))]
    if not found:
        error = refusals[0]
        error.message = (f'No customer, job, vendor, employee or other name matches {selector.strip()!r}, '
                         'exactly or in part.')
        raise error
    return found


def _family(raw, parties: list[dict]) -> dict[str, list[str]]:
    """Every party id a search by these parties reaches: jobs and merged-away names included."""
    from bookflow.company import party_merges
    reached: dict[str, set[str]] = {kind: set() for kind in PARTY_KINDS}
    for party in parties:
        kind, party_id = party['name_type'], party['id']
        if kind in ('customer', 'vendor'):
            party_id = raw.execute(f"SELECT {party_merges.survivor_sql(kind, ':p')}", {'p': party_id}).fetchone()[0]
        if kind == 'customer':
            reached[kind] |= {row[0] for row in raw.execute(
                f"WITH RECURSIVE {party_merges.family_cte('fam', ':p')} SELECT id FROM fam", {'p': party_id})}
        elif kind == 'vendor':
            reached[kind] |= {party_id} | {row[0] for row in raw.execute(
                party_merges.merged_sql('vendor') + ' AND survivor_id=:p', {'p': party_id})}
        else:
            reached[kind].add(party_id)
    return {kind: sorted(ids) for kind, ids in reached.items()}


def _labels(raw, pairs) -> dict[tuple[str, str], tuple[str, str, str]]:
    """(kind, id) -> (kind, survivor id, survivor's current name)."""
    from bookflow.company import party_merges
    out = {}
    for kind, party_id in pairs:
        survivor = party_id
        if kind in ('customer', 'vendor'):
            survivor = raw.execute(f"SELECT {party_merges.survivor_sql(kind, ':p')}", {'p': party_id}).fetchone()[0]
        table, column = _PARTY_TABLES[kind]
        row = raw.execute(f'SELECT {column} FROM {table} WHERE id=?', (survivor,)).fetchone()
        if row is not None:
            out[(kind, party_id)] = (kind, survivor, row[0])
    return out


def _withheld(s) -> list[str]:
    """The families whose own read this caller may not run."""
    from bookflow.core import registry
    from bookflow.hub.access import require_resource
    answers: dict[tuple, bool] = {}
    withheld = []
    for family, (_label, noun) in FAMILIES.items():
        show = registry.get(f'{noun} show')
        key = (show.capability, show.required_role)
        if key not in answers:
            try:
                require_resource(s, *key)
                answers[key] = True
            except BookflowError as error:
                if error.code != 'E_PERMISSION':
                    raise
                answers[key] = False
        if not answers[key]:
            withheld.append(family)
    return withheld


def _state(raw, inp, principal_id, s):
    from bookflow.company import query_freshness as fresh
    from bookflow.company.query import permission_fingerprint
    key = fresh.cursor_key(s.company)
    contract = hashlib.sha256(json.dumps(['transaction-find', inp.model_dump(exclude={'cursor'})],
                                         sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    sequence = raw.execute('SELECT coalesce(max(seq),0) FROM audit_events').fetchone()[0]
    proof = fresh.QueryProof(company=str(s.company_row['id']), contract=contract,
                             audience=permission_fingerprint(s, principal_id),
                             facts=fresh.keyed(key, fresh.FACTS, ['transaction-find', sequence]))
    offset = 0
    if inp.cursor is not None:
        previous = fresh.decode_cursor(key, inp.cursor)
        fresh.check_proof(previous.proof, proof)
        offset = previous.offset
    return key, proof, offset


def find(inp: TransactionFindInput, s, *, principal_id=None) -> TransactionFindOutput:
    from bookflow.company import query_freshness as fresh
    with ledger._snapshot(s.company):
        raw = s.company.raw
        currency = raw.execute('SELECT home_currency FROM company_info').fetchone()[0]
        withheld = _withheld(s)
        types = list(inp.types) if inp.types else list(FAMILIES)
        searched = [family for family in dict.fromkeys(types) if family not in withheld]
        parties = _names(s, inp.name, inp.name_type) if inp.name else None
        reached = _family(raw, parties) if parties else {kind: [] for kind in PARTY_KINDS}
        account_id = None
        if inp.account:
            from bookflow.company.accounts import resolve_account
            from bookflow.company.list_merges import survivor_id
            # A merged account's name finds what it posted, under its survivor (R117).
            account_id = survivor_id(s.company, 'account', resolve_account(s.company, inp.account)['id'])
        exact = _minor(inp.amount, 'amount', currency)
        params = dict(
            types=json.dumps(searched), account=account_id, named=1 if parties else 0,
            number=inp.number.strip().lower() if inp.number else None,
            memo=inp.memo.strip().lower() if inp.memo else None,
            date_from=inp.date_from, date_to=inp.date_to,
            amount_from=exact if exact is not None else _minor(inp.amount_from, 'amount_from', currency),
            amount_to=exact if exact is not None else _minor(inp.amount_to, 'amount_to', currency),
            include_voided=1 if inp.include_voided else 0, limit=inp.limit,
            **{f'p_{kind}': json.dumps(ids) for kind, ids in reached.items()})
        key, proof, offset = _state(raw, inp, principal_id, s)
        params['offset'] = offset
        rows = _rows(raw.execute(_FOUND, params)) if searched else []
        total = rows[0]['total'] if rows else (0 if offset == 0 else None)
        if total is None:  # a cursor past the end: count what there is
            total = len(_rows(raw.execute(_FOUND, {**params, 'limit': -1, 'offset': 0})))
        items = _detail(raw, rows, currency)
        names = None
        if parties:
            labels = _labels(raw, [(p['name_type'], p['id']) for p in parties])
            names = [FoundName(name_type=p['name_type'], id=p['id'], name=labels[(p['name_type'], p['id'])][2])
                     for p in parties if (p['name_type'], p['id']) in labels]
        hidden = [family for family in dict.fromkeys(types) if family in withheld]
        note = None
        if hidden:
            note = ('Not searched, because you may not read them: '
                    + ', '.join(FAMILIES[family][0].lower() + 's' for family in hidden) + '.')
        return TransactionFindOutput(
            count=len(items), matching_total=total, items=items, names=names, withheld_types=hidden, note=note,
            next_cursor=fresh.continuation(key, proof, offset, len(items), total) if items else None)


def _detail(raw, rows, currency) -> list[FoundTransaction]:
    """Name, account and state for the rows on this page, read for those rows alone."""
    revisions = [row['rev'] for row in rows if row['rev']]
    lines: dict[str, list[dict]] = {}
    if revisions:
        for line in _rows(raw.execute("""
            SELECT b.revision_id AS rev, l.line_no, a.id AS account_id, a.type AS account_type, a.full_name, a.name, a.number,
                   l.name_type, l.name_id
            FROM posting_batches b JOIN posting_lines l ON l.batch_id=b.id
            JOIN accounts a ON a.id=""" + survivor_sql('account', 'l.account_id') + """
            WHERE b.revision_id IN (SELECT value FROM json_each(:revs)) AND b.kind IN ('original','replacement')
            ORDER BY b.revision_id, l.line_no""", {'revs': json.dumps(revisions)})):
            lines.setdefault(line['rev'], []).append(line)
    numbers, lowest = raw.execute('SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info').fetchone()
    pairs = set()
    for row in rows:
        if row['name_id'] is None and row['rev']:
            named = next((line for line in lines.get(row['rev'], []) if line['name_id']), None)
            if named:
                row['name_type'], row['name_id'] = named['name_type'], named['name_id']
        if row['name_id']:
            pairs.add((row['name_type'], row['name_id']))
    labels = _labels(raw, sorted(pairs))
    owed = _owed(raw, [row for row in rows if row['family'] in _SETTLED and row['state'] == 'posted'])
    from bookflow.company import check_numbers
    pending = [row['tx'] for row in rows if row['family'] == 'check' and row['number'] is None]
    # A check with no number that is not waiting to be printed is a bank payment (R183).
    unnumbered = {tx for (tx,) in raw.execute(
        'SELECT value FROM json_each(?) WHERE value NOT IN (SELECT transaction_id FROM check_print_marks)',
        (json.dumps(pending),))} if pending else set()
    items = []
    for row in rows:
        family = row['family']
        label, noun = FAMILIES[family]
        if row['tx'] in unnumbered:
            label = check_numbers.BANK_PAYMENT
        account = None
        if lines.get(row['rev']):
            account = min(lines[row['rev']], key=lambda line: (_RANK.get(line['account_type'], 9), line['line_no']))
        party = labels.get((row['name_type'], row['name_id'])) if row['name_id'] else None
        state = row['state']
        if state == 'voided':
            status = 'void'
        elif family in _SETTLED:
            status = 'open' if owed.get(row['tx'], 0) > 0 else 'paid'
        elif family in NON_POSTING:
            status = 'open' if state in _OPEN_STATES else 'closed'
        else:
            status = 'posted'
        items.append(FoundTransaction(
            id=row['id'], transaction_id=row['tx'], type=family, type_label=label, show_command=f'{noun} show',
            date=row['date'], number=row['number'],
            name=party[2] if party else None, name_type=party[0] if party else None, name_id=party[1] if party else None,
            account_id=account['account_id'] if account else None,
            account=ledger._account_display(account['full_name'], account['name'], account['number'], numbers, lowest)
            if account else None,
            memo=row['memo'], amount=money(int(row['amount']), currency), status=status, state=state))
    return items


def _owed(raw, rows) -> dict[str, int]:
    """What is still owed on each settled document: its control-account lines less what settles it."""
    if not rows:
        return {}
    ids = json.dumps([row['tx'] for row in rows])
    owed = {tx: int(value) for tx, value in raw.execute("""
        SELECT l.transaction_id, sum(CASE WHEN a.type='accounts_receivable' THEN l.debit_minor_units-l.credit_minor_units
                                          ELSE l.credit_minor_units-l.debit_minor_units END)
        FROM posting_lines l JOIN accounts a ON a.id=l.account_id
        WHERE l.transaction_id IN (SELECT value FROM json_each(:ids))
          AND a.type IN ('accounts_receivable','accounts_payable')
        GROUP BY l.transaction_id""", {'ids': ids})}
    for tx, settled in raw.execute("""
        SELECT paid_transaction_id, sum(CASE WHEN kind='apply' THEN amount_minor_units ELSE -amount_minor_units END)
        FROM applications WHERE paid_transaction_id IN (SELECT value FROM json_each(:ids)) GROUP BY 1
        UNION ALL
        SELECT obligation_transaction_id, sum(CASE WHEN kind='apply' THEN amount_minor_units ELSE -amount_minor_units END)
        FROM ap_applications WHERE obligation_transaction_id IN (SELECT value FROM json_each(:ids)) GROUP BY 1""",
            {'ids': ids}):
        owed[tx] = owed.get(tx, 0) - int(settled)
    return owed


def uncovered_types() -> set[str]:
    """Stored transaction types no family reads; a new one must be placed here before it ships."""
    return set(TRANSACTION_TYPES) - set(FAMILIES)
