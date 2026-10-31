"""The POS layer (R114 increment 4, design/point-of-sale.md §2, §9).

Point of sale is an optional layer on sales receipts: a company turns it on with ``company update --pos-enabled
true`` (Company settings, *Sales*: Use point of sale). While it is off every `pos*` command refuses with
E_FEATURE_DISABLED, no POS surface shows, and nothing else changes.

**Registers.** Turning POS on the first time makes register **Main**: the default inventory site, Undeposited Funds,
the company's cash, card and check payment methods (in that order), a new walk-in customer, the first bank account
for cash refunds, an 80 mm receipt and no shift counting. A register is not an account. Turning POS off keeps the
registers; turning it on again finds Main where it was.

**The walk-in customer.** A sale with no customer is recorded against its register's walk-in customer, so refunds,
reports and sales by customer work unchanged; the POS screen and the receipt never show it (K2). It is an ordinary
customer record, made with the register (Walk-in for Main, "Walk-in (<register>)" for another; a name already taken
gets a number), or an existing customer a register names.

**Who sells.** The capabilities are in the catalog (pos-v1). Before permissions are activated there are no grants, so
the roles decide: ringing up needs the standard role, as `pos.sell`'s defaults say, though its commands name the
`member` threshold a granted `readonly` clerk needs once activated.
"""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from bookflow.company import placements, schema as c
from bookflow.company.lists import normalize_display_name
from bookflow.company.pos_models import Named, PaymentMethodRef, RegisterOutput, RegisterPageOutput, RegisterWriteOutput
from bookflow.company.pos_schema import SETTING
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

FEATURE = 'pos'
MAIN = 'Main'
WALK_IN = 'Walk-in'
# The capabilities a seller holds (pos-v1); before activation a role at least this one holds them.
SELLER_CAPABILITIES = frozenset(('pos.sell', 'pos.void', 'pos.refund', 'pos.price-override'))
LEGACY_SELLER_ROLE = 'standard'
_METHOD_ORDER = {'cash': 0, 'credit_card': 1, 'debit_card': 1, 'check': 2}


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


# ---------------------------------------------------------------------------- the switch

def enabled(s) -> bool:
    row = s.company.conn.execute(sa.select(c.company_info.c[SETTING])).first()
    return bool(row and row[0])


def disabled_error() -> BookflowError:
    return BookflowError('E_FEATURE_DISABLED', message=(
        'Point of sale is off for this company: it is an optional layer on sales receipts. An admin turns it on in '
        'Company settings (Sales: Use point of sale), or with `bookflow company update --pos-enabled true`. Until '
        'then, record a sale with `sales-receipt post`.'),
        details=dict(feature=FEATURE, setting=SETTING, enable_command='company update'))


def require_enabled(s) -> None:
    if not enabled(s):
        raise disabled_error()


def require_seller(s) -> None:
    """Before activation the roles decide: a POS seller is standard or above (pos.sell's role defaults)."""
    from bookflow.hub import access
    from bookflow.hub.permission_access import activated
    if activated(s):
        return
    acc, role = access.company_role(s, s.company_row['id'], s.company_row['organization_id'])
    if not access.role_satisfies(role, acc, LEGACY_SELLER_ROLE, s.is_hub_admin):
        raise BookflowError('E_PERMISSION', details={'capability': 'pos.sell', 'required_role': LEGACY_SELLER_ROLE,
                                                     'role': role})


def legacy_required_role(cmd) -> str | None:
    """The role a command needs before activation: a POS seller command needs standard (pos.sell's role defaults),
    every other its own threshold. The pages, `user home set` and the commands all ask this."""
    from bookflow.hub.capabilities import pos_capability
    return LEGACY_SELLER_ROLE if pos_capability(cmd) in SELLER_CAPABILITIES else cmd.required_role


def check_turning_off(s) -> None:
    """Turning POS off is refused (E_WORK_DEPENDENCY) while a ticket is held or a shift is open. Neither exists yet:
    held tickets arrive with increment 7 and shifts with increment 10, each adding its check here."""
    return None


