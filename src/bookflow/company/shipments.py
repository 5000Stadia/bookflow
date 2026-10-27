"""Shipments and the books (R181 increment 1; design/pick-pack-ship.md §2, §3, §6).

A **shipment** issues an order's units on its ship date, as a stock document of its own: one ``issue`` movement
per line through ``inventory_effects.plan`` (places, numbers and count position as any stock document), posted in a
journal entry the inventory ledger owns -- Dr Shipped Not Invoiced, Cr Inventory Asset at the replay's cost. The
issue's offset account *is* Shipped Not Invoiced, so every later cost change of the issue (a backdated purchase, a
provisional cost trued up, a landed cost) posts against it by the existing machinery.

**The invoice moves no stock.** An invoice line billing shipped units (``shipment_billings``) is skipped by the
sale's stock entries. Its cost moves to cost of goods sold by one **derived balance**, per shipment line:

    waiting(d) = cost(d) - endpoint_share(cost(d), quantity, 0, billed(d))

where ``cost(d)`` is everything the inventory machinery posted against Shipped Not Invoiced for the line dated by
``d`` and ``billed(d)`` the quantity live invoices dated by ``d`` billed. ``settle`` posts, at each date, the
difference between what that says cost of goods sold holds and what ``shipment_recognitions`` already moved -- so an
invoice, its void, its date change, a recost before or after it, each leaves the account exactly where the
definition says. It runs after every company write that posted anything (``settle_in_command``, from dispatch).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction

import sqlalchemy as sa

from bookflow.company import accounts as account_service, inventory, inventory_effects, journals, placements
from bookflow.company import schema as c
from bookflow.company.journal_models import JournalLineInput, JournalPostInput, JournalUpdateInput, JournalVoidInput
from bookflow.company.shipment_models import (
    ShipmentBillingOutput, ShipmentHistoryOutput, ShipmentLineOutput, ShipmentListOutput, ShipmentListRow,
    ShipmentOutput, ShippedNotInvoicedOutput, ShippedNotInvoicedRow,
)
from bookflow.company.shipment_schema import ROLE, SEQUENCE
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import endpoint_share, format_quantity_micro_units, parse_quantity_micro_units, round_ratio_half_even
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

ACCOUNT_NAME = 'Shipped Not Invoiced'
KIND, COST_KIND = 'shipment', 'shipment_cost'
NANO = 1_000_000_000
_PLACEHOLDER = 'SHIPPED-NOT-INVOICED-PREVIEW'


def _invalid(field_name, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message,
                         details={'fields': [{'field': field_name, 'problem': problem}], **details})


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _money(minor, currency):
    return Money(int(minor), currency).to_dict()


def _plain(micro):
    return format_quantity_micro_units(int(micro))


def _to_base(selected, factor):
    """A selected-unit quantity (microunits, exact) in the item's base unit."""
    value = Fraction(selected) * int(factor)
    return round_ratio_half_even(value.numerator, value.denominator * NANO)


def _to_selected(base, factor):
    return Fraction(int(base) * NANO, int(factor))


# ---------------------------------------------------------------- the account


def find_account(db):
    row = db.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == ROLE)).mappings().first()
    return dict(row) if row is not None else None


def ensure_account(s, ctx):
    """Shipped Not Invoiced, made on the first shipment; returns (account, touched)."""
    account = find_account(s.company)
    if account is not None:
        return account, []
    from bookflow.company.lists import normalize_display_name
    name = ACCOUNT_NAME
    for candidate in [ACCOUNT_NAME] + [f'{ACCOUNT_NAME} {n}' for n in range(2, 50)]:
        _, key = normalize_display_name(candidate)
        if s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.full_name_key == key)).first() is None:
            name = candidate
            break
    mutation = account_service.plan_account_create(
        s.company, {'name': name, 'type': 'other_current_asset',
                    'description': 'The cost of goods shipped to customers and not yet invoiced'},
        actor_id=s.actor.id, via=ctx.interface.value, at=clock.now_iso())
    mutation = account_service.AccountMutation(mutation.action, mutation.before, dict(mutation.after, system_role=ROLE))
    account_service.persist_account_mutation(s.company, mutation)
    account = find_account(s.company)
    touched = [Touched('account', account['id'], 'create', None, account['version'], dict(account), None, db='company')]
    audit.write_event_to(s.company, ctx, 'shipment post', f'create account {account["full_name"]}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    return account, touched


def has_shipments(s):
    return s.company is not None and s.company.conn.execute(sa.select(c.shipments.c.id).limit(1)).first() is not None


# ---------------------------------------------------------------- reads


_CURRENT = """
    SELECT r.* FROM shipment_revisions r
    WHERE r.revision_number = (SELECT max(x.revision_number) FROM shipment_revisions x WHERE x.shipment_id = r.shipment_id)
"""


def _rows(s, sql, params=None):
    return [dict(row) for row in s.company.conn.execute(sa.text(sql), params or {}).mappings()]


def _marks(prefix, values, params):
    names = []
    for index, value in enumerate(values):
        params[f'{prefix}{index}'] = value
        names.append(f':{prefix}{index}')
    return ', '.join(names) or 'NULL'


def resolve(s, selector):
    t = c.shipments
    text = str(selector).strip()
    row = None
    if is_ulid(text):
        row = s.company.conn.execute(sa.select(t).where(t.c.id == text.upper())).mappings().first()
    if row is None:
        row = s.company.conn.execute(sa.select(t).where(t.c.number == text)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'shipment', 'selector': text})
    header = dict(row)
    header['revision'] = _rows(s, _CURRENT + ' AND r.shipment_id = :id', {'id': header['id']})[0]
    return header


def _revision_lines(s, revision_id):
    return [dict(row) for row in s.company.conn.execute(sa.select(c.shipment_lines).where(
        c.shipment_lines.c.revision_id == revision_id).order_by(c.shipment_lines.c.position)).mappings()]


def _history(s, shipment_id):
    return [dict(row) for row in s.company.conn.execute(sa.select(c.shipment_revisions).where(
        c.shipment_revisions.c.shipment_id == shipment_id).order_by(c.shipment_revisions.c.revision_number)).mappings()]


def shipped_lines(s, *, roots=None, shipment_ids=None, order_ids=None):
    """Lines of every shipment whose current revision is shipped, oldest ship date first."""
    if not has_shipments(s):
        return []
    params = {}
    where = ["cur.status = 'shipped'"]
    if shipment_ids is not None:
        where.append(f"l.shipment_id IN ({_marks('sh', list(shipment_ids), params)})")
    if order_ids is not None:
        where.append(f"h.sales_order_id IN ({_marks('so', list(order_ids), params)})")
    if roots is not None:
        docs = sorted({root[0] for root in roots})
        where.append(f"l.root_document_id IN ({_marks('rd', docs, params)})")
    rows = _rows(s, f"""
        SELECT l.*, cur.ship_date, cur.site_id, cur.count_position, h.number, h.sales_order_id, h.customer_id
        FROM ({_CURRENT}) cur
        JOIN shipment_lines l ON l.revision_id = cur.id
        JOIN shipments h ON h.id = cur.shipment_id
        WHERE {' AND '.join(where)}
        ORDER BY cur.ship_date, h.number, l.position""", params)
    if roots is not None:
        wanted = set(roots)
        rows = [row for row in rows if (row['root_document_id'], row['root_line_id']) in wanted]
    return rows


def live_billings(s, identities=None, *, transaction_ids=None):
    """Billings whose invoice is posted and whose current revision still carries the billing line."""
    if not has_shipments(s):
        return []
    params = {}
    where = ["t.status = 'posted'"]
    if identities is not None:
        where.append(f"b.shipment_line_identity IN ({_marks('id', list(identities), params)})")
    if transaction_ids is not None:
        where.append(f"b.transaction_id IN ({_marks('tx', list(transaction_ids), params)})")
    return _rows(s, f"""
        SELECT b.*, r.date AS invoice_date, t.number AS invoice_number
        FROM shipment_billings b
        JOIN transactions t ON t.id = b.transaction_id
        JOIN transaction_revisions r ON r.id = t.current_revision_id
        WHERE {' AND '.join(where)}
          AND EXISTS (SELECT 1 FROM document_lines d WHERE d.revision_id = r.id AND d.line_id = b.line_id)
        ORDER BY r.date, b.created_at, b.id""", params)


def billed_line_ids(s, transaction_id):
    """``{invoice line identity: base quantity it bills}`` for one invoice's stored billings, live or not."""
    if not has_shipments(s) or transaction_id is None:
        return {}
    out = {}
    for row in s.company.conn.execute(sa.select(c.shipment_billings).where(
            c.shipment_billings.c.transaction_id == transaction_id)).mappings():
        out[row['line_id']] = out.get(row['line_id'], 0) + int(row['quantity_microunits'])
    return out


def live_returns(s, identities=None):
    """Lines of shipment returns not voided (R181 increment 2), oldest first: identity, date, quantity, movement."""
    if not has_shipments(s):
        return []
    params = {}
    where = ["cur.status = 'returned'"]
    if identities is not None:
        where.append(f"l.shipment_line_identity IN ({_marks('id', list(identities), params)})")
    return _rows(s, f"""
        SELECT l.*, h.return_date, h.number, h.reopen, h.shipment_id, h.transaction_id
        FROM shipment_return_lines l JOIN shipment_returns h ON h.id = l.return_id
        JOIN shipment_return_revisions cur ON cur.return_id = h.id AND cur.revision_number = (
            SELECT max(x.revision_number) FROM shipment_return_revisions x WHERE x.return_id = h.id)
        WHERE {' AND '.join(where)} ORDER BY h.return_date, h.number, l.position""", params)


def returned_by_identity(s, identities=None):
    out = {}
    for row in live_returns(s, identities):
        out[row['shipment_line_identity']] = out.get(row['shipment_line_identity'], 0) + int(row['quantity_microunits'])
    return out


@dataclass
class Part:
    """Shipped units of one shipment line, what invoices billed and what came back on shipment returns."""
    shipment_id: str
    number: str
    ship_date: str
    identity: str
    item_id: str
    shipped: int
    billed: int
    returned: int = 0

    @property
    def out(self):
        """Shipped and not brought back on a shipment return."""
        return self.shipped - self.returned

    @property
    def waiting(self):
        return self.shipped - self.billed - self.returned


def waiting_parts(s, roots=None, *, order_ids=None, exclude_shipment=None):
    """``{root: [Part, ...]}`` oldest shipment first, for every order line with shipped units."""
    lines = [row for row in shipped_lines(s, roots=roots, order_ids=order_ids) if row['shipment_id'] != exclude_shipment]
    if not lines:
        return {}
    billed = {}
    identities = [line['line_identity'] for line in lines]
    for row in live_billings(s, identities):
        billed[row['shipment_line_identity']] = billed.get(row['shipment_line_identity'], 0) + int(row['quantity_microunits'])
    returned = returned_by_identity(s, identities)
    out = {}
    for line in lines:
        out.setdefault((line['root_document_id'], line['root_line_id']), []).append(Part(
            line['shipment_id'], line['number'], line['ship_date'], line['line_identity'], line['item_id'],
            int(line['quantity_microunits']), billed.get(line['line_identity'], 0),
            returned.get(line['line_identity'], 0)))
    return out


