"""Mileage (R118): the Vehicle List, mileage rates, trips, trip billing and reimbursement.

The anchor's Enter Vehicle Mileage records a trip and posts nothing; this does the same. What a
trip is *for* is three separate things, each reached by its own path:

* **The tax deduction** is miles x the mileage rate in effect on the trip's end date. It is
  computed by every reader from ``mileage_rates`` and never stored, so a rate entered late for an
  earlier year is the rate the reports use.
* **Billing** a customer for the miles runs exactly as billing recorded time does. A billable trip
  keeps a one-line customer-work document of kind ``mileage`` current: the trip's customer:job, its
  end date, its class, and one line of the mileage item for its miles. ``trip invoice`` and
  ``trip sales-receipt`` are the work billing commands over that document, so the interval ledger
  decides what is left to bill and a trip is billed exactly once; ``report unbilled-costs`` lists it
  with the rest. The invoice is what posts.
* **Paying somebody back** for driving is a check, as the anchor's users write one: ``trip
  reimburse`` draws a check to an employee or vendor for chosen trips at the rate and links each
  trip to it. A trip linked to a posted check cannot be reimbursed again; voiding the check frees it.

A trip is a versioned record whose every change is audited with its before image. Its billing
document is written by the same command, inside the same transaction, through the work service, so
every rule about billed work -- a billed trip cannot be voided or have its miles reduced from under
its invoice -- is the rule billed time already obeys.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.ledger_reports import I64_MAX, money
from bookflow.company.lists import normalize_display_name
from bookflow.company.mileage_models import (
    MileageGroupRow, MileageRateOutput, MileageRatePageOutput, MileageRateWriteOutput, MileageReportOutput,
    MileageTripRow, ReimbursedTrip, TripOutput, TripPageOutput, TripReimburseOutput, TripWriteOutput,
    VehicleOutput, VehiclePageOutput, VehicleWriteOutput,
)
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units, round_ratio_half_even
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.money import Money, minor_units_of
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

KIND = 'mileage'
BILLABLE_ITEM_TYPES = ('service', 'other_charge')
MICRO = 1_000_000


def _invalid(field: str, problem: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def _row(s, table, *where) -> dict[str, Any] | None:
    found = s.company.conn.execute(sa.select(table).where(*where)).mappings().first()
    return dict(found) if found else None


def home_currency(s) -> str:
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _today(s) -> str:
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


def _version_check(record_type: str, row: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': record_type, 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': expected, 'updated_at': row['updated_at'], 'updated_by': row['updated_by']})


def _bumped(row: dict[str, Any], s, ctx) -> dict[str, Any]:
    return dict(row, version=row['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


# ---------------------------------------------------------------------------- numbers

def parse_miles(text, field: str, *, zero: bool = False) -> int:
    units = parse_quantity_micro_units(text, field=field)
    if units < 0 or (units == 0 and not zero):
        raise _invalid(field, 'must be above 0' if not zero else 'must not be negative')
    if units > I64_MAX // 1000:
        raise BookflowError('E_VALUE_RANGE', details={'field': field})
    return units


def miles_text(units: int | None) -> str | None:
    return None if units is None else format_quantity_micro_units(units)


def parse_rate(text: str, field: str = 'rate') -> str:
    """A rate per mile as canonical decimal text: at most four places, 0 through 1000."""
    try:
        number = Decimal(str(text).strip())
    except InvalidOperation:
        raise _invalid(field, 'a rate per mile such as 0.655') from None
    if not number.is_finite() or number < 0 or number > 1000:
        raise _invalid(field, 'a rate per mile from 0 through 1000')
    if -number.as_tuple().exponent > 4:
        raise BookflowError('E_AMOUNT_PRECISION', details={'field': field, 'maximum_decimal_places': 4})
    canonical = format(number.normalize(), 'f')
    if '.' not in canonical:
        canonical += '.00'
    elif len(canonical.split('.')[1]) < 2:
        canonical += '0'
    return canonical


def amount_for(miles_units: int, rate: str, currency: str) -> int:
    """Miles x rate per mile in minor units, rounded once, half to even."""
    exact = Fraction(miles_units, MICRO) * Fraction(Decimal(rate)) * 10 ** minor_units_of(currency)
    return round_ratio_half_even(exact.numerator, exact.denominator)


def rates(s) -> list[dict[str, Any]]:
    t = c.mileage_rates
    return [dict(r) for r in s.company.conn.execute(sa.select(t).order_by(t.c.effective_date)).mappings()]


def rate_on(table: list[dict[str, Any]], date: str) -> str | None:
    found = None
    for row in table:
        if row['effective_date'] <= date:
            found = row['rate']
        else:
            break
    return found


# ---------------------------------------------------------------------------- vehicles

def resolve_vehicle(s, selector: str, *, field: str = 'vehicle') -> dict[str, Any]:
    t = c.vehicles
    text = selector.strip()
    row = _row(s, t, t.c.id == normalize_ulid(text)) if is_ulid(text) else None
    if row is None and text:
        _, key = normalize_display_name(text, field=field)
        row = _row(s, t, t.c.name_key == key)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'vehicle', 'selector': selector, 'field': field})
    return row


def _vehicle_name(s, name: str, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    query = sa.select(c.vehicles.c.id).where(c.vehicles.c.name_key == key)
    if own:
        query = query.where(c.vehicles.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': 'vehicle', 'name': display, 'record_id': clash[0]})
    return display, key


def _vehicle_totals(s) -> dict[str, tuple[int, int]]:
    t = c.mileage_trips
    rows = s.company.conn.execute(sa.select(t.c.vehicle_id, sa.func.count(), sa.func.sum(t.c.miles_microunits))
                                  .where(t.c.status == 'recorded').group_by(t.c.vehicle_id))
    return {vehicle: (count, miles or 0) for vehicle, count, miles in rows}


def vehicle_output(row, totals=None, cls=VehicleOutput):
    count, miles = (totals or {}).get(row['id'], (0, 0))
    return cls(id=row['id'], version=row['version'], name=row['name'], description=row['description'],
               active=bool(row['active']), trip_count=count, total_miles=miles_text(miles))


def plan_vehicle_create(s, ctx, inp) -> Plan:
    display, key = _vehicle_name(s, inp.name)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
               description=(inp.description or '').strip() or None, active=True)
    return Plan(vehicle_output(row, cls=VehicleWriteOutput), dict(row=row, before=None))


def plan_vehicle_update(s, ctx, inp) -> Plan:
    old = resolve_vehicle(s, inp.vehicle)
    _version_check('vehicle', old, inp.expected_version)
    new = dict(old)
    if inp.name is not None:
        new['name'], new['name_key'] = _vehicle_name(s, inp.name, old['id'])
    if 'description' in inp.model_fields_set:
        new['description'] = (inp.description or '').strip() or None
    if inp.active is not None:
        new['active'] = inp.active
    changed = any(new[k] != old[k] for k in ('name', 'description', 'active'))
    if changed:
        new = _bumped(new, s, ctx)
    out = vehicle_output(new, _vehicle_totals(s), cls=VehicleWriteOutput).model_copy(update=dict(changed=changed))
    return Plan(out, dict(row=new, before=old if changed else None, changed=changed))


def apply_vehicle(plan: Plan, ctx, s) -> Applied:
    row, before = plan.data['row'], plan.data['before']
    if before is None and plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    if before is None:
        s.company.conn.execute(c.vehicles.insert().values(**row))
        return Applied(plan.preview, [Touched('vehicle', row['id'], 'create', None, 1, row, db='company')],
                       f"added vehicle {row['name']}")
    done = s.company.conn.execute(c.vehicles.update().where(c.vehicles.c.id == row['id'],
                                  c.vehicles.c.version == before['version']).values(**row)).rowcount
    if done != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': row['id']})
    return Applied(plan.preview, [Touched('vehicle', row['id'], 'update', before['version'], row['version'], row, before,
                                          db='company')], f"updated vehicle {row['name']}")


def vehicle_show(s, inp) -> VehicleOutput:
    return vehicle_output(resolve_vehicle(s, inp.vehicle), _vehicle_totals(s))


def vehicle_list(s, inp) -> VehiclePageOutput:
    t = c.vehicles
    query = sa.select(t).order_by(t.c.name_key, t.c.id)
    if not inp.include_inactive:
        query = query.where(t.c.active.is_(True))
    totals = _vehicle_totals(s)
    return VehiclePageOutput(vehicles=[vehicle_output(dict(r), totals) for r in s.company.conn.execute(query).mappings()])


# ---------------------------------------------------------------------------- rates

def _rate_output(row, currency, cls=MileageRateOutput, **extra):
    return cls(id=row['id'], version=row['version'], effective_date=row['effective_date'], rate=row['rate'],
               currency=currency, **extra)


def plan_rate_set(s, ctx, inp) -> Plan:
    rate = parse_rate(inp.rate)
    t = c.mileage_rates
    old = _row(s, t, t.c.effective_date == inp.effective_date)
    if old is None:
        row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()),
                   effective_date=inp.effective_date, rate=rate)
        changed = True
    else:
        changed = old['rate'] != rate
        row = _bumped(dict(old, rate=rate), s, ctx) if changed else old
    out = _rate_output(row, home_currency(s), MileageRateWriteOutput, changed=changed)
    return Plan(out, dict(row=row, before=old, changed=changed))


def plan_rate_delete(s, ctx, inp) -> Plan:
    t = c.mileage_rates
    old = _row(s, t, t.c.effective_date == inp.effective_date)
    if old is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'mileage_rate', 'selector': inp.effective_date,
                                                         'field': 'effective_date'})
    return Plan(_rate_output(old, home_currency(s), MileageRateWriteOutput, deleted=True),
                dict(row=old, before=old, changed=True, delete=True))


def apply_rate(plan: Plan, ctx, s) -> Applied:
    row, before, t = plan.data['row'], plan.data['before'], c.mileage_rates
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    if plan.data.get('delete'):
        s.company.conn.execute(t.delete().where(t.c.id == row['id']))
        return Applied(plan.preview, [Touched('mileage_rate', row['id'], 'delete', row['version'], None, None, row,
                                              db='company')], f"removed the mileage rate from {row['effective_date']}")
    if before is None:
        s.company.conn.execute(t.insert().values(**row))
        touched = Touched('mileage_rate', row['id'], 'create', None, 1, row, db='company')
    else:
        s.company.conn.execute(t.update().where(t.c.id == row['id']).values(**row))
        touched = Touched('mileage_rate', row['id'], 'update', before['version'], row['version'], row, before, db='company')
    return Applied(plan.preview, [touched], f"mileage rate {row['rate']} from {row['effective_date']}")


def rate_list(s, inp) -> MileageRatePageOutput:
    currency = home_currency(s)
    return MileageRatePageOutput(rates=[_rate_output(r, currency) for r in reversed(rates(s))])


# ---------------------------------------------------------------------------- trip reads

def resolve_trip(s, selector: str) -> dict[str, Any]:
    text = selector.strip()
    row = _row(s, c.mileage_trips, c.mileage_trips.c.id == normalize_ulid(text)) if is_ulid(text) else None
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'trip', 'selector': selector, 'field': 'trip'})
    return row


def _names(s, table, ids, column='full_name') -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {r[0]: r[1] for r in s.company.conn.execute(sa.select(table.c.id, table.c[column]).where(table.c.id.in_(ids)))}


def reimbursing_check(s, trip_id: str) -> str | None:
    """The posted check that paid this trip back, if one stands."""
    r, t = c.mileage_reimbursements, c.transactions
    return s.company.conn.execute(sa.select(r.c.transaction_id).join(t, t.c.id == r.c.transaction_id)
                                  .where(r.c.trip_id == trip_id, t.c.status == 'posted').limit(1)).scalar()


def billing_state(s, document_id: str | None) -> dict[str, Any]:
    """The billing state of a trip's billing document, read as its own billing window reads it."""
    if not document_id:
        return dict(status='not_billable', net=None, sales=[])
    from bookflow.company import billing_allocations as alloc, billing_queries as query, tax_policy, work
    header = work.resolve(s, document_id, KIND)
    if header['status'] == 'voided':
        return dict(status='not_billable', net=None, sales=[])
    revision = work.revision(s, header)
    identities = query.root_identities(s, header)
    policy = tax_policy.effective(work.facts(revision).profile)
    row = work.saved_lines(s, revision)[0]
    facts = work.line_facts(row)
    identity = identities[row['line_id']]
    root = (identity['root_document_id'], identity['root_line_id'])
    free_length, free_net, _ = query.free_amounts(s, root, facts, policy)
    used = alloc.active_totals(s, root)
    state = query.line_state(free_length, used['count'], facts.billable, free_net if facts.billable else 0)
    a = c.work_billing_allocations
    sales = sorted({r[0] for r in s.company.conn.execute(alloc.active_query([root]).with_only_columns(a.c.transaction_id))})
    return dict(status='not_billable' if state == 'nonbillable' else state, net=facts.net_minor_units, sales=sales,
                unit_price=facts.price_units, header=header, line_id=row['line_id'])  # R150: price units