def plan_setup_on(s, ctx) -> Plan | None:
    """Turning POS on: register Main (and its walk-in customer), unless a register already exists."""
    if s.company.conn.execute(sa.select(c.pos_registers.c.id).limit(1)).first() is not None:
        return None
    from bookflow.company.pos_models import RegisterCreateInput
    return _plan_create(s, ctx, RegisterCreateInput(name=MAIN))


def write_setup(plan: Plan, ctx, s) -> list[Touched]:
    return _write(plan, ctx, s)


# ---------------------------------------------------------------------------- what registers point at

# (what is referenced, register column, JSON list?)
_REFERENCES = {
    'customer': (('walk_in_customer_id', False),),
    'account': (('deposit_account_id', False), ('refund_account_id', False)),
    'inventory_site': (('site_id', False),),
    'payment-method': (('payment_method_ids', True),),
}
_LABELS = {'walk_in_customer_id': 'walk-in customer', 'deposit_account_id': 'deposit account',
           'refund_account_id': 'refund account', 'site_id': 'inventory site', 'payment_method_ids': 'payment method'}


def registers_using(db, kind: str, record_id: str) -> list[tuple[str, str]]:
    """[(register name, what it uses the record as)] for every register pointing at the record."""
    raw = db.conn
    if raw.execute(sa.text("SELECT 1 FROM sqlite_schema WHERE name = 'pos_registers'")).first() is None:
        return []
    found = []
    for row in raw.execute(sa.select(c.pos_registers)).mappings():
        for column, many in _REFERENCES.get(kind, ()):
            value = row[column]
            if (record_id in json.loads(value)) if many and value else value == record_id:
                found.append((row['name'], _LABELS[column]))
    return found


def refuse_if_used(db, kind: str, record_id: str, act: str) -> None:
    """Deactivating or merging away a record a register points at is refused (E_RECORD_IN_USE), naming the register:
    change the register first (`pos-register update`)."""
    used = registers_using(db, kind, record_id)
    if used:
        names = '; '.join(f'register {name} uses it as its {what}' for name, what in used)
        raise BookflowError('E_RECORD_IN_USE', message=(
            f'This record cannot be {act}: {names}. Point the register at another one first '
            f'(`pos-register update`).'), details={'record_type': kind.replace('-', '_'), 'record_id': record_id,
                                                   'registers': [name for name, _ in used],
                                                   'reason': 'pos_register_reference'})


def refuse_walk_in_merge(db, merged, target) -> None:
    if merged.get('pos_walk_in') or target.get('pos_walk_in'):
        raise BookflowError('E_RECORD_IN_USE', message=(
            "A point-of-sale walk-in customer is never merged: its sales are a register's sales with no customer."),
            details={'record_type': 'customer', 'merged_id': merged['id'], 'survivor_id': target['id'],
                     'reason': 'pos_walk_in'})


def inactive_references(s, row) -> list[str]:
    """What a register points at that is no longer active, in words, for `pos-register show`."""
    out = []
    checks = ((c.customers, row['walk_in_customer_id'], 'walk-in customer'), (c.accounts, row['deposit_account_id'],
              'deposit account'), (c.accounts, row['refund_account_id'], 'refund account'),
              (c.inventory_sites, row['site_id'], 'inventory site'))
    for table, record_id, what in checks:
        if record_id is None:
            continue
        found = s.company.conn.execute(sa.select(table.c.active).where(table.c.id == record_id)).first()
        if found is not None and not found[0]:
            out.append(f'its {what} is inactive')
    ids = json.loads(row['payment_method_ids'])
    if ids:
        t = c.payment_methods
        names = [r[0] for r in s.company.conn.execute(sa.select(t.c.name).where(t.c.id.in_(ids),
                                                                             t.c.active.is_(False)))]
        out += [f'payment method {name} is inactive' for name in names]
    return out


