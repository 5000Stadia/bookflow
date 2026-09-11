"""Billing groups, and one batch of invoices checked figure by figure.

Every amount asserted here is written out in full so a reader can add it up without running
anything. The worked case is deliberately the one the feature is most likely to get wrong:

    Item "Monthly Service" is priced at 100.00.
    Ridgeway is on standard price and 15-day terms.
    Harbourfront is on the "Retainer discount" price level, ten percent off, and 30-day terms.

    One batch, one line, two invoices:  Ridgeway 100.00 due 2026-09-16
                                       Harbourfront  90.00 due 2026-10-01

If the batch resolved the customers' defaults once and stamped the result onto every invoice,
both invoices would read the same amount and the same due date, and every assertion below about
the second customer would fail.
"""

import pytest

import bookflow
from bookflow.core.errors import BookflowError

PRICE = '100.00'
STANDARD_UNITS = 10000        # what Ridgeway pays: the catalog price
PREFERRED_UNITS = 9000        # what Harbourfront pays: ten percent off 100.00
DATE = '2026-09-01'
RIDGEWAY_DUE = '2026-09-16'   # 15-day terms from 2026-09-01
HARBOURFRONT_DUE = '2026-10-01'  # 30-day terms from 2026-09-01

LINES = [{'item': 'Monthly Service', 'quantity': '1'}]


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A company of its own, so every figure reported here belongs only to this test."""
    data_root = tmp_path / 'batches'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Batch organization')
    company = client.company.new(legal_name='Batch', home_currency='USD', timezone='UTC',
                                 organization='Batch organization', chart='general')['company_id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    income = next(row['id'] for row in run('account query', {'limit': 200})['items']
                  if row['type'] == 'income')
    run('company update', {'enable_price_levels': True}, reason='Use price levels')
    net15 = run('term create', {'name': 'Retainer 15', 'kind': 'standard', 'due_days': 15},
                reason='Retainer terms')['id']
    net30 = run('term create', {'name': 'Retainer 30', 'kind': 'standard', 'due_days': 30},
                reason='Retainer terms')['id']
    preferred = run('price-level create', {'name': 'Retainer discount', 'kind': 'fixed_percent',
                                           'percent': '-10', 'rounding_mode': 'nearest',
                                           'rounding_increment': '0.01', 'rounding_offset': '0'},
                    reason='Retainer discount')['id']
    # Sales tax is off in this company, so the arithmetic below is exactly the catalog price:
    # what is being proved here is per-customer resolution, not tax, which has its own tests.
    # A service item still has to name a code, and a nontaxable one is the honest choice.
    nontaxable = next(row['id'] for row in run('sales-tax-code query', {'limit': 50})['items']
                      if not row['taxable'])
    run('item create', {'name': 'Monthly Service', 'type': 'service', 'sales_enabled': True,
                        'description': 'Monthly retainer', 'price': PRICE,
                        'income_account_id': income, 'sales_tax_code_id': nontaxable},
        reason='Retainer item')
    ridgeway = run('customer create', {
        'name': 'Ridgeway Estates', 'terms_id': net15,
        'billing_address': {'line1': '1 Ridgeway Lane', 'city': 'Springfield', 'country': 'US'},
    }, reason='Retainer customer')['id']
    harbour = run('customer create', {
        'name': 'Harbourfront Trust', 'terms_id': net30, 'price_level_id': preferred,
        'billing_address': {'line1': '9 Harbour Road', 'city': 'Northport', 'country': 'US'},
    }, reason='Retainer customer')['id']
    group = run('billing-group create', {'name': 'Monthly retainers',
                                         'customers': ['Ridgeway Estates', 'Harbourfront Trust']},
                reason='Set up the retainer round')['billing_group']['id']
    return dict(client=client, company=company, run=run, income=income, ridgeway=ridgeway,
                harbour=harbour, group=group, preferred=preferred, net15=net15, net30=net30)


def _rows(result):
    return {row['customer_label']: row for row in result['batch_rows']}


# ------------------------------------------------------------------ billing groups

def test_a_group_is_a_named_ordered_set_a_customer_can_belong_to_twice_over(books):
    run = books['run']
    shown = run('billing-group show', {'billing_group': books['group']})
    assert [member['customer_label'] for member in shown['members']] == [
        'Ridgeway Estates', 'Harbourfront Trust']
    assert [member['position'] for member in shown['members']] == [1, 2]
    assert shown['member_count'] == 2 and shown['name'] == 'Monthly retainers'

    # The same customer in a second group: membership is its own row, not a column.
    other = run('billing-group create', {'name': 'Quarterly reviews',
                                         'customers': ['Ridgeway Estates']},
                reason='Second round')['billing_group']['id']
    assert run('billing-group show', {'billing_group': other})['member_count'] == 1
    assert run('billing-group show', {'billing_group': books['group']})['member_count'] == 2

    listed = run('billing-group list', {'limit': 50})
    assert [item['name'] for item in listed['items']] == ['Monthly retainers', 'Quarterly reviews']
    assert [item['member_count'] for item in listed['items']] == [2, 1]

    # A repeated add is not a move, and a remove of a customer that is not there is not an error.
    again = run('billing-group add', {'billing_group': books['group'],
                                      'customers': ['Ridgeway Estates']}, reason='No change')
    assert again['changed'] is False
    assert run('billing-group remove', {'billing_group': other,
                                        'customers': ['Harbourfront Trust']},
               reason='Absent')['changed'] is False

    renamed = run('billing-group rename', {'billing_group': books['group'],
                                           'name': 'Monthly retainers 2026', 'expected_version': 1},
                  reason='Name the year')
    assert renamed['billing_group']['name'] == 'Monthly retainers 2026'
    assert renamed['billing_group']['member_count'] == 2
    with pytest.raises(BookflowError) as stale:
        run('billing-group rename', {'billing_group': books['group'], 'name': 'Something else',
                                     'expected_version': 1}, reason='Stale')
    assert stale.value.code == 'E_VERSION_CONFLICT'
    with pytest.raises(BookflowError) as taken:
        run('billing-group rename', {'billing_group': other, 'name': 'monthly RETAINERS 2026'},
            reason='Clash')
    assert taken.value.code == 'E_NAME_TAKEN'


def test_deleting_a_group_leaves_its_customers_and_the_batches_it_ran(books):
    run = books['run']
    posted = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                        'lines': LINES}, reason='Bill the retainer')
    run('billing-group delete', {'billing_group': books['group'], 'expected_version': 1},
        reason='Round ended')
    assert run('customer show', {'customer': books['ridgeway']})['active'] is True
    read_back = run('batch-invoice show', {'batch': posted['batch_id']})
    assert read_back['created_count'] == 2
    # The batch captured what it was addressed to rather than pointing at a row that can go.
    assert read_back['billing_group_id'] == books['group']
    assert read_back['billing_group_name'] == 'Monthly retainers'
    with pytest.raises(BookflowError) as gone:
        run('billing-group show', {'billing_group': books['group']})
    assert gone.value.code == 'E_RECORD_NOT_FOUND'


# ------------------------------------------------------------------ the rule for a deleted customer

def _creation_event(run, customer_id):
    """The audit event that created one customer, found by what it actually touched."""
    for event in run('audit list', {'limit': 200})['items']:
        if event['command'] != 'customer create':
            continue
        entries = run('audit show', {'event': event['id']})['entries']
        if any(entry['record_id'] == customer_id for entry in entries):
            return event['id']
    raise AssertionError('no creation event for ' + customer_id)


def test_a_member_customer_cannot_be_removed_until_it_leaves_its_groups(books):
    """The rule is refuse. Take the customer out of its groups first.

    Nothing in Bookflow hard-deletes a customer: `undo` of the creation event is the act that
    takes it back off the lists, and that is the act a membership refuses, naming the
    membership table so the person can see what is holding it.
    """
    run = books['run']
    event = _creation_event(run, books['harbour'])
    with pytest.raises(BookflowError) as refused:
        run('undo', {'event_id': event}, reason='Remove the customer')
    assert refused.value.code == 'E_UNDO_CONFLICT'
    assert any(item['record_type'] == 'billing_group_members'
               for item in refused.value.details.get('dependents', [])), refused.value.details

    run('billing-group remove', {'billing_group': books['group'],
                                 'customers': [books['harbour']]}, reason='Release it')
    run('undo', {'event_id': event}, reason='Remove the customer')
    assert run('customer show', {'customer': books['harbour']})['active'] is False
    assert [member['customer_label'] for member in
            run('billing-group show', {'billing_group': books['group']})['members']] == [
        'Ridgeway Estates']


def test_deactivating_a_member_keeps_it_in_the_group_and_the_batch_says_so(books):
    """Deactivation is a different decision from removal and is deliberately not refused."""
    run = books['run']
    version = run('customer show', {'customer': books['harbour']})['version']
    run('customer deactivate', {'customer': books['harbour'], 'expected_version': version},
        reason='Paused account')
    shown = run('billing-group show', {'billing_group': books['group']})
    assert [member['customer_label'] for member in shown['members']] == [
        'Ridgeway Estates', 'Harbourfront Trust']
    assert [member['active'] for member in shown['members']] == [True, False]

    # The batch says so out loud rather than silently invoicing one customer instead of two.
    posted = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                        'lines': LINES}, reason='Bill the retainer')
    assert (posted['created_count'], posted['failed_count']) == (1, 1)
    assert _rows(posted)['Harbourfront Trust']['error_code'] == 'E_INACTIVE_REFERENCE'


# ------------------------------------------------------------------ per-customer resolution

def test_two_customers_on_different_price_levels_get_two_different_totals(books):
    """The defect this feature ships with if anything is resolved once and reused."""
    run = books['run']
    posted = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                        'lines': LINES}, reason='Bill the retainer')
    rows = _rows(posted)
    assert rows['Ridgeway Estates']['total']['minor_units'] == STANDARD_UNITS
    assert rows['Harbourfront Trust']['total']['minor_units'] == PREFERRED_UNITS
    assert rows['Ridgeway Estates']['price_level'] is None
    assert rows['Harbourfront Trust']['price_level'] == 'Retainer discount'
    assert posted['created_total']['minor_units'] == STANDARD_UNITS + PREFERRED_UNITS

    # Not the batch's own arithmetic: the saved invoices themselves.
    for label, units in (('Ridgeway Estates', STANDARD_UNITS), ('Harbourfront Trust', PREFERRED_UNITS)):
        invoice = run('invoice show', {'invoice': rows[label]['transaction_id']})
        assert invoice['total']['minor_units'] == units
        assert invoice['revision']['lines'][0]['net']['minor_units'] == units


def test_each_invoice_carries_its_own_terms_and_due_date(books):
    run = books['run']
    posted = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                        'lines': LINES}, reason='Bill the retainer')
    rows = _rows(posted)
    assert rows['Ridgeway Estates']['terms'] == 'Retainer 15'
    assert rows['Harbourfront Trust']['terms'] == 'Retainer 30'
    for label, due, street in (('Ridgeway Estates', RIDGEWAY_DUE, '1 Ridgeway Lane'),
                               ('Harbourfront Trust', HARBOURFRONT_DUE, '9 Harbour Road')):
        assert rows[label]['due_date'] == due
        invoice = run('invoice show', {'invoice': rows[label]['transaction_id']})
        assert invoice['due_date'] == due
        # The billing address is captured from the customer the invoice is for, not from
        # whichever customer happened to be resolved first.
        assert invoice['revision']['profile']['billing_address']['line1'] == street


def test_an_explicit_list_invoices_the_customers_named_in_the_order_given(books):
    run = books['run']
    posted = run('batch-invoice post', {'date': DATE,
                                        'customers': ['Harbourfront Trust', 'Ridgeway Estates'],
                                        'lines': LINES, 'memo': 'September retainer'},
                 reason='Bill two customers')
    assert [row['customer_label'] for row in posted['batch_rows']] == [
        'Harbourfront Trust', 'Ridgeway Estates']
    assert posted['billing_group_id'] is None
    assert all(run('invoice show', {'invoice': row['transaction_id']})['memo'] == 'September retainer'
               for row in posted['batch_rows'])
    with pytest.raises(BookflowError) as both:
        run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                   'customers': ['Ridgeway Estates'], 'lines': LINES},
            reason='Both sources')
    assert both.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as twice:
        run('batch-invoice post', {'date': DATE,
                                   'customers': ['Ridgeway Estates', 'Ridgeway Estates'],
                                   'lines': LINES}, reason='Same customer twice')
    assert twice.value.code == 'E_VALIDATION'


# ------------------------------------------------------------------ preview

def test_a_preview_shows_a_row_per_customer_and_writes_nothing(books):
    run = books['run']
    preview = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                         'lines': LINES}, reason='Look first', dry_run=True)
    assert preview['dry_run'] is True and preview['batch_id'] is None
    rows = _rows(preview)
    assert {row['status'] for row in preview['batch_rows']} == {'will_create'}
    assert rows['Ridgeway Estates']['total']['minor_units'] == STANDARD_UNITS
    assert rows['Harbourfront Trust']['total']['minor_units'] == PREFERRED_UNITS
    assert preview['created_count'] == 2
    assert preview['created_total']['minor_units'] == STANDARD_UNITS + PREFERRED_UNITS
    # A number is taken from the allocator at the moment an invoice is written, so a preview
    # has none to show and shows none rather than showing one number twice.
    assert all(row['number'] is None and row['transaction_id'] is None
               for row in preview['batch_rows'])
    assert run('invoice query', {'limit': 50})['count'] == 0
    assert run('batch-invoice query', {'limit': 50})['count'] == 0


def test_a_preview_shows_the_customer_that_would_be_refused_before_anything_is_posted(books):
    run = books['run']
    version = run('customer show', {'customer': books['harbour']})['version']
    run('customer deactivate', {'customer': books['harbour'], 'expected_version': version},
        reason='Paused account')
    preview = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                         'lines': LINES}, reason='Look first', dry_run=True)
    assert (preview['created_count'], preview['failed_count']) == (1, 1)
    assert _rows(preview)['Harbourfront Trust']['error_code'] == 'E_INACTIVE_REFERENCE'
    assert run('invoice query', {'limit': 50})['count'] == 0


# ------------------------------------------------------------------ partial failure

def test_one_refused_customer_does_not_roll_back_the_invoices_that_succeeded(books):
    run = books['run']
    third = run('customer create', {'name': 'Wharfside Holdings', 'terms_id': books['net15']},
                reason='Third retainer customer')['id']
    run('billing-group add', {'billing_group': books['group'], 'customers': [third]},
        reason='Add the third')
    version = run('customer show', {'customer': books['harbour']})['version']
    run('customer deactivate', {'customer': books['harbour'], 'expected_version': version},
        reason='Paused account')

    posted = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                        'lines': LINES}, reason='Bill the retainer')
    assert (posted['requested_count'], posted['created_count'], posted['failed_count']) == (3, 2, 1)
    rows = _rows(posted)
    assert rows['Harbourfront Trust']['status'] == 'failed'
    assert rows['Harbourfront Trust']['transaction_id'] is None
    assert rows['Ridgeway Estates']['status'] == 'created'
    assert rows['Wharfside Holdings']['status'] == 'created'

    # The two that worked are real, saved invoices, and the run's total is only theirs.
    saved = run('invoice query', {'limit': 50})
    assert saved['count'] == 2
    assert posted['created_total']['minor_units'] == STANDARD_UNITS * 2

    # Numbers come from the allocator and only on success: the refused customer consumed none,
    # so the two that were created hold the first two numbers of the series with no gap.
    assert sorted(row['number'] for row in saved['items']) == sorted(
        rows[label]['number'] for label in ('Ridgeway Estates', 'Wharfside Holdings'))
    assert len({rows[label]['number'] for label in ('Ridgeway Estates', 'Wharfside Holdings')}) == 2

    # And the outcome is durable, not just a response the user might have closed.
    stored = run('batch-invoice show', {'batch': posted['batch_id']})
    assert (stored['created_count'], stored['failed_count']) == (2, 1)
    assert {row['customer_label']: row['status'] for row in stored['batch_rows']} == {
        'Ridgeway Estates': 'created', 'Harbourfront Trust': 'failed',
        'Wharfside Holdings': 'created'}
    failed = next(row for row in stored['batch_rows'] if row['status'] == 'failed')
    assert failed['error_code'] == 'E_INACTIVE_REFERENCE' and failed['error_message']


def test_only_the_failures_are_invoiced_again_by_a_retry(books):
    run = books['run']
    version = run('customer show', {'customer': books['harbour']})['version']
    run('customer deactivate', {'customer': books['harbour'], 'expected_version': version},
        reason='Paused account')
    first = run('batch-invoice post', {'date': DATE, 'billing_group': books['group'],
                                       'lines': LINES, 'memo': 'September retainer'},
                reason='Bill the retainer')
    assert (first['created_count'], first['failed_count']) == (1, 1)

    # A retry that is still too early is a recorded batch of its own, not an exception: the
    # customer is still inactive, so the run reports it refused again rather than pretending.
    early = run('batch-invoice retry', {'batch': first['batch_id']}, reason='Too early')
    assert (early['requested_count'], early['created_count'], early['failed_count']) == (1, 0, 1)
    assert early['retry_of_batch_id'] == first['batch_id']
    assert run('invoice query', {'limit': 50})['count'] == 1

    run('customer activate', {'customer': books['harbour'],
                              'expected_version': run('customer show', {'customer': books['harbour']})['version']},
        reason='Back on')
    retried = run('batch-invoice retry', {'batch': early['batch_id']}, reason='Invoice the rest')
    assert retried['requested_count'] == 1 and retried['created_count'] == 1
    assert retried['retry_of_batch_id'] == early['batch_id']
    assert retried['batch_rows'][0]['customer_label'] == 'Harbourfront Trust'
    # The retry asks the original question again: the same line and the same memo.
    assert retried['batch_rows'][0]['total']['minor_units'] == PREFERRED_UNITS
    assert run('invoice show', {'invoice': retried['batch_rows'][0]['transaction_id']})['memo'] == (
        'September retainer')
    assert run('invoice query', {'limit': 50})['count'] == 2

    with pytest.raises(BookflowError) as nothing:
        run('batch-invoice retry', {'batch': retried['batch_id']}, reason='Nothing left')
    assert nothing.value.code == 'E_VALIDATION'
