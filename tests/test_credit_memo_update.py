"""Actual correction commands: endpoint cents, immutable history, and atomic refusal."""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.credit_support import books, balances, goodwill_credit, returned_credit, taxed_invoice
from tests.test_credit_memo_lifecycle import _three_unit_invoice


def update(books, credit, **patch):
    return books['run']('credit-memo update', dict(
        credit_memo=credit['id'], expected_version=credit['version'], **patch), reason='Correct the credit')


def snapshot(books):
    with sqlite3.connect(books['database']) as db:
        return list(db.iterdump())


def test_quantity_correction_returns_exact_endpoint_cents_and_preserves_history(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    first = returned_credit(books, invoice['id'], source_line)
    second = returned_credit(books, invoice['id'], source_line)
    # First unit (33+2) and last unit (34+3), while the second credit still owns the middle.
    corrected = update(books, first, lines=[dict(
        line_id=first['revision']['lines'][0]['line_id'], source_invoice=invoice['id'],
        source_line=source_line, quantity='2')])
    assert corrected['total_minor_units'] == 72
    line = corrected['revision']['lines'][0]
    assert (line['net_minor_units'], line['tax_minor_units']) == (67, 5)
    assert line['line_id'] == first['revision']['lines'][0]['line_id']
    assert [(row['start_microunits'], row['end_microunits']) for row in line['claims']] == [
        (0, 1_000_000), (2_000_000, 3_000_000)]
    assert corrected['source_current']['credit_source_key_id'] == first['source_current']['credit_source_key_id']
    old = books['run']('credit-memo show', dict(credit_memo=first['id'], revision_number=1))
    assert old['revision']['lines'] == first['revision']['lines']
    assert books['run']('credit-memo show', dict(credit_memo=second['id']))['total_minor_units'] == 35
    totals, _ = balances(books)
    assert totals.get(books['receivable'], 0) == totals.get(books['income'], 0) == totals.get(books['liability'], 0) == 0
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        returned_credit(books, invoice['id'], source_line)
    assert error.value.code == 'E_RETURN_EXHAUSTED'
    assert snapshot(books) == before


@pytest.mark.parametrize('linked', [False, True])
def test_header_only_retains_captures_after_master_changes_and_retries_once(books, linked):
    invoice = taxed_invoice(books)
    credit = (returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id']) if linked
              else goodwill_credit(books, '30.00'))
    item = books['widget'] if linked else books['service']
    books['run']('item update', dict(item=item, price='99.00', description='Changed description'))
    request = dict(credit_memo=credit['id'], expected_version=credit['version'], memo='Corrected note')
    result = books['run']('credit-memo update', request, idempotency_key='memo-correction', reason='Correct note')
    assert result['total_minor_units'] == credit['total_minor_units']
    old_line, new_line = credit['revision']['lines'][0], result['revision']['lines'][0]
    for key in ('line_id', 'item_snapshot', 'quantity', 'description', 'net_minor_units', 'tax_minor_units'):
        assert new_line[key] == old_line[key]
    before = snapshot(books)
    replay = books['run']('credit-memo update', request, idempotency_key='memo-correction', reason='Correct note')
    assert replay['revision']['id'] == result['revision']['id']
    assert snapshot(books) == before
    with pytest.raises(BookflowError) as error:
        update(books, credit, memo='Stale correction')
    assert error.value.code == 'E_VERSION_CONFLICT'
    assert snapshot(books) == before


def test_refused_quantity_leaves_every_row_unchanged(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    credit = returned_credit(books, invoice['id'], source_line)
    returned_credit(books, invoice['id'], source_line)
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, credit, lines=[dict(source_invoice=invoice['id'], source_line=source_line, quantity='3')])
    assert error.value.code == 'E_RETURN_EXHAUSTED'
    assert snapshot(books) == before


@pytest.mark.parametrize('consumed', ['applied', 'refunded'])
def test_consumed_noop_preserves_versions_and_history(books, consumed):
    from tests.credit_support import apply_credit, invoice, refund
    credit = goodwill_credit(books, '30.00')
    if consumed == 'applied':
        target = invoice(books)
        apply_credit(books, credit, target['id'], amount='10.00')
    else:
        refund(books, credit['id'], '10.00')
    current = books['run']('credit-memo show', dict(credit_memo=credit['id']))
    before = snapshot(books)
    assert update(books, current)['changed'] is False
    assert update(books, current, number=current['number'], memo=current['revision']['memo'])['changed'] is False
    assert snapshot(books) == before
    with pytest.raises(BookflowError) as error:
        update(books, dict(current, version=current['version'] + 1))
    assert error.value.code == 'E_VERSION_CONFLICT'
    assert snapshot(books) == before


def test_closed_dates_refuse_without_writes_and_empty_patch_stays_empty(books):
    credit = goodwill_credit(books, '30.00')
    books['run']('company update', {'closing_date': '2026-03-31'})
    before = snapshot(books)
    assert update(books, credit)['changed'] is False
    with pytest.raises(BookflowError) as error:
        update(books, credit, date='2026-04-10', memo='Move to April')
    assert error.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(books) == before


def test_preview_stales_when_another_return_takes_its_quantity(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    credit = returned_credit(books, invoice['id'], source_line)
    patch = dict(credit_memo=credit['id'], expected_version=credit['version'], lines=[dict(
        line_id=credit['revision']['lines'][0]['line_id'],
        source_invoice=invoice['id'], source_line=source_line, quantity='2')])
    preview = books['run']('credit-memo update', patch, dry_run=True)
    # The other writer takes the next interval; corrected total changes from 70 to 72.
    returned_credit(books, invoice['id'], source_line)
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        books['run']('credit-memo update', dict(patch, expected_facts_fingerprint=preview['facts_fingerprint']))
    assert error.value.code == 'E_PREVIEW_STALE'
    assert snapshot(books) == before


def test_validator_rejects_balanced_but_wrong_reversal_and_claim_release(books, monkeypatch):
    from bookflow.company import credit_validation
    invoice = _three_unit_invoice(books)
    credit = returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id'])
    original = credit_validation.validate_update
    before = snapshot(books)
    for corrupt in ('reversal', 'claim'):
        def validate(plan, session, context):
            if corrupt == 'reversal':
                # Still balances at the same gross, but reverses the wrong account.
                plan.data['pending']['posting_lines'][0]['account_id'] = books['checking']
            else:
                plan.data['pending']['credit_source_claims'][0]['end_microunits'] += 1
            return original(plan, session, context)
        monkeypatch.setattr(credit_validation, 'validate_update', validate)
        with pytest.raises(BookflowError) as error:
            update(books, credit, memo='Cannot commit corrupt plan')
        assert error.value.code == 'E_INTERNAL'
        assert snapshot(books) == before


def test_failed_write_rolls_back_releases_and_replacement_as_one_operation(books, monkeypatch):
    import sqlalchemy as sa
    from sqlalchemy.engine import Engine
    invoice = _three_unit_invoice(books)
    credit = returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id'])
    before = snapshot(books)
    observed = []

    def after_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO credit_source_claims'):
            # The pending releases AND retakes have reached SQLite. An independent reader
            # must still see the old whole document until the outer transaction commits.
            with sqlite3.connect(books['database']) as reader:
                observed.append(reader.execute(
                    'SELECT current_revision_id FROM transactions WHERE id=?', (credit['id'],)).fetchone()[0])
            raise RuntimeError('injected after claim insertion')
    sa.event.listen(Engine, 'after_cursor_execute', after_insert)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            update(books, credit, memo='Rolled back')
    finally:
        sa.event.remove(Engine, 'after_cursor_execute', after_insert)
    assert observed == [credit['revision']['id']]
    assert snapshot(books) == before


def test_reducing_return_releases_the_last_endpoint_for_another_credit(books):
    invoice = _three_unit_invoice(books)
    source_line = invoice['revision']['lines'][0]['line_id']
    credit = returned_credit(books, invoice['id'], source_line, quantity='3')
    corrected = update(books, credit, lines=[dict(
        line_id=credit['revision']['lines'][0]['line_id'],
        source_invoice=invoice['id'], source_line=source_line, quantity='2')])
    assert corrected['total_minor_units'] == 70
    last = returned_credit(books, invoice['id'], source_line)
    assert last['total_minor_units'] == 37
    assert (last['revision']['lines'][0]['net_minor_units'], last['revision']['lines'][0]['tax_minor_units']) == (34, 3)
    totals, _ = balances(books)
    assert totals.get(books['receivable'], 0) == 0
    # Voiding a corrected credit must reverse the replacement, not the first original batch.
    books['run']('credit-memo void', dict(credit_memo=corrected['id'], expected_version=corrected['version']),
                 reason='Cancel corrected credit')
    again = returned_credit(books, invoice['id'], source_line, quantity='2')
    assert again['total_minor_units'] == 70


def test_standalone_amount_correction_and_retained_tax_after_rate_change(books):
    credit = books['run']('credit-memo post', dict(
        customer=books['kerr'], date='2026-04-05', sales_tax_item=books['tax_item'],
        lines=[dict(item=books['widget'], quantity='4', unit_price='25.00', tax_code=books['taxable'])]))
    books['run']('item update', dict(item=books['tax_item'], tax_percent='10'))
    header = update(books, credit, memo='Preserve eight percent')
    assert header['total_minor_units'] == 10800
    assert header['revision']['lines'][0]['tax_components'][0]['rate_percent_millionths'] == 8_000_000
    corrected = update(books, header, lines=[dict(
        line_id=header['revision']['lines'][0]['line_id'], item=books['widget'], quantity='2')])
    assert corrected['total_minor_units'] == 5400
    totals, _ = balances(books)
    assert totals[books['receivable']] == -5400
    assert totals[books['income']] == 5000
    assert totals[books['liability']] == 400


def test_linked_return_customer_change_is_refused_atomically(books):
    invoice = taxed_invoice(books)
    credit = returned_credit(books, invoice['id'], invoice['revision']['lines'][0]['line_id'])
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, credit, customer=books['ridge'])
    assert error.value.code == 'E_VALIDATION'
    assert error.value.details['fields'][0]['field'] == 'source_invoice'
    assert snapshot(books) == before


def test_cli_correction_can_be_continued_and_cleared_by_python(books):
    import json
    import subprocess
    import sys
    credit = goodwill_credit(books, '30.00')
    result = subprocess.run([
        sys.executable, '-c', 'from bookflow.bootstrap import main; main()', 'credit-memo', 'update', credit['id'],
        '--expected-version', str(credit['version']), '--memo', 'Entered through CLI',
        '--company', books['company'], '--reason', 'Correct credit', '--json'],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)
    # CLI returns the common result envelope, just as other commands do.
    saved = books['run']('credit-memo show', dict(credit_memo=credit['id']))
    assert saved['revision']['memo'] == 'Entered through CLI'
    assert saved['version'] == credit['version'] + 1
    corrected = update(books, saved, memo=None)
    assert corrected['revision']['memo'] is None
    assert corrected['total_minor_units'] == 3000


def current(books, credit):
    return books['run']('credit-memo show', dict(credit_memo=credit['id']))


def unapply_current(books, credit, target):
    settlement = books['run']('invoice settlement', dict(invoice=target['id']))
    cancelled = {row['reverses_application_id'] for row in settlement['applications'] if row['kind'] == 'unapply'}
    apps = [row for row in settlement['applications'] if row['paying_transaction_id'] == credit['id']
            and row['kind'] == 'apply' and row['id'] not in cancelled]
    assert apps
    return books['run']('customer-credit unapply', dict(
        credit_memo=credit['id'], expected_version=current(books, credit)['version'],
        applications=[dict(application_id=row['id'], invoice_expected_version=settlement['version'])
                      for row in apps]), reason='Unapply corrected credit')


def corrected_lines(books, amounts):
    return [dict(item=books['service'], quantity='1', unit_price=amount) for amount in amounts]


@pytest.mark.parametrize('use', ['applied', 'refunded', 'mixed'])
def test_used_correction_restates_combined_capacity_and_later_cancellations(books, use):
    from tests.credit_support import apply_credit, invoice, refund, ties_to_the_receivable
    credit = goodwill_credit(books, '60.00')
    target = invoice(books, lines=[dict(item=books['service'], quantity='1', unit_price='100.00')])
    applied = apply_credit(books, credit, target['id'], amount='25.00', date='2026-03-15') if use != 'refunded' else None
    refunded = refund(books, credit['id'], '15.00') if use != 'applied' else None
    old = current(books, credit)
    invoice_before = books['run']('invoice show', dict(invoice=target['id']))
    old_rows = snapshot(books)
    patch = dict(credit_memo=credit['id'], expected_version=old['version'],
                 lines=corrected_lines(books, ['10.00', '12.00', '28.00']))
    preview = books['run']('credit-memo update', patch, dry_run=True, reason='Correct credit')
    assert snapshot(books) == old_rows
    request = dict(patch, expected_facts_fingerprint=preview['facts_fingerprint'])
    saved = books['run']('credit-memo update', request, reason='Correct credit', idempotency_key='used-correction')
    expected_used = (2500 if applied else 0) + (1500 if refunded else 0)
    assert saved['source_current']['available_minor_units'] == 5000 - expected_used
    assert saved['version'] == old['version'] + 1
    assert books['run']('invoice show', dict(invoice=target['id']))['version'] == invoice_before['version'] + bool(applied)
    history = books['run']('credit-memo show', dict(credit_memo=credit['id'], revision_number=1))
    assert history['revision']['lines'] == credit['revision']['lines']
    with sqlite3.connect(books['database']) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        parts = db.execute('''SELECT c.amount_minor_units,
          (SELECT coalesce(sum(a.amount_minor_units),0) FROM application_allocations a
           WHERE a.credit_source_component_id=c.id AND a.kind='allocation'
           AND NOT EXISTS (SELECT 1 FROM application_allocations u WHERE u.reverses_allocation_id=a.id)) +
          (SELECT coalesce(sum(r.amount_minor_units),0) FROM customer_refund_consumptions r
           WHERE r.credit_source_component_id=c.id AND r.kind='consume'
           AND NOT EXISTS (SELECT 1 FROM customer_refund_consumptions u WHERE u.reverses_consumption_id=r.id))
          FROM credit_components c WHERE c.transaction_id=? AND c.revision_id=?''',
          (credit['id'], saved['revision']['id'])).fetchall()
        assert sum(row[1] for row in parts) == expected_used
        assert all(0 <= used <= amount for amount, used in parts)
        stale_uses = db.execute('''SELECT count(*) FROM application_allocations a
          WHERE a.source_transaction_id=? AND a.source_revision_id<>? AND a.kind='allocation'
          AND NOT EXISTS (SELECT 1 FROM application_allocations u WHERE u.reverses_allocation_id=a.id)''',
          (credit['id'], saved['revision']['id'])).fetchone()[0]
        assert stale_uses == 0
    ties_to_the_receivable(books)
    before = snapshot(books)
    replay = books['run']('credit-memo update', request, reason='Correct credit', idempotency_key='used-correction')
    assert replay['revision']['id'] == saved['revision']['id']
    assert snapshot(books) == before
    # A second correction exercises both a retained application ID and already-restated refunds.
    saved = update(books, saved, memo='Second corrected capture')
    if applied:
        unapply_current(books, credit, target)
    if refunded:
        latest = books['run']('customer-refund show', dict(refund=refunded['id']))
        assert latest['version'] == refunded['version'] + 2
        books['run']('customer-refund void', dict(refund=refunded['id'], expected_version=latest['version']),
                     reason='Cancel corrected refund')
    assert current(books, credit)['source_current']['available_minor_units'] == 5000
    ties_to_the_receivable(books)


@pytest.mark.parametrize('patch,code,reason', [
    ({'customer': 'Kerr'}, 'E_APPLICATION_INCOMPATIBLE', 'credit_source_ownership'),
    ({'date': '2026-03-16'}, 'E_HAS_APPLICATIONS', 'credit_date_after_use'),
    ({'amounts': ['39.99']}, 'E_APPLIED_EXCEEDS_TOTAL', 'credit_combined_use'),
])
def test_mixed_use_refusals_are_named_and_atomic(books, patch, code, reason):
    from tests.credit_support import apply_credit, invoice, refund
    credit = goodwill_credit(books, '60.00')
    apply_credit(books, credit, invoice(books)['id'], amount='25.00', date='2026-03-15')
    refund(books, credit['id'], '15.00')
    patch = dict(patch)
    if 'amounts' in patch:
        patch['lines'] = corrected_lines(books, patch.pop('amounts'))
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, current(books, credit), **patch)
    assert (error.value.code, error.value.details['reason']) == (code, reason)
    if reason == 'credit_date_after_use':
        assert error.value.details['earliest_use_date'] == '2026-03-15'
    assert snapshot(books) == before


