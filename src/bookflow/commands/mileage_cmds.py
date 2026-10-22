"""Mileage (R118): the Vehicle List, mileage rates, trips, their billing and reimbursement, and the mileage reports.

The work lives in ``company/mileage.py``. A billable trip is billed through the same work billing
path recorded time uses (``trip invoice``, ``trip sales-receipt``, ``trip billing``).
"""
from bookflow.company import mileage
from bookflow.company import mileage_models as m
from bookflow.company.billing_outputs import BillingOutput
from bookflow.company.sales_outputs import SalesWriteOutput
from bookflow.core.registry import Plan, command

_LIST_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_NAME_TAKEN', 'E_VERSION_CONFLICT']


def _write(name, description, inp, out, planner, applier, *, errors, positional=(), version=None,
           capability='customer-work', ledger=False):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability=capability, accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    if ledger:
        cmd.ledger = True
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=(), capability='customer-work'):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability=capability, positional=list(positional),
                   error_codes=errors)(planner)


# ---------------------------------------------------------------------------- vehicles

def plan_vehicle_create(inp, ctx, s) -> Plan:
    return mileage.plan_vehicle_create(s, ctx, inp)


def plan_vehicle_update(inp, ctx, s) -> Plan:
    return mileage.plan_vehicle_update(s, ctx, inp)


def plan_vehicle_show(inp, ctx, s) -> Plan:
    return Plan(mileage.vehicle_show(s, inp))


def plan_vehicle_list(inp, ctx, s) -> Plan:
    return Plan(mileage.vehicle_list(s, inp))


vehicle_create = _write(
    'vehicle create', "Add a vehicle to the Vehicle List, as the anchor's New Vehicle does: a unique name (such as"
    ' "Ford Transit 2019 White") and a description for make, model, plate or VIN. Trips are recorded against it.',
    m.VehicleCreateInput, m.VehicleWriteOutput, plan_vehicle_create, mileage.apply_vehicle,
    errors=['E_VALIDATION', 'E_NAME_TAKEN'])
vehicle_update = _write(
    'vehicle update', 'Rename a vehicle, change its description, or make it inactive (active false) so new trips'
    ' cannot name it; its trips stay as recorded.',
    m.VehicleUpdateInput, m.VehicleWriteOutput, plan_vehicle_update, mileage.apply_vehicle,
    errors=_LIST_ERRORS, positional=['vehicle'], version=('vehicle show', 'vehicle', 'version'))
vehicle_show = _read('vehicle show', 'Show a vehicle with its count of recorded trips and total miles.',
                     m.VehicleShowInput, m.VehicleOutput, plan_vehicle_show, errors=['E_RECORD_NOT_FOUND'],
                     positional=['vehicle'])
vehicle_list = _read('vehicle list', 'The Vehicle List: active vehicles by name (include_inactive for all), each'
                     ' with its recorded trips and total miles.', m.VehicleListInput, m.VehiclePageOutput,
                     plan_vehicle_list, errors=[])


# ---------------------------------------------------------------------------- rates

def plan_rate_set(inp, ctx, s) -> Plan:
    return mileage.plan_rate_set(s, ctx, inp)


def plan_rate_delete(inp, ctx, s) -> Plan:
    return mileage.plan_rate_delete(s, ctx, inp)


def plan_rate_list(inp, ctx, s) -> Plan:
    return Plan(mileage.rate_list(s, inp))


mileage_rate_set = _write(
    'mileage-rate set', "Set the mileage rate per mile from an effective date, as the anchor's Mileage Rates window"
    ' does (the standard rate changes each January). A rate holds from its date until the next one. Setting a date'
    ' that already has a rate replaces it. The rate in effect on a trip end date is the deduction rate on the mileage'
    ' reports and the default rate of `trip reimburse`; it is never stored on the trip.',
    m.MileageRateSetInput, m.MileageRateWriteOutput, plan_rate_set, mileage.apply_rate,
    errors=['E_VALIDATION', 'E_AMOUNT_PRECISION'])
mileage_rate_delete = _write(
    'mileage-rate delete', 'Remove the mileage rate that starts on a date; the earlier rate then holds until the'
    ' next one. Reimbursements already written keep the rate they were paid at.',
    m.MileageRateDeleteInput, m.MileageRateWriteOutput, plan_rate_delete, mileage.apply_rate,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'], positional=['effective_date'])
mileage_rate_list = _read('mileage-rate list', 'Mileage rates by effective date, newest first.',
                          m.MileageRateListInput, m.MileageRatePageOutput, plan_rate_list, errors=[])


# ---------------------------------------------------------------------------- trips

_TRIP_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                      'E_AMOUNT_PRECISION', 'E_PREVIEW_STALE']