def waiting_base_by_root(s, roots=None):
    """``{root: base microunits shipped and not invoiced}`` -- what commitment leaves out."""
    return {root: sum(part.waiting for part in parts) for root, parts in waiting_parts(s, roots).items()}


def order_line_figures(s, roots):
    """``{root: (shipped, waiting)}`` in base microunits, for an order's figures; shipped is net of shipment returns."""
    out = {}
    for root, parts in waiting_parts(s, roots).items():
        out[root] = (sum(p.out for p in parts), sum(p.waiting for p in parts))
    return out


# ---------------------------------------------------------------- the derived balance


def _families(s, identities):
    """``{identity: [movement rows]}``: every movement posted against Shipped Not Invoiced for each line."""
    identities = list(dict.fromkeys(identities))
    if not identities:
        return {}
    params = {}
    marks = _marks('id', identities, params)
    owners = _rows(s, f"SELECT line_identity, movement_id, 0 AS back FROM shipment_lines WHERE movement_id IS NOT NULL "
                      f"AND line_identity IN ({marks}) UNION SELECT shipment_line_identity, movement_id, 1 "
                      f"FROM shipment_return_lines WHERE shipment_line_identity IN ({marks})", params)
    back = {row['movement_id'] for row in owners if row['back']}
    # A shipment line's issue and every shipment return giving part of it back: all post against Shipped Not
    # Invoiced, so the line's cost there is net of what its returns took back (design §3).
    owner = {row['movement_id']: row['line_identity'] for row in owners}
    if not owner:
        return {}
    params = {}
    marks = _marks('m', list(owner), params)
    rows = _rows(s, f"""SELECT id, kind, value_minor_units, effective_date, reverses_movement_id, corrects_movement_id
        FROM inventory_movements WHERE id IN ({marks}) OR reverses_movement_id IN ({marks})
        OR corrects_movement_id IN ({marks})""", params)
    out = {}
    for row in rows:
        key = owner.get(row['id']) or owner.get(row['reverses_movement_id']) or owner.get(row['corrects_movement_id'])
        row['back'] = bool({row['id'], row['reverses_movement_id'], row['corrects_movement_id']} & back)
        out.setdefault(key, []).append(row)
    return out


def _posted_recognitions(s, identities):
    params = {}
    out = {}
    for row in _rows(s, f"SELECT shipment_line_identity, effective_date, amount_minor_units FROM shipment_recognitions "
                        f"WHERE shipment_line_identity IN ({_marks('id', list(identities), params)})", params):
        out.setdefault(row['shipment_line_identity'], []).append((row['effective_date'], int(row['amount_minor_units'])))
    return out


def _current_quantities(s, identities):
    """``{identity: (quantity, line row)}`` of each line on its shipment's current revision (0 when voided/removed)."""
    params = {}
    rows = _rows(s, f"""SELECT l.*, cur.status, h.customer_id, h.number
        FROM shipment_lines l JOIN ({_CURRENT}) cur ON cur.id = l.revision_id
        JOIN shipments h ON h.id = l.shipment_id
        WHERE l.line_identity IN ({_marks('id', list(identities), params)})""", params)
    out = {row['line_identity']: (int(row['quantity_microunits']) if row['status'] == 'shipped' else 0, row) for row in rows}
    if len(out) < len(set(identities)):
        # A line a later revision dropped: its newest row still names its accounts and customer.
        params = {}
        for row in _rows(s, f"""SELECT l.*, h.customer_id, h.number FROM shipment_lines l JOIN shipments h ON h.id = l.shipment_id
                WHERE l.line_identity IN ({_marks('id', [i for i in identities if i not in out], params)})
                ORDER BY l.created_at""", params):
            out[row['line_identity']] = (0, row)
    return out


@dataclass
class LineBalance:
    identity: str
    quantity: int
    row: dict
    cost: list = field(default_factory=list)          # (date, Shipped Not Invoiced amount), returns netted
    billed: list = field(default_factory=list)        # (date, quantity)
    posted: list = field(default_factory=list)        # (date, amount moved to cost of goods sold)
    returned: list = field(default_factory=list)      # (date, quantity) brought back on shipment returns
    issued: list = field(default_factory=list)        # (date, amount) of the issue alone, before returns

    def at(self, events, date):
        return sum(amount for when, amount in events if when <= date)

    def out(self, date):
        """Shipped and still out on ``date``: the quantity less what shipment returns dated by then took back."""
        return self.quantity - self.at(self.returned, date)

    def recognised(self, date):
        """What cost of goods sold should hold for this line on ``date``: the endpoint share of its net cost for
        the quantity billed, over the quantity still out (design §3)."""
        out = self.out(date)
        if self.quantity <= 0 or out <= 0:
            return 0
        billed = min(self.at(self.billed, date), out)
        return endpoint_share(self.at(self.cost, date), out, 0, max(billed, 0))

    def owed(self):
        """``[(date, amount)]`` still to post so the posted recognitions equal the derived balance on every date."""
        dates = sorted({when for when, _ in self.cost + self.billed + self.posted + self.returned})
        out, planned = [], 0
        for date in dates:
            delta = self.recognised(date) - (self.at(self.posted, date) + planned)
            if delta:
                out.append((date, delta))
                planned += delta
        return out

    def waiting(self, date):
        return self.at(self.cost, date) - self.at(self.posted, date)


def balances(s, identities):
    identities = list(dict.fromkeys(identities))
    if not identities:
        return {}
    families = _families(s, identities)
    current = _current_quantities(s, identities)
    posted = _posted_recognitions(s, identities)
    out = {}
    for identity in identities:
        quantity, row = current.get(identity, (0, None))
        if row is None:
            continue
        balance = LineBalance(identity, quantity, row, posted=posted.get(identity, []))
        balance.cost = [(m['effective_date'], -int(m['value_minor_units'])) for m in families.get(identity, ())
                        if int(m['value_minor_units'])]
        balance.issued = [(m['effective_date'], -int(m['value_minor_units'])) for m in families.get(identity, ())
                          if int(m['value_minor_units']) and not m['back']]
        out[identity] = balance
    for row in live_billings(s, identities):
        if row['shipment_line_identity'] in out:
            out[row['shipment_line_identity']].billed.append((row['invoice_date'], int(row['quantity_microunits'])))
    for row in live_returns(s, identities):
        if row['shipment_line_identity'] in out:
            out[row['shipment_line_identity']].returned.append((row['return_date'], int(row['quantity_microunits'])))
    return out


def all_identities(s):
    return [row[0] for row in s.company.conn.execute(sa.text('SELECT DISTINCT line_identity FROM shipment_lines'))]


def owed(s, identities=None):
    """``{identity: [(date, amount)]}`` the derived balance still requires -- empty when the books are right."""
    if not has_shipments(s):
        return {}
    found = balances(s, all_identities(s) if identities is None else identities)
    return {identity: rows for identity, balance in found.items() if (rows := balance.owed())}


def watermark(s):
    """Where posting lines, audit entries and billings stand before a command; None while it has no shipment."""
    if s.company is None or find_account(s.company) is None:
        return None
    return tuple(int(s.company.conn.execute(sa.text(f'SELECT coalesce(max(rowid), 0) FROM {table}')).scalar_one())
                 for table in ('posting_lines', 'audit_entries', 'shipment_billings'))


def _affected(s, mark):
    # A transaction the command posted to, wrote (a new revision, a void: its audit entry) or billed shipped units on;
    # a zero-total invoice posts no line at all and must still be reached.
    lines, audited, billed = mark
    touched = sorted({row[0] for row in s.company.conn.execute(sa.text(
        'SELECT transaction_id FROM posting_lines WHERE rowid > :a '
        "UNION SELECT record_id FROM audit_entries WHERE rowid > :b AND record_type = 'transaction' "
        'UNION SELECT transaction_id FROM shipment_billings WHERE rowid > :c'), {'a': lines, 'b': audited, 'c': billed})})
    if not touched:
        return []
    params = {}
    marks = _marks('tx', touched, params)
    identities = {row[0] for row in s.company.conn.execute(sa.text(f"""
        SELECT l.line_identity FROM shipment_lines l JOIN inventory_movements m ON m.id = l.movement_id
            WHERE m.transaction_id IN ({marks})
        UNION SELECT l.line_identity FROM shipment_lines l JOIN inventory_movements m ON m.corrects_movement_id = l.movement_id
            WHERE m.transaction_id IN ({marks})
        UNION SELECT l.line_identity FROM shipment_lines l JOIN inventory_movements m ON m.reverses_movement_id = l.movement_id
            WHERE m.transaction_id IN ({marks})
        UNION SELECT shipment_line_identity FROM shipment_billings WHERE transaction_id IN ({marks})
        UNION SELECT l.shipment_line_identity FROM shipment_return_lines l JOIN shipment_returns h ON h.id = l.return_id
            WHERE h.transaction_id IN ({marks})
        UNION SELECT l.shipment_line_identity FROM shipment_return_lines l JOIN inventory_movements m
            ON m.corrects_movement_id = l.movement_id OR m.reverses_movement_id = l.movement_id
            WHERE m.transaction_id IN ({marks})"""), params)}
    return sorted(identities)


def settle_in_command(s, ctx, mark):
    """After a command: post what the derived balance of every shipment line it reached still requires."""
    if mark is None or s.company is None:  # a command that closed or replaced the company (rename, upgrade)
        return []
    identities = _affected(s, mark)
    if not identities:
        return []
    return settle(s, ctx, identities)


def settle(s, ctx, identities):
    found = balances(s, identities)
    pending = {}
    for identity, balance in found.items():
        for date, amount in balance.owed():
            pending.setdefault(date, []).append((balance, amount))
    if not pending:
        return []
    account = find_account(s.company)
    currency = _home(s)
    for date in sorted(pending):
        rows = pending[date]
        for start in range(0, len(rows), 99):
            _post_recognition(s, ctx, account, currency, date, rows[start:start + 99])
    return []


def _post_recognition(s, ctx, account, currency, date, rows):
    lines, records = [], []
    for balance, amount in rows:
        row = balance.row
        money = Money(abs(amount), currency).to_dict()
        text = f'Cost of shipment {row["number"]} line {row["position"]} billed' if amount > 0 else \
            f'Cost of shipment {row["number"]} line {row["position"]} back to not invoiced'
        name = dict(name_type='customer', name_id=row['customer_id'])
        lines.append(JournalLineInput(account=row['cogs_account_id'], side='debit' if amount > 0 else 'credit',
                                      amount=money, class_id=row['class_id'], description=text, **name))
        lines.append(JournalLineInput(account=account['id'], side='credit' if amount > 0 else 'debit',
                                      amount=money, class_id=row['class_id'], description=text, **name))
        records.append(dict(id=new_id(), shipment_id=row['shipment_id'], shipment_line_identity=balance.identity,
                            effective_date=date, amount_minor_units=amount, currency=currency))
    journal = JournalPostInput(date=date, memo='Cost of shipped goods invoiced', lines=lines)
    try:
        fresh = journals.prepare(s, ctx, journal, 'post', owner=inventory.OWNER)
    except BookflowError as error:
        raise BookflowError(error.code, message=(
            f'This change moves the cost of shipped goods between Shipped Not Invoiced and cost of goods sold on '
            f'{date}, and that entry could not be posted: {error.message} Nothing was written.'),
            details={**error.details, 'blocked_correction_date': date}) from None
    at = clock.now_iso()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                      audit_event_id=fresh.data['event'])
    header = fresh.data['header']
    marker = dict(transaction_id=header['id'], type='journal_entry', kind=COST_KIND, **provenance)
    records = [dict(record, transaction_id=header['id'], **provenance) for record in records]
    journals.persist_prepared(fresh, ctx, s, command_name=getattr(ctx, 'command', None) or 'shipment cost',
                              extra=(('inventory_documents', 'inventory_document', 'transaction_id', [marker]),
                                     ('shipment_recognitions', 'shipment_recognition', 'id', records)),
                              noun='cost of shipped goods')