def trip_output(s, row, *, cls=TripOutput, table=None, **extra) -> TripOutput:
    currency = home_currency(s)
    rate = rate_on(table if table is not None else rates(s), row['trip_end_date'])
    vehicle = _names(s, c.vehicles, [row['vehicle_id']], 'name')
    customers = _names(s, c.customers, [row['customer_id']])
    items = _names(s, c.items, [row['item_id']])
    billing = billing_state(s, row['work_document_id'])
    return cls(id=row['id'], version=row['version'], status=row['status'], vehicle_id=row['vehicle_id'],
               vehicle_name=vehicle.get(row['vehicle_id'], ''), trip_start_date=row['trip_start_date'],
               trip_end_date=row['trip_end_date'], odometer_start=miles_text(row['odometer_start_microunits']),
               odometer_end=miles_text(row['odometer_end_microunits']), miles=miles_text(row['miles_microunits']),
               billable=bool(row['billable']), customer_id=row['customer_id'],
               customer_name=customers.get(row['customer_id']), item_id=row['item_id'], item_name=items.get(row['item_id']),
               billing_rate=row['billing_rate'], class_id=row['class_id'], notes=row['notes'],
               void_reason=row['void_reason'], deduction_rate=rate,
               deduction=money(amount_for(row['miles_microunits'], rate, currency), currency) if rate else None,
               billing_document_id=row['work_document_id'], billing_status=billing['status'],
               billable_amount=money(billing['net'], currency) if billing['net'] is not None else None,
               invoices=billing['sales'], reimbursed_by=reimbursing_check(s, row['id']), **extra)