# ---------------------------------------------------------------------------- defaults and lookups

def _undeposited(s) -> dict[str, Any]:
    row = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == 'undeposited_funds')
                                 ).mappings().first()
    if row is None:
        raise BookflowError('E_VALIDATION', message='The company has no Undeposited Funds account; name a bank '
                            'account as deposit_account.', details={'fields': [{'field': 'deposit_account',
                                                                                 'problem': 'required'}]})
    return dict(row)


def _first_bank(s) -> dict[str, Any] | None:
    t = c.accounts
    row = s.company.conn.execute(sa.select(t).where(t.c.type == 'bank', t.c.active.is_(True))
                                 .order_by(t.c.full_name_key, t.c.id).limit(1)).mappings().first()
    return dict(row) if row else None


def _default_methods(s) -> list[str]:
    t = c.payment_methods
    rows = s.company.conn.execute(sa.select(t.c.id, t.c.name_key, t.c.kind).where(
        t.c.active.is_(True), t.c.kind.in_(tuple(_METHOD_ORDER)))).all()
    return [row[0] for row in sorted(rows, key=lambda r: (_METHOD_ORDER[r[2]], r[1], r[0]))]


def _account(s, selector, field: str, *, bank_only: bool) -> dict[str, Any]:
    from bookflow.company.accounts import resolve_account
    row = resolve_account(s.company, selector, by_number=True)
    if not row.get('active'):
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': field, 'record_id': row['id']})
    if row['type'] != 'bank' and (bank_only or row.get('system_role') != 'undeposited_funds'):
        raise _invalid(field, 'must be a bank account' if bank_only else 'must be Undeposited Funds or a bank account')
    return row


def _method(s, selector) -> dict[str, Any]:
    from bookflow.company import list_service
    from bookflow.company.lists import get_list_definition
    row = list_service.resolve_selector(s.company, c.payment_methods, get_list_definition('payment-method'), selector)
    if not row.get('active'):
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'payment_methods', 'record_id': row['id']})
    return dict(row)


def _customer(s, selector) -> dict[str, Any]:
    from bookflow.company import parties
    row = parties.resolve_party(s.company, 'customer', selector)
    if not row.get('active'):
        raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'walk_in_customer', 'record_id': row['id']})
    return dict(row)


def _new_walk_in(s, ctx, register_name: str):
    """A planned walk-in customer: "Walk-in" for Main, "Walk-in (<register>)" for another; a taken name gets a
    number."""
    from bookflow.company import parties
    base = WALK_IN if register_name.casefold() == MAIN.casefold() else f'{WALK_IN} ({register_name})'[:190]
    for n in range(1, 100):
        name = base if n == 1 else f'{base} {n}'
        try:
            return parties.plan_party_create(s.company, 'customer', {'name': name, 'notes': (
                'Point of sale: the customer a sale with no customer is recorded against.')},
                actor_id=s.actor.id, via=ctx.interface.value)
        except BookflowError as exc:
            if exc.code != 'E_NAME_TAKEN':
                raise
    raise _invalid('walk_in_customer', 'name an existing customer; every Walk-in name is taken')


def _named(s, table, record_id, label='full_name') -> Named | None:
    if record_id is None:
        return None
    row = s.company.conn.execute(sa.select(table.c.id, table.c[label]).where(table.c.id == record_id)).first()
    return Named(id=row[0], name=row[1]) if row else None