def test_used_preview_detects_refund_without_header_version_change(books):
    from tests.credit_support import refund
    credit = goodwill_credit(books, '60.00')
    request = dict(credit_memo=credit['id'], expected_version=credit['version'], memo='Correct note')
    preview = books['run']('credit-memo update', request, dry_run=True)
    refund(books, credit['id'], '15.00')
    assert current(books, credit)['version'] == credit['version']  # existing refund owner behavior
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        books['run']('credit-memo update', dict(request, expected_facts_fingerprint=preview['facts_fingerprint']),
                     reason='Correct note')
    assert error.value.code == 'E_PREVIEW_STALE'
    assert snapshot(books) == before


def test_standalone_dimension_changes_choose_current_key_and_reuse_old_key(books):
    from tests.credit_support import apply_credit, invoice, refund
    credit = goodwill_credit(books, '60.00')
    ar = books['run']('account create', dict(name='Other receivable', type='accounts_receivable'))['id']
    changed = update(books, credit, customer=books['kerr'], ar_account=ar)
    key = changed['source_current']
    assert (key['party_id'], key['ar_account_id']) == (books['kerr'], ar)
    assert key['credit_source_key_id'] != credit['source_current']['credit_source_key_id']
    assert current(books, credit)['source_current'] == key
    page = books['run']('credit-memo query', dict(customer=books['kerr'], ar_account=ar, available_only=True))
    assert page['items'][0]['source_current'] == key
    target = invoice(books, customer=books['kerr'], ar_account=ar)
    apply_credit(books, changed, target['id'], amount='10.00')
    paid = refund(books, changed['id'], '5.00')
    latest = current(books, credit)
    assert latest['source_current']['available_minor_units'] == 4500
    unapply_current(books, credit, target)
    books['run']('customer-refund void', dict(refund=paid['id'], expected_version=paid['version']), reason='Void refund')
    restored = update(books, current(books, credit), customer=books['ridge'], ar_account=books['receivable'])
    assert restored['source_current']['credit_source_key_id'] == credit['source_current']['credit_source_key_id']
    assert restored['source_current']['available_minor_units'] == 6000
    with sqlite3.connect(books['database']) as db:
        assert db.execute('SELECT count(*) FROM credit_source_keys WHERE transaction_id=?', (credit['id'],)).fetchone()[0] == 2
    historical = books['run']('credit-memo show', dict(credit_memo=credit['id'], revision_number=2))
    assert historical['revision']['profile']['customer']['id'] == books['kerr']
    assert historical['source_current']['party_id'] == books['ridge']