def trip_show(s, inp) -> TripOutput:
    return trip_output(s, resolve_trip(s, inp.trip))


def _trip_rows(s, *, vehicle=None, customer=None, date_from=None, date_to=None, billable=None, include_voided=False):
    t = c.mileage_trips
    query = sa.select(t).order_by(t.c.trip_end_date, t.c.id)
    if not include_voided:
        query = query.where(t.c.status == 'recorded')
    if vehicle:
        query = query.where(t.c.vehicle_id == vehicle)
    if customer:
        query = query.where(t.c.customer_id == customer)
    if date_from:
        query = query.where(t.c.trip_end_date >= date_from)
    if date_to:
        query = query.where(t.c.trip_end_date <= date_to)
    if billable is not None:
        query = query.where(t.c.billable.is_(billable))
    return [dict(r) for r in s.company.conn.execute(query).mappings()]


def _customer_id(s, selector, field='customer'):
    from bookflow.company.parties import resolve_party
    try:
        return resolve_party(s.company, 'customer', selector)
    except BookflowError as exc:
        if exc.code == 'E_RECORD_NOT_FOUND':
            exc.details.setdefault('field', field)
        raise


def trip_query(s, inp) -> TripPageOutput:
    vehicle = resolve_vehicle(s, inp.vehicle)['id'] if inp.vehicle else None
    customer = _customer_id(s, inp.customer)['id'] if inp.customer else None
    rows = _trip_rows(s, vehicle=vehicle, customer=customer, date_from=inp.date_from, date_to=inp.date_to,
                      billable=inp.billable, include_voided=inp.include_voided)
    if inp.unreimbursed:
        rows = [r for r in rows if r['status'] == 'recorded' and reimbursing_check(s, r['id']) is None]
    try:
        start = int(inp.cursor) if inp.cursor else 0
    except ValueError:
        raise _invalid('cursor', 'use the next_cursor of the previous page') from None
    page = rows[start:start + inp.limit]
    table = rates(s)
    more = start + inp.limit < len(rows)
    return TripPageOutput(trips=[trip_output(s, r, table=table) for r in page],
                          next_cursor=str(start + inp.limit) if more else None)


