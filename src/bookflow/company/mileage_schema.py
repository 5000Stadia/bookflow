"""Vehicles, mileage rates, trips and mileage reimbursements (co0083, R118).

A trip is a record of miles driven in a company vehicle: which vehicle, when the trip started and
ended, the odometer readings or just the total miles, and optionally the customer:job it was for,
the item it is billed as, a class and notes. Recording a trip posts nothing, as the anchor's Enter
Vehicle Mileage posts nothing.

A billable trip is billed the way recorded time is: through a one-line customer-work document of
kind ``mileage`` that the trip keeps current (``work_document_id``). Its single line is the trip's
miles at the mileage item's sales price, so the interval ledger ``work_billing_allocations`` is what
makes a billable trip billable exactly once, and ``report unbilled-costs`` lists it with the rest.

The tax-deduction figure is miles x the mileage rate in effect on the trip's end date, read from
``mileage_rates`` by every report; it is never stored on the trip, so a rate entered later for an
earlier year is what the reports use.

``mileage_reimbursements`` links a trip to the check that paid somebody back for it. A link stands
while its check is posted: voiding the check frees the trip to be reimbursed again, and nothing here
is ever rewritten.
"""
import sqlalchemy as sa

TRIP_STATUSES = ('recorded', 'voided')


def define_tables(metadata, C, T, common):
    vehicles = T('vehicles',
        *common(),
        C('name', sa.String(200), 'Vehicle name as entered, such as Ford Transit 2019 White.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('description', sa.String(256), 'Make, model, plate or VIN, as the person wrote it.', nullable=True),
        C('active', sa.Boolean, 'Whether new trips may name this vehicle.', nullable=False, default=True),
        sa.UniqueConstraint('name_key', name='uq_vehicle_name_key'),
        sa.CheckConstraint('length(trim(name)) > 0', name='ck_vehicle_name_nonblank'),
        sa.CheckConstraint('active IN (0,1)', name='ck_vehicle_active'),
        description='Vehicle List: the company vehicles whose trips are recorded.')

    rates = T('mileage_rates',
        *common(),
        C('effective_date', sa.String(10), 'First date, YYYY-MM-DD, this rate applies to; it holds until the next rate.', nullable=False),
        C('rate', sa.String(16), 'Deduction rate per mile in the home currency, canonical decimal text such as 0.655.', nullable=False),
        sa.UniqueConstraint('effective_date', name='uq_mileage_rate_date'),
        sa.CheckConstraint('length(effective_date) = 10', name='ck_mileage_rate_date'),
        description='Mileage rates by effective date, used for the mileage deduction and reimbursements.')

    trips = T('mileage_trips',
        *common(),
        C('vehicle_id', sa.String(26), 'Vehicle driven.', sa.ForeignKey('vehicles.id'), nullable=False),
        C('trip_start_date', sa.String(10), 'Date the trip started, YYYY-MM-DD.', nullable=False),
        C('trip_end_date', sa.String(10), 'Date the trip ended, YYYY-MM-DD; the mileage rate in effect on it applies.', nullable=False),
        C('odometer_start_microunits', sa.BigInteger, 'Odometer reading at the start, in millionths of a mile; null when only total miles were entered.', nullable=True),
        C('odometer_end_microunits', sa.BigInteger, 'Odometer reading at the end, in millionths of a mile; null when only total miles were entered.', nullable=True),
        C('miles_microunits', sa.BigInteger, 'Total miles driven in millionths; odometer end minus start when both are given.', nullable=False),
        C('billable', sa.Boolean, 'Whether the miles are billed to the customer:job.', nullable=False),
        C('customer_id', sa.String(26), 'Customer or job the trip was for; required when billable.', sa.ForeignKey('customers.id'), nullable=True),
        C('item_id', sa.String(26), 'Service or other-charge item the miles are billed as; required when billable.', sa.ForeignKey('items.id'), nullable=True),
        C('billing_rate', sa.String(32), 'Billing price per mile overriding the item price, canonical decimal text; null charges the item price.', nullable=True),
        C('class_id', sa.String(26), 'Class of the trip.', sa.ForeignKey('classes.id'), nullable=True),
        C('notes', sa.Text, 'Why the trip was made.', nullable=True),
        C('status', sa.String(16), 'recorded, or voided (withdrawn with a reason; kept readable).', nullable=False),
        C('void_reason', sa.String(140), 'Reason recorded when the trip was voided.', nullable=True),
        C('work_document_id', sa.String(26), 'Current one-line customer-work document of kind mileage that bills this trip; null when it is not billable.',
          sa.ForeignKey('work_documents.id'), nullable=True),
        sa.CheckConstraint('trip_start_date <= trip_end_date', name='ck_mileage_trip_dates'),
        sa.CheckConstraint("typeof(miles_microunits) = 'integer' AND miles_microunits > 0", name='ck_mileage_trip_miles'),
        sa.CheckConstraint('(odometer_start_microunits IS NULL) = (odometer_end_microunits IS NULL)', name='ck_mileage_trip_odometer_pair'),
        sa.CheckConstraint('odometer_start_microunits IS NULL OR (odometer_start_microunits >= 0 AND '
                           'odometer_end_microunits - odometer_start_microunits = miles_microunits)', name='ck_mileage_trip_odometer'),
        sa.CheckConstraint('billable IN (0,1) AND (billable = 0 OR (customer_id IS NOT NULL AND item_id IS NOT NULL))',
                           name='ck_mileage_trip_billable'),
        sa.CheckConstraint("status IN ('recorded','voided')", name='ck_mileage_trip_status'),
        sa.Index('ix_mileage_trips_vehicle', 'vehicle_id', 'trip_end_date'),
        sa.Index('ix_mileage_trips_customer', 'customer_id', 'trip_end_date'),
        sa.Index('ux_mileage_trips_work_document', 'work_document_id', unique=True,
                 sqlite_where=sa.text('work_document_id IS NOT NULL')),
        description='Trips driven in company vehicles; non-posting, optionally billable to a customer:job.')

    reimbursements = T('mileage_reimbursements',
        C('id', sa.String(26), 'Stable ULID of this reimbursement link.', primary_key=True),
        C('trip_id', sa.String(26), 'Trip paid back.', sa.ForeignKey('mileage_trips.id'), nullable=False),
        C('transaction_id', sa.String(26), 'Check that paid it; the link stands while that check is posted.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('miles_microunits', sa.BigInteger, 'Miles reimbursed, in millionths.', nullable=False),
        C('rate', sa.String(16), 'Rate per mile the trip was reimbursed at, canonical decimal text.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Amount reimbursed for this trip, in home-currency minor units.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the reimbursement was written.', nullable=False),
        C('created_by', sa.String(26), 'Principal who wrote it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was written through.', nullable=False),
        sa.UniqueConstraint('trip_id', 'transaction_id', name='uq_mileage_reimbursement_trip_check'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0", name='ck_mileage_reimbursement_amount'),
        sa.Index('ix_mileage_reimbursements_transaction', 'transaction_id'),
        description='Which check paid somebody back for which trip; immutable, standing while the check is posted.')
    return {'vehicles': vehicles, 'mileage_rates': rates, 'mileage_trips': trips,
            'mileage_reimbursements': reimbursements}