# ---------------------------------------------------------------- planning a shipment


@dataclass
class _Line:
    key: str
    order_line: dict
    facts: object
    root: tuple
    base: int
    numbers: object = None
    identity: str | None = None
    prior_movement: str | None = None


@dataclass
class _Shipment:
    order: dict
    revision: dict
    customer_id: str
    customer_name: str | None
    date: str
    site_id: str
    position: str
    lines: list
    change: object = None
    journal_lines: list = field(default_factory=list)
    line_of: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _order(s, selector):
    from bookflow.company import work
    header = work.resolve(s, selector, 'sales_order')
    if header['status'] == 'voided':
        raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=header['id'],
            problem='a voided sales order ships nothing'))
    rev = work.revision(s, header)
    return header, rev


def _order_lines(s, header, rev, exclude_shipment=None):
    """Each stocked order line with its root, facts and what it still has to ship (selected units, exact)."""
    from bookflow.company import tax_policy, work
    from bookflow.company.billing_queries import root_identities
    from bookflow.company.billing_selection import derived
    from bookflow.company.items import TRACKED_TYPES
    from bookflow.company.sales_orders import _root, invoiced_microunits
    policy = tax_policy.effective(work.facts(rev).profile)
    identities = root_identities(s, header)
    rows = []
    for line in work.saved_lines(s, rev):
        facts = work.line_facts(line)
        if derived(facts) or facts.profile.item_type not in TRACKED_TYPES:
            continue
        rows.append((line, facts, _root(s, identities, line)))
    every = waiting_parts(s, [root for _, _, root in rows])
    parts = {root: [p for p in found if p.shipment_id != exclude_shipment] for root, found in every.items()}
    # A shipment being corrected may hold its units again: those an invoice billed from it count in invoiced, so
    # they are added back with the ones still waiting (left out of parts).
    own_billed = {root: sum(p.billed for p in found if p.shipment_id == exclude_shipment)
                  for root, found in every.items()} if exclude_shipment else {}
    out = []
    running = header['status'] == 'open' and bool(header['active'])
    for line, facts, root in rows:
        invoiced = invoiced_microunits(s, root, facts, policy)
        waiting = _to_selected(sum(p.waiting for p in parts.get(root, ())), facts.unit_factor_nanounits)
        to_ship = (facts.quantity_microunits - invoiced - facts.cancelled_quantity_microunits - waiting
                   + _to_selected(own_billed.get(root, 0), facts.unit_factor_nanounits))
        out.append(dict(line=line, facts=facts, root=root, to_ship=max(to_ship, Fraction(0)) if running else Fraction(0)))
    return out


def _numbers(entry):
    from bookflow.company import serial_lots
    return serial_lots.from_line(entry)


def _place_key(entry):
    """``(bin, lot)`` an input line names, folded for comparison."""
    bin_, lot = getattr(entry, 'bin', None), getattr(entry, 'lot', None)
    return (str(bin_).strip().casefold() or None if bin_ is not None else None,
            str(lot).strip().casefold() or None if lot is not None else None)


def _match_priors(s, chosen, prior_lines, site_id):
    """The prior line each chosen line restates: of the same order line, the one in the same bin and lot first, then
    the first one left, so a split shipment corrected line by line keeps each line's identity."""
    if not prior_lines:
        return [None] * len(chosen)
    from bookflow.company import bins, serial_lots
    movements = [row['movement_id'] for row in prior_lines if row.get('movement_id')]
    lots = serial_lots.line_numbers(s, movements) if movements else {}
    names = bins.bin_names(s, [row['bin_id'] for row in prior_lines if row.get('bin_id')])
    left = list(prior_lines)

    def place(row):
        kept = lots.get(row.get('movement_id'))
        bin_name = names.get(row['bin_id']) if row.get('bin_id') else None
        return (bin_name.casefold() if bin_name else None, kept.lot.casefold() if kept and kept.lot else None)

    out = []
    for row, _, numbers in chosen:
        mine = [p for p in left if (p['root_document_id'], p['root_line_id']) == row['root']]
        wanted = (numbers.bin.casefold() if numbers and numbers.bin else None,
                  numbers.lot.casefold() if numbers and numbers.lot else None)
        found = next((p for p in mine if all(w is None or w == h for w, h in zip(wanted, place(p)))
                      and wanted != (None, None)), mine[0] if mine else None)
        if found is not None:
            left.remove(found)
        out.append(found)
    return out


def _pick_line(candidates, entry, index, s):
    if entry.order_line is not None:
        found = [row for row in candidates if row['line']['line_id'] == entry.order_line.upper()
                 or row['line']['line_id'] == entry.order_line]
        if not found:
            raise _invalid(f'lines.{index}.order_line', 'no stocked line of this order has this line_id')
        return found[0]
    if entry.item is None:
        raise _invalid(f'lines.{index}', 'name order_line or item')
    from bookflow.company import sales_defaults
    item = sales_defaults._row(s.company, 'item', entry.item, active=False)['id']
    found = [row for row in candidates if row['facts'].item_id == item and row['to_ship'] > 0] or \
        [row for row in candidates if row['facts'].item_id == item]
    if len(found) != 1:
        raise _invalid(f'lines.{index}.item', f'{len(found)} lines of this order carry this item; name the order_line')
    return found[0]


def _site(s, inp_site, candidates, current=None):
    if inp_site is not None:
        return placements.site_id_for(s, inp_site)
    if current is not None:
        return current['site_id']
    named = {row['line'].get('site_id') for row in candidates if row['to_ship'] > 0 and row['line'].get('site_id')}
    if len(named) == 1:
        return named.pop()
    return placements.default_site_id(s)