# ---------------------------------------------------------------------------- trip writes

def _class_id(s, selector):
    from bookflow.company import list_service
    from bookflow.company.lists import get_list_definition
    try:
        return list_service.resolve_selector(s.company, c.classes, get_list_definition('class'), selector)
    except BookflowError as exc:
        if exc.code == 'E_RECORD_NOT_FOUND':
            exc.details.setdefault('field', 'class_id')
        raise


def _inactive(record_type, row):
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': record_type, 'record_id': row['id']})


def _resolved(s, inp, old: dict[str, Any] | None) -> dict[str, Any]:
    """The trip row this input makes, from the input and, for a correction, the saved trip."""
    given = inp.model_fields_set
    new = dict(old) if old else dict(customer_id=None, item_id=None, class_id=None, notes=None, billing_rate=None,
                                     odometer_start_microunits=None, odometer_end_microunits=None)
    if 'vehicle' in given or old is None:
        vehicle = resolve_vehicle(s, inp.vehicle)
        if old is None or vehicle['id'] != old['vehicle_id']:
            _inactive('vehicle', vehicle)
        new['vehicle_id'] = vehicle['id']
    if 'trip_end_date' in given or old is None:
        new['trip_end_date'] = inp.trip_end_date
    if 'trip_start_date' in given:
        new['trip_start_date'] = inp.trip_start_date or new['trip_end_date']
    elif old is None:
        new['trip_start_date'] = new['trip_end_date']
    if new['trip_start_date'] > new['trip_end_date']:
        raise _invalid('trip_start_date', 'the trip cannot start after it ends')
    odometer = given & {'odometer_start', 'odometer_end'}
    if odometer:
        start = parse_miles(inp.odometer_start, 'odometer_start', zero=True) if inp.odometer_start is not None else (
            None if 'odometer_start' in given else new['odometer_start_microunits'])
        end = parse_miles(inp.odometer_end, 'odometer_end', zero=True) if inp.odometer_end is not None else (
            None if 'odometer_end' in given else new['odometer_end_microunits'])
        if start is None and end is None:
            new['odometer_start_microunits'] = new['odometer_end_microunits'] = None
            if inp.total_miles is None and old is None:
                raise _invalid('total_miles', 'give total_miles, or both odometer readings')
        elif start is None or end is None:
            raise _invalid('odometer_end' if end is None else 'odometer_start', 'give both odometer readings, or total_miles')
        else:
            if end <= start:
                raise _invalid('odometer_end', 'must be above odometer_start')
            if inp.total_miles is not None and parse_miles(inp.total_miles, 'total_miles') != end - start:
                raise _invalid('total_miles', 'differs from odometer_end minus odometer_start; give one or the other')
            new.update(odometer_start_microunits=start, odometer_end_microunits=end, miles_microunits=end - start)
    if inp.total_miles is not None and not (odometer and new['odometer_start_microunits'] is not None):
        new.update(miles_microunits=parse_miles(inp.total_miles, 'total_miles'), odometer_start_microunits=None,
                   odometer_end_microunits=None)
    if old is None and new.get('miles_microunits') is None:
        raise _invalid('total_miles', 'give total_miles, or both odometer readings')
    if 'customer' in given or (old is None and inp.customer is not None):
        if inp.customer is None:
            new['customer_id'] = None
        else:
            customer = _customer_id(s, inp.customer)
            if old is None or customer['id'] != old['customer_id']:
                _inactive('customer', customer)
            new['customer_id'] = customer['id']
    if 'item' in given:
        if inp.item is None:
            new['item_id'] = None
        else:
            from bookflow.company.items import resolve_item
            item = resolve_item(s.company, inp.item)
            if item['type'] not in BILLABLE_ITEM_TYPES:
                raise _invalid('item', 'mileage is billed as a service or other-charge item')
            if old is None or item['id'] != old['item_id']:
                _inactive('item', item)
            new['item_id'] = item['id']
    if 'class_id' in given:
        new['class_id'] = _class_id(s, inp.class_id)['id'] if inp.class_id is not None else None
    if 'billing_rate' in given:
        rate = inp.billing_rate
        new['billing_rate'] = None if rate is None else (rate if isinstance(rate, str) else rate.amount).strip()
    if 'notes' in given:
        new['notes'] = (inp.notes or '').strip() or None
    if 'billable' in given or old is None:
        new['billable'] = bool(inp.billable)
    if new['billable'] and not new['customer_id']:
        raise _invalid('customer', 'a billable trip names the customer:job it is billed to')
    if new['billable'] and not new['item_id']:
        raise _invalid('item', 'a billable trip names the service or other-charge item it is billed as')
    return new