def output(s, row, *, cls=RegisterOutput, walk_in_name: str | None = None, **extra):
    t = c.payment_methods
    ids = json.loads(row['payment_method_ids'])
    found = {r[0]: r for r in s.company.conn.execute(sa.select(t.c.id, t.c.name, t.c.kind).where(t.c.id.in_(ids)))}
    walk_in = _named(s, c.customers, row['walk_in_customer_id']) or Named(id=row['walk_in_customer_id'],
                                                                       name=walk_in_name or WALK_IN)
    return cls(id=row['id'], version=row['version'], name=row['name'],
               site=_named(s, c.inventory_sites, row['site_id'], 'name'),
               deposit_account=_named(s, c.accounts, row['deposit_account_id']),
               payment_methods=[PaymentMethodRef(id=i, name=found[i][1], kind=found[i][2]) for i in ids if i in found],
               walk_in_customer=walk_in, refund_account=_named(s, c.accounts, row['refund_account_id']),
               receipt_width=row['receipt_width'], receipt_message=row['receipt_message'],
               count_shifts=bool(row['count_shifts']), decoder_order=row['decoder_order'],
               warnings=_warnings(s, row), **extra)


def _warnings(s, row) -> list[str]:
    out = inactive_references(s, row)
    if row['refund_account_id'] is None:
        out.append('No bank account for cash refunds yet: set refund_account once the company has one.')
    return out


def resolve(s, selector) -> dict[str, Any]:
    t = c.pos_registers
    text = str(selector).strip()
    row = None
    if is_ulid(text):
        row = s.company.conn.execute(sa.select(t).where(t.c.id == normalize_ulid(text))).mappings().first()
    if row is None and text:
        _, key = normalize_display_name(text, field='register')
        row = s.company.conn.execute(sa.select(t).where(t.c.name_key == key)).mappings().first()
    if row is None:
        names = [r[0] for r in s.company.conn.execute(sa.select(t.c.name).order_by(t.c.name_key))]
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_register', 'selector': text,
                                                           'suggestions': names[:10]})
    return dict(row)


def _name(s, value, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(value, field='name')
    t = c.pos_registers
    clash = s.company.conn.execute(sa.select(t.c.id).where(t.c.name_key == key)).first()
    if clash is not None and clash[0] != own:
        raise BookflowError('E_NAME_TAKEN', details={'field': 'name', 'value': display})
    return display, key


# ---------------------------------------------------------------------------- plans

def _apply_fields(s, ctx, inp, row: dict[str, Any]) -> Any:
    """Resolve the supplied fields onto ``row``; returns a planned walk-in customer mutation, if one is made."""
    fields = inp.model_fields_set
    if 'site' in fields:
        row['site_id'] = placements.resolve_site(s, inp.site)['id'] if inp.site is not None else \
            placements.default_site_id(s)
    if 'deposit_account' in fields:
        row['deposit_account_id'] = (_account(s, inp.deposit_account, 'deposit_account', bank_only=False)['id']
                                     if inp.deposit_account is not None else _undeposited(s)['id'])
    if 'payment_methods' in fields:
        ids = [_method(s, x)['id'] for x in inp.payment_methods] if inp.payment_methods is not None else \
            _default_methods(s)
        if len(set(ids)) != len(ids):
            raise _invalid('payment_methods', 'a payment method is named twice')
        row['payment_method_ids'] = json.dumps(ids)
    if 'refund_account' in fields:
        row['refund_account_id'] = (_account(s, inp.refund_account, 'refund_account', bank_only=True)['id']
                                    if inp.refund_account is not None else None)
    if 'receipt_width' in fields:
        row['receipt_width'] = inp.receipt_width or 80
        if row['receipt_width'] not in (80, 58):
            raise _invalid('receipt_width', 'must be 80 or 58 (millimetres)')
    if 'receipt_message' in fields:
        row['receipt_message'] = (inp.receipt_message or '').strip() or None
    if 'count_shifts' in fields:
        row['count_shifts'] = bool(inp.count_shifts)
    if 'decoder_order' in fields:
        from bookflow.core import scan_decoders
        text = (inp.decoder_order or '').strip()
        row['decoder_order'] = scan_decoders.normalize(text, field='decoder_order') if text else None
    mutation = None
    if 'walk_in_customer' in fields and inp.walk_in_customer is not None:
        row['walk_in_customer_id'] = _customer(s, inp.walk_in_customer)['id']
    elif row.get('walk_in_customer_id') is None:
        mutation = _new_walk_in(s, ctx, row['name'])
        row['walk_in_customer_id'] = str(mutation.after['id'])
    return mutation


def _plan_create(s, ctx, inp) -> Plan:
    display, key = _name(s, inp.name)
    bank = _first_bank(s)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
               site_id=placements.default_site_id(s), deposit_account_id=_undeposited(s)['id'],
               payment_method_ids=json.dumps(_default_methods(s)), walk_in_customer_id=None,
               refund_account_id=bank['id'] if bank else None, receipt_width=80, receipt_message=None,
               count_shifts=False, decoder_order=None)
    mutation = _apply_fields(s, ctx, inp, row)
    walk_in_name = mutation.after['name'] if mutation is not None else None
    preview = output(s, row, cls=RegisterWriteOutput, walk_in_name=walk_in_name,
                     created_walk_in_customer=mutation is not None)
    return Plan(preview, dict(row=row, before=None, walk_in=mutation))