def _from_picks(s, candidates, picked, warnings):
    """``chosen`` from a pick list's parts (R181 increment 3): one shipment line per order line, bin and lot the
    picks came from, taking at most what each order line has to ship; the excess is named to put back."""
    from bookflow.company import bins, serial_lots
    from bookflow.company.serial_lots import Numbers
    by_root = {row['root']: row for row in candidates}
    left, chosen = {}, []
    names = bins.bin_names(s, [part['bin_id'] for part in picked if part['bin_id']])
    numbers = serial_lots.number_names(s, lot_ids=[part['lot_id'] for part in picked if part['lot_id']],
                                       serial_ids=[i for part in picked for i in part['serials']])
    for part in picked:
        row = by_root.get(part['root'])
        if row is None:
            continue
        if part['root'] not in left:
            left[part['root']] = _to_base(row['to_ship'], row['facts'].unit_factor_nanounits) if row['to_ship'] > 0 else 0
        base = min(part['base'], left[part['root']])
        excess = part['base'] - base
        place = names.get(part['bin_id']) if part['bin_id'] else None
        if excess:
            warnings.append(f'{format_quantity_micro_units(excess)} {row["facts"].profile.item.label} picked beyond '
                            f'what the order has to ship; put them back from {("bin " + place) if place else "the shelf"}.')
        if base <= 0:
            continue
        left[part['root']] -= base
        serials = [numbers[i]['number'] for i in part['serials']][:base // serial_lots.UNIT] if part['serials'] else []
        chosen.append((row, base, Numbers(lot=numbers[part['lot_id']]['number'] if part['lot_id'] else None,
                                          serial_numbers=tuple(serials) or None, bin=place or bins.NO_BIN)))
    if not chosen:
        raise BookflowError('E_VALIDATION', message=(
            'Nothing picked for this order is left to ship: it has shipped or been billed since it was picked.'),
            details=dict(reason='nothing_to_ship'))
    return chosen


def _plan(s, ctx, *, header, rev, date, site_id, position, entries_in, sni_id, current=None, prior_lines=(),
          picked=None, draft=False, held=None, drafted=None):
    """Every line this shipment ships, and the stock change it implies; nothing written. ``held``: what live drafts
    of the order hold (R181 increment 4), planned around; ``drafted``: a draft's own lines, shipped as they are."""
    from bookflow.company import commitments, work
    from bookflow.company.serial_lots import Numbers
    from bookflow.company.sales_calculations import base_quantity
    profile = work.facts(rev).profile
    candidates = _order_lines(s, header, rev, exclude_shipment=current['id'] if current else None)
    for row in candidates:
        if held and held.get(row['root']):
            row['to_ship'] = max(row['to_ship'] - _to_selected(held[row['root']], row['facts'].unit_factor_nanounits),
                                 Fraction(0))
    chosen, explicit_priors, extra_warnings = [], [], []
    drafted_identities = None
    if drafted is not None:
        by_root = {row['root']: row for row in candidates}
        named = {}
        for root, base, numbers, _ in drafted:
            row = by_root.get(root)
            if row is None:
                raise _invalid('shipment', 'an order line this draft holds is no longer on the order')
            named[root] = named.get(root, 0) + base
            if named[root] > _to_base(row['to_ship'], row['facts'].unit_factor_nanounits):
                raise _invalid('shipment', 'the order no longer has this much to ship', message=(
                    f'{row["facts"].profile.item.label}: the draft ships {format_quantity_micro_units(named[root])}; '
                    f'the order has {_plain(row["to_ship"])} to ship now. Cancel the draft and make it again.'))
            chosen.append((row, base, numbers))
        drafted_identities = [identity for _, _, _, identity in drafted]
    elif picked is not None:
        chosen = _from_picks(s, candidates, picked, extra_warnings)
    elif entries_in is None and current is not None:
        # Restating date, place or count position only: the lines stay as they are.
        by_root = {row['root']: row for row in candidates}
        for row in prior_lines:
            found = by_root.get((row['root_document_id'], row['root_line_id']))
            if found is None:
                raise _invalid('lines', 'an order line this shipment holds is no longer on the order')
            chosen.append((found, int(row['quantity_microunits']), None))
            explicit_priors.append(row)
    elif entries_in is None:
        stocked = [row for row in candidates if row['to_ship'] > 0
                   and (row['line'].get('site_id') in (None, site_id))]
        available = commitments.available_to(s, {row['facts'].item_id for row in stocked}, excluding=header['id'])
        left = dict(available)
        for row in stocked:
            factor = row['facts'].unit_factor_nanounits
            can = max(0, left.get(row['facts'].item_id, 0))
            base = min(_to_base(row['to_ship'], factor), can)
            if base <= 0:
                continue
            left[row['facts'].item_id] = left.get(row['facts'].item_id, 0) - base
            chosen.append((row, base, None))
        if not chosen:
            raise BookflowError('E_VALIDATION', message=(
                'Nothing on this order can ship from this site now: ' + ', '.join(
                    f'{row["facts"].profile.item.label} has {_plain(row["to_ship"])} to ship and '
                    f'{format_quantity_micro_units(max(0, available.get(row["facts"].item_id, 0)))} available'
                    for row in stocked) + '. Name the lines and quantities to ship past what is on hand.'
                if stocked else 'Nothing on this order is left to ship.'),
                details=dict(reason='nothing_to_ship', lines=[dict(
                    order_line=row['line']['line_id'], item=row['facts'].profile.item.label,
                    to_ship=_plain(row['to_ship']), available=format_quantity_micro_units(
                        max(0, available.get(row['facts'].item_id, 0)))) for row in stocked]))
    else:
        seen, named = set(), {}
        for index, entry in enumerate(entries_in):
            row = _pick_line(candidates, entry, index, s)
            # One order line may ship on several lines (a split over bins or lots, as a pick list ships it), each
            # told apart by its bin or lot.
            place = _place_key(entry)
            if (row['root'], place) in seen or (place == (None, None) and row['root'] in named) or \
                    (row['root'], (None, None)) in seen:
                raise _invalid(f'lines.{index}', 'name each order line once, or tell its lines apart by bin or lot')
            seen.add((row['root'], place))
            if row['line'].get('site_id') not in (None, site_id):
                raise _invalid(f'lines.{index}', 'this order line ships from another site; ship it on its own shipment',
                               site=placements.site_names(s).get(row['line']['site_id']))
            factor = row['facts'].unit_factor_nanounits
            if entry.quantity is not None:
                try:
                    selected = parse_quantity_micro_units(entry.quantity)
                except BookflowError:
                    raise _invalid(f'lines.{index}.quantity', 'use a positive decimal quantity') from None
                if selected <= 0:
                    raise _invalid(f'lines.{index}.quantity', 'must be positive')
                if named.get(row['root'], 0) + selected > row['to_ship']:
                    raise _invalid(f'lines.{index}.quantity', f'more than the {_plain(row["to_ship"])} this line has to ship',
                                   message=(f'{row["facts"].profile.item.label}: {entry.quantity} is more than the '
                                            f'{_plain(row["to_ship"] - named.get(row["root"], 0))} the order still '
                                            'has to ship.'))
                named[row['root']] = named.get(row['root'], 0) + selected
                base = base_quantity(selected, factor)
            else:
                if row['to_ship'] - named.get(row['root'], 0) <= 0:
                    raise _invalid(f'lines.{index}', 'this order line has nothing left to ship')
                base = _to_base(row['to_ship'] - named.get(row['root'], 0), factor)
                named[row['root']] = row['to_ship']
            named.setdefault(row['root'], 0)
            chosen.append((row, base, _numbers(entry)))
    lines = []
    matched = _match_priors(s, chosen, prior_lines, site_id) if not explicit_priors else explicit_priors
    if drafted_identities is not None:
        matched = [dict(line_identity=identity, movement_id=None) for identity in drafted_identities]
    for index, (row, base, numbers) in enumerate(chosen):
        # A shipment from a pick list may hold one order line on several lines (bins, lots): each restates its own.
        prior = matched[index]
        if numbers is None and prior is not None and prior.get('movement_id'):
            # A line that restates nothing about its place or numbers keeps the ones its units left with.
            from bookflow.company import serial_lots
            kept = serial_lots.line_numbers(s, [prior['movement_id']]).get(prior['movement_id'])
            place = placements.place_of_movement(s, prior['movement_id'])
            bin_id = place[1] if place and place[0] == site_id else None
            if kept is not None or bin_id:
                from bookflow.company import bins
                numbers = Numbers(lot=kept.lot if kept else None,
                                  serial_numbers=kept.serial_numbers if kept else None,
                                  bin=bins.bin_names(s, [bin_id]).get(bin_id) if bin_id else None)
        lines.append(_Line(key=f'l{index}', order_line=row['line'], facts=row['facts'], root=row['root'], base=base,
                           numbers=numbers, identity=prior['line_identity'] if prior else None,
                           prior_movement=prior.get('movement_id') if prior else None))
    entries = [inventory_effects.Entry(
        key=line.key, item_id=line.facts.item_id, item_name=line.facts.profile.item.label, kind='issue',
        quantity_microunits=line.base, asset_account_id=line.facts.profile.asset_account.id,
        offset_account_id=sni_id, class_id=line.facts.profile.class_id.id if line.facts.profile.class_id else None,
        site_id=site_id, numbers=line.numbers) for line in lines]
    reversing = inventory_effects.own_movements(s, current['transaction_id']) if current else ()
    change = inventory_effects.plan(s, entries=entries, reversing=reversing, date=date, currency=rev['currency'],
                                    field='lines', count_position=position)
    shipment = _Shipment(order=header, revision=rev, customer_id=profile.customer.id, customer_name=profile.customer.label,
                         date=date, site_id=site_id, position=position, lines=lines, change=change)
    currency = rev['currency']
    for line in lines:
        cost = -change.costs.get(line.key, 0)
        line.cost = cost
        if not cost:
            continue
        money = Money(cost, currency).to_dict()
        text = f'Ship {format_quantity_micro_units(line.base)} {line.facts.profile.item.label}'
        common = dict(amount=money, name_type='customer', name_id=profile.customer.id, description=text,
                      class_id=line.facts.profile.class_id.id if line.facts.profile.class_id else None)
        shipment.journal_lines.append(JournalLineInput(account=line.facts.profile.asset_account.id, side='credit', **common))
        shipment.line_of[line.key] = len(shipment.journal_lines)
        shipment.journal_lines.append(JournalLineInput(account=sni_id, side='debit', **common))
    if not shipment.journal_lines and not draft:
        raise _invalid('lines', 'the units shipped are worth nothing', message=(
            'Every unit on this shipment is worth nothing in the books, so there is no cost to hold in Shipped Not '
            'Invoiced. Enter the bill that bought the stock, or record what it is worth with `inventory adjust`, '
            'then ship.'), reason='shipment_without_value')
    shipment.warnings = list(change.warnings) + extra_warnings
    return shipment


def _bind(shipment, fresh):
    pending = fresh.data['pending']
    header = fresh.data['header']
    revision = pending['transaction_revisions'][0]
    entered = {line['position']: line for line in pending['document_lines']}
    live = {b['id'] for b in pending['posting_batches'] if b['kind'] != 'reversal'}
    legs = {line['line_no']: line for line in pending['posting_lines'] if line['batch_id'] in live}
    batch = next(b for b in pending['posting_batches'] if b['kind'] != 'reversal') if live else None
    for movement in shipment.change.movements:
        if movement.key is None:
            continue
        position = shipment.line_of.get(movement.key)
        if position is None:
            inventory_effects.bind(movement, None, transaction_id=header['id'], revision_id=revision['id'],
                                   document_line_id=entered[1]['id'], batch=batch)
            continue
        inventory_effects.bind(movement, legs[position], transaction_id=header['id'], revision_id=revision['id'],
                               document_line_id=entered[position]['id'])
    inventory_effects.bind_reversals(shipment.change, pending['posting_lines'], pending['posting_batches'])


def _number(s):
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (1001, '')
    while s.company.conn.execute(sa.select(c.shipments.c.id).where(c.shipments.c.number == f'{prefix}{number}')).first() \
            or s.company.conn.execute(sa.select(c.shipment_drafts.c.id).where(
                c.shipment_drafts.c.number == f'{prefix}{number}')).first():
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def _journal_version(s, transaction_id):
    return s.company.conn.execute(sa.select(c.transactions.c.version).where(
        c.transactions.c.id == transaction_id)).scalar_one()


def _ship_method(s, selector, rev):
    from bookflow.company import sales_defaults
    if selector is not None:
        return sales_defaults._row(s.company, 'ship_method', selector)['id']
    from bookflow.company import work
    method = getattr(work.facts(rev).profile, 'ship_method', None)
    return getattr(method, 'id', None)


def _billed_by_identity(s, identities):
    out = {}
    for row in live_billings(s, identities):
        out.setdefault(row['shipment_line_identity'], []).append(row)
    return out


def _guard_billed(s, current, prior_lines, shipment=None, date=None):
    """An update or void may not take a shipment below what invoices billed, or past an invoice's date."""
    billed = _billed_by_identity(s, [row['line_identity'] for row in prior_lines])
    if not billed:
        return
    invoices = sorted({(row['invoice_number'] or row['transaction_id']) for rows in billed.values() for row in rows})
    if shipment is None:
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'Invoice {", ".join(invoices)} bills this shipment; void or correct the invoice first.'),
            details=dict(problem='an invoice bills this shipment; void or correct it first', invoices=invoices,
                         reason='shipment_invoiced'))
    kept = {line.identity: line.base for line in shipment.lines if line.identity}
    identities = set(kept)
    for row in prior_lines:
        rows = billed.get(row['line_identity'])
        if not rows:
            continue
        if row['line_identity'] not in identities:
            raise BookflowError('E_WORK_DEPENDENCY', message=(
                f'Invoice {", ".join(sorted({r["invoice_number"] or r["transaction_id"] for r in rows}))} billed a line '
                'this correction would drop; correct it line for line, or void or correct the invoice first.'),
                details=dict(problem='a billed line would be dropped', invoices=invoices, reason='shipment_invoiced'))
        total = sum(int(r['quantity_microunits']) for r in rows)
        if kept.get(row['line_identity'], 0) < total:
            raise BookflowError('E_WORK_DEPENDENCY', message=(
                f'Invoice {", ".join(sorted({r["invoice_number"] or r["transaction_id"] for r in rows}))} billed '
                f'{format_quantity_micro_units(total)} of this line; the shipment cannot hold fewer.'),
                details=dict(problem='below what invoices billed', invoices=invoices, reason='shipment_invoiced'))
        earliest = min(r['invoice_date'] for r in rows)
        if date is not None and date > earliest:
            raise _invalid('date', f'invoice {rows[0]["invoice_number"]} billing this shipment is dated {earliest}',
                           message=(f'An invoice dated {earliest} bills this shipment, so it cannot ship later than '
                                    'that. Correct the invoice first.'))


def _guard_picked(s, header, shipment, prior_lines):
    """Units of an order a live pick list holds ship from the list (`shipment post --pick-list`), not past it: a
    shipment from the order alone (or a correction shipping more of such a line) would issue them a second time."""
    from bookflow.company import pick_lists
    held = pick_lists.holding(s, order_id=header['id'])
    if not held:
        return
    before, after = {}, {}
    for row in prior_lines:
        root = (row['root_document_id'], row['root_line_id'])
        before[root] = before.get(root, 0) + int(row['quantity_microunits'])
    for line in shipment.lines:
        after[line.root] = after.get(line.root, 0) + line.base
    found = {root: held[root] for root in held if after.get(root, 0) > before.get(root, 0)}
    if found:
        pick_lists.refuse_held(found, 'shipping it from the order would issue those units a second time')


def standing_returns(s, movement_ids):
    """``[(kind, number)]`` of every return not voided that gives back units of these issues: shipment returns and
    credit memos returning invoiced units (R181 increment 2)."""
    movement_ids = [m for m in movement_ids if m]
    if not movement_ids:
        return []
    params = {}
    marks = _marks('mv', movement_ids, params)
    rows = _rows(s, f"""SELECT m.id, m.transaction_id, t.number AS tx_number, t.type, h.number AS return_number
        FROM inventory_movements m JOIN transactions t ON t.id = m.transaction_id
        LEFT JOIN shipment_returns h ON h.transaction_id = m.transaction_id
        WHERE m.kind = 'receipt' AND m.returns_movement_id IN ({marks})
          AND NOT EXISTS (SELECT 1 FROM inventory_movements r WHERE r.reverses_movement_id = m.id)""", params)
    return sorted({('shipment return', row['return_number']) if row['return_number'] else
                   (row['type'].replace('_', ' '), row['tx_number'] or row['transaction_id']) for row in rows})