def _title(s, row) -> str:
    vehicle = _names(s, c.vehicles, [row['vehicle_id']], 'name').get(row['vehicle_id'], '')
    return ('Mileage — ' + vehicle)[:200]


def _description(s, row) -> str:
    if row['notes']:
        return row['notes'][:2000]
    span = row['trip_end_date'] if row['trip_start_date'] == row['trip_end_date'] else \
        f"{row['trip_start_date']} to {row['trip_end_date']}"
    return f"Mileage {span}: {miles_text(row['miles_microunits'])} miles"


def _line(s, row, previous_line_id=None):
    from bookflow.company.work_models import WorkLineInput
    values = dict(item=row['item_id'], quantity=miles_text(row['miles_microunits']), description=_description(s, row),
                  billable=True)
    if row['billing_rate'] is not None:
        values['unit_price'] = row['billing_rate']
    elif previous_line_id:
        values['use_defaults'] = ['unit_price']
    if previous_line_id:
        values['line_id'] = previous_line_id
    return WorkLineInput(**values)


def _companion(s, ctx, old: dict[str, Any] | None, new: dict[str, Any]):
    """The work write that keeps a trip's billing document in step with the trip, or None."""
    from bookflow.company import work
    from bookflow.company.work_models import _MileageWorkCreateInput, _MileageWorkUpdateInput, _MileageWorkVoidInput
    wanted = new['status'] == 'recorded' and new['billable']
    current = new.get('work_document_id')
    header = work.resolve(s, current, KIND) if current else None
    if header is not None and header['status'] == 'voided':
        header = None
    if not wanted:
        if header is None:
            return None
        return 'void', _MileageWorkVoidInput(mileage=header['id'], expected_version=header['version'])
    fields = dict(customer=new['customer_id'], date=new['trip_end_date'], title=_title(s, new), status='recorded')
    if new['class_id']:
        fields['class_id'] = new['class_id']
    if header is None:
        return 'create', _MileageWorkCreateInput(lines=[_line(s, new)], **fields)
    saved = work.saved_lines(s, work.revision(s, header))
    if new['class_id'] is None:
        fields['class_id'] = None
    return 'update', _MileageWorkUpdateInput(mileage=header['id'], expected_version=header['version'],
                                             lines=[_line(s, new, saved[0]['line_id'])], **fields)


def _reason(ctx, required: bool) -> str | None:
    reason = (ctx.reason or '').strip()
    if required and not reason:
        raise BookflowError('E_REASON_REQUIRED')
    if len(reason) > 140:
        raise _invalid('reason', 'must be at most 140 characters')
    return reason or None


def _work_ctx(ctx, operation, default_reason):
    if operation == 'void' and not (ctx.reason or '').strip():
        return ctx.model_copy(update={'reason': default_reason})
    return ctx


def _guard_reimbursed(s, old, new):
    check = reimbursing_check(s, old['id'])
    if check and (new['status'] != 'recorded' or new['miles_microunits'] != old['miles_microunits']
                  or new['trip_end_date'] != old['trip_end_date']):
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'problem': 'this trip was paid back by a posted check; void that check first, then correct or void the trip',
            'transaction_id': check, 'source_id': old['id']})


def _guard_billed(s, old, change):
    """A trip an invoice carries keeps what the invoice was billed on until the invoice lets go."""
    if old is None or change == 'update' or not old['work_document_id']:
        return
    sales = billing_state(s, old['work_document_id'])['sales']
    if sales:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'problem': 'an invoice or sales receipt carries this trip\'s miles; void or edit that invoice first, '
                       'then void or correct the trip (its vehicle, odometer readings and class can still change)',
            'source_id': old['id'], 'destination_id': sales[0]})


def plan_trip(s, ctx, inp, operation: str) -> Plan:
    from bookflow.company import work
    at = clock.now_iso()
    if operation == 'create':
        new = _resolved(s, inp, None)
        old = None
        new.update(id=new_id(), **common(s.actor.id, ctx.interface.value, at), status='recorded', void_reason=None,
                   work_document_id=None)
    else:
        old = resolve_trip(s, inp.trip)
        _version_check('trip', old, inp.expected_version)
        if old['status'] == 'voided':
            if operation == 'void':
                return Plan(trip_output(s, old, cls=TripWriteOutput, changed=False), dict(changed=False))
            raise _invalid('trip', 'a voided trip cannot be changed')
        if operation == 'void':
            reason = _reason(ctx, True)
            new = dict(old, status='voided', void_reason=reason)
        else:
            new = _resolved(s, inp, old)
        _guard_reimbursed(s, old, new)
    keys = [k for k in new if k not in ('version', 'created_at', 'created_by', 'created_via', 'updated_at', 'updated_by',
                                        'updated_via')]
    changed = old is None or any(new[k] != old[k] for k in keys)
    if not changed:
        return Plan(trip_output(s, old, cls=TripWriteOutput, changed=False), dict(changed=False))
    if old is not None:
        new = _bumped(new, s, ctx)
    step = _companion(s, ctx, old, new)
    warnings = []
    if step is not None:
        # Planned now, so whatever the work service would refuse -- an inactive item, a billed trip
        # whose miles would drop below what its invoice carries -- is refused at preview.
        kind_op, work_inp = step
        _guard_billed(s, old, kind_op)
        try:
            planned = work.prepare(s, _work_ctx(ctx, kind_op, 'trip no longer billable'), work_inp, KIND, kind_op)
        except BookflowError as exc:
            if exc.code == 'E_WORK_DEPENDENCY':
                _guard_billed(s, old, 'changed')
            raise
        if kind_op == 'update' and planned.data.get('changed'):
            _guard_billed(s, old, 'changed')
        warnings = list(getattr(planned.preview, 'warnings', []) or [])
    preview = trip_output(s, new, cls=TripWriteOutput, warnings=warnings)
    return Plan(preview, dict(changed=True, input=inp, operation=operation))