def plan_trip_create(inp, ctx, s) -> Plan:
    return mileage.plan_trip(s, ctx, inp, 'create')


def plan_trip_update(inp, ctx, s) -> Plan:
    return mileage.plan_trip(s, ctx, inp, 'update')


def plan_trip_void(inp, ctx, s) -> Plan:
    return mileage.plan_trip(s, ctx, inp, 'void')


def plan_trip_show(inp, ctx, s) -> Plan:
    return Plan(mileage.trip_show(s, inp))


def plan_trip_query(inp, ctx, s) -> Plan:
    return Plan(mileage.trip_query(s, inp))


_VERSION = ('trip show', 'trip', 'version')

trip_create = _write(
    'trip create', "Record a trip driven in a company vehicle, as the anchor's Enter Vehicle Mileage does: the"
    ' vehicle, the trip start and end dates, and either both odometer readings (the miles are the difference) or'
    ' total_miles. A trip may name the customer:job it was for, a class and notes. billable true bills the miles to'
    ' that customer:job as a service or other-charge item (its price is the price per mile; billing_rate overrides it)'
    ' through `trip invoice`, exactly once, and lists it in `report unbilled-costs`. Recording a trip posts nothing.',
    m.TripCreateInput, m.TripWriteOutput, plan_trip_create, mileage.apply_trip, errors=_TRIP_WRITE_ERRORS)
trip_update = _write(
    'trip update', 'Correct a trip using its expected version; name only what changes. total_miles replaces the'
    ' odometer readings; odometer readings replace total_miles. Making a trip non-billable withdraws it from billing.'
    ' A billed trip cannot have its miles reduced or be made non-billable while an invoice carries it, and a'
    ' reimbursed trip cannot change its miles or end date while its check stands (E_WORK_DEPENDENCY says which).',
    m.TripUpdateInput, m.TripWriteOutput, plan_trip_update, mileage.apply_trip,
    errors=[*_TRIP_WRITE_ERRORS, 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY', 'E_REASON_REQUIRED'],
    positional=['trip'], version=_VERSION)
trip_void = _write(
    'trip void', 'Withdraw a trip with a required reason. It posted nothing, so nothing is reversed; it stays'
    ' readable with the reason, and leaves the mileage reports. Refused while an invoice carries its miles (void or'
    ' edit the invoice first) or a posted check paid it back (void the check first).',
    m.TripVoidInput, m.TripWriteOutput, plan_trip_void, mileage.apply_trip,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED', 'E_WORK_DEPENDENCY'],
    positional=['trip'], version=_VERSION)
trip_show = _read('trip show', 'Show a trip: vehicle, dates, odometer and miles, customer:job, item, class, notes,'
                  ' the deduction rate and amount on its end date, its billing status and invoices, and the check'
                  ' that paid it back.', m.TripShowInput, m.TripOutput, plan_trip_show,
                  errors=['E_RECORD_NOT_FOUND'], positional=['trip'])
trip_query = _read('trip query', 'Find trips by vehicle, customer:job, end-date range and billable, oldest first;'
                   ' unreimbursed true lists the trips not yet paid back, as `trip reimburse` takes them.',
                   m.TripQueryInput, m.TripPageOutput, plan_trip_query, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])


# ---------------------------------------------------------------------------- billing

_BILL_ERRORS = ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY', 'E_CONVERSION_KEY_REUSED',
                'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                'E_AMOUNT_PRECISION', 'E_PREVIEW_STALE', 'E_FEATURE_DISABLED']