def _guard_returns(s, prior_lines, *, void):
    """A shipment whose units came back, on a shipment return or a credit memo, keeps its stock as shipped: the
    return names its issue. Void (or correct) the return first; to take units off it, record a return."""
    found = standing_returns(s, [row.get('movement_id') for row in prior_lines])
    if not found:
        return
    names = ', '.join(f'{kind} {number}' for kind, number in found)
    first = found[0]
    remedy = (f'void the return first (`shipment-return void {first[1]}`)' if first[0] == 'shipment return'
              else f'void or correct {first[0]} {first[1]} first')
    raise BookflowError('E_WORK_DEPENDENCY', message=(
        f'Units of this shipment came back on {names}; {"voiding" if void else "restating the stock of"} the '
        f'shipment would leave that return naming stock that never left. {remedy[0].upper() + remedy[1:]}.'
        + ('' if void else ' To take units off the shipment, record a return (`shipment return`) instead.')),
        details=dict(problem='a return gives back units of this shipment', returns=[f'{k} {n}' for k, n in found],
                     reason='shipment_returned'))


def _line_rows(s, shipment, revision_id, shipment_id, provenance):
    rows = []
    by_key = {m.key: m.values['id'] for m in shipment.change.movements if m.key is not None}
    for position, line in enumerate(shipment.lines, 1):
        facts = line.facts
        bin_id = None
        if line.numbers is not None and line.numbers.bin:
            from bookflow.company import bins
            bin_id = bins.line_bin(s, facts.item_id, shipment.site_id, line.numbers.bin)
        line.identity = line.identity or new_id()
        rows.append(dict(id=new_id(), revision_id=revision_id, shipment_id=shipment_id, line_identity=line.identity,
                         position=position, root_document_id=line.root[0], root_line_id=line.root[1],
                         item_id=facts.item_id, quantity_microunits=line.base, bin_id=bin_id,
                         description=facts.description, asset_account_id=facts.profile.asset_account.id,
                         cogs_account_id=facts.profile.cogs_account.id,
                         class_id=facts.profile.class_id.id if facts.profile.class_id else None,
                         movement_id=by_key.get(line.key), **provenance))
    return rows


def _draft_to_post(s, inp):
    """The draft `shipment post --shipment` ships (R181 increment 4): open, at the version given, alone."""
    from bookflow.company import shipment_packing as packing
    packing.require_enabled(s)
    draft = packing.find_draft(s, inp.shipment)
    if draft is None:
        if s.company.conn.execute(sa.select(c.shipments.c.id).where(sa.or_(
                c.shipments.c.number == inp.shipment.strip(), c.shipments.c.id == inp.shipment.strip().upper()))).first():
            raise _invalid('shipment', 'this shipment has shipped already', message=(
                f'Shipment {inp.shipment} has shipped already; `shipment show {inp.shipment}` shows it.'),
                reason='already_shipped')
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'draft shipment', 'selector': inp.shipment})
    if inp.expected_version is not None and inp.expected_version != draft['revision_number']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': draft['id'],
                                                          'expected_version': inp.expected_version,
                                                          'current_version': draft['revision_number']})
    if draft['status'] == 'shipped':
        raise _invalid('shipment', 'this shipment has shipped already', message=(
            f'Shipment {draft["number"]} has shipped already; `shipment show {draft["number"]}` shows it.'),
            reason='already_shipped')
    if draft['status'] != 'draft':
        raise _invalid('shipment', f'draft {draft["number"]} is {draft["status"]}', message=(
            f'Draft shipment {draft["number"]} is {draft["status"]}; it ships nothing.'), reason='draft_cancelled')
    for name in ('lines', 'pick_list', 'packages', 'site'):
        if getattr(inp, name, None) is not None:
            raise _invalid(name, 'a draft ships its own lines, place and boxes; leave this out (or cancel the draft)')
    if inp.sales_order is not None:
        from bookflow.company import work
        if work.resolve(s, inp.sales_order, 'sales_order')['id'] != draft['sales_order_id']:
            raise _invalid('sales_order', f'draft {draft["number"]} ships another order')
    return draft


def _drafted_lines(s, draft):
    """``[(root, base, Numbers, identity)]`` a draft ships."""
    from bookflow.company import bins, shipment_packing as packing
    from bookflow.company.serial_lots import Numbers
    rows = packing.draft_lines(s, draft['id'])
    names = bins.bin_names(s, [row['bin_id'] for row in rows if row['bin_id']])
    out = []
    for row in rows:
        serials = tuple(json.loads(row['serial_numbers'])) if row['serial_numbers'] else None
        numbers = Numbers(lot=row['lot'], serial_numbers=serials, bin=names.get(row['bin_id']) if row['bin_id'] else None)
        out.append(((row['root_document_id'], row['root_line_id']), int(row['quantity_microunits']),
                    numbers if (numbers.lot or serials or numbers.bin) else None, row['line_identity']))
    return out


def _match_draft(s, draft, shipment):
    """A draft made from a pick list ships what was picked; refused if the picks changed since it was made."""
    from bookflow.company import shipment_packing as packing
    def key(root, base, numbers):
        return (root, base, (numbers.lot or '').casefold() if numbers else '',
                tuple(sorted(numbers.serial_numbers or ())) if numbers else ())
    wanted = {}
    for root, base, numbers, identity in _drafted_lines(s, draft):
        wanted.setdefault(key(root, base, numbers), []).append(identity)
    for line in shipment.lines:
        found = wanted.get(key(line.root, line.base, line.numbers))
        if not found:
            raise _invalid('shipment', 'the picks changed since this draft was made', message=(
                f'What was picked for this order changed since draft {draft["number"]} was made. Cancel it '
                f'(`shipment cancel {draft["number"]}`) and make it again from the pick list.'), reason='draft_stale')
        line.identity = found.pop(0)
    if any(wanted.values()):
        raise _invalid('shipment', 'the picks changed since this draft was made', reason='draft_stale')


def _draft_revision(draft, status, ctx, provenance, **changes):
    row = dict(id=new_id(), draft_id=draft['id'], revision_number=draft['revision_number'] + 1, status=status,
               ship_date=draft['ship_date'], count_position=draft['count_position'],
               ship_method_id=draft['ship_method_id'], carrier=draft['carrier'],
               tracking_numbers=draft['tracking_numbers'], memo=draft['memo'],
               freight_minor_units=draft['freight_minor_units'], freight_item_id=draft['freight_item_id'],
               reason=(ctx.reason or '').strip() or None, **provenance)
    row.update(changes)
    return row


def freight_item(s, selector):
    """The item freight bills as: the one named, else the company's shipping item."""
    from bookflow.company import sales_defaults
    if selector is None:
        found = s.company.conn.execute(sa.select(c.company_info.c.shipping_item_id)).scalar()
        if found is None:
            raise _invalid('freight.item', 'name the item freight bills as', message=(
                'Name the other-charge or service item the freight bills as (freight.item), or set the company\'s '
                'shipping item with `company update --shipping-item-id`.'))
        selector = found
    item = sales_defaults._row(s.company, 'item', selector)
    if item['type'] not in ('other_charge', 'service') or not item.get('sales_enabled', True):
        raise _invalid('freight.item', 'freight bills as an other-charge or service item that is sold')
    return item


def freight_billed(s, shipment_id):
    """``(invoice number or id)`` of the live invoice line that billed this shipment's freight, or None."""
    row = s.company.conn.execute(sa.text(
        "SELECT t.number, t.id FROM shipment_freight_billings b JOIN transactions t ON t.id = b.transaction_id "
        "WHERE b.shipment_id = :id AND t.status = 'posted' AND EXISTS (SELECT 1 FROM document_lines d "
        "WHERE d.revision_id = t.current_revision_id AND d.line_id = b.line_id) LIMIT 1"), {'id': shipment_id}).first()
    return (row[0] or row[1]) if row else None


def _freight_facts(s, inp, rev, current, draft, operation):
    """``freight_minor_units`` and ``freight_item_id`` the new revision carries; fixed once an invoice billed it."""
    given = getattr(inp, 'freight', None)
    if current is not None:
        kept = dict(freight_minor_units=current['revision'].get('freight_minor_units'),
                    freight_item_id=current['revision'].get('freight_item_id'))
    elif draft is not None:
        kept = dict(freight_minor_units=draft['freight_minor_units'], freight_item_id=draft['freight_item_id'])
    else:
        kept = dict(freight_minor_units=None, freight_item_id=None)
    if given is None or operation == 'void':
        return kept
    from bookflow.company.sales_models import money
    amount = money(given.amount, rev['currency'], 'freight.amount').minor_units
    if amount < 0:
        raise _invalid('freight.amount', 'must not be negative')
    fresh = dict(freight_minor_units=amount or None, freight_item_id=freight_item(s, given.item)['id'] if amount else None)
    if current is not None and fresh != kept:
        billed = freight_billed(s, current['id'])
        if billed:
            raise BookflowError('E_WORK_DEPENDENCY', message=(
                f'Invoice {billed} billed this shipment\'s freight, so it is fixed; correct the invoice instead.'),
                details=dict(problem='freight already billed', invoices=[billed], reason='freight_invoiced'))
    return fresh