def apply_trip(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import work
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    inp, operation = plan.data['input'], plan.data['operation']
    # Rebuilt inside the writer transaction, as every write here is.
    at = clock.now_iso()
    if operation == 'create':
        old = None
        new = _resolved(s, inp, None)
        new.update(id=plan.preview.id, **common(s.actor.id, ctx.interface.value, at), status='recorded',
                   void_reason=None, work_document_id=None)
    else:
        old = resolve_trip(s, inp.trip)
        _version_check('trip', old, inp.expected_version)
        new = dict(old, status='voided', void_reason=_reason(ctx, True)) if operation == 'void' else _resolved(s, inp, old)
        _guard_reimbursed(s, old, new)
        new = _bumped(new, s, ctx)
    step = _companion(s, ctx, old, new)
    if step is not None:
        kind_op, work_inp = step
        _guard_billed(s, old, kind_op)
        work_ctx = _work_ctx(ctx, kind_op, 'trip no longer billable')
        written = work.apply(work.prepare(s, work_ctx, work_inp, KIND, kind_op), work_ctx, s)
        if kind_op == 'create':
            new['work_document_id'] = written.output.id
    t = c.mileage_trips
    if old is None:
        s.company.conn.execute(t.insert().values(**new))
        touched = [Touched('trip', new['id'], 'create', None, 1, new, db='company')]
    else:
        done = s.company.conn.execute(t.update().where(t.c.id == new['id'], t.c.version == old['version'])
                                      .values(**new)).rowcount
        if done != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': new['id']})
        touched = [Touched('trip', new['id'], 'update', old['version'], new['version'], new, old, db='company')]
    verb = {'create': 'recorded', 'update': 'corrected', 'void': 'voided'}[operation]
    summary = f"{verb} trip of {miles_text(new['miles_microunits'])} miles on {new['trip_end_date']}"
    audit.write_event_to(s.company, ctx, 'trip ' + operation, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    output = trip_output(s, new, cls=TripWriteOutput, warnings=plan.preview.warnings)
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------------------- billing

def _billing_input(s, inp, model):
    """The work billing input a trip billing request is: the trip's billing document at its version."""
    from bookflow.company import work
    trip = resolve_trip(s, inp.trip)
    if hasattr(inp, 'expected_version'):
        _version_check('trip', trip, inp.expected_version)
    if trip['status'] == 'voided':
        raise BookflowError('E_WORK_DEPENDENCY', details={'problem': 'a voided trip cannot be billed', 'source_id': trip['id']})
    if not trip['work_document_id']:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'problem': 'this trip is not billable; make it billable with `trip update` (billable true), naming a customer:job and item',
            'source_id': trip['id']})
    header = work.resolve(s, trip['work_document_id'], KIND)
    values = inp.model_dump(exclude={'trip'}, exclude_unset=True)
    values['mileage'] = header['id']
    if 'expected_version' in model.model_fields:
        # The billing document's version the request is made at. A retry under a permanent key that
        # already billed this document is the same request, made at the version it was made at, so
        # the conversion it wrote replays rather than reading as a different input.
        values['expected_version'] = header['version']
        key = hashlib.sha256(inp.conversion_key.encode()).hexdigest()
        w = c.work_billing_conversions
        born = s.company.conn.execute(sa.select(w.c.source_version).where(
            w.c.conversion_key_hash == key, w.c.source_document_id == header['id'])).scalar()
        if born is not None:
            values['expected_version'] = born
    return model.model_validate(values)


def plan_billing(s, ctx, inp, destination):
    from bookflow.company import billing
    from bookflow.company.billing_allocations import one_reading
    from bookflow.company.mileage_models import MileageInvoiceInput, MileageSalesReceiptInput
    model = MileageInvoiceInput if destination == 'invoice' else MileageSalesReceiptInput
    with one_reading(s):
        translated = _billing_input(s, inp, model)
        plan = billing.prepare(s, ctx, translated, KIND, destination)
    return plan


def billing_read(s, ctx, inp):
    from bookflow.company import billing_queries
    from bookflow.company.billing_allocations import one_reading
    from bookflow.company.mileage_models import MileageBillingInput
    with one_reading(s):
        return billing_queries.billing(s, ctx, _billing_input(s, inp, MileageBillingInput), KIND)


def replay_billing(s, ctx, inp, destination, hit):
    from bookflow.company import billing
    from bookflow.company.mileage_models import MileageInvoiceInput, MileageSalesReceiptInput
    model = MileageInvoiceInput if destination == 'invoice' else MileageSalesReceiptInput
    return billing.replay_conversion(s, ctx, _billing_input(s, inp, model), KIND, destination, hit)