def _billing(verb, model, description):
    destination = verb.replace('-', '_')
    read = verb == 'billing'

    def planner(inp, ctx, s):
        if read:
            return Plan(mileage.billing_read(s, ctx, inp))
        return mileage.plan_billing(s, ctx, inp, destination)
    planner.__name__ = planner.__qualname__ = 'plan_trip_' + destination
    cmd = command('trip ' + verb, scope='company', description=description, input_model=model,
                  output_model=BillingOutput if read else SalesWriteOutput,
                  writes=set() if read else {'company'}, required_role='member' if read else 'standard',
                  capability='ledger.read' if read else 'ledger.post', accepts_idempotency_key=not read,
                  positional=['trip'], version_source=None if read else _VERSION,
                  authorization='ledger and customer-work membership' if read else 'ledger.post and customer-work standard role',
                  error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE', 'E_WORK_DEPENDENCY'] if read else _BILL_ERRORS)(planner)
    cmd.resource_requirements = (('customer-work', 'member' if read else 'standard'),)
    if not read:
        from bookflow.company import billing
        cmd.ledger = True
        cmd.applier(billing.apply)
        cmd.replay = lambda inp, ctx, s, hit: mileage.replay_billing(s, ctx, inp, destination, hit)
    return cmd


trip_invoice = _billing('invoice', m.TripInvoiceInput,
    'Bill a billable trip: make an invoice for its miles at the mileage item price (or the trip billing_rate), the'
    " anchor's Add Time/Costs for mileage. expected_version is the trip's version. The trip's miles are billed exactly"
    ' once: a second invoice finds nothing left, and voiding the invoice releases them to be billed again. A'
    ' permanent conversion_key makes a retry return the same invoice. Selections and percent bill part of the miles.')
trip_sales_receipt = _billing('sales-receipt', m.TripSalesReceiptInput,
    'They paid on the spot: make a sales receipt for a billable trip, with the amount received, payment method and'
    ' deposit account. Billed exactly once, as `trip invoice` is.')
trip_billing = _billing('billing', m.TripBillingInput,
    "Show a billable trip's billing window: its miles, what is billed and remaining, and the invoices carrying it.")


# ---------------------------------------------------------------------------- reimbursement

def plan_trip_reimburse(inp, ctx, s) -> Plan:
    return mileage.plan_reimburse(s, ctx, inp)


trip_reimburse = _write(
    'trip reimburse', 'Pay somebody back for driving: write one check to an employee or vendor for the chosen trips,'
    ' at the mileage rate in effect on each trip end date (or rate), one expense line per trip on expense_account'
    ' with the trip class, drawn on a bank account. Dry-run is the preview. Each trip is linked to the check, so it'
    ' cannot be reimbursed twice while that check stands; voiding the check frees the trips. A date in a closed period'
    ' is refused.',
    m.TripReimburseInput, m.TripReimburseOutput, plan_trip_reimburse, mileage.apply_reimburse,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_WORK_DEPENDENCY', 'E_PERIOD_CLOSED',
            'E_AMOUNT_PRECISION', 'E_VALUE_RANGE', 'E_DUPLICATE_NUMBER', 'E_PREVIEW_STALE'],
    capability='ledger.post', ledger=True)


# ---------------------------------------------------------------------------- reports

def plan_mileage_by_vehicle(inp, ctx, s) -> Plan:
    return Plan(mileage.mileage_by_vehicle(s, inp))


def plan_mileage_by_job(inp, ctx, s) -> Plan:
    return Plan(mileage.mileage_by_job(s, inp))


report_mileage_by_vehicle = _read(
    'report mileage-by-vehicle', "Mileage by Vehicle, the anchor's tax documentation: for each vehicle, the trips and"
    ' miles ending in the period and the mileage deduction, miles x the mileage rate in effect on each trip end date'
    ' (rounded per trip, half to even). detail true lists each trip with its end date, miles, rate and amount. Miles on'
    ' a trip whose date has no rate are counted and reported as unrated_miles. Dates default to this calendar year.',
    m.MileageByVehicleInput, m.MileageReportOutput, plan_mileage_by_vehicle, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'],
    capability='reports')
report_mileage_by_job = _read(
    'report mileage-by-job', 'Mileage by Job: for each customer:job, the trips and miles ending in the period, the'
    ' billable miles, and the billable amount at the mileage item price. detail true lists each trip with its end'
    ' date, miles, billing status (unbilled, billed, not_billable), rate and billable amount.',
    m.MileageByJobInput, m.MileageReportOutput, plan_mileage_by_job, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'],
    capability='reports')

MILEAGE_COMMANDS = [vehicle_create, vehicle_update, vehicle_show, vehicle_list, mileage_rate_set, mileage_rate_delete,
                    mileage_rate_list, trip_create, trip_update, trip_void, trip_show, trip_query, trip_invoice,
                    trip_sales_receipt, trip_billing, trip_reimburse, report_mileage_by_vehicle, report_mileage_by_job]
