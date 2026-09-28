"""A bill reads each billed root's allocation history a fixed number of times.

Billing work that has been billed in many installments used to rescan every root's whole
history once per internal question (selection, posting checks, the remaining-work forecast,
validation), so one bill cost history x questions and a 2000-span conversion took most of an
hour to build. One preparation now reads each root's history once, whatever the history's
length. This counts statements, not seconds: the counts are the same on any machine.
"""
from contextlib import contextmanager

import sqlalchemy as sa

from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY
from tests.test_customer_work_lifecycle import run
from tests.test_work_billing_lifecycle import accepted, bill


@contextmanager
def statements():
    seen = []
    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    sa.event.listen(sa.engine.Engine, 'before_cursor_execute', record)
    try:
        yield seen
    finally:
        sa.event.remove(sa.engine.Engine, 'before_cursor_execute', record)


def history_scans(seen):
    # The one statement that walks a root's allocation intervals.
    return sum('json_each' in statement and 'work_billing_allocations' in statement for statement in seen)


def test_bill_reads_each_root_history_a_fixed_number_of_times(client, tax_sale):
    lines, installments = 3, 12
    source = accepted(client, tax_sale, **dict(request(tax_sale), lines=[dict(item=tax_sale['item'],
        quantity=str(installments + 1), net_amount=f'0.{installments + 1:02d}', tax_code=tax_sale['taxable'])
        for _ in range(lines)]))
    ids = [row['line_id'] for row in source['revision']['lines']]
    counts = []
    for index in range(installments):
        with statements() as seen:
            bill(client, dict(source, version=2 + index), f'reads-{index}',
                 selections=[dict(line_id=key, quantity='1') for key in ids])
        counts.append((len(seen), history_scans(seen)))
    # Plan and apply each prepare the bill once, and each preparation scans a root once
    # (38 scans a root before the preparation shared its reads).
    assert all(scans <= 2 * lines for _, scans in counts), counts
    # Nothing in a bill grows with the history already billed.
    assert counts[1] == counts[-1], counts
    with statements() as seen:
        state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert history_scans(seen) <= lines, history_scans(seen)
    assert state['remaining_net_minor_units'] == lines and state['remaining_tax_minor_units'] == 0