# ---------------------------------------------------------------------------- reimbursement

def _reimbursement_rows(s, inp):
    currency = home_currency(s)
    override = parse_rate(inp.rate) if inp.rate is not None else None
    table = rates(s)
    vehicles = _names(s, c.vehicles, [], 'name')
    out = []
    for index, selector in enumerate(inp.trips):
        field = f'trips.{index}'
        try:
            trip = resolve_trip(s, selector)
        except BookflowError as exc:
            exc.details['field'] = field
            raise
        if trip['status'] != 'recorded':
            raise _invalid(field, 'a voided trip cannot be reimbursed')
        paid = reimbursing_check(s, trip['id'])
        if paid:
            raise BookflowError('E_WORK_DEPENDENCY', details={
                'problem': 'this trip was already paid back by a posted check; void that check to pay it again',
                'transaction_id': paid, 'source_id': trip['id'], 'field': field})
        rate = override or rate_on(table, trip['trip_end_date'])
        if rate is None:
            raise _invalid(field, f"no mileage rate is in effect on {trip['trip_end_date']}; set one with "
                                  '`mileage-rate set` or give rate')
        amount = amount_for(trip['miles_microunits'], rate, currency)
        if amount <= 0:
            raise _invalid(field, 'miles x rate is 0.00; nothing to pay back')
        vehicles.update(_names(s, c.vehicles, [trip['vehicle_id']], 'name'))
        out.append(dict(trip=trip, rate=rate, amount=amount, vehicle=vehicles.get(trip['vehicle_id'], '')))
    return out, currency


def _check_input(s, inp, rows, currency):
    from bookflow.company.check_models import CheckPostInput
    lines = []
    for row in rows:
        trip = row['trip']
        memo = (f"Mileage {trip['trip_end_date']} {row['vehicle']}: {miles_text(trip['miles_microunits'])} mi"
                f" @ {row['rate']}")[:2000]
        line = dict(account=inp.expense_account, amount=Money(row['amount'], currency).amount, memo=memo)
        if trip['class_id']:
            line['class_id'] = trip['class_id']
        lines.append(line)
    total = sum(row['amount'] for row in rows)
    values = dict(account=inp.account, pay_to=inp.pay_to.model_dump(), date=inp.date,
                  amount=Money(total, currency).amount, expenses=lines,
                  memo=inp.memo or f"Mileage reimbursement: {len(rows)} trip{'s' if len(rows) != 1 else ''}")
    if inp.number is not None:
        values['number'] = inp.number
    return CheckPostInput.model_validate(values), total


def _reimburse_fingerprint(inp, rows) -> str:
    facts = dict(pay_to=inp.pay_to.model_dump(), account=inp.account, expense_account=inp.expense_account, date=inp.date,
                 trips=[(r['trip']['id'], r['trip']['version'], r['rate'], r['amount']) for r in rows])
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _reimburse_output(inp, rows, currency, total, fingerprint, *, check_id=None, check_number=None, pay_to=''):
    return TripReimburseOutput(
        check_id=check_id, check_number=check_number, date=inp.date, pay_to=pay_to, total=money(total, currency),
        trips=[ReimbursedTrip(trip_id=r['trip']['id'], trip_end_date=r['trip']['trip_end_date'], vehicle_name=r['vehicle'],
                              miles=miles_text(r['trip']['miles_microunits']), rate=r['rate'],
                              amount=money(r['amount'], currency)) for r in rows],
        facts_fingerprint=fingerprint)


def plan_reimburse(s, ctx, inp) -> Plan:
    from bookflow.company import checks
    rows, currency = _reimbursement_rows(s, inp)
    fingerprint = _reimburse_fingerprint(inp, rows)
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'trip_reimbursement_facts',
                                                        'current_facts_fingerprint': fingerprint})
    check_inp, total = _check_input(s, inp, rows, currency)
    step = checks.prepare(s, ctx, check_inp, 'check', 'post')
    payee = getattr(step.preview, 'pay_to', None)
    name = getattr(payee, 'name', None) or getattr(payee, 'label', None) or inp.pay_to.name_id
    number = getattr(step.preview, 'check_number', None)
    return Plan(_reimburse_output(inp, rows, currency, total, fingerprint, check_number=number, pay_to=str(name)),
                dict(input=inp, fingerprint=fingerprint))