def _run(s, ctx, inp, operation, *, persist):
    command_name = 'shipment ' + operation
    account = find_account(s.company)
    touched = []
    if persist and operation == 'post' and account is None:
        account, touched = ensure_account(s, ctx)
    sni_id = account['id'] if account else _PLACEHOLDER
    current, prior_lines = None, []
    picked = pick_current = draft = None
    from bookflow.company import shipment_packing as packing
    if operation == 'post' and getattr(inp, 'shipment', None) is not None:
        # Posting a draft (R181 increment 4): its lines, places, numbers and boxes ship as they are.
        draft = _draft_to_post(s, inp)
        if draft['pick_list_id']:
            from bookflow.company import pick_lists
            pick_current, order_id, picked = pick_lists.shipping_plan(s, draft['pick_list_id'],
                                                                     draft['sales_order_id'], None)
    elif operation == 'post' and getattr(inp, 'packages', None) is not None:
        packing.require_enabled(s)
    if operation == 'post' and draft is None and getattr(inp, 'pick_list', None) is not None:
        # Shipping what was picked (R181 increment 3): the order and site come from the list.
        from bookflow.company import pick_lists
        if inp.lines is not None:
            raise _invalid('lines', 'ship from the pick list or name the lines, not both')
        pick_current, order_id, picked = pick_lists.shipping_plan(s, inp.pick_list, inp.sales_order, inp.site)
    if operation == 'post':
        if picked is None and draft is None and inp.sales_order is None:
            raise _invalid('sales_order', 'name the sales order to ship (or the pick_list it was picked on)')
        header, rev = _order(s, order_id if picked is not None else
                             draft['sales_order_id'] if draft is not None else inp.sales_order)
        if header['status'] != 'open' or not header['active']:
            raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=header['id'],
                problem='a closed or inactive sales order ships nothing; reopen it first'))
        date = inp.date or (draft['ship_date'] if draft else None) or clock.today_iso()
    else:
        current = resolve(s, inp.shipment)
        current_rev = current['revision']
        if inp.expected_version is not None and inp.expected_version != current_rev['revision_number']:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': current['id'],
                                                              'expected_version': inp.expected_version,
                                                              'current_version': current_rev['revision_number']})
        if current_rev['status'] == 'voided':
            raise _invalid('shipment', 'this shipment is voided')
        prior_lines = _revision_lines(s, current_rev['id'])
        from bookflow.company import work
        header = work.resolve(s, current['sales_order_id'], 'sales_order')
        rev = work.revision(s, header)
        date = getattr(inp, 'date', None) or current_rev['ship_date']
        current = dict(current, **{k: current_rev[k] for k in ('site_id', 'ship_date')})
    if operation == 'update' and _text_only(inp, current):
        return _restate_text(s, ctx, inp, current, prior_lines, header, rev, persist=persist)
    if operation != 'post':
        _guard_returns(s, prior_lines, void=operation == 'void')
    if operation == 'void':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        _guard_billed(s, current, prior_lines)
        journals.open_dates(s, [current['ship_date']])
        change = inventory_effects.plan(s, entries=[], reversing=inventory_effects.own_movements(s, current['transaction_id']),
                                        currency=rev['currency'], field='lines')
        inventory_effects.open_dates(s, change, [current['ship_date']])
        shipment = _Shipment(order=header, revision=rev, customer_id=current['customer_id'], customer_name=None,
                             date=current['ship_date'], site_id=current['site_id'],
                             position=current['revision']['count_position'], lines=[], change=change)
        journal = JournalVoidInput(journal=current['transaction_id'],
                                   expected_version=_journal_version(s, current['transaction_id']))
        status = 'voided'
    else:
        site_id = draft['site_id'] if draft else pick_current['site_id'] if pick_current else \
            _site(s, getattr(inp, 'site', None), _order_lines(s, header, rev) if current is None else [], current)
        position = inp.count_position or (current['revision']['count_position'] if current else
                                          draft['count_position'] if draft else 'before')
        journals.open_dates(s, [date] + ([current['ship_date']] if current else []))
        shipment = _plan(s, ctx, header=header, rev=rev, date=date, site_id=site_id, position=position,
                         entries_in=inp.lines, sni_id=sni_id, current=current, prior_lines=prior_lines,
                         picked=picked, held=packing.drafted(s, header['id'], exclude=draft['id'] if draft else None)
                         if current is None else None,
                         drafted=_drafted_lines(s, draft) if draft is not None and picked is None else None)
        if draft is not None and picked is not None:
            _match_draft(s, draft, shipment)
        if current is not None:
            _guard_billed(s, current, prior_lines, shipment, date if date != current['ship_date'] else None)
        if picked is None:
            _guard_picked(s, header, shipment, prior_lines)
        inventory_effects.open_dates(s, shipment.change)
        memo = inp.memo if inp.memo is not None else (current['revision']['memo'] if current else None)
        journal = (JournalPostInput(date=date, memo=memo or f'Shipment for sales order {header["number"]}',
                                    lines=shipment.journal_lines) if current is None else
                   JournalUpdateInput(journal=current['transaction_id'],
                                      expected_version=_journal_version(s, current['transaction_id']), date=date,
                                      memo=memo or f'Shipment for sales order {header["number"]}',
                                      lines=shipment.journal_lines))
        status = 'shipped'
    facts = dict(
        ship_method_id=(_ship_method(s, inp.ship_method, rev) if operation == 'post' else
                        (_ship_method(s, inp.ship_method, rev) if getattr(inp, 'ship_method', None) is not None
                         else current['revision']['ship_method_id'])),
        carrier=(getattr(inp, 'carrier', None) if operation == 'post' or getattr(inp, 'carrier', None) is not None
                 else current['revision']['carrier']),
        tracking_numbers=json.dumps(list(getattr(inp, 'tracking_numbers', None) or []) if operation == 'post' or
                                    getattr(inp, 'tracking_numbers', None) is not None
                                    else json.loads(current['revision']['tracking_numbers'])),
        memo=(getattr(inp, 'memo', None) if operation == 'post' or getattr(inp, 'memo', None) is not None
              else current['revision']['memo']))
    if draft is not None:
        # A draft's own facts stand unless the post restates them.
        for name in ('ship_method_id', 'carrier', 'memo'):
            if getattr(inp, name.removesuffix('_id'), None) is None:
                facts[name] = draft[name]
        if inp.tracking_numbers is None:
            facts['tracking_numbers'] = draft['tracking_numbers']
    facts.update(_freight_facts(s, inp, rev, current, draft, operation))
    boxes, pack_lines = [], []
    if operation != 'void':
        pack_lines = packing.planned_lines(s, shipment.lines)
        if draft is not None:
            boxes = packing.carry(packing.load(s, draft['revision_id']), pack_lines)
        elif current is not None:
            boxes = packing.carry(packing.load(s, current['revision']['id']), pack_lines)
        elif getattr(inp, 'packages', None) is not None:
            boxes = packing.from_packages(s, inp.packages, pack_lines)
    if not persist:
        if account is not None or operation != 'post':
            fresh = journals.prepare(s, ctx, journal, operation if operation != 'post' else 'post', owner=inventory.OWNER)
            if operation != 'void':
                _bind(shipment, fresh)
                inventory_effects.check(s, shipment.change, fresh.data['pending']['posting_lines'])
        return _output(s, shipment, current=current, status=status, facts=facts, preview=True,
                       packed=packing.output(s, boxes, pack_lines)), [], 'preview'
    fresh = journals.prepare(s, ctx, journal, 'post' if operation == 'post' else operation, owner=inventory.OWNER)
    if not fresh.data['changed']:
        raise BookflowError('E_INTERNAL', message='A shipment produced no accounting.')
    if operation != 'void':
        _bind(shipment, fresh)
    else:
        inventory_effects.bind_reversals(shipment.change, fresh.data['pending']['posting_lines'],
                                         fresh.data['pending']['posting_batches'])
    inventory_effects.check(s, shipment.change, fresh.data['pending']['posting_lines'])
    at = clock.now_iso()
    event = fresh.data['event']
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    tx = fresh.data['header']
    extra = []
    sequence = None
    if current is None:
        if draft is not None:
            number, shipment_id = draft['number'], draft['id']
            extra.append(('shipment_draft_revisions', 'shipment_draft_revision', 'id', [_draft_revision(
                draft, 'shipped', ctx, provenance)]))
        else:
            number, sequence = _number(s)
            shipment_id = new_id()
        extra.append(('inventory_documents', 'inventory_document', 'transaction_id',
                      [dict(transaction_id=tx['id'], type='journal_entry', kind=KIND, **provenance)]))
        extra.append(('shipments', 'shipment', 'id', [dict(
            id=shipment_id, number=number, sales_order_id=header['id'], customer_id=shipment.customer_id,
            transaction_id=tx['id'], **provenance)]))
        if pick_current is not None:
            from bookflow.company import pick_lists
            extra.extend(pick_lists.link_rows(s, pick_current, shipment_id, header['id'], provenance))
        revision_number = 1
    else:
        shipment_id, number = current['id'], current['number']
        revision_number = current['revision']['revision_number'] + 1
    revision_row = dict(id=new_id(), shipment_id=shipment_id, revision_number=revision_number, status=status,
                        ship_date=shipment.date, count_position=shipment.position, site_id=shipment.site_id,
                        ship_to=None, reason=(ctx.reason or '').strip() or None, **facts, **provenance)
    extra.append(('shipment_revisions', 'shipment_revision', 'id', [revision_row]))
    movements = shipment.change.rows(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                                     audit_event_id=event)
    if movements:
        extra.extend(placements.extras(placements.placements_for_rows(
            s, movements, shipment.change.sites, shipment.change.placements)))
        extra.append(('inventory_movements', 'inventory_movement', 'id', movements))
    if operation == 'void':
        line_rows = [dict(row, id=new_id(), revision_id=revision_row['id'], movement_id=None, **provenance)
                     for row in prior_lines]
    else:
        line_rows = _line_rows(s, shipment, revision_row['id'], shipment_id, provenance)
    if line_rows:
        extra.append(('shipment_lines', 'shipment_line', 'id', line_rows))
    if boxes:
        extra.extend(packing.rows(boxes, shipment_id, revision_row['id'], provenance, identities={
            f'#{row["position"]}': row['line_identity'] for row in line_rows}))
    applied = journals.persist_prepared(fresh, ctx, s, command_name=command_name, extra=tuple(extra), noun='shipment',
                                        number=number)
    touched += list(applied.touched)
    touched += inventory_effects.write_corrections(s, ctx, shipment.change, command_name=command_name, created_at=at)
    if sequence:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**sequence)
        s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=sequence))
    shown = resolve(s, shipment_id)
    return _output(s, shipment, current=shown, status=status, facts=facts), touched, \
        f'{operation} shipment {number}'


def _text_only(inp, current):
    """An update restating only packing and carrier facts: ship method, carrier, tracking numbers, memo."""
    rev = current['revision']
    return (inp.lines is None and (inp.date is None or inp.date == rev['ship_date'])
            and (inp.count_position is None or inp.count_position == rev['count_position']))


def _restate_text(s, ctx, inp, current, prior_lines, header, rev, *, persist):
    """The correction table's first row: a new revision, nothing reposts (design §6). The lines are carried across
    unchanged, each still standing behind the issue it posted."""
    current_rev = current['revision']
    facts = dict(
        ship_method_id=(_ship_method(s, inp.ship_method, rev) if inp.ship_method is not None
                        else current_rev['ship_method_id']),
        carrier=inp.carrier if inp.carrier is not None else current_rev['carrier'],
        tracking_numbers=json.dumps(list(inp.tracking_numbers) if inp.tracking_numbers is not None
                                    else json.loads(current_rev['tracking_numbers'])),
        memo=inp.memo if inp.memo is not None else current_rev['memo'])
    facts.update(_freight_facts(s, inp, rev, current, None, 'update'))
    if not persist:
        shown = show_output(s, current['id'])
        return shown.model_copy(update=dict(status='will_ship', ship_method_id=facts['ship_method_id'],
                                            carrier=facts['carrier'], memo=facts['memo'],
                                            freight=_freight_output(s, current['id'], facts, rev['currency']),
                                            tracking_numbers=json.loads(facts['tracking_numbers']))), [], 'preview'
    from bookflow.company import shipment_packing as packing
    touched, summary = write_revision(s, ctx, current, prior_lines, facts, 'shipment update',
                                      packing.load(s, current_rev['id']))
    return show_output(s, current['id']), touched, summary