def plan_create(s, ctx, inp) -> Plan:
    require_enabled(s)
    return _plan_create(s, ctx, inp)


def plan_update(s, ctx, inp) -> Plan:
    require_enabled(s)
    old = resolve(s, inp.pos_register)
    if inp.expected_version is not None and inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'pos_register', 'record_id': old['id'], 'current_version': old['version'],
            'expected_version': inp.expected_version, 'updated_at': old['updated_at'],
            'updated_by': old['updated_by']})
    new = dict(old)
    if inp.name is not None:
        new['name'], new['name_key'] = _name(s, inp.name, old['id'])
    _apply_fields(s, ctx, inp, new)
    changed = any(new[k] != old[k] for k in new)
    if changed:
        new.update(version=old['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                   updated_via=ctx.interface.value)
    return Plan(output(s, new, cls=RegisterWriteOutput, changed=changed),
                dict(row=new, before=old if changed else None, walk_in=None, changed=changed))


def _write(plan: Plan, ctx, s) -> list[Touched]:
    row, before, mutation = plan.data['row'], plan.data['before'], plan.data.get('walk_in')
    touched = []
    if mutation is not None:
        from bookflow.company import parties
        parties.persist_party_mutation(s.company, mutation)
        # The walk-in customer a register makes carries the flag (never merged or edited in bulk; reports can group
        # it), written in its own creating transaction, before anything else can read it.
        s.company.conn.execute(c.customers.update().where(c.customers.c.id == str(mutation.after['id']))
                               .values(pos_walk_in=True))
        touched.append(Touched('customer', str(mutation.after['id']), 'create', None, 1,
                               dict(mutation.after_snapshot, pos_walk_in=True), db='company'))
    t = c.pos_registers
    if before is None:
        s.company.conn.execute(t.insert().values(**row))
        touched.append(Touched('pos_register', row['id'], 'create', None, 1, row, db='company'))
    else:
        if s.company.conn.execute(t.update().where(t.c.id == row['id'], t.c.version == before['version'])
                                  .values(**row)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': row['id']})
        touched.append(Touched('pos_register', row['id'], 'update', before['version'], row['version'], row, before,
                               db='company'))
    return touched


def apply(plan: Plan, ctx, s) -> Applied:
    row = plan.data['row']
    if plan.data['before'] is None and plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    touched = _write(plan, ctx, s)
    verb = 'added' if plan.data['before'] is None else 'updated'
    return Applied(plan.preview, touched, f"{verb} register {row['name']}")


def show(s, inp) -> RegisterOutput:
    require_enabled(s)
    require_seller(s)
    return output(s, resolve(s, inp.pos_register))


def list_registers(s, inp) -> RegisterPageOutput:
    require_enabled(s)
    require_seller(s)
    t = c.pos_registers
    rows = s.company.conn.execute(sa.select(t).order_by(t.c.name_key)).mappings().all()
    return RegisterPageOutput(items=[output(s, dict(row)) for row in rows])