def apply_reimburse(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import checks
    inp = plan.data['input']
    rows, currency = _reimbursement_rows(s, inp)
    fingerprint = _reimburse_fingerprint(inp, rows)
    if fingerprint != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'trip_reimbursement_facts',
                                                        'current_facts_fingerprint': fingerprint})
    check_inp, total = _check_input(s, inp, rows, currency)
    written = checks.apply(checks.prepare(s, ctx, check_inp, 'check', 'post'), ctx, s).output
    at, records, touched = clock.now_iso(), [], []
    for row in rows:
        record = dict(id=new_id(), trip_id=row['trip']['id'], transaction_id=written.id,
                      miles_microunits=row['trip']['miles_microunits'], rate=row['rate'], amount_minor_units=row['amount'],
                      created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
        records.append(record)
        touched.append(Touched('mileage_reimbursement', record['id'], 'create', None, 1, record, db='company'))
    s.company.conn.execute(c.mileage_reimbursements.insert(), records)
    summary = f"reimbursed {len(records)} trip{'s' if len(records) != 1 else ''} by check {getattr(written, 'check_number', None) or written.id}"
    audit.write_event_to(s.company, ctx, 'trip reimburse', summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    output = plan.preview.model_copy(update=dict(check_id=written.id, check_number=getattr(written, 'check_number', None)))
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------------------- reports

def _period(s, inp) -> tuple[str, str]:
    date_to = inp.date_to or _today(s)
    date_from = inp.date_from or date_to[:4] + '-01-01'
    if date_from > date_to:
        raise _invalid('date_from', 'must not be after date_to')
    return date_from, date_to


def _detail_row(s, trip, names, currency, *, rate, amount, status=None) -> MileageTripRow:
    return MileageTripRow(trip_id=trip['id'], trip_start_date=trip['trip_start_date'], trip_end_date=trip['trip_end_date'],
                          vehicle_name=names['vehicles'].get(trip['vehicle_id'], ''),
                          customer_name=names['customers'].get(trip['customer_id']),
                          miles=miles_text(trip['miles_microunits']), billable=bool(trip['billable']), rate=rate,
                          amount=money(amount, currency) if amount is not None else None, billing_status=status,
                          notes=trip['notes'])


def mileage_by_vehicle(s, inp) -> MileageReportOutput:
    date_from, date_to = _period(s, inp)
    vehicle = resolve_vehicle(s, inp.vehicle)['id'] if inp.vehicle else None
    trips = _trip_rows(s, vehicle=vehicle, date_from=date_from, date_to=date_to)
    currency, table = home_currency(s), rates(s)
    names = dict(vehicles=_names(s, c.vehicles, [t['vehicle_id'] for t in trips], 'name'),
                 customers=_names(s, c.customers, [t['customer_id'] for t in trips]))
    groups: dict[str, dict[str, Any]] = {}
    for trip in trips:
        rate = rate_on(table, trip['trip_end_date'])
        amount = amount_for(trip['miles_microunits'], rate, currency) if rate else None
        g = groups.setdefault(trip['vehicle_id'], dict(trips=0, miles=0, amount=0, unrated=0, detail=[]))
        g['trips'] += 1
        g['miles'] += trip['miles_microunits']
        g['amount'] += amount or 0
        g['unrated'] += 0 if rate else trip['miles_microunits']
        if inp.detail:
            g['detail'].append(_detail_row(s, trip, names, currency, rate=rate, amount=amount))
    rows = [MileageGroupRow(key=key, name=names['vehicles'].get(key, ''), trips=g['trips'], miles=miles_text(g['miles']),
                            amount=money(g['amount'], currency), unrated_miles=miles_text(g['unrated']), detail=g['detail'])
            for key, g in sorted(groups.items(), key=lambda kv: (names['vehicles'].get(kv[0], '').casefold(), kv[0]))]
    return MileageReportOutput(report='mileage-by-vehicle-' + ('detail' if inp.detail else 'summary'), date_from=date_from,
                               date_to=date_to, currency=currency, rows=rows,
                               total_miles=miles_text(sum(t['miles_microunits'] for t in trips)),
                               total_amount=money(sum(g['amount'] for g in groups.values()), currency))


def mileage_by_job(s, inp) -> MileageReportOutput:
    """Miles per customer:job, billable miles, and the billable amount at the mileage item's price."""
    date_from, date_to = _period(s, inp)
    customer = _customer_id(s, inp.customer)['id'] if inp.customer else None
    trips = [t for t in _trip_rows(s, customer=customer, date_from=date_from, date_to=date_to) if t['customer_id']]
    currency = home_currency(s)
    names = dict(vehicles=_names(s, c.vehicles, [t['vehicle_id'] for t in trips], 'name'),
                 customers=_names(s, c.customers, [t['customer_id'] for t in trips]))
    groups: dict[str, dict[str, Any]] = {}
    for trip in trips:
        state = billing_state(s, trip['work_document_id'])
        amount = state['net'] if state['net'] is not None else None
        g = groups.setdefault(trip['customer_id'], dict(trips=0, miles=0, billable=0, amount=0, detail=[]))
        g['trips'] += 1
        g['miles'] += trip['miles_microunits']
        if trip['billable']:
            g['billable'] += trip['miles_microunits']
            g['amount'] += amount or 0
        if inp.detail:
            from bookflow.core import unit_price
            rate = unit_price.text(state['unit_price'], currency) if state.get('unit_price') is not None else None
            g['detail'].append(_detail_row(s, trip, names, currency, rate=rate, amount=amount, status=state['status']))
    rows = [MileageGroupRow(key=key, name=names['customers'].get(key, ''), trips=g['trips'], miles=miles_text(g['miles']),
                            billable_miles=miles_text(g['billable']), amount=money(g['amount'], currency), detail=g['detail'])
            for key, g in sorted(groups.items(), key=lambda kv: (names['customers'].get(kv[0], '').casefold(), kv[0]))]
    return MileageReportOutput(report='mileage-by-job-' + ('detail' if inp.detail else 'summary'), date_from=date_from,
                               date_to=date_to, currency=currency, rows=rows,
                               total_miles=miles_text(sum(t['miles_microunits'] for t in trips)),
                               total_amount=money(sum(g['amount'] for g in groups.values()), currency))