def write_revision(s, ctx, current, prior_lines, facts, command_name, boxes):
    """A revision that reposts nothing (design §6, the correction table's first row): the lines carried across
    unchanged, each still standing behind the issue it posted, with ``facts`` and the packing ``boxes``."""
    from bookflow.company import shipment_packing as packing
    current_rev = current['revision']
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    revision_row = dict(id=new_id(), shipment_id=current['id'], revision_number=current_rev['revision_number'] + 1,
                        status=current_rev['status'], ship_date=current_rev['ship_date'],
                        count_position=current_rev['count_position'], site_id=current_rev['site_id'],
                        ship_to=current_rev['ship_to'], reason=(ctx.reason or '').strip() or None, **facts, **provenance)
    line_rows = [dict(row, id=new_id(), revision_id=revision_row['id'], **provenance) for row in prior_lines]
    touched = [Touched('shipment_revision', revision_row['id'], 'create', None, 1, dict(revision_row), None,
                       db='company')]
    touched += [Touched('shipment_line', row['id'], 'create', None, 1, dict(row), None, db='company')
                for row in line_rows]
    extra = packing.rows(boxes, current['id'], revision_row['id'], provenance)
    for _, kind, _, rows in extra:
        touched += [Touched(kind, row['id'], 'create', None, 1, dict(row), None, db='company') for row in rows]
    summary = f'{command_name.split()[-1]} shipment {current["number"]}'
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.shipment_revisions.insert().values(**revision_row))
    if line_rows:
        s.company.conn.execute(c.shipment_lines.insert(), line_rows)
    for table, _, _, rows in extra:
        s.company.conn.execute(getattr(c, table).insert(), rows)
    return touched, summary


def _freight_output(s, shipment_id, facts, currency):
    if not facts.get('freight_minor_units'):
        return None
    from bookflow.company.shipment_models import FreightOutput
    name = s.company.conn.execute(sa.select(c.items.c.full_name).where(
        c.items.c.id == facts['freight_item_id'])).scalar()
    return FreightOutput(amount=_money(facts['freight_minor_units'], currency), item_id=facts['freight_item_id'],
                         item_name=name, billed_by=freight_billed(s, shipment_id) if shipment_id else None)


# ---------------------------------------------------------------- output


def _output(s, shipment, *, current, status, facts, preview=False, packed=None):
    currency = shipment.revision['currency']
    order = shipment.order
    if current is not None and not preview:
        return show_output(s, current['id'], warnings=shipment.warnings)
    from bookflow.company import items as item_service
    lines = []
    for position, line in enumerate(shipment.lines, 1):
        cost = getattr(line, 'cost', 0)
        numbers = line.numbers
        lines.append(ShipmentLineOutput(
            line_identity=line.identity or '', position=position, order_line_id=line.order_line['line_id'],
            item_id=line.facts.item_id, item_name=line.facts.profile.item.label, quantity=_plain(line.base),
            bin=numbers.bin if numbers else None, lot=numbers.lot if numbers else None,
            serial_numbers=list(numbers.serial_numbers) if numbers and numbers.serial_numbers else None,
            cost=_money(cost, currency), invoiced='0', not_invoiced=_plain(line.base), waiting=_money(cost, currency)))
    total = sum(getattr(line, 'cost', 0) for line in shipment.lines)
    after = {}
    for row in _order_lines(s, order, shipment.revision):
        planned = sum(line.base for line in shipment.lines if line.root == row['root'])
        left = row['to_ship'] - _to_selected(planned, row['facts'].unit_factor_nanounits)
        if current is not None:
            left += _to_selected(sum(int(p['quantity_microunits']) for p in _revision_lines(s, current['revision']['id'])
                                     if (p['root_document_id'], p['root_line_id']) == row['root']),
                                 row['facts'].unit_factor_nanounits) if status != 'voided' else 0
        after[row['line']['line_id']] = _plain(max(left, 0))
    return ShipmentOutput(
        id=current['id'] if current else None, number=current['number'] if current else None,
        version=current['revision']['revision_number'] if current else None,
        status='will_' + ('void' if status == 'voided' else 'ship'),
        sales_order_id=order['id'], sales_order_number=order['number'], customer_id=shipment.customer_id,
        customer_name=shipment.customer_name, date=shipment.date, count_position=shipment.position,
        site_id=shipment.site_id, site_name=placements.site_names(s).get(shipment.site_id),
        ship_method_id=facts['ship_method_id'], carrier=facts['carrier'],
        tracking_numbers=json.loads(facts['tracking_numbers']), memo=facts['memo'],
        transaction_id=current['transaction_id'] if current else None, lines=lines,
        cost=_money(total, currency), shipped_not_invoiced=_money(total, currency), order_to_ship=after,
        packing=packed, freight=_freight_output(s, current['id'] if current else None, facts, currency),
        warnings=list(dict.fromkeys(shipment.warnings)))


def show_output(s, shipment_id, warnings=()):
    header = resolve(s, shipment_id)
    rev = header['revision']
    from bookflow.company import work
    order = work.resolve(s, header['sales_order_id'], 'sales_order')
    order_rev = work.revision(s, order)
    currency = order_rev['currency']
    rows = _revision_lines(s, rev['id'])
    identities = [row['line_identity'] for row in rows]
    found = balances(s, identities)
    billed = _billed_by_identity(s, identities)
    returned = returned_by_identity(s, identities)
    from bookflow.company import bins, serial_lots
    names = dict(s.company.conn.execute(sa.select(c.items.c.id, c.items.c.full_name).where(
        c.items.c.id.in_([row['item_id'] for row in rows] or ['']))).all())
    numbers = serial_lots.line_numbers(s, [row['movement_id'] for row in rows if row['movement_id']])
    bin_names = bins.bin_names(s, [row['bin_id'] for row in rows if row['bin_id']])
    lines, invoices = [], []
    total = waiting_total = 0
    order_lines = {}
    for line in work.saved_lines(s, order_rev):
        order_lines[line['line_id']] = line
    for row in rows:
        balance = found.get(row['line_identity'])
        cost = balance.at(balance.issued, '9999-12-31') if balance else 0
        waiting = balance.waiting('9999-12-31') if balance else 0
        quantity = int(row['quantity_microunits']) if rev['status'] == 'shipped' else 0
        billed_quantity = sum(int(r['quantity_microunits']) for r in billed.get(row['line_identity'], ()))
        back = returned.get(row['line_identity'], 0) if rev['status'] == 'shipped' else 0
        total += cost
        waiting_total += waiting
        place = placements.place_of_movement(s, row['movement_id']) if row['movement_id'] else None
        bin_name = bin_names.get(row['bin_id']) or (bins.bin_names(s, [place[1]]).get(place[1]) if place and place[1] else None)
        number = numbers.get(row['movement_id'])
        lines.append(ShipmentLineOutput(
            line_identity=row['line_identity'], position=row['position'], order_line_id=row['root_line_id'],
            item_id=row['item_id'], item_name=names.get(row['item_id'], row['item_id']), quantity=_plain(quantity),
            bin=bin_name, lot=number.lot if number else None,
            serial_numbers=list(number.serial_numbers) if number and number.serial_numbers else None,
            cost=_money(cost, currency), invoiced=_plain(billed_quantity), returned=_plain(back),
            not_invoiced=_plain(max(quantity - billed_quantity - back, 0)), waiting=_money(waiting, currency)))
        for r in billed.get(row['line_identity'], ()):
            invoices.append(ShipmentBillingOutput(invoice_id=r['transaction_id'], invoice_number=r['invoice_number'],
                                                  date=r['invoice_date'], quantity=_plain(r['quantity_microunits']),
                                                  line_identity=row['line_identity']))
    after = {}
    for row in _order_lines(s, order, order_rev):
        after[row['line']['line_id']] = _plain(row['to_ship'])
    history = [ShipmentHistoryOutput(version=h['revision_number'], status=h['status'], date=h['ship_date'],
                                     reason=h['reason'], created_at=h['created_at'], created_by=h['created_by'],
                                     created_via=h['created_via']) for h in _history(s, header['id'])]
    customer = s.company.conn.execute(sa.select(c.customers.c.full_name).where(
        c.customers.c.id == header['customer_id'])).scalar_one_or_none()
    next_commands = []
    preference = s.company.conn.execute(sa.select(c.company_info.c.invoice_when_shipping)).scalar()
    if rev['status'] == 'shipped' and preference != 'never' and any(Fraction(line.not_invoiced) > 0 for line in lines):
        next_commands.append(f'bookflow shipment invoice {header["number"]} --json')
    from bookflow.company.shipment_models import ShipmentReturnRef
    returns = [ShipmentReturnRef(return_id=row['id'], number=row['number'], date=row['return_date'],
                                 status=row['status'], reopen=bool(row['reopen']), quantity=_plain(row['quantity']))
               for row in _rows(s, """SELECT h.id, h.number, h.return_date, h.reopen, cur.status,
                       (SELECT sum(l.quantity_microunits) FROM shipment_return_lines l WHERE l.return_id = h.id) AS quantity
                   FROM shipment_returns h JOIN shipment_return_revisions cur ON cur.return_id = h.id
                   AND cur.revision_number = (SELECT max(x.revision_number) FROM shipment_return_revisions x
                                              WHERE x.return_id = h.id)
                   WHERE h.shipment_id = :id ORDER BY h.return_date, h.number""", {'id': header['id']})]
    return ShipmentOutput(
        id=header['id'], number=header['number'], version=rev['revision_number'], status=rev['status'],
        sales_order_id=order['id'], sales_order_number=order['number'], customer_id=header['customer_id'],
        customer_name=customer, date=rev['ship_date'], count_position=rev['count_position'], site_id=rev['site_id'],
        site_name=placements.site_names(s).get(rev['site_id']), ship_method_id=rev['ship_method_id'],
        carrier=rev['carrier'], tracking_numbers=json.loads(rev['tracking_numbers']), memo=rev['memo'],
        transaction_id=header['transaction_id'], lines=lines, cost=_money(total, currency),
        shipped_not_invoiced=_money(waiting_total, currency), invoices=invoices, returns=returns, order_to_ship=after,
        history=history, packing=_packing_output(s, rev), freight=_freight_output(s, header['id'], rev, currency),
        warnings=list(dict.fromkeys(warnings)), next_commands=next_commands)


def _packing_output(s, rev):
    from bookflow.company import shipment_packing as packing
    boxes = packing.load(s, rev['id'])
    if not boxes and rev['status'] != 'shipped':
        return None
    if not boxes and not packing.enabled(s):
        return None
    return packing.output(s, boxes, packing.shipment_lines(s, rev['id']), show_empty=True)


def prepare(s, ctx, inp, operation):
    output, _, _ = _run(s, ctx, inp, operation, persist=False)
    return Plan(output, {'input': inp, 'operation': operation})


def apply(plan, ctx, s):
    output, touched, summary = _run(s, ctx, plan.data['input'], plan.data['operation'], persist=True)
    return Applied(output, touched, summary, audited=True)


def show(s, inp):
    try:
        found = resolve(s, inp.shipment)
    except BookflowError as error:
        # A draft (R181 increment 4) shows until it ships; the shipment keeps its number.
        from bookflow.company import shipment_drafts
        if error.code != 'E_RECORD_NOT_FOUND':
            raise
        return shipment_drafts.show(s, inp.shipment)
    return show_output(s, found['id'])