def test_used_credit_keeps_taxed_target_captures_through_invoice_restatement(books):
    from tests.credit_support import apply_credit, refund, ties_to_the_receivable
    from tests.test_credit_memo_lifecycle import _correct
    credit = goodwill_credit(books, '60.00', customer=books['kerr'])
    target = taxed_invoice(books)
    apply_credit(books, credit, target['id'], amount='35.00', date='2026-03-15')
    refund(books, credit['id'], '15.00')
    with sqlite3.connect(books['database']) as db:
        old = db.execute('''SELECT target_line_id, logical_kind, tax_item_id, facts_snapshot,
          sum(amount_minor_units) FROM application_allocations WHERE source_transaction_id=?
          AND kind='allocation' GROUP BY 1,2,3,4 ORDER BY 1,2''', (credit['id'],)).fetchall()
    saved = update(books, current(books, credit), lines=corrected_lines(books, ['10.00', '15.00', '25.00']))
    with sqlite3.connect(books['database']) as db:
        new = db.execute('''SELECT target_line_id, logical_kind, tax_item_id, facts_snapshot,
          sum(amount_minor_units) FROM application_allocations a WHERE source_transaction_id=?
          AND kind='allocation' AND NOT EXISTS
          (SELECT 1 FROM application_allocations u WHERE u.reverses_allocation_id=a.id)
          GROUP BY 1,2,3,4 ORDER BY 1,2''', (credit['id'],)).fetchall()
    assert new == old
    _correct(books, target['id'], 'correct-taxed-target-after-credit', [dict(
        line_id=target['revision']['lines'][0]['line_id'], item=books['widget'], quantity='5',
        unit_price='25.00', tax_code=books['taxable'])],
        settlement_versions=[dict(payment=credit['id'], expected_version=saved['version'])])
    # Invoice correction may keep semantically unchanged source attribution or restate target cells;
    # either way the credit's current component use and the later unapply must remain correct.
    assert current(books, credit)['source_current']['available_minor_units'] == 0
    latest = current(books, credit)
    unapply_current(books, credit, target)
    assert current(books, credit)['source_current']['available_minor_units'] == 3500
    ties_to_the_receivable(books)


def test_used_credit_requires_reason_and_closed_dates_are_atomic(books):
    from tests.credit_support import refund
    credit = goodwill_credit(books, '60.00')
    refund(books, credit['id'], '15.00')
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        books['run']('credit-memo update', dict(credit_memo=credit['id'], memo='No reason'))
    assert error.value.code == 'E_REASON_REQUIRED'
    assert snapshot(books) == before
    books['run']('company update', {'closing_date': '2026-03-20'})
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, current(books, credit), memo='Closed use date')
    assert error.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(books) == before


def test_refund_restatement_insertion_failure_rolls_back_all_effects(books):
    import sqlalchemy as sa
    from sqlalchemy.engine import Engine
    from tests.credit_support import apply_credit, invoice, refund
    credit = goodwill_credit(books, '60.00')
    apply_credit(books, credit, invoice(books)['id'], amount='25.00', date='2026-03-15')
    refund(books, credit['id'], '15.00')
    before = snapshot(books)
    observed = []
    old = current(books, credit)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO customer_refund_consumptions'):
            with sqlite3.connect(books['database']) as reader:
                observed.append(reader.execute('SELECT current_revision_id FROM transactions WHERE id=?',
                                                (credit['id'],)).fetchone()[0])
            raise RuntimeError('injected after refund restatement')
    sa.event.listen(Engine, 'after_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='injected after refund'):
            update(books, old, lines=corrected_lines(books, ['10.00', '40.00']))
    finally:
        sa.event.remove(Engine, 'after_cursor_execute', fail)
    assert observed == [old['revision']['id']]
    assert snapshot(books) == before


@pytest.mark.parametrize('corruption', ['refund', 'target', 'capacity', 'omitted_refund'])
def test_restatement_validator_rejects_corruption_before_any_write(books, monkeypatch, corruption):
    from bookflow.company import credit_validation
    from tests.credit_support import apply_credit, invoice, refund
    credit = goodwill_credit(books, '60.00')
    apply_credit(books, credit, invoice(books)['id'], amount='25.00', date='2026-03-15')
    refund(books, credit['id'], '15.00')
    original = credit_validation.validate_update

    def corrupt(plan, session, context):
        pending = plan.data['pending']
        if corruption == 'omitted_refund':
            plan.data['previous']['consumptions'] = []
            plan.data['restatement']['graph']['consumptions'] = []
            plan.data['restatement']['refund_map'] = {}
            pending['customer_refund_consumptions'] = []
        elif corruption == 'refund':
            next(row for row in pending['customer_refund_consumptions'] if row['kind'] == 'release')['amount_minor_units'] += 1
        elif corruption == 'target':
            next(row for row in pending['application_allocations'] if row['kind'] == 'allocation')['facts_snapshot'] = '{}'
        else:
            rows = [row for row in pending['application_allocations'] if row['kind'] == 'allocation']
            part = pending['credit_components'][0]
            for row in rows:
                row['credit_source_component_id'] = part['id']
                row['source_posting_source_id'] = part['posting_source_id']
        return original(plan, session, context)
    monkeypatch.setattr(credit_validation, 'validate_update', corrupt)
    before = snapshot(books)
    with pytest.raises(BookflowError) as error:
        update(books, current(books, credit), lines=corrected_lines(books, ['10.00', '40.00']))
    assert error.value.code == 'E_INTERNAL'
    assert snapshot(books) == before


def test_used_return_correction_releases_claims_and_restates_uses_together(books):
    from tests.credit_support import apply_credit, refund
    source = _three_unit_invoice(books)
    source_line = source['revision']['lines'][0]['line_id']
    credit = returned_credit(books, source['id'], source_line, quantity='3')
    apply_credit(books, credit, source['id'], amount='0.35', date='2026-04-06')
    paid = refund(books, credit['id'], '0.35', date='2026-04-07')
    saved = update(books, current(books, credit), lines=[dict(
        line_id=credit['revision']['lines'][0]['line_id'], source_invoice=source['id'],
        source_line=source_line, quantity='2')])
    assert saved['total_minor_units'] == 70
    assert saved['source_current']['available_minor_units'] == 0
    last = returned_credit(books, source['id'], source_line)
    assert last['total_minor_units'] == 37
    unapply_current(books, credit, source)
    latest = books['run']('customer-refund show', dict(refund=paid['id']))
    books['run']('customer-refund void', dict(refund=paid['id'], expected_version=latest['version']), reason='Void refund')
    assert current(books, credit)['source_current']['available_minor_units'] == 70