def query(s, inp):
    from bookflow.company import work
    params = {}
    where = ['1 = 1']
    if inp.sales_order is not None:
        where.append('h.sales_order_id = :order')
        params['order'] = work.resolve(s, inp.sales_order, 'sales_order')['id']
    if inp.customer is not None:
        from bookflow.company import sales_defaults
        where.append('h.customer_id = :customer')
        params['customer'] = sales_defaults._row(s.company, 'customer', inp.customer, active=False)['id']
    if inp.status is not None:
        where.append('cur.status = :status')
        params['status'] = inp.status
    if inp.date_from is not None:
        where.append('cur.ship_date >= :date_from')
        params['date_from'] = inp.date_from
    if inp.date_to is not None:
        where.append('cur.ship_date <= :date_to')
        params['date_to'] = inp.date_to
    if inp.status in ('draft', 'cancelled') or (inp.status is None and not inp.not_invoiced):
        drafts = _draft_rows(s, inp, where, params)
    else:
        drafts = []
    if inp.status in ('draft', 'cancelled') or not has_shipments(s):
        return ShipmentListOutput(rows=drafts[:inp.limit], total=len(drafts))
    found = _rows(s, f"""SELECT h.*, cur.revision_number, cur.status, cur.ship_date, cur.site_id, cur.tracking_numbers,
            cur.id AS revision_id, w.number AS order_number, cu.full_name AS customer_name
        FROM shipments h JOIN ({_CURRENT}) cur ON cur.shipment_id = h.id
        JOIN work_documents w ON w.id = h.sales_order_id
        LEFT JOIN customers cu ON cu.id = h.customer_id
        WHERE {' AND '.join(where)} ORDER BY cur.ship_date DESC, h.number DESC""", params)
    currency = _home(s)
    sites = placements.site_names(s)
    rows = []
    for row in found:
        lines = _revision_lines(s, row['revision_id'])
        identities = [line['line_identity'] for line in lines]
        state = balances(s, identities)
        cost = sum(b.at(b.issued, '9999-12-31') for b in state.values())
        waiting = sum(b.waiting('9999-12-31') for b in state.values())
        if inp.not_invoiced and not (row['status'] == 'shipped' and any(
                b.out('9999-12-31') > min(b.at(b.billed, '9999-12-31'), b.out('9999-12-31')) for b in state.values())):
            continue
        rows.append(ShipmentListRow(
            id=row['id'], number=row['number'], version=row['revision_number'], status=row['status'], date=row['ship_date'],
            sales_order_number=row['order_number'], customer_name=row['customer_name'], site_name=sites.get(row['site_id']),
            quantity=_plain(sum(int(line['quantity_microunits']) for line in lines) if row['status'] == 'shipped' else 0),
            cost=_money(cost, currency), shipped_not_invoiced=_money(waiting, currency),
            tracking_numbers=json.loads(row['tracking_numbers'])))
    rows = drafts + rows
    return ShipmentListOutput(rows=rows[:inp.limit], total=len(rows))


def _draft_rows(s, inp, where, params):
    """Draft shipments (R181 increment 4) as list rows: open ones unless the status names cancelled."""
    from bookflow.company import shipment_packing as packing
    clauses = [w.replace('cur.status', 'h.status').replace('cur.ship_date', 'h.ship_date') for w in where]
    params = dict(params, status=inp.status or 'draft')
    if inp.status is None:
        clauses.append('h.status = :status')
    found = _rows(s, f"""SELECT h.*, w.number AS order_number, cu.full_name AS customer_name
        FROM ({packing._CURRENT_DRAFT}) h JOIN work_documents w ON w.id = h.sales_order_id
        LEFT JOIN customers cu ON cu.id = h.customer_id
        WHERE {' AND '.join(clauses)} ORDER BY h.number DESC""", params)
    zero = _money(0, _home(s))
    sites = placements.site_names(s)
    return [ShipmentListRow(id=row['id'], number=row['number'], version=row['revision_number'], status=row['status'],
                            date=row['ship_date'] or '', sales_order_number=row['order_number'],
                            customer_name=row['customer_name'], site_name=sites.get(row['site_id']),
                            quantity=_plain(sum(int(line['quantity_microunits']) for line in packing.draft_lines(s, row['id']))),
                            cost=zero, shipped_not_invoiced=zero,
                            tracking_numbers=json.loads(row['tracking_numbers'])) for row in found]


def account_balance(s, account_id, as_of):
    return int(s.company.conn.execute(sa.text(
        'SELECT coalesce(sum(l.debit_minor_units - l.credit_minor_units), 0) FROM posting_lines l '
        'JOIN posting_batches b ON b.id = l.batch_id WHERE l.account_id = :account AND b.effective_date <= :as_of'),
        {'account': account_id, 'as_of': as_of}).scalar_one())


def report(s, inp):
    """Shipped Not Invoiced by customer, order, shipment and line on a date; refuses if it does not tie."""
    as_of = inp.as_of or clock.today_iso()
    currency = _home(s)
    account = find_account(s.company)
    from bookflow.company import ledger_reports as ledger
    if account is None or not has_shipments(s):
        return ShippedNotInvoicedOutput(**ledger.page_of(s, inp, 'shipped-not-invoiced', [], date_to=as_of), as_of=as_of,
                                        account_id=None, account_name=None, total=_money(0, currency),
                                        account_balance=_money(0, currency))
    customer = None
    if inp.customer is not None:
        from bookflow.company import sales_defaults
        customer = sales_defaults._row(s.company, 'customer', inp.customer, active=False)['id']
    found = balances(s, all_identities(s))
    total = 0
    rows = []
    orders = dict(s.company.conn.execute(sa.text(
        'SELECT h.id, w.number FROM shipments h JOIN work_documents w ON w.id = h.sales_order_id')).all())
    names = dict(s.company.conn.execute(sa.select(c.customers.c.id, c.customers.c.full_name)).all())
    items = dict(s.company.conn.execute(sa.select(c.items.c.id, c.items.c.full_name)).all())
    for identity, balance in found.items():
        waiting = balance.waiting(as_of)
        total += waiting
        row = balance.row
        dates = [when for when, _ in balance.cost]
        ship_date = min(dates) if dates else as_of
        out = balance.out(as_of)
        billed = min(balance.at(balance.billed, as_of), max(out, 0))
        quantity = out - billed if ship_date <= as_of else 0
        if not waiting and not quantity:
            continue
        if customer is not None and row['customer_id'] != customer:
            continue
        rows.append(ShippedNotInvoicedRow(
            customer_id=row['customer_id'], customer_name=names.get(row['customer_id']),
            sales_order_number=orders.get(row['shipment_id']), shipment_number=row['number'],
            shipment_id=row['shipment_id'], ship_date=ship_date, line_identity=identity,
            item_name=items.get(row['item_id'], row['item_id']), quantity=_plain(quantity), cost=_money(waiting, currency)))
    balance_now = account_balance(s, account['id'], as_of)
    if total != balance_now:
        raise BookflowError('E_INTERNAL', message=(
            f'Shipped Not Invoiced lists {Money(total, currency)} waiting on {as_of} but the account holds '
            f'{Money(balance_now, currency)}; the two must agree.'))
    positions = {identity: int(balance.row['position']) for identity, balance in found.items()}
    rows.sort(key=lambda r: (r.customer_name or '', r.ship_date, r.shipment_number, positions[r.line_identity],
                             r.line_identity))
    return ShippedNotInvoicedOutput(**ledger.page_of(s, inp, 'shipped-not-invoiced', rows, date_to=as_of), as_of=as_of,
                                    account_id=account['id'], account_name=account['full_name'],
                                    total=_money(total, currency),
                                    account_balance=_money(balance_now, currency))


def guard_invoice_lines(s, billed, pending):
    """An invoice correction keeps the quantity of each line billing shipped units, or removes the line."""
    profiles = {row['document_line_id']: row for row in pending['sales_line_profiles']}
    for envelope in pending['document_lines']:
        quantity = billed.get(envelope['line_id'])
        if quantity is None:
            continue
        profile = profiles.get(envelope['id'])
        if profile is None or int(profile['base_quantity_microunits'] or 0) != quantity:
            raise BookflowError('E_VALIDATION', message=(
                f'Line {envelope["position"]} bills {format_quantity_micro_units(quantity)} shipped units; keep that '
                'quantity, or remove the line (its units go back to shipped and not invoiced) and bill again.'),
                details={'fields': [{'field': f'lines.{envelope["position"] - 1}.quantity',
                                     'problem': 'a line billing shipped units keeps its quantity'}],
                         'reason': 'shipment_billing_quantity'})


def billed_issue(s, transaction_id, document_line_id, intervals):
    """The shipment issue a credit memo returning units of an invoice line gives back, or None when the line billed no
    shipment (R181 increment 2, design §6: its place, numbers and cost come from the shipment line it billed).

    An invoice line's billings cover it in the order they were made: the first billing its units [0, q1), the next
    [q1, q1 + q2), and so on. A return takes intervals of the line (``credit_returns.take``); each must fall within
    one billing, so one credited line names one shipment issue."""
    identity = s.company.conn.execute(sa.select(c.document_lines.c.line_id).where(
        c.document_lines.c.id == document_line_id)).scalar()
    if identity is None or not has_shipments(s):
        return None
    rows = _rows(s, """SELECT b.shipment_line_identity, b.quantity_microunits, h.number FROM shipment_billings b
        JOIN shipments h ON h.id = b.shipment_id WHERE b.transaction_id = :tx AND b.line_id = :line
        ORDER BY b.rowid""", {'tx': transaction_id, 'line': identity})
    if not rows:
        return None
    spans, low = [], 0
    for row in rows:
        spans.append((low, low + int(row['quantity_microunits']), row))
        low += int(row['quantity_microunits'])
    hit = []
    for start, end in intervals:
        for low, high, row in spans:
            if start < high and end > low and row not in hit:
                hit.append(row)
    if not hit:
        hit = [spans[0][2]]
    if len(hit) > 1:
        sizes = ', '.join(f'{_plain(high - low)} from shipment {row["number"]}' for low, high, row in spans)
        raise _invalid('lines', 'these units were billed from more than one shipment line', message=(
            f'This invoice line billed {sizes}; one credit memo line returns units of one of them. Return them on '
            'separate lines, each no more than one shipment\'s units, or return fewer.'),
            reason='return_spans_shipments', shipments=[row['number'] for row in hit])
    current = _current_quantities(s, [hit[0]['shipment_line_identity']]).get(hit[0]['shipment_line_identity'])
    movement_id = current[1].get('movement_id') if current and current[0] else None
    if movement_id is None:
        raise _invalid('lines', 'the shipment this invoice line billed is no longer shipped')
    return dict(s.company.conn.execute(sa.select(c.inventory_movements).where(
        c.inventory_movements.c.id == movement_id)).mappings().one())


def check_invoice_date(s, transaction_id, line_ids, date):
    """An invoice correction may not date the invoice before a shipment its remaining lines bill (R181 §3)."""
    rows = s.company.conn.execute(sa.text(
        'SELECT DISTINCT h.number, r.ship_date FROM shipment_billings b JOIN shipments h ON h.id = b.shipment_id '
        'JOIN shipment_revisions r ON r.shipment_id = h.id WHERE b.transaction_id = :tx AND r.revision_number = '
        '(SELECT max(x.revision_number) FROM shipment_revisions x WHERE x.shipment_id = h.id) '
        f"AND b.line_id IN ({_marks('ln', list(line_ids), params := {'tx': transaction_id})})"), params).all()
    later = sorted((ship_date, number) for number, ship_date in rows if ship_date > date)
    if later:
        ship_date, number = later[-1]
        raise BookflowError('E_VALIDATION', message=(
            f'Shipment {number} shipped on {ship_date}, after the invoice date {date}; an invoice may not bill goods '
            'before they ship. Date it on or after the ship date, or remove that line.'),
            details={'fields': [{'field': 'date', 'problem': 'before the ship date of a shipment it bills'}],
                     'ship_date': ship_date})
